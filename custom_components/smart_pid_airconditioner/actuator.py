"""Bounded switch command guard with OFF preemption, in-flight ON tracking, timeout, and retry."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    DEFAULT_SWITCH_CONFIRM_TIMEOUT,
    DEFAULT_SWITCH_OFF_RETRY_DELAYS,
)

_LOGGER = logging.getLogger(__name__)
_UNVERIFIED_STATES: frozenset[str | None] = frozenset(
    {None, "", STATE_UNKNOWN, STATE_UNAVAILABLE}
)


class SwitchCommandGuard:
    """Confirm switch ON/OFF commands, preempt ON on OFF, and retry failed OFF."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        confirm_timeout: float = DEFAULT_SWITCH_CONFIRM_TIMEOUT,
        off_retry_delays: tuple[float, ...] = DEFAULT_SWITCH_OFF_RETRY_DELAYS,
        service_timeout: float | None = None,
    ) -> None:
        self.hass = hass
        self.confirm_timeout = float(confirm_timeout)
        self.off_retry_delays = tuple(float(d) for d in off_retry_delays)
        self.service_timeout = (
            max(0.05, float(service_timeout))
            if service_timeout is not None
            else max(self.confirm_timeout, 2.0)
        )
        self._generations: dict[str, int] = {}
        self._off_requested_gen: dict[str, int] = {}
        self._pending_off: set[str] = set()
        self._inflight_on_counts: dict[str, int] = {}
        self._inflight_on_done: dict[str, asyncio.Event] = {}
        self.last_error: dict[str, str] = {}
        self.failure_counts: dict[str, int] = {}

    def _is_confirmed_state(self, entity_id: str, desired_state: str) -> bool:
        """Return True only when state exists, is known/available, and matches desired_state."""
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _UNVERIFIED_STATES:
            return False
        return st.state == desired_state

    def _is_confirmed_off(self, entity_id: str) -> bool:
        """Return True only when switch state is explicitly confirmed OFF."""
        return self._is_confirmed_state(entity_id, STATE_OFF)

    def has_inflight_on(self, entity_id: str) -> bool:
        """Return True when a turn_on service call is currently executing for entity_id."""
        return self._inflight_on_counts.get(entity_id, 0) > 0

    def is_confirmed_off(self, entity_id: str) -> bool:
        """Public wrapper returning True only when explicitly OFF and no turn_on is in flight."""
        return self._is_confirmed_off(entity_id) and not self.has_inflight_on(entity_id)

    def is_off_pending(self, entity_id: str) -> bool:
        """Return True if an OFF confirmation retry sequence is currently in progress."""
        return entity_id in self._pending_off

    def _next_generation(self, entity_id: str) -> int:
        gen = self._generations.get(entity_id, 0) + 1
        self._generations[entity_id] = gen
        return gen

    def _was_preempted_by_off(self, entity_id: str, on_gen: int) -> bool:
        """Return True if an OFF request superseded on_gen without a newer ON superseding that OFF."""
        off_gen = self._off_requested_gen.get(entity_id, 0)
        if off_gen <= on_gen:
            return entity_id in self._pending_off
        cur_gen = self._generations.get(entity_id, 0)
        return cur_gen == off_gen or entity_id in self._pending_off

    async def _async_corrective_off(self, entity_id: str) -> None:
        """Send corrective OFF when a delayed ON finishes after an OFF preemption."""
        if entity_id in self._pending_off:
            return
        if self._is_confirmed_off(entity_id) and not self.has_inflight_on(entity_id):
            return
        _LOGGER.warning(
            "Preempted turn_on completed for %s after OFF; issuing corrective turn_off",
            entity_id,
        )
        await self.async_turn_off(entity_id, confirm=True)

    async def _call_switch_service(
        self,
        entity_id: str,
        service: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        target_domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
        call_timeout = self.service_timeout if timeout is None else max(0.01, float(timeout))
        try:
            async with asyncio.timeout(call_timeout):
                await self.hass.services.async_call(
                    target_domain,
                    service,
                    {ATTR_ENTITY_ID: entity_id},
                    blocking=True,
                )
            return True
        except TimeoutError:
            _LOGGER.warning(
                "Switch service %s.%s timed out after %.2fs for %s",
                target_domain,
                service,
                call_timeout,
                entity_id,
            )
            return False
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
                    async with asyncio.timeout(call_timeout):
                        await self.hass.services.async_call(
                            "homeassistant",
                            service,
                            {ATTR_ENTITY_ID: entity_id},
                            blocking=True,
                        )
                    return True
                except TimeoutError:
                    _LOGGER.error(
                        "Fallback homeassistant.%s timed out after %.2fs for %s",
                        service,
                        call_timeout,
                        entity_id,
                    )
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
        def _is_ready() -> bool:
            if self._generations.get(entity_id) != generation:
                return False
            if not self._is_confirmed_state(entity_id, desired_state):
                return False
            if desired_state == STATE_OFF and self.has_inflight_on(entity_id):
                return False
            return True

        if _is_ready():
            return True
        if timeout <= 0:
            return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        event = asyncio.Event()

        @callback
        def _on_change(ev: Event) -> None:
            new_st = ev.data.get("new_state") if isinstance(ev.data, Mapping) else None
            if (
                new_st is not None
                and new_st.state not in _UNVERIFIED_STATES
                and new_st.state == desired_state
            ):
                event.set()

        unsub = async_track_state_change_event(self.hass, [entity_id], _on_change)
        try:
            while True:
                if self._generations.get(entity_id) != generation:
                    return False
                if _is_ready():
                    return True
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return _is_ready()
                event.clear()
                if _is_ready():
                    return True
                try:
                    await asyncio.wait_for(event.wait(), timeout=remaining)
                except TimeoutError:
                    return _is_ready()
                if _is_ready():
                    return True
        finally:
            unsub()

    async def async_turn_on(
        self,
        entity_id: str,
        *,
        confirm: bool = False,
    ) -> bool:
        """Send turn_on with safety OFF recovery; blocked while OFF confirmation is pending."""
        if not entity_id:
            return False
        if entity_id in self._pending_off:
            _LOGGER.warning(
                "Blocked turn_on for %s because an OFF command is still pending confirmation",
                entity_id,
            )
            return False
        gen = self._next_generation(entity_id)
        done_ev = self._inflight_on_done.get(entity_id)
        if done_ev is None:
            done_ev = asyncio.Event()
            self._inflight_on_done[entity_id] = done_ev
        done_ev.clear()
        self._inflight_on_counts[entity_id] = self._inflight_on_counts.get(entity_id, 0) + 1
        try:
            sent = await self._call_switch_service(entity_id, SERVICE_TURN_ON)
        finally:
            rem_count = self._inflight_on_counts.get(entity_id, 1) - 1
            if rem_count <= 0:
                self._inflight_on_counts.pop(entity_id, None)
                done_ev.set()
            else:
                self._inflight_on_counts[entity_id] = rem_count

        if self._generations.get(entity_id) != gen:
            if self._was_preempted_by_off(entity_id, gen):
                await self._async_corrective_off(entity_id)
            return False

        if not sent:
            self.failure_counts[entity_id] = self.failure_counts.get(entity_id, 0) + 1
            self.last_error[entity_id] = "turn_on_failed"
            await self.async_turn_off(entity_id, confirm=False)
            return False

        if confirm and self.confirm_timeout > 0:
            confirmed = await self._wait_for_state(
                entity_id, STATE_ON, gen, self.confirm_timeout
            )
            if self._generations.get(entity_id) != gen:
                if self._was_preempted_by_off(entity_id, gen):
                    await self._async_corrective_off(entity_id)
                return False
            if not confirmed:
                _LOGGER.error(
                    "Switch %s did not confirm ON within %.1fs; forcing OFF recovery",
                    entity_id,
                    self.confirm_timeout,
                )
                self.failure_counts[entity_id] = self.failure_counts.get(entity_id, 0) + 1
                self.last_error[entity_id] = "on_unconfirmed"
                await self.async_turn_off(entity_id, confirm=False)
                return False

        self.last_error.pop(entity_id, None)
        self.failure_counts.pop(entity_id, None)
        return True

    async def async_turn_off(
        self,
        entity_id: str,
        *,
        confirm: bool = True,
        deadline_timeout: float | None = None,
    ) -> bool:
        """Immediately preempt pending ON and execute bounded retry until OFF confirms."""
        if not entity_id:
            return False
        gen = self._next_generation(entity_id)
        self._off_requested_gen[entity_id] = gen
        self._pending_off.add(entity_id)
        delays = (0.0, *self.off_retry_delays)
        loop = asyncio.get_running_loop()
        total_budget = (
            max(0.02, float(deadline_timeout))
            if deadline_timeout is not None
            else (
                max(self.service_timeout, self.confirm_timeout) * len(delays)
                + sum(self.off_retry_delays)
                + 0.5
            )
        )
        deadline = loop.time() + total_budget

        try:
            for delay in delays:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                if delay > 0:
                    await asyncio.sleep(min(delay, remaining))
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                if self._generations.get(entity_id) != gen:
                    return False

                had_inflight_on = self.has_inflight_on(entity_id)
                sent = await self._call_switch_service(
                    entity_id,
                    SERVICE_TURN_OFF,
                    timeout=min(remaining, self.service_timeout),
                )
                if self._generations.get(entity_id) != gen:
                    return False

                # If an earlier turn_on was still running when OFF arrived, wait for it to finish
                # and re-issue a final OFF before ever accepting OFF confirmation.
                if had_inflight_on or self.has_inflight_on(entity_id):
                    done_ev = self._inflight_on_done.get(entity_id)
                    if done_ev is not None and self.has_inflight_on(entity_id):
                        wait_rem = deadline - loop.time()
                        if wait_rem <= 0:
                            break
                        try:
                            async with asyncio.timeout(wait_rem):
                                await done_ev.wait()
                        except TimeoutError:
                            break
                    if self._generations.get(entity_id) != gen:
                        return False
                    post_rem = deadline - loop.time()
                    if post_rem <= 0:
                        break
                    sent = await self._call_switch_service(
                        entity_id,
                        SERVICE_TURN_OFF,
                        timeout=min(post_rem, self.service_timeout),
                    )
                    if self._generations.get(entity_id) != gen:
                        return False

                if self.has_inflight_on(entity_id):
                    continue
                if not sent:
                    continue

                if not confirm or self.confirm_timeout <= 0:
                    self.last_error.pop(entity_id, None)
                    self.failure_counts.pop(entity_id, None)
                    return True

                if self._is_confirmed_off(entity_id) and not self.has_inflight_on(entity_id):
                    self.last_error.pop(entity_id, None)
                    self.failure_counts.pop(entity_id, None)
                    return True

                wait_rem = deadline - loop.time()
                if wait_rem <= 0:
                    break
                confirmed = await self._wait_for_state(
                    entity_id,
                    STATE_OFF,
                    gen,
                    min(wait_rem, self.confirm_timeout),
                )
                if self._generations.get(entity_id) != gen:
                    return False
                if confirmed and not self.has_inflight_on(entity_id):
                    self.last_error.pop(entity_id, None)
                    self.failure_counts.pop(entity_id, None)
                    return True

            self.failure_counts[entity_id] = self.failure_counts.get(entity_id, 0) + 1
            final_st = self.hass.states.get(entity_id)
            final_val = final_st.state if final_st is not None else None
            if self.has_inflight_on(entity_id):
                self.last_error[entity_id] = "inflight_on_pending"
                _LOGGER.warning(
                    "Switch %s OFF unconfirmed because turn_on is still in flight (failures=%d)",
                    entity_id,
                    self.failure_counts[entity_id],
                )
            elif final_val in _UNVERIFIED_STATES:
                self.last_error[entity_id] = "unverified_off"
                _LOGGER.warning(
                    "Switch %s OFF state unverified (state=%r) after bounded retries (failures=%d)",
                    entity_id,
                    final_val,
                    self.failure_counts[entity_id],
                )
            else:
                self.last_error[entity_id] = "off_unconfirmed"
                _LOGGER.warning(
                    "Switch %s remained '%s' (OFF unconfirmed) after bounded retries (failures=%d)",
                    entity_id,
                    final_val,
                    self.failure_counts[entity_id],
                )
            return False
        finally:
            if self._generations.get(entity_id) == gen:
                self._pending_off.discard(entity_id)
