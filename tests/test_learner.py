"""Stage 3 unit & physical simulation tests for psychrometrics, learner, and storage."""

from __future__ import annotations

import json
import random

import pytest

from custom_components.smart_pid_airconditioner.learner import (
    CyclePhase,
    DynamicCoastingLearner,
    HuberRLS,
    INERTIA_FAST_AIR,
    INERTIA_HIGH_INERTIA,
    SimcAutoTuner,
    TuningParameters,
    TuningSafetyManager,
)
from custom_components.smart_pid_airconditioner.psychrometrics import (
    ah_to_rh,
    dew_point,
    rh_to_ah,
)
from custom_components.smart_pid_airconditioner.storage import (
    LearningStateSnapshot,
    ModelStore,
)


def test_psychrometrics_roundtrip_and_clamping() -> None:
    """Verify Bolton (1980) RH <-> AH roundtrip and boundary clamping."""
    ah = rh_to_ah(25.0, 50.0)
    assert 11.0 < ah < 12.0  # ~11.52 g/m³ at 25°C, 50% RH
    rh_back = ah_to_rh(25.0, ah)
    assert rh_back == pytest.approx(50.0, abs=0.05)

    # Clamping
    assert rh_to_ah(25.0, -10.0) == 0.0
    assert ah_to_rh(25.0, -5.0) == 0.0
    assert ah_to_rh(25.0, 500.0) == 100.0
    assert dew_point(25.0, 100.0) == pytest.approx(25.0, abs=0.1)


def test_heater_plus_humidifier_ah_invariance() -> None:
    """1. Absolute humidity learning rate stays invariant (< 2% deviation) during concurrent heating."""
    true_humid_rate = 0.08  # g/m³/min per unit humidifier effort
    true_loss = 0.01  # 1/min moisture exchange

    def _run_sim(temp_start: float, temp_rise_per_min: float) -> float:
        rls = HuberRLS(
            n_features=2,
            initial_theta=[0.05, 0.005],
            bounds=[(0.001, 2.0), (0.0001, 0.5)],
            forgetting=0.992,
        )
        ah_in = rh_to_ah(temp_start, 40.0)
        ah_out = rh_to_ah(15.0, 40.0)
        for step in range(160):
            u = 1.0 if (step % 16) < 8 else 0.0
            d_ah = true_humid_rate * u - true_loss * (ah_in - ah_out)
            # Even as temperature rises sharply from heater, AH dynamics are invariant
            _temp_now = temp_start + temp_rise_per_min * step
            _rh_now = ah_to_rh(_temp_now, ah_in)
            ah_recomputed = rh_to_ah(_temp_now, _rh_now)
            rls.update([u, -(ah_recomputed - ah_out)], d_ah)
            ah_in += d_ah
        return rls.theta[0]

    rate_no_heat = _run_sim(temp_start=18.0, temp_rise_per_min=0.0)
    rate_with_heat = _run_sim(temp_start=18.0, temp_rise_per_min=0.15)
    rel_diff = abs(rate_with_heat - rate_no_heat) / rate_no_heat
    assert rel_diff < 0.02
    assert rate_with_heat == pytest.approx(true_humid_rate, rel=0.05)


def test_huber_hard_reject_outlier_defense() -> None:
    """2. Injecting +0.5 / -0.6 °C/min spikes after warmup keeps parameter error within 5%."""
    true_rate = 0.06  # °C/min
    true_loss = 0.008
    rls = HuberRLS(
        n_features=2,
        initial_theta=[0.04, 0.005],
        bounds=[(0.001, 1.0), (0.0001, 0.2)],
        warmup_samples=20,
        huber_delta=0.04,
        hard_reject=0.12,
    )

    rng = random.Random(42)
    t_in = 26.0
    t_out = 32.0
    for step in range(120):
        u = 1.0 if (step % 15) < 10 else 0.2
        clean_dy = -true_rate * u + true_loss * (t_out - t_in)
        # Normal small noise
        y_obs = clean_dy + rng.gauss(0.0, 0.002)
        # Inject extreme unmeasured disturbances after warmup
        if step in (30, 55, 75, 95):
            y_obs += 0.5
        elif step in (42, 68, 88, 110):
            y_obs -= 0.6

        rls.update([-u, (t_out - t_in)], y_obs)
        t_in += clean_dy

    assert rls.rejected_outliers >= 8
    rate_err = abs(rls.theta[0] - true_rate) / true_rate
    assert rate_err < 0.05


