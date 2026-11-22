"""Bounded switch command guard with OFF preemption, state confirmation, and retry."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    DEFAULT_SWITCH_CONFIRM_TIMEOUT,
    DEFAULT_SWITCH_OFF_RETRY_DELAYS,
)

_LOGGER = logging.getLogger(__name__)


class SwitchCommandGuard:
    """Confirm switch ON/OFF commands, preempt ON on OFF, and retry failed OFF."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        confirm_timeout: float = DEFAULT_SWITCH_CONFIRM_TIMEOUT,
        off_retry_delays: tuple[float, ...] = DEFAULT_SWITCH_OFF_RETRY_DELAYS,
    ) -> None:
        self.hass = hass
        self.confirm_timeout = float(confirm_timeout)
        self.off_retry_delays = tuple(float(d) for d in off_retry_delays)
        self._generations: dict[str, int] = {}
        self.last_error: dict[str, str] = {}

    def _next_generation(self, entity_id: str) -> int:
        gen = self._generations.get(entity_id, 0) + 1
        self._generations[entity_id] = gen
        return gen

    async def _call_switch_service(self, entity_id: str, service: str) -> bool:
        target_domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
        try:
            await self.hass.services.async_call(
                target_domain,
                service,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            return True
        except Exception as err:
            _LOGGER.warning(
                "Switch service %s.%s failed for %s: %s",
                target_domain,
                service,
                entity_id,
                err,
            )
            if target_domain != "homeassistant":
                try:
                    await self.hass.services.async_call(
                        "homeassistant",
                        service,
                        {ATTR_ENTITY_ID: entity_id},
                        blocking=True,
                    )
                    return True
                except Exception as fb_err:
                    _LOGGER.error(
                        "Fallback homeassistant.%s failed for %s: %s",
                        service,
                        entity_id,
                        fb_err,
                    )
        return False

    async def _wait_for_state(
        self,
        entity_id: str,
        desired_state: str,
        generation: int,
        timeout: float,
    ) -> bool:
        st = self.hass.states.get(entity_id)
        if st is None or st.state == desired_state:
            return True
        if timeout <= 0:
            return True

        event = asyncio.Event()

        @callback
        def _on_change(ev: Event) -> None:
            new_st = ev.data.get("new_state") if isinstance(ev.data, dict) else None
            if new_st is not None and new_st.state == desired_state:
                event.set()

        unsub = async_track_state_change_event(self.hass, [entity_id], _on_change)
        try:
            st_again = self.hass.states.get(entity_id)
            if st_again is not None and st_again.state == desired_state:
                return True
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._generations.get(entity_id) == generation
        except TimeoutError:
            final_st = self.hass.states.get(entity_id)
            return final_st is not None and final_st.state == desired_state
        finally:
            unsub()

    async def async_turn_on(
        self,
        entity_id: str,
        *,
        confirm: bool = False,
    ) -> bool:
        """Send turn_on with safety OFF recovery if execution/confirmation fails."""
        if not entity_id:
            return False
        gen = self._next_generation(entity_id)
        sent = await self._call_switch_service(entity_id, SERVICE_TURN_ON)
        if not sent:
            self.last_error[entity_id] = "turn_on_failed"
            await self.async_turn_off(entity_id, confirm=False)
            return False

        if self._generations.get(entity_id) != gen:
            return False

        if confirm and self.confirm_timeout > 0:
            confirmed = await self._wait_for_state(
                entity_id, STATE_ON, gen, self.confirm_timeout
            )
            if self._generations.get(entity_id) != gen:
                return False
            if not confirmed:
                _LOGGER.error(
                    "Switch %s did not confirm ON within %.1fs; forcing OFF recovery",
                    entity_id,
                    self.confirm_timeout,
                )
                self.last_error[entity_id] = "on_unconfirmed"
                await self.async_turn_off(entity_id, confirm=False)
                return False

        self.last_error.pop(entity_id, None)
        return True

    async def async_turn_off(
        self,
        entity_id: str,
        *,
        confirm: bool = True,
    ) -> bool:
        """Immediately preempt pending ON and execute bounded retry until OFF confirms."""
        if not entity_id:
            return False
        gen = self._next_generation(entity_id)
        delays = (0.0, *self.off_retry_delays)

        for delay in delays:
            if delay > 0:
                await asyncio.sleep(delay)
            if self._generations.get(entity_id) != gen:
                return False

            sent = await self._call_switch_service(entity_id, SERVICE_TURN_OFF)
            if self._generations.get(entity_id) != gen:
                return False
            if not sent:
                continue

            st = self.hass.states.get(entity_id)
            if not confirm or self.confirm_timeout <= 0 or st is None or st.state == STATE_OFF:
                self.last_error.pop(entity_id, None)
                return True

            confirmed = await self._wait_for_state(
                entity_id, STATE_OFF, gen, self.confirm_timeout
            )
            if self._generations.get(entity_id) != gen:
                return False
            if confirmed:
                self.last_error.pop(entity_id, None)
                return True

        self.last_error[entity_id] = "off_unconfirmed"
        _LOGGER.error("Switch %s could not confirm OFF after bounded retries", entity_id)
        return False
