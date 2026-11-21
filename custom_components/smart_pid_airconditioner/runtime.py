"""Centralized runtime for Smart PID control observation, self-learning, and safety."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.util.dt as dt_util

from .const import (
    CONF_AUX_TEMP_SENSOR,
    CONF_COUPLED_AC,
    CONF_COUPLED_DEHUMIDIFIER,
    CONF_INERTIA_PROFILE,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MANUAL_OVERRIDE_MIN,
    CONF_MAX_RUN_MIN,
    CONF_PREDICTION_MODE,
    CONF_RECORDER_BOOTSTRAP,
    CONF_SAFETY_REST_MIN,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    DEFAULT_INERTIA_PROFILE,
    DEFAULT_KE,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MANUAL_OVERRIDE_MIN,
    DEFAULT_MAX_RUN_MIN,
    DEFAULT_PREDICTION_MODE,
    DEFAULT_RECORDER_BOOTSTRAP,
    DEFAULT_SAFETY_REST_MIN,
    DEFAULT_THERMOSTAT_TYPE,
    DEFAULT_TUNING_MODE,
    DOMAIN,
    TUNING_MODE_AUTO,
    TUNING_MODE_SHADOW,
    is_humidity_type,
)
from .learner import (
    ConfidenceStage,
    CyclePhase,
    SimcAutoTuner,
    TuningParameters,
)
from .psychrometrics import rh_to_ah
from .storage import LearningStateSnapshot, ModelStore
from .ventilation import VentState, VentStatus

_LOGGER = logging.getLogger(__name__)


class SmartPIDRuntime:
    """Shared runtime coordinating learning, storage, and diagnostic subscribers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store = ModelStore(hass, entry.entry_id)
        self.tuner = SimcAutoTuner()
        self.snapshot = LearningStateSnapshot(inertia_profile=self._get_setting(CONF_INERTIA_PROFILE, DEFAULT_INERTIA_PROFILE))

        self._subscribers: list[Callable[[], None]] = []
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._bootstrap_task: Any = None
        self._dirty: bool = False

        # Config cache
        self.tuning_mode: str = str(self._get_setting(CONF_TUNING_MODE, DEFAULT_TUNING_MODE))
        self.prediction_mode: str = str(self._get_setting(CONF_PREDICTION_MODE, DEFAULT_PREDICTION_MODE))
        self.inertia_profile: str = str(self._get_setting(CONF_INERTIA_PROFILE, DEFAULT_INERTIA_PROFILE))
        self.aux_temp_sensor: str | None = self._get_setting(CONF_AUX_TEMP_SENSOR, None) or None
        self.thermostat_type: str = str(self._get_setting(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE))
        self.is_humidity: bool = is_humidity_type(self.thermostat_type)

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
        self.recommended: TuningParameters = self.tuner.compute(
            self._active_rls(),
            prediction_mode=self.prediction_mode,
            baseline=self.baseline,
        )

        # Latest observation from climate / humidifier entity
        self._obs_indoor: float | None = None
        self._obs_outdoor: float | None = None
        self._obs_target: float | None = None
        self._obs_u_effort: float = 0.0
        self._obs_is_active: bool = False
        self._obs_is_heating: bool = False
        self._obs_vent_status: VentStatus = VentStatus(VentState.NORMAL, False, False, 0, False)
        self._obs_manual_override: bool = False

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

    def _get_setting(self, key: str, default: Any = None) -> Any:
        if key in self.entry.options and self.entry.options[key] is not None:
            return self.entry.options[key]
        for sec in ("adaptive_learning", "ki_group", "ventilation", "section_0"):
            d = self.entry.options.get(sec)
            if isinstance(d, dict) and key in d and d[key] is not None:
                return d[key]
        if key in self.entry.data and self.entry.data[key] is not None:
            return self.entry.data[key]
        for sec in ("adaptive_learning", "ki_group", "ventilation", "section_0"):
            d = self.entry.data.get(sec)
            if isinstance(d, dict) and key in d and d[key] is not None:
                return d[key]
        return default

    def _active_rls(self):
        return self.snapshot.hum_rls if self.is_humidity else self.snapshot.temp_rls

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

    async def async_setup(self) -> None:
        """Load persisted state and start 1-minute learning tick."""
        self.snapshot = await self.store.async_load(inertia_profile=self.inertia_profile)
        self.recommended = self.tuner.compute(
            self._active_rls(),
            prediction_mode=self.prediction_mode,
            baseline=self.baseline,
        )
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
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        if self._bootstrap_task is not None and not self._bootstrap_task.done():
            self._bootstrap_task.cancel()
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
    ) -> None:
        """Receive latest state observation from climate/humidifier entity."""
        self._obs_indoor = indoor_val
        self._obs_outdoor = outdoor_val
        self._obs_target = target_val
        self._obs_u_effort = max(0.0, min(1.0, float(u_effort)))
        self._obs_is_active = bool(is_active)
        self._obs_is_heating = bool(is_heating)
        self._obs_vent_status = vent_status
        self._obs_manual_override = bool(manual_override)

        if vent_status.should_block_learning or manual_override:
            reason = "manual_override" if manual_override else (
                "ventilation_degraded" if vent_status.degraded else "ventilation_contamination"
            )
            self.snapshot.coasting.mark_contaminated(reason)
            self._block_contaminated = True

        self._notify_subscribers()

    def _resolve_learning_values(self) -> tuple[float | None, float | None, str | None]:
        """Return (indoor_learning_val, outdoor_learning_val, fallback_reason)."""
        if self._obs_indoor is None:
            return None, None, self.fallback_reason

        if not self.is_humidity:
            return self._obs_indoor, self._obs_outdoor, None

        # Humidity type: try converting RH% to AH (g/m³) using aux_temp_sensor
        if self.aux_temp_sensor:
            st = self.hass.states.get(self.aux_temp_sensor)
            if st is not None and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                try:
                    temp_c = float(st.state)
                    ah_in = rh_to_ah(temp_c, self._obs_indoor)
                    ah_out = (
                        rh_to_ah(temp_c, self._obs_outdoor)
                        if self._obs_outdoor is not None
                        else None
                    )
                    return ah_in, ah_out, None
                except (ValueError, TypeError):
                    pass
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

    async def async_step_learning(self, now: datetime | None = None) -> None:
        """Advance learning by 1 minute (public for deterministic testing)."""
        val_in, val_out, fb_reason = self._resolve_learning_values()
        if fb_reason is not None:
            self.fallback_reason = fb_reason
        elif self.tuning_mode != TUNING_MODE_AUTO:
            self.fallback_reason = None

        if val_in is None:
            return

        blocked = self._obs_vent_status.should_block_learning or self._obs_manual_override
        block_reason = (
            "manual_override"
            if self._obs_manual_override
            else ("ventilation_degraded" if self._obs_vent_status.degraded else "ventilation_contamination")
        )

        # 1. Step 4-phase DynamicCoastingLearner (1-minute resolution)
        self.snapshot.coasting.step_cycle(
            is_active=self._obs_is_active,
            u_effort=self._obs_u_effort,
            value=val_in,
            is_heating=self._obs_is_heating,
            dt_min=1.0,
            blocked_for_learning=blocked,
            block_reason=block_reason,
        )

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

                self.recommended = self.tuner.compute(
                    rls,
                    prediction_mode=self.prediction_mode,
                    baseline=self.baseline,
                )
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
            self.snapshot.safety.begin_application(
                prev_params=prev,
                baseline_mae=0.3,
                baseline_overshoot=0.4,
            )
            self._applied_auto_params = self.recommended
            self._dirty = True

        self.fallback_reason = None
        return (
            self._applied_auto_params.kp,
            self._applied_auto_params.ki,
            self._applied_auto_params.ke,
            None,
        )

    def get_early_stop_prediction(self) -> float:
        """Return predicted coasting amount when adaptive_auto is active and tuned."""
        if self.tuning_mode != TUNING_MODE_AUTO:
            return 0.0
        stage, _ = self.get_confidence()
        if stage in (ConfidenceStage.COLLECTING, ConfidenceStage.STABLE):
            return 0.0
        return self.snapshot.coasting.predict_coast()

    def record_cycle_performance(self, cycle_mae: float, cycle_overshoot: float) -> bool:
        """Evaluate post-apply performance; returns True if auto-rollback occurred."""
        rolled_back = self.snapshot.safety.record_post_apply_cycle(
            cycle_mae=cycle_mae,
            cycle_overshoot=cycle_overshoot,
        )
        if rolled_back is not None:
            self._applied_auto_params = rolled_back
            self.fallback_reason = self.snapshot.safety.last_fallback_reason
            self._dirty = True
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

    async def async_reset_learning(self) -> None:
        """Reset all learned models, coasting history, and safety snapshots."""
        self.snapshot = LearningStateSnapshot(inertia_profile=self.inertia_profile)
        self.recommended = self.tuner.compute(
            self._active_rls(),
            prediction_mode=self.prediction_mode,
            baseline=self.baseline,
        )
        self._applied_auto_params = None
        self.fallback_reason = None
        self._block_minutes = 0
        self._block_start_val = None
        self._block_end_val = None
        self._block_u_sum = 0.0
        self._block_out_diff_sum = 0.0
        self._block_aux_heat_sum = 0.0
        self._block_contaminated = False
        self._dirty = False
        await self.store.async_save(self.snapshot)
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

        states = (history or {}).get(indoor_id, []) if isinstance(history, dict) else []
        if len(states) < 2:
            return

        rls = self._active_rls()
        for prev_st, curr_st in zip(states, states[1:]):
            try:
                v0 = float(prev_st.state)
                v1 = float(curr_st.state)
                dt_min = max(1.0, (curr_st.last_changed - prev_st.last_changed).total_seconds() / 60.0)
                if dt_min <= 15.0:
                    dy = (v1 - v0) / dt_min
                    if abs(dy) <= 0.2:
                        rls.update([0.5, 0.0] if self.is_humidity else [0.5, 0.0, 0.0], dy)
            except (ValueError, TypeError, AttributeError):
                continue

        self.recommended = self.tuner.compute(
            rls,
            prediction_mode=self.prediction_mode,
            baseline=self.baseline,
        )
        self._dirty = True
        self._notify_subscribers()
