"""Shared configuration, entity, and sensor parsing utilities for Smart PID."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import event as ha_event
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData

from .const import (
    CONF_VENT_COOLDOWN_MIN,
    CONF_VENT_DEBOUNCE_SEC,
    CONF_VENT_LOGIC,
    CONF_VENT_MODE,
    CONF_VENT_SENSORS,
    CONF_VENT_SWITCH,
    DEFAULT_VENT_COOLDOWN_MIN,
    DEFAULT_VENT_DEBOUNCE_SEC,
    DEFAULT_VENT_LOGIC,
    DEFAULT_VENT_MODE,
    DOMAIN,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    duration_to_seconds,
)
from .ventilation import VentilationGuard

DEFAULT_SETTING_SECTIONS: tuple[str, ...] = (
    "adaptive_learning",
    "ki_group",
    "section_0",
    "section_1",
    "section_2",
    "section_3",
    "section_4",
    "ventilation",
)

_INVALID_STATES = frozenset({"unknown", "unavailable", "none", "null", "restored", ""})


def get_entry_setting(
    entry: ConfigEntry,
    key: str,
    default: Any = None,
    *,
    sections: tuple[str, ...] = DEFAULT_SETTING_SECTIONS,
) -> Any:
    """Resolve a setting from entry.options first, then entry.data, checking nested sections."""
    target_sec: str | None = None
    for i in range(0, 5):
        if key.startswith(f"sec{i}_"):
            target_sec = f"section_{i}"
            break

    for source in (getattr(entry, "options", None), getattr(entry, "data", None)):
        if not isinstance(source, Mapping):
            continue
        if target_sec and target_sec in source:
            sec_dict = source[target_sec]
            if isinstance(sec_dict, Mapping) and key in sec_dict and sec_dict[key] is not None:
                return sec_dict[key]
        val = source.get(key)
        if val is not None:
            return val
        for sec in sections:
            sec_dict = source.get(sec)
            if isinstance(sec_dict, Mapping):
                sec_val = sec_dict.get(key)
                if sec_val is not None:
                    return sec_val
    return default


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build standard DeviceInfo linking all Smart PID entities to one HA device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Smart PID",
        model="Smart PID Controller",
    )


def run_async_on_loop(hass: HomeAssistant, coro: Any) -> None:
    """Execute a coroutine safely on the Home Assistant event loop."""
    try:
        loop = hass.loop
        if loop.is_running():
            loop.call_soon_threadsafe(lambda: hass.async_create_task(coro))
        else:
            hass.async_create_task(coro)
    except Exception:
        hass.async_create_task(coro)


def build_ventilation_guard(get_setting: Any) -> VentilationGuard:
    """Build a VentilationGuard from a setting getter callable."""
    vent_mode = str(get_setting(CONF_VENT_MODE, DEFAULT_VENT_MODE))
    vent_sensors = get_setting(CONF_VENT_SENSORS, []) or []
    if isinstance(vent_sensors, str):
        vent_sensors = [vent_sensors] if vent_sensors else []
    vent_logic = str(get_setting(CONF_VENT_LOGIC, DEFAULT_VENT_LOGIC))
    vent_switch = get_setting(CONF_VENT_SWITCH, None)
    vent_debounce = duration_to_seconds(
        get_setting(CONF_VENT_DEBOUNCE_SEC, DEFAULT_VENT_DEBOUNCE_SEC),
        DEFAULT_VENT_DEBOUNCE_SEC,
    )
    vent_cooldown = int(
        float(get_setting(CONF_VENT_COOLDOWN_MIN, DEFAULT_VENT_COOLDOWN_MIN))
    )
    return VentilationGuard(
        mode=vent_mode,
        sensors=list(vent_sensors),
        logic=vent_logic,
        switch_entity=str(vent_switch) if vent_switch else None,
        debounce_sec=vent_debounce,
        cooldown_min=vent_cooldown,
    )


def resolve_type_label(thermostat_type: str) -> str:
    """Map thermostat_type constant to human-readable English title label."""
    if thermostat_type == THERMOSTAT_TYPE_HEATER:
        return "Heater"
    if thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
        return "Heat/Cool"
    if thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
        return "Dehumidifier"
    if thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
        return "Humidifier"
    if thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
        return "Humidity/Dehumidity"
    return "Cooler"


_TEMP_STR_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][+-]?\d+)?)\s*(?:°\s*([cCfFkK])|([℃℉])|([cCfFkK]))?\s*$"
)


def _is_finite_number(val: Any) -> bool:
    """Return True if val is a real finite int/float (excluding bool, NaN, Inf)."""
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def _normalize_temp_unit(unit: Any) -> str | None:
    """Normalize a temperature unit enum/string to 'C', 'F', or 'K'."""
    if unit is None:
        return None
    raw = unit.value if hasattr(unit, "value") else str(unit)
    norm = str(raw).strip().upper().replace("°", "").replace(" ", "")
    if norm in ("F", "℉", "FAHRENHEIT"):
        return "F"
    if norm in ("K", "KELVIN"):
        return "K"
    if norm in ("C", "℃", "CELSIUS"):
        return "C"
    return None


def _convert_temperature_to_celsius(val: float, unit: Any) -> float:
    """Convert temperature value to Celsius when unit is Fahrenheit or Kelvin."""
    norm = _normalize_temp_unit(unit)
    if norm == "F":
        return (val - 32.0) * 5.0 / 9.0
    if norm == "K":
        return val - 273.15
    return val


def _parse_temp_string(raw: str) -> tuple[float | None, str | None]:
    """Parse a strict finite temperature string and optional inline unit suffix."""
    if raw.strip().lower() in _INVALID_STATES:
        return None, None
    match = _TEMP_STR_RE.fullmatch(raw)
    if match is None:
        return None, None
    num_str = match.group(1).replace(",", ".")
    try:
        num = float(num_str)
    except (ValueError, TypeError):
        return None, None
    if not math.isfinite(num):
        return None, None
    inline_unit = match.group(2) or match.group(3) or match.group(4)
    return num, inline_unit


def parse_temperature_state(state_obj: Any, *, default_unit: Any = None) -> float | None:
    """Extract a finite Celsius float temperature from a Home Assistant State object."""
    if state_obj is None:
        return None
    if isinstance(state_obj, (int, float, bool)):
        if not _is_finite_number(state_obj):
            return None
        c_val = _convert_temperature_to_celsius(float(state_obj), default_unit)
        return c_val if math.isfinite(c_val) else None

    raw_attrs = getattr(state_obj, "attributes", None)
    attrs: Mapping[str, Any] = raw_attrs if isinstance(raw_attrs, Mapping) else {}
    state_unit = attrs.get("unit_of_measurement") or attrs.get("temperature_unit") or default_unit

    state_val = getattr(state_obj, "state", None)
    if isinstance(state_val, (int, float, bool)):
        if _is_finite_number(state_val):
            c_val = _convert_temperature_to_celsius(float(state_val), state_unit)
            if math.isfinite(c_val):
                return c_val
    elif isinstance(state_val, str):
        parsed, inline_unit = _parse_temp_string(state_val)
        if parsed is not None:
            c_val = _convert_temperature_to_celsius(parsed, inline_unit or state_unit)
            if math.isfinite(c_val):
                return c_val

    attr_unit = attrs.get("temperature_unit") or attrs.get("unit_of_measurement") or default_unit
    for attr in (
        "temperature",
        "native_temperature",
        "current_temperature",
        "temp",
        "target_temperature",
        "apparent_temperature",
    ):
        val = attrs.get(attr)
        if val is None:
            continue
        if isinstance(val, (int, float, bool)):
            if _is_finite_number(val):
                c_val = _convert_temperature_to_celsius(float(val), attr_unit)
                if math.isfinite(c_val):
                    return c_val
        elif isinstance(val, str):
            parsed, inline_unit = _parse_temp_string(val)
            if parsed is not None:
                c_val = _convert_temperature_to_celsius(parsed, inline_unit or attr_unit)
                if math.isfinite(c_val):
                    return c_val
    return None


def parse_humidity_state(state_obj: Any) -> float | None:
    """Extract a finite float humidity value (%) from a Home Assistant State object."""
    if state_obj is None:
        return None
    if isinstance(state_obj, (int, float, bool)):
        return float(state_obj) if _is_finite_number(state_obj) else None

    state_val = getattr(state_obj, "state", None)
    if isinstance(state_val, (int, float, bool)):
        if _is_finite_number(state_val):
            return float(state_val)
    elif isinstance(state_val, str):
        if state_val.strip().lower() not in _INVALID_STATES:
            cleaned = state_val.replace("%", "").replace(",", ".").strip()
            try:
                num = float(cleaned)
                if math.isfinite(num):
                    return num
            except (ValueError, TypeError):
                pass

    raw_attrs = getattr(state_obj, "attributes", None)
    attrs: Mapping[str, Any] = raw_attrs if isinstance(raw_attrs, Mapping) else {}
    for attr in ("humidity", "relative_humidity", "current_humidity", "target_humidity"):
        val = attrs.get(attr)
        if val is None:
            continue
        if isinstance(val, (int, float, bool)):
            if _is_finite_number(val):
                return float(val)
        elif isinstance(val, str):
            if val.strip().lower() in _INVALID_STATES:
                continue
            cleaned = val.replace("%", "").replace(",", ".").strip()
            try:
                num = float(cleaned)
                if math.isfinite(num):
                    return num
            except (ValueError, TypeError):
                pass
    return None


def get_state_report_time(state_obj: Any) -> datetime | None:
    """Return the most recent report/update timestamp present on a HA State object."""
    if state_obj is None:
        return None
    for attr in ("last_reported", "last_updated", "last_changed"):
        val = getattr(state_obj, attr, None)
        if isinstance(val, datetime):
            return val
    return None


def is_state_unavailable(hass: HomeAssistant | None, entity_id: str | None) -> bool | None:
    """Return True if entity state is unavailable/unknown, False if valid, None if missing."""
    if not entity_id or hass is None:
        return None
    st = hass.states.get(entity_id)
    if st is None:
        return None
    return st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)


def async_subscribe_state_reported(
    hass: HomeAssistant,
    entity_ids: Iterable[str],
    action: Callable[[Event], Any],
) -> CALLBACK_TYPE | None:
    """Subscribe to HA `state_reported` events for tracked entity IDs safely.

    Uses `async_track_state_report_event` on HA 2024.5+/2026.x where raw
    unfiltered `state_reported` bus listeners are rejected, falling back to a
    filtered `hass.bus.async_listen` (or legacy unfiltered mock signature).
    """
    tracked = list(dict.fromkeys(e for e in entity_ids if e))
    if not tracked:
        return None

    track_report = getattr(ha_event, "async_track_state_report_event", None)
    if callable(track_report):
        return track_report(hass, tracked, action)

    bus = getattr(hass, "bus", None)
    async_listen = getattr(bus, "async_listen", None)
    if not callable(async_listen):
        return None

    tracked_set = set(tracked)

    @callback
    def _event_filter(event_data: Mapping[str, Any]) -> bool:
        return isinstance(event_data, Mapping) and event_data.get(ATTR_ENTITY_ID) in tracked_set

    try:
        return async_listen("state_reported", action, event_filter=_event_filter)
    except TypeError:
        return async_listen("state_reported", action)


@dataclass
class SmartPIDExtraStoredData(ExtraStoredData):
    """Extra stored data for Smart PID Thermostat / Humidifier state restoration."""

    last_switch_on_times: dict[str, str]
    last_switch_off_times: dict[str, str]
    active_section: int
    integral: float
    last_run_duration_sec: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the extra data."""
        return {
            "last_switch_on_times": dict(self.last_switch_on_times),
            "last_switch_off_times": dict(self.last_switch_off_times),
            "active_section": self.active_section,
            "integral": self.integral,
            "last_run_duration_sec": self.last_run_duration_sec,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> SmartPIDExtraStoredData | None:
        """Initialize extra stored data from a mapping."""
        if not isinstance(data, Mapping):
            return None
        try:
            on_times = {
                str(k): str(v)
                for k, v in (data.get("last_switch_on_times") or {}).items()
                if v is not None
            }
            off_times = {
                str(k): str(v)
                for k, v in (data.get("last_switch_off_times") or {}).items()
                if v is not None
            }
            active_sec = int(data.get("active_section", 1))
            integral = float(data.get("integral", 0.0))
            if not math.isfinite(integral):
                integral = 0.0
            last_run_dur = float(data.get("last_run_duration_sec", 0.0))
            last_run_dur = max(0.0, last_run_dur) if math.isfinite(last_run_dur) else 0.0
            return cls(
                last_switch_on_times=on_times,
                last_switch_off_times=off_times,
                active_section=active_sec,
                integral=integral,
                last_run_duration_sec=last_run_dur,
            )
        except (ValueError, TypeError):
            return None


class SmartPIDRuntimeEntityMixin:
    """Shared mixin for entities subscribed to SmartPIDRuntime updates."""

    _attr_has_entity_name = True

    def _init_runtime_entity(
        self,
        entry: ConfigEntry,
        runtime: Any,
        key: str,
    ) -> None:
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = build_device_info(entry)
        self._unsub: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()  # type: ignore[misc]
        self._unsub = self._runtime.subscribe(self._on_runtime_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _on_runtime_update(self) -> None:
        self.async_write_ha_state()  # type: ignore[attr-defined]