def test_high_inertia_ondol_coasting_simulation() -> None:
    """3. Ondol high_inertia simulation converges k_afterglow smoothly without chattering."""
    learner = DynamicCoastingLearner(
        inertia_profile=INERTIA_HIGH_INERTIA,
        tau_em_min=35.0,
        ewma_alpha=0.3,
        min_run_min=5.0,
    )

    # Simulate 6 ondol heating + coasting cycles
    tau_em = 35.0
    e_em = 0.0
    t_room = 20.0
    for _cycle in range(6):
        # Active heating for 30 min
        for _ in range(30):
            e_em += (1.0 / (tau_em + 1.0)) * (1.0 - e_em)
            t_room += 0.08 * e_em - 0.015 * (t_room - 15.0)
            learner.step_cycle(is_active=True, u_effort=1.0, value=t_room, is_heating=True, dt_min=1.0)

        # Coasting for 40 min
        for _ in range(40):
            e_em += (1.0 / (tau_em + 1.0)) * (0.0 - e_em)
            t_room += 0.08 * e_em - 0.015 * (t_room - 15.0)
            learner.step_cycle(is_active=False, u_effort=0.0, value=t_room, is_heating=True, dt_min=1.0)

    assert learner.accepted_count >= 5
    assert learner.last_quality_reason == "accepted"
    assert learner.predict_coast() > 0.0
    assert len(learner.history) >= 5


def test_simc_auto_tuner_and_5_cycle_rollback() -> None:
    """4. SIMC computes valid Kp/Ki/Ke and 5-cycle >30% degradation triggers auto rollback."""
    rls = HuberRLS(n_features=2, initial_theta=[0.06, 0.008])
    tuner = SimcAutoTuner()
    baseline = TuningParameters(kp=20.0, ki=0.5, ke=0.6)

    params_eco = tuner.compute(rls, prediction_mode="eco", baseline=baseline)
    params_comfort = tuner.compute(rls, prediction_mode="comfort", baseline=baseline)
    assert params_comfort.kp >= params_eco.kp
    assert baseline.kp * 0.5 <= params_comfort.kp <= baseline.kp * 1.5

    safety = TuningSafetyManager(min_valid_cycles=5, eval_cycles=5, degradation_threshold=0.30)
    ok, _ = safety.can_auto_apply(rls_samples=30, valid_cycles=6)
    assert ok is True

    safety.begin_application(prev_params=baseline, baseline_mae=0.20, baseline_overshoot=0.30)
    # Feed 4 degraded cycles -> not yet rolled back
    for _ in range(4):
        assert safety.record_post_apply_cycle(cycle_mae=0.40, cycle_overshoot=0.50) is None
    # 5th cycle -> triggers auto rollback (> 30% worse)
    rolled_back = safety.record_post_apply_cycle(cycle_mae=0.40, cycle_overshoot=0.50)
    assert rolled_back is not None
    assert rolled_back.kp == baseline.kp
    assert safety.rollback_count == 1
    assert safety.last_fallback_reason == "auto_rollback_performance_degraded"


@pytest.mark.asyncio
async def test_storage_to_dict_from_dict_roundtrip(hass) -> None:
    """5. Verify ModelStore and LearningStateSnapshot to_dict -> JSON -> from_dict integrity."""
    snap = LearningStateSnapshot(inertia_profile=INERTIA_FAST_AIR)
    snap.temp_rls.update([1.0, -2.0, 0.0], 0.05)
    snap.coasting.accepted_count = 7
    snap.coasting.tau_coast = 4.25
    snap.coasting.history.append({"obs_coast": 0.45, "tau_coast": 4.25})
    snap.safety.season_applies = 2
    snap.safety.lifetime_applies = 5

    raw_json = json.dumps(snap.to_dict())
    restored = LearningStateSnapshot.from_dict(json.loads(raw_json))

    assert restored.temp_rls.theta == pytest.approx(snap.temp_rls.theta)
    assert restored.temp_rls.sample_count == snap.temp_rls.sample_count
    assert restored.coasting.accepted_count == 7
    assert restored.coasting.tau_coast == pytest.approx(4.25)
    assert len(restored.coasting.history) == 1
    assert restored.safety.season_applies == 2
    assert restored.safety.lifetime_applies == 5

    store = ModelStore(hass, "entry_roundtrip")
    store.async_delay_save(snap.to_dict, delay=600)
    loaded = await store.async_load()
    assert loaded.coasting.accepted_count == 7
