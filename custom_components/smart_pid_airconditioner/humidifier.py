"""Humidifier platform for Smart PID Air Conditioner."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
    EVENT_HOMEASSISTANT_STOP,
    Platform,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
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
    CONF_DYNAMIC_MIN_OFF_MULTIPLIER,
    DEFAULT_DYNAMIC_MIN_OFF_MULTIPLIER,
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
    DEFAULT_SENSOR_STALE_TIMEOUT_SEC,
    DEFAULT_SYNC_SLAVE_STATE,
    DEFAULT_TAU_D,
    DEFAULT_THERMOSTAT_TYPE,
    FAIL_SAFE_MULTIPLIER,
    FAIL_SAFE_OUTPUT,
    HYSTERESIS_BAND,
    MIN_DERIVATIVE_EVENT_DT,
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
    CONF_MANUAL_OVERRIDE_MIN,
    CONF_MAX_RUN_MIN,
    CONF_SAFETY_REST_MIN,
    DEFAULT_MANUAL_OVERRIDE_MIN,
    DEFAULT_MAX_RUN_MIN,
    DEFAULT_SAFETY_REST_MIN,
    DUAL_MIN_DEADBAND_HUMIDITY,
    is_humidity_type,
    is_dual_controller,
    duration_to_seconds,
    resolve_thermostat_type,
)
from .actuator import SwitchCommandGuard
from .config_utils import (
    SmartPIDExtraStoredData,
    async_subscribe_state_reported,
    build_device_info,
    build_ventilation_guard,
    get_entry_setting,
    get_state_report_time,
    is_state_unavailable,
    parse_humidity_state,
    run_async_on_loop,
)
from .pid_core import (
    compute_effective_min_off,
    compute_pid_step,
    determine_active_section,
    evaluate_sensor_recovery,
)
from .ventilation import VentState, VentStatus, VentilationGuard

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart PID Humidifier entity from a config entry."""
    thermostat_type = resolve_thermostat_type(entry)

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
        self._attr_name = None
        self._attr_device_info = build_device_info(entry)

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
        self._last_d_calc_time: datetime | None = None
        self._prev_room_humidity: float | None = None
        self._prev_error: float | None = None
        self._filtered_d: float = 0.0
        self._integral: float = 0.0
        self._pid_error: float | None = None
        self._pid_p: float = 0.0
        self._pid_i: float = 0.0
        self._pid_d: float = 0.0
        self._pid_e: float = 0.0
        self._pid_output: float = 0.0
        self._active_section: int = 1
        self._vent_status: VentStatus = VentStatus(VentState.NORMAL, False, False, 0, False)
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
        self._sensor_fault_active: bool = False
        self._sensor_recovery_first_at: datetime | None = None

        # Event tracking unsubscribers
        self._unsub_sensor_track: CALLBACK_TYPE | None = None
        self._unsub_state_reported: CALLBACK_TYPE | None = None
        self._unsub_interval_tick: CALLBACK_TYPE | None = None

        # Guard flags & Stage 4/5/11 runtime, manual override, watchdog, reload min-off
        self._runtime: Any = getattr(entry, "runtime_data", None)
        self._switch_guard = SwitchCommandGuard(hass)
        self._device_unavailable: bool = False
        self._unsub_runtime: CALLBACK_TYPE | None = None
        self._manual_override_until: datetime | None = None
        self._continuous_run_start: datetime | None = None
        self._watchdog_rest_until: datetime | None = None
        self._is_dispatching: bool = False
        self._reload_min_off_deadlines: dict[str, datetime] = {}
        self._sensor_stale_timeout_sec: float = max(
            float(DEFAULT_SENSOR_STALE_TIMEOUT_SEC),
            float(self._sample_period) * FAIL_SAFE_MULTIPLIER,
        )
        self._shutting_down: bool = False
        self._shutdown_lock = asyncio.Lock()
        self._shutdown_done: bool = False
        self._unsub_stop_event: CALLBACK_TYPE | None = None
        self._unverified_off_devices: dict[str, float] = {}
        self._pwm_cycle_id: int = 0
        self._short_pwm_warned: bool = False

    def _run_async(self, coro) -> None:
        """Execute a coroutine safely on the Home Assistant event loop."""
        run_async_on_loop(self.hass, coro)

    def _get_setting(self, key: str, default: Any) -> Any:
        """Helper to get value from options first, then entry data, then default."""
        return get_entry_setting(self._entry, key, default)

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
        self._dynamic_min_off_multiplier: float = max(
            0.0,
            min(
                3.0,
                float(
                    self._get_setting(
                        CONF_DYNAMIC_MIN_OFF_MULTIPLIER,
                        DEFAULT_DYNAMIC_MIN_OFF_MULTIPLIER,
                    )
                ),
            ),
        )
        self._last_run_duration_sec: float = 0.0
        self._keep_alive: int = duration_to_seconds(
            self._get_setting(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE), DEFAULT_KEEP_ALIVE
        )
        self._output_safety: float = float(
            self._get_setting(CONF_OUTPUT_SAFETY, DEFAULT_OUTPUT_SAFETY)
        )

        # Section configurations (1..4)
        _sec_defaults = (
            (1, DEFAULT_SEC1_MAX, DEFAULT_SEC1_DELTA, DEFAULT_SEC1_FAN),
            (2, DEFAULT_SEC2_MAX, DEFAULT_SEC2_DELTA, DEFAULT_SEC2_FAN),
            (3, DEFAULT_SEC3_MAX, DEFAULT_SEC3_DELTA, DEFAULT_SEC3_FAN),
            (4, None, DEFAULT_SEC4_DELTA, DEFAULT_SEC4_FAN),
        )
        for idx, def_max, def_delta, def_fan in _sec_defaults:
            if def_max is not None:
                setattr(self, f"_sec{idx}_max", float(self._get_setting(f"sec{idx}_max", def_max)))
            setattr(self, f"_sec{idx}_apply_temp", bool(self._get_setting(f"sec{idx}_apply_temp", DEFAULT_SEC_APPLY_TEMP)))
            setattr(self, f"_sec{idx}_apply_delta", bool(self._get_setting(f"sec{idx}_apply_delta", DEFAULT_SEC_APPLY_DELTA)))
            setattr(self, f"_sec{idx}_delta", float(self._get_setting(f"sec{idx}_delta", def_delta)))
            setattr(self, f"_sec{idx}_apply_fan", bool(self._get_setting(f"sec{idx}_apply_fan", DEFAULT_SEC_APPLY_FAN)))
            setattr(self, f"_sec{idx}_fan", self._get_setting(f"sec{idx}_fan", def_fan))
            setattr(self, f"_sec{idx}_apply_action", bool(self._get_setting(f"sec{idx}_apply_action", DEFAULT_SEC_APPLY_ACTION)))
            setattr(
                self,
                f"_sec{idx}_action",
                str(
                    self._get_setting(
                        f"sec{idx}_action",
                        "off" if self._get_setting(f"sec{idx}_hvac_mode", None) == "off" else DEFAULT_SEC_ACTION,
                    )
                ),
            )
            apply_mode = bool(
                self._get_setting(
                    f"sec{idx}_apply_mode",
                    self._get_setting(f"sec{idx}_apply_hvac_mode", DEFAULT_SEC_APPLY_MODE),
                )
            )
            mode_val = str(
                self._get_setting(
                    f"sec{idx}_mode",
                    self._get_setting(f"sec{idx}_hvac_mode", MODE_DEHUMIDITY),
                )
            )
            setattr(self, f"_sec{idx}_apply_mode", apply_mode)
            setattr(self, f"_sec{idx}_mode", mode_val)
            setattr(self, f"_sec{idx}_apply_hvac_mode", apply_mode)
            setattr(self, f"_sec{idx}_hvac_mode", mode_val)

        self._vent_guard = build_ventilation_guard(self._get_setting)
        self._manual_override_min: int = int(
            float(self._get_setting(CONF_MANUAL_OVERRIDE_MIN, DEFAULT_MANUAL_OVERRIDE_MIN))
        )
        self._max_run_min: int = int(
            float(self._get_setting(CONF_MAX_RUN_MIN, DEFAULT_MAX_RUN_MIN))
        )
        self._safety_rest_min: int = int(
            float(self._get_setting(CONF_SAFETY_REST_MIN, DEFAULT_SAFETY_REST_MIN))
        )

    def _active_target_device_id(self) -> str:
        """Return the active target switch/slave device ID based on mode and configuration."""
        if self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
            if self._current_mode == MODE_HUMIDITY or (
                self._current_mode == MODE_AUTO and getattr(self, "_is_heating", False)
            ):
                return self._target_device_humidity_id or self._target_device_id
            return self._target_device_dehumidity_id or self._target_device_id
        return self._target_device_id

    def _inactive_target_device_id(self, active_dev: str | None = None) -> str | None:
        """Return the opposing inactive device entity ID in dual humidity/dehumidity mode."""
        if self._thermostat_type != THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
            return None
        cur_active = active_dev if active_dev is not None else self._active_target_device_id()
        if self._current_mode == MODE_HUMIDITY or (
            self._current_mode == MODE_AUTO and getattr(self, "_is_heating", False)
        ):
            inactive = self._target_device_dehumidity_id
        else:
            inactive = self._target_device_humidity_id
        if inactive and inactive != cur_active:
            return inactive
        return None

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
            "ventilation_state": self._vent_status.state.value,
            "ventilation_cooldown_remaining_sec": self._vent_status.cooldown_remaining_sec,
            "learning_blocked": self._vent_status.should_block_learning,
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
    @property
    def extra_restore_state_data(self) -> SmartPIDExtraStoredData:
        """Return entity specific state data to be restored."""
        return SmartPIDExtraStoredData(
            last_switch_on_times={
                k: v.isoformat() for k, v in self._last_switch_on_times.items()
            },
            last_switch_off_times={
                k: v.isoformat() for k, v in self._last_switch_off_times.items()
            },
            active_section=self._active_section,
            integral=self._integral,
            last_run_duration_sec=self._last_run_duration_sec,
        )

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

        raw_extra = await self.async_get_last_extra_data()
        extra_data: SmartPIDExtraStoredData | None = None
        if isinstance(raw_extra, SmartPIDExtraStoredData):
            extra_data = raw_extra
        elif raw_extra is not None and hasattr(raw_extra, "as_dict"):
            extra_data = SmartPIDExtraStoredData.from_dict(raw_extra.as_dict())
        elif isinstance(raw_extra, Mapping):
            extra_data = SmartPIDExtraStoredData.from_dict(raw_extra)

        if extra_data is not None:
            for dev_id, ts_str in extra_data.last_switch_on_times.items():
                parsed_dt = dt_util.parse_datetime(ts_str)
                if parsed_dt is not None:
                    self._last_switch_on_times[dev_id] = parsed_dt
            for dev_id, ts_str in extra_data.last_switch_off_times.items():
                parsed_dt = dt_util.parse_datetime(ts_str)
                if parsed_dt is not None:
                    self._last_switch_off_times[dev_id] = parsed_dt
            if self._last_switch_on_times:
                self._last_switch_on_time = max(self._last_switch_on_times.values())
            if self._last_switch_off_times:
                self._last_switch_off_time = max(self._last_switch_off_times.values())
            if extra_data.active_section in (1, 2, 3, 4):
                self._active_section = extra_data.active_section
            if self._is_on:
                self._integral = float(extra_data.integral)
            self._last_run_duration_sec = max(0.0, float(extra_data.last_run_duration_sec))

        self._restore_reload_off()

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
        sensors_to_track.extend(self._vent_guard.tracked_entities)

        valid_sensors = [s for s in dict.fromkeys(sensors_to_track) if s]
        if valid_sensors:
            self._unsub_sensor_track = async_track_state_change_event(
                self.hass, valid_sensors, self._async_entity_changed
            )

        self._unsub_state_reported = async_subscribe_state_reported(
            self.hass,
            (self._indoor_sensor_id, self._outdoor_sensor_id),
            self._async_state_reported,
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

        if self._runtime is not None:
            self._unsub_runtime = self._runtime.subscribe(self.async_write_ha_state)

        bus = getattr(self.hass, "bus", None)
        if bus is not None:
            listen_once = getattr(bus, "async_listen_once", None)
            listen = getattr(bus, "async_listen", None)
            if callable(listen_once):
                self._unsub_stop_event = listen_once(
                    EVENT_HOMEASSISTANT_STOP, self._async_handle_hass_stop
                )
            elif callable(listen):
                self._unsub_stop_event = listen(
                    EVENT_HOMEASSISTANT_STOP, self._async_handle_hass_stop
                )

        self._update_sensor_values()
        await self._async_calculate_and_control(force=True)

    @callback
    def _prepare_shutdown(self) -> None:
        """Synchronously block new ON cycles and release timers/subscriptions on shutdown."""
        self._shutting_down = True
        self._pwm_cycle_id += 1
        if self._unsub_stop_event is not None:
            self._unsub_stop_event()
            self._unsub_stop_event = None
        if self._unsub_runtime is not None:
            self._unsub_runtime()
            self._unsub_runtime = None
        if self._unsub_sensor_track:
            self._unsub_sensor_track()
            self._unsub_sensor_track = None
        if self._unsub_state_reported:
            self._unsub_state_reported()
            self._unsub_state_reported = None
        if self._unsub_interval_tick:
            self._unsub_interval_tick()
            self._unsub_interval_tick = None
        if self._keep_alive_cancel:
            self._keep_alive_cancel()
            self._keep_alive_cancel = None
        self._cancel_pwm_timers()

    @callback
    def _async_handle_hass_stop(self, _event: Event) -> None:
        """Handle Home Assistant shutdown by blocking new ONs and stopping active PWM switches."""
        self._prepare_shutdown()
        if self.hass is None:
            return
        create_task = getattr(self.hass, "async_create_task", None)
        if callable(create_task):
            create_task(self._async_shutdown_pwm(reason="hass_stop"))
        else:
            self._run_async(self._async_shutdown_pwm(reason="hass_stop"))

    async def _async_shutdown_pwm(self, *, reason: str) -> dict[str, tuple[datetime, float]]:
        """Idempotent shutdown routine shared between HA stop and entity removal."""
        self._prepare_shutdown()
        async with self._shutdown_lock:
            if self._shutdown_done and not self._unverified_off_devices:
                return {}
            shutdown_records, unverified_devices = await self._async_safe_shutdown_pwm_on_remove(
                reason=reason
            )
            self._record_reload_off(
                shutdown_records=shutdown_records,
                unverified_devices=unverified_devices,
            )
            self._shutdown_done = True
            return shutdown_records

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timers and subscriptions."""
        await self._async_shutdown_pwm(reason="remove")
        parent_remove = getattr(super(), "async_will_remove_from_hass", None)
        if callable(parent_remove):
            await parent_remove()

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
    def _parse_humidity(self, state_val: Any, entity_id: str = "") -> float | None:
        """Parse finite float humidity from sensor state or weather entity."""
        if hasattr(state_val, "state") or hasattr(state_val, "attributes"):
            return parse_humidity_state(state_val)
        if not state_val or state_val in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        st = self.hass.states.get(entity_id) if (entity_id and self.hass is not None) else None
        if st and entity_id.startswith("weather."):
            parsed_weather = parse_humidity_state(st)
            if parsed_weather is not None:
                return parsed_weather
        return parse_humidity_state(
            state_val
            if isinstance(state_val, (int, float, bool))
            else type("_HumidityWrap", (), {"state": state_val, "attributes": {}})()
        )

    def _update_sensor_values(self, now: datetime | None = None) -> None:
        """Read fresh sensor values and update stale count."""
        if now is None:
            now = dt_util.utcnow()

        val: float | None = None
        st = self.hass.states.get(self._indoor_sensor_id) if self._indoor_sensor_id else None
        if st and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            val = self._parse_humidity(st, self._indoor_sensor_id)

        if val is not None:
            state_ts = get_state_report_time(st)
            prev_report_at = self._last_indoor_update
            report_at = prev_report_at
            if state_ts is not None and (report_at is None or state_ts > report_at):
                report_at = state_ts
            if report_at is None:
                report_at = now

            report_age = (now - report_at).total_seconds()
            if report_age > self._sensor_stale_timeout_sec:
                if not self._sensor_fault_active:
                    _LOGGER.warning(
                        "Indoor humidity sensor %s report expired (%.0fs > %.0fs); activating fail-safe",
                        self._indoor_sensor_id,
                        report_age,
                        self._sensor_stale_timeout_sec,
                    )
                self._sensor_fault_active = True
                self._sensor_recovery_first_at = None
                self._sensor_stale_count += 1
                self._indoor_humidity = None
            else:
                was_fault = self._sensor_fault_active
                accept, self._sensor_fault_active, self._sensor_recovery_first_at = (
                    evaluate_sensor_recovery(
                        parsed=val,
                        fault_active=self._sensor_fault_active,
                        recovery_first_at=self._sensor_recovery_first_at,
                        now=now,
                        report_at=report_at,
                    )
                )
                self._last_indoor_update = report_at
                if accept:
                    self._indoor_humidity = val
                    is_new_report = prev_report_at is None or report_at > prev_report_at
                    if is_new_report or was_fault:
                        self._sensor_stale_count = 0
                else:
                    self._indoor_humidity = None
        else:
            _, self._sensor_fault_active, self._sensor_recovery_first_at = (
                evaluate_sensor_recovery(
                    parsed=None,
                    fault_active=self._sensor_fault_active,
                    recovery_first_at=self._sensor_recovery_first_at,
                    now=now,
                )
            )
            self._indoor_humidity = None
            self._sensor_stale_count += 1

        if self._outdoor_sensor_id:
            st = self.hass.states.get(self._outdoor_sensor_id)
            if st and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                oval = self._parse_humidity(st, self._outdoor_sensor_id)
                if oval is not None:
                    out_ts = get_state_report_time(st)
                    out_at = self._last_outdoor_update
                    if out_ts is not None and (out_at is None or out_ts > out_at):
                        out_at = out_ts
                    if out_at is None:
                        out_at = now
                    if (now - out_at).total_seconds() > self._sensor_stale_timeout_sec:
                        self._outdoor_humidity = None
                    else:
                        self._outdoor_humidity = oval
                        self._last_outdoor_update = out_at
                else:
                    self._outdoor_humidity = None
            else:
                self._outdoor_humidity = None
        else:
            self._outdoor_humidity = None

    def _is_target_device_unavailable(self) -> bool:
        """Return True if the active target device state is currently unavailable/unknown."""
        unavail = is_state_unavailable(self.hass, self._active_target_device_id())
        if unavail is not None:
            self._device_unavailable = unavail
        return self._device_unavailable

    @callback
    def _async_state_reported(self, event: Event) -> None:
        """Refresh sensor timestamps and reset stale counter on state_reported heartbeats."""
        if self.hass is None or self._shutting_down:
            return
        data = event.data if isinstance(event.data, Mapping) else {}
        entity_id = data.get(ATTR_ENTITY_ID)
        if not entity_id or entity_id not in (self._indoor_sensor_id, self._outdoor_sensor_id):
            return

        new_state = data.get("new_state") or self.hass.states.get(entity_id)
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        parsed = self._parse_humidity(new_state, entity_id)
        if parsed is None:
            return

        now = dt_util.utcnow()
        if entity_id == self._indoor_sensor_id:
            if self._sensor_fault_active:
                accept, self._sensor_fault_active, self._sensor_recovery_first_at = (
                    evaluate_sensor_recovery(
                        parsed=parsed,
                        fault_active=True,
                        recovery_first_at=self._sensor_recovery_first_at,
                        now=now,
                        report_at=now,
                    )
                )
                self._last_indoor_update = now
                if accept:
                    self._indoor_humidity = parsed
                    self._sensor_stale_count = 0
            else:
                self._last_indoor_update = now
                self._sensor_stale_count = 0
                self._indoor_humidity = parsed
        elif entity_id == self._outdoor_sensor_id:
            self._last_outdoor_update = now
            self._outdoor_humidity = parsed

    async def _async_entity_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle tracked entity state updates."""
        if self.hass is None or self._shutting_down:
            return
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        if new_state is None:
            return

        if entity_id in self._vent_guard.tracked_entities:
            self._run_async(self._async_calculate_and_control())
            return

        if entity_id in (self._target_device_id, self._target_device_humidity_id, self._target_device_dehumidity_id):
            is_unavail = new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            if entity_id == self._active_target_device_id():
                self._device_unavailable = is_unavail
            if is_unavail:
                return
            if (
                self._control_mode == CONTROL_MODE_SWITCH_PWM
                and entity_id == self._active_target_device_id()
            ):
                self._pwm_state = new_state.state.lower() == STATE_ON
                self.async_write_ha_state()
            if (
                self._control_mode == CONTROL_MODE_SWITCH_PWM
                and new_state.state.lower() == STATE_OFF
                and entity_id in self._unverified_off_devices
            ):
                prior_run_dur = self._unverified_off_devices.get(
                    entity_id, self._last_run_duration_sec
                )
                off_at = dt_util.utcnow()
                self._record_confirmed_off(
                    entity_id, off_at, run_dur_override=prior_run_dur
                )
                eff = self._compute_effective_min_off(prior_run_dur)
                if eff > 0:
                    deadline = off_at + timedelta(seconds=eff)
                    prev_dl = self._reload_min_off_deadlines.get(entity_id)
                    if prev_dl is None or deadline > prev_dl:
                        self._reload_min_off_deadlines[entity_id] = deadline
            if (
                self._control_mode == CONTROL_MODE_SWITCH_PWM
                and self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY
                and self._is_on
                and entity_id == self._inactive_target_device_id()
                and new_state.state.lower() == STATE_ON
            ):
                _LOGGER.warning(
                    "Inactive PWM device %s turned ON unexpectedly while active! Forcing it OFF.",
                    entity_id,
                )
                await self._async_turn_off_specific_device(entity_id)
                return
            if (
                not self._is_dispatching
                and self._manual_override_min > 0
                and event.context is not None
                and event.context.user_id is not None
            ):
                self._manual_override_until = dt_util.utcnow() + timedelta(
                    minutes=self._manual_override_min
                )
            if (
                self._control_mode != CONTROL_MODE_SWITCH_PWM
                and self._sync_slave_state
                and not self._is_dispatching
            ):
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
        if self._shutting_down:
            return
        await self._async_calculate_and_control(now=now)
        self.async_write_ha_state()

    def _reset_pid_state(self) -> None:
        """Reset PID algorithm variables."""
        self._integral = 0.0
        self._prev_error = None
        self._prev_room_humidity = None
        self._filtered_d = 0.0
        self._last_d_calc_time = None
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
        return determine_active_section(
            output=output,
            current_sec=self._active_section,
            sec1_max=self._sec1_max,
            sec2_max=self._sec2_max,
            sec3_max=self._sec3_max,
            allow_sec0=False,
        )

    def _get_section_parameters(
        self, section: int
    ) -> tuple[bool, float, bool, str, bool, str]:
        """Return (apply_temp, delta, apply_action, action, apply_mode, mode)."""
        idx = section if section in (1, 2, 3) else 4
        return (
            getattr(self, f"_sec{idx}_apply_temp"),
            getattr(self, f"_sec{idx}_delta"),
            getattr(self, f"_sec{idx}_apply_action"),
            getattr(self, f"_sec{idx}_action"),
            getattr(self, f"_sec{idx}_apply_mode"),
            getattr(self, f"_sec{idx}_mode"),
        )

    # ---------------------------------------------------------
    # Core PID Calculation & Control Loop
    # ---------------------------------------------------------
    async def _async_calculate_and_control(
        self, now: datetime | None = None, force: bool = False
    ) -> None:
        """Run PID calculation, section mapping, and dispatch to slave humidifier or PWM switches."""
        async with self._calc_lock:
            if self._shutting_down:
                return
            try:
                if now is None:
                    now = dt_util.utcnow()

                self._update_sensor_values(now=now)
                self._vent_status = self._vent_guard.evaluate(self.hass, now)

                if self._last_calc_time is None or force:
                    dt = float(self._sample_period)
                else:
                    raw_dt = (now - self._last_calc_time).total_seconds()
                    dt = max(1.0, min(float(self._sample_period) * 2.0, raw_dt))
                self._last_calc_time = now

                indoor_val_valid = (
                    self._indoor_humidity is not None and math.isfinite(self._indoor_humidity)
                )

                # Fail-Safe check
                if not indoor_val_valid or self._sensor_stale_count >= 3:
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
                            half_db = DUAL_MIN_DEADBAND_HUMIDITY / 2.0
                            if self._is_heating:
                                is_heating = self._indoor_humidity < (self._target_humidity + half_db)
                            else:
                                is_heating = self._indoor_humidity < (self._target_humidity - half_db)
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
                        if (
                            self._control_mode == CONTROL_MODE_SWITCH_PWM
                            and self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY
                        ):
                            prev_dev = (
                                self._target_device_humidity_id
                                if self._is_heating
                                else self._target_device_dehumidity_id
                            )
                            if prev_dev:
                                await self._async_turn_off_specific_device(prev_dev)
                        self._integral = 0.0
                        self._prev_error = None
                        self._prev_room_humidity = None
                        self._filtered_d = 0.0
                        self._last_d_calc_time = None
                        if self._control_mode == CONTROL_MODE_SWITCH_PWM:
                            self._cancel_pwm_timers()
                            self._pwm_cycle_id += 1
                            self._pwm_state = False
                    self._is_heating = is_heating

                    eff_kp, eff_ki, eff_ke = self._kp, self._ki, self._ke
                    if self._runtime is not None:
                        eff_kp, eff_ki, eff_ke, _ = self._runtime.get_effective_pid_params(
                            self._kp, self._ki, self._ke
                        )

                    step = compute_pid_step(
                        indoor_val=self._indoor_humidity,
                        target_val=self._target_humidity,
                        outdoor_val=self._outdoor_humidity,
                        has_outdoor_sensor=bool(self._outdoor_sensor_id),
                        is_heating=is_heating,
                        kp=eff_kp,
                        ki=eff_ki,
                        kd=self._kd,
                        ke=eff_ke,
                        max_i=self._max_i,
                        dt=dt,
                        sample_period=self._sample_period,
                        now=now,
                        force=force,
                        integral=self._integral,
                        filtered_d=self._filtered_d,
                        last_d_calc_time=self._last_d_calc_time,
                        prev_val=getattr(self, "_prev_room_humidity", None),
                        freeze_integral=self._vent_status.should_pause_control,
                        per_minute_integral=False,
                    )
                    error = step.error
                    p_term = step.p_term
                    i_term = step.i_term
                    d_term = step.d_term
                    e_term = step.e_term
                    final_output = step.final_output
                    self._integral = step.integral
                    self._filtered_d = step.filtered_d
                    self._last_d_calc_time = step.last_d_calc_time
                    self._prev_room_humidity = step.prev_val
                    self._prev_error = error

                    self._pid_error = round(error, 2)
                    self._pid_p = round(p_term, 2)
                    self._pid_i = round(i_term, 2)
                    self._pid_d = round(d_term, 2)
                    self._pid_e = round(e_term, 2)
                    self._pid_output = round(final_output, 2)

                # Stage 5: Manual override, Watchdog, Coupled AC interlock & Early-stop
                manual_override_active = (
                    self._manual_override_until is not None
                    and now < self._manual_override_until
                )
                if self._manual_override_until is not None and now >= self._manual_override_until:
                    self._manual_override_until = None

                watchdog_resting = (
                    self._watchdog_rest_until is not None
                    and now < self._watchdog_rest_until
                )
                if self._watchdog_rest_until is not None and now >= self._watchdog_rest_until:
                    self._watchdog_rest_until = None
                    self._continuous_run_start = None

                coupled_ac_inhibit = (
                    self._runtime is not None
                    and not self._is_heating
                    and self._runtime.is_coupled_ac_cooling()
                )
                sensor_valid = (
                    not self._fail_safe_active
                    and not self._sensor_fault_active
                    and indoor_val_valid
                )
                early_stop_pred = (
                    self._runtime.get_early_stop_prediction()
                    if self._runtime is not None
                    else 0.0
                )
                early_stop_triggered = (
                    sensor_valid
                    and early_stop_pred > 0.0
                    and self._pid_error is not None
                    and self._pid_error <= early_stop_pred
                )

                is_running = (
                    self._is_on
                    and not self._vent_status.should_pause_control
                    and not watchdog_resting
                    and not coupled_ac_inhibit
                    and self._pid_output > 0.0
                )
                if is_running:
                    if self._continuous_run_start is None:
                        self._continuous_run_start = now
                    elif (
                        self._max_run_min > 0
                        and (now - self._continuous_run_start).total_seconds()
                        >= self._max_run_min * 60.0
                    ):
                        self._watchdog_rest_until = now + timedelta(minutes=self._safety_rest_min)
                        self._continuous_run_start = None
                        watchdog_resting = True
                else:
                    self._continuous_run_start = None

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

                def _report_hum_obs(act: bool) -> None:
                    if self._runtime is not None:
                        self._runtime.update_observation(
                            indoor_val=self._indoor_humidity,
                            outdoor_val=self._outdoor_humidity,
                            target_val=self._target_humidity,
                            u_effort=(self._pid_output / 100.0) if act else 0.0,
                            is_active=act,
                            is_heating=self._is_heating,
                            vent_status=self._vent_status,
                            manual_override=manual_override_active,
                            device_unavailable=self._is_target_device_unavailable(),
                            early_stop_active=early_stop_triggered,
                        )

                if manual_override_active:
                    _report_hum_obs(self._is_on)
                    return

                # When OFF, VENTILATING, watchdog resting, coupled AC cooling, or early-stop: turn off
                if (
                    not self._is_on
                    or self._vent_status.should_pause_control
                    or watchdog_resting
                    or coupled_ac_inhibit
                    or early_stop_triggered
                ):
                    self._cancel_pwm_timers()
                    self._pwm_state = False
                    await self._async_turn_off_all_devices()
                    _report_hum_obs(False)
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
                _report_hum_obs(True)

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
        if self._shutting_down or self.hass is None:
            return
        self._cancel_pwm_timers()
        self._pwm_cycle_id += 1

        self._cycle_start_time = now
        self._cycle_end_time = now + timedelta(seconds=self._pwm_duration)

        raw_out = float(self._pid_output or 0.0)
        duty_cycle = (max(0.0, min(100.0, raw_out)) / 100.0) if math.isfinite(raw_out) else 0.0
        pwm_dur = (
            float(self._pwm_duration)
            if self._pwm_duration > 0
            else float(DEFAULT_PWM_DURATION_SWITCH)
        )
        on_time = duty_cycle * pwm_dur
        off_time = pwm_dur - on_time

        active_dev = self._active_target_device_id()

        # Dual controller inactive device cleanup (also enforced as prerequisite inside _async_turn_on_switch)
        inactive_dev = self._inactive_target_device_id(active_dev)
        if inactive_dev:
            await self._async_turn_off_specific_device(inactive_dev)

        # Full OFF
        if duty_cycle <= 0.0 or on_time < float(self._min_on_time):
            await self._async_turn_off_switch(active_dev)
            self._schedule_next_cycle(self._cycle_end_time)
            return

        # Full ON
        if duty_cycle >= 1.0 or off_time < self._compute_effective_min_off():
            turned_on = await self._async_turn_on_switch(active_dev)
            self._pwm_state = bool(turned_on)
            self._schedule_next_cycle(self._cycle_end_time)
            return

        # Anti-short-cycling check
        turn_off_time = now + timedelta(seconds=on_time)
        self._cycle_turn_off_time = turn_off_time

        turned_on = await self._async_turn_on_switch(active_dev)
        self._pwm_state = bool(turned_on)

        if turned_on:
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
        if self._shutting_down or self.hass is None:
            return
        active_dev = self._active_target_device_id()
        await self._async_turn_off_switch(active_dev)
        self.async_write_ha_state()

    async def _async_pwm_cycle_callback(self, now: datetime) -> None:
        """Callback for start of next PWM cycle."""
        if self._shutting_down or self.hass is None:
            return
        await self._async_calculate_and_control(now=now, force=True)
        self.async_write_ha_state()

    def _pwm_device_ids(self) -> list[str]:
        """Return unique PWM target device entity IDs configured on this humidifier."""
        return [
            d
            for d in dict.fromkeys(
                [
                    self._target_device_id,
                    self._target_device_humidity_id,
                    self._target_device_dehumidity_id,
                ]
            )
            if d
        ]

    def _compute_effective_min_off(self, run_duration_sec: float | None = None) -> float:
        """Compute effective minimum OFF protection time in seconds."""
        run_dur = (
            self._last_run_duration_sec
            if run_duration_sec is None
            else max(0.0, float(run_duration_sec))
        )
        return compute_effective_min_off(
            self._min_off_time,
            run_dur,
            self._dynamic_min_off_multiplier,
        )

    def _record_confirmed_off(
        self,
        dev_id: str,
        off_at: datetime,
        *,
        run_dur_override: float | None = None,
    ) -> float:
        """Record verified OFF timestamp and run duration, clearing any unverified state."""
        last_on = self._last_switch_on_times.get(dev_id, self._last_switch_on_time)
        last_off = self._last_switch_off_times.get(dev_id, self._last_switch_off_time)
        active_dev = self._active_target_device_id()
        is_active_target = dev_id == active_dev
        if run_dur_override is not None:
            run_dur = max(0.0, float(run_dur_override))
        elif last_on is not None and (last_off is None or last_on > last_off):
            run_dur = max(0.0, (off_at - last_on).total_seconds())
        else:
            run_dur = self._last_run_duration_sec

        if is_active_target and (
            run_dur_override is not None
            or (last_on is not None and (last_off is None or last_on > last_off))
        ):
            self._last_run_duration_sec = run_dur

        self._last_switch_off_times[dev_id] = off_at
        if (
            is_active_target
            or self._last_switch_off_time is None
            or off_at > self._last_switch_off_time
        ):
            self._last_switch_off_time = off_at

        self._unverified_off_devices.pop(dev_id, None)
        if self.hass is not None:
            domain_data = self.hass.data.get(DOMAIN)
            if isinstance(domain_data, dict):
                reload_unverified = domain_data.get("reload_unverified_off")
                if isinstance(reload_unverified, dict):
                    reload_unverified.pop(dev_id, None)
        return run_dur

    async def _async_safe_shutdown_pwm_on_remove(
        self, *, reason: str = "remove"
    ) -> tuple[dict[str, tuple[datetime, float]], dict[str, float]]:
        """Safely turn off active/indeterminate PWM switches on removal or HA shutdown."""
        if self._control_mode != CONTROL_MODE_SWITCH_PWM or self.hass is None:
            return {}, {}
        active_dev = self._active_target_device_id()
        controller_active = bool(self._is_on)
        shutdown_records: dict[str, tuple[datetime, float]] = {}
        unverified_devices: dict[str, float] = {}

        for dev_id in self._pwm_device_ids():
            st = self.hass.states.get(dev_id)
            state_val = st.state if st is not None else None
            is_active_target = dev_id == active_dev
            internal_active = bool(self._pwm_state and is_active_target)
            state_is_on = state_val == STATE_ON
            state_is_indeterminate = state_val in (
                None,
                "",
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            )
            was_unverified = dev_id in self._unverified_off_devices
            needs_shutdown = (
                state_is_on
                or internal_active
                or was_unverified
                or (
                    state_is_indeterminate
                    and controller_active
                    and (is_active_target or st is not None)
                )
            )
            if not needs_shutdown:
                continue

            now_before = dt_util.utcnow()
            last_on = self._last_switch_on_times.get(dev_id, self._last_switch_on_time)
            if was_unverified:
                run_dur = self._unverified_off_devices[dev_id]
            elif last_on is not None:
                run_dur = max(0.0, (now_before - last_on).total_seconds())
            else:
                run_dur = self._last_run_duration_sec

            confirmed_off = False
            try:
                confirmed_off = await self._switch_guard.async_turn_off(
                    dev_id, confirm=True
                )
                if not confirmed_off:
                    _LOGGER.error(
                        "Failed to confirm safe PWM shutdown for %s on %s (reason=%s)",
                        dev_id,
                        reason,
                        self._switch_guard.last_error.get(dev_id, "unknown"),
                    )
            except Exception as err:
                _LOGGER.error(
                    "Safe PWM shutdown raised error for %s on %s: %s",
                    dev_id,
                    reason,
                    err,
                )

            if confirmed_off:
                off_at = dt_util.utcnow()
                if is_active_target:
                    self._pwm_state = False
                recorded_dur = self._record_confirmed_off(
                    dev_id,
                    off_at,
                    run_dur_override=run_dur if (is_active_target and last_on is not None) or was_unverified else None,
                )
                shutdown_records[dev_id] = (off_at, recorded_dur)
            else:
                self._unverified_off_devices[dev_id] = run_dur
                unverified_devices[dev_id] = run_dur
                cur_st = self.hass.states.get(dev_id)
                if is_active_target:
                    self._pwm_state = bool(cur_st is not None and cur_st.state == STATE_ON)

        return shutdown_records, unverified_devices

    def _record_reload_off(
        self,
        shutdown_records: Mapping[str, tuple[datetime, float]] | None = None,
        unverified_devices: Mapping[str, float] | None = None,
    ) -> None:
        """Persist confirmed minimum OFF deadlines and unverified OFF devices across reload."""
        if self._control_mode != CONTROL_MODE_SWITCH_PWM or self.hass is None:
            return
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        reload_off = domain_data.setdefault("reload_off", {})
        reload_unverified = domain_data.setdefault("reload_unverified_off", {})
        now = dt_util.utcnow()
        active_dev = self._active_target_device_id()

        unverified_map: dict[str, float] = dict(self._unverified_off_devices)
        if unverified_devices:
            unverified_map.update(unverified_devices)

        for dev_id in self._pwm_device_ids():
            if dev_id in unverified_map:
                reload_off.pop(dev_id, None)
                reload_unverified[dev_id] = max(0.0, float(unverified_map[dev_id]))
                continue

            if shutdown_records and dev_id in shutdown_records:
                reload_unverified.pop(dev_id, None)
                off_at, run_dur = shutdown_records[dev_id]
                eff = self._compute_effective_min_off(run_dur)
                deadline = (
                    off_at + timedelta(seconds=eff) if eff > 0 else None
                )
                prev_deadline = self._reload_min_off_deadlines.get(dev_id)
                if prev_deadline is not None and (
                    deadline is None or prev_deadline > deadline
                ):
                    deadline = prev_deadline
                if deadline is not None and deadline > now:
                    reload_off[dev_id] = (off_at, deadline)
                continue

            st = self.hass.states.get(dev_id)
            is_running = (self._pwm_state and dev_id == active_dev) or (
                st is not None and st.state == STATE_ON
            )
            if is_running:
                last_on = self._last_switch_on_times.get(dev_id, self._last_switch_on_time)
                run_dur = (
                    max(0.0, (now - last_on).total_seconds())
                    if last_on is not None
                    else self._last_run_duration_sec
                )
                reload_off.pop(dev_id, None)
                reload_unverified[dev_id] = run_dur
                continue

            last_off = self._last_switch_off_times.get(
                dev_id,
                self._last_switch_off_time if dev_id == active_dev else None,
            )
            eff = self._compute_effective_min_off()
            deadline = (
                last_off + timedelta(seconds=eff)
                if last_off is not None and eff > 0
                else None
            )
            prev_deadline = self._reload_min_off_deadlines.get(dev_id)
            if prev_deadline is not None and (deadline is None or prev_deadline > deadline):
                deadline = prev_deadline
            if deadline is not None and deadline > now:
                reload_off[dev_id] = (last_off or now, deadline)

    def _restore_reload_off(self) -> None:
        """Inherit minimum OFF deadline and unverified OFF targets across config entry reload."""
        domain_data = self.hass.data.get(DOMAIN)
        if not isinstance(domain_data, Mapping):
            return
        reload_off = domain_data.get("reload_off")
        reload_unverified = domain_data.get("reload_unverified_off")
        now = dt_util.utcnow()
        for dev_id in self._pwm_device_ids():
            if isinstance(reload_unverified, Mapping):
                unv_dur = reload_unverified.pop(dev_id, None)
                if isinstance(unv_dur, (int, float)) and not isinstance(unv_dur, bool):
                    self._unverified_off_devices[dev_id] = max(0.0, float(unv_dur))
            if isinstance(reload_off, Mapping):
                rec = reload_off.pop(dev_id, None)
                if isinstance(rec, tuple) and len(rec) == 2:
                    off_at_utc, deadline_utc = rec
                    if (
                        isinstance(off_at_utc, datetime)
                        and isinstance(deadline_utc, datetime)
                        and deadline_utc > now
                    ):
                        existing = self._last_switch_off_times.get(dev_id)
                        if existing is None or off_at_utc > existing:
                            self._last_switch_off_times[dev_id] = off_at_utc
                        if self._last_switch_off_time is None or off_at_utc > self._last_switch_off_time:
                            self._last_switch_off_time = off_at_utc
                        self._reload_min_off_deadlines[dev_id] = deadline_utc

    async def _async_ensure_inactive_off(self, active_dev: str) -> bool:
        """Verify any opposing dual-mode device is explicitly OFF before enabling active_dev."""
        inactive_dev = self._inactive_target_device_id(active_dev)
        if not inactive_dev:
            return True
        if self._switch_guard.is_off_pending(inactive_dev):
            _LOGGER.warning(
                "Blocked ON for %s because opposing device %s still has an OFF confirmation pending",
                active_dev,
                inactive_dev,
            )
            return False
        if (
            self._switch_guard.is_confirmed_off(inactive_dev)
            and inactive_dev not in self._unverified_off_devices
        ):
            return True
        confirmed = await self._async_turn_off_specific_device(inactive_dev)
        if not confirmed:
            _LOGGER.error(
                "Blocked ON for %s because opposing device %s could not be confirmed OFF",
                active_dev,
                inactive_dev,
            )
            return False
        return True

    async def _async_clear_unverified_off_before_on(self) -> bool:
        """Confirm OFF for any post-reload unverified switches and start their min_off on confirmation."""
        if not self._unverified_off_devices or self.hass is None:
            return True
        for dev_id, prior_run_dur in list(self._unverified_off_devices.items()):
            if self._switch_guard.is_off_pending(dev_id):
                return False
            if self._switch_guard.is_confirmed_off(dev_id):
                confirmed = True
            else:
                try:
                    confirmed = await self._switch_guard.async_turn_off(dev_id, confirm=True)
                except Exception as err:
                    _LOGGER.error("Error confirming post-reload OFF for %s: %s", dev_id, err)
                    confirmed = False
            if not confirmed:
                _LOGGER.warning(
                    "Blocking switch ON: prior switch %s has not confirmed OFF",
                    dev_id,
                )
                return False
            off_at = dt_util.utcnow()
            self._record_confirmed_off(dev_id, off_at, run_dur_override=prior_run_dur)
            eff = self._compute_effective_min_off(prior_run_dur)
            if eff > 0:
                deadline = off_at + timedelta(seconds=eff)
                prev_dl = self._reload_min_off_deadlines.get(dev_id)
                if prev_dl is None or deadline > prev_dl:
                    self._reload_min_off_deadlines[dev_id] = deadline
        return True

    async def _async_turn_on_switch(self, device_id: str) -> bool:
        """Turn on switch/input_boolean after enforcing min_off, reload guard, and dual-relay interlock."""
        if self._shutting_down or not device_id or self.hass is None:
            return False

        if not await self._async_clear_unverified_off_before_on():
            return False

        if not await self._async_ensure_inactive_off(device_id):
            return False

        if self._shutting_down or device_id != self._active_target_device_id():
            return False

        now = dt_util.utcnow()
        reload_deadline = self._reload_min_off_deadlines.get(device_id)
        if reload_deadline is not None:
            if now < reload_deadline:
                _LOGGER.info(
                    "Anti-short-cycling: switch %s ON blocked by reload carry-over (%.1fs remaining)",
                    device_id,
                    (reload_deadline - now).total_seconds(),
                )
                return False
            self._reload_min_off_deadlines.pop(device_id, None)

        if self._thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
            last_off = self._last_switch_off_times.get(device_id)
        else:
            last_off = self._last_switch_off_times.get(device_id, self._last_switch_off_time)
        effective_min_off = self._compute_effective_min_off()
        if last_off is not None and effective_min_off > 0.0:
            elapsed_off = (now - last_off).total_seconds()
            if elapsed_off < effective_min_off:
                _LOGGER.info(
                    "Anti-short-cycling: switch %s ON blocked (%.1fs < effective min_off %.1fs)",
                    device_id,
                    elapsed_off,
                    effective_min_off,
                )
                return False

        st = self.hass.states.get(device_id)
        if st is not None and st.state == STATE_ON:
            return True
        _LOGGER.info("Turning ON switch %s", device_id)
        if await self._switch_guard.async_turn_on(device_id, confirm=False):
            self._last_switch_on_time = now
            self._last_switch_on_times[device_id] = now
            return True
        return False

    async def _async_turn_off_switch(self, device_id: str) -> bool:
        """Turn off switch/input_boolean and record OFF timestamp only upon confirmation."""
        if not device_id or self.hass is None:
            return False
        if (
            self._switch_guard.is_confirmed_off(device_id)
            and not self._switch_guard.is_off_pending(device_id)
            and device_id not in self._unverified_off_devices
        ):
            if device_id == self._active_target_device_id():
                self._pwm_state = False
            return True

        _LOGGER.info("Turning OFF switch %s", device_id)
        now = dt_util.utcnow()
        last_on = self._last_switch_on_times.get(device_id, self._last_switch_on_time)
        last_off = self._last_switch_off_times.get(device_id, self._last_switch_off_time)
        try:
            confirmed = await self._switch_guard.async_turn_off(device_id, confirm=True)
        except Exception as err:
            _LOGGER.error("Error turning OFF switch %s: %s", device_id, err)
            confirmed = False

        if confirmed:
            self._record_confirmed_off(device_id, now)
            if device_id == self._active_target_device_id():
                self._pwm_state = False
            return True

        run_dur = (
            max(0.0, (now - last_on).total_seconds())
            if last_on is not None and (last_off is None or last_on > last_off)
            else self._last_run_duration_sec
        )
        self._unverified_off_devices[device_id] = run_dur
        if device_id == self._active_target_device_id():
            cur_st = self.hass.states.get(device_id)
            self._pwm_state = bool(cur_st is not None and cur_st.state == STATE_ON)
        return False

    async def _async_turn_off_specific_device(self, device_id: str) -> bool:
        """Turn off a specific target device and confirm OFF via SwitchCommandGuard."""
        if not device_id or self.hass is None:
            return True
        if (
            self._switch_guard.is_confirmed_off(device_id)
            and not self._switch_guard.is_off_pending(device_id)
            and device_id not in self._unverified_off_devices
        ):
            return True

        now = dt_util.utcnow()
        last_on = self._last_switch_on_times.get(device_id, self._last_switch_on_time)
        last_off = self._last_switch_off_times.get(device_id, self._last_switch_off_time)
        try:
            confirmed = await self._switch_guard.async_turn_off(device_id, confirm=True)
        except Exception as err:
            _LOGGER.error("Failed to turn off device %s: %s", device_id, err)
            confirmed = False

        if confirmed:
            self._record_confirmed_off(device_id, dt_util.utcnow())
            return True

        run_dur = (
            max(0.0, (now - last_on).total_seconds())
            if last_on is not None and (last_off is None or last_on > last_off)
            else self._last_run_duration_sec
        )
        self._unverified_off_devices[device_id] = run_dur
        _LOGGER.error(
            "Device %s failed to confirm OFF (reason=%s)",
            device_id,
            self._switch_guard.last_error.get(device_id, "unknown"),
        )
        return False

    async def _async_turn_off_all_devices(self) -> None:
        """Turn off all associated slave/switch target devices."""
        devices = self._pwm_device_ids()
        first_turned_off = False
        for dev in devices:
            if not dev:
                continue
            if (
                self._switch_guard.is_confirmed_off(dev)
                and not self._switch_guard.is_off_pending(dev)
                and dev not in self._unverified_off_devices
            ):
                continue
            if first_turned_off:
                await asyncio.sleep(0.5)
            await self._async_turn_off_specific_device(dev)
            first_turned_off = True

    async def _async_keep_alive_handler(self, now: datetime) -> None:
        """Periodic keep-alive handler ensuring switch state integrity via SwitchCommandGuard."""
        if (
            self._shutting_down
            or self._control_mode != CONTROL_MODE_SWITCH_PWM
            or self.hass is None
        ):
            return

        if (
            not self._is_on
            or self._vent_status.should_pause_control
            or (self._watchdog_rest_until is not None and dt_util.utcnow() < self._watchdog_rest_until)
            or (
                self._runtime is not None
                and not self._is_heating
                and self._runtime.is_coupled_ac_cooling()
            )
        ):
            await self._async_turn_off_all_devices()
            return

        active_dev = self._active_target_device_id()
        if not active_dev:
            return

        if self._pwm_state:
            if not await self._async_clear_unverified_off_before_on():
                self._pwm_state = False
                self.async_write_ha_state()
                return
            if not await self._async_ensure_inactive_off(active_dev):
                self._pwm_state = False
                self.async_write_ha_state()
                return
            if (
                self._shutting_down
                or not self._is_on
                or not self._pwm_state
                or active_dev != self._active_target_device_id()
                or self._switch_guard.is_off_pending(active_dev)
            ):
                return
            cur_st = self.hass.states.get(active_dev)
            if cur_st is not None and cur_st.state == STATE_ON:
                await self._switch_guard.async_turn_on(active_dev, confirm=False)
            else:
                await self._async_turn_on_switch(active_dev)
            return

        await self._async_turn_off_switch(active_dev)
        inactive_dev = self._inactive_target_device_id(active_dev)
        if inactive_dev:
            inact_st = self.hass.states.get(inactive_dev)
            if inact_st is None or inact_st.state != STATE_OFF:
                await self._async_turn_off_specific_device(inactive_dev)
