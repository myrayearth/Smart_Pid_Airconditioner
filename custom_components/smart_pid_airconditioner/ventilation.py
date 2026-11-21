"""4-state ventilation guard for Smart PID Airconditioner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
import homeassistant.util.dt as dt_util

from .const import (
    DEFAULT_VENT_COOLDOWN_MIN,
    DEFAULT_VENT_DEBOUNCE_SEC,
    DEFAULT_VENT_LOGIC,
    DEFAULT_VENT_MODE,
    DEFAULT_VENT_UNAVAILABLE_GRACE_SEC,
    VENT_LOGIC_ALL,
    VENT_LOGIC_ANY,
    VENT_MODE_COMBINED,
    VENT_MODE_DISABLED,
    VENT_MODE_SENSORS,
    VENT_MODE_SWITCH,
)


class VentState(str, Enum):
    """4-state ventilation state machine."""

    NORMAL = "normal"
    OPEN_DEBOUNCE = "open_debounce"
    VENTILATING = "ventilating"
    POST_CLOSE_COOLDOWN = "post_close_cooldown"


@dataclass(frozen=True)
class VentStatus:
    """Snapshot of ventilation guard evaluation."""

    state: VentState
    should_pause_control: bool
    should_block_learning: bool
    cooldown_remaining_sec: int
    degraded: bool = False


class VentilationGuard:
    """Monitors door/window sensors and ventilation switches with debounce & cooldown."""

    def __init__(
        self,
        mode: str = DEFAULT_VENT_MODE,
        sensors: list[str] | None = None,
        logic: str = DEFAULT_VENT_LOGIC,
        switch_entity: str | None = None,
        debounce_sec: int = DEFAULT_VENT_DEBOUNCE_SEC,
        cooldown_min: int = DEFAULT_VENT_COOLDOWN_MIN,
        unavailable_grace_sec: int = DEFAULT_VENT_UNAVAILABLE_GRACE_SEC,
    ) -> None:
        self.mode = mode or DEFAULT_VENT_MODE
        self.sensors = [s for s in (sensors or []) if s]
        self.logic = logic or DEFAULT_VENT_LOGIC
        self.switch_entity = switch_entity or None
        self.debounce_sec = max(0, int(debounce_sec))
        self.cooldown_min = max(0, int(cooldown_min))
        self.unavailable_grace_sec = max(0, int(unavailable_grace_sec))

        self._state: VentState = VentState.NORMAL
        self._debounce_start: datetime | None = None
        self._cooldown_end: datetime | None = None
        self._unavailable_since: datetime | None = None
        self._last_known_open: bool = False
        self._degraded: bool = False

    @property
    def tracked_entities(self) -> list[str]:
        """Return list of entity IDs to track for state changes."""
        if self.mode == VENT_MODE_DISABLED:
            return []
        entities: list[str] = []
        if self.mode in (VENT_MODE_SENSORS, VENT_MODE_COMBINED):
            entities.extend(self.sensors)
        if self.mode in (VENT_MODE_SWITCH, VENT_MODE_COMBINED) and self.switch_entity:
            entities.append(self.switch_entity)
        return list(dict.fromkeys(entities))

    @property
    def state(self) -> VentState:
        """Return current ventilation state."""
        return self._state

    def _read_raw_open(self, hass: HomeAssistant) -> tuple[bool | None, bool]:
        """Read configured sources. Returns (is_open_or_None_if_all_unavailable, all_unavailable)."""
        sensor_open: bool | None = None
        switch_open: bool | None = None

        if self.mode in (VENT_MODE_SENSORS, VENT_MODE_COMBINED) and self.sensors:
            valid_states: list[bool] = []
            for eid in self.sensors:
                st = hass.states.get(eid)
                if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    continue
                valid_states.append(st.state.lower() == STATE_ON)
            if valid_states:
                if self.logic == VENT_LOGIC_ANY:
                    sensor_open = any(valid_states)
                else:
                    sensor_open = all(valid_states)

        if self.mode in (VENT_MODE_SWITCH, VENT_MODE_COMBINED) and self.switch_entity:
            st = hass.states.get(self.switch_entity)
            if st is not None and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                switch_open = (st.state.lower() == STATE_ON)

        if self.mode == VENT_MODE_SENSORS:
            if not self.sensors:
                return False, False
            return sensor_open, (sensor_open is None)

        if self.mode == VENT_MODE_SWITCH:
            if not self.switch_entity:
                return False, False
            return switch_open, (switch_open is None)

        if self.mode == VENT_MODE_COMBINED:
            if not self.sensors and not self.switch_entity:
                return False, False
            if sensor_open is None and switch_open is None:
                return None, True
            return bool(sensor_open) or bool(switch_open), False

        return False, False

    def evaluate(self, hass: HomeAssistant, now: datetime | None = None) -> VentStatus:
        """Evaluate current ventilation state and control/learning flags."""
        if now is None:
            now = dt_util.utcnow()

        if self.mode == VENT_MODE_DISABLED:
            self._state = VentState.NORMAL
            self._debounce_start = None
            self._cooldown_end = None
            self._unavailable_since = None
            self._degraded = False
            return VentStatus(
                state=VentState.NORMAL,
                should_pause_control=False,
                should_block_learning=False,
                cooldown_remaining_sec=0,
                degraded=False,
            )

        raw_open, all_unavail = self._read_raw_open(hass)

        if all_unavail or raw_open is None:
            if self._unavailable_since is None:
                self._unavailable_since = now
            unavail_elapsed = (now - self._unavailable_since).total_seconds()
            if unavail_elapsed <= self.unavailable_grace_sec:
                # Within 5-minute grace period: hold previous state
                is_open = self._last_known_open
                self._degraded = False
            else:
                # Beyond 5 minutes: degraded mode -> safe control (not paused), block learning
                self._degraded = True
                self._state = VentState.NORMAL
                self._debounce_start = None
                self._cooldown_end = None
                return VentStatus(
                    state=VentState.NORMAL,
                    should_pause_control=False,
                    should_block_learning=True,
                    cooldown_remaining_sec=0,
                    degraded=True,
                )
        else:
            self._unavailable_since = None
            self._degraded = False
            self._last_known_open = raw_open
            is_open = raw_open

        # 4-state transitions
        if self._state == VentState.NORMAL:
            if is_open:
                if self.debounce_sec == 0:
                    self._state = VentState.VENTILATING
                    self._debounce_start = None
                else:
                    self._state = VentState.OPEN_DEBOUNCE
                    self._debounce_start = now

        elif self._state == VentState.OPEN_DEBOUNCE:
            if not is_open:
                self._state = VentState.NORMAL
                self._debounce_start = None
            elif self._debounce_start is not None:
                elapsed = (now - self._debounce_start).total_seconds()
                if elapsed >= self.debounce_sec:
                    self._state = VentState.VENTILATING
                    self._debounce_start = None

        elif self._state == VentState.VENTILATING:
            if not is_open:
                if self.cooldown_min == 0:
                    self._state = VentState.NORMAL
                    self._cooldown_end = None
                else:
                    self._state = VentState.POST_CLOSE_COOLDOWN
                    self._cooldown_end = now + timedelta(minutes=self.cooldown_min)

        elif self._state == VentState.POST_CLOSE_COOLDOWN:
            if is_open:
                self._state = VentState.VENTILATING
                self._cooldown_end = None
            elif self._cooldown_end is not None and now >= self._cooldown_end:
                self._state = VentState.NORMAL
                self._cooldown_end = None

        cooldown_rem = 0
        if self._state == VentState.POST_CLOSE_COOLDOWN and self._cooldown_end is not None:
            cooldown_rem = max(0, int((self._cooldown_end - now).total_seconds()))

        should_pause = self._state == VentState.VENTILATING
        should_block = self._state in (
            VentState.OPEN_DEBOUNCE,
            VentState.VENTILATING,
            VentState.POST_CLOSE_COOLDOWN,
        )

        return VentStatus(
            state=self._state,
            should_pause_control=should_pause,
            should_block_learning=should_block,
            cooldown_remaining_sec=cooldown_rem,
            degraded=False,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize guard configuration & state."""
        return {
            "state": self._state.value,
            "degraded": self._degraded,
            "last_known_open": self._last_known_open,
        }
