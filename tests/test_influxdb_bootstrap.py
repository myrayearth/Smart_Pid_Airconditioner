"""Tests for InfluxDB v1 historical self-learning bootstrap with regime segmentation."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any

import pytest

from custom_components.smart_pid_airconditioner import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.const import (
    CONF_AUX_TEMP_SENSOR,
    CONF_CONTROL_MODE,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_OUTDOOR_SENSOR,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONF_VENT_COOLDOWN_MIN,
    CONF_VENT_DEBOUNCE_SEC,
    CONF_VENT_MODE,
    CONF_VENT_SENSORS,
    CONTROL_MODE_SWITCH_PWM,
    DOMAIN,
    SERVICE_BOOTSTRAP_INFLUXDB,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    VENT_MODE_SENSORS,
)
from custom_components.smart_pid_airconditioner.learner import CyclePhase, DynamicCoastingLearner
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util


class _MockResponse:
    def __init__(self, status: int, payload: Any, text_body: str = "") -> None:
        self.status = status
        self._payload = payload
        self._text_body = text_body

    async def __aenter__(self) -> _MockResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return self._text_body


class FakeInfluxSession:
    """In-memory InfluxQL query evaluator for testing HTTP GET /query calls."""

    def __init__(self) -> None:
        self.series: dict[tuple[str, str, str], list[tuple[int, Any]]] = {}
        self.requests: list[dict[str, Any]] = []
        self.force_status: int | None = None
        self.force_error: Exception | None = None
        self.force_json_error: str | None = None

    def add_point(self, measurement: str, entity_tag: str, field: str, ts_ms: int, val: Any) -> None:
        self.series.setdefault((measurement, entity_tag, field), []).append((ts_ms, val))

    def _lookup_rows(self, meas: str, q: str, field: str) -> list[tuple[int, Any]]:
        matched_tags = set(re.findall(r'"entity_id"\s*=\s*\'([^\']+)\'', q))
        rows: list[tuple[int, Any]] = []
        for (m, etag, fld), pts in self.series.items():
            if m != meas or fld != field:
                continue
            if matched_tags and etag not in matched_tags:
                continue
            rows.extend(pts)
        rows.sort(key=lambda item: item[0])
        return rows

    def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None):
        params = dict(params or {})
        self.requests.append({"url": url, "params": params, "headers": dict(headers or {})})
        if self.force_error is not None:
            raise self.force_error
        if self.force_status is not None and self.force_status >= 400:
            return _MockResponse(self.force_status, {}, text_body="Unauthorized")
        if self.force_json_error is not None:
            return _MockResponse(200, {"error": self.force_json_error})

        q = str(params.get("q", ""))
        m_meas = re.search(r'FROM\s+"([^"]+)"', q)
        m_fld = re.search(r'SELECT\s+(?:MEAN\()?"([^"]+)"', q)
        if not m_meas or not m_fld:
            return _MockResponse(200, {"results": [{"statement_id": 0}]})

        meas = m_meas.group(1)
        fld = m_fld.group(1)
        all_rows = self._lookup_rows(meas, q, fld)

        # Parse optional time bounds
        m_gte = re.search(r"time\s*>=\s*(\d+)ms", q)
        m_lte = re.search(r"time\s*<=\s*(\d+)ms", q)
        m_lt = re.search(r"time\s*<\s*(\d+)ms", q)
        gte_ms = int(m_gte.group(1)) if m_gte else None
        lte_ms = int(m_lte.group(1)) if m_lte else None
        lt_ms = int(m_lt.group(1)) if m_lt else None

        filtered: list[tuple[int, Any]] = []
        for ts_ms, val in all_rows:
            if gte_ms is not None and ts_ms < gte_ms:
                continue
            if lte_ms is not None and ts_ms > lte_ms:
                continue
            if lt_ms is not None and ts_ms >= lt_ms:
                continue
            filtered.append((ts_ms, val))

        if "ORDER BY time DESC" in q:
            filtered.sort(key=lambda item: item[0], reverse=True)
        else:
            filtered.sort(key=lambda item: item[0])

        if "GROUP BY time(1m)" in q:
            buckets: dict[int, list[float]] = {}
            for ts_ms, val in filtered:
                try:
                    num = float(val)
                except (ValueError, TypeError):
                    continue
                b_ms = (ts_ms // 60_000) * 60_000
                buckets.setdefault(b_ms, []).append(num)
            values = [
                [b_ms, sum(vals) / len(vals)]
                for b_ms, vals in sorted(buckets.items())
            ]
            col_name = "val" if "AS val" in q else "mean"
            series_obj = (
                [{"name": meas, "columns": ["time", col_name], "values": values}]
                if values
                else []
            )
            return _MockResponse(200, {"results": [{"statement_id": 0, "series": series_obj}]})

        m_lim = re.search(r"LIMIT\s+(\d+)", q)
        if m_lim:
            filtered = filtered[: int(m_lim.group(1))]

        col_name = "val" if "AS val" in q else fld
        values = [[ts_ms, val] for ts_ms, val in filtered]
        series_obj = (
            [{"name": meas, "columns": ["time", col_name], "values": values}]
            if values
            else []
        )
        return _MockResponse(200, {"results": [{"statement_id": 0, "series": series_obj}]})


def _seed_boiler_cycle(
    session: FakeInfluxSession,
    start_dt: datetime,
    *,
    temp_meas: str = "°C",
    temp_tag: str = "living_temp",
    dev_meas: str = "state",
    dev_tag: str = "boiler",
    out_tag: str = "outdoor_temp",
    base_temp: float = 19.0,
    rate_per_min: float = 0.08,
) -> datetime:
    """Seed one realistic 30-min ON + 15-min post-stop coast + 15-min cool cycle."""
    # Pre-set switch OFF before start
    session.add_point(dev_meas, dev_tag, "value", int((start_dt - timedelta(minutes=2)).timestamp() * 1000), 0)
    t = start_dt
    temp = base_temp
    # Active 25 minutes: device = 1
    for m in range(25):
        ts_ms = int(t.timestamp() * 1000)
        session.add_point(dev_meas, dev_tag, "value", ts_ms, 1)
        session.add_point(temp_meas, temp_tag, "value", ts_ms, round(temp, 3))
        session.add_point(temp_meas, out_tag, "value", ts_ms, 5.0)
        temp += rate_per_min
        t += timedelta(minutes=1)
    # Turn OFF: device = 0, coast peak +0.55°C over 5 min then cool down
    coast_deltas = [0.15, 0.25, 0.15, -0.05, -0.15, -0.15, -0.10, -0.10, -0.08, -0.08]
    for idx, d in enumerate(coast_deltas):
        ts_ms = int(t.timestamp() * 1000)
        if idx == 0:
            session.add_point(dev_meas, dev_tag, "value", ts_ms, 0)
        temp += d
        session.add_point(temp_meas, temp_tag, "value", ts_ms, round(temp, 3))
        session.add_point(temp_meas, out_tag, "value", ts_ms, 5.0)
        t += timedelta(minutes=1)
    return t


def _setup_climate_entry_with_reg(hass, entry_id: str = "entry_boiler") -> tuple[ConfigEntry, str]:
    entry = ConfigEntry(
        entry_id=entry_id,
        title="Living Room Boiler",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.living_temp",
            CONF_OUTDOOR_SENSOR: "sensor.outdoor_temp",
            CONF_TARGET_DEVICE: "switch.boiler",
            CONF_INERTIA_PROFILE: "fast_air",
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KE: 0.5,
        },
        version=2,
    )
    entity_id = f"climate.{entry_id}"
    er.async_get(hass).async_register(
        entity_id,
        entry_id,
        platform=Platform.CLIMATE,
        unique_id=f"{entry_id}_thermostat",
    )
    return entry, entity_id


@pytest.mark.asyncio
async def test_two_year_seasonal_filtering_winter_only(hass) -> None:
    """2-year history with winter boiler (1/0) and hot summer isolates only winter range."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    entry, climate_eid = _setup_climate_entry_with_reg(hass)
    assert await async_setup_entry(hass, entry) is True
    runtime: SmartPIDRuntime = entry.runtime_data

    session = FakeInfluxSession()
    hass._mock_http_session = session

    # Year 1 winter (~400 days ago): 3 heating cycles in [18.5..22.0°C]
    t_w1 = now - timedelta(days=400)
    for _ in range(3):
        t_w1 = _seed_boiler_cycle(session, t_w1, base_temp=18.5)
        t_w1 += timedelta(minutes=20)

    # Summer (~150 days ago): 28.0°C with wild unheated noise [26.5..30.0°C] (MUST be filtered out)
    t_sum = now - timedelta(days=150)
    for m in range(180):
        ts_ms = int((t_sum + timedelta(minutes=m)).timestamp() * 1000)
        session.add_point("°C", "living_temp", "value", ts_ms, 28.0 + (m % 5) * 0.5)
        session.add_point("°C", "outdoor_temp", "value", ts_ms, 33.0)
        session.add_point("state", "boiler", "value", ts_ms, 0)

    # Year 2 winter (~10 days ago): 3 more heating cycles in [19.0..22.0°C]
    t_w2 = now - timedelta(days=10)
    for _ in range(3):
        t_w2 = _seed_boiler_cycle(session, t_w2, base_temp=19.0)
        t_w2 += timedelta(minutes=20)

    calls_before = len(hass.services.calls)
    res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": climate_eid,
            "days": 730,
            "min_indoor_temp": 16.0,
            "max_indoor_temp": 23.5,
            "reset_existing": True,
            "dry_run": False,
        },
        return_response=True,
    )

    assert res["applied"] is True
    assert res["dry_run"] is False
    assert res["period_days"] == 730
    assert res["skipped_out_of_range_minutes"] > 100_000  # summer + gaps excluded
    assert res["in_range_minutes"] >= 200
    assert res["rls_updates"] >= 20
    assert res["accepted_cycles"] >= 4
    assert res["learned_coast_factor"] > 0.0
    assert runtime._active_rls().sample_count == res["rls_updates"]
    assert runtime.snapshot.coasting.accepted_count == res["accepted_cycles"]

    # Zero physical control service calls emitted during historical replay
    control_calls = [
        c for c in hass.services.calls[calls_before:]
        if c[0] in ("switch", "climate", "humidifier", "homeassistant")
    ]
    assert control_calls == []
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_out_of_range_aborts_cycle_cleanly_preserving_converged_model(hass) -> None:
    """Crossing out-of-range aborts mid-cycle to IDLE without cross-season stitching."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    # Unit check on abort_cycle_to_idle preserving converged factors
    dc = DynamicCoastingLearner(inertia_profile="fast_air")
    dc.tau_coast = 4.25
    dc.accepted_count = 3
    for i in range(6):
        dc.step_cycle(is_active=True, u_effort=1.0, value=20.0 + i * 0.1, is_heating=True, blocked_for_learning=False)
    assert dc.phase == CyclePhase.ACTIVE
    dc.abort_cycle_to_idle()
    assert dc.phase == CyclePhase.IDLE
    assert dc.tau_coast == pytest.approx(4.25)
    assert dc.accepted_count == 3

    entry, climate_eid = _setup_climate_entry_with_reg(hass, entry_id="entry_abort")
    assert await async_setup_entry(hass, entry) is True

    session = FakeInfluxSession()
    hass._mock_http_session = session

    # First, 2 clean winter cycles
    t = now - timedelta(days=20)
    for _ in range(2):
        t = _seed_boiler_cycle(session, t, base_temp=19.0)
        t += timedelta(minutes=15)

    # Now start an active cycle at 22.0°C (boiler ON for 10 min) then jump immediately to 27.5°C summer!
    for m in range(10):
        ts_ms = int((t + timedelta(minutes=m)).timestamp() * 1000)
        session.add_point("state", "boiler", "value", ts_ms, 1)
        session.add_point("°C", "living_temp", "value", ts_ms, 22.0 + m * 0.1)
    t_hot = t + timedelta(minutes=10)
    for m in range(20):
        ts_ms = int((t_hot + timedelta(minutes=m)).timestamp() * 1000)
        session.add_point("state", "boiler", "value", ts_ms, 0)
        session.add_point("°C", "living_temp", "value", ts_ms, 27.5)

    # Next winter resume at 19.0°C with another clean cycle
    t_w2 = now - timedelta(days=5)
    _seed_boiler_cycle(session, t_w2, base_temp=19.0)

    res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": climate_eid,
            "days": 30,
            "min_indoor_temp": 16.0,
            "max_indoor_temp": 23.5,
            "reset_existing": True,
            "dry_run": False,
        },
        return_response=True,
    )

    rt: SmartPIDRuntime = entry.runtime_data
    # Only the 3 clean cycles complete cleanly; interrupted active->summer cycle did NOT forge fake coast
    assert res["accepted_cycles"] == 3
    assert rt._active_rls().theta[0] > 0.01
    assert rt.snapshot.coasting.phase in (CyclePhase.IDLE, CyclePhase.PASSIVE_DRIFT)
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_chunked_queries_95_days_and_pre_chunk_locf_seeding(hass) -> None:
    """days=95 splits queries into four <=30-day chunks and executes pre-chunk DESC LIMIT 1 LOCF."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    entry, climate_eid = _setup_climate_entry_with_reg(hass, entry_id="entry_chunks")
    assert await async_setup_entry(hass, entry) is True

    session = FakeInfluxSession()
    hass._mock_http_session = session

    # Boiler turned ON before chunk #1 and held steady; temp rises in chunk #4 (last 5 days)
    pre_ms = int((now - timedelta(days=96)).timestamp() * 1000)
    session.add_point("state", "boiler", "value", pre_ms, 1)
    _seed_boiler_cycle(session, now - timedelta(days=2), base_temp=19.0)

    res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": climate_eid,
            "days": 95,
            "dry_run": True,
        },
        return_response=True,
    )

    queries = [r["params"]["q"] for r in session.requests]
    temp_chunk_queries = [q for q in queries if 'MEAN("value")' in q and "living_temp" in q]
    pre_chunk_locf_queries = [q for q in queries if "ORDER BY time DESC LIMIT 1" in q and "boiler" in q]

    assert len(temp_chunk_queries) == 4  # 30 + 30 + 30 + 5 days
    assert len(pre_chunk_locf_queries) == 4  # 1 pre-chunk LOCF seed per chunk
    assert res["total_minutes_scanned"] == 95 * 1440
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_humidity_regime_filtering_rh_to_ah_and_ventilation_cooldown(hass) -> None:
    """Humidity bounds, rh_to_ah conversion, and VentilationGuard 20-min cooldown blocking."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    entry = ConfigEntry(
        entry_id="entry_hum",
        title="Study Humidifier",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.room_hum",
            CONF_AUX_TEMP_SENSOR: "sensor.room_temp",
            CONF_TARGET_DEVICE: "switch.hum_plug",
            CONF_VENT_MODE: VENT_MODE_SENSORS,
            CONF_VENT_SENSORS: ["binary_sensor.win1"],
            CONF_VENT_DEBOUNCE_SEC: 0,
            CONF_VENT_COOLDOWN_MIN: 20,
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KE: 0.2,
        },
        version=2,
    )
    hum_eid = "humidifier.entry_hum"
    er.async_get(hass).async_register(
        hum_eid, entry.entry_id, platform=Platform.HUMIDIFIER, unique_id="entry_hum_humidifier"
    )
    assert await async_setup_entry(hass, entry) is True

    session = FakeInfluxSession()
    hass._mock_http_session = session

    # Cycle 1 (clean in-range [40..65]% RH): humidify 45% -> 53%, coast +1.5%
    t0 = now - timedelta(hours=8)
    session.add_point("switch", "hum_plug", "value", int((t0 - timedelta(minutes=2)).timestamp() * 1000), 0)
    session.add_point("binary_sensor", "win1", "value", int((t0 - timedelta(minutes=2)).timestamp() * 1000), "off")
    t = t0
    rh = 45.0
    for _ in range(15):
        ts_ms = int(t.timestamp() * 1000)
        session.add_point("switch", "hum_plug", "value", ts_ms, 1)
        session.add_point("%", "room_hum", "value", ts_ms, round(rh, 2))
        session.add_point("°C", "room_temp", "value", ts_ms, 22.0)
        rh += 0.5
        t += timedelta(minutes=1)
    for idx, d in enumerate([0.8, 0.7, -0.7, -0.8, -0.7]):
        ts_ms = int(t.timestamp() * 1000)
        if idx == 0:
            session.add_point("switch", "hum_plug", "value", ts_ms, 0)
        rh += d
        session.add_point("%", "room_hum", "value", ts_ms, round(rh, 2))
        session.add_point("°C", "room_temp", "value", ts_ms, 22.0)
        t += timedelta(minutes=1)

    # Cycle 2 (window opens during run -> blocked across open + 20-min cooldown)
    t_v = now - timedelta(hours=4)
    for m in range(12):
        ts_ms = int((t_v + timedelta(minutes=m)).timestamp() * 1000)
        session.add_point("switch", "hum_plug", "value", ts_ms, 1)
        session.add_point("%", "room_hum", "value", ts_ms, 48.0 + m * 0.3)
        session.add_point("°C", "room_temp", "value", ts_ms, 22.0)
        if 3 <= m <= 6:
            session.add_point("binary_sensor", "win1", "value", ts_ms, "on")
        elif m == 7:
            session.add_point("binary_sensor", "win1", "value", ts_ms, "off")
    for m in range(12, 22):
        ts_ms = int((t_v + timedelta(minutes=m)).timestamp() * 1000)
        if m == 12:
            session.add_point("switch", "hum_plug", "value", ts_ms, 0)
        session.add_point("%", "room_hum", "value", ts_ms, 50.0)
        session.add_point("°C", "room_temp", "value", ts_ms, 22.0)

    # Out-of-range high humidity (80% RH > max 65%)
    t_hi = now - timedelta(hours=1)
    for m in range(15):
        ts_ms = int((t_hi + timedelta(minutes=m)).timestamp() * 1000)
        session.add_point("switch", "hum_plug", "value", ts_ms, 0)
        session.add_point("%", "room_hum", "value", ts_ms, 80.0)
        session.add_point("°C", "room_temp", "value", ts_ms, 22.0)

    res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": hum_eid,
            "days": 1,
            "min_indoor_humidity": 40.0,
            "max_indoor_humidity": 65.0,
            "reset_existing": True,
            "dry_run": False,
        },
        return_response=True,
    )

    assert res["applied"] is True
    assert res["accepted_cycles"] >= 1
    assert res["skipped_out_of_range_minutes"] >= 15
    assert "ventilation_contamination" in res["rejected_cycles"]
    assert res["rls_updates"] >= 2
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_dry_run_preview_isolation(hass) -> None:
    """dry_run=True leaves live runtime snapshot and Store completely unmodified."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    entry, climate_eid = _setup_climate_entry_with_reg(hass, entry_id="entry_dry")
    assert await async_setup_entry(hass, entry) is True
    rt: SmartPIDRuntime = entry.runtime_data
    notified = 0
    rt.subscribe(lambda: nonlocal_inc())

    def nonlocal_inc() -> None:
        nonlocal notified
        notified += 1

    session = FakeInfluxSession()
    hass._mock_http_session = session
    _seed_boiler_cycle(session, now - timedelta(hours=3), base_temp=19.0)

    dry_res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": climate_eid,
            "days": 1,
            "dry_run": True,
        },
        return_response=True,
    )
    assert dry_res["applied"] is False
    assert dry_res["dry_run"] is True
    assert dry_res["rls_updates"] >= 4
    assert dry_res["accepted_cycles"] == 1
    assert rt._active_rls().sample_count == 0
    assert rt.snapshot.coasting.accepted_count == 0
    assert notified == 0

    real_res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": climate_eid,
            "days": 1,
            "dry_run": False,
        },
        return_response=True,
    )
    assert real_res["applied"] is True
    assert rt._active_rls().sample_count == dry_res["rls_updates"]
    assert rt.snapshot.coasting.accepted_count == 1
    assert notified == 1
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_influxdb_errors_auth_secret_protection_and_validation(hass, caplog) -> None:
    """Verify auth failures, connection errors, empty data, validation, and password redaction."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)
    caplog.set_level(logging.DEBUG)

    entry, climate_eid = _setup_climate_entry_with_reg(hass, entry_id="entry_err")
    assert await async_setup_entry(hass, entry) is True

    session = FakeInfluxSession()
    hass._mock_http_session = session
    secret_pw = "UltraSecretInfluxPassword987!"

    # 1. Invalid bounds -> ServiceValidationError
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_BOOTSTRAP_INFLUXDB,
            {
                "entity_id": climate_eid,
                "days": 30,
                "min_indoor_temp": 25.0,
                "max_indoor_temp": 20.0,
            },
            return_response=True,
        )

    # 2. HTTP 401 Auth failure -> sends BasicAuth header, raises HomeAssistantError, hides password
    session.force_status = 401
    with pytest.raises(HomeAssistantError) as auth_exc:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_BOOTSTRAP_INFLUXDB,
            {
                "entity_id": climate_eid,
                "username": "admin_user",
                "password": secret_pw,
                "days": 7,
            },
            return_response=True,
        )
    expected_b64 = base64.b64encode(f"admin_user:{secret_pw}".encode("utf-8")).decode("ascii")
    assert session.requests[0]["headers"]["Authorization"] == f"Basic {expected_b64}"
    assert secret_pw not in str(auth_exc.value)
    assert secret_pw not in caplog.text

    # 3. Connection error embedding password -> sanitized error without password
    session.force_status = None
    session.force_error = OSError(f"failed connecting with {secret_pw}")
    with pytest.raises(HomeAssistantError) as conn_exc:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_BOOTSTRAP_INFLUXDB,
            {
                "entity_id": climate_eid,
                "username": "admin_user",
                "password": secret_pw,
                "days": 7,
            },
            return_response=True,
        )
    assert secret_pw not in str(conn_exc.value)
    assert secret_pw not in caplog.text

    # 4. Empty dataset -> clear HomeAssistantError
    session.force_error = None
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_BOOTSTRAP_INFLUXDB,
            {"entity_id": climate_eid, "days": 7},
            return_response=True,
        )

    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_single_bound_entity_overrides_and_reset_existing_false(hass) -> None:
    """Verify entity overrides, single upper bound, and reset_existing=False incremental accumulation."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    entry, climate_eid = _setup_climate_entry_with_reg(hass, entry_id="entry_ovr")
    assert await async_setup_entry(hass, entry) is True
    rt: SmartPIDRuntime = entry.runtime_data

    # Seed pre-existing converged knowledge (should persist when reset_existing=False)
    rt.snapshot.coasting.accepted_count = 2
    rt.snapshot.coasting.tau_coast = 3.5

    session = FakeInfluxSession()
    hass._mock_http_session = session

    # Seed data under overridden entity IDs & sensor measurement
    _seed_boiler_cycle(
        session,
        now - timedelta(hours=3),
        temp_meas="sensor",
        temp_tag="override_temp",
        dev_meas="switch",
        dev_tag="override_boiler",
        out_tag="override_out",
        base_temp=19.0,
    )

    res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": climate_eid,
            "days": 1,
            "max_indoor_temp": 24.0,
            "indoor_temp_entity_id": "sensor.override_temp",
            "device_entity_id": "switch.override_boiler",
            "outdoor_entity_id": "sensor.override_out",
            "ventilation_entity_ids": ["binary_sensor.override_win"],
            "reset_existing": False,
            "dry_run": False,
        },
        return_response=True,
    )

    assert res["applied"] is True
    assert res["accepted_cycles"] == 1
    assert rt.snapshot.coasting.accepted_count == 3  # 2 existing + 1 new accumulated
    assert res["warnings"]  # missing override_win logged safely as warning without failing
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_duration_dict_vent_debounce_and_string_state_sensor(hass) -> None:
    """DurationSelector dict in options, empty string bounds, list entity_id, and string sensor values."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    entry = ConfigEntry(
        entry_id="entry_dur_dict",
        title="Living Room Boiler Dur",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.living_temp",
            CONF_TARGET_DEVICE: "switch.boiler",
            CONF_INERTIA_PROFILE: "fast_air",
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KE: 0.5,
        },
        options={
            "ventilation": {
                CONF_VENT_MODE: VENT_MODE_SENSORS,
                CONF_VENT_SENSORS: ["binary_sensor.win1"],
                CONF_VENT_DEBOUNCE_SEC: {"hours": 0, "minutes": 0, "seconds": 45},
                CONF_VENT_COOLDOWN_MIN: 20.0,
            }
        },
        version=2,
    )
    climate_eid = "climate.entry_dur_dict"
    er.async_get(hass).async_register(
        climate_eid, entry.entry_id, platform=Platform.CLIMATE, unique_id="entry_dur_dict_thermostat"
    )
    assert await async_setup_entry(hass, entry) is True
    rt: SmartPIDRuntime = entry.runtime_data
    assert rt.get_bootstrap_defaults()["vent_debounce_sec"] == 45
    assert rt.get_bootstrap_defaults()["vent_cooldown_min"] == 20

    session = FakeInfluxSession()
    hass._mock_http_session = session

    # Seed indoor temps as strings under 'state' measurement & 'state' field
    t = now - timedelta(hours=2)
    session.add_point("state", "boiler", "value", int((t - timedelta(minutes=2)).timestamp() * 1000), 0)
    temp = 19.0
    for _ in range(20):
        ts_ms = int(t.timestamp() * 1000)
        session.add_point("state", "boiler", "value", ts_ms, 1)
        session.add_point("state", "living_temp", "state", ts_ms, f"{temp:.2f}")
        temp += 0.08
        t += timedelta(minutes=1)
    for idx, d in enumerate([0.2, 0.2, -0.15, -0.15]):
        ts_ms = int(t.timestamp() * 1000)
        if idx == 0:
            session.add_point("state", "boiler", "value", ts_ms, 0)
        temp += d
        session.add_point("state", "living_temp", "state", ts_ms, f"{temp:.2f}")
        t += timedelta(minutes=1)

    res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": [climate_eid],
            "days": 1.0,
            "min_indoor_temp": "",
            "max_indoor_temp": 25.0,
            "dry_run": True,
        },
        return_response=True,
    )
    assert res["dry_run"] is True
    assert res["accepted_cycles"] == 1
    assert res["rls_updates"] >= 3
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_boiler_switch_string_and_numeric_with_unknown_locf(hass) -> None:
    """Boiler switch learns identically for string 'ON'/'off' and numeric 1/0, preserving LOCF on 'unknown'."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    def _make_entry(eid: str) -> ConfigEntry:
        return ConfigEntry(
            entry_id=eid,
            title=f"Boiler {eid}",
            data={
                CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
                CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
                CONF_INDOOR_SENSOR: "sensor.living_temp",
                CONF_TARGET_DEVICE: "switch.boiler",
                CONF_INERTIA_PROFILE: "fast_air",
                CONF_KP: 20.0,
                CONF_KI: 0.5,
                CONF_KE: 0.5,
            },
            version=2,
        )

    # Case A: string "ON" / "off" in "state" field (auto-discovered) + "unknown" mid-cycle
    entry_str = _make_entry("entry_str_sw")
    er.async_get(hass).async_register(
        "climate.entry_str_sw", entry_str.entry_id, platform=Platform.CLIMATE, unique_id="entry_str_sw_thermostat"
    )
    await async_setup_entry(hass, entry_str)

    sess_str = FakeInfluxSession()
    hass._mock_http_session = sess_str
    t = now - timedelta(hours=2)
    sess_str.add_point("switch", "boiler", "state", int((t - timedelta(minutes=2)).timestamp() * 1000), "off")
    temp = 19.0
    for m in range(20):
        ts_ms = int(t.timestamp() * 1000)
        if m == 0:
            sess_str.add_point("switch", "boiler", "state", ts_ms, " ON ")
        elif m == 10:
            # Exceptional value must NOT drop active state (LOCF keeps active)
            sess_str.add_point("switch", "boiler", "state", ts_ms, "unknown")
        sess_str.add_point("°C", "living_temp", "value", ts_ms, round(temp, 3))
        temp += 0.08
        t += timedelta(minutes=1)
    for idx, d in enumerate([0.2, 0.2, -0.15, -0.15]):
        ts_ms = int(t.timestamp() * 1000)
        if idx == 0:
            sess_str.add_point("switch", "boiler", "state", ts_ms, "off")
        temp += d
        sess_str.add_point("°C", "living_temp", "value", ts_ms, round(temp, 3))
        t += timedelta(minutes=1)

    res_str = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": "climate.entry_str_sw",
            "days": 1,
            "dry_run": False,
        },
        return_response=True,
    )
    assert res_str["applied"] is True
    assert res_str["accepted_cycles"] == 1
    assert res_str["rls_updates"] >= 3
    assert res_str["discovered_states"]["device"]["field"] == "state"
    assert "ON" in res_str["discovered_states"]["device"]["unique_values"]
    assert "off" in res_str["discovered_states"]["device"]["unique_values"]
    assert "unknown" in res_str["discovered_states"]["device"]["unique_values"]

    # Case B: numeric 1.0 / 0 with explicit device_active_values / device_inactive_values
    entry_num = _make_entry("entry_num_sw")
    er.async_get(hass).async_register(
        "climate.entry_num_sw", entry_num.entry_id, platform=Platform.CLIMATE, unique_id="entry_num_sw_thermostat"
    )
    await async_setup_entry(hass, entry_num)

    sess_num = FakeInfluxSession()
    hass._mock_http_session = sess_num
    t = now - timedelta(hours=2)
    sess_num.add_point("state", "boiler", "value", int((t - timedelta(minutes=2)).timestamp() * 1000), 0)
    temp = 19.0
    for m in range(20):
        ts_ms = int(t.timestamp() * 1000)
        if m == 0:
            sess_num.add_point("state", "boiler", "value", ts_ms, 1.0)
        sess_num.add_point("°C", "living_temp", "value", ts_ms, round(temp, 3))
        temp += 0.08
        t += timedelta(minutes=1)
    for idx, d in enumerate([0.2, 0.2, -0.15, -0.15]):
        ts_ms = int(t.timestamp() * 1000)
        if idx == 0:
            sess_num.add_point("state", "boiler", "value", ts_ms, 0)
        temp += d
        sess_num.add_point("°C", "living_temp", "value", ts_ms, round(temp, 3))
        t += timedelta(minutes=1)

    res_num = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": "climate.entry_num_sw",
            "days": 1,
            "device_active_values": ["1"],
            "device_inactive_values": ["0"],
            "dry_run": False,
        },
        return_response=True,
    )
    assert res_num["applied"] is True
    assert res_num["accepted_cycles"] == 1
    assert res_num["rls_updates"] >= 3
    assert res_num["discovered_states"]["device"]["applied_active_values"] == ["1"]
    assert res_num["discovered_states"]["device"]["applied_inactive_values"] == ["0"]

    await async_unload_entry(hass, entry_str)
    await async_unload_entry(hass, entry_num)


