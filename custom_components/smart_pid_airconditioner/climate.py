"""Climate platform for Smart PID Thermostat."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    Platform,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
import homeassistant.util.dt as dt_util

from .const import (
    DOMAIN,
    ALL_FAN_MODES,
    ALL_HVAC_MODES,
    CONF_AVAILABLE_FAN_MODES,
    CONF_AVAILABLE_HVAC_MODES,
    CONF_INITIAL_TARGET_TEMP,
    CONF_MIN_TEMP,
    CONF_MAX_TEMP,
    CONF_INDOOR_SENSOR,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_OFF_TEMP_ACTION,
    CONF_OUTDOOR_SENSOR,
    CONF_PWM_DURATION,
    CONF_SAMPLE_PERIOD,
    CONF_SEC1_APPLY_TEMP,
    CONF_SEC1_APPLY_DELTA,
    CONF_SEC1_APPLY_FAN,
    CONF_SEC1_APPLY_HVAC_MODE,
    CONF_SEC1_DELTA,
    CONF_SEC1_FAN,
    CONF_SEC1_HVAC_MODE,
    CONF_SEC1_MAX,
    CONF_SEC2_APPLY_TEMP,
    CONF_SEC2_APPLY_DELTA,
    CONF_SEC2_APPLY_FAN,
    CONF_SEC2_APPLY_HVAC_MODE,
    CONF_SEC2_DELTA,
    CONF_SEC2_FAN,
    CONF_SEC2_HVAC_MODE,
    CONF_SEC2_MAX,
    CONF_SEC3_APPLY_TEMP,
    CONF_SEC3_APPLY_DELTA,
    CONF_SEC3_APPLY_FAN,
    CONF_SEC3_APPLY_HVAC_MODE,
    CONF_SEC3_DELTA,
    CONF_SEC3_FAN,
    CONF_SEC3_HVAC_MODE,
    CONF_SEC3_MAX,
    CONF_SEC4_APPLY_TEMP,
    CONF_SEC4_APPLY_DELTA,
    CONF_SEC4_APPLY_FAN,
    CONF_SEC4_APPLY_HVAC_MODE,
    CONF_SEC4_DELTA,
    CONF_SEC4_FAN,
    CONF_SEC4_HVAC_MODE,
    CONF_SYNC_SLAVE_STATE,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HUMIDITY,
    CONF_TEMP_STEP,
    CONF_THERMOSTAT_TYPE,
    CONF_CONTROL_MODE,
    CONF_KEEP_ALIVE,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_OUTPUT_SAFETY,
    CONTROL_MODES,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    DEFAULT_AVAILABLE_FAN_MODES,
    DEFAULT_AVAILABLE_HVAC_MODES,
    DEFAULT_AVAILABLE_HUMIDITY_MODES,
    DEFAULT_CONTROL_MODE,
    DEFAULT_INITIAL_TARGET_TEMP,
    DEFAULT_INITIAL_TARGET_HUMIDITY,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MIN_OFF_TIME,
    DEFAULT_MIN_ON_TIME,
    DEFAULT_MIN_TEMP,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_HUMIDITY,
    DEFAULT_MAX_HUMIDITY,
    DEFAULT_HUMIDITY_STEP,
    DEFAULT_SLAVE_MIN_HUMIDITY,
    DEFAULT_SLAVE_MAX_HUMIDITY,
    DEFAULT_KD,
    DEFAULT_KE,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MAX_I,
    DEFAULT_OFF_TEMP_ACTION,
    DEFAULT_OUTPUT_SAFETY,
    DEFAULT_PWM_DURATION,
    DEFAULT_PWM_DURATION_SWITCH,
    DEFAULT_SAMPLE_PERIOD,
    DEFAULT_SEC1_DELTA,
    DEFAULT_SEC1_FAN,
    DEFAULT_SEC1_MAX,
    DEFAULT_SEC2_DELTA,
    DEFAULT_SEC2_FAN,
    DEFAULT_SEC2_MAX,
    DEFAULT_SEC3_DELTA,
    DEFAULT_SEC3_FAN,
    DEFAULT_SEC3_MAX,
    DEFAULT_SEC4_DELTA,
    DEFAULT_SEC4_FAN,
    DEFAULT_SEC_APPLY_TEMP,
    DEFAULT_SEC_APPLY_DELTA,
    DEFAULT_SEC_APPLY_FAN,
    DEFAULT_SEC_APPLY_HVAC_MODE,
    DEFAULT_SEC_HVAC_MODE,
    DEFAULT_SLAVE_MAX_TEMP,
    DEFAULT_SLAVE_MIN_TEMP,
    DEFAULT_SYNC_SLAVE_STATE,
    DEFAULT_TEMP_STEP,
    DEFAULT_THERMOSTAT_TYPE,
    FAIL_SAFE_MULTIPLIER,
    FAIL_SAFE_OUTPUT,
    HYSTERESIS_BAND,
    OFF_ACTION_SYNC_ONLY,
    OFF_ACTION_TURN_ON,
    SUPPORTED_HVAC_MODES,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    ALL_HUMIDITY_MODES,
    MODE_OFF,
    MODE_AUTO,
    MODE_HUMIDITY,
    MODE_DEHUMIDITY,
    MODE_FAN_ONLY,
    MODE_SILENT,
    is_humidity_type,
    is_dual_controller,
    duration_to_seconds,
)

_LOGGER = logging.getLogger(__name__)

FAN_ALIAS_MAP: dict[str, list[str]] = {
    "turbo": [
        "turbo", "max", "highest", "strong", "high", "터보", "파워", "파워풍", "강력", "3", "4", "5",
    ],
    "high": [
        "high", "max", "strong", "turbo", "강", "강풍", "3단", "3", "2",
    ],
    "medium": [
        "medium", "med", "mid", "middle", "normal", "중", "중풍", "2단", "2", "1",
    ],
    "mid": [
        "mid", "medium", "med", "middle", "normal", "중", "중풍", "2단", "2", "1",
    ],
    "med": [
        "med", "medium", "mid", "middle", "normal", "중", "중풍", "2단", "2", "1",
    ],
    "middle": [
        "middle", "medium", "med", "mid", "normal", "중", "중풍", "2단", "2", "1",
    ],
    "low": [
        "low", "min", "quiet", "silent", "soft", "약", "약풍", "미풍", "1단", "1", "0",
    ],
    "auto": [
        "auto", "smart", "automatic", "자동", "스마트", "인공지능",
    ],
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart PID Thermostat climate entity from a config entry."""
    thermostat_type = entry.data.get(CONF_THERMOSTAT_TYPE, entry.options.get(CONF_THERMOSTAT_TYPE))

    # If this entry is for humidifier/dehumidifier, remove any stale climate entity and exit
    if is_humidity_type(thermostat_type):
        registry = er.async_get(hass)
        old_id = registry.async_get_entity_id(
            Platform.CLIMATE, DOMAIN, f"{entry.entry_id}_thermostat"
        )
        if old_id:
            registry.async_remove(old_id)
        return

    async_add_entities([SmartPIDThermostatEntity(hass, entry)], True)


