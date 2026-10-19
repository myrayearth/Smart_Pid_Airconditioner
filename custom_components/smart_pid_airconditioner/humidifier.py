"""Humidifier platform for Smart PID Air Conditioner."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.components.humidifier import (
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    Platform,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
import homeassistant.util.dt as dt_util

from .const import (
    DOMAIN,
    ALL_FAN_MODES,
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
    CONF_SEC1_APPLY_ACTION,
    CONF_SEC1_ACTION,
    CONF_SEC1_APPLY_MODE,
    CONF_SEC1_MODE,
    CONF_SEC1_DELTA,
    CONF_SEC1_FAN,
    CONF_SEC1_HVAC_MODE,
    CONF_SEC1_MAX,
    CONF_SEC2_APPLY_TEMP,
    CONF_SEC2_APPLY_DELTA,
    CONF_SEC2_APPLY_FAN,
    CONF_SEC2_APPLY_HVAC_MODE,
    CONF_SEC2_APPLY_ACTION,
    CONF_SEC2_ACTION,
    CONF_SEC2_APPLY_MODE,
    CONF_SEC2_MODE,
    CONF_SEC2_DELTA,
    CONF_SEC2_FAN,
    CONF_SEC2_HVAC_MODE,
    CONF_SEC2_MAX,
    CONF_SEC3_APPLY_TEMP,
    CONF_SEC3_APPLY_DELTA,
    CONF_SEC3_APPLY_FAN,
    CONF_SEC3_APPLY_HVAC_MODE,
    CONF_SEC3_APPLY_ACTION,
    CONF_SEC3_ACTION,
    CONF_SEC3_APPLY_MODE,
    CONF_SEC3_MODE,
    CONF_SEC3_DELTA,
    CONF_SEC3_FAN,
    CONF_SEC3_HVAC_MODE,
    CONF_SEC3_MAX,
    CONF_SEC4_APPLY_TEMP,
    CONF_SEC4_APPLY_DELTA,
    CONF_SEC4_APPLY_FAN,
    CONF_SEC4_APPLY_HVAC_MODE,
    CONF_SEC4_APPLY_ACTION,
    CONF_SEC4_ACTION,
    CONF_SEC4_APPLY_MODE,
    CONF_SEC4_MODE,
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
    DEFAULT_AVAILABLE_HUMIDITY_MODES,
    DEFAULT_CONTROL_MODE,
    DEFAULT_INITIAL_TARGET_HUMIDITY,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MIN_OFF_TIME,
    DEFAULT_MIN_ON_TIME,
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
    DEFAULT_SEC_APPLY_ACTION,
    DEFAULT_SEC_ACTION,
    DEFAULT_SEC_APPLY_MODE,
    DEFAULT_SEC_MODE,
    DEFAULT_SYNC_SLAVE_STATE,
    DEFAULT_THERMOSTAT_TYPE,
    FAIL_SAFE_MULTIPLIER,
    FAIL_SAFE_OUTPUT,
    HYSTERESIS_BAND,
    OFF_ACTION_SYNC_ONLY,
    OFF_ACTION_TURN_ON,
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart PID Humidifier entity from a config entry."""
    thermostat_type = entry.data.get(CONF_THERMOSTAT_TYPE, entry.options.get(CONF_THERMOSTAT_TYPE))

    # If this entry is for cooler/heater, clean up any stale humidifier entity and return
    if not is_humidity_type(thermostat_type):
        registry = er.async_get(hass)
        old_id = registry.async_get_entity_id(
            Platform.HUMIDIFIER, DOMAIN, f"{entry.entry_id}_humidifier"
        )
        if old_id:
            registry.async_remove(old_id)
        return

    # Clean up old climate entity if it was previously created under climate platform
    registry = er.async_get(hass)
    old_climate_id = registry.async_get_entity_id(
        Platform.CLIMATE, DOMAIN, f"{entry.entry_id}_thermostat"
    )
    if old_climate_id:
        registry.async_remove(old_climate_id)

    async_add_entities([SmartPIDHumidifierEntity(hass, entry)], True)


