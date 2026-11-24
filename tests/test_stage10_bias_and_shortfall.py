"""Stage 10 tests: Signed coasting prediction bias and early-stop shortfall defense."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homeassistant.config_entries import ConfigEntry

from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONTROL_MODE_SWITCH_PWM,
    THERMOSTAT_TYPE_HEATER,
    TUNING_MODE_AUTO,
)
from custom_components.smart_pid_airconditioner.learner import (
    COAST_BIAS_CLAMP,
    DynamicCoastingLearner,
    INERTIA_HIGH_INERTIA,
    SHORTFALL_DECAY_FACTOR,
    SHORTFALL_MIN_SCALE,
    SHORTFALL_RECOVERY_STEP,
    TuningSafetyManager,
)
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.sensor import (
    SmartPIDConfidenceSensor,
    SmartPIDPredictedCoastSensor,
)
from custom_components.smart_pid_airconditioner.storage import LearningStateSnapshot
from custom_components.smart_pid_airconditioner.ventilation import VentState, VentStatus


def _run_complete_cycle(
    learner: DynamicCoastingLearner,
    *,
    is_heating: bool = True,
    start_val: float = 20.0,
    stop_val: float = 21.0,
    peak_val: float = 21.8,
    target_val: float | None = 22.0,
    early_stop_active: bool = False,
    t0: float = 1_700_000_000.0,
) -> dict | None:
    """Drive a clean ACTIVE -> POST_STOP_COAST -> PASSIVE_DRIFT cycle with 2-step peak confirm."""
    # Active phase (6 minutes)
    for m in range(6):
        val = start_val + (stop_val - start_val) * (m / 5.0)
        learner.step_cycle(
            is_active=True,
            u_effort=1.0,
            value=val,
            is_heating=is_heating,
            dt_min=1.0,
            target_val=target_val,
            now_ts=t0 + m * 60.0,
            early_stop_active=False,
        )

    # Stop & coast to peak (3 minutes)
    for m in range(3):
        val = stop_val + (peak_val - stop_val) * ((m + 1) / 3.0)
        learner.step_cycle(
            is_active=False,
            u_effort=0.0,
            value=val,
            is_heating=is_heating,
            dt_min=1.0,
            target_val=target_val,
            now_ts=t0 + (6 + m) * 60.0,
            early_stop_active=early_stop_active if m == 0 else False,
        )

    # 2 consecutive reversal steps >= peak_confirm_delta to confirm peak cleanly
    delta = learner.peak_confirm_delta + 0.03
    step1 = peak_val - delta if is_heating else peak_val + delta
    step2 = peak_val - 2.0 * delta if is_heating else peak_val + 2.0 * delta
    for idx, rev_val in enumerate((step1, step2)):
        learner.step_cycle(
            is_active=False,
            u_effort=0.0,
            value=rev_val,
            is_heating=is_heating,
            dt_min=1.0,
            target_val=target_val,
            now_ts=t0 + (9 + idx) * 60.0,
            early_stop_active=False,
        )
    return learner.consume_completed_cycle()


def test_signed_coast_bias_convergence_and_clamping() -> None:
    """1) Persistent under-/over-prediction converges coast_bias in the right sign and clamps to [-0.3, +0.3]."""
    learner = DynamicCoastingLearner(
        inertia_profile="fast_air",
        tau_em_min=10.0,
        max_coast_window_min=30.0,
        peak_confirm_delta=0.05,
    )
    t = 1_700_000_000.0

    # Persistent under-prediction: force tau_coast low so raw_pred < obs_coast (0.9)
    for i in range(6):
        learner.tau_coast = 1.0
        learner.tau_coast_memory.current = None
        learner.tau_coast_memory.long_term = None
        completed = _run_complete_cycle(
            learner,
            is_heating=True,
            start_val=20.0,
            stop_val=21.0,
            peak_val=21.9,  # obs_coast = 0.9, slope = 0.2 -> raw_pred = 0.2 -> signed_err = +0.7
            target_val=21.8,
            t0=t + i * 3600.0,
        )
        assert completed is not None
        assert completed["signed_coast_error"] > 0.0

    assert learner.coast_bias_samples == 6
    assert learner.coast_bias > 0.0
    assert learner.coast_bias <= COAST_BIAS_CLAMP

    # Bias should only be added when apply_bias=True and coast_bias_samples >= 3
    raw_pred = learner.predict_coast(abs_slope=0.2, apply_bias=False)
    biased_pred = learner.predict_coast(abs_slope=0.2, apply_bias=True)
    assert biased_pred == pytest.approx(raw_pred + learner.coast_bias, abs=1e-4)

    # Persistent over-prediction drives coast_bias negative and clamps at -COAST_BIAS_CLAMP
    for i in range(25):
        learner.tau_coast = 10.0
        learner.tau_coast_memory.current = None
        learner.tau_coast_memory.long_term = None
        _run_complete_cycle(
            learner,
            is_heating=True,
            start_val=20.0,
            stop_val=21.0,
            peak_val=21.1,  # obs_coast = 0.1, slope = 0.2 -> raw_pred = 2.0 -> signed_err = -1.9
            target_val=21.0,
            t0=t + (10 + i) * 3600.0,
        )

    assert learner.coast_bias == pytest.approx(-COAST_BIAS_CLAMP)
    assert -COAST_BIAS_CLAMP <= learner.coast_bias <= COAST_BIAS_CLAMP


def test_early_stop_shortfall_decay_floor_and_recovery() -> None:
    """2) Early-stop shortfalls decay shortfall_margin_scale down to 0.5 and recover on target arrival."""
    learner = DynamicCoastingLearner(
        inertia_profile="fast_air",
        tau_em_min=10.0,
        max_coast_window_min=30.0,
        peak_confirm_delta=0.05,
    )
    t = 1_700_000_000.0

    # First early-stop shortfall: target=22.0, peak=21.5 -> shortfall=0.5 > 0.15
    completed = _run_complete_cycle(
        learner,
        is_heating=True,
        start_val=20.0,
        stop_val=21.2,
        peak_val=21.5,
        target_val=22.0,
        early_stop_active=True,
        t0=t,
    )
    assert completed is not None
    assert completed["stopped_by_early_stop"] is True
    assert completed["shortfall"] == pytest.approx(0.5, abs=1e-3)
    assert learner.consecutive_shortfalls == 1
    assert learner.shortfall_margin_scale == pytest.approx(SHORTFALL_DECAY_FACTOR, abs=1e-4)

    # Repeated early-stop shortfalls floor at SHORTFALL_MIN_SCALE (0.5)
    for i in range(1, 10):
        _run_complete_cycle(
            learner,
            is_heating=True,
            start_val=20.0,
            stop_val=21.2,
            peak_val=21.5,
            target_val=22.0,
            early_stop_active=True,
            t0=t + i * 3600.0,
        )
    assert learner.consecutive_shortfalls == 10
    assert learner.shortfall_margin_scale == pytest.approx(SHORTFALL_MIN_SCALE)

    # Cooling mode shortfall symmetry: target=24.0, peak=24.4 -> shortfall=0.4 > 0.15
    learner_cool = DynamicCoastingLearner(
        inertia_profile="fast_air",
        tau_em_min=10.0,
        max_coast_window_min=30.0,
        peak_confirm_delta=0.05,
    )
    c_cool = _run_complete_cycle(
        learner_cool,
        is_heating=False,
        start_val=26.0,
        stop_val=24.8,
        peak_val=24.4,
        target_val=24.0,
        early_stop_active=True,
        t0=t,
    )
    assert c_cool is not None
    assert c_cool["shortfall"] == pytest.approx(0.4, abs=1e-3)
    assert learner_cool.shortfall_margin_scale == pytest.approx(SHORTFALL_DECAY_FACTOR, abs=1e-4)

    # Normal target arrival (shortfall <= 0.15) resets consecutive_shortfalls and recovers by +0.05
    _run_complete_cycle(
        learner,
        is_heating=True,
        start_val=20.0,
        stop_val=21.5,
        peak_val=22.0,
        target_val=22.0,
        early_stop_active=True,
        t0=t + 20 * 3600.0,
    )
    assert learner.consecutive_shortfalls == 0
    assert learner.shortfall_margin_scale == pytest.approx(
        SHORTFALL_MIN_SCALE + SHORTFALL_RECOVERY_STEP, abs=1e-4
    )

    # Continued successful arrivals cap cleanly at 1.0
    for i in range(15):
        _run_complete_cycle(
            learner,
            is_heating=True,
            start_val=20.0,
            stop_val=21.5,
            peak_val=22.05,
            target_val=22.0,
            early_stop_active=True,
            t0=t + (25 + i) * 3600.0,
        )
    assert learner.shortfall_margin_scale == pytest.approx(1.0)


def test_normal_stop_and_interrupted_cycle_do_not_corrupt_bias_or_shortfall() -> None:
    """3) Normal stops (early_stop_active=False) and interrupted cycles must not corrupt shortfall or bias."""
    learner = DynamicCoastingLearner(
        inertia_profile="fast_air",
        tau_em_min=10.0,
        max_coast_window_min=30.0,
        peak_confirm_delta=0.05,
    )
    t = 1_700_000_000.0

    # Normal stop with shortfall > 0.15 must NOT decay shortfall_margin_scale
    completed = _run_complete_cycle(
        learner,
        is_heating=True,
        start_val=20.0,
        stop_val=21.0,
        peak_val=21.4,
        target_val=22.0,
        early_stop_active=False,
        t0=t,
    )
    assert completed is not None
    assert learner.shortfall_margin_scale == 1.0
    assert learner.consecutive_shortfalls == 0
    bias_before = learner.coast_bias
    bias_samples_before = learner.coast_bias_samples

    # Interrupted cycle (device restarts during POST_STOP_COAST) must NOT update coast_bias or shortfall
    for m in range(6):
        learner.step_cycle(
            is_active=True,
            u_effort=1.0,
            value=20.0 + m * 0.2,
            is_heating=True,
            dt_min=1.0,
            target_val=22.0,
            now_ts=t + 3600.0 + m * 60.0,
            early_stop_active=False,
        )
    # Enter POST_STOP_COAST with early_stop_active=True
    learner.step_cycle(
        is_active=False,
        u_effort=0.0,
        value=21.1,
        is_heating=True,
        dt_min=1.0,
        target_val=22.0,
        now_ts=t + 3600.0 + 6 * 60.0,
        early_stop_active=True,
    )
    # Immediately restart device -> triggers _finalize_coast(interrupted=True)
    learner.step_cycle(
        is_active=True,
        u_effort=1.0,
        value=21.1,
        is_heating=True,
        dt_min=1.0,
        target_val=22.0,
        now_ts=t + 3600.0 + 7 * 60.0,
        early_stop_active=False,
    )
    assert learner.coast_bias == bias_before
    assert learner.coast_bias_samples == bias_samples_before
    assert learner.shortfall_margin_scale == 1.0
    assert learner.consecutive_shortfalls == 0


async def test_runtime_sensors_diagnostics_and_backward_compat(hass) -> None:
    """4) Verify runtime prediction split, sensor attributes, diagnostics, serialization, and translations."""
    entry = ConfigEntry(
        entry_id="stage10_entry",
        title="Stage10 Boiler",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_TARGET_DEVICE: "switch.boiler_stage10",
            CONF_INDOOR_SENSOR: "sensor.indoor_stage10",
            CONF_INERTIA_PROFILE: INERTIA_HIGH_INERTIA,
            CONF_TUNING_MODE: TUNING_MODE_AUTO,
        },
        options={},
    )
    runtime = SmartPIDRuntime(hass, entry)

    # Populate enough samples so confidence >= TUNED
    coasting = runtime.snapshot.coasting
    coasting.k_afterglow = 1.6
    coasting.emitter_charge = 0.5  # base_pred = 0.8
    for _ in range(15):
        coasting.history.append({"obs_coast": 0.8, "coast_minutes": 10.0})
    coasting.accepted_count = 15
    coasting.coast_bias = 0.1
    coasting.coast_bias_samples = 5
    coasting.shortfall_margin_scale = 0.7
    coasting.consecutive_shortfalls = 2
    runtime._active_rls().sample_count = 500

    # get_predicted_coast applies bias (0.8 + 0.1 = 0.9) without shortfall_margin_scale
    pred_coast = runtime.get_predicted_coast()
    assert pred_coast == pytest.approx(0.9, abs=1e-3)

    # get_early_stop_prediction applies both bias and shortfall_margin_scale ((0.8 + 0.1) * 0.7 = 0.63)
    early_stop_pred = runtime.get_early_stop_prediction()
    assert early_stop_pred == pytest.approx(0.63, abs=1e-3)

    # Record signed error in TuningSafetyManager
    runtime.snapshot.safety.record_prediction_comparison(predicted_coast=0.9, observed_coast=0.65)
    assert runtime.snapshot.safety.last_signed_coast_error == pytest.approx(-0.25, abs=1e-4)

    # Sensor attributes
    conf_sensor = SmartPIDConfidenceSensor(entry, runtime)
    assert conf_sensor.extra_state_attributes["last_signed_coast_error"] == pytest.approx(-0.25, abs=1e-4)

    coast_sensor = SmartPIDPredictedCoastSensor(entry, runtime)
    coast_attrs = coast_sensor.extra_state_attributes
    assert coast_attrs["coast_bias"] == pytest.approx(0.1, abs=1e-4)
    assert coast_attrs["shortfall_margin_scale"] == pytest.approx(0.7, abs=1e-4)
    assert coast_attrs["consecutive_shortfalls"] == 2

    # Diagnostics payload
    diag = runtime.get_diagnostics_data()
    assert diag["coast_bias"] == pytest.approx(0.1, abs=1e-4)
    assert diag["coast_bias_samples"] == 5
    assert diag["shortfall_margin_scale"] == pytest.approx(0.7, abs=1e-4)
    assert diag["consecutive_shortfalls"] == 2
    assert diag["last_signed_coast_error"] == pytest.approx(-0.25, abs=1e-4)

    # update_observation stores early_stop_active
    runtime.update_observation(
        indoor_val=21.5,
        outdoor_val=5.0,
        target_val=22.0,
        u_effort=0.0,
        is_active=False,
        is_heating=True,
        vent_status=VentStatus(VentState.NORMAL, False, False, 0, False),
        early_stop_active=True,
    )
    assert runtime._obs_early_stop_active is True

    # Serialization round-trip + legacy backward compatibility + clamping
    snap_dict = runtime.snapshot.to_dict()
    restored = LearningStateSnapshot.from_dict(snap_dict)
    assert restored.coasting.coast_bias == pytest.approx(0.1, abs=1e-4)
    assert restored.coasting.coast_bias_samples == 5
    assert restored.coasting.shortfall_margin_scale == pytest.approx(0.7, abs=1e-4)
    assert restored.coasting.consecutive_shortfalls == 2
    assert restored.safety.last_signed_coast_error == pytest.approx(-0.25, abs=1e-4)

    # Legacy snapshot without Stage 10 fields loads with safe defaults
    legacy = DynamicCoastingLearner.from_dict({"k_afterglow": 0.4, "accepted_count": 3})
    assert legacy.coast_bias == 0.0
    assert legacy.coast_bias_samples == 0
    assert legacy.shortfall_margin_scale == 1.0
    assert legacy.consecutive_shortfalls == 0

    legacy_safety = TuningSafetyManager.from_dict({"comparison_count": 2})
    assert legacy_safety.last_signed_coast_error is None

    # Out-of-range values are clamped safely on load
    clamped = DynamicCoastingLearner.from_dict(
        {"coast_bias": 9.9, "shortfall_margin_scale": 0.01, "consecutive_shortfalls": -3}
    )
    assert clamped.coast_bias == pytest.approx(COAST_BIAS_CLAMP)
    assert clamped.shortfall_margin_scale == pytest.approx(SHORTFALL_MIN_SCALE)
    assert clamped.consecutive_shortfalls == 0

    # Translation key parity across strings.json, ko.json, en.json
    base = Path(__file__).resolve().parents[1] / "custom_components" / "smart_pid_airconditioner"
    for rel in ("strings.json", "translations/ko.json", "translations/en.json"):
        data = json.loads((base / rel).read_text(encoding="utf-8"))
        lc_attrs = data["entity"]["sensor"]["learning_confidence"]["state_attributes"]
        assert "last_signed_coast_error" in lc_attrs
        pc_attrs = data["entity"]["sensor"]["predicted_coast"]["state_attributes"]
        for key in ("coast_bias", "shortfall_margin_scale", "consecutive_shortfalls"):
            assert key in pc_attrs
