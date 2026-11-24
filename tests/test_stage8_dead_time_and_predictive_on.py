"""Stage 8 tests: Measured dead time (θ) learning, SIMC injection, passive drift, and Predictive ON."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
import homeassistant.util.dt as dt_util

from custom_components.smart_pid_airconditioner import async_setup_entry, async_unload_entry
from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_DYNAMIC_MIN_OFF_MULTIPLIER,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_OUTDOOR_SENSOR,
    CONF_PREDICTIVE_ON_ENABLED,
    CONF_PWM_DURATION,
    CONF_SAMPLE_PERIOD,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONTROL_MODE_SWITCH_PWM,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    TUNING_MODE_AUTO,
)
from custom_components.smart_pid_airconditioner.learner import (
    CyclePhase,
    DynamicCoastingLearner,
    HuberRLS,
    INERTIA_HIGH_INERTIA,
    SimcAutoTuner,
    TuningParameters,
)
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.sensor import (
    SmartPIDCyclePhaseSensor,
    SmartPIDRecommendedTuningSensor,
)
from custom_components.smart_pid_airconditioner.storage import LearningStateSnapshot
from custom_components.smart_pid_airconditioner.switch import (
    SmartPIDPredictiveOnSwitch,
    async_setup_entry as async_setup_switch,
)


def _make_boiler_entry(entry_id: str = "stage8_boiler", **extra) -> ConfigEntry:
    data = {
        CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
        CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
        CONF_TARGET_DEVICE: "switch.boiler_stage8",
        CONF_INDOOR_SENSOR: "sensor.living_temp_stage8",
        CONF_OUTDOOR_SENSOR: "sensor.outdoor_temp_stage8",
        CONF_INITIAL_TARGET_TEMP: 22.0,
        CONF_KP: 20.0,
        CONF_KI: 0.5,
        CONF_MAX_I: 20.0,
        CONF_KD: 0.0,
        CONF_KE: 0.0,
        CONF_SAMPLE_PERIOD: 60,
        CONF_PWM_DURATION: 900,
        CONF_MIN_ON_TIME: 180,
        CONF_MIN_OFF_TIME: 180,
        CONF_DYNAMIC_MIN_OFF_MULTIPLIER: 1.0,
        CONF_INERTIA_PROFILE: INERTIA_HIGH_INERTIA,
        CONF_TUNING_MODE: TUNING_MODE_AUTO,
        CONF_PREDICTIVE_ON_ENABLED: False,
        **extra,
    }
    return ConfigEntry(entry_id=entry_id, title="Stage 8 Boiler", data=data, options={})


def test_dead_time_learning_and_dynamic_simc_injection() -> None:
    """1) Learn response_delay_min over 3 delayed heating cycles and verify SIMC Kp/Ti shift."""
    learner = DynamicCoastingLearner(inertia_profile=INERTIA_HIGH_INERTIA, ewma_alpha=0.25)
    rls = HuberRLS(n_features=3, initial_theta=[0.04, 0.004, 0.0])
    tuner = SimcAutoTuner()

    # Before 3 samples, effective dead time falls back to default 2.0 min
    assert learner.get_effective_dead_time() == pytest.approx(2.0)
    rec_before = tuner.compute(
        rls,
        prediction_mode="balanced",
        dead_time_min=learner.get_effective_dead_time(),
    )
    assert rec_before.dead_time_min == pytest.approx(2.0)

    # Simulate 3 delayed heating cycles where temperature stays flat for 10 min, then rises >= 0.1°C at min 11
    for _ in range(3):
        temp = 20.0
        # IDLE -> ACTIVE (min 1)
        learner.step_cycle(is_active=True, u_effort=1.0, value=temp, is_heating=True, dt_min=1.0)
        # Flat for minutes 2..10
        for _m in range(9):
            learner.step_cycle(is_active=True, u_effort=1.0, value=temp, is_heating=True, dt_min=1.0)
        assert not learner._response_detected
        # First >= 0.1°C rise at minute 11
        learner.step_cycle(is_active=True, u_effort=1.0, value=temp + 0.12, is_heating=True, dt_min=1.0)
        assert learner._response_detected
        # Abort mid-cycle to verify learned response_delay_min & samples are preserved
        learner.abort_cycle_to_idle()

    assert learner.response_delay_samples == 3
    assert learner.response_delay_min == pytest.approx(11.0, abs=0.05)
    assert learner.get_effective_dead_time() == pytest.approx(11.0, abs=0.05)

    # Dynamic SIMC injection with learned θ=11 min must produce lower Kp and larger Ti than θ=2 min
    rec_after = tuner.compute(
        rls,
        prediction_mode="balanced",
        dead_time_min=learner.get_effective_dead_time(),
    )
    assert rec_after.dead_time_min == pytest.approx(11.0, abs=0.05)
    assert rec_after.kp < rec_before.kp
    assert rec_after.ti_min > rec_before.ti_min


def test_passive_drift_learning_and_outdoor_band_preheat_drop() -> None:
    """2) Learn PASSIVE_DRIFT cooling rates per 5°C outdoor band and predict dead-time drop."""
    learner = DynamicCoastingLearner(inertia_profile=INERTIA_HIGH_INERTIA, ewma_alpha=0.25)
    # Seed 3 dead-time samples at 10.0 min
    learner.response_delay_min = 10.0
    learner.response_delay_samples = 3
    learner._cycle_direction_heating = True

    # Cold band (-5°C to 0°C, e.g. outdoor = -3°C -> band "-5"): fast drop 0.03°C/min
    for _ in range(3):
        learner.phase = CyclePhase.PASSIVE_DRIFT
        learner.emitter_charge = 0.4
        learner._reset_passive_drift_window(22.0)
        for m in range(1, 16):
            learner.step_cycle(
                is_active=False,
                u_effort=0.0,
                value=22.0 - 0.03 * m,
                is_heating=True,
                dt_min=1.0,
                outdoor_temp=-3.0,
                solar_rising_active=False,
            )

    # Mild band (5°C to 10°C, e.g. outdoor = 7°C -> band "5"): slower drop 0.012°C/min
    for _ in range(3):
        learner.phase = CyclePhase.PASSIVE_DRIFT
        learner.emitter_charge = 0.4
        learner._reset_passive_drift_window(22.0)
        for m in range(1, 16):
            learner.step_cycle(
                is_active=False,
                u_effort=0.0,
                value=22.0 - 0.012 * m,
                is_heating=True,
                dt_min=1.0,
                outdoor_temp=7.0,
                solar_rising_active=False,
            )

    # Solar rising active must NOT record passive drift
    prev_samples = learner.passive_drift_samples
    learner.phase = CyclePhase.PASSIVE_DRIFT
    learner.emitter_charge = 0.4
    learner._reset_passive_drift_window(22.0)
    for m in range(1, 17):
        learner.step_cycle(
            is_active=False,
            u_effort=0.0,
            value=22.0 - 0.05 * m,
            is_heating=True,
            dt_min=1.0,
            outdoor_temp=-3.0,
            solar_rising_active=True,
        )
    assert learner.passive_drift_samples == prev_samples

    drop_cold = learner.predict_preheat_drop(outdoor_temp=-2.0)
    drop_mild = learner.predict_preheat_drop(outdoor_temp=8.0)
    drop_untrained_band = learner.predict_preheat_drop(outdoor_temp=17.0)

    # Cold band: 0.03 * 10 = 0.30°C; Mild band: 0.012 * 10 = 0.12°C
    assert drop_cold == pytest.approx(0.30, abs=0.02)
    assert drop_mild == pytest.approx(0.12, abs=0.02)
    assert drop_cold > drop_mild
    # Untrained band falls back to overall passive_drift_rate
    assert drop_untrained_band > 0.0


async def test_predictive_on_switch_gating_sunrise_and_min_off_guards(hass) -> None:
    """3) Verify Predictive ON switch, confidence gating, sunrise block, and effective_min_off guard."""
    t0 = datetime(2026, 11, 24, 2, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(t0)

    hass.states.async_set("switch.boiler_stage8", "off")
    hass.states.async_set("sensor.living_temp_stage8", "22.2")  # error = 22.0 - 22.2 = -0.2°C (<= 0)
    hass.states.async_set("sensor.outdoor_temp_stage8", "-2.0")
    hass.states.async_set("sun.sun", "below_horizon")

    entry = _make_boiler_entry()
    assert await async_setup_entry(hass, entry)
    runtime: SmartPIDRuntime = entry.runtime_data

    # Verify switch platform registers both sunrise_saving and predictive_on for heater
    added_switches = []
    await async_setup_switch(hass, entry, lambda ents, _=False: added_switches.extend(ents))
    pred_switches = [e for e in added_switches if isinstance(e, SmartPIDPredictiveOnSwitch)]
    assert len(pred_switches) == 1
    pred_sw = pred_switches[0]
    assert pred_sw.is_on is False

    # Verify humidity controller does NOT register predictive_on switch
    hum_entry = ConfigEntry(
        entry_id="stage8_hum",
        title="Hum",
        data={CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDIFIER},
        options={},
    )
    hum_rt = SmartPIDRuntime(hass, hum_entry)
    hum_entry.runtime_data = hum_rt
    hum_switches = []
    await async_setup_switch(hass, hum_entry, lambda ents, _=False: hum_switches.extend(ents))
    assert not any(isinstance(e, SmartPIDPredictiveOnSwitch) for e in hum_switches)

    # Verify cooler DOES register predictive_on switch
    cool_entry = ConfigEntry(
        entry_id="stage8_cool",
        title="Cool",
        data={CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER},
        options={},
    )
    cool_rt = SmartPIDRuntime(hass, cool_entry)
    cool_entry.runtime_data = cool_rt
    cool_switches = []
    await async_setup_switch(hass, cool_entry, lambda ents, _=False: cool_switches.extend(ents))
    assert any(isinstance(e, SmartPIDPredictiveOnSwitch) for e in cool_switches)

    # Seed learned dead time & passive drift (0.04°C/min * 10 min = 0.40°C margin)
    coasting = runtime.snapshot.coasting
    coasting.response_delay_min = 10.0
    coasting.response_delay_samples = 4
    coasting.passive_drift_rate = 0.04
    coasting.passive_drift_samples = 4

    # Toggle Predictive ON switch ON
    await pred_sw.async_turn_on()
    assert runtime.predictive_on_enabled is True
    assert pred_sw.is_on is True
    assert pred_sw.extra_state_attributes["effective_dead_time_min"] == pytest.approx(10.0)
    assert pred_sw.extra_state_attributes["predicted_preheat_drop"] == pytest.approx(0.4)

    # Confidence still COLLECTING -> get_predictive_on_margin must return 0.0
    assert runtime.get_predictive_on_margin() == 0.0

    # Promote confidence to TUNED (rls_samples >= 20, accepted_count >= 10)
    runtime.snapshot.temp_rls.sample_count = 25
    coasting.accepted_count = 12
    assert runtime.get_predictive_on_margin() == pytest.approx(0.4)

    entity = SmartPIDThermostatEntity(hass, entry)
    entity._runtime = runtime
    entity._attr_hvac_mode = HVACMode.HEAT
    entity._target_temp = 22.0
    entity._is_heating = True
    entity._pwm_state = False

    # Case A: Sunrise Saving active -> Predictive ON MUST be blocked
    await runtime.async_set_sunrise_saving_enabled(True)
    hass.states.async_set("sun.sun", "above_horizon")
    await entity._async_calculate_and_control(now=t0, force=True)
    assert entity._pwm_state is False

    # Turn off sunrise saving
    await runtime.async_set_sunrise_saving_enabled(False)
    hass.states.async_set("sun.sun", "below_horizon")

    # Case B: effective_min_off active (last run 600s, multiplier 1.0 -> 600s min_off; only 100s elapsed)
    entity._last_run_duration_sec = 600.0
    entity._last_switch_off_time = t0 - timedelta(seconds=100)
    entity._last_switch_off_times["switch.boiler_stage8"] = t0 - timedelta(seconds=100)
    await entity._async_calculate_and_control(now=t0 + timedelta(seconds=10), force=True)
    assert entity._pwm_state is False  # Blocked by dynamic effective_min_off

    # Case C: After effective_min_off has elapsed, error = -0.2°C + margin 0.4°C = +0.2°C > 0 -> Predictive ON starts!
    t_ready = t0 + timedelta(seconds=700)
    dt_util.set_utcnow(t_ready)
    await entity._async_calculate_and_control(now=t_ready, force=True)
    assert entity._pwm_state is True

    # Case D: Switch turned OFF -> margin becomes 0.0
    await pred_sw.async_turn_off()
    assert runtime.predictive_on_enabled is False
    assert runtime.get_predictive_on_margin() == 0.0

    await async_unload_entry(hass, entry)


async def test_legacy_snapshot_compat_diagnostics_and_translations(hass) -> None:
    """4) Verify legacy snapshot from_dict backward compatibility, sensors/diagnostics, and translations."""
    # Legacy snapshot missing all Stage 8 fields
    legacy_payload = {
        "temp_rls": {"n_features": 3, "theta": [0.05, 0.005, 0.0], "sample_count": 22},
        "hum_rls": {"n_features": 2, "theta": [0.2, 0.01], "sample_count": 0},
        "coasting": {
            "inertia_profile": "high_inertia",
            "phase": "idle",
            "k_afterglow": 1.2,
            "accepted_count": 10,
            "history": [],
        },
        "safety": {"apply_count": 0},
    }
    snap = LearningStateSnapshot.from_dict(legacy_payload)
    assert snap.coasting.response_delay_min is None
    assert snap.coasting.response_delay_samples == 0
    assert snap.coasting.passive_drift_rate == 0.0
    assert snap.coasting.passive_drift_samples == 0
    assert snap.coasting.passive_drift_by_band == {}
    assert snap.coasting.get_effective_dead_time() == pytest.approx(2.0)
    assert snap.coasting.predict_preheat_drop(outdoor_temp=0.0) == 0.0

    # Roundtrip with populated Stage 8 fields
    snap.coasting.response_delay_min = 9.5
    snap.coasting.response_delay_samples = 5
    snap.coasting.passive_drift_rate = 0.025
    snap.coasting.passive_drift_samples = 4
    snap.coasting.passive_drift_by_band = {"-5": {"rate": 0.03, "samples": 3}}
    restored = LearningStateSnapshot.from_dict(snap.to_dict())
    assert restored.coasting.response_delay_min == pytest.approx(9.5)
    assert restored.coasting.response_delay_samples == 5
    assert restored.coasting.passive_drift_rate == pytest.approx(0.025)
    assert restored.coasting.passive_drift_by_band["-5"]["samples"] == 3

    # TuningParameters backward compatibility
    tp_legacy = TuningParameters.from_dict({"kp": 15.0, "ki": 0.4, "ke": 0.5})
    assert tp_legacy.dead_time_min == pytest.approx(2.0)

    # Diagnostic sensors & runtime.get_diagnostics_data()
    entry = _make_boiler_entry(entry_id="stage8_diag")
    rt = SmartPIDRuntime(hass, entry)
    rt.snapshot = restored
    rt.recommended = rt._recompute_recommended()

    cycle_sensor = SmartPIDCyclePhaseSensor(entry, rt)
    rec_sensor = SmartPIDRecommendedTuningSensor(entry, rt)
    assert cycle_sensor.extra_state_attributes["response_delay_min"] == pytest.approx(9.5)
    assert cycle_sensor.extra_state_attributes["response_delay_samples"] == 5
    assert rec_sensor.extra_state_attributes["learned_dead_time_min"] == pytest.approx(9.5)

    diag = rt.get_diagnostics_data()
    assert diag["response_delay_min"] == pytest.approx(9.5)
    assert diag["response_delay_samples"] == 5
    assert diag["passive_drift_rate"] == pytest.approx(0.025)
    assert diag["predictive_on_enabled"] is False
    assert diag["predictive_on_margin"] == 0.0

    # Translation keys parity across strings.json, ko.json, en.json
    base = Path(__file__).resolve().parent.parent / "custom_components" / "smart_pid_airconditioner"
    for rel in ("strings.json", "translations/ko.json", "translations/en.json"):
        payload = json.loads((base / rel).read_text(encoding="utf-8"))
        ent = payload["entity"]
        assert "predictive_on" in ent["switch"], f"Missing predictive_on switch in {rel}"
        sw_attrs = ent["switch"]["predictive_on"]["state_attributes"]
        assert "predicted_preheat_drop" in sw_attrs
        assert "effective_dead_time_min" in sw_attrs
        cp_attrs = ent["sensor"]["cycle_phase"]["state_attributes"]
        assert "response_delay_min" in cp_attrs
        assert "response_delay_samples" in cp_attrs
        rt_attrs = ent["sensor"]["recommended_tuning"]["state_attributes"]
        assert "learned_dead_time_min" in rt_attrs
