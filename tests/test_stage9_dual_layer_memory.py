"""Stage 9 tests: Dual-layer memory (Current / Long-term promotion, decay, blending, resilience)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

import pytest

from homeassistant.config_entries import ConfigEntry
import homeassistant.util.dt as dt_util

from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_OUTDOOR_SENSOR,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONTROL_MODE_SWITCH_PWM,
    THERMOSTAT_TYPE_HEATER,
)
from custom_components.smart_pid_airconditioner.influxdb_bootstrap import (
    async_bootstrap_from_influxdb,
)
from custom_components.smart_pid_airconditioner.learner import (
    DualLayerMemory,
    DynamicCoastingLearner,
    INERTIA_HIGH_INERTIA,
    MEMORY_CURRENT_MAX_AGE_SEC,
    StatBucket,
)
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.sensor import SmartPIDPredictedCoastSensor
from custom_components.smart_pid_airconditioner.storage import LearningStateSnapshot


def test_stable_samples_promote_to_long_term() -> None:
    """1) 5+ consistent samples promote current -> long_term and increment promotions."""
    mem = DualLayerMemory(scale=0.35)
    t0 = 1_700_000_000.0

    # First 5 samples: samples=5 -> confidence = 5/8 = 0.625 (< 0.7), not promoted yet
    for i in range(5):
        promoted = mem.learn(1.20, t0 + i * 3600.0)
        assert promoted is False
    assert mem.long_term is None

    # 6th stable sample: samples=6 -> confidence = 6/8 = 0.75 (>= 0.7) -> promoted!
    assert mem.learn(1.20, t0 + 5 * 3600.0) is True
    assert mem.long_term is not None
    assert mem.long_term.promotions == 1
    assert mem.long_term.mean == pytest.approx(1.20, abs=0.01)

    # 7th stable sample promotes again smoothly
    assert mem.learn(1.22, t0 + 6 * 3600.0) is True
    assert mem.long_term.promotions == 2
    assert mem.long_term.mean == pytest.approx(1.20, abs=0.02)


def test_noisy_high_variance_data_blocked_from_long_term_promotion() -> None:
    """2) High-variance noisy samples never promote to long_term regardless of sample count."""
    mem = DualLayerMemory(scale=0.3)
    t0 = 1_700_000_000.0

    # Alternating wild values (0.1 vs 2.5) -> huge variance -> confidence stays well below 0.7
    for i in range(25):
        val = 0.1 if i % 2 == 0 else 2.5
        promoted = mem.learn(val, t0 + i * 3600.0)
        assert promoted is False

    assert mem.current is not None
    assert mem.current.samples == 25
    assert mem.long_term is None


def test_dormancy_freshness_decay_and_current_reset_preserving_long_term() -> None:
    """3) After 30 days dormancy, current decays to 0, long_term survives alone, and new sample resets only current."""
    mem = DualLayerMemory(scale=0.35)
    t0 = 1_700_000_000.0

    # Build stable long_term baseline around 1.50
    for i in range(8):
        mem.learn(1.50, t0 + i * 3600.0)
    assert mem.long_term is not None
    assert mem.long_term.promotions >= 3
    lt_mean_before = mem.long_term.mean
    lt_promos_before = mem.long_term.promotions

    # Advance 31 days (> MEMORY_CURRENT_MAX_AGE_SEC)
    t_dormant = t0 + 7 * 3600.0 + MEMORY_CURRENT_MAX_AGE_SEC + 86400.0
    assert mem.current is not None
    assert mem.current.confidence(t_dormant, long_term=False, scale=0.35) == 0.0

    est = mem.estimate(t_dormant)
    assert est is not None
    mean, conf, source, weight = est
    assert source == "long_term"
    assert weight == 0.0
    assert mean == pytest.approx(lt_mean_before, abs=0.01)
    assert conf >= 0.7

    # First new sample after dormancy resets ONLY current (samples=1), preserving long_term
    promoted = mem.learn(2.20, t_dormant)
    assert promoted is False
    assert mem.current.samples == 1
    assert mem.current.mean == pytest.approx(2.20)
    assert mem.long_term.mean == pytest.approx(lt_mean_before)
    assert mem.long_term.promotions == lt_promos_before

    # Prediction still falls back to long_term until current has >= 3 samples
    est_after_one = mem.estimate(t_dormant + 60.0)
    assert est_after_one is not None
    assert est_after_one[2] == "long_term"


async def test_blended_estimation_and_influxdb_historical_timestamps(hass) -> None:
    """4) Verify blended estimation, DynamicCoastingLearner sensor/diagnostics, and InfluxDB historical now_ts."""
    # A. Direct blended estimation test
    mem = DualLayerMemory(scale=0.35, current_weight_limit=0.5)
    t0 = 1_700_000_000.0
    for i in range(8):
        mem.learn(1.0, t0 + i * 3600.0)
    assert mem.long_term is not None

    # After 31 days, new season with higher value 1.8 (4 stable samples -> current conf ~ 0.5)
    t_new = t0 + 7 * 3600.0 + MEMORY_CURRENT_MAX_AGE_SEC + 86400.0
    for i in range(4):
        mem.learn(1.8, t_new + i * 3600.0)

    est = mem.estimate(t_new + 4 * 3600.0)
    assert est is not None
    mean, _conf, source, weight = est
    assert source == "blended"
    assert 0.25 <= weight <= 0.5
    assert 1.0 < mean < 1.8

    # B. DynamicCoastingLearner + Sensor + Diagnostics
    learner = DynamicCoastingLearner(inertia_profile=INERTIA_HIGH_INERTIA)
    learner.emitter_charge = 0.5
    learner.k_afterglow_memory = mem
    pred = learner.predict_coast(now_ts=t_new + 4 * 3600.0)
    assert pred > 0.0
    assert learner.last_memory_source == "blended"
    assert 0.25 <= learner.last_memory_current_weight <= 0.5

    entry = ConfigEntry(
        entry_id="stage9_diag",
        title="Stage 9 Boiler",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_TARGET_DEVICE: "switch.boiler_s9",
            CONF_INDOOR_SENSOR: "sensor.temp_s9",
            CONF_OUTDOOR_SENSOR: "sensor.out_s9",
            CONF_INERTIA_PROFILE: INERTIA_HIGH_INERTIA,
        },
        options={},
    )
    rt = SmartPIDRuntime(hass, entry)
    rt.snapshot.coasting = learner
    pred_sensor = SmartPIDPredictedCoastSensor(entry, rt)
    attrs = pred_sensor.extra_state_attributes
    assert attrs["coast_memory_source"] == "blended"
    assert attrs["coast_current_weight"] == pytest.approx(learner.last_memory_current_weight)

    diag = rt.get_diagnostics_data()
    assert "dual_layer_memory" in diag
    assert diag["dual_layer_memory"]["k_afterglow"]["long_term"] is not None
    assert diag["dual_layer_memory"]["last_memory_source"] == "blended"

    # C. Verify influxdb_bootstrap passes historical epoch timestamps into DualLayerMemory
    now_dt = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now_dt)
    start_dt = now_dt - timedelta(days=2)

    # Build 2 days with 1 clean heating + coasting cycle using historical timestamps
    indoor_rows = []
    dev_rows = []
    out_rows = []
    for m in range(0, 120):
        ts_ms = int((start_dt + timedelta(minutes=m)).timestamp() * 1000)
        if m < 20:
            t_val = 20.0 + 0.02 * m
            d_val = 1
        elif m < 35:
            t_val = 20.4 + 0.04 * (m - 20)
            d_val = 0
        else:
            t_val = 21.0 - 0.05 * (m - 35)
            d_val = 0
        indoor_rows.append([ts_ms, round(t_val, 3)])
        dev_rows.append([ts_ms, d_val])
        out_rows.append([ts_ms, 2.0])

    class _MockResp:
        def __init__(self, payload: dict) -> None:
            self.status = 200
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self, content_type=None):
            return self._payload

        async def text(self):
            return json.dumps(self._payload)

    class _MockSession:
        def get(self, url, params=None, **kwargs):
            q = (params or {}).get("q", "")
            if "LIMIT 1" in q and "ORDER BY time DESC" not in q:
                if "temp_s9" in q:
                    return _MockResp({"results": [{"series": [{"columns": ["time", "value"], "values": [indoor_rows[0]]}]}]})
                if "out_s9" in q:
                    return _MockResp({"results": [{"series": [{"columns": ["time", "value"], "values": [out_rows[0]]}]}]})
                if "boiler_s9" in q:
                    return _MockResp({"results": [{"series": [{"columns": ["time", "value"], "values": [dev_rows[0]]}]}]})
            if "ORDER BY time DESC LIMIT 1" in q:
                return _MockResp({"results": [{}]})
            if "temp_s9" in q:
                return _MockResp({"results": [{"series": [{"columns": ["time", "val"], "values": indoor_rows}]}]})
            if "out_s9" in q:
                return _MockResp({"results": [{"series": [{"columns": ["time", "val"], "values": out_rows}]}]})
            if "boiler_s9" in q:
                return _MockResp({"results": [{"series": [{"columns": ["time", "value"], "values": dev_rows}]}]})
            return _MockResp({"results": [{}]})

    hass._mock_http_session = _MockSession()
    res = await async_bootstrap_from_influxdb(
        hass,
        rt,
        {
            "url": "http://influx.local:8086",
            "database": "homeassistant",
            "days": 2,
            "indoor_measurement": "°C",
            "outdoor_measurement": "°C",
            "device_measurement": "state",
            "reset_existing": True,
            "dry_run": False,
        },
    )
    assert res["accepted_cycles"] >= 1
    k_cur = rt.snapshot.coasting.k_afterglow_memory.current
    assert k_cur is not None
    # Timestamp recorded in DualLayerMemory must match the historical replay window, not wall-clock now!
    assert start_dt.timestamp() <= k_cur.updated_at <= (start_dt + timedelta(hours=3)).timestamp()


def test_legacy_snapshot_corrupted_memory_defense_and_translations() -> None:
    """5) Verify legacy snapshot compat, corrupted StatBucket/DualLayerMemory defense, and translations."""
    # Legacy snapshot without any Stage 9 memory keys
    legacy = {
        "temp_rls": {"n_features": 3, "theta": [0.05, 0.005, 0.0], "sample_count": 20},
        "hum_rls": {"n_features": 2, "theta": [0.2, 0.01], "sample_count": 0},
        "coasting": {
            "inertia_profile": "high_inertia",
            "k_afterglow": 1.4,
            "accepted_count": 6,
            "history": [],
        },
        "safety": {"apply_count": 0},
    }
    snap = LearningStateSnapshot.from_dict(legacy)
    assert snap.coasting.k_afterglow_memory.current is None
    assert snap.coasting.k_afterglow_memory.long_term is None

    # Corrupted current (NaN / negative variance) is discarded while valid long_term survives!
    corrupted = {
        "inertia_profile": "high_inertia",
        "k_afterglow": 1.4,
        "k_afterglow_memory": {
            "current": {"mean": float("nan"), "variance": -1.0, "samples": 5, "updated_at": 100.0},
            "long_term": {"mean": 1.35, "variance": 0.01, "samples": 8, "updated_at": 100.0, "promotions": 3},
        },
        "tau_coast_memory": "corrupted-not-a-dict",
        "outdoor_band_memories": "also-corrupted",
    }
    recovered = DynamicCoastingLearner.from_dict(corrupted)
    assert recovered.k_afterglow_memory.current is None
    assert recovered.k_afterglow_memory.long_term is not None
    assert recovered.k_afterglow_memory.long_term.mean == pytest.approx(1.35)
    assert recovered.tau_coast_memory.current is None
    assert recovered.outdoor_band_memories == {}

    # StatBucket.from_dict rejects NaN / Inf / negative variance
    with pytest.raises(ValueError):
        StatBucket.from_dict({"mean": math.inf, "variance": 0.1, "samples": 3})
    with pytest.raises(ValueError):
        StatBucket.from_dict({"mean": 1.0, "variance": -0.5, "samples": 3})

    # Translations check across strings.json, ko.json, en.json
    base = Path(__file__).resolve().parent.parent / "custom_components" / "smart_pid_airconditioner"
    for rel in ("strings.json", "translations/ko.json", "translations/en.json"):
        payload = json.loads((base / rel).read_text(encoding="utf-8"))
        pc_attrs = payload["entity"]["sensor"]["predicted_coast"]["state_attributes"]
        assert "coast_memory_source" in pc_attrs, f"Missing coast_memory_source in {rel}"
        assert "coast_current_weight" in pc_attrs, f"Missing coast_current_weight in {rel}"
        for st in ("blended", "current", "long_term", "legacy"):
            assert st in pc_attrs["coast_memory_source"]["state"], f"Missing state {st} in {rel}"