@pytest.mark.asyncio
async def test_mixed_normal_and_inverted_ventilation_sensor_map(hass) -> None:
    """Mixed normal (1/'on'=open) and inverted (0/'off'=open) doors block learning & cooldown accurately."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    entry = ConfigEntry(
        entry_id="entry_inv_vent",
        title="Inverted Vent Boiler",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.living_temp",
            CONF_TARGET_DEVICE: "switch.boiler",
            CONF_INERTIA_PROFILE: "fast_air",
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KE: 0.5,
        },
        version=2,
    )
    er.async_get(hass).async_register(
        "climate.entry_inv_vent", entry.entry_id, platform=Platform.CLIMATE, unique_id="entry_inv_vent_thermostat"
    )
    await async_setup_entry(hass, entry)

    session = FakeInfluxSession()
    hass._mock_http_session = session

    t = now - timedelta(hours=3)
    # Seed: normal_door closed ("off"), inverted_door closed (1 = closed for inverted!)
    seed_ms = int((t - timedelta(minutes=2)).timestamp() * 1000)
    session.add_point("state", "boiler", "value", seed_ms, 0)
    session.add_point("binary_sensor", "normal_door", "state", seed_ms, "off")
    session.add_point("binary_sensor", "inverted_door", "value", seed_ms, 1)

    # Cycle 1: clean (both closed, inverted_door gets "unavailable" briefly -> LOCF stays closed!)
    temp = 19.0
    for m in range(20):
        ts_ms = int(t.timestamp() * 1000)
        if m == 0:
            session.add_point("state", "boiler", "value", ts_ms, 1)
        if m == 5:
            session.add_point("binary_sensor", "inverted_door", "value", ts_ms, "unavailable")
        session.add_point("°C", "living_temp", "value", ts_ms, round(temp, 3))
        temp += 0.08
        t += timedelta(minutes=1)
    for idx, d in enumerate([0.2, 0.2, -0.15, -0.15]):
        ts_ms = int(t.timestamp() * 1000)
        if idx == 0:
            session.add_point("state", "boiler", "value", ts_ms, 0)
        temp += d
        session.add_point("°C", "living_temp", "value", ts_ms, round(temp, 3))
        t += timedelta(minutes=1)

    # Wait 10 min
    for _ in range(10):
        session.add_point("°C", "living_temp", "value", int(t.timestamp() * 1000), round(temp, 3))
        t += timedelta(minutes=1)

    # Cycle 2: inverted_door opens (0 = open!) for 3 min then closes (1 = closed), triggering 20m cooldown
    for m in range(20):
        ts_ms = int(t.timestamp() * 1000)
        if m == 0:
            session.add_point("state", "boiler", "value", ts_ms, 1)
        if m == 2:
            session.add_point("binary_sensor", "inverted_door", "value", ts_ms, 0)
        if m == 5:
            session.add_point("binary_sensor", "inverted_door", "value", ts_ms, 1)
        session.add_point("°C", "living_temp", "value", ts_ms, round(temp, 3))
        temp += 0.05
        t += timedelta(minutes=1)
    for idx, d in enumerate([0.1, 0.1, -0.1, -0.1]):
        ts_ms = int(t.timestamp() * 1000)
        if idx == 0:
            session.add_point("state", "boiler", "value", ts_ms, 0)
        temp += d
        session.add_point("°C", "living_temp", "value", ts_ms, round(temp, 3))
        t += timedelta(minutes=1)

    res = await hass.services.async_call(
        DOMAIN,
        SERVICE_BOOTSTRAP_INFLUXDB,
        {
            "entity_id": "climate.entry_inv_vent",
            "days": 1,
            "ventilation_entity_ids": [
                "binary_sensor.normal_door",
                "binary_sensor.inverted_door",
            ],
            "ventilation_open_values": [1, "on", "open"],
            "ventilation_closed_values": [0, "off", "closed"],
            "ventilation_sensor_map": {
                "binary_sensor.inverted_door": {
                    "open": [0, "off"],
                    "closed": [1, "on"],
                }
            },
            "dry_run": True,
        },
        return_response=True,
    )

    # Cycle 1 accepted, Cycle 2 rejected due to inverted_door opening + 20m cooldown
    assert res["accepted_cycles"] == 1
    assert res["rejected_cycles"].get("ventilation_contamination", 0) >= 1

    # Verify discovered_states & dry_run immutability
    disc = res["discovered_states"]
    assert disc["device"]["unique_values"] == [0, 1]
    assert "binary_sensor.inverted_door" in disc["ventilation"]
    inv_info = disc["ventilation"]["binary_sensor.inverted_door"]
    assert inv_info["applied_active_values"] == [0, "off"]
    assert inv_info["applied_inactive_values"] == [1, "on"]
    assert 0 in inv_info["unique_values"] and 1 in inv_info["unique_values"]
    assert "unavailable" in inv_info["unique_values"]

    rt: SmartPIDRuntime = entry.runtime_data
    assert rt._active_rls().sample_count == 0
    assert rt.snapshot.coasting.accepted_count == 0
    await async_unload_entry(hass, entry)