class SmartPIDHumidifierEntity(HumidifierEntity, RestoreEntity):
    """Smart PID Humidifier / Dehumidifier Entity."""

    _attr_has_entity_name = True
    _attr_translation_key = DOMAIN

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the humidifier entity."""
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_humidifier"
        self._attr_name = entry.title

        # Lock to prevent concurrent calculation loops
        self._calc_lock = asyncio.Lock()

        # Read config / options
        self._load_config()

        # Target humidity & state
        self._target_humidity: float = self._initial_target_temp
        self._is_on: bool = False
        if self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
            self._current_mode = MODE_DEHUMIDITY
        elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
            self._current_mode = MODE_HUMIDITY
        else:
            self._current_mode = MODE_DEHUMIDITY

        # Sensor readings & timestamps
        self._indoor_temp: float | None = None
        self._outdoor_temp: float | None = None
        self._indoor_humidity: float | None = None
        self._outdoor_humidity: float | None = None
        self._last_indoor_update: datetime | None = None
        self._last_outdoor_update: datetime | None = None

        # PID algorithm state
        self._last_calc_time: datetime | None = None
        self._prev_room_humidity: float | None = None
        self._prev_error: float | None = None
        self._integral: float = 0.0
        self._pid_error: float | None = None
        self._pid_p: float = 0.0
        self._pid_i: float = 0.0
        self._pid_d: float = 0.0
        self._pid_e: float = 0.0
        self._pid_output: float = 0.0
        self._active_section: int = 1
        self._raw_target_humidity: float = self._target_humidity
        self._dispatched_humidity: int | None = None
        self._dispatched_fan_mode: str | None = None
        self._dispatched_action: str | None = None
        self._dispatched_mode: str | None = None
        self._last_dispatched_humidity: int | None = None
        self._last_dispatched_fan_mode: str | None = None
        self._last_dispatched_section: int | None = None
        self._last_dispatched_mode: str | None = None
        self._fail_safe_active: bool = False
        self._is_heating: bool = (self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER)

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

        # Event tracking unsubscribers
        self._unsub_sensor_track: CALLBACK_TYPE | None = None
        self._unsub_interval_tick: CALLBACK_TYPE | None = None

        # Guard flags
        self._is_dispatching: bool = False

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
        """Helper to get value from options first, then entry data, then default."""
        target_sec = None
        for i in range(1, 5):
            if key.startswith(f"sec{i}_"):
                target_sec = f"section_{i}"
                break

        if target_sec and target_sec in self._entry.options:
            sec_dict = self._entry.options[target_sec]
            if isinstance(sec_dict, dict) and key in sec_dict and sec_dict[key] is not None:
                return sec_dict[key]

        val = self._entry.options.get(key)
        if val is not None:
            return val

        for sec in ("ki_group", "section_1", "section_2", "section_3", "section_4"):
            sec_dict = self._entry.options.get(sec)
            if isinstance(sec_dict, dict):
                sec_val = sec_dict.get(key)
                if sec_val is not None:
                    return sec_val

        if target_sec and target_sec in self._entry.data:
            sec_dict = self._entry.data[target_sec]
            if isinstance(sec_dict, dict) and key in sec_dict and sec_dict[key] is not None:
                return sec_dict[key]

        val = self._entry.data.get(key)
        if val is not None:
            return val

        for sec in ("ki_group", "section_1", "section_2", "section_3", "section_4"):
            sec_dict = self._entry.data.get(sec)
            if isinstance(sec_dict, dict):
                sec_val = sec_dict.get(key)
                if sec_val is not None:
                    return sec_val
        return default

    def _load_config(self) -> None:
        """Load and cache all configuration parameters."""
        self._control_mode: str = self._get_setting(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        self._thermostat_type: str = self._get_setting(CONF_THERMOSTAT_TYPE, THERMOSTAT_TYPE_DEHUMIDIFIER)
        self._target_device_id: str = self._get_setting(
            CONF_TARGET_DEVICE, self._get_setting(CONF_TARGET_CLIMATE, "")
        )
        self._target_device_cool_id: str = self._get_setting(CONF_TARGET_DEVICE_COOL, "")
        self._target_device_heat_id: str = self._get_setting(CONF_TARGET_DEVICE_HEAT, "")
        self._target_device_dehumidity_id: str = self._get_setting(CONF_TARGET_DEVICE_DEHUMIDITY, "")
        self._target_device_humidity_id: str = self._get_setting(CONF_TARGET_DEVICE_HUMIDITY, "")

        self._indoor_sensor_id: str = self._get_setting(CONF_INDOOR_SENSOR, "")
        self._outdoor_sensor_id: str = self._get_setting(CONF_OUTDOOR_SENSOR, "")

        self._available_modes: list[str] = self._get_setting(
            CONF_AVAILABLE_HVAC_MODES, DEFAULT_AVAILABLE_HUMIDITY_MODES
        )
        if self._control_mode == CONTROL_MODE_CLIMATE_DELTA and self._target_device_id:
            slave_state = self.hass.states.get(self._target_device_id)
            if slave_state and "available_modes" in slave_state.attributes:
                modes = slave_state.attributes.get("available_modes")
                if isinstance(modes, list) and modes:
                    self._available_modes = list(dict.fromkeys(str(m) for m in modes))

        self._available_fan_modes: list[str] = self._get_setting(
            CONF_AVAILABLE_FAN_MODES, DEFAULT_AVAILABLE_FAN_MODES
        )

        self._initial_target_temp: float = float(
            self._get_setting(CONF_INITIAL_TARGET_TEMP, DEFAULT_INITIAL_TARGET_HUMIDITY)
        )
        self._min_humidity: float = float(self._get_setting(CONF_MIN_TEMP, DEFAULT_MIN_HUMIDITY))
        self._max_humidity: float = float(self._get_setting(CONF_MAX_TEMP, DEFAULT_MAX_HUMIDITY))
        self._temp_step: float = float(self._get_setting(CONF_TEMP_STEP, DEFAULT_HUMIDITY_STEP))

        self._off_temp_action: str = self._get_setting(
            CONF_OFF_TEMP_ACTION, DEFAULT_OFF_TEMP_ACTION
        )
        self._sync_slave_state: bool = bool(
            self._get_setting(CONF_SYNC_SLAVE_STATE, DEFAULT_SYNC_SLAVE_STATE)
        )

        self._kp: float = float(self._get_setting(CONF_KP, DEFAULT_KP))
        self._ki: float = float(self._get_setting(CONF_KI, DEFAULT_KI))
        self._max_i: float = float(self._get_setting(CONF_MAX_I, DEFAULT_MAX_I))
        self._kd: float = float(self._get_setting(CONF_KD, DEFAULT_KD))
        self._ke: float = float(self._get_setting(CONF_KE, DEFAULT_KE))

        raw_pwm = self._get_setting(
            CONF_PWM_DURATION,
            DEFAULT_PWM_DURATION_SWITCH
            if self._control_mode == CONTROL_MODE_SWITCH_PWM
            else DEFAULT_PWM_DURATION,
        )
        self._pwm_duration: int = duration_to_seconds(
            raw_pwm,
            DEFAULT_PWM_DURATION_SWITCH
            if self._control_mode == CONTROL_MODE_SWITCH_PWM
            else DEFAULT_PWM_DURATION,
        )
        self._sample_period: int = int(
            self._get_setting(CONF_SAMPLE_PERIOD, DEFAULT_SAMPLE_PERIOD)
        )

        self._min_on_time: int = duration_to_seconds(
            self._get_setting(CONF_MIN_ON_TIME, DEFAULT_MIN_ON_TIME), DEFAULT_MIN_ON_TIME
        )
        self._min_off_time: int = duration_to_seconds(
            self._get_setting(CONF_MIN_OFF_TIME, DEFAULT_MIN_OFF_TIME), DEFAULT_MIN_OFF_TIME
        )
        self._keep_alive: int = duration_to_seconds(
            self._get_setting(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE), DEFAULT_KEEP_ALIVE
        )
        self._output_safety: float = float(
            self._get_setting(CONF_OUTPUT_SAFETY, DEFAULT_OUTPUT_SAFETY)
        )

        # Section configurations
        self._sec1_max: float = float(self._get_setting(CONF_SEC1_MAX, DEFAULT_SEC1_MAX))
        self._sec1_apply_temp: bool = bool(
            self._get_setting(CONF_SEC1_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP)
        )
        self._sec1_apply_delta: bool = bool(
            self._get_setting(CONF_SEC1_APPLY_DELTA, DEFAULT_SEC_APPLY_DELTA)
        )
        self._sec1_delta: float = float(self._get_setting(CONF_SEC1_DELTA, DEFAULT_SEC1_DELTA))
        self._sec1_apply_fan: bool = bool(
            self._get_setting(CONF_SEC1_APPLY_FAN, DEFAULT_SEC_APPLY_FAN)
        )
        self._sec1_fan: str = self._get_setting(CONF_SEC1_FAN, DEFAULT_SEC1_FAN)
        self._sec1_apply_action: bool = bool(
            self._get_setting(CONF_SEC1_APPLY_ACTION, DEFAULT_SEC_APPLY_ACTION)
        )
        self._sec1_action: str = str(
            self._get_setting(
                CONF_SEC1_ACTION,
                "off" if self._get_setting(CONF_SEC1_HVAC_MODE, None) == "off" else DEFAULT_SEC_ACTION,
            )
        )
        self._sec1_apply_mode: bool = bool(
            self._get_setting(
                CONF_SEC1_APPLY_MODE,
                self._get_setting(CONF_SEC1_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_MODE),
            )
        )
        self._sec1_mode: str = str(
            self._get_setting(
                CONF_SEC1_MODE,
                self._get_setting(CONF_SEC1_HVAC_MODE, MODE_DEHUMIDITY),
            )
        )
        self._sec1_apply_hvac_mode: bool = self._sec1_apply_mode
        self._sec1_hvac_mode: str = self._sec1_mode

        self._sec2_max: float = float(self._get_setting(CONF_SEC2_MAX, DEFAULT_SEC2_MAX))
        self._sec2_apply_temp: bool = bool(
            self._get_setting(CONF_SEC2_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP)
        )
        self._sec2_apply_delta: bool = bool(
            self._get_setting(CONF_SEC2_APPLY_DELTA, DEFAULT_SEC_APPLY_DELTA)
        )
        self._sec2_delta: float = float(self._get_setting(CONF_SEC2_DELTA, DEFAULT_SEC2_DELTA))
        self._sec2_apply_fan: bool = bool(
            self._get_setting(CONF_SEC2_APPLY_FAN, DEFAULT_SEC_APPLY_FAN)
        )
        self._sec2_fan: str = self._get_setting(CONF_SEC2_FAN, DEFAULT_SEC2_FAN)
        self._sec2_apply_action: bool = bool(
            self._get_setting(CONF_SEC2_APPLY_ACTION, DEFAULT_SEC_APPLY_ACTION)
        )
        self._sec2_action: str = str(
            self._get_setting(
                CONF_SEC2_ACTION,
                "off" if self._get_setting(CONF_SEC2_HVAC_MODE, None) == "off" else DEFAULT_SEC_ACTION,
            )
        )
        self._sec2_apply_mode: bool = bool(
            self._get_setting(
                CONF_SEC2_APPLY_MODE,
                self._get_setting(CONF_SEC2_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_MODE),
            )
        )
        self._sec2_mode: str = str(
            self._get_setting(
                CONF_SEC2_MODE,
                self._get_setting(CONF_SEC2_HVAC_MODE, MODE_DEHUMIDITY),
            )
        )
        self._sec2_apply_hvac_mode: bool = self._sec2_apply_mode
        self._sec2_hvac_mode: str = self._sec2_mode

        self._sec3_max: float = float(self._get_setting(CONF_SEC3_MAX, DEFAULT_SEC3_MAX))
        self._sec3_apply_temp: bool = bool(
            self._get_setting(CONF_SEC3_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP)
        )
        self._sec3_apply_delta: bool = bool(
            self._get_setting(CONF_SEC3_APPLY_DELTA, DEFAULT_SEC_APPLY_DELTA)
        )
        self._sec3_delta: float = float(self._get_setting(CONF_SEC3_DELTA, DEFAULT_SEC3_DELTA))
        self._sec3_apply_fan: bool = bool(
            self._get_setting(CONF_SEC3_APPLY_FAN, DEFAULT_SEC_APPLY_FAN)
        )
        self._sec3_fan: str = self._get_setting(CONF_SEC3_FAN, DEFAULT_SEC3_FAN)
        self._sec3_apply_action: bool = bool(
            self._get_setting(CONF_SEC3_APPLY_ACTION, DEFAULT_SEC_APPLY_ACTION)
        )
        self._sec3_action: str = str(
            self._get_setting(
                CONF_SEC3_ACTION,
                "off" if self._get_setting(CONF_SEC3_HVAC_MODE, None) == "off" else DEFAULT_SEC_ACTION,
            )
        )
        self._sec3_apply_mode: bool = bool(
            self._get_setting(
                CONF_SEC3_APPLY_MODE,
                self._get_setting(CONF_SEC3_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_MODE),
            )
        )
        self._sec3_mode: str = str(
            self._get_setting(
                CONF_SEC3_MODE,
                self._get_setting(CONF_SEC3_HVAC_MODE, MODE_DEHUMIDITY),
            )
        )
        self._sec3_apply_hvac_mode: bool = self._sec3_apply_mode
        self._sec3_hvac_mode: str = self._sec3_mode

        self._sec4_apply_temp: bool = bool(
            self._get_setting(CONF_SEC4_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP)
        )
        self._sec4_apply_delta: bool = bool(
            self._get_setting(CONF_SEC4_APPLY_DELTA, DEFAULT_SEC_APPLY_DELTA)
        )
        self._sec4_delta: float = float(self._get_setting(CONF_SEC4_DELTA, DEFAULT_SEC4_DELTA))
        self._sec4_apply_fan: bool = bool(
            self._get_setting(CONF_SEC4_APPLY_FAN, DEFAULT_SEC_APPLY_FAN)
        )
        self._sec4_fan: str = self._get_setting(CONF_SEC4_FAN, DEFAULT_SEC4_FAN)
        self._sec4_apply_action: bool = bool(
            self._get_setting(CONF_SEC4_APPLY_ACTION, DEFAULT_SEC_APPLY_ACTION)
        )
        self._sec4_action: str = str(
            self._get_setting(
                CONF_SEC4_ACTION,
                "off" if self._get_setting(CONF_SEC4_HVAC_MODE, None) == "off" else DEFAULT_SEC_ACTION,
            )
        )
        self._sec4_apply_mode: bool = bool(
            self._get_setting(
                CONF_SEC4_APPLY_MODE,
                self._get_setting(CONF_SEC4_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_MODE),
            )
        )
        self._sec4_mode: str = str(
            self._get_setting(
                CONF_SEC4_MODE,
                self._get_setting(CONF_SEC4_HVAC_MODE, MODE_DEHUMIDITY),
            )
        )
        self._sec4_apply_hvac_mode: bool = self._sec4_apply_mode
        self._sec4_hvac_mode: str = self._sec4_mode

    def _active_target_device_id(self) -> str:
        """Return the active target switch/slave device ID based on mode and configuration."""
        if self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
            if self._current_mode == MODE_HUMIDITY:
                return self._target_device_humidity_id or self._target_device_id
            return self._target_device_dehumidity_id or self._target_device_id
        return self._target_device_id

    # ---------------------------------------------------------
    # Humidifier Entity Properties
    # ---------------------------------------------------------
    @property
    def supported_features(self) -> HumidifierEntityFeature:
        """Return supported features."""
        if self._available_modes:
            return HumidifierEntityFeature.MODES
        return HumidifierEntityFeature(0)

    @property
    def device_class(self) -> HumidifierDeviceClass | None:
        """Return the device class."""
        if self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
            return HumidifierDeviceClass.DEHUMIDIFIER
        if self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
            return HumidifierDeviceClass.HUMIDIFIER
        if self._current_mode == MODE_DEHUMIDITY:
            return HumidifierDeviceClass.DEHUMIDIFIER
        return HumidifierDeviceClass.HUMIDIFIER

    @property
    def is_on(self) -> bool:
        """Return True if the humidifier is on."""
        return self._is_on

    @property
    def target_humidity(self) -> int | None:
        """Return target humidity."""
        if self._target_humidity is None:
            return None
        return int(round(self._target_humidity))

    @property
    def current_humidity(self) -> float | None:
        """Return current indoor relative humidity."""
        return self._indoor_humidity

    @property
    def min_humidity(self) -> int:
        """Return the minimum humidity."""
        return int(self._min_humidity)

    @property
    def max_humidity(self) -> int:
        """Return the maximum humidity."""
        return int(self._max_humidity)

    @property
    def mode(self) -> str | None:
        """Return current humidifier operating mode."""
        return self._current_mode

    @property
    def available_modes(self) -> list[str] | None:
        """Return available modes for the humidifier."""
        return [m for m in self._available_modes if m != MODE_OFF]

    @property
    def action(self) -> HumidifierAction | None:
        """Return current humidifier action."""
        if not self._is_on:
            return HumidifierAction.OFF
        if self._pwm_state or (self._pid_output and self._pid_output > 0):
            if self._current_mode == MODE_DEHUMIDITY:
                return HumidifierAction.DRYING
            if self._current_mode == MODE_HUMIDITY:
                return HumidifierAction.HUMIDIFYING
            if self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                return HumidifierAction.DRYING
            if self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                return HumidifierAction.HUMIDIFYING
        return HumidifierAction.IDLE

    @property
    def _time_to_next_switch(self) -> int | None:
        """Remaining seconds until next scheduled switch toggle."""
        if not self._pwm_state or self._cycle_turn_off_time is None:
            return None
        now = dt_util.utcnow()
        if now < self._cycle_turn_off_time:
            return max(0, int((self._cycle_turn_off_time - now).total_seconds()))
        if self._cycle_end_time and now < self._cycle_end_time:
            return max(0, int((self._cycle_end_time - now).total_seconds()))
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic and operational PID attributes."""
        attrs: dict[str, Any] = {
            "control_mode": self._control_mode,
            "thermostat_type": self._thermostat_type,
            "is_heating": self._is_heating,
            "pid_error": round(self._pid_error, 2) if self._pid_error is not None else None,
            "pid_p": round(self._pid_p, 2),
            "pid_i": round(self._pid_i, 2),
            "max_i": self._max_i,
            "pid_d": round(self._pid_d, 2),
            "pid_e": round(self._pid_e, 2),
            "pid_output": round(self._pid_output, 2),
            "indoor_humidity": self._indoor_humidity,
            "outdoor_humidity": self._outdoor_humidity,
            "fail_safe_active": self._fail_safe_active,
            "target_humidity": self._target_humidity,
            "active_section": self._active_section,
            "dispatched_humidity": self._dispatched_humidity,
            "dispatched_fan_mode": self._dispatched_fan_mode,
            "dispatched_action": self._dispatched_action,
            "dispatched_mode": self._dispatched_mode,
            "off_temp_action": self._off_temp_action,
            "sync_slave_state": self._sync_slave_state,
        }

        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
            attrs.update(
                {
                    "pwm_duration": self._pwm_duration,
                    "pwm_duty_cycle": round(self._pwm_duty_cycle, 2),
                    "pwm_on_time": round(self._pwm_on_time, 1),
                    "pwm_state": self._pwm_state,
                    "time_to_next_switch": self._time_to_next_switch,
                }
            )

        # Section attributes
        for sec in range(1, 5):
            p = self._get_section_parameters(sec)
            attrs[f"sec{sec}_apply_temp"] = p[0]
            attrs[f"sec{sec}_delta"] = p[1]
            attrs[f"sec{sec}_apply_action"] = p[2]
            attrs[f"sec{sec}_action"] = p[3]
            attrs[f"sec{sec}_apply_mode"] = p[4]
            attrs[f"sec{sec}_mode"] = p[5]
            # Backwards compatibility attributes
            attrs[f"sec{sec}_apply_delta"] = p[0]
            attrs[f"sec{sec}_apply_hvac_mode"] = p[4]
            attrs[f"sec{sec}_hvac_mode"] = p[5]

        return attrs

    # ---------------------------------------------------------
    # Lifecycle and Event Tracking
    # ---------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        """Restore state and set up event listeners."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = (last_state.state == STATE_ON)
            hum = last_state.attributes.get("humidity") or last_state.attributes.get("target_humidity")
            if hum is not None:
                try:
                    self._target_humidity = float(hum)
                except (ValueError, TypeError):
                    pass
            m = last_state.attributes.get("mode")
            if m:
                self._current_mode = str(m)

            old_i = last_state.attributes.get("pid_i")
            if old_i is not None and self._ki > 0:
                try:
                    self._integral = float(old_i) / self._ki
                except (ValueError, TypeError):
                    pass

        # Track sensor changes
        sensors_to_track = [self._indoor_sensor_id]
        if self._outdoor_sensor_id:
            sensors_to_track.append(self._outdoor_sensor_id)
        if self._target_device_id:
            sensors_to_track.append(self._target_device_id)
        if self._target_device_dehumidity_id:
            sensors_to_track.append(self._target_device_dehumidity_id)
        if self._target_device_humidity_id:
            sensors_to_track.append(self._target_device_humidity_id)

        valid_sensors = [s for s in sensors_to_track if s]
        if valid_sensors:
            self._unsub_sensor_track = async_track_state_change_event(
                self.hass, valid_sensors, self._async_entity_changed
            )

        # Periodic interval tick
        self._unsub_interval_tick = async_track_time_interval(
            self.hass, self._async_interval_tick, timedelta(seconds=self._sample_period)
        )

        # Periodic keep-alive handler for PWM mode
        if self._control_mode == CONTROL_MODE_SWITCH_PWM and self._keep_alive > 0:
            self._keep_alive_cancel = async_track_time_interval(
                self.hass, self._async_keep_alive_handler, timedelta(seconds=self._keep_alive)
            )

        self._update_sensor_values()
        await self._async_calculate_and_control(force=True)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timers and subscriptions."""
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
        """Cancel any running PWM cycle timers."""
        if self._pwm_turn_on_cancel:
            self._pwm_turn_on_cancel()
            self._pwm_turn_on_cancel = None
        if self._pwm_turn_off_cancel:
            self._pwm_turn_off_cancel()
            self._pwm_turn_off_cancel = None
        if self._pwm_cycle_cancel:
            self._pwm_cycle_cancel()
            self._pwm_cycle_cancel = None

    # ---------------------------------------------------------
    # Sensor Readings
    # ---------------------------------------------------------
    def _parse_humidity(self, state_val: str | None, entity_id: str = "") -> float | None:
        """Parse float humidity from sensor state or weather entity."""
        if not state_val or state_val in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        st = self.hass.states.get(entity_id) if entity_id else None
        if st and entity_id.startswith("weather."):
            hum = st.attributes.get("humidity")
            if hum is not None:
                try:
                    return float(hum)
                except (ValueError, TypeError):
                    pass
        try:
            return float(state_val)
        except (ValueError, TypeError):
            return None

    def _update_sensor_values(self) -> None:
        """Read fresh sensor values and update stale count."""
        now = dt_util.utcnow()
        prev_hum = self._indoor_humidity

        if self._indoor_sensor_id:
            st = self.hass.states.get(self._indoor_sensor_id)
            if st and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                val = self._parse_humidity(st.state, self._indoor_sensor_id)
                if val is not None:
                    self._indoor_humidity = val
                    self._last_indoor_update = now
                    self._sensor_stale_count = 0
                else:
                    self._sensor_stale_count += 1
            else:
                self._sensor_stale_count += 1
        else:
            self._sensor_stale_count += 1

        if self._outdoor_sensor_id:
            st = self.hass.states.get(self._outdoor_sensor_id)
            if st and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                val = self._parse_humidity(st.state, self._outdoor_sensor_id)
                if val is not None:
                    self._outdoor_humidity = val
                    self._last_outdoor_update = now
                else:
                    self._outdoor_humidity = None
            else:
                self._outdoor_humidity = None
        else:
            self._outdoor_humidity = None

    async def _async_entity_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle tracked entity state updates."""
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        if new_state is None:
            return

        if entity_id in (self._target_device_id, self._target_device_humidity_id, self._target_device_dehumidity_id):
            if self._sync_slave_state and not self._is_dispatching:
                await self._async_sync_mode_from_slave()
            return

        if entity_id in (self._indoor_sensor_id, self._outdoor_sensor_id):
            old_state = event.data.get("old_state")
            if old_state and old_state.state == new_state.state:
                old_attrs = old_state.attributes
                new_attrs = new_state.attributes
                hum_attr_changed = False
                for attr in ("humidity", "current_humidity", "relative_humidity"):
                    if attr in new_attrs and new_attrs.get(attr) != old_attrs.get(attr):
                        hum_attr_changed = True
                        break
                if not hum_attr_changed:
                    return

            self._update_sensor_values()
            await self._async_calculate_and_control()
            self.async_write_ha_state()

    async def _async_sync_mode_from_slave(self) -> None:
        """Sync internal state when slave humidifier is toggled externally."""
        target_id = self._active_target_device_id()
        slave_state = self.hass.states.get(target_id)
        if not slave_state or slave_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        slave_is_on = (slave_state.state == STATE_ON)
        if self._is_on != slave_is_on:
            _LOGGER.info("External slave state changed: %s -> %s", self._is_on, slave_is_on)
            self._is_on = slave_is_on
            if slave_is_on:
                await self._async_calculate_and_control(force=True)
            else:
                self._cancel_pwm_timers()
                self._reset_pid_state()
            self.async_write_ha_state()

    async def _async_interval_tick(self, now: datetime) -> None:
        """Periodic calculation tick."""
        await self._async_calculate_and_control(now=now)
        self.async_write_ha_state()

    def _reset_pid_state(self) -> None:
        """Reset PID algorithm variables."""
        self._integral = 0.0
        self._prev_error = None
        self._prev_room_humidity = None
        self._pid_error = 0.0
        self._pid_p = 0.0
        self._pid_i = 0.0
        self._pid_d = 0.0
        self._pid_e = 0.0
        self._pid_output = 0.0
        self._pwm_duty_cycle = 0.0
        self._pwm_on_time = 0.0
        self._pwm_state = False

    # ---------------------------------------------------------
    # Humidifier Control Services
    # ---------------------------------------------------------
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the humidifier."""
        self._is_on = True
        if not self._current_mode or self._current_mode == MODE_OFF:
            if self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                self._current_mode = MODE_DEHUMIDITY
            elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                self._current_mode = MODE_HUMIDITY
            else:
                self._current_mode = MODE_DEHUMIDITY
        await self._async_calculate_and_control(force=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the humidifier."""
        self._is_on = False
        await self._async_turn_off_all_devices()
        self._cancel_pwm_timers()
        self._reset_pid_state()
        self.async_write_ha_state()

    async def async_set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        if humidity is None:
            return
        target = max(self._min_humidity, min(self._max_humidity, float(humidity)))
        self._target_humidity = target
        self._update_sensor_values()

        if not self._is_on:
            if self._off_temp_action == OFF_ACTION_TURN_ON:
                self._is_on = True
                if not self._current_mode:
                    self._current_mode = (
                        MODE_DEHUMIDITY
                        if self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER
                        else MODE_HUMIDITY
                    )
                await self._async_calculate_and_control(force=True)
            else:
                # sync_only: recalculate PID internally while remaining off
                await self._async_calculate_and_control(force=True)
        else:
            await self._async_calculate_and_control(force=True)
        self.async_write_ha_state()

    async def async_set_mode(self, mode: str) -> None:
        """Set operating mode (e.g. humidity, dehumidity, silent, auto)."""
        if mode == MODE_OFF:
            await self.async_turn_off()
            return
        self._is_on = True
        self._current_mode = mode

        if self._control_mode == CONTROL_MODE_CLIMATE_DELTA:
            target_id = self._active_target_device_id()
            if target_id:
                try:
                    await self.hass.services.async_call(
                        "humidifier",
                        "set_mode",
                        {ATTR_ENTITY_ID: target_id, "mode": mode},
                        blocking=True,
                    )
                except Exception as err:
                    _LOGGER.debug("Failed to set mode on slave humidifier %s: %s", target_id, err)

        await self._async_calculate_and_control(force=True)
        self.async_write_ha_state()

    # ---------------------------------------------------------
    # PID Section Mapping
    # ---------------------------------------------------------
    def _determine_active_section(self, output: float) -> int:
        """Map PID output percentage to section 1..4 with hysteresis."""
        s1 = self._sec1_max
        s2 = self._sec2_max
        s3 = self._sec3_max
        band = HYSTERESIS_BAND
        current_sec = self._active_section

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
    ) -> tuple[bool, float, bool, str, bool, str]:
        """Return (apply_temp, delta, apply_action, action, apply_mode, mode)."""
        if section == 1:
            return (
                self._sec1_apply_temp,
                self._sec1_delta,
                self._sec1_apply_action,
                self._sec1_action,
                self._sec1_apply_mode,
                self._sec1_mode,
            )
        if section == 2:
            return (
                self._sec2_apply_temp,
                self._sec2_delta,
                self._sec2_apply_action,
                self._sec2_action,
                self._sec2_apply_mode,
                self._sec2_mode,
            )
        if section == 3:
            return (
                self._sec3_apply_temp,
                self._sec3_delta,
                self._sec3_apply_action,
                self._sec3_action,
                self._sec3_apply_mode,
                self._sec3_mode,
            )
        return (
            self._sec4_apply_temp,
            self._sec4_delta,
            self._sec4_apply_action,
            self._sec4_action,
            self._sec4_apply_mode,
            self._sec4_mode,
        )

    # ---------------------------------------------------------
    # Core PID Calculation & Control Loop
    # ---------------------------------------------------------
    async def _async_calculate_and_control(
        self, now: datetime | None = None, force: bool = False
    ) -> None:
        """Run PID calculation, section mapping, and dispatch to slave humidifier or PWM switches."""
        async with self._calc_lock:
            try:
                if now is None:
                    now = dt_util.utcnow()

                self._update_sensor_values()

                if self._last_calc_time is None or force:
                    dt = float(self._sample_period)
                else:
                    raw_dt = (now - self._last_calc_time).total_seconds()
                    dt = max(1.0, min(float(self._sample_period) * 2.0, raw_dt))
                self._last_calc_time = now

                # Fail-Safe check
                if self._indoor_humidity is None or self._sensor_stale_count >= 3:
                    self._fail_safe_active = True
                    _LOGGER.warning(
                        "Fail-safe triggered: Indoor humidity sensor (%s) unavailable or stale",
                        self._indoor_sensor_id,
                    )
                    final_output = (
                        self._output_safety
                        if self._control_mode == CONTROL_MODE_SWITCH_PWM
                        else FAIL_SAFE_OUTPUT
                    )
                    self._pid_error = 0.0
                    self._pid_p = 0.0
                    self._pid_i = 0.0
                    self._pid_d = 0.0
                    self._pid_e = 0.0
                    self._pid_output = round(final_output, 2)
                    is_heating = (self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER)
                else:
                    self._fail_safe_active = False

                    # Determine heating (humidifying) vs cooling (dehumidifying)
                    if self._thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                        is_heating = False
                    elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                        is_heating = True
                    elif self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                        if self._current_mode == MODE_AUTO:
                            is_heating = (self._indoor_humidity < self._target_humidity)
                        elif self._current_mode == MODE_HUMIDITY:
                            is_heating = True
                        else:
                            is_heating = False
                    else:
                        is_heating = False

                    if self._is_heating != is_heating:
                        _LOGGER.info(
                            "Humidifier direction switched: %s -> %s. Resetting integral.",
                            "HUMIDIFYING" if self._is_heating else "DEHUMIDIFYING",
                            "HUMIDIFYING" if is_heating else "DEHUMIDIFYING",
                        )
                        self._integral = 0.0
                        self._prev_error = None
                        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
                            self._cancel_pwm_timers()
                            self._pwm_state = False
                    self._is_heating = is_heating

                    if is_heating:
                        # Humidifier demand: target - current
                        error = self._target_humidity - self._indoor_humidity
                        e_term = (
                            self._ke * (self._target_humidity - self._outdoor_humidity)
                            if self._outdoor_sensor_id and self._outdoor_humidity is not None
                            else 0.0
                        )
                    else:
                        # Dehumidifier demand: current - target
                        error = self._indoor_humidity - self._target_humidity
                        e_term = (
                            self._ke * (self._outdoor_humidity - self._target_humidity)
                            if self._outdoor_sensor_id and self._outdoor_humidity is not None
                            else 0.0
                        )

                    p_term = self._kp * error

                    dt_norm = (
                        (dt / float(self._sample_period))
                        if self._sample_period > 0
                        else 1.0
                    )
                    if dt_norm < 0.05:
                        dt_norm = 0.05

                    # D term on measurement to prevent derivative kick on target changes
                    if getattr(self, "_prev_room_humidity", None) is not None:
                        diff_humidity = self._indoor_humidity - self._prev_room_humidity
                        if is_heating:
                            # Humidifier: demand increases as humidity drops
                            d_term = -self._kd * (diff_humidity / dt_norm)
                        else:
                            # Dehumidifier: demand increases as humidity rises
                            d_term = self._kd * (diff_humidity / dt_norm)
                    else:
                        d_term = 0.0

                    self._prev_error = error
                    self._prev_room_humidity = self._indoor_humidity

                    # Integral calculation with anti-windup and bidirectional Max I limit [-max_i, +max_i]
                    max_i_limit = max(0.0, float(self._max_i))
                    tentative_output = p_term + (self._ki * self._integral) + d_term + e_term
                    is_saturated_high = (tentative_output >= 100.0)
                    is_saturated_low = (tentative_output <= 0.0)

                    if not ((is_saturated_high and error > 0) or (is_saturated_low and error < 0)):
                        self._integral += error * dt_norm

                    raw_i_term = self._ki * self._integral
                    i_term = max(-max_i_limit, min(max_i_limit, raw_i_term))
                    if self._ki > 0:
                        self._integral = i_term / self._ki
                    else:
                        self._integral = 0.0
                        i_term = 0.0
                    raw_output = p_term + i_term + d_term + e_term
                    final_output = max(0.0, min(100.0, raw_output))

                    self._pid_error = round(error, 2)
                    self._pid_p = round(p_term, 2)
                    self._pid_i = round(i_term, 2)
                    self._pid_d = round(d_term, 2)
                    self._pid_e = round(e_term, 2)
                    self._pid_output = round(final_output, 2)

                # Map section
                new_section = self._determine_active_section(self._pid_output)
                self._active_section = new_section

                (
                    apply_temp,
                    delta,
                    apply_action,
                    action,
                    apply_mode,
                    mode,
                ) = self._get_section_parameters(new_section)

                # Target humidity with delta applied
                if apply_temp:
                    adj_humidity = self._target_humidity + delta
                else:
                    adj_humidity = self._target_humidity

                # Clamp adjusted target humidity between min and max
                adj_humidity = max(self._min_humidity, min(self._max_humidity, adj_humidity))
                int_humidity = int(round(adj_humidity))

                self._raw_target_humidity = self._target_humidity
                self._dispatched_humidity = int_humidity
                self._dispatched_action = action if apply_action else None
                self._dispatched_mode = mode if apply_mode else None

                # When OFF: ensure all switches or slave devices are off
                if not self._is_on:
                    await self._async_turn_off_all_devices()
                    return

                # When ON: dispatch control
                await self._async_dispatch_control(
                    section=new_section,
                    target_humidity=int_humidity,
                    apply_temp=apply_temp,
                    apply_action=apply_action,
                    action=action,
                    apply_mode=apply_mode,
                    mode=mode,
                    force=force,
                )

            except Exception as ex:
                _LOGGER.exception("Unexpected error in humidifier calculate_and_control: %s", ex)

    async def _async_dispatch_control(
        self,
        section: int,
        target_humidity: int,
        apply_temp: bool,
        apply_action: bool,
        action: str,
        apply_mode: bool,
        mode: str,
        force: bool = False,
    ) -> None:
        """Dispatch control signals to target humidifier or PWM switches."""
        section_changed = (self._last_dispatched_section != section)
        has_sent_command = False

        async def _delay_before_command() -> None:
            nonlocal has_sent_command
            if not has_sent_command:
                if section_changed:
                    _LOGGER.debug(
                        "Humidifier section changed (%s -> %s) -> waiting 1.0s before first command",
                        self._last_dispatched_section,
                        section,
                    )
                    await asyncio.sleep(1.0)
                has_sent_command = True
            else:
                _LOGGER.debug("Humidifier multiple commands -> waiting 0.5s between commands")
                await asyncio.sleep(0.5)

        self._is_dispatching = True
        try:
            if self._control_mode == CONTROL_MODE_CLIMATE_DELTA:
                # Slave Humidifier Mode
                target_id = self._active_target_device_id()
                if not target_id:
                    return

                slave_state = self.hass.states.get(target_id)
                current_slave_is_on = (slave_state.state == STATE_ON) if slave_state else False

                should_turn_off = (apply_action and action == "off")

                if should_turn_off:
                    if current_slave_is_on or force or section_changed:
                        _LOGGER.info(
                            "Turning off slave humidifier %s (Section %d action: off)",
                            target_id,
                            section,
                        )
                        try:
                            await _delay_before_command()
                            await self.hass.services.async_call(
                                "humidifier",
                                "turn_off",
                                {ATTR_ENTITY_ID: target_id},
                                blocking=True,
                            )
                        except Exception as err:
                            _LOGGER.warning("Failed to turn off slave humidifier %s: %s", target_id, err)
                    self._last_dispatched_section = section
                    return

                # Ensure slave humidifier is ON
                if not current_slave_is_on:
                    _LOGGER.info("Turning on slave humidifier %s", target_id)
                    try:
                        await _delay_before_command()
                        await self.hass.services.async_call(
                            "humidifier",
                            "turn_on",
                            {ATTR_ENTITY_ID: target_id},
                            blocking=True,
                        )
                    except Exception as err:
                        _LOGGER.warning("Failed to turn on slave humidifier %s: %s", target_id, err)

                # Set Mode (before humidity to ensure preset mode doesn't override target humidity)
                if apply_mode and mode:
                    current_slave_mode = (
                        slave_state.attributes.get("mode") if slave_state else None
                    )
                    is_same_mode = (
                        current_slave_mode is not None
                        and str(current_slave_mode).lower().strip() == str(mode).lower().strip()
                    )
                    is_same_last_mode = (
                        self._last_dispatched_mode is not None
                        and str(self._last_dispatched_mode).lower().strip() == str(mode).lower().strip()
                    )
                    if force or section_changed or not (is_same_mode and is_same_last_mode):
                        try:
                            await _delay_before_command()
                            await self.hass.services.async_call(
                                "humidifier",
                                "set_mode",
                                {ATTR_ENTITY_ID: target_id, "mode": mode},
                                blocking=True,
                            )
                            self._last_dispatched_mode = mode
                        except Exception as err:
                            _LOGGER.debug("Slave humidifier does not support set_mode: %s", err)

                # Set Target Humidity
                if apply_temp:
                    current_slave_humidity = (
                        slave_state.attributes.get("humidity") if slave_state else None
                    )
                    is_same_as_slave = (current_slave_humidity == target_humidity)
                    is_same_as_last = (self._last_dispatched_humidity == target_humidity)
                    if force or section_changed or not (is_same_as_slave and is_same_as_last):
                        _LOGGER.info(
                            "Setting slave humidifier %s target humidity to %d%% (PID output: %.1f%%, Section %d)",
                            target_id,
                            target_humidity,
                            self._pid_output,
                            section,
                        )
                        try:
                            await _delay_before_command()
                            await self.hass.services.async_call(
                                "humidifier",
                                "set_humidity",
                                {ATTR_ENTITY_ID: target_id, "humidity": target_humidity},
                                blocking=True,
                            )
                            self._last_dispatched_humidity = target_humidity
                        except Exception as err:
                            _LOGGER.warning("Failed to set slave humidifier %s target humidity: %s", target_id, err)

                self._last_dispatched_section = section

            elif self._control_mode == CONTROL_MODE_SWITCH_PWM:
                # Switch PWM Mode
                should_turn_off = (apply_action and action == "off")
                if should_turn_off:
                    if section_changed:
                        await asyncio.sleep(1.0)
                    await self._async_turn_off_all_devices()
                    self._last_dispatched_section = section
                    return
                await self._async_handle_pwm_control(force=force)
                self._last_dispatched_section = section

        finally:
            self._is_dispatching = False

    # ---------------------------------------------------------
    # Switch PWM Mode Logic
    # ---------------------------------------------------------
    async def _async_handle_pwm_control(self, force: bool = False) -> None:
        """Handle PWM cycle start and timing calculation."""
        duty_cycle = self._pid_output / 100.0
        self._pwm_duty_cycle = duty_cycle
        on_time = duty_cycle * float(self._pwm_duration)
        self._pwm_on_time = on_time

        now = dt_util.utcnow()

        if self._pwm_cycle_cancel is None or force:
            await self._async_start_pwm_cycle(now)

    async def _async_start_pwm_cycle(self, now: datetime) -> None:
        """Start a new PWM time-proportioning cycle."""
        self._cancel_pwm_timers()

        self._cycle_start_time = now
        self._cycle_end_time = now + timedelta(seconds=self._pwm_duration)

        duty_cycle = self._pid_output / 100.0
        on_time = duty_cycle * float(self._pwm_duration)
        off_time = float(self._pwm_duration) - on_time

        active_dev = self._active_target_device_id()

        # Dual controller inactive device cleanup
        if self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
            inactive_dev = (
                self._target_device_dehumidity_id
                if self._is_heating
                else self._target_device_humidity_id
            )
            if inactive_dev and inactive_dev != active_dev:
                await self._async_turn_off_specific_device(inactive_dev)

        # Full ON
        if duty_cycle >= 1.0:
            self._pwm_state = True
            await self._async_turn_on_switch(active_dev)
            self._schedule_next_cycle(self._cycle_end_time)
            return

        # Full OFF
        if duty_cycle <= 0.0 or on_time < float(self._min_on_time):
            self._pwm_state = False
            await self._async_turn_off_switch(active_dev)
            self._schedule_next_cycle(self._cycle_end_time)
            return

        # Anti-short-cycling check
        turn_off_time = now + timedelta(seconds=on_time)
        self._cycle_turn_off_time = turn_off_time

        self._pwm_state = True
        await self._async_turn_on_switch(active_dev)

        # Schedule turn off
        self._pwm_turn_off_cancel = async_track_point_in_time(
            self.hass, self._async_pwm_turn_off_callback, turn_off_time
        )
        self._schedule_next_cycle(self._cycle_end_time)

    def _schedule_next_cycle(self, cycle_end_time: datetime) -> None:
        """Schedule next PWM cycle."""
        self._pwm_cycle_cancel = async_track_point_in_time(
            self.hass, self._async_pwm_cycle_callback, cycle_end_time
        )

    async def _async_pwm_turn_off_callback(self, now: datetime) -> None:
        """Callback to turn off switch during PWM cycle."""
        active_dev = self._active_target_device_id()
        self._pwm_state = False
        await self._async_turn_off_switch(active_dev)
        self.async_write_ha_state()

    async def _async_pwm_cycle_callback(self, now: datetime) -> None:
        """Callback for start of next PWM cycle."""
        await self._async_calculate_and_control(now=now, force=True)
        self.async_write_ha_state()

    async def _async_turn_on_switch(self, device_id: str) -> None:
        """Turn on switch/input_boolean if currently off."""
        if not device_id:
            return
        st = self.hass.states.get(device_id)
        if st and st.state != STATE_ON:
            domain = device_id.split(".")[0]
            _LOGGER.info("Turning ON switch %s", device_id)
            await self.hass.services.async_call(
                domain,
                "turn_on",
                {ATTR_ENTITY_ID: device_id},
                blocking=True,
            )
            self._last_switch_on_time = dt_util.utcnow()
            self._last_switch_on_times[device_id] = self._last_switch_on_time

    async def _async_turn_off_switch(self, device_id: str) -> None:
        """Turn off switch/input_boolean if currently on."""
        if not device_id:
            return
        st = self.hass.states.get(device_id)
        if st and st.state != STATE_OFF:
            domain = device_id.split(".")[0]
            _LOGGER.info("Turning OFF switch %s", device_id)
            await self.hass.services.async_call(
                domain,
                "turn_off",
                {ATTR_ENTITY_ID: device_id},
                blocking=True,
            )
            self._last_switch_off_time = dt_util.utcnow()
            self._last_switch_off_times[device_id] = self._last_switch_off_time

    async def _async_turn_off_specific_device(self, device_id: str) -> None:
        """Turn off a specific target device regardless of domain."""
        if not device_id:
            return
        domain = device_id.split(".")[0]
        st = self.hass.states.get(device_id)
        if st and st.state != STATE_OFF:
            try:
                await self.hass.services.async_call(
                    domain,
                    "turn_off",
                    {ATTR_ENTITY_ID: device_id},
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.warning("Failed to turn off %s: %s", device_id, err)

    async def _async_turn_off_all_devices(self) -> None:
        """Turn off all associated slave/switch target devices."""
        devices = [
            self._target_device_id,
            self._target_device_humidity_id,
            self._target_device_dehumidity_id,
        ]
        first_turned_off = False
        for dev in devices:
            if dev:
                st = self.hass.states.get(dev)
                if st and st.state != STATE_OFF:
                    if first_turned_off:
                        await asyncio.sleep(0.5)
                    await self._async_turn_off_specific_device(dev)
                    first_turned_off = True

    async def _async_keep_alive_handler(self, now: datetime) -> None:
        """Periodic keep-alive signal to ensure switch state matches internal PWM state."""
        if not self._is_on:
            return
        active_dev = self._active_target_device_id()
        if not active_dev:
            return
        if self._pwm_state:
            await self._async_turn_on_switch(active_dev)
        else:
            await self._async_turn_off_switch(active_dev)
