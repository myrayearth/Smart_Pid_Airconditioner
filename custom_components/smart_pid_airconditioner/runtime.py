"""Centralized runtime for Smart PID control observation, self-learning, and safety."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import contextlib
from datetime import datetime, time as dt_time, timedelta
import logging
import math
import time
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.util.dt as dt_util

from .const import (
    CONF_AUX_TEMP_SENSOR,
    CONF_CONTROL_MODE,
    CONF_COUPLED_AC,
    CONF_COUPLED_DEHUMIDIFIER,
    CONF_CUSTOM_COAST_WINDOW_MIN,
    CONF_CUSTOM_TAU_EM_MIN,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MANUAL_OVERRIDE_MIN,
    CONF_MAX_RUN_MIN,
    CONF_OUTDOOR_SENSOR,
    CONF_PREDICTION_MODE,
    CONF_PREDICTIVE_ON_ENABLED,
    CONF_RECORDER_BOOTSTRAP,
    CONF_SAFETY_REST_MIN,
    CONF_SOLAR_GUARD,
    CONF_SUNRISE_SAVING_ENABLED,
    CONF_SUNRISE_SAVING_END,
    CONF_SUNRISE_SAVING_START,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_HUMIDITY,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONF_VENT_COOLDOWN_MIN,
    CONF_VENT_DEBOUNCE_SEC,
    CONF_VENT_LOGIC,
    CONF_VENT_MODE,
    CONF_VENT_SENSORS,
    CONF_VENT_SWITCH,
    DEFAULT_COAST_WINDOW_FAST_AIR_MIN,
    DEFAULT_COAST_WINDOW_HIGH_INERTIA_MIN,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CUSTOM_COAST_WINDOW_MIN,
    DEFAULT_CUSTOM_TAU_EM_MIN,
    DEFAULT_DEVICE_UNAVAILABLE_GRACE_SEC,
    DEFAULT_INERTIA_PROFILE,
    DEFAULT_KE,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MANUAL_OVERRIDE_MIN,
    DEFAULT_MAX_RUN_MIN,
    DEFAULT_PREDICTION_MODE,
    DEFAULT_PREDICTIVE_ON_ENABLED,
    DEFAULT_RECORDER_BOOTSTRAP,
    DEFAULT_SAFETY_REST_MIN,
    DEFAULT_SOLAR_GUARD,
    DEFAULT_SUNRISE_SAVING_ENABLED,
    DEFAULT_SUNRISE_SAVING_END,
    DEFAULT_SUNRISE_SAVING_START,
    DEFAULT_THERMOSTAT_TYPE,
    DEFAULT_TUNING_MODE,
    DEFAULT_VENT_COOLDOWN_MIN,
    DEFAULT_VENT_DEBOUNCE_SEC,
    DEFAULT_VENT_LOGIC,
    DEFAULT_VENT_MODE,
    DOMAIN,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    TUNING_MODES,
    TUNING_MODE_AUTO,
    TUNING_MODE_SHADOW,
    duration_to_seconds,
    is_heating_type,
    is_humidity_type,
)
from .learner import (
    COAST_CONFIDENCE_ABSTAIN_THRESHOLD,
    ConfidenceStage,
    CyclePhase,
    INERTIA_CUSTOM,
    INERTIA_HIGH_INERTIA,
    PREDICTION_MODES,
    SimcAutoTuner,
    TuningParameters,
)
from .config_utils import get_entry_setting, parse_temperature_state
from .psychrometrics import ah_to_rh, rh_to_ah
from .storage import LearningStateSnapshot, ModelStore
from .ventilation import VentState, VentStatus

_LOGGER = logging.getLogger(__name__)
_MAX_OUTAGE_BUFFERED_TICKS = 10


def _same_pid_gains(a: TuningParameters | None, b: TuningParameters | None) -> bool:
    """Return True when two TuningParameters have identical effective (kp, ki, ke)."""
    if a is None or b is None:
        return a is b
    return (
        math.isclose(a.kp, b.kp, rel_tol=0.0, abs_tol=1e-6)
        and math.isclose(a.ki, b.ki, rel_tol=0.0, abs_tol=1e-6)
        and math.isclose(a.ke, b.ke, rel_tol=0.0, abs_tol=1e-6)
    )


def _parse_time_value(value: Any, default: dt_time) -> dt_time:
    """Parse HH:MM[:SS] string or datetime.time into datetime.time."""
    if isinstance(value, dt_time):
        return value
    if isinstance(value, str):
        parts = value.strip().split(":")
        try:
            if len(parts) == 2:
                return dt_time(int(parts[0]), int(parts[1]), 0)
            if len(parts) >= 3:
                return dt_time(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, TypeError):
            pass
    return default


class SmartPIDRuntime:
    """Shared runtime coordinating learning, storage, and diagnostic subscribers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store = ModelStore(hass, entry.entry_id)
        self.tuner = SimcAutoTuner()

        self._subscribers: list[Callable[[], None]] = []
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._bootstrap_task: Any = None
        self._active_bootstrap_tasks: set[asyncio.Task[Any]] = set()
        self._dirty: bool = False
        self._unloaded: bool = False
        self._snapshot_generation: int = 0
        self._reset_generation: int = 0
        self._apply_generation: int = 0

        # Config cache
        self.tuning_mode: str = str(self._get_setting(CONF_TUNING_MODE, DEFAULT_TUNING_MODE))
        self.prediction_mode: str = str(self._get_setting(CONF_PREDICTION_MODE, DEFAULT_PREDICTION_MODE))
        self.inertia_profile: str = str(self._get_setting(CONF_INERTIA_PROFILE, DEFAULT_INERTIA_PROFILE))
        self.custom_coast_window_min: float = float(
            self._get_setting(CONF_CUSTOM_COAST_WINDOW_MIN, DEFAULT_CUSTOM_COAST_WINDOW_MIN)
        )
        self.custom_tau_em_min: float = float(
            self._get_setting(CONF_CUSTOM_TAU_EM_MIN, DEFAULT_CUSTOM_TAU_EM_MIN)
        )
        self.solar_guard_enabled: bool = bool(
            self._get_setting(CONF_SOLAR_GUARD, DEFAULT_SOLAR_GUARD)
        )
        self.sunrise_saving_enabled: bool = bool(
            self._get_setting(CONF_SUNRISE_SAVING_ENABLED, DEFAULT_SUNRISE_SAVING_ENABLED)
        )
        self.sunrise_saving_start: dt_time = _parse_time_value(
            self._get_setting(CONF_SUNRISE_SAVING_START, DEFAULT_SUNRISE_SAVING_START),
            dt_time(6, 0, 0),
        )
        self.sunrise_saving_end: dt_time = _parse_time_value(
            self._get_setting(CONF_SUNRISE_SAVING_END, DEFAULT_SUNRISE_SAVING_END),
            dt_time(10, 0, 0),
        )
        self.predictive_on_enabled: bool = bool(
            self._get_setting(CONF_PREDICTIVE_ON_ENABLED, DEFAULT_PREDICTIVE_ON_ENABLED)
        )
        self.aux_temp_sensor: str | None = self._get_setting(CONF_AUX_TEMP_SENSOR, None) or None
        self.thermostat_type: str = str(self._get_setting(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE))
        self.is_humidity: bool = is_humidity_type(self.thermostat_type)
        self.learning_unit: str = (
            ("ah" if self.aux_temp_sensor else "rh") if self.is_humidity else "c"
        )

        tau_em, coast_win, peak_delta = self._resolve_coasting_settings()
        self.snapshot = LearningStateSnapshot(
            inertia_profile=self.inertia_profile,
            tau_em_min=tau_em,
            max_coast_window_min=coast_win,
            peak_confirm_delta=peak_delta,
            learning_unit=self.learning_unit,
        )

        self.coupled_dehumidifier: str | None = self._get_setting(CONF_COUPLED_DEHUMIDIFIER, None) or None
        self.coupled_ac: str | None = self._get_setting(CONF_COUPLED_AC, None) or None
        self.manual_override_min: int = int(float(self._get_setting(CONF_MANUAL_OVERRIDE_MIN, DEFAULT_MANUAL_OVERRIDE_MIN)))
        self.max_run_min: int = int(float(self._get_setting(CONF_MAX_RUN_MIN, DEFAULT_MAX_RUN_MIN)))
        self.safety_rest_min: int = int(float(self._get_setting(CONF_SAFETY_REST_MIN, DEFAULT_SAFETY_REST_MIN)))
        self.recorder_bootstrap_enabled: bool = bool(
            self._get_setting(CONF_RECORDER_BOOTSTRAP, DEFAULT_RECORDER_BOOTSTRAP)
        )

        self.baseline = TuningParameters(
            kp=float(self._get_setting(CONF_KP, DEFAULT_KP)),
            ki=float(self._get_setting(CONF_KI, DEFAULT_KI)),
            ke=float(self._get_setting(CONF_KE, DEFAULT_KE)),
        )
        self.recommended: TuningParameters = self._recompute_recommended()

        # Latest observation from climate / humidifier entity
        self._obs_indoor: float | None = None
        self._obs_outdoor: float | None = None
        self._obs_target: float | None = None
        self._obs_u_effort: float = 0.0
        self._obs_is_active: bool = False
        self._obs_is_heating: bool = False
        self._obs_vent_status: VentStatus = VentStatus(VentState.NORMAL, False, False, 0, False)
        self._obs_manual_override: bool = False
        self._obs_device_unavailable: bool = False
        self._obs_early_stop_active: bool = False
        self._device_unavailable_since: datetime | None = None
        self._outage_buffered_ticks: list[dict[str, Any]] = []
        self._outage_pre_active: bool | None = None

        # 5-minute RLS block accumulator
        self._block_minutes: int = 0
        self._block_start_val: float | None = None
        self._block_end_val: float | None = None
        self._block_u_sum: float = 0.0
        self._block_out_diff_sum: float = 0.0
        self._block_aux_heat_sum: float = 0.0
        self._block_contaminated: bool = False

        self.fallback_reason: str | None = None
        self._applied_auto_params: TuningParameters | None = None
        self._pending_command: bool = False

    @property
    def can_reset_learning(self) -> bool:
        """Return True only when the target device is idle and no command is pending."""
        return not self._obs_is_active and not self._pending_command

    def set_pending_command(self, pending: bool) -> None:
        """Track whether a control command is currently pending dispatch/confirmation."""
        pending_bool = bool(pending)
        if self._pending_command != pending_bool:
            self._pending_command = pending_bool
            self._notify_subscribers()

    def _get_setting(self, key: str, default: Any = None) -> Any:
        return get_entry_setting(
            self.entry,
            key,
            default,
            sections=("adaptive_learning", "ki_group", "ventilation", "section_0"),
        )

    def _active_rls(self):
        return self.snapshot.hum_rls if self.is_humidity else self.snapshot.temp_rls

    def _recompute_recommended(self, rls: Any | None = None) -> TuningParameters:
        return self.tuner.compute(
            self._active_rls() if rls is None else rls,
            prediction_mode=self.prediction_mode,
            dead_time_min=self.snapshot.coasting.get_effective_dead_time(),
            baseline=self.baseline,
        )

    def subscribe(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to runtime diagnostic/state updates."""
        self._subscribers.append(cb)

        def _unsub() -> None:
            if cb in self._subscribers:
                self._subscribers.remove(cb)

        return _unsub

    def _notify_subscribers(self) -> None:
        for cb in list(self._subscribers):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error notifying SmartPIDRuntime subscriber")

    def _resolve_coasting_settings(self) -> tuple[float, float, float]:
        """Resolve (tau_em_min, max_coast_window_min, peak_confirm_delta) for current profile."""
        if self.inertia_profile == INERTIA_CUSTOM:
            tau_em = self.custom_tau_em_min
            coast_win = self.custom_coast_window_min
        elif self.inertia_profile == INERTIA_HIGH_INERTIA:
            tau_em = 35.0
            coast_win = DEFAULT_COAST_WINDOW_HIGH_INERTIA_MIN
        else:
            tau_em = 10.0
            coast_win = DEFAULT_COAST_WINDOW_FAST_AIR_MIN
        peak_delta = 0.5 if (self.is_humidity and not self.aux_temp_sensor) else 0.1
        return tau_em, coast_win, peak_delta

    def is_active_runtime(self) -> bool:
        """Return True if this runtime is still registered and not unloaded."""
        if self._unloaded:
            return False
        domain_data = getattr(self.hass, "data", {}).get(DOMAIN)
        if isinstance(domain_data, Mapping):
            reg = domain_data.get(self.entry.entry_id)
            if isinstance(reg, SmartPIDRuntime) and reg is not self:
                return False
        entry_rt = getattr(self.entry, "runtime_data", None)
        if isinstance(entry_rt, SmartPIDRuntime) and entry_rt is not self:
            return False
        return True

    def register_bootstrap_task(self, task: asyncio.Task[Any] | None) -> None:
        """Track an active external bootstrap task so unload can cancel it cleanly."""
        if task is not None:
            self._active_bootstrap_tasks.add(task)

    def unregister_bootstrap_task(self, task: asyncio.Task[Any] | None) -> None:
        """Stop tracking a finished bootstrap task."""
        if task is not None:
            self._active_bootstrap_tasks.discard(task)

    async def async_setup(self) -> None:
        """Load persisted state and start 1-minute learning tick."""
        self._unloaded = False
        tau_em, coast_win, peak_delta = self._resolve_coasting_settings()
        self.snapshot = await self.store.async_load(
            inertia_profile=self.inertia_profile,
            tau_em_min=tau_em,
            max_coast_window_min=coast_win,
            peak_confirm_delta=peak_delta,
            expected_learning_unit=self.learning_unit,
        )
        self._applied_auto_params = self.snapshot.safety.applied_params
        self.recommended = self._recompute_recommended()
        self._unsub_tick = async_track_time_interval(
            self.hass,
            self._async_learning_tick,
            timedelta(minutes=1),
        )

        # Optional Stage 5 Recorder bootstrap on fresh install
        if self.recorder_bootstrap_enabled and self._active_rls().sample_count == 0:
            self._bootstrap_task = self.hass.async_create_task(
                self._async_recorder_bootstrap(),
                name=f"{DOMAIN}_recorder_bootstrap_{self.entry.entry_id}",
            )

    async def async_unload(self) -> None:
        """Clean up timers and persist pending dirty state."""
        self._unloaded = True
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        if self._bootstrap_task is not None:
            task = self._bootstrap_task
            self._bootstrap_task = None
            if not task.done():
                task.cancel()
                if asyncio.isfuture(task):
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        current_task = asyncio.current_task()
        for task in list(self._active_bootstrap_tasks):
            self._active_bootstrap_tasks.discard(task)
            if task is current_task:
                continue
            if not task.done():
                task.cancel()
                if asyncio.isfuture(task):
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        if self._dirty:
            await self.store.async_save(self.snapshot)
            self._dirty = False

    def update_observation(
        self,
        *,
        indoor_val: float | None,
        outdoor_val: float | None,
        target_val: float | None,
        u_effort: float,
        is_active: bool,
        is_heating: bool,
        vent_status: VentStatus,
        manual_override: bool = False,
        device_unavailable: bool = False,
        early_stop_active: bool = False,
    ) -> None:
        """Receive latest state observation from climate/humidifier entity."""
        now = dt_util.utcnow()
        if device_unavailable:
            if self._device_unavailable_since is None:
                self._device_unavailable_since = now
                self._outage_pre_active = self._obs_is_active
                self._outage_buffered_ticks.clear()
            unavailable_expired = (
                (now - self._device_unavailable_since).total_seconds()
                >= DEFAULT_DEVICE_UNAVAILABLE_GRACE_SEC
            )
            if unavailable_expired:
                self._outage_buffered_ticks.clear()
                self._outage_pre_active = None
        else:
            if self._device_unavailable_since is not None:
                elapsed = (now - self._device_unavailable_since).total_seconds()
                was_in_grace = (
                    not self._obs_device_unavailable
                    and elapsed < DEFAULT_DEVICE_UNAVAILABLE_GRACE_SEC
                )
                pre_active = self._outage_pre_active
                buffered = list(self._outage_buffered_ticks)
                self._device_unavailable_since = None
                self._outage_buffered_ticks.clear()
                self._outage_pre_active = None

                if was_in_grace and pre_active is not None and bool(is_active) == pre_active:
                    for tick in buffered:
                        self.snapshot.coasting.step_cycle(
                            is_active=pre_active,
                            u_effort=tick["u_effort"],
                            value=tick["value"],
                            is_heating=tick["is_heating"],
                            dt_min=1.0,
                            blocked_for_learning=tick["blocked"],
                            block_reason=tick["block_reason"],
                            target_val=tick["target_val"],
                            solar_rising_active=tick["solar_rising_active"],
                            outdoor_temp=tick["outdoor_temp"],
                            now_ts=tick["now_ts"],
                            early_stop_active=tick["early_stop_active"],
                        )
                        self._process_completed_coasting_cycle()
                elif pre_active is not None:
                    self.snapshot.coasting.mark_contaminated("device_unavailable")
                    self._block_contaminated = True
            unavailable_expired = False

        # Preserve last known active state during brief unavailability grace period
        if not device_unavailable or unavailable_expired:
            self._obs_is_active = bool(is_active) if not device_unavailable else False

        self._obs_indoor = indoor_val
        self._obs_outdoor = outdoor_val
        self._obs_target = target_val
        self._obs_u_effort = max(0.0, min(1.0, float(u_effort)))
        self._obs_is_heating = bool(is_heating)
        self._obs_vent_status = vent_status
        self._obs_manual_override = bool(manual_override)
        self._obs_device_unavailable = bool(unavailable_expired)
        self._obs_early_stop_active = bool(early_stop_active)

        if vent_status.should_block_learning or manual_override or unavailable_expired:
            reason = (
                "manual_override"
                if manual_override
                else (
                    "device_unavailable"
                    if unavailable_expired
                    else ("ventilation_degraded" if vent_status.degraded else "ventilation_contamination")
                )
            )
            self.snapshot.coasting.mark_contaminated(reason)
            self._block_contaminated = True

        self._notify_subscribers()

    def _is_solar_guard_active(self) -> bool:
        """Return True when sun is above horizon during heating and solar_guard is enabled."""
        if not self.solar_guard_enabled or self.is_humidity or not self._obs_is_heating:
            return False
        st = self.hass.states.get("sun.sun")
        if st is None:
            return False
        elevation = st.attributes.get("elevation") if isinstance(st.attributes, Mapping) else None
        if isinstance(elevation, (int, float)):
            return str(st.state) == "above_horizon" and float(elevation) > 0.0
        return str(st.state) == "above_horizon"

    def _get_aux_temp_celsius(self) -> float | None:
        """Read and normalize aux_temp_sensor to °C, returning None if unavailable."""
        if not self.aux_temp_sensor:
            return None
        st = self.hass.states.get(self.aux_temp_sensor)
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        default_unit = getattr(
            getattr(getattr(self.hass, "config", None), "units", None),
            "temperature_unit",
            None,
        )
        return parse_temperature_state(st, default_unit=default_unit)

    def _resolve_learning_values(self) -> tuple[float | None, float | None, str | None]:
        """Return (indoor_learning_val, outdoor_learning_val, fallback_reason)."""
        if self._obs_indoor is None:
            return None, None, self.fallback_reason

        if not self.is_humidity:
            return self._obs_indoor, self._obs_outdoor, None

        # Humidity type: try converting RH% to AH (g/m³) using aux_temp_sensor
        if self.aux_temp_sensor:
            temp_c = self._get_aux_temp_celsius()
            if temp_c is not None:
                ah_in = rh_to_ah(temp_c, self._obs_indoor)
                ah_out = (
                    rh_to_ah(temp_c, self._obs_outdoor)
                    if self._obs_outdoor is not None
                    else None
                )
                return ah_in, ah_out, None
            return self._obs_indoor, self._obs_outdoor, "aux_temp_unavailable_rh_fallback"

        return self._obs_indoor, self._obs_outdoor, "no_aux_temp_rh_fallback"

    def _is_coupled_dehumidifier_on(self) -> float:
        """Return 1.0 if coupled dehumidifier is active (adds compressor heat to room)."""
        if not self.coupled_dehumidifier or self.is_humidity:
            return 0.0
        st = self.hass.states.get(self.coupled_dehumidifier)
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return 0.0
        return 1.0 if st.state.lower() in (STATE_ON, "dehumidifying", "drying") else 0.0

    def is_coupled_ac_cooling(self) -> bool:
        """Return True if coupled AC is currently cooling (for dehumidifier interlock)."""
        if not self.coupled_ac or not self.is_humidity:
            return False
        st = self.hass.states.get(self.coupled_ac)
        if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        hvac_action = str(st.attributes.get("hvac_action", "")).lower()
        return st.state.lower() == "cool" or hvac_action == "cooling"

    async def _async_learning_tick(self, now: datetime | None = None) -> None:
        """1-minute learning tick: step coasting cycle and accumulate 5-min RLS blocks."""
        await self.async_step_learning(now=now)

    def _process_completed_coasting_cycle(self) -> None:
        """Consume completed coasting cycle and feed MAE/safety tracking."""
        completed = self.snapshot.coasting.consume_completed_cycle()
        if completed is not None:
            self.snapshot.safety.record_prediction_comparison(
                predicted_coast=completed["predicted_coast"],
                observed_coast=completed["observed_coast"],
            )
            self.record_cycle_performance(
                cycle_mae=completed["coast_error"],
                cycle_overshoot=completed["overshoot"],
            )
            self._snapshot_generation += 1
            self._dirty = True
            self.store.async_delay_save(self.snapshot.to_dict, delay=600)

    async def async_step_learning(self, now: datetime | None = None) -> None:
        """Advance learning by 1 minute (public for deterministic testing)."""
        val_in, val_out, fb_reason = self._resolve_learning_values()
        if fb_reason is not None:
            self.fallback_reason = fb_reason
        elif self.tuning_mode != TUNING_MODE_AUTO:
            self.fallback_reason = None

        if val_in is None:
            return

        if (
            self.is_humidity
            and self.learning_unit == "ah"
            and fb_reason == "aux_temp_unavailable_rh_fallback"
        ):
            self.snapshot.coasting.mark_contaminated("aux_temp_unavailable")
            self._block_contaminated = True
            self._notify_subscribers()
            return

        blocked = (
            self._obs_vent_status.should_block_learning
            or self._obs_manual_override
            or self._obs_device_unavailable
        )
        block_reason = (
            "manual_override"
            if self._obs_manual_override
            else (
                "device_unavailable"
                if self._obs_device_unavailable
                else ("ventilation_degraded" if self._obs_vent_status.degraded else "ventilation_contamination")
            )
        )

        # Resolve target in same domain as val_in (convert target RH% to AH when aux temp active)
        target_for_cycle = self._obs_target
        if self.is_humidity and target_for_cycle is not None and fb_reason is None and self.aux_temp_sensor:
            aux_temp_c = self._get_aux_temp_celsius()
            if aux_temp_c is not None:
                try:
                    target_for_cycle = rh_to_ah(aux_temp_c, float(target_for_cycle))
                except (ValueError, TypeError):
                    pass

        solar_rising = self._is_solar_guard_active()
        outdoor_for_cycle = None if self.is_humidity else self._obs_outdoor
        now_ts = now.timestamp() if now is not None else time.time()

        # Buffer ticks during brief <60s device unavailability grace period
        if self._device_unavailable_since is not None and not self._obs_device_unavailable:
            ref_now = now or dt_util.utcnow()
            if (ref_now - self._device_unavailable_since).total_seconds() >= DEFAULT_DEVICE_UNAVAILABLE_GRACE_SEC:
                self._obs_device_unavailable = True
                self._obs_is_active = False
                self._outage_buffered_ticks.clear()
                self._outage_pre_active = None
                self.snapshot.coasting.mark_contaminated("device_unavailable")
                self._block_contaminated = True
                blocked = True
                block_reason = "device_unavailable"
            else:
                if len(self._outage_buffered_ticks) < _MAX_OUTAGE_BUFFERED_TICKS:
                    self._outage_buffered_ticks.append(
                        {
                            "value": val_in,
                            "outdoor_temp": outdoor_for_cycle,
                            "target_val": target_for_cycle,
                            "u_effort": self._obs_u_effort,
                            "is_heating": self._obs_is_heating,
                            "blocked": blocked,
                            "block_reason": block_reason,
                            "solar_rising_active": solar_rising,
                            "early_stop_active": self._obs_early_stop_active,
                            "now_ts": now_ts,
                        }
                    )
                self._notify_subscribers()
                return

        # 1. Step 4-phase DynamicCoastingLearner (1-minute resolution)
        self.snapshot.coasting.step_cycle(
            is_active=self._obs_is_active,
            u_effort=self._obs_u_effort,
            value=val_in,
            is_heating=self._obs_is_heating,
            dt_min=1.0,
            blocked_for_learning=blocked,
            block_reason=block_reason,
            target_val=target_for_cycle,
            solar_rising_active=solar_rising,
            outdoor_temp=outdoor_for_cycle,
            now_ts=now_ts,
            early_stop_active=self._obs_early_stop_active,
        )

        # Feed real-world completed cycle error (MAE) & overshoot into TuningSafetyManager
        self._process_completed_coasting_cycle()

        # 2. Accumulate 5-minute block for HuberRLS
        if blocked:
            self._block_contaminated = True

        if self._block_start_val is None:
            self._block_start_val = val_in
        self._block_end_val = val_in
        signed_u = self._obs_u_effort if self._obs_is_heating else -self._obs_u_effort
        self._block_u_sum += signed_u
        self._block_out_diff_sum += (val_in - val_out) if val_out is not None else 0.0
        self._block_aux_heat_sum += self._is_coupled_dehumidifier_on()
        self._block_minutes += 1

        if self._block_minutes >= 5:
            if not self._block_contaminated and self._block_start_val is not None:
                dy_per_min = (self._block_end_val - self._block_start_val) / float(self._block_minutes)
                u_avg = self._block_u_sum / float(self._block_minutes)
                diff_avg = self._block_out_diff_sum / float(self._block_minutes)
                aux_avg = self._block_aux_heat_sum / float(self._block_minutes)

                rls = self._active_rls()
                if self.is_humidity:
                    rls.update([abs(u_avg), -diff_avg], dy_per_min if self._obs_is_heating else -dy_per_min)
                else:
                    rls.update([u_avg, -diff_avg, aux_avg], dy_per_min)

                self._snapshot_generation += 1
                self.recommended = self._recompute_recommended(rls)
                self._dirty = True
                self.store.async_delay_save(self.snapshot.to_dict, delay=600)

            # Reset 5-min block
            self._block_minutes = 0
            self._block_start_val = None
            self._block_end_val = None
            self._block_u_sum = 0.0
            self._block_out_diff_sum = 0.0
            self._block_aux_heat_sum = 0.0
            self._block_contaminated = False

        self._notify_subscribers()

    def get_confidence(self) -> tuple[ConfidenceStage, float]:
        rls = self._active_rls()
        return self.snapshot.safety.get_confidence_stage(
            rls_samples=rls.sample_count,
            valid_cycles=self.snapshot.coasting.accepted_count,
        )

    def get_effective_pid_params(
        self, manual_kp: float, manual_ki: float, manual_ke: float
    ) -> tuple[float, float, float, str | None]:
        """Return active PID parameters based on tuning_mode and confidence gating."""
        if self.tuning_mode != TUNING_MODE_AUTO:
            return manual_kp, manual_ki, manual_ke, self.fallback_reason

        stage, _ = self.get_confidence()
        if stage in (ConfidenceStage.COLLECTING, ConfidenceStage.STABLE):
            self.fallback_reason = "insufficient_valid_cycles"
            return manual_kp, manual_ki, manual_ke, self.fallback_reason

        if self._applied_auto_params is None:
            can_apply, reason = self.snapshot.safety.can_auto_apply(
                self._active_rls().sample_count,
                self.snapshot.coasting.accepted_count,
            )
            if not can_apply:
                self.fallback_reason = reason
                return manual_kp, manual_ki, manual_ke, reason

            prev = TuningParameters(kp=manual_kp, ki=manual_ki, ke=manual_ke)
            base_mae = self.snapshot.safety.mean_absolute_error
            self.snapshot.safety.begin_application(
                prev_params=prev,
                baseline_mae=max(0.15, base_mae) if base_mae is not None else 0.3,
                baseline_overshoot=0.4,
                applied_params=self.recommended,
            )
            self._applied_auto_params = self.recommended
            self._dirty = True
            self.store.async_delay_save(self.snapshot.to_dict, delay=600)

        self.fallback_reason = None
        return (
            self._applied_auto_params.kp,
            self._applied_auto_params.ki,
            self._applied_auto_params.ke,
            None,
        )

    def get_outdoor_coast_status(self) -> tuple[float, str]:
        """Return (outdoor_coast_correction, outdoor_band_status) for current outdoor temp."""
        if self.is_humidity:
            return 0.0, "missing"
        return self.snapshot.coasting.get_outdoor_coast_correction(self._obs_outdoor)

    def get_predicted_coast(self) -> float:
        """Return current coasting prediction including outdoor band and signed bias correction."""
        outdoor = None if self.is_humidity else self._obs_outdoor
        return self.snapshot.coasting.predict_coast(
            outdoor_temp=outdoor,
            apply_bias=True,
            apply_shortfall_scale=False,
        )

    def get_early_stop_prediction(self) -> float:
        """Return predicted coasting amount when adaptive_auto is active and tuned."""
        if self.tuning_mode != TUNING_MODE_AUTO:
            return 0.0
        stage, _ = self.get_confidence()
        if stage in (ConfidenceStage.COLLECTING, ConfidenceStage.STABLE):
            return 0.0
        if (
            len(self.snapshot.coasting.history) > 0
            and self.snapshot.coasting.get_coast_confidence() < COAST_CONFIDENCE_ABSTAIN_THRESHOLD
        ):
            self.fallback_reason = "high_coast_variance"
            return 0.0
        outdoor = None if self.is_humidity else self._obs_outdoor
        return self.snapshot.coasting.predict_coast(
            outdoor_temp=outdoor,
            apply_bias=True,
            apply_shortfall_scale=True,
        )

    def get_early_stop_prediction_in_control_units(
        self,
        *,
        current_rh: float | None = None,
        is_heating: bool | None = None,
    ) -> float:
        """Return early-stop prediction converted into control units (°C or %RH)."""
        coast = self.get_early_stop_prediction()
        if coast <= 0.0 or not self.is_humidity or self.learning_unit != "ah":
            return coast

        temp_c = self._get_aux_temp_celsius()
        rh_now = current_rh if current_rh is not None else self._obs_indoor
        if temp_c is None or rh_now is None or not math.isfinite(float(rh_now)):
            return 0.0

        rh_val = float(rh_now)
        ah_now = rh_to_ah(temp_c, rh_val)
        heating = self._obs_is_heating if is_heating is None else bool(is_heating)
        if heating:
            ah_after = ah_now + coast
            rh_after = ah_to_rh(temp_c, ah_after)
            return max(0.0, rh_after - rh_val)

        ah_after = max(0.0, ah_now - coast)
        rh_after = ah_to_rh(temp_c, ah_after)
        return max(0.0, rh_val - rh_after)

    def is_sunrise_saving_active(self, now: datetime | None = None) -> bool:
        """Return True when sunrise saving is enabled and within schedule OR sun above horizon."""
        if not is_heating_type(self.thermostat_type) or not self.sunrise_saving_enabled:
            return False

        # Sun entity check (sun.sun == above_horizon)
        st = self.hass.states.get("sun.sun")
        if st is not None and str(st.state) == "above_horizon":
            return True

        # Time window check (supports midnight wrap, e.g. 22:00 -> 07:00)
        ref_dt = now or dt_util.utcnow()
        if hasattr(dt_util, "as_local"):
            try:
                ref_dt = dt_util.as_local(ref_dt)
            except Exception:
                pass
        cur_t = ref_dt.time()
        start_t = self.sunrise_saving_start
        end_t = self.sunrise_saving_end
        if start_t <= end_t:
            return start_t <= cur_t <= end_t
        return cur_t >= start_t or cur_t <= end_t

    async def async_set_sunrise_saving_enabled(self, enabled: bool) -> None:
        """Toggle sunrise saving live without full config entry reload."""
        val = bool(enabled)
        if val == self.sunrise_saving_enabled:
            return
        self.sunrise_saving_enabled = val
        new_options = dict(self.entry.options)
        new_options[CONF_SUNRISE_SAVING_ENABLED] = val
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._notify_subscribers()

    async def async_set_sunrise_saving_start(self, value: dt_time) -> None:
        """Update sunrise saving start time live without full config entry reload."""
        if value == self.sunrise_saving_start:
            return
        self.sunrise_saving_start = value
        new_options = dict(self.entry.options)
        new_options[CONF_SUNRISE_SAVING_START] = value.strftime("%H:%M:%S")
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._notify_subscribers()

    async def async_set_sunrise_saving_end(self, value: dt_time) -> None:
        """Update sunrise saving end time live without full config entry reload."""
        if value == self.sunrise_saving_end:
            return
        self.sunrise_saving_end = value
        new_options = dict(self.entry.options)
        new_options[CONF_SUNRISE_SAVING_END] = value.strftime("%H:%M:%S")
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._notify_subscribers()

    async def async_set_predictive_on_enabled(self, enabled: bool) -> None:
        """Toggle Predictive ON live without full config entry reload."""
        val = bool(enabled)
        if val == self.predictive_on_enabled:
            return
        self.predictive_on_enabled = val
        new_options = dict(self.entry.options)
        new_options[CONF_PREDICTIVE_ON_ENABLED] = val
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._notify_subscribers()

    def get_predictive_on_margin(self) -> float:
        """Return predicted drop during dead time when Predictive ON is active and tuned."""
        if self.tuning_mode != TUNING_MODE_AUTO or not self.predictive_on_enabled or self.is_humidity:
            return 0.0
        stage, _ = self.get_confidence()
        if stage not in (ConfidenceStage.TUNED, ConfidenceStage.OPTIMIZED):
            return 0.0
        return self.snapshot.coasting.predict_preheat_drop(self._obs_outdoor)

    def record_cycle_performance(self, cycle_mae: float, cycle_overshoot: float) -> bool:
        """Evaluate post-apply performance; returns True if auto-rollback occurred."""
        rolled_back = self.snapshot.safety.record_post_apply_cycle(
            cycle_mae=cycle_mae,
            cycle_overshoot=cycle_overshoot,
        )
        if rolled_back is not None:
            self._applied_auto_params = rolled_back
            self.snapshot.safety.applied_params = rolled_back
            self.fallback_reason = self.snapshot.safety.last_fallback_reason
            self._snapshot_generation += 1
            self._dirty = True
            self.store.async_delay_save(self.snapshot.to_dict, delay=600)
            _LOGGER.warning(
                "Adaptive auto-tuning rolled back due to >30%% performance degradation on %s",
                self.entry.title,
            )
            try:
                from homeassistant.components import persistent_notification
                persistent_notification.async_create(
                    self.hass,
                    f"{self.entry.title}: 자동 튜닝 성능 저하(>30%)가 감지되어 이전 파라미터로 자동 롤백되었습니다.",
                    title="Smart PID 자동 롤백 알림",
                    notification_id=f"{DOMAIN}_rollback_{self.entry.entry_id}",
                )
            except Exception:
                pass
            self._notify_subscribers()
            return True
        return False

    async def async_set_tuning_mode(self, mode: str) -> None:
        """Update tuning_mode live without full config entry reload."""
        if mode not in TUNING_MODES:
            raise ValueError(f"Unsupported tuning mode: {mode}")
        if mode == self.tuning_mode:
            return
        self.tuning_mode = mode
        if mode != TUNING_MODE_AUTO:
            self._applied_auto_params = None
            if self.snapshot.safety.applied_params is not None:
                self.snapshot.safety.applied_params = None
                self._dirty = True
                self.store.async_delay_save(self.snapshot.to_dict, delay=600)
        new_options = dict(self.entry.options)
        new_options[CONF_TUNING_MODE] = mode
        al_sec = new_options.get("adaptive_learning")
        if isinstance(al_sec, Mapping):
            new_options["adaptive_learning"] = {**al_sec, CONF_TUNING_MODE: mode}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._notify_subscribers()

    async def async_set_prediction_mode(self, mode: str) -> None:
        """Update SIMC prediction_mode live without full config entry reload."""
        if mode not in PREDICTION_MODES:
            raise ValueError(f"Unsupported prediction mode: {mode}")
        if mode == self.prediction_mode:
            return
        self.prediction_mode = mode
        self.recommended = self._recompute_recommended()
        if self.tuning_mode == TUNING_MODE_AUTO:
            stage, _ = self.get_confidence()
            if (
                stage in (ConfidenceStage.TUNED, ConfidenceStage.OPTIMIZED)
                and not _same_pid_gains(self.recommended, self._applied_auto_params)
            ):
                can_apply, reason = self.snapshot.safety.can_auto_apply(
                    self._active_rls().sample_count,
                    self.snapshot.coasting.accepted_count,
                )
                if can_apply:
                    prev = (
                        self._applied_auto_params
                        if self._applied_auto_params is not None
                        else self.baseline
                    )
                    base_mae = self.snapshot.safety.mean_absolute_error
                    self.snapshot.safety.begin_application(
                        prev_params=prev,
                        baseline_mae=max(0.15, base_mae) if base_mae is not None else 0.3,
                        baseline_overshoot=0.4,
                        applied_params=self.recommended,
                    )
                    self._applied_auto_params = self.recommended
                    self.fallback_reason = None
                    self._dirty = True
                    self.store.async_delay_save(self.snapshot.to_dict, delay=600)
                elif self._applied_auto_params is None:
                    self.fallback_reason = reason
        new_options = dict(self.entry.options)
        new_options[CONF_PREDICTION_MODE] = mode
        al_sec = new_options.get("adaptive_learning")
        if isinstance(al_sec, Mapping):
            new_options["adaptive_learning"] = {**al_sec, CONF_PREDICTION_MODE: mode}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._notify_subscribers()

    def get_diagnostics_data(self) -> dict[str, Any]:
        """Return structured diagnostics snapshot for HA Download Diagnostics."""
        stage, pct = self.get_confidence()
        tau_em, coast_win, peak_delta = self._resolve_coasting_settings()
        outdoor_corr, outdoor_status = self.get_outdoor_coast_status()
        coasting = self.snapshot.coasting
        return {
            "entry": {
                "entry_id": self.entry.entry_id,
                "title": self.entry.title,
                "version": self.entry.version,
                "thermostat_type": self.thermostat_type,
                "is_humidity": self.is_humidity,
            },
            "runtime_config": {
                "tuning_mode": self.tuning_mode,
                "prediction_mode": self.prediction_mode,
                "inertia_profile": self.inertia_profile,
                "tau_em_min": tau_em,
                "max_coast_window_min": coast_win,
                "peak_confirm_delta": peak_delta,
                "solar_guard_enabled": self.solar_guard_enabled,
                "sunrise_saving_enabled": self.sunrise_saving_enabled,
                "sunrise_saving_active": self.is_sunrise_saving_active(),
                "sunrise_saving_start": self.sunrise_saving_start.strftime("%H:%M:%S"),
                "sunrise_saving_end": self.sunrise_saving_end.strftime("%H:%M:%S"),
                "predictive_on_enabled": self.predictive_on_enabled,
                "aux_temp_sensor": self.aux_temp_sensor,
                "coupled_dehumidifier": self.coupled_dehumidifier,
                "coupled_ac": self.coupled_ac,
                "manual_override_min": self.manual_override_min,
                "max_run_min": self.max_run_min,
                "safety_rest_min": self.safety_rest_min,
            },
            "confidence": {
                "stage": stage.value,
                "percent": pct,
                "coast_confidence": coasting.get_coast_confidence(),
            },
            "outdoor_coast_correction": outdoor_corr,
            "outdoor_band_status": outdoor_status,
            "response_delay_min": (
                round(coasting.response_delay_min, 2)
                if coasting.response_delay_min is not None
                else None
            ),
            "response_delay_samples": coasting.response_delay_samples,
            "passive_drift_rate": round(coasting.passive_drift_rate, 5),
            "predictive_on_enabled": self.predictive_on_enabled,
            "predictive_on_margin": self.get_predictive_on_margin(),
            "dual_layer_memory": coasting.get_memory_summary(time.time()),
            "coast_bias": round(coasting.coast_bias, 4),
            "coast_bias_samples": coasting.coast_bias_samples,
            "shortfall_margin_scale": round(coasting.shortfall_margin_scale, 4),
            "consecutive_shortfalls": coasting.consecutive_shortfalls,
            "recommended": self.recommended.to_dict(),
            "applied_auto_params": (
                self._applied_auto_params.to_dict() if self._applied_auto_params is not None else None
            ),
            "fallback_reason": self.fallback_reason,
            "mean_absolute_error": self.snapshot.safety.mean_absolute_error,
            "last_signed_coast_error": self.snapshot.safety.last_signed_coast_error,
            "snapshot": self.snapshot.to_dict(),
        }

    async def async_reset_learning(self) -> None:
        """Reset all learned models, coasting history, and safety snapshots."""
        tau_em, coast_win, peak_delta = self._resolve_coasting_settings()
        self._reset_generation += 1
        self._snapshot_generation += 1
        self.snapshot = LearningStateSnapshot(
            inertia_profile=self.inertia_profile,
            tau_em_min=tau_em,
            max_coast_window_min=coast_win,
            peak_confirm_delta=peak_delta,
            learning_unit=self.learning_unit,
        )
        self.recommended = self._recompute_recommended()
        self._applied_auto_params = None
        self.fallback_reason = None
        self._block_minutes = 0
        self._block_start_val = None
        self._block_end_val = None
        self._block_u_sum = 0.0
        self._block_out_diff_sum = 0.0
        self._block_aux_heat_sum = 0.0
        self._block_contaminated = False
        self._outage_buffered_ticks.clear()
        self._outage_pre_active = None
        self._device_unavailable_since = None
        self._dirty = False
        await self.store.async_save(self.snapshot)
        self._notify_subscribers()

    def get_bootstrap_defaults(self) -> dict[str, Any]:
        """Resolve configured sensor, device, and ventilation entity IDs for bootstrapping."""
        control_mode = str(self._get_setting(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE))
        t_type = self.thermostat_type
        target_dev = self._get_setting(CONF_TARGET_DEVICE, None)
        target_climate = self._get_setting(CONF_TARGET_CLIMATE, None)
        dev_cool = self._get_setting(CONF_TARGET_DEVICE_COOL, None)
        dev_heat = self._get_setting(CONF_TARGET_DEVICE_HEAT, None)
        dev_hum = self._get_setting(CONF_TARGET_DEVICE_HUMIDITY, None)
        dev_dehum = self._get_setting(CONF_TARGET_DEVICE_DEHUMIDITY, None)

        if t_type == THERMOSTAT_TYPE_HEAT_COOL:
            primary_dev = dev_heat or dev_cool or target_dev or target_climate
        elif t_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
            primary_dev = dev_hum or dev_dehum or target_dev or target_climate
        elif t_type == THERMOSTAT_TYPE_HEATER:
            primary_dev = target_dev or target_climate or dev_heat
        elif t_type == THERMOSTAT_TYPE_HUMIDIFIER:
            primary_dev = target_dev or target_climate or dev_hum
        else:
            primary_dev = target_dev or target_climate or dev_cool or dev_dehum

        raw_vent_sensors = self._get_setting(CONF_VENT_SENSORS, [])
        if isinstance(raw_vent_sensors, str):
            vent_sensors = [raw_vent_sensors] if raw_vent_sensors.strip() else []
        elif isinstance(raw_vent_sensors, (list, tuple, set)):
            vent_sensors = [str(s) for s in raw_vent_sensors if s]
        else:
            vent_sensors = []

        raw_debounce = self._get_setting(CONF_VENT_DEBOUNCE_SEC, DEFAULT_VENT_DEBOUNCE_SEC)
        vent_debounce_sec = max(
            0, duration_to_seconds(raw_debounce, DEFAULT_VENT_DEBOUNCE_SEC)
        )

        raw_cooldown = self._get_setting(CONF_VENT_COOLDOWN_MIN, DEFAULT_VENT_COOLDOWN_MIN)
        if isinstance(raw_cooldown, Mapping):
            vent_cooldown_min = max(
                0, duration_to_seconds(raw_cooldown, DEFAULT_VENT_COOLDOWN_MIN * 60) // 60
            )
        elif raw_cooldown in (None, ""):
            vent_cooldown_min = DEFAULT_VENT_COOLDOWN_MIN
        else:
            try:
                vent_cooldown_min = max(0, int(float(raw_cooldown)))
            except (ValueError, TypeError):
                vent_cooldown_min = DEFAULT_VENT_COOLDOWN_MIN

        return {
            "control_mode": control_mode,
            "thermostat_type": t_type,
            "is_humidity": self.is_humidity,
            "is_heating_default": t_type in (THERMOSTAT_TYPE_HEATER, THERMOSTAT_TYPE_HUMIDIFIER),
            "indoor_sensor": self._get_setting(CONF_INDOOR_SENSOR, None) or None,
            "outdoor_sensor": self._get_setting(CONF_OUTDOOR_SENSOR, None) or None,
            "aux_temp_sensor": self.aux_temp_sensor,
            "device_entity_id": primary_dev or None,
            "device_cool_entity_id": dev_cool or None,
            "device_heat_entity_id": dev_heat or None,
            "device_humidity_entity_id": dev_hum or None,
            "device_dehumidity_entity_id": dev_dehum or None,
            "vent_mode": str(self._get_setting(CONF_VENT_MODE, DEFAULT_VENT_MODE)),
            "vent_sensors": vent_sensors,
            "vent_logic": str(self._get_setting(CONF_VENT_LOGIC, DEFAULT_VENT_LOGIC)),
            "vent_switch": self._get_setting(CONF_VENT_SWITCH, None) or None,
            "vent_debounce_sec": vent_debounce_sec,
            "vent_cooldown_min": vent_cooldown_min,
        }

    async def async_apply_bootstrapped_snapshot(
        self,
        snapshot: LearningStateSnapshot,
        *,
        expected_snapshot_generation: int | None = None,
        expected_reset_generation: int | None = None,
        expected_apply_generation: int | None = None,
    ) -> None:
        """Atomically replace learning snapshot from historical bootstrap, persist, and notify."""
        if not self.is_active_runtime():
            raise HomeAssistantError("Runtime is unloaded or no longer active.")
        if (
            expected_reset_generation is not None
            and self._reset_generation != expected_reset_generation
        ):
            raise HomeAssistantError("Learning state was reset while bootstrap was running.")
        if (
            expected_apply_generation is not None
            and self._apply_generation != expected_apply_generation
        ):
            raise HomeAssistantError("Another bootstrap snapshot was already applied while running.")
        if (
            expected_snapshot_generation is not None
            and self._snapshot_generation != expected_snapshot_generation
        ):
            raise HomeAssistantError("Learning snapshot changed while bootstrap was running.")
        if snapshot.learning_unit is not None and snapshot.learning_unit != self.learning_unit:
            raise HomeAssistantError(
                f"Bootstrapped snapshot unit {snapshot.learning_unit!r} does not match runtime unit {self.learning_unit!r}."
            )
        snapshot.learning_unit = self.learning_unit

        tau_em, coast_win, peak_delta = self._resolve_coasting_settings()
        snapshot.coasting.apply_profile_settings(
            self.inertia_profile,
            tau_em_min=tau_em,
            max_coast_window_min=coast_win,
            peak_confirm_delta=peak_delta,
        )
        self._snapshot_generation += 1
        self._apply_generation += 1
        self.snapshot = snapshot
        self.recommended = self._recompute_recommended()
        self._applied_auto_params = snapshot.safety.applied_params
        self.fallback_reason = None
        self._block_minutes = 0
        self._block_start_val = None
        self._block_end_val = None
        self._block_u_sum = 0.0
        self._block_out_diff_sum = 0.0
        self._block_aux_heat_sum = 0.0
        self._block_contaminated = False
        self._outage_buffered_ticks.clear()
        self._outage_pre_active = None
        self._device_unavailable_since = None
        self._dirty = False
        await self.store.async_save(self.snapshot)
        if not self.is_active_runtime():
            return
        self._notify_subscribers()

    async def _async_recorder_bootstrap(self) -> None:
        """Optionally estimate initial thermal/moisture slope from Recorder history."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states
        except ImportError:
            _LOGGER.debug("Recorder component not available; skipping bootstrap")
            return

        indoor_id = self._get_setting("indoor_sensor")
        if not indoor_id:
            return

        start_gen = self._snapshot_generation
        start_reset_gen = self._reset_generation
        end_time = dt_util.utcnow()
        start_time = end_time - timedelta(days=3)
        try:
            rec = get_instance(self.hass)
            history = await rec.async_add_executor_job(
                get_significant_states,
                self.hass,
                start_time,
                end_time,
                [indoor_id],
            )
        except Exception as err:
            _LOGGER.debug("Recorder bootstrap skipped safely: %s", err)
            return

        if (
            not self.is_active_runtime()
            or self._snapshot_generation != start_gen
            or self._reset_generation != start_reset_gen
        ):
            return

        states = (history or {}).get(indoor_id, []) if isinstance(history, Mapping) else []
        if len(states) < 2:
            return

        rls = self._active_rls()
        updated_rls = False
        for prev_st, curr_st in zip(states, states[1:]):
            try:
                v0 = float(prev_st.state)
                v1 = float(curr_st.state)
                if not (math.isfinite(v0) and math.isfinite(v1)):
                    continue
                dt_min = max(1.0, (curr_st.last_changed - prev_st.last_changed).total_seconds() / 60.0)
                if dt_min <= 15.0:
                    dy = (v1 - v0) / dt_min
                    if abs(dy) <= 0.2:
                        rls.update([0.5, 0.0] if self.is_humidity else [0.5, 0.0, 0.0], dy)
                        updated_rls = True
            except (ValueError, TypeError, AttributeError):
                continue

        if updated_rls:
            self._snapshot_generation += 1
        self.recommended = self._recompute_recommended(rls)
        self._dirty = True
        self._notify_subscribers()
