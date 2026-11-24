"""Diagnostic sensor platform for Smart PID Air Conditioner."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_utils import SmartPIDRuntimeEntityMixin
from .learner import CyclePhase
from .runtime import SmartPIDRuntime
from .ventilation import VentState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up diagnostic sensors for Smart PID Air Conditioner."""
    runtime: SmartPIDRuntime = entry.runtime_data
    async_add_entities(
        [
            SmartPIDConfidenceSensor(entry, runtime),
            SmartPIDCyclePhaseSensor(entry, runtime),
            SmartPIDPredictedCoastSensor(entry, runtime),
            SmartPIDRecommendedTuningSensor(entry, runtime),
            SmartPIDVentilationStateSensor(entry, runtime),
        ],
        True,
    )


class _BaseSmartPIDDiagnosticSensor(SmartPIDRuntimeEntityMixin, SensorEntity):
    """Base diagnostic sensor subscribed to SmartPIDRuntime."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime, key: str) -> None:
        self._init_runtime_entity(entry, runtime, key)


class SmartPIDConfidenceSensor(_BaseSmartPIDDiagnosticSensor):
    """Learning confidence (%) and stage diagnostic sensor."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "learning_confidence")

    @property
    def native_value(self) -> float:
        _, pct = self._runtime.get_confidence()
        return pct

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stage, _ = self._runtime.get_confidence()
        safety = self._runtime.snapshot.safety
        return {
            "stage": stage.value,
            "rls_samples": self._runtime._active_rls().sample_count,
            "rejected_outliers": self._runtime._active_rls().rejected_outliers,
            "tuning_mode": self._runtime.tuning_mode,
            "mean_absolute_error": safety.mean_absolute_error,
            "comparison_count": safety.accuracy_count,
            "last_predicted_coast": safety.last_predicted_coast,
            "last_observed_coast": safety.last_observed_coast,
            "last_coast_error": safety.last_coast_error,
        }


class SmartPIDCyclePhaseSensor(_BaseSmartPIDDiagnosticSensor):
    """Cycle phase diagnostic sensor (idle/active/post_stop_coast/passive_drift)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [phase.value for phase in CyclePhase]

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "cycle_phase")

    @property
    def native_value(self) -> str:
        return self._runtime.snapshot.coasting.phase.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self._runtime.snapshot.coasting
        tau_em, coast_win, _ = self._runtime._resolve_coasting_settings()
        return {
            "accepted_count": c.accepted_count,
            "rejected_counts": dict(c.rejected_counts),
            "last_quality_reason": c.last_quality_reason,
            "inertia_profile": c.inertia_profile,
            "tau_em_min": tau_em,
            "max_coast_window_min": coast_win,
        }


class SmartPIDPredictedCoastSensor(_BaseSmartPIDDiagnosticSensor):
    """Predicted coasting excursion diagnostic sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "predicted_coast")
        if runtime.is_humidity:
            self._attr_native_unit_of_measurement = "g/m³" if runtime.aux_temp_sensor else PERCENTAGE
        else:
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        return round(self._runtime.get_predicted_coast(), 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        corr, status = self._runtime.get_outdoor_coast_status()
        return {
            "outdoor_coast_correction": corr,
            "outdoor_band_status": status,
            "coast_confidence": self._runtime.snapshot.coasting.get_coast_confidence(),
        }


class SmartPIDRecommendedTuningSensor(_BaseSmartPIDDiagnosticSensor):
    """Recommended Kp (state) and Ki/Ke/learned model attributes."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "recommended_tuning")

    @property
    def native_value(self) -> float:
        return round(self._runtime.recommended.kp, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rec = self._runtime.recommended
        return {
            "recommended_kp": rec.kp,
            "recommended_ki": rec.ki,
            "recommended_ke": rec.ke,
            "learned_rate": rec.learned_rate,
            "learned_loss": rec.learned_loss,
            "fallback_reason": self._runtime.fallback_reason,
        }


class SmartPIDVentilationStateSensor(_BaseSmartPIDDiagnosticSensor):
    """Ventilation state diagnostic sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [state.value for state in VentState]

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "ventilation_state")

    @property
    def native_value(self) -> str:
        return self._runtime._obs_vent_status.state.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._runtime._obs_vent_status
        return {
            "cooldown_remaining_seconds": st.cooldown_remaining_sec,
            "should_pause_control": st.should_pause_control,
            "should_block_learning": st.should_block_learning,
            "degraded": st.degraded,
        }