class SmartPIDThermostatEntity(ClimateEntity, RestoreEntity):
    """Smart PID Thermostat Climate Entity."""

    _attr_has_entity_name = True
    _attr_translation_key = DOMAIN
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the thermostat entity."""
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_thermostat"
        self._attr_name = entry.title

        # Lock to prevent concurrent calculation loops and race conditions
        self._calc_lock = asyncio.Lock()

        # Read config / options
        self._load_config()

        # Target temperature & HVAC mode
        self._target_temp: float = self._initial_target_temp
        self._attr_hvac_mode = HVACMode.OFF

        # Sensor readings & timestamps
        self._indoor_temp: float | None = None
        self._outdoor_temp: float | None = None
        self._indoor_humidity: float | None = None
        self._outdoor_humidity: float | None = None
        self._last_indoor_update: datetime | None = None
        self._last_outdoor_update: datetime | None = None

        # PID algorithm state
        self._last_calc_time: datetime | None = None
        self._prev_room_temp: float | None = None
        self._prev_error: float | None = None
        self._integral: float = 0.0
        self._pid_error: float | None = None
        self._pid_p: float = 0.0
        self._pid_i: float = 0.0
        self._pid_d: float = 0.0
        self._pid_e: float = 0.0
        self._pid_output: float = 0.0
        self._active_section: int = 1
        self._raw_target_temperature: float = self._target_temp
        self._dispatched_temperature: int | None = None
        self._dispatched_fan_mode: str | None = None
        self._last_dispatched_temp: int | None = None
        self._last_dispatched_fan_mode: str | None = None
        self._last_dispatched_section: int | None = None
        self._fail_safe_active: bool = False
        self._is_heating: bool = (self._thermostat_type in (THERMOSTAT_TYPE_HEATER, THERMOSTAT_TYPE_HUMIDIFIER))

        # PWM & Anti-Short-Cycling control state
        self._pwm_timer_cancel: CALLBACK_TYPE | None = None
        self._pwm_turn_on_cancel: CALLBACK_TYPE | None = None
        self._pwm_turn_off_cancel: CALLBACK_TYPE | None = None
        self._pwm_cycle_cancel: CALLBACK_TYPE | None = None
        self._keep_alive_cancel: CALLBACK_TYPE | None = None
        self._pwm_state: bool = False
        self._pwm_cycle_start: datetime | None = None
        self._cycle_start_time: datetime | None = None
        self._cycle_end_time: datetime | None = None
        self._cycle_turn_off_time: datetime | None = None
        self._last_switch_on_time: datetime | None = None
        self._last_switch_off_time: datetime | None = None
        self._last_switch_on_times: dict[str, datetime] = {}
        self._last_switch_off_times: dict[str, datetime] = {}
        self._pwm_duty_cycle: float = 0.0
        self._pwm_on_time: float = 0.0
        self._sensor_stale_count: int = 0
        self._internal_pwm_slave_off: bool = False

        # Event tracking unsubscribers
        self._unsub_sensor_track: CALLBACK_TYPE | None = None
        self._unsub_interval_tick: CALLBACK_TYPE | None = None

        # Guard flags
        self._is_dispatching: bool = False
        self._is_dispatching_hvac_mode: bool = False
        self._last_mode_change_time: datetime | None = None
        self._last_mode_dispatch_time: datetime | None = None
        self._expected_slave_mode: HVACMode | None = None

    def _run_async(self, coro) -> None:
        """Execute a coroutine safely on the Home Assistant event loop."""
        try:
            loop = self.hass.loop
            if loop.is_running():
                loop.call_soon_threadsafe(lambda: self.hass.async_create_task(coro))
            else:
                self.hass.async_create_task(coro)
        except Exception:
            self.hass.async_create_task(coro)

    def _get_setting(self, key: str, default: Any) -> Any:
        """Helper to get value from options first, then entry data, then default, checking nested sections."""
        target_sec = None
        for i in range(1, 5):
            if key.startswith(f"sec{i}_"):
                target_sec = f"section_{i}"
                break

        # If key belongs to a section, check options[section_X][key] first
        if target_sec and target_sec in self._entry.options:
            sec_dict = self._entry.options[target_sec]
            if isinstance(sec_dict, dict) and key in sec_dict and sec_dict[key] is not None:
                return sec_dict[key]

        # Check top-level options
        val = self._entry.options.get(key)
        if val is not None:
            return val

        # Check all sections in options
        for sec in ("ki_group", "section_1", "section_2", "section_3", "section_4"):
            sec_dict = self._entry.options.get(sec)
            if isinstance(sec_dict, dict):
                sec_val = sec_dict.get(key)
                if sec_val is not None:
                    return sec_val

        # Check target section in entry data
        if target_sec and target_sec in self._entry.data:
            sec_dict = self._entry.data[target_sec]
            if isinstance(sec_dict, dict) and key in sec_dict and sec_dict[key] is not None:
                return sec_dict[key]

        # Check top-level entry data
        val = self._entry.data.get(key)
        if val is not None:
            return val

        # Check all sections in entry data
        for sec in ("ki_group", "section_1", "section_2", "section_3", "section_4"):
            sec_dict = self._entry.data.get(sec)
            if isinstance(sec_dict, dict):
                sec_val = sec_dict.get(key)
                if sec_val is not None:
                    return sec_val
        return default

    def _load_config(self) -> None:
        """Load configuration parameters."""
        self._target_device_id: str = str(
            self._get_setting(
                CONF_TARGET_DEVICE, self._get_setting(CONF_TARGET_CLIMATE, "")
            )
        )
        self._target_device_cool_id: str = str(
            self._get_setting(CONF_TARGET_DEVICE_COOL, "")
        )
        self._target_device_heat_id: str = str(
            self._get_setting(CONF_TARGET_DEVICE_HEAT, "")
        )
        self._target_device_dehumidity_id: str = str(
            self._get_setting(CONF_TARGET_DEVICE_DEHUMIDITY, "")
        )
        self._target_device_humidity_id: str = str(
            self._get_setting(CONF_TARGET_DEVICE_HUMIDITY, "")
        )
        self._target_climate_id: str = self._target_device_id
        self._control_mode: str = str(
            self._get_setting(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        )
        self._thermostat_type: str = str(
            self._get_setting(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)
        )
        self._indoor_sensor_id: str = self._get_setting(CONF_INDOOR_SENSOR, "")
        self._outdoor_sensor_id: str = self._get_setting(CONF_OUTDOOR_SENSOR, "")

        is_hum = is_humidity_type(self._thermostat_type)
        def_initial_target = DEFAULT_INITIAL_TARGET_HUMIDITY if is_hum else DEFAULT_INITIAL_TARGET_TEMP
        def_min_temp = DEFAULT_MIN_HUMIDITY if is_hum else DEFAULT_MIN_TEMP
        def_max_temp = DEFAULT_MAX_HUMIDITY if is_hum else DEFAULT_MAX_TEMP
        def_temp_step = DEFAULT_HUMIDITY_STEP if is_hum else DEFAULT_TEMP_STEP
        def_modes = DEFAULT_AVAILABLE_HUMIDITY_MODES if is_hum else DEFAULT_AVAILABLE_HVAC_MODES

        self._initial_target_temp: float = float(
            self._get_setting(CONF_INITIAL_TARGET_TEMP, def_initial_target)
        )
        self._min_temp: float = float(
            self._get_setting(CONF_MIN_TEMP, def_min_temp)
        )
        self._max_temp: float = float(
            self._get_setting(CONF_MAX_TEMP, def_max_temp)
        )
        self._temp_step: float = float(
            self._get_setting(CONF_TEMP_STEP, def_temp_step)
        )
        self._off_temp_action: str = str(
            self._get_setting(CONF_OFF_TEMP_ACTION, DEFAULT_OFF_TEMP_ACTION)
        )
        self._sync_slave_state: bool = bool(
            self._get_setting(CONF_SYNC_SLAVE_STATE, DEFAULT_SYNC_SLAVE_STATE)
        )
        self._kp: float = float(self._get_setting(CONF_KP, DEFAULT_KP))
        self._ki: float = float(self._get_setting(CONF_KI, DEFAULT_KI))
        self._max_i: float = float(self._get_setting(CONF_MAX_I, DEFAULT_MAX_I))
        self._kd: float = float(self._get_setting(CONF_KD, DEFAULT_KD))
        self._ke: float = float(self._get_setting(CONF_KE, DEFAULT_KE))
        self._min_on_time: int = duration_to_seconds(
            self._get_setting(CONF_MIN_ON_TIME, DEFAULT_MIN_ON_TIME),
            DEFAULT_MIN_ON_TIME,
        )
        self._min_off_time: int = duration_to_seconds(
            self._get_setting(CONF_MIN_OFF_TIME, DEFAULT_MIN_OFF_TIME),
            DEFAULT_MIN_OFF_TIME,
        )
        self._keep_alive: int = duration_to_seconds(
            self._get_setting(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE),
            DEFAULT_KEEP_ALIVE,
        )
        self._output_safety: float = float(
            self._get_setting(CONF_OUTPUT_SAFETY, DEFAULT_OUTPUT_SAFETY)
        )
        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            self._pwm_duration: int = duration_to_seconds(
                self._get_setting(CONF_PWM_DURATION, DEFAULT_PWM_DURATION_SWITCH),
                DEFAULT_PWM_DURATION_SWITCH,
            )
        else:
            self._pwm_duration = 0
        self._sample_period: int = int(
            self._get_setting(CONF_SAMPLE_PERIOD, DEFAULT_SAMPLE_PERIOD)
        )

        # Available modes
        self._available_hvac_modes: list[str] = list(
            self._get_setting(
                CONF_AVAILABLE_HVAC_MODES, def_modes
            )
        )
        self._available_fan_modes: list[str] = list(
            self._get_setting(
                CONF_AVAILABLE_FAN_MODES, DEFAULT_AVAILABLE_FAN_MODES
            )
        )
        if self._control_mode == CONTROL_MODE_CLIMATE_DELTA and self._target_device_id:
            slave_state = self.hass.states.get(self._target_device_id)
            if slave_state:
                if "hvac_modes" in slave_state.attributes:
                    h_modes = slave_state.attributes.get("hvac_modes")
                    if isinstance(h_modes, list) and h_modes:
                        self._available_hvac_modes = list(dict.fromkeys(str(m) for m in h_modes))
                if "fan_modes" in slave_state.attributes:
                    f_modes = slave_state.attributes.get("fan_modes")
                    if isinstance(f_modes, list) and f_modes:
                        self._available_fan_modes = list(dict.fromkeys(str(m) for m in f_modes))

        # Sections
        self._sec1_max: float = float(
            self._get_setting(CONF_SEC1_MAX, DEFAULT_SEC1_MAX)
        )
        self._sec1_apply_temp: bool = bool(
            self._get_setting(CONF_SEC1_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP)
        )
        self._sec1_apply_delta: bool = self._sec1_apply_temp
        self._sec1_delta: float = float(
            self._get_setting(CONF_SEC1_DELTA, DEFAULT_SEC1_DELTA)
        )
        self._sec1_apply_fan: bool = bool(
            self._get_setting(CONF_SEC1_APPLY_FAN, DEFAULT_SEC_APPLY_FAN)
        )
        self._sec1_fan: str = str(self._get_setting(CONF_SEC1_FAN, DEFAULT_SEC1_FAN))
        self._sec1_apply_hvac_mode: bool = bool(
            self._get_setting(
                CONF_SEC1_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_HVAC_MODE
            )
        )
        self._sec1_hvac_mode: str = str(
            self._get_setting(CONF_SEC1_HVAC_MODE, DEFAULT_SEC_HVAC_MODE)
        )

        self._sec2_max: float = float(
            self._get_setting(CONF_SEC2_MAX, DEFAULT_SEC2_MAX)
        )
        self._sec2_apply_temp: bool = bool(
            self._get_setting(CONF_SEC2_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP)
        )
        self._sec2_apply_delta: bool = self._sec2_apply_temp
        self._sec2_delta: float = float(
            self._get_setting(CONF_SEC2_DELTA, DEFAULT_SEC2_DELTA)
        )
        self._sec2_apply_fan: bool = bool(
            self._get_setting(CONF_SEC2_APPLY_FAN, DEFAULT_SEC_APPLY_FAN)
        )
        self._sec2_fan: str = str(self._get_setting(CONF_SEC2_FAN, DEFAULT_SEC2_FAN))
        self._sec2_apply_hvac_mode: bool = bool(
            self._get_setting(
                CONF_SEC2_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_HVAC_MODE
            )
        )
        self._sec2_hvac_mode: str = str(
            self._get_setting(CONF_SEC2_HVAC_MODE, DEFAULT_SEC_HVAC_MODE)
        )

        self._sec3_max: float = float(
            self._get_setting(CONF_SEC3_MAX, DEFAULT_SEC3_MAX)
        )
        self._sec3_apply_temp: bool = bool(
            self._get_setting(CONF_SEC3_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP)
        )
        self._sec3_apply_delta: bool = self._sec3_apply_temp
        self._sec3_delta: float = float(
            self._get_setting(CONF_SEC3_DELTA, DEFAULT_SEC3_DELTA)
        )
        self._sec3_apply_fan: bool = bool(
            self._get_setting(CONF_SEC3_APPLY_FAN, DEFAULT_SEC_APPLY_FAN)
        )
        self._sec3_fan: str = str(self._get_setting(CONF_SEC3_FAN, DEFAULT_SEC3_FAN))
        self._sec3_apply_hvac_mode: bool = bool(
            self._get_setting(
                CONF_SEC3_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_HVAC_MODE
            )
        )
        self._sec3_hvac_mode: str = str(
            self._get_setting(CONF_SEC3_HVAC_MODE, DEFAULT_SEC_HVAC_MODE)
        )

        self._sec4_apply_temp: bool = bool(
            self._get_setting(CONF_SEC4_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP)
        )
        self._sec4_apply_delta: bool = self._sec4_apply_temp
        self._sec4_delta: float = float(
            self._get_setting(CONF_SEC4_DELTA, DEFAULT_SEC4_DELTA)
        )
        self._sec4_apply_fan: bool = bool(
            self._get_setting(CONF_SEC4_APPLY_FAN, DEFAULT_SEC_APPLY_FAN)
        )
        self._sec4_fan: str = str(self._get_setting(CONF_SEC4_FAN, DEFAULT_SEC4_FAN))
        self._sec4_apply_hvac_mode: bool = bool(
            self._get_setting(
                CONF_SEC4_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_HVAC_MODE
            )
        )
        self._sec4_hvac_mode: str = str(
            self._get_setting(CONF_SEC4_HVAC_MODE, DEFAULT_SEC_HVAC_MODE)
        )

    @property
    def _is_active_hvac_mode(self) -> bool:
        """Return True if thermostat is in an active controlling mode."""
        mode_str = (
            self._attr_hvac_mode.value
            if hasattr(self._attr_hvac_mode, "value")
            else str(self._attr_hvac_mode)
        ).lower().strip()
        return mode_str in ("cool", "heat", "auto", "humidity", "dehumidity", "silent")

    @property
    def _active_target_device_id(self) -> str:
        """Return the active target device entity ID."""
        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            if self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                if getattr(self, "_is_heating", False):
                    return self._target_device_heat_id or self._target_device_id
                return self._target_device_cool_id or self._target_device_id
            if self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                if getattr(self, "_is_heating", False):
                    return self._target_device_humidity_id or self._target_device_id
                return self._target_device_dehumidity_id or self._target_device_id
        return self._target_device_id

    @property
    def min_temp(self) -> float:
        """Return the minimum target temperature."""
        return self._min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum target temperature."""
        return self._max_temp

    @property
    def min_humidity(self) -> float | None:
        """Return the minimum target humidity."""
        if is_humidity_type(self._thermostat_type):
            return self._min_temp
        return None

    @property
    def max_humidity(self) -> float | None:
        """Return the maximum target humidity."""
        if is_humidity_type(self._thermostat_type):
            return self._max_temp
        return None

    @property
    def target_humidity(self) -> float | None:
        """Return the target humidity."""
        if is_humidity_type(self._thermostat_type):
            return self._target_temp
        return None

    @property
    def current_humidity(self) -> float | None:
        """Return the current indoor humidity."""
        return self._indoor_humidity

    @property
    def target_temperature_step(self) -> float:
        """Return the target temperature step."""
        return self._temp_step

    @property
    def current_temperature(self) -> float | None:
        """Return the current indoor temperature/humidity."""
        if is_humidity_type(self._thermostat_type):
            return self._indoor_humidity
        return self._indoor_temp

    @property
    def target_temperature(self) -> float:
        """Return the target temperature."""
        return self._target_temp

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features."""
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if is_humidity_type(self._thermostat_type):
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        if self._control_mode == CONTROL_MODE_CLIMATE_DELTA:
            features |= ClimateEntityFeature.FAN_MODE
        return features

    @property
    def hvac_modes(self) -> list[HVACMode | str]:
        """Return the list of available HVAC modes configured for this thermostat."""
        if is_humidity_type(self._thermostat_type):
            if self._control_mode == CONTROL_MODE_SWITCH_PWM:
                if self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                    return [HVACMode.OFF, MODE_HUMIDITY]
                if self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                    return [HVACMode.OFF, MODE_DEHUMIDITY]
                return [HVACMode.OFF, MODE_DEHUMIDITY, MODE_HUMIDITY, HVACMode.AUTO]

            modes: list[HVACMode | str] = []
            for m in self._available_hvac_modes:
                if m not in modes:
                    modes.append(m)
            if HVACMode.OFF not in modes and "off" not in modes:
                modes.append(HVACMode.OFF)
            if self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER and MODE_HUMIDITY not in modes:
                modes.append(MODE_HUMIDITY)
            elif self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER and MODE_DEHUMIDITY not in modes:
                modes.append(MODE_DEHUMIDITY)
            elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY and HVACMode.AUTO not in modes and "auto" not in modes:
                modes.append(HVACMode.AUTO)
            return modes

        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            if self._thermostat_type == THERMOSTAT_TYPE_HEATER:
                return [HVACMode.OFF, HVACMode.HEAT]
            if self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                return [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.AUTO]
            return [HVACMode.OFF, HVACMode.COOL]

        modes: list[HVACMode | str] = []
        for m in self._available_hvac_modes:
            try:
                modes.append(HVACMode(m))
            except ValueError:
                if m not in modes:
                    modes.append(m)
        if HVACMode.OFF not in modes:
            modes.append(HVACMode.OFF)
        if self._thermostat_type == THERMOSTAT_TYPE_COOLER and HVACMode.COOL not in modes:
            modes.append(HVACMode.COOL)
        elif self._thermostat_type == THERMOSTAT_TYPE_HEATER and HVACMode.HEAT not in modes:
            modes.append(HVACMode.HEAT)
        elif self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL and HVACMode.AUTO not in modes:
            modes.append(HVACMode.AUTO)
        return modes

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode (None in switch PWM mode)."""
        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            return None
        if self._dispatched_fan_mode in ("mid", "middle", "med"):
            return "medium"
        return self._dispatched_fan_mode

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the list of available fan modes configured for this thermostat."""
        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            return None
        modes: list[str] = []
        for m in self._available_fan_modes:
            norm = "medium" if m in ("mid", "middle", "med") else m
            if norm not in modes:
                modes.append(norm)
        for sec_fan in (
            getattr(self, "_sec1_fan", None),
            getattr(self, "_sec2_fan", None),
            getattr(self, "_sec3_fan", None),
            getattr(self, "_sec4_fan", None),
        ):
            if sec_fan:
                norm = "medium" if sec_fan in ("mid", "middle", "med") else sec_fan
                if norm not in modes:
                    modes.append(norm)
        if self._dispatched_fan_mode:
            disp_norm = (
                "medium"
                if self._dispatched_fan_mode in ("mid", "middle", "med")
                else self._dispatched_fan_mode
            )
            if disp_norm not in modes:
                modes.append(disp_norm)
        return modes

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running action."""
        mode_str = (
            self._attr_hvac_mode.value
            if hasattr(self._attr_hvac_mode, "value")
            else str(self._attr_hvac_mode)
        ).lower().strip()
        if mode_str in ("off", ""):
            return HVACAction.OFF
        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            if not self._pwm_state:
                return HVACAction.IDLE
            if is_humidity_type(self._thermostat_type):
                return HVACAction.HEATING if self._is_heating else HVACAction.DRYING
            if self._thermostat_type == THERMOSTAT_TYPE_HEATER:
                return HVACAction.HEATING
            if self._thermostat_type == THERMOSTAT_TYPE_COOLER:
                return HVACAction.COOLING
            if self._is_heating:
                return HVACAction.HEATING
            return HVACAction.COOLING

        if is_humidity_type(self._thermostat_type):
            if self._pwm_duration > 0 and not self._pwm_state:
                return HVACAction.IDLE
            if self._pid_output <= 0.0:
                return HVACAction.IDLE
            return HVACAction.HEATING if self._is_heating else HVACAction.DRYING

        if self._attr_hvac_mode == HVACMode.DRY:
            return HVACAction.DRYING
        if self._attr_hvac_mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        if self._pwm_duration > 0 and not self._pwm_state:
            return HVACAction.IDLE
        if self._pid_output <= 0.0:
            return HVACAction.IDLE
        if self._thermostat_type == THERMOSTAT_TYPE_HEATER:
            return HVACAction.HEATING
        if self._thermostat_type == THERMOSTAT_TYPE_COOLER:
            return HVACAction.COOLING
        # THERMOSTAT_TYPE_HEAT_COOL:
        if self._is_heating:
            return HVACAction.HEATING
        return HVACAction.COOLING

    @property
    def _time_to_next_switch(self) -> int:
        """Return remaining seconds until next ON/OFF switch transition."""
        if not self._is_active_hvac_mode or self._control_mode != CONTROL_MODE_SWITCH_PWM:
            return 0
        now = dt_util.utcnow()
        if self._pwm_state:
            if self._cycle_turn_off_time is not None:
                return max(0, int((self._cycle_turn_off_time - now).total_seconds()))
            if self._cycle_end_time is not None:
                return max(0, int((self._cycle_end_time - now).total_seconds()))
        else:
            if self._cycle_end_time is not None:
                return max(0, int((self._cycle_end_time - now).total_seconds()))
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device specific state attributes."""
        attrs: dict[str, Any] = {
            "control_mode": self._control_mode,
            "thermostat_type": self._thermostat_type,
            "is_heating": self._is_heating,
            "pid_error": self._pid_error,
            "pid_p": self._pid_p,
            "pid_i": self._pid_i,
            "max_i": self._max_i,
            "pid_d": self._pid_d,
            "pid_e": self._pid_e,
            "pid_output": self._pid_output,
            "indoor_temperature": self._indoor_temp,
            "outdoor_temperature": self._outdoor_temp,
            "indoor_humidity": self._indoor_humidity,
            "outdoor_humidity": self._outdoor_humidity,
            "fail_safe_active": self._fail_safe_active,
        }

        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            attrs.update(
                {
                    "pwm_duty_cycle": self._pwm_duty_cycle,
                    "pwm_on_time": self._pwm_on_time,
                    "pwm_duration": self._pwm_duration,
                    "min_on_time": self._min_on_time,
                    "min_off_time": self._min_off_time,
                    "switch_state": "on" if self._pwm_state else "off",
                    "time_to_next_switch": self._time_to_next_switch,
                    "active_target_device": self._active_target_device_id,
                }
            )
            if self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                attrs.update(
                    {
                        "target_device_cool": self._target_device_cool_id,
                        "target_device_heat": self._target_device_heat_id,
                    }
                )
            elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                attrs.update(
                    {
                        "target_device_dehumidity": self._target_device_dehumidity_id,
                        "target_device_humidity": self._target_device_humidity_id,
                    }
                )
        else:
            attrs.update(
                {
                    "active_section": self._active_section,
                    "raw_target_temperature": self._raw_target_temperature,
                    "dispatched_temperature": self._dispatched_temperature,
                    "dispatched_fan_mode": self._dispatched_fan_mode,
                    "off_temp_action": self._off_temp_action,
                    "sync_slave_state": self._sync_slave_state,
                    "sec1_apply_temp": self._sec1_apply_temp,
                    "sec1_apply_delta": self._sec1_apply_delta,
                    "sec1_delta": self._sec1_delta,
                    "sec1_apply_fan": self._sec1_apply_fan,
                    "sec1_fan": self._sec1_fan,
                    "sec1_apply_hvac_mode": self._sec1_apply_hvac_mode,
                    "sec1_hvac_mode": self._sec1_hvac_mode,
                    "sec2_apply_temp": self._sec2_apply_temp,
                    "sec2_apply_delta": self._sec2_apply_delta,
                    "sec2_delta": self._sec2_delta,
                    "sec2_apply_fan": self._sec2_apply_fan,
                    "sec2_fan": self._sec2_fan,
                    "sec2_apply_hvac_mode": self._sec2_apply_hvac_mode,
                    "sec2_hvac_mode": self._sec2_hvac_mode,
                    "sec3_apply_temp": self._sec3_apply_temp,
                    "sec3_apply_delta": self._sec3_apply_delta,
                    "sec3_delta": self._sec3_delta,
                    "sec3_apply_fan": self._sec3_apply_fan,
                    "sec3_fan": self._sec3_fan,
                    "sec3_apply_hvac_mode": self._sec3_apply_hvac_mode,
                    "sec3_hvac_mode": self._sec3_hvac_mode,
                    "sec4_apply_temp": self._sec4_apply_temp,
                    "sec4_apply_delta": self._sec4_apply_delta,
                    "sec4_delta": self._sec4_delta,
                    "sec4_apply_fan": self._sec4_apply_fan,
                    "sec4_fan": self._sec4_fan,
                    "sec4_apply_hvac_mode": self._sec4_apply_hvac_mode,
                    "sec4_hvac_mode": self._sec4_hvac_mode,
                }
            )
        return attrs

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to Home Assistant."""
        await super().async_added_to_hass()

        # Restore previous state if available
        old_state = await self.async_get_last_state()
        if old_state is not None:
            valid_modes = [
                (m.value if hasattr(m, "value") else str(m)) for m in self.hvac_modes
            ]
            if old_state.state in valid_modes:
                try:
                    self._attr_hvac_mode = HVACMode(old_state.state)
                except ValueError:
                    self._attr_hvac_mode = old_state.state
            old_target = old_state.attributes.get(ATTR_TEMPERATURE)
            if old_target is not None:
                try:
                    self._target_temp = float(old_target)
                except (ValueError, TypeError):
                    self._target_temp = self._initial_target_temp
            old_fan = old_state.attributes.get(ATTR_FAN_MODE)
            if old_fan is not None:
                self._dispatched_fan_mode = str(old_fan)
            old_i = old_state.attributes.get("pid_i")
            if old_i is not None and self._is_active_hvac_mode:
                try:
                    self._integral = float(old_i)
                except (ValueError, TypeError):
                    self._integral = 0.0

        # Read initial sensor values
        self._update_sensor_values()

        # Listen for sensor and slave climate state changes
        monitored_entities = [
            s
            for s in [
                self._indoor_sensor_id,
                self._outdoor_sensor_id,
                self._target_climate_id,
                self._target_device_id,
                self._target_device_cool_id,
                self._target_device_heat_id,
                self._target_device_dehumidity_id,
                self._target_device_humidity_id,
            ]
            if s
        ]
        monitored_entities = list(dict.fromkeys(monitored_entities))
        if monitored_entities:
            self._unsub_sensor_track = async_track_state_change_event(
                self.hass, monitored_entities, self._async_entity_changed
            )

        # Periodic evaluation loop
        self._unsub_interval_tick = async_track_time_interval(
            self.hass,
            self._async_interval_tick,
            timedelta(seconds=self._sample_period),
        )

        if self._control_mode == CONTROL_MODE_SWITCH_PWM and self._keep_alive > 0:
            self._keep_alive_cancel = async_track_time_interval(
                self.hass,
                self._async_keep_alive_handler,
                timedelta(seconds=self._keep_alive),
            )

        # Initial calculation to populate P, I, D and output diagnostics immediately
        await self._async_calculate_and_control(force=True)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from Home Assistant."""
        if self._unsub_sensor_track:
            self._unsub_sensor_track()
            self._unsub_sensor_track = None
        if self._unsub_interval_tick:
            self._unsub_interval_tick()
            self._unsub_interval_tick = None
        if self._keep_alive_cancel:
            self._keep_alive_cancel()
            self._keep_alive_cancel = None
        self._cancel_pwm_timers()
        await super().async_will_remove_from_hass()

    def _cancel_pwm_timers(self) -> None:
        """Cancel PWM running timers."""
        if hasattr(self, "_pwm_timer_cancel") and self._pwm_timer_cancel:
            self._pwm_timer_cancel()
            self._pwm_timer_cancel = None
        if hasattr(self, "_pwm_turn_off_cancel") and self._pwm_turn_off_cancel:
            self._pwm_turn_off_cancel()
            self._pwm_turn_off_cancel = None
        if hasattr(self, "_pwm_turn_on_cancel") and self._pwm_turn_on_cancel:
            self._pwm_turn_on_cancel()
            self._pwm_turn_on_cancel = None
        if hasattr(self, "_pwm_cycle_cancel") and self._pwm_cycle_cancel:
            self._pwm_cycle_cancel()
            self._pwm_cycle_cancel = None

    @staticmethod
    def _parse_temperature(state_obj: Any) -> float | None:
        """Safely extract float temperature from entity state or attributes."""
        if not state_obj:
            return None

        state_val = state_obj.state
        if state_val in (STATE_UNAVAILABLE, STATE_UNKNOWN, None):
            return None

        if isinstance(state_val, str):
            cleaned_state = state_val.strip().lower()
            if cleaned_state in ("unknown", "unavailable", "none", "null", "restored", ""):
                return None

            # Clean common unit suffixes (e.g., '21.5 °C', '21.5°C', '21.5 C') and handle comma decimals
            cleaned = (
                state_val.replace("°C", "")
                .replace("°", "")
                .replace("C", "")
                .replace("F", "")
                .replace(",", ".")
                .strip()
            )
            try:
                return float(cleaned)
            except (ValueError, TypeError):
                pass

        # Check attributes (especially for weather entities where state is 'sunny', 'cloudy', etc.)
        attrs = getattr(state_obj, "attributes", {}) or {}
        for attr in (
            "temperature",
            "native_temperature",
            "current_temperature",
            "temp",
            "target_temperature",
            "apparent_temperature",
        ):
            if attr in attrs and attrs[attr] is not None:
                val = attrs[attr]
                if isinstance(val, (int, float)):
                    return float(val)
                if isinstance(val, str):
                    cleaned = (
                        val.replace("°C", "")
                        .replace("°", "")
                        .replace("C", "")
                        .replace("F", "")
                        .replace(",", ".")
                        .strip()
                    )
                    try:
                        return float(cleaned)
                    except (ValueError, TypeError):
                        pass

        return None

    @staticmethod
    def _parse_humidity(state_obj: Any) -> float | None:
        """Safely extract float humidity from entity state or attributes."""
        if not state_obj:
            return None

        state_val = state_obj.state
        if state_val in (STATE_UNAVAILABLE, STATE_UNKNOWN, None):
            return None

        if isinstance(state_val, str):
            cleaned_state = state_val.strip().lower()
            if cleaned_state in ("unknown", "unavailable", "none", "null", "restored", ""):
                return None

            cleaned = (
                state_val.replace("%", "")
                .replace(",", ".")
                .strip()
            )
            try:
                return float(cleaned)
            except (ValueError, TypeError):
                pass

        # Check attributes (e.g. for weather entities or climate/humidifier entities)
        attrs = getattr(state_obj, "attributes", {}) or {}
        for attr in (
            "humidity",
            "relative_humidity",
            "current_humidity",
            "target_humidity",
        ):
            if attr in attrs and attrs[attr] is not None:
                val = attrs[attr]
                if isinstance(val, (int, float)):
                    return float(val)
                if isinstance(val, str):
                    cleaned = val.replace("%", "").replace(",", ".").strip()
                    try:
                        return float(cleaned)
                    except (ValueError, TypeError):
                        pass

        return None

    def _update_sensor_values(self) -> None:
        """Read and parse sensor values from Home Assistant state machine."""
        now = dt_util.utcnow()
        is_hum = is_humidity_type(self._thermostat_type)

        # Indoor sensor
        indoor_state = self.hass.states.get(self._indoor_sensor_id)
        if (
            indoor_state
            and indoor_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            parsed = self._parse_humidity(indoor_state) if is_hum else self._parse_temperature(indoor_state)
            if parsed is not None:
                if is_hum:
                    self._indoor_humidity = parsed
                    self._indoor_temp = parsed
                else:
                    self._indoor_temp = parsed
                self._last_indoor_update = now
                self._sensor_stale_count = 0
            else:
                self._sensor_stale_count += 1
                if is_hum:
                    self._indoor_humidity = None
                self._indoor_temp = None
                _LOGGER.warning(
                    "Invalid indoor sensor value from %s: %s",
                    self._indoor_sensor_id,
                    indoor_state.state,
                )
        else:
            self._sensor_stale_count += 1
            if is_hum:
                self._indoor_humidity = None
            self._indoor_temp = None

        # Outdoor sensor (optional feedforward)
        if self._outdoor_sensor_id:
            outdoor_state = self.hass.states.get(self._outdoor_sensor_id)
            if (
                outdoor_state
                and outdoor_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            ):
                parsed_out = self._parse_humidity(outdoor_state) if is_hum else self._parse_temperature(outdoor_state)
                if parsed_out is not None:
                    if is_hum:
                        self._outdoor_humidity = parsed_out
                        self._outdoor_temp = parsed_out
                    else:
                        self._outdoor_temp = parsed_out
                    self._last_outdoor_update = now
                else:
                    _LOGGER.debug(
                        "Unable to parse outdoor value from %s (state: %s, attributes: %s)",
                        self._outdoor_sensor_id,
                        outdoor_state.state,
                        outdoor_state.attributes,
                    )
            else:
                if is_hum:
                    self._outdoor_humidity = None
                self._outdoor_temp = None
        else:
            if is_hum:
                self._outdoor_humidity = None
            self._outdoor_temp = None

    @callback
    def _async_entity_changed(self, event: Event) -> None:
        """Handle state change event for sensors or slave climate/switch."""
        entity_id = event.data.get(ATTR_ENTITY_ID)

        # Handle slave switch / climate state changes
        if entity_id in (
            self._target_device_id,
            self._target_device_cool_id,
            self._target_device_heat_id,
            self._target_device_dehumidity_id,
            self._target_device_humidity_id,
        ):
            if self._control_mode == CONTROL_MODE_SWITCH_PWM:
                if entity_id == self._active_target_device_id:
                    new_state = event.data.get("new_state")
                    if new_state:
                        self._pwm_state = (new_state.state.lower() == "on")
                        self.async_write_ha_state()
                elif (
                    (self._thermostat_type in (THERMOSTAT_TYPE_HEAT_COOL, THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY))
                    and self._is_active_hvac_mode
                ):
                    new_state = event.data.get("new_state")
                    if new_state and new_state.state.lower() == "on":
                        _LOGGER.warning(
                            "Inactive PWM device %s turned ON unexpectedly during active mode! Forcing it OFF to maintain separation.",
                            entity_id,
                        )
                        self._run_async(self._async_turn_off_specific_device(entity_id))
                return

            if not self._sync_slave_state:
                return

            # Guard 1: Ignore if we are currently dispatching an HVAC mode command
            if self._is_dispatching_hvac_mode or self._is_dispatching:
                return

            new_state = event.data.get("new_state")
            if not new_state:
                return

            slave_mode = new_state.state
            now = dt_util.utcnow()

            # Guard 2: Ignore if slave mode matches what we commanded or expected
            expected_mode_val = (
                self._expected_slave_mode.value
                if hasattr(self._expected_slave_mode, "value")
                else str(self._expected_slave_mode)
            ) if self._expected_slave_mode is not None else None

            if expected_mode_val and slave_mode == expected_mode_val:
                self._expected_slave_mode = None
                return

            # Guard 3: Avoid loop if mode is already identical to virtual thermostat
            curr_mode_val = (
                self._attr_hvac_mode.value
                if hasattr(self._attr_hvac_mode, "value")
                else str(self._attr_hvac_mode)
            )
            if slave_mode == curr_mode_val:
                return

            # Guard 4: Grace period check (10 seconds)
            # When the thermostat was recently changed or a mode command dispatched,
            # ignore mismatching slave reports (e.g., slave still reporting OFF while turning ON)
            if self._last_mode_dispatch_time is not None:
                elapsed_dispatch = (now - self._last_mode_dispatch_time).total_seconds()
                if elapsed_dispatch < 10.0:
                    _LOGGER.debug(
                        "Ignoring slave mode '%s' during dispatch grace period (%.1fs < 10.0s)",
                        slave_mode,
                        elapsed_dispatch,
                    )
                    return
            if self._last_mode_change_time is not None:
                elapsed_change = (now - self._last_mode_change_time).total_seconds()
                if elapsed_change < 10.0:
                    _LOGGER.debug(
                        "Ignoring slave mode '%s' during mode change grace period (%.1fs < 10.0s)",
                        slave_mode,
                        elapsed_change,
                    )
                    return

            # Guard 5: Internal PWM off in climate_delta mode
            if getattr(self, "_internal_pwm_slave_off", False) and slave_mode == "off":
                _LOGGER.debug("Ignoring slave 'off' caused by internal PWM off cycle.")
                return

            # Handle external mode transition
            valid_modes = [
                (m.value if hasattr(m, "value") else str(m)) for m in self.hvac_modes
            ]
            if slave_mode in valid_modes:
                _LOGGER.info(
                    "Slave climate mode changed externally from %s to %s. Syncing virtual thermostat.",
                    self._attr_hvac_mode,
                    slave_mode,
                )
                self._run_async(
                    self._async_sync_mode_from_slave(slave_mode)
                )
            return

        # Otherwise indoor or outdoor sensor state changed
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not new_state:
            return

        # Performance optimization: Ignore attribute-only changes (e.g. battery, rssi, linkquality)
        # if neither the state nor the temperature attribute has changed.
        if old_state and old_state.state == new_state.state:
            old_attrs = old_state.attributes
            new_attrs = new_state.attributes
            temp_attr_changed = False
            for attr in (
                "temperature",
                "native_temperature",
                "current_temperature",
                "temp",
                "target_temperature",
                "apparent_temperature",
            ):
                if attr in new_attrs and new_attrs.get(attr) != old_attrs.get(attr):
                    temp_attr_changed = True
                    break
            if not temp_attr_changed:
                return

        self._update_sensor_values()
        # Trigger calculation safely
        self._run_async(self._async_calculate_and_control())

    async def _async_sync_mode_from_slave(self, new_mode: HVACMode | str) -> None:
        """Synchronize HVAC mode from slave to virtual thermostat safely without loopback."""
        str_new_mode = (
            new_mode.value if hasattr(new_mode, "value") else str(new_mode)
        ).lower().strip()
        str_cur_mode = (
            self._attr_hvac_mode.value
            if hasattr(self._attr_hvac_mode, "value")
            else str(self._attr_hvac_mode)
        ).lower().strip() if self._attr_hvac_mode else ""

        if str_cur_mode == str_new_mode:
            return

        _LOGGER.info(
            "Syncing HVAC mode from slave (%s -> %s). Resetting PID state to 0.",
            self._attr_hvac_mode,
            new_mode,
        )
        try:
            self._attr_hvac_mode = HVACMode(str_new_mode)
        except ValueError:
            self._attr_hvac_mode = str_new_mode
        self._expected_slave_mode = self._attr_hvac_mode
        self._reset_pid_state()

        if str_new_mode == "off":
            # Calculate diagnostics without dispatching turn-off to slave again
            await self._async_calculate_and_control(force=True)
            return

        if str_new_mode in ("cool", "heat", "auto", MODE_HUMIDITY, MODE_DEHUMIDITY, MODE_SILENT):
            # Trigger calculation & temperature/fan dispatch WITHOUT calling set_hvac_mode on slave
            await self._async_calculate_and_control(force=True)
            return

        if str_new_mode in ("dry", "fan_only"):
            self.async_write_ha_state()
            return

    @callback
    def _async_interval_tick(self, now: datetime) -> None:
        """Handle periodic evaluation loop."""
        # Always update and calculate to keep diagnostics fresh
        self._run_async(self._async_calculate_and_control(now))

    def _reset_pid_state(self) -> None:
        """Reset PID integral, error history, and PWM state on mode transition."""
        self._integral = 0.0
        self._pid_i = 0.0
        self._prev_error = None
        self._prev_room_temp = None
        self._cancel_pwm_timers()
        self._pwm_state = False
        self._last_dispatched_temp = None
        self._last_dispatched_fan_mode = None
        self._last_dispatched_section = None

    async def async_turn_on(self) -> None:
        """Turn the entity on (defaulting to appropriate operating mode)."""
        if self._thermostat_type == THERMOSTAT_TYPE_HEATER:
            default_mode = (
                HVACMode.HEAT
                if HVACMode.HEAT in self.hvac_modes
                else self.hvac_modes[0]
            )
        elif self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
            default_mode = (
                HVACMode.AUTO
                if HVACMode.AUTO in self.hvac_modes
                else (
                    HVACMode.COOL
                    if HVACMode.COOL in self.hvac_modes
                    else self.hvac_modes[0]
                )
            )
        elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
            default_mode = (
                MODE_HUMIDITY
                if MODE_HUMIDITY in self.hvac_modes
                else self.hvac_modes[0]
            )
        elif self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
            default_mode = (
                MODE_DEHUMIDITY
                if MODE_DEHUMIDITY in self.hvac_modes
                else self.hvac_modes[0]
            )
        elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
            default_mode = (
                HVACMode.AUTO
                if HVACMode.AUTO in self.hvac_modes
                else (
                    MODE_DEHUMIDITY
                    if MODE_DEHUMIDITY in self.hvac_modes
                    else self.hvac_modes[0]
                )
            )
        else:
            default_mode = (
                HVACMode.COOL
                if HVACMode.COOL in self.hvac_modes
                else self.hvac_modes[0]
            )
        await self.async_set_hvac_mode(default_mode)

    async def async_turn_off(self) -> None:
        """Turn the entity off and immediately power off the physical device."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode | str) -> None:
        """Set new HVAC mode."""
        str_mode = (
            hvac_mode.value if hasattr(hvac_mode, "value") else str(hvac_mode)
        ).lower().strip()
        supported_str_modes = [
            (m.value if hasattr(m, "value") else str(m)).lower().strip()
            for m in self.hvac_modes
        ]
        if str_mode not in supported_str_modes:
            _LOGGER.warning("Unsupported HVAC mode: %s", hvac_mode)
            return

        old_mode = self._attr_hvac_mode
        self._last_mode_change_time = dt_util.utcnow()

        old_mode_str = (
            old_mode.value if hasattr(old_mode, "value") else str(old_mode)
        ).lower().strip() if old_mode else ""

        # Reset PID integral and error history whenever mode changes
        if old_mode_str != str_mode:
            _LOGGER.info(
                "HVAC mode changed from %s to %s. Resetting PID state to 0.",
                old_mode,
                hvac_mode,
            )
            self._reset_pid_state()

        if str_mode == "off":
            # 1. Immediate physical device shutdown & state reset
            self._attr_hvac_mode = HVACMode.OFF
            if self._control_mode == CONTROL_MODE_SWITCH_PWM:
                await self._async_stop_pwm()
            else:
                await self._async_set_slave_hvac_mode(HVACMode.OFF)
                await self._async_calculate_and_control(force=True)
            self.async_write_ha_state()
            return

        try:
            self._attr_hvac_mode = HVACMode(str_mode)
        except ValueError:
            self._attr_hvac_mode = str_mode

        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            await self._async_calculate_and_control(force=True)
            self.async_write_ha_state()
            return

        # Air conditioner / humidifier / dehumidity control mode
        target_domain = (
            self._target_climate_id.split(".")[0]
            if "." in self._target_climate_id
            else "climate"
        )
        if target_domain == "humidifier":
            await self._async_set_slave_hvac_mode(str_mode)
            await self._async_calculate_and_control(force=True)
            self.async_write_ha_state()
            return

        if str_mode in (MODE_HUMIDITY, MODE_DEHUMIDITY, MODE_SILENT):
            await self._async_set_slave_hvac_mode(str_mode)
            await self._async_calculate_and_control(force=True)
            return

        # Check slave supported HVAC modes
        slave_state = self.hass.states.get(self._target_climate_id)
        slave_modes = (
            slave_state.attributes.get("hvac_modes", []) if slave_state else []
        )
        slave_modes_str = [
            (m.value if hasattr(m, "value") else str(m)).lower().strip()
            for m in slave_modes
        ]

        if str_mode == "cool":
            await self._async_set_slave_hvac_mode(HVACMode.COOL)
            await self._async_calculate_and_control(force=True)
            return

        if str_mode == "heat":
            if slave_modes_str and "heat" not in slave_modes_str:
                _LOGGER.warning(
                    "Slave climate %s does not support HEAT mode",
                    self._target_climate_id,
                )
                return
            await self._async_set_slave_hvac_mode(HVACMode.HEAT)
            await self._async_calculate_and_control(force=True)
            return

        if str_mode == "auto":
            if self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                if slave_modes_str and "auto" not in slave_modes_str:
                    target_slave = HVACMode.HEAT if getattr(self, "_is_heating", False) else HVACMode.COOL
                    await self._async_set_slave_hvac_mode(target_slave)
                else:
                    await self._async_set_slave_hvac_mode(HVACMode.AUTO)
            elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                target_slave = MODE_HUMIDITY if getattr(self, "_is_heating", False) else MODE_DEHUMIDITY
                await self._async_set_slave_hvac_mode(target_slave)
            else:
                if slave_modes_str and "auto" not in slave_modes_str:
                    _LOGGER.warning(
                        "Slave climate %s does not support AUTO mode",
                        self._target_climate_id,
                    )
                    return
                await self._async_set_slave_hvac_mode(HVACMode.AUTO)
            await self._async_calculate_and_control(force=True)
            return

        if str_mode == "dry":
            if slave_modes_str and "dry" not in slave_modes_str:
                _LOGGER.warning(
                    "Slave climate %s does not support DRY mode",
                    self._target_climate_id,
                )
                return
            await self._async_set_slave_hvac_mode(HVACMode.DRY)
            int_target = int(math.floor(self._target_temp + 0.5))
            slave_min, slave_max = self._get_slave_temp_limits()
            safe_temp = max(int(slave_min), min(int(slave_max), int_target))
            await self._async_send_temperature(safe_temp)
            self.async_write_ha_state()
            return

        if str_mode == "fan_only":
            if slave_modes_str and "fan_only" not in slave_modes_str:
                _LOGGER.warning(
                    "Slave climate %s does not support FAN_ONLY mode",
                    self._target_climate_id,
                )
                return
            await self._async_set_slave_hvac_mode(HVACMode.FAN_ONLY)
            self.async_write_ha_state()
            return

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new fan mode."""
        if self.fan_modes and fan_mode not in self.fan_modes:
            _LOGGER.warning("Unsupported fan mode: %s", fan_mode)
            return

        self._dispatched_fan_mode = fan_mode
        # Send ONLY fan mode command to slave device (without altering temperature or HVAC mode)
        await self._async_send_fan_mode(fan_mode, force=True)
        self.async_write_ha_state()

    async def async_set_humidity(self, humidity: float) -> None:
        """Set new target humidity."""
        await self.async_set_temperature(humidity=humidity)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature/humidity with immediate PID recalculation and dispatch."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            temp = kwargs.get(ATTR_HUMIDITY)
        if temp is None:
            temp = kwargs.get("humidity")
        if temp is None:
            return

        temp = max(self._min_temp, min(self._max_temp, float(temp)))
        self._target_temp = temp
        self._update_sensor_values()

        # Policy handling when currently OFF
        mode_str = (
            self._attr_hvac_mode.value
            if hasattr(self._attr_hvac_mode, "value")
            else str(self._attr_hvac_mode)
        ).lower().strip()

        if mode_str == "off":
            if self._off_temp_action == OFF_ACTION_TURN_ON:
                _LOGGER.info(
                    "Target setpoint changed while OFF. Automatically turning on thermostat."
                )
                if self._thermostat_type == THERMOSTAT_TYPE_HEATER:
                    default_on_mode = (
                        HVACMode.HEAT
                        if HVACMode.HEAT in self.hvac_modes
                        else self.hvac_modes[0]
                    )
                elif self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                    default_on_mode = (
                        HVACMode.AUTO
                        if HVACMode.AUTO in self.hvac_modes
                        else (
                            HVACMode.HEAT
                            if self._indoor_temp is not None and self._target_temp > self._indoor_temp and HVACMode.HEAT in self.hvac_modes
                            else (
                                HVACMode.COOL
                                if HVACMode.COOL in self.hvac_modes
                                else self.hvac_modes[0]
                            )
                        )
                    )
                elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                    default_on_mode = (
                        MODE_HUMIDITY
                        if MODE_HUMIDITY in self.hvac_modes
                        else self.hvac_modes[0]
                    )
                elif self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                    default_on_mode = (
                        MODE_DEHUMIDITY
                        if MODE_DEHUMIDITY in self.hvac_modes
                        else self.hvac_modes[0]
                    )
                elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                    default_on_mode = (
                        HVACMode.AUTO
                        if HVACMode.AUTO in self.hvac_modes
                        else (
                            MODE_HUMIDITY
                            if self._indoor_temp is not None and self._target_temp > self._indoor_temp and MODE_HUMIDITY in self.hvac_modes
                            else (
                                MODE_DEHUMIDITY
                                if MODE_DEHUMIDITY in self.hvac_modes
                                else self.hvac_modes[0]
                            )
                        )
                    )
                else:
                    default_on_mode = (
                        HVACMode.COOL
                        if HVACMode.COOL in self.hvac_modes
                        else self.hvac_modes[0]
                    )
                await self.async_set_hvac_mode(default_on_mode)
                return

            # When OFF and sync_only:
            # 1. Recalculate PID and section mapping so diagnostics, active section,
            #    dispatched_temperature (with delta), and dispatched_fan_mode update immediately
            await self._async_calculate_and_control(force=True)

            if self._control_mode != CONTROL_MODE_SWITCH_PWM:
                # 2. Synchronize dispatched temperature with slave device only if section permits temperature control
                sec_apply_temp = self._get_section_parameters(self._active_section)[0]
                if sec_apply_temp and self._dispatched_temperature is not None:
                    await self._async_send_temperature(self._dispatched_temperature, force=True)
            self.async_write_ha_state()
            return

        # If in DRY mode, forward target temperature directly
        if self._attr_hvac_mode == HVACMode.DRY:
            int_target = int(math.floor(self._target_temp + 0.5))
            slave_min, slave_max = self._get_slave_temp_limits()
            safe_temp = max(int(slave_min), min(int(slave_max), int_target))
            self._dispatched_temperature = safe_temp
            await self._async_send_temperature(safe_temp, force=True)
            self.async_write_ha_state()
            return

        # Active operating modes (COOL, HEAT, AUTO):
        await self._async_calculate_and_control(force=True)

    def _determine_active_section(self, output: float, current_sec: int) -> int:
        """Determine active section using hysteresis state machine (±1.5% deadband)."""
        s1 = self._sec1_max
        s2 = self._sec2_max
        s3 = self._sec3_max
        band = HYSTERESIS_BAND

        # Initial fallback if section not initialized
        if current_sec not in (1, 2, 3, 4):
            if output <= s1:
                return 1
            if output <= s2:
                return 2
            if output <= s3:
                return 3
            return 4

        # Hysteresis transitions
        if current_sec == 1:
            if output > s3 + band:
                return 4
            if output > s2 + band:
                return 3
            if output > s1 + band:
                return 2
            return 1

        if current_sec == 2:
            if output > s3 + band:
                return 4
            if output > s2 + band:
                return 3
            if output < s1 - band:
                return 1
            return 2

        if current_sec == 3:
            if output > s3 + band:
                return 4
            if output < s1 - band:
                return 1
            if output < s2 - band:
                return 2
            return 3

        if current_sec == 4:
            if output < s1 - band:
                return 1
            if output < s2 - band:
                return 2
            if output < s3 - band:
                return 3
            return 4

        return current_sec

    def _get_section_parameters(
        self, section: int
    ) -> tuple[bool, float, str, str, bool, bool, bool]:
        """Return apply_temp, delta, fan mode, hvac mode, apply_delta, apply_fan, apply_hvac_mode."""
        if section == 1:
            return (
                self._sec1_apply_temp,
                self._sec1_delta,
                self._sec1_fan,
                self._sec1_hvac_mode,
                self._sec1_apply_delta,
                self._sec1_apply_fan,
                self._sec1_apply_hvac_mode,
            )
        if section == 2:
            return (
                self._sec2_apply_temp,
                self._sec2_delta,
                self._sec2_fan,
                self._sec2_hvac_mode,
                self._sec2_apply_delta,
                self._sec2_apply_fan,
                self._sec2_apply_hvac_mode,
            )
        if section == 3:
            return (
                self._sec3_apply_temp,
                self._sec3_delta,
                self._sec3_fan,
                self._sec3_hvac_mode,
                self._sec3_apply_delta,
                self._sec3_apply_fan,
                self._sec3_apply_hvac_mode,
            )
        return (
            self._sec4_apply_temp,
            self._sec4_delta,
            self._sec4_fan,
            self._sec4_hvac_mode,
            self._sec4_apply_delta,
            self._sec4_apply_fan,
            self._sec4_apply_hvac_mode,
        )

    async def _async_calculate_and_control(
        self, now: datetime | None = None, force: bool = False
    ) -> None:
        """Run PID and Ke calculation loop, hysteresis mapping, and slave dispatch."""
        async with self._calc_lock:
            try:
                if now is None:
                    now = dt_util.utcnow()

                # Always read fresh sensor values on every evaluation
                self._update_sensor_values()

                # Calculate dt (time delta in seconds) with bounds
                if self._last_calc_time is None or force:
                    dt = float(self._sample_period)
                else:
                    raw_dt = (now - self._last_calc_time).total_seconds()
                    # Bound dt between 1s and 2x sample_period to avoid integral runaway or near-zero division
                    dt = max(1.0, min(float(self._sample_period) * 2.0, raw_dt))
                self._last_calc_time = now

                # Fail-Safe check: Indoor sensor unavailable, invalid, or stale (3 consecutive times)
                if self._indoor_temp is None or self._sensor_stale_count >= 3:
                    self._fail_safe_active = True
                    _LOGGER.warning(
                        "Fail-safe triggered: Indoor sensor (%s) unavailable, invalid, or stale (stale_count=%d)",
                        self._indoor_sensor_id,
                        self._sensor_stale_count,
                    )
                    final_output = self._output_safety if self._control_mode == CONTROL_MODE_SWITCH_PWM else FAIL_SAFE_OUTPUT
                    self._pid_error = 0.0
                    self._pid_p = 0.0
                    self._pid_i = 0.0
                    self._pid_d = 0.0
                    self._pid_e = 0.0
                    self._pid_output = round(final_output, 2)
                    str_mode = (
                        self._attr_hvac_mode.value
                        if hasattr(self._attr_hvac_mode, "value")
                        else str(self._attr_hvac_mode)
                    ).lower().strip()
                    if self._thermostat_type == THERMOSTAT_TYPE_HEATER:
                        is_heating = True
                    elif self._thermostat_type == THERMOSTAT_TYPE_COOLER:
                        is_heating = False
                    elif self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                        is_heating = (str_mode == "heat")
                    elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                        is_heating = True
                    elif self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                        is_heating = False
                    elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                        is_heating = (str_mode == MODE_HUMIDITY)
                    else:
                        is_heating = False
                else:
                    self._fail_safe_active = False

                    # Determine whether we are heating/humidifying or cooling/dehumidifying
                    str_mode = (
                        self._attr_hvac_mode.value
                        if hasattr(self._attr_hvac_mode, "value")
                        else str(self._attr_hvac_mode)
                    ).lower().strip()

                    if self._thermostat_type == THERMOSTAT_TYPE_COOLER:
                        is_heating = False
                    elif self._thermostat_type == THERMOSTAT_TYPE_HEATER:
                        is_heating = True
                    elif self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                        if str_mode == "auto":
                            is_heating = (self._indoor_temp < self._target_temp)
                        elif str_mode == "heat":
                            is_heating = True
                        else:
                            is_heating = False
                    elif self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                        is_heating = False
                    elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                        is_heating = True
                    elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                        if str_mode == "auto":
                            is_heating = (self._indoor_temp < self._target_temp)
                        elif str_mode == MODE_HUMIDITY:
                            is_heating = True
                        else:
                            is_heating = False
                    else:
                        is_heating = False

                    # If heating/cooling direction changed in AUTO mode, reset PID integral
                    if self._is_heating != is_heating:
                        _LOGGER.info(
                            "Thermostat PID direction switched: %s -> %s. Resetting PID integral.",
                            "HEATING/HUMIDIFYING" if self._is_heating else "COOLING/DEHUMIDIFYING",
                            "HEATING/HUMIDIFYING" if is_heating else "COOLING/DEHUMIDIFYING",
                        )
                        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
                            if self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                                prev_dev = (
                                    self._target_device_heat_id
                                    if self._is_heating
                                    else self._target_device_cool_id
                                )
                                if prev_dev:
                                    await self._async_turn_off_specific_device(prev_dev)
                            elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                                prev_dev = (
                                    self._target_device_humidity_id
                                    if self._is_heating
                                    else self._target_device_dehumidity_id
                                )
                                if prev_dev:
                                    await self._async_turn_off_specific_device(prev_dev)

                            self._cancel_pwm_timers()
                            self._cycle_start_time = None
                            self._cycle_end_time = None
                            self._cycle_turn_off_time = None
                            self._pwm_state = False

                        self._integral = 0.0
                        self._prev_error = None
                        self._prev_room_temp = None
                    self._is_heating = is_heating

                    if is_heating:
                        # Heating error: e(t) = T_target(t) - T_room(t)
                        error = self._target_temp - self._indoor_temp
                        # Outdoor feedforward Ke term for heating:
                        # When outdoor is colder than target, heat loss is higher -> increase heating demand
                        if self._outdoor_sensor_id and self._outdoor_temp is not None:
                            e_term = self._ke * (self._target_temp - self._outdoor_temp)
                        else:
                            e_term = 0.0
                    else:
                        # Cooling error: e(t) = T_room(t) - T_target(t)
                        error = self._indoor_temp - self._target_temp
                        # Outdoor feedforward Ke term for cooling:
                        # When outdoor is hotter than target, heat gain is higher -> increase cooling demand
                        if self._outdoor_sensor_id and self._outdoor_temp is not None:
                            e_term = self._ke * (self._outdoor_temp - self._target_temp)
                        else:
                            e_term = 0.0

                    # (2) P term: P(t) = Kp * e(t)
                    p_term = self._kp * error

                    # (3) D term using derivative on measurement with sample period normalization:
                    # Prevents derivative kick on setpoint change
                    # Bound dt_norm >= 0.05 to prevent artificial derivative spikes from sensor noise
                    dt_norm = (
                        (dt / float(self._sample_period))
                        if self._sample_period > 0
                        else 1.0
                    )
                    if dt_norm < 0.05:
                        dt_norm = 0.05

                    if self._prev_room_temp is not None:
                        diff_temp = self._indoor_temp - self._prev_room_temp
                        if is_heating:
                            # Heating: demand increases as temperature drops
                            d_term = -self._kd * (diff_temp / dt_norm)
                        else:
                            # Cooling: demand increases as temperature rises
                            d_term = self._kd * (diff_temp / dt_norm)
                    else:
                        d_term = 0.0
                    self._prev_error = error
                    self._prev_room_temp = self._indoor_temp

                    # (5) I term with anti-windup and bidirectional Max I limit [-max_i, +max_i]:
                    # Always calculate integral across all modes; reset to 0 only on HVAC mode transitions
                    max_i_limit = max(0.0, float(self._max_i))
                    candidate_i = self._integral + (self._ki * error * dt)
                    candidate_i = max(-max_i_limit, min(max_i_limit, candidate_i))
                    candidate_raw = p_term + candidate_i + d_term + e_term

                    # Anti-windup check:
                    # When raw output saturates and error reinforces saturation, stop accumulation
                    if candidate_raw >= 100.0 and error > 0:
                        self._integral = max(-max_i_limit, min(max_i_limit, self._integral))
                    elif candidate_raw <= 0.0 and error < 0:
                        self._integral = max(-max_i_limit, min(max_i_limit, self._integral))
                    else:
                        self._integral = candidate_i

                    i_term = self._integral

                    # Total raw and final outputs (0.0 ~ 100.0%)
                    raw_output = p_term + i_term + d_term + e_term
                    final_output = max(0.0, min(100.0, raw_output))

                    # Store diagnostics
                    self._pid_error = round(error, 2)
                    self._pid_p = round(p_term, 2)
                    self._pid_i = round(i_term, 2)
                    self._pid_d = round(d_term, 2)
                    self._pid_e = round(e_term, 2)
                    self._pid_output = round(final_output, 2)

                # Branch for switch PWM mode (no climate sections / delta / fan)
                if self._control_mode == CONTROL_MODE_SWITCH_PWM:
                    if self._is_active_hvac_mode:
                        await self._async_start_pwm_cycle(force=force)
                    else:
                        await self._async_stop_pwm()
                    self.async_write_ha_state()
                    return

                # Section and Hysteresis mapping
                new_section = self._determine_active_section(
                    final_output, self._active_section
                )
                section_changed = (self._last_dispatched_section != new_section)
                self._active_section = new_section
                (
                    sec_apply_temp,
                    sec_delta,
                    sec_fan,
                    sec_hvac_mode,
                    apply_delta,
                    apply_fan,
                    apply_hvac_mode,
                ) = self._get_section_parameters(new_section)

                # 1. Delta & Target Temperature handling:
                if apply_delta:
                    effective_delta = sec_delta
                else:
                    effective_delta = 0.0

                raw_slave_temp = self._target_temp + effective_delta
                self._raw_target_temperature = round(raw_slave_temp, 2)
                int_slave_temp = int(math.floor(raw_slave_temp + 0.5))
                slave_min_temp, slave_max_temp = self._get_slave_temp_limits()
                safe_slave_temp = max(
                    int(slave_min_temp), min(int(slave_max_temp), int_slave_temp)
                )
                self._dispatched_temperature = safe_slave_temp

                # 2. HVAC Mode handling:
                if apply_hvac_mode and sec_hvac_mode and sec_hvac_mode != "follow":
                    try:
                        target_slave_mode = HVACMode(sec_hvac_mode)
                    except ValueError:
                        target_slave_mode = self._attr_hvac_mode
                else:
                    target_slave_mode = self._attr_hvac_mode

                # 3. Fan Mode handling:
                matched_fan = self._match_slave_fan_mode(sec_fan)
                if apply_fan and matched_fan:
                    self._dispatched_fan_mode = matched_fan

                # Dispatch control commands to slave climate entity
                if self._is_active_hvac_mode:
                    # Active PID control: dispatch according to section parameters
                    _LOGGER.info(
                        "PID active: section %d (output=%.1f%%). Dispatched temp: %s °C (target: %.1f °C, delta: %.1f °C, apply_temp: %s), Dispatched fan: %s (apply_fan: %s)",
                        new_section,
                        final_output,
                        safe_slave_temp,
                        self._target_temp,
                        effective_delta,
                        sec_apply_temp,
                        self._dispatched_fan_mode,
                        apply_fan,
                    )
                    await self._async_dispatch_control(
                        safe_slave_temp,
                        matched_fan,
                        final_output,
                        target_slave_mode,
                        apply_temp=sec_apply_temp,
                        apply_delta=apply_delta,
                        apply_fan=apply_fan,
                        apply_hvac_mode=apply_hvac_mode,
                        force=force or section_changed,
                        section_changed=section_changed,
                    )
                    self._last_dispatched_section = new_section
                else:
                    _LOGGER.debug(
                        "Thermostat not active (%s): active section %d, dispatched temp %s °C (apply_temp: %s), dispatched fan %s (apply_fan: %s)",
                        self._attr_hvac_mode,
                        new_section,
                        safe_slave_temp,
                        sec_apply_temp,
                        self._dispatched_fan_mode,
                        apply_fan,
                    )

            except Exception as err:
                _LOGGER.exception("Unexpected error in calculation and control: %s", err)
            finally:
                self.async_write_ha_state()

    def _get_slave_temp_limits(self) -> tuple[float, float]:
        """Fetch temperature/humidity bounds supported by slave device."""
        slave_state = self.hass.states.get(self._target_climate_id)
        is_hum = is_humidity_type(self._thermostat_type)
        dev_min = DEFAULT_SLAVE_MIN_HUMIDITY if is_hum else DEFAULT_SLAVE_MIN_TEMP
        dev_max = DEFAULT_SLAVE_MAX_HUMIDITY if is_hum else DEFAULT_SLAVE_MAX_TEMP
        if slave_state:
            val_min = slave_state.attributes.get("min_humidity" if is_hum else "min_temp")
            if val_min is None and is_hum:
                val_min = slave_state.attributes.get("min_temp")
            if val_min is not None:
                try:
                    dev_min = float(val_min)
                except (ValueError, TypeError):
                    dev_min = DEFAULT_SLAVE_MIN_HUMIDITY if is_hum else DEFAULT_SLAVE_MIN_TEMP

            val_max = slave_state.attributes.get("max_humidity" if is_hum else "max_temp")
            if val_max is None and is_hum:
                val_max = slave_state.attributes.get("max_temp")
            if val_max is not None:
                try:
                    dev_max = float(val_max)
                except (ValueError, TypeError):
                    dev_max = DEFAULT_SLAVE_MAX_HUMIDITY if is_hum else DEFAULT_SLAVE_MAX_TEMP

        return dev_min, dev_max

    def _match_slave_fan_mode(self, requested_fan: str) -> str:
        """Match requested fan mode to slave device's supported fan_modes."""
        slave_state = self.hass.states.get(self._target_climate_id)
        if not slave_state:
            return requested_fan

        supported_modes = slave_state.attributes.get("fan_modes")
        if not supported_modes or not isinstance(supported_modes, list):
            return requested_fan

        supported_str_modes = [str(m) for m in supported_modes]

        # 1. Exact match
        if requested_fan in supported_str_modes:
            return requested_fan

        # 2. Case-insensitive match
        req_lower = requested_fan.lower().strip()
        for mode in supported_str_modes:
            if mode.lower().strip() == req_lower:
                return mode

        # 3. Alias / keyword mapping using module constant
        candidates = FAN_ALIAS_MAP.get(req_lower, [req_lower])
        # Pass 3a: Exact candidate match
        for cand in candidates:
            cand_lower = cand.lower().strip()
            for mode in supported_str_modes:
                if cand_lower == mode.lower().strip():
                    return mode

        # Pass 3b: Substring candidate match (min length 2 to avoid single char mismatches)
        for cand in candidates:
            cand_lower = cand.lower().strip()
            if len(cand_lower) < 2:
                continue
            for mode in supported_str_modes:
                target_mode_lower = mode.lower().strip()
                if (
                    cand_lower in target_mode_lower
                    or target_mode_lower in cand_lower
                ):
                    return mode

        # 4. Fallback to requested_fan directly so the command is actually sent
        return requested_fan

    async def _async_set_slave_hvac_mode(self, mode: HVACMode | str) -> None:
        """Call climate.set_hvac_mode or humidifier.turn_on/off on the slave device only when mode actually changes."""
        target_domain = (
            self._target_climate_id.split(".")[0]
            if "." in self._target_climate_id
            else "climate"
        )
        target_mode_str = (
            mode.value if hasattr(mode, "value") else str(mode)
        ).lower().strip()

        slave_state = self.hass.states.get(self._target_climate_id)
        current_slave_state = (
            slave_state.state.lower().strip()
            if slave_state and slave_state.state
            else ""
        )

        if target_domain == "humidifier":
            if target_mode_str == "off":
                if current_slave_state == "off":
                    return
                service = SERVICE_TURN_OFF
            else:
                if current_slave_state == "on":
                    return
                service = SERVICE_TURN_ON
            self._is_dispatching = True
            try:
                await self.hass.services.async_call(
                    "humidifier",
                    service,
                    {ATTR_ENTITY_ID: self._target_climate_id},
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.error("Failed to set humidifier %s to %s: %s", self._target_climate_id, service, err)
            finally:
                self._is_dispatching = False
            return

        slave_modes = (
            slave_state.attributes.get("hvac_modes", []) if slave_state else []
        )
        slave_modes_str = [
            (m.value if hasattr(m, "value") else str(m)).lower().strip()
            for m in slave_modes
        ]

        effective_mode = target_mode_str
        if slave_modes_str and target_mode_str not in slave_modes_str:
            if target_mode_str == MODE_DEHUMIDITY:
                if "dry" in slave_modes_str:
                    effective_mode = "dry"
                elif "cool" in slave_modes_str:
                    effective_mode = "cool"
            elif target_mode_str == MODE_HUMIDITY:
                if "heat" in slave_modes_str:
                    effective_mode = "heat"
            elif target_mode_str == MODE_SILENT:
                if "fan_only" in slave_modes_str:
                    effective_mode = "fan_only"

        if current_slave_state == effective_mode:
            _LOGGER.debug(
                "Slave climate %s is already in %s mode; skipping mode change.",
                self._target_climate_id,
                effective_mode,
            )
            return

        was_dispatching = self._is_dispatching
        self._is_dispatching = True
        self._is_dispatching_hvac_mode = True
        try:
            self._expected_slave_mode = HVACMode(effective_mode)
        except ValueError:
            self._expected_slave_mode = None
        self._last_mode_dispatch_time = dt_util.utcnow()
        try:
            _LOGGER.info(
                "Setting slave %s HVAC mode to: %s",
                self._target_climate_id,
                effective_mode,
            )
            await self.hass.services.async_call(
                "climate",
                SERVICE_SET_HVAC_MODE,
                {
                    ATTR_ENTITY_ID: self._target_climate_id,
                    ATTR_HVAC_MODE: effective_mode,
                },
                blocking=True,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to set slave HVAC mode to %s: %s", effective_mode, err
            )
        finally:
            self._is_dispatching = was_dispatching
            self._is_dispatching_hvac_mode = False

    async def _async_dispatch_control(
        self,
        slave_temp: int,
        fan_mode: str,
        output: float,
        target_mode: HVACMode | None = None,
        apply_temp: bool = True,
        apply_delta: bool = True,
        apply_fan: bool = True,
        apply_hvac_mode: bool = True,
        force: bool = False,
        section_changed: bool = False,
    ) -> None:
        """Dispatch temperature, fan speed, and handle PWM or inverter continuous mode."""
        self._is_dispatching = True
        try:
            if target_mode is None:
                if self._thermostat_type == THERMOSTAT_TYPE_HEATER:
                    target_mode = HVACMode.HEAT
                elif self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                    target_mode = (
                        HVACMode.HEAT if getattr(self, "_is_heating", False) else HVACMode.COOL
                    )
                elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                    target_mode = MODE_HUMIDITY
                elif self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                    target_mode = MODE_DEHUMIDITY
                elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                    target_mode = (
                        MODE_HUMIDITY if getattr(self, "_is_heating", False) else MODE_DEHUMIDITY
                    )
                else:
                    target_mode = HVACMode.COOL
            elif (
                target_mode == HVACMode.AUTO
                and self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL
            ):
                slave_state = self.hass.states.get(self._target_climate_id)
                slave_modes = (
                    slave_state.attributes.get("hvac_modes", []) if slave_state else []
                )
                if (
                    slave_modes
                    and HVACMode.AUTO not in slave_modes
                    and HVACMode.AUTO.value not in slave_modes
                ):
                    target_mode = (
                        HVACMode.HEAT
                        if getattr(self, "_is_heating", False)
                        else HVACMode.COOL
                    )
            elif (
                (target_mode == HVACMode.AUTO or target_mode == "auto")
                and self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY
            ):
                target_mode = (
                    MODE_HUMIDITY
                    if getattr(self, "_is_heating", False)
                    else MODE_DEHUMIDITY
                )

            target_domain = (
                self._target_climate_id.split(".")[0]
                if "." in self._target_climate_id
                else "climate"
            )

            has_sent_command = False

            async def _delay_before_command() -> None:
                nonlocal has_sent_command
                if not has_sent_command:
                    if section_changed:
                        _LOGGER.debug("Section changed -> waiting 1.0s before first command")
                        await asyncio.sleep(1.0)
                    has_sent_command = True
                else:
                    _LOGGER.debug("Multiple commands -> waiting 0.5s between commands")
                    await asyncio.sleep(0.5)

            if target_domain == "humidifier":
                self._cancel_pwm_timers()
                self._pwm_state = True
                slave_state = self.hass.states.get(self._target_climate_id)
                if slave_state and slave_state.state != STATE_ON:
                    await _delay_before_command()
                    await self._async_set_slave_hvac_mode("on")
                if apply_temp:
                    current_slave_temp = (
                        slave_state.attributes.get("humidity") if slave_state else None
                    )
                    is_same_as_slave = (current_slave_temp == slave_temp)
                    is_same_as_last = (self._last_dispatched_temp == slave_temp)
                    if force or not (is_same_as_slave and is_same_as_last):
                        await _delay_before_command()
                        await self._async_send_temperature(slave_temp, force=force)
                return

            async def _ensure_slave_mode(mode: HVACMode | str) -> bool:
                if not apply_hvac_mode:
                    return False

                self._internal_pwm_slave_off = False
                slave_state = self.hass.states.get(self._target_climate_id)
                current_slave_state = (
                    slave_state.state.lower().strip()
                    if slave_state and slave_state.state
                    else ""
                )
                target_mode_str = (
                    mode.value if hasattr(mode, "value") else str(mode)
                ).lower().strip()

                if current_slave_state == target_mode_str:
                    _LOGGER.debug(
                        "Slave %s mode (%s) unchanged; skipping mode service call.",
                        self._target_climate_id,
                        target_mode_str,
                    )
                    return False

                await _delay_before_command()
                await self._async_set_slave_hvac_mode(mode)
                return True

            async def _dispatch_setpoints(force_call: bool = False) -> None:
                if apply_fan and fan_mode:
                    matched_fan = self._match_slave_fan_mode(fan_mode)
                    slave_state = self.hass.states.get(self._target_climate_id)
                    current_slave_fan = (
                        slave_state.attributes.get(ATTR_FAN_MODE)
                        if slave_state
                        else None
                    )
                    is_same_as_slave = (
                        current_slave_fan is not None
                        and str(current_slave_fan).lower().strip()
                        == str(matched_fan).lower().strip()
                    )
                    is_same_as_last = (
                        self._last_dispatched_fan_mode is not None
                        and str(self._last_dispatched_fan_mode).lower().strip()
                        == str(matched_fan).lower().strip()
                    )
                    if force_call or not (is_same_as_slave and is_same_as_last):
                        await _delay_before_command()
                        await self._async_send_fan_mode(fan_mode, force=force_call)

                if apply_temp:
                    slave_state = self.hass.states.get(self._target_climate_id)
                    current_slave_temp = (
                        slave_state.attributes.get(ATTR_TEMPERATURE)
                        if slave_state
                        else None
                    )
                    is_same_as_slave = (current_slave_temp == slave_temp)
                    is_same_as_last = (self._last_dispatched_temp == slave_temp)
                    if force_call or not (is_same_as_slave and is_same_as_last):
                        await _delay_before_command()
                        await self._async_send_temperature(slave_temp, force=force_call)

            # Continuous Inverter Dispatch (Keep slave HVAC mode active; do NOT shut off on low output)
            self._cancel_pwm_timers()
            self._pwm_state = True
            await _ensure_slave_mode(target_mode)
            target_mode_str = (
                target_mode.value if hasattr(target_mode, "value") else str(target_mode)
            ).lower()
            if target_mode_str not in ("off", HVACMode.OFF.value):
                await _dispatch_setpoints(force_call=force)
        finally:
            self._is_dispatching = False

    async def _async_send_temperature(
        self, slave_temp: int, force: bool = False
    ) -> bool:
        """Send target temperature or humidity only when value changes. Returns True if command sent."""
        target_domain = (
            self._target_climate_id.split(".")[0]
            if "." in self._target_climate_id
            else "climate"
        )
        slave_state = self.hass.states.get(self._target_climate_id)
        current_slave_temp = None
        if slave_state:
            if target_domain == "humidifier":
                current_slave_temp = slave_state.attributes.get("humidity")
            else:
                current_slave_temp = slave_state.attributes.get(ATTR_TEMPERATURE)

        # Update dispatched_temperature tracker
        self._dispatched_temperature = slave_temp

        is_same_as_slave = (current_slave_temp == slave_temp)
        is_same_as_last = (self._last_dispatched_temp == slave_temp)

        # Avoid redundant call if slave already has this temperature and was previously dispatched
        if not force and is_same_as_slave and is_same_as_last:
            return False

        was_dispatching = self._is_dispatching
        self._is_dispatching = True
        try:
            if target_domain == "humidifier":
                _LOGGER.info(
                    "Setting slave humidifier humidity to %d %% (slave prev: %s, last dispatched: %s, entity: %s)",
                    slave_temp,
                    current_slave_temp,
                    self._last_dispatched_temp,
                    self._target_climate_id,
                )
                await self.hass.services.async_call(
                    "humidifier",
                    "set_humidity",
                    {
                        ATTR_ENTITY_ID: self._target_climate_id,
                        "humidity": slave_temp,
                    },
                    blocking=True,
                )
            else:
                _LOGGER.info(
                    "Setting slave temperature to %d °C (slave prev: %s, last dispatched: %s, entity: %s)",
                    slave_temp,
                    current_slave_temp,
                    self._last_dispatched_temp,
                    self._target_climate_id,
                )
                await self.hass.services.async_call(
                    "climate",
                    SERVICE_SET_TEMPERATURE,
                    {
                        ATTR_ENTITY_ID: self._target_climate_id,
                        ATTR_TEMPERATURE: slave_temp,
                    },
                    blocking=True,
                )
            self._last_dispatched_temp = slave_temp
            return True
        except Exception as err:
            _LOGGER.error(
                "Failed to set slave setpoint to %d for %s: %s",
                slave_temp,
                self._target_climate_id,
                err,
            )
            return False
        finally:
            self._is_dispatching = was_dispatching

    async def _async_send_fan_mode(
        self, fan_mode: str, force: bool = False
    ) -> bool:
        """Send fan mode to slave climate entity. Returns True if command sent."""
        matched_fan = self._match_slave_fan_mode(fan_mode)
        self._dispatched_fan_mode = matched_fan

        slave_state = self.hass.states.get(self._target_climate_id)
        if not slave_state:
            _LOGGER.warning(
                "Target climate entity %s not found in hass states",
                self._target_climate_id,
            )
            return False

        # Check current fan mode on slave and last dispatched to avoid redundant calls
        current_slave_fan = slave_state.attributes.get(ATTR_FAN_MODE)
        is_same_as_slave = (
            current_slave_fan is not None
            and str(current_slave_fan).lower().strip() == str(matched_fan).lower().strip()
        )
        is_same_as_last = (
            self._last_dispatched_fan_mode is not None
            and str(self._last_dispatched_fan_mode).lower().strip() == str(matched_fan).lower().strip()
        )

        if not force and is_same_as_slave and is_same_as_last:
            _LOGGER.debug(
                "Slave climate %s fan mode is already %s (slave: %s, last: %s); skipping redundant call.",
                self._target_climate_id,
                matched_fan,
                current_slave_fan,
                self._last_dispatched_fan_mode,
            )
            return False

        was_dispatching = self._is_dispatching
        self._is_dispatching = True
        try:
            _LOGGER.info(
                "Setting slave fan mode to %s (slave prev: %s, last dispatched: %s, entity: %s)",
                matched_fan,
                current_slave_fan,
                self._last_dispatched_fan_mode,
                self._target_climate_id,
            )
            await self.hass.services.async_call(
                "climate",
                SERVICE_SET_FAN_MODE,
                {
                    ATTR_ENTITY_ID: self._target_climate_id,
                    ATTR_FAN_MODE: matched_fan,
                },
                blocking=True,
            )
            self._last_dispatched_fan_mode = matched_fan
            return True
        except Exception as err:
            _LOGGER.error(
                "Failed to set slave fan mode to %s for %s: %s",
                matched_fan,
                self._target_climate_id,
                err,
            )
            return False
        finally:
            self._is_dispatching = was_dispatching

    # ------------------------------------------------------------------
    # Switch PWM Control Methods
    # ------------------------------------------------------------------
    async def _async_turn_off_specific_device(self, entity_id: str) -> None:
        """Turn off a specific switch entity immediately."""
        if not entity_id:
            return
        target_domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
        try:
            await self.hass.services.async_call(
                target_domain,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
        except Exception as err:
            _LOGGER.debug("Failed turning off %s via %s: %s", entity_id, target_domain, err)
            if target_domain != "homeassistant":
                try:
                    await self.hass.services.async_call(
                        "homeassistant",
                        SERVICE_TURN_OFF,
                        {ATTR_ENTITY_ID: entity_id},
                        blocking=True,
                    )
                except Exception:
                    pass

    async def _async_turn_on_switch(self, force: bool = False) -> None:
        """Turn on the target switch with anti-short-cycling delay enforcement."""
        active_dev = self._active_target_device_id
        if not active_dev:
            return

        now = dt_util.utcnow()
        last_off = self._last_switch_off_times.get(active_dev, self._last_switch_off_time)
        pwm_dur = float(self._pwm_duration) if self._pwm_duration > 0 else float(DEFAULT_PWM_DURATION_SWITCH)
        if (float(self._min_on_time) + float(self._min_off_time)) >= pwm_dur:
            effective_min_off = min(float(self._min_off_time), max(1.0, pwm_dur * 0.05))
        else:
            effective_min_off = float(self._min_off_time)

        if not force and last_off is not None:
            elapsed_off = (now - last_off).total_seconds()
            if elapsed_off < effective_min_off:
                remaining_off = effective_min_off - elapsed_off
                _LOGGER.info(
                    "Anti-short-cycling: switch %s ON delayed by %.1fs (min_off_time: %.1fs)",
                    active_dev,
                    remaining_off,
                    effective_min_off,
                )
                if self._pwm_turn_on_cancel is not None:
                    self._pwm_turn_on_cancel()
                self._pwm_turn_on_cancel = async_call_later(
                    self.hass,
                    remaining_off,
                    lambda _: self._run_async(self._async_turn_on_switch(force=True)),
                )
                return

        if self._pwm_turn_on_cancel is not None:
            self._pwm_turn_on_cancel()
            self._pwm_turn_on_cancel = None

        # In dual PWM mode, ensure the inactive switch is turned off
        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            inactive_dev = None
            if self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                inactive_dev = (
                    self._target_device_cool_id
                    if getattr(self, "_is_heating", False)
                    else self._target_device_heat_id
                )
            elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                inactive_dev = (
                    self._target_device_dehumidity_id
                    if getattr(self, "_is_heating", False)
                    else self._target_device_humidity_id
                )
            if inactive_dev and inactive_dev != active_dev:
                await self._async_turn_off_specific_device(inactive_dev)

        target_domain = (
            active_dev.split(".")[0]
            if "." in active_dev
            else "homeassistant"
        )
        _LOGGER.info(
            "Turning ON switch %s (duty: %.1f%%, on_time: %.1fs)",
            active_dev,
            self._pwm_duty_cycle,
            self._pwm_on_time,
        )
        try:
            await self.hass.services.async_call(
                target_domain,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: active_dev},
                blocking=True,
            )
            self._pwm_state = True
            self._last_switch_on_time = now
            self._last_switch_on_times[active_dev] = now
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to turn on switch %s via %s: %s", active_dev, target_domain, err)
            if target_domain != "homeassistant":
                try:
                    await self.hass.services.async_call(
                        "homeassistant",
                        SERVICE_TURN_ON,
                        {ATTR_ENTITY_ID: active_dev},
                        blocking=True,
                    )
                    self._pwm_state = True
                    self._last_switch_on_time = now
                    self._last_switch_on_times[active_dev] = now
                    self.async_write_ha_state()
                except Exception as fb_err:
                    _LOGGER.error("Fallback homeassistant.turn_on failed for %s: %s", active_dev, fb_err)

    async def _async_turn_off_switch(self, force: bool = False) -> None:
        """Turn off the target switch with anti-short-cycling delay enforcement."""
        active_dev = self._active_target_device_id
        if not active_dev:
            return

        now = dt_util.utcnow()
        last_on = self._last_switch_on_times.get(active_dev, self._last_switch_on_time)
        pwm_dur = float(self._pwm_duration) if self._pwm_duration > 0 else float(DEFAULT_PWM_DURATION_SWITCH)
        if (float(self._min_on_time) + float(self._min_off_time)) >= pwm_dur:
            effective_min_on = min(float(self._min_on_time), max(1.0, pwm_dur * 0.05))
        else:
            effective_min_on = float(self._min_on_time)

        if not force and last_on is not None:
            elapsed_on = (now - last_on).total_seconds()
            if elapsed_on < effective_min_on:
                remaining_on = effective_min_on - elapsed_on
                _LOGGER.info(
                    "Anti-short-cycling: switch %s OFF delayed by %.1fs (min_on_time: %.1fs)",
                    active_dev,
                    remaining_on,
                    effective_min_on,
                )
                if self._pwm_turn_off_cancel is not None:
                    self._pwm_turn_off_cancel()
                self._pwm_turn_off_cancel = async_call_later(
                    self.hass,
                    remaining_on,
                    lambda _: self._run_async(self._async_turn_off_switch(force=True)),
                )
                return

        if self._pwm_turn_off_cancel is not None:
            self._pwm_turn_off_cancel()
            self._pwm_turn_off_cancel = None

        target_domain = (
            active_dev.split(".")[0]
            if "." in active_dev
            else "homeassistant"
        )
        _LOGGER.info("Turning OFF switch %s", active_dev)
        try:
            await self.hass.services.async_call(
                target_domain,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: active_dev},
                blocking=True,
            )
            self._pwm_state = False
            self._last_switch_off_time = now
            self._last_switch_off_times[active_dev] = now
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to turn off switch %s via %s: %s", active_dev, target_domain, err)
            if target_domain != "homeassistant":
                try:
                    await self.hass.services.async_call(
                        "homeassistant",
                        SERVICE_TURN_OFF,
                        {ATTR_ENTITY_ID: active_dev},
                        blocking=True,
                    )
                    self._pwm_state = False
                    self._last_switch_off_time = now
                    self._last_switch_off_times[active_dev] = now
                    self.async_write_ha_state()
                except Exception as fb_err:
                    _LOGGER.error("Fallback homeassistant.turn_off failed for %s: %s", active_dev, fb_err)

    async def _async_stop_pwm(self) -> None:
        """Stop PWM operation and ensure target switch is turned OFF."""
        if self._pwm_cycle_cancel is not None:
            self._pwm_cycle_cancel()
            self._pwm_cycle_cancel = None
        if self._pwm_turn_off_cancel is not None:
            self._pwm_turn_off_cancel()
            self._pwm_turn_off_cancel = None
        if self._pwm_turn_on_cancel is not None:
            self._pwm_turn_on_cancel()
            self._pwm_turn_on_cancel = None

        self._pwm_duty_cycle = 0.0
        self._pwm_on_time = 0.0
        self._pwm_cycle_start = None
        self._cycle_start_time = None
        self._cycle_end_time = None
        self._cycle_turn_off_time = None
        self._pwm_state = False

        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            if self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                if self._target_device_cool_id:
                    await self._async_turn_off_specific_device(self._target_device_cool_id)
                if self._target_device_heat_id:
                    await self._async_turn_off_specific_device(self._target_device_heat_id)
            elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                if self._target_device_dehumidity_id:
                    await self._async_turn_off_specific_device(self._target_device_dehumidity_id)
                if self._target_device_humidity_id:
                    await self._async_turn_off_specific_device(self._target_device_humidity_id)
        await self._async_turn_off_switch(force=True)

    async def _async_start_pwm_cycle(self, force: bool = False) -> None:
        """Manage and start/update PWM cycle based on the current PID output."""
        if not self._is_active_hvac_mode:
            await self._async_stop_pwm()
            return

        output = max(0.0, min(100.0, float(self._pid_output or 0.0)))
        pwm_duration = float(self._pwm_duration)
        if pwm_duration <= 0:
            pwm_duration = float(DEFAULT_PWM_DURATION_SWITCH)

        calc_on_time = pwm_duration * (output / 100.0)
        calc_off_time = pwm_duration - calc_on_time

        # Always report the genuine proportional duty cycle and on-time to HA state
        self._pwm_duty_cycle = round(output, 1)
        self._pwm_on_time = round(calc_on_time, 1)

        # Anti-short-cycling guard thresholds for physical cycle execution:
        if (float(self._min_on_time) + float(self._min_off_time)) >= pwm_duration:
            guard_min_on = min(float(self._min_on_time), max(1.0, pwm_duration * 0.05))
            guard_min_off = min(float(self._min_off_time), max(1.0, pwm_duration * 0.05))
        else:
            guard_min_on = float(self._min_on_time)
            guard_min_off = float(self._min_off_time)

        if output <= 0.0 or calc_on_time < guard_min_on:
            effective_on_time = 0.0
        elif output >= 100.0 or calc_off_time < guard_min_off:
            effective_on_time = pwm_duration
        else:
            effective_on_time = calc_on_time

        now = dt_util.utcnow()

        _LOGGER.debug(
            "PWM evaluation: output=%.1f%%, effective duty=%.1f%%, on_time=%.1fs (calc=%.1fs), duration=%ds",
            output,
            round((effective_on_time / pwm_duration) * 100.0, 1),
            effective_on_time,
            calc_on_time,
            int(pwm_duration),
        )

        # -------------------------------------------------------------
        # Case 1: 0% duty cycle (fully OFF this cycle)
        # -------------------------------------------------------------
        if effective_on_time <= 0.0:
            if self._pwm_turn_on_cancel is not None:
                self._pwm_turn_on_cancel()
                self._pwm_turn_on_cancel = None
            if self._pwm_turn_off_cancel is not None:
                self._pwm_turn_off_cancel()
                self._pwm_turn_off_cancel = None

            self._cycle_start_time = None
            self._cycle_end_time = None
            self._cycle_turn_off_time = None

            if self._pwm_state:
                await self._async_turn_off_switch()

            # If output > 0 but suppressed by guard_min_on, schedule next cycle evaluation
            if self._pwm_cycle_cancel is not None:
                self._pwm_cycle_cancel()
                self._pwm_cycle_cancel = None

            if output > 0.0:
                @callback
                def _handle_suppressed_cycle_next(_):
                    self._pwm_cycle_cancel = None
                    self._run_async(self._async_calculate_and_control())

                self._pwm_cycle_cancel = async_call_later(
                    self.hass,
                    pwm_duration,
                    _handle_suppressed_cycle_next,
                )
            return

        # -------------------------------------------------------------
        # Case 2: 100% duty cycle (continuous ON)
        # -------------------------------------------------------------
        if effective_on_time >= pwm_duration:
            if self._pwm_turn_off_cancel is not None:
                self._pwm_turn_off_cancel()
                self._pwm_turn_off_cancel = None

            self._cycle_start_time = now
            self._cycle_end_time = now + timedelta(seconds=pwm_duration)
            self._cycle_turn_off_time = self._cycle_end_time

            if not self._pwm_state:
                await self._async_turn_on_switch()

            # Schedule periodic refresh for next cycle
            if self._pwm_cycle_cancel is not None:
                self._pwm_cycle_cancel()

            @callback
            def _handle_full_cycle_next(_):
                self._pwm_cycle_cancel = None
                self._run_async(self._async_calculate_and_control())

            self._pwm_cycle_cancel = async_call_later(
                self.hass,
                pwm_duration,
                _handle_full_cycle_next,
            )
            return

        # -------------------------------------------------------------
        # Case 3: Intermediate duty cycle (0 < effective_on_time < pwm_duration)
        # -------------------------------------------------------------
        cycle_running = (
            self._cycle_start_time is not None
            and (now - self._cycle_start_time).total_seconds() < pwm_duration
        )

        if not cycle_running or force:
            # Start a brand new PWM cycle
            self._cycle_start_time = now
            self._cycle_end_time = now + timedelta(seconds=pwm_duration)
            self._cycle_turn_off_time = now + timedelta(seconds=effective_on_time)

            await self._async_turn_on_switch()

            if self._pwm_turn_off_cancel is not None:
                self._pwm_turn_off_cancel()

            @callback
            def _handle_turn_off(_):
                self._pwm_turn_off_cancel = None
                self._run_async(self._async_turn_off_switch())

            self._pwm_turn_off_cancel = async_call_later(
                self.hass,
                effective_on_time,
                _handle_turn_off,
            )

            if self._pwm_cycle_cancel is not None:
                self._pwm_cycle_cancel()

            @callback
            def _handle_next_cycle(_):
                self._pwm_cycle_cancel = None
                self._cycle_start_time = None
                self._run_async(self._async_calculate_and_control())

            self._pwm_cycle_cancel = async_call_later(
                self.hass,
                pwm_duration,
                _handle_next_cycle,
            )
            return

        # Dynamically adjust current running cycle according to new calculation
        cycle_elapsed = (now - self._cycle_start_time).total_seconds()
        self._cycle_turn_off_time = self._cycle_start_time + timedelta(seconds=effective_on_time)

        if self._pwm_state:
            if cycle_elapsed >= effective_on_time:
                # Target on-time reached; turn off switch
                if self._pwm_turn_off_cancel is not None:
                    self._pwm_turn_off_cancel()
                    self._pwm_turn_off_cancel = None
                await self._async_turn_off_switch()
            else:
                # Adjust remaining on-time
                remaining_on = effective_on_time - cycle_elapsed
                if self._pwm_turn_off_cancel is not None:
                    self._pwm_turn_off_cancel()

                @callback
                def _handle_turn_off_adjusted(_):
                    self._pwm_turn_off_cancel = None
                    self._run_async(self._async_turn_off_switch())

                self._pwm_turn_off_cancel = async_call_later(
                    self.hass,
                    remaining_on,
                    _handle_turn_off_adjusted,
                )
        else:
            if cycle_elapsed < effective_on_time:
                # Output increased while switch was off; turn back on
                await self._async_turn_on_switch()
                remaining_on = effective_on_time - cycle_elapsed
                if self._pwm_turn_off_cancel is not None:
                    self._pwm_turn_off_cancel()

                @callback
                def _handle_turn_off_resume(_):
                    self._pwm_turn_off_cancel = None
                    self._run_async(self._async_turn_off_switch())

                self._pwm_turn_off_cancel = async_call_later(
                    self.hass,
                    remaining_on,
                    _handle_turn_off_resume,
                )

    async def _async_keep_alive_handler(self, _now: datetime) -> None:
        """Periodic keep-alive handler to ensure switch state integrity."""
        if self._control_mode != CONTROL_MODE_SWITCH_PWM:
            return

        active_dev = self._active_target_device_id
        if not active_dev:
            return

        target_domain = (
            active_dev.split(".")[0]
            if "." in active_dev
            else "homeassistant"
        )
        desired_service = SERVICE_TURN_ON if (self._is_active_hvac_mode and self._pwm_state) else SERVICE_TURN_OFF
        current_state = self.hass.states.get(active_dev)

        # Check if actual state differs from desired state, or re-assert periodically to prevent RF/Zigbee loss
        is_on = current_state is not None and current_state.state == STATE_ON
        desired_is_on = (desired_service == SERVICE_TURN_ON)

        if is_on != desired_is_on:
            _LOGGER.warning(
                "Keep-alive mismatch for switch %s: actual=%s, desired=%s. Re-asserting.",
                active_dev,
                current_state.state if current_state else "None",
                "on" if desired_is_on else "off",
            )

        try:
            await self.hass.services.async_call(
                target_domain,
                desired_service,
                {ATTR_ENTITY_ID: active_dev},
                blocking=False,
            )
        except Exception as err:
            _LOGGER.error("Keep-alive signal failed for %s via %s: %s", active_dev, target_domain, err)
            if target_domain != "homeassistant":
                try:
                    await self.hass.services.async_call(
                        "homeassistant",
                        desired_service,
                        {ATTR_ENTITY_ID: active_dev},
                        blocking=False,
                    )
                except Exception as fb_err:
                    _LOGGER.error("Fallback homeassistant keep-alive failed for %s: %s", active_dev, fb_err)

        # Ensure inactive device is strictly OFF in dual PWM mode
        inactive_dev = None
        if self._thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
            inactive_dev = (
                self._target_device_cool_id
                if getattr(self, "_is_heating", False)
                else self._target_device_heat_id
            )
        elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
            inactive_dev = (
                self._target_device_dehumidity_id
                if getattr(self, "_is_heating", False)
                else self._target_device_humidity_id
            )
        if inactive_dev and inactive_dev != active_dev:
            inact_state = self.hass.states.get(inactive_dev)
            if inact_state is not None and inact_state.state == STATE_ON:
                _LOGGER.warning(
                    "Inactive switch %s was found ON during %s mode! Turning it OFF.",
                    inactive_dev,
                    "HEATING/HUMIDIFYING" if getattr(self, "_is_heating", False) else "COOLING/DEHUMIDIFYING",
                )
                await self._async_turn_off_specific_device(inactive_dev)

