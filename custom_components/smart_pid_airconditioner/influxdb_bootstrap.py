"""InfluxDB v1 historical self-learning bootstrap for Smart PID controllers."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.util.dt as dt_util

from .const import (
    DEFAULT_INFLUXDB_DATABASE,
    DEFAULT_INFLUXDB_DAYS,
    DEFAULT_INFLUXDB_DEVICE_FIELD,
    DEFAULT_INFLUXDB_URL,
    INFLUXDB_CHUNK_DAYS,
    INFLUXDB_MAX_SENSOR_GAP_MIN,
    MAX_INFLUXDB_DAYS,
    VENT_LOGIC_ANY,
    VENT_MODE_DISABLED,
    VENT_MODE_SENSORS,
)
from .learner import CyclePhase, INERTIA_CUSTOM, INERTIA_HIGH_INERTIA
from .psychrometrics import rh_to_ah
from .runtime import SmartPIDRuntime
from .storage import LearningStateSnapshot
from .ventilation import VentState, VentStatus, VentilationGuard

_LOGGER = logging.getLogger(__name__)

_HTTP_TIMEOUT_SEC = 30.0
_TEMP_MEASUREMENT_CANDIDATES = ("°C", "°F", "state", "sensor")
_HUMIDITY_MEASUREMENT_CANDIDATES = ("%", "state", "sensor")
_DEVICE_MEASUREMENT_CANDIDATES = ("state", "switch", "input_boolean", "climate", "humidifier")
_VENT_MEASUREMENT_CANDIDATES = ("state", "binary_sensor", "switch", "input_boolean")

_ACTIVE_STRINGS = frozenset(
    {"1", "on", "true", "heat", "heating", "cool", "cooling", "humidifying", "drying"}
)
_HEATING_STRINGS = frozenset({"heat", "heating", "humidifying"})
_COOLING_STRINGS = frozenset({"cool", "cooling", "drying"})


def _escape_tag(val: str) -> str:
    return val.replace("\\", "\\\\").replace("'", "\\'")


def _escape_ident(val: str) -> str:
    return val.replace("\\", "\\\\").replace('"', '\\"')


def _entity_where_clause(entity_id: str) -> str:
    """Match both stripped short entity_id (HA InfluxDB default) and full domain.entity_id."""
    eid = entity_id.strip()
    if "." in eid:
        short_id = eid.split(".", 1)[1]
        return f'("entity_id" = \'{_escape_tag(short_id)}\' OR "entity_id" = \'{_escape_tag(eid)}\')'
    return f'"entity_id" = \'{_escape_tag(eid)}\''


def _parse_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(val)
    except (ValueError, TypeError):
        return None
    return num if math.isfinite(num) else None


def _parse_device_active(val: Any) -> bool:
    """Interpret InfluxDB numeric (1/0) or string device state."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return math.isfinite(float(val)) and float(val) >= 0.5
    if val is None:
        return False
    s = str(val).strip().lower()
    if s in _ACTIVE_STRINGS:
        return True
    try:
        num = float(s)
        return math.isfinite(num) and num >= 0.5
    except ValueError:
        return False


def _parse_vent_state(val: Any) -> str:
    """Convert InfluxDB state value into 'on' or 'off' string for VentilationGuard."""
    if isinstance(val, bool):
        return "on" if val else "off"
    if isinstance(val, (int, float)):
        return "on" if (math.isfinite(float(val)) and float(val) >= 0.5) else "off"
    if val is None:
        return "off"
    s = str(val).strip().lower()
    if s in ("unavailable", "unknown"):
        return s
    if s in ("on", "open", "true", "1"):
        return "on"
    try:
        num = float(s)
        return "on" if (math.isfinite(num) and num >= 0.5) else "off"
    except ValueError:
        return "off"


class _ReplayState:
    __slots__ = ("state",)

    def __init__(self, state: str) -> None:
        self.state = state


class _ReplayStates:
    def __init__(self) -> None:
        self._states: dict[str, _ReplayState] = {}

    def set_state(self, entity_id: str, state: str) -> None:
        self._states[entity_id] = _ReplayState(state)

    def get(self, entity_id: str) -> _ReplayState | None:
        return self._states.get(entity_id)


class _ReplayHass:
    """Lightweight Home Assistant proxy passed to VentilationGuard without touching live state."""

    def __init__(self) -> None:
        self.states = _ReplayStates()


@dataclass
class _SeriesSpec:
    entity_id: str
    measurement: str
    field: str
    is_fahrenheit: bool = False


class InfluxDBv1Client:
    """Minimal async InfluxDB v1 HTTP /query client using HA aiohttp session."""

    def __init__(
        self,
        hass: HomeAssistant,
        url: str,
        database: str,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = async_get_clientsession(hass)
        self._query_url = f"{url.rstrip('/')}/query"
        self._database = database
        self._headers: dict[str, str] = {}
        if username or password:
            raw = f"{username or ''}:{password or ''}".encode("utf-8")
            encoded = base64.b64encode(raw).decode("ascii")
            self._headers["Authorization"] = f"Basic {encoded}"

    async def async_query(self, query: str) -> list[tuple[int, Any]]:
        """Execute InfluxQL query with epoch=ms and return list of (ts_ms, value)."""
        params = {
            "db": self._database,
            "q": query,
            "epoch": "ms",
        }
        try:
            async with asyncio.timeout(_HTTP_TIMEOUT_SEC):
                async with self._session.get(
                    self._query_url,
                    params=params,
                    headers=self._headers or None,
                ) as resp:
                    if resp.status in (401, 403):
                        raise HomeAssistantError(
                            f"InfluxDB authentication failed (HTTP {resp.status})."
                        )
                    if resp.status >= 400:
                        text = await resp.text()
                        raise HomeAssistantError(
                            f"InfluxDB query failed (HTTP {resp.status}): {text[:200]}"
                        )
                    payload = await resp.json()
        except HomeAssistantError:
            raise
        except TimeoutError as err:
            raise HomeAssistantError("Timed out connecting to InfluxDB v1 endpoint.") from err
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to connect to InfluxDB v1 endpoint: {type(err).__name__}"
            ) from err

        if not isinstance(payload, dict):
            raise HomeAssistantError("Unexpected non-object JSON response from InfluxDB.")
        if payload.get("error"):
            raise HomeAssistantError(f"InfluxDB returned error: {payload['error']}")

        results = payload.get("results") or []
        rows: list[tuple[int, Any]] = []
        for res in results:
            if not isinstance(res, dict):
                continue
            if res.get("error"):
                raise HomeAssistantError(f"InfluxDB query error: {res['error']}")
            for series in res.get("series") or []:
                cols = series.get("columns") or []
                values = series.get("values") or []
                if len(cols) < 2:
                    continue
                time_idx = cols.index("time") if "time" in cols else 0
                val_idx = next((i for i, c in enumerate(cols) if c != "time"), 1)
                for row in values:
                    if len(row) > max(time_idx, val_idx) and row[time_idx] is not None:
                        try:
                            rows.append((int(row[time_idx]), row[val_idx]))
                        except (ValueError, TypeError):
                            continue
        return rows


async def _resolve_series_spec(
    client: InfluxDBv1Client,
    entity_id: str,
    explicit_measurement: str | None,
    meas_candidates: tuple[str, ...],
    field_candidates: tuple[str, ...],
    start_ms: int,
    end_ms: int,
    *,
    is_temperature: bool = False,
) -> _SeriesSpec | None:
    """Resolve measurement and field containing data in [start_ms, end_ms]."""
    where = _entity_where_clause(entity_id)
    measurements = (explicit_measurement,) if explicit_measurement else meas_candidates

    for meas in measurements:
        for fld in field_candidates:
            q = (
                f'SELECT "{_escape_ident(fld)}" FROM "{_escape_ident(meas)}" '
                f"WHERE {where} AND time >= {start_ms}ms AND time <= {end_ms}ms LIMIT 1"
            )
            rows = await client.async_query(q)
            if rows:
                return _SeriesSpec(
                    entity_id=entity_id,
                    measurement=meas,
                    field=fld,
                    is_fahrenheit=(is_temperature and meas == "°F"),
                )
    return None


async def _fetch_numeric_chunk(
    client: InfluxDBv1Client,
    spec: _SeriesSpec,
    c_start_ms: int,
    c_end_ms: int,
) -> dict[int, float]:
    """Query 1-minute aggregated numeric series within [c_start_ms, c_end_ms)."""
    where = _entity_where_clause(spec.entity_id)
    q = (
        f'SELECT MEAN("{_escape_ident(spec.field)}") AS val '
        f'FROM "{_escape_ident(spec.measurement)}" '
        f"WHERE {where} AND time >= {c_start_ms}ms AND time < {c_end_ms}ms "
        f"GROUP BY time(1m) fill(none)"
    )
    rows = await client.async_query(q)
    out: dict[int, float] = {}
    for ts_ms, raw_val in rows:
        num = _parse_float(raw_val)
        if num is None:
            continue
        if spec.is_fahrenheit:
            num = (num - 32.0) * 5.0 / 9.0
        m_idx = (ts_ms - c_start_ms) // 60_000
        if m_idx >= 0:
            out[m_idx] = num
    return out


async def _fetch_pre_chunk_state(
    client: InfluxDBv1Client,
    spec: _SeriesSpec,
    c_start_ms: int,
) -> Any | None:
    """Query the single most recent state immediately before chunk start for LOCF seeding."""
    where = _entity_where_clause(spec.entity_id)
    q = (
        f'SELECT "{_escape_ident(spec.field)}" AS val '
        f'FROM "{_escape_ident(spec.measurement)}" '
        f"WHERE {where} AND time < {c_start_ms}ms "
        f"ORDER BY time DESC LIMIT 1"
    )
    rows = await client.async_query(q)
    if rows:
        return rows[0][1]
    return None


async def _fetch_raw_chunk(
    client: InfluxDBv1Client,
    spec: _SeriesSpec,
    c_start_ms: int,
    c_end_ms: int,
) -> list[tuple[int, Any]]:
    """Query raw transition rows within [c_start_ms, c_end_ms) ordered chronologically."""
    where = _entity_where_clause(spec.entity_id)
    q = (
        f'SELECT "{_escape_ident(spec.field)}" AS val '
        f'FROM "{_escape_ident(spec.measurement)}" '
        f"WHERE {where} AND time >= {c_start_ms}ms AND time < {c_end_ms}ms "
        f"ORDER BY time ASC"
    )
    rows = await client.async_query(q)
    events: list[tuple[int, Any]] = []
    for ts_ms, raw_val in rows:
        m_idx = max(0, (ts_ms - c_start_ms) // 60_000)
        events.append((m_idx, raw_val))
    return events


async def async_bootstrap_from_influxdb(
    hass: HomeAssistant,
    runtime: SmartPIDRuntime,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Bootstrap SmartPIDRuntime learning models from historical InfluxDB v1 data."""
    url = str(params.get("url") or DEFAULT_INFLUXDB_URL).strip()
    database = str(params.get("database") or DEFAULT_INFLUXDB_DATABASE).strip()
    username = params.get("username")
    password = params.get("password")
    days = int(params.get("days") if params.get("days") is not None else DEFAULT_INFLUXDB_DAYS)
    if days < 1 or days > MAX_INFLUXDB_DAYS:
        raise ServiceValidationError(
            f"days must be between 1 and {MAX_INFLUXDB_DAYS} (got {days})."
        )

    min_temp = _parse_float(params.get("min_indoor_temp"))
    max_temp = _parse_float(params.get("max_indoor_temp"))
    if min_temp is not None and max_temp is not None and min_temp > max_temp:
        raise ServiceValidationError(
            f"min_indoor_temp ({min_temp}) cannot exceed max_indoor_temp ({max_temp})."
        )

    min_hum = _parse_float(params.get("min_indoor_humidity"))
    max_hum = _parse_float(params.get("max_indoor_humidity"))
    if min_hum is not None and max_hum is not None and min_hum > max_hum:
        raise ServiceValidationError(
            f"min_indoor_humidity ({min_hum}) cannot exceed max_indoor_humidity ({max_hum})."
        )

    device_field = str(params.get("device_field") or DEFAULT_INFLUXDB_DEVICE_FIELD).strip()
    reset_existing = bool(params.get("reset_existing", True))
    dry_run = bool(params.get("dry_run", True))

    defaults = runtime.get_bootstrap_defaults()
    is_humidity = bool(defaults["is_humidity"])
    is_heating_default = bool(defaults["is_heating_default"])

    if is_humidity:
        indoor_hum_eid = str(params.get("indoor_humidity_entity_id") or defaults["indoor_sensor"] or "").strip() or None
        indoor_temp_eid = str(params.get("indoor_temp_entity_id") or defaults["aux_temp_sensor"] or "").strip() or None
        if not indoor_hum_eid:
            raise ServiceValidationError("No indoor humidity sensor configured or specified.")
        if (min_temp is not None or max_temp is not None) and not indoor_temp_eid:
            raise ServiceValidationError(
                "indoor_temp_entity_id (or aux_temp_sensor) is required when indoor temperature bounds are specified."
            )
    else:
        indoor_temp_eid = str(params.get("indoor_temp_entity_id") or defaults["indoor_sensor"] or "").strip() or None
        indoor_hum_eid = str(params.get("indoor_humidity_entity_id") or "").strip() or None
        if not indoor_temp_eid:
            raise ServiceValidationError("No indoor temperature sensor configured or specified.")
        if (min_hum is not None or max_hum is not None) and not indoor_hum_eid:
            raise ServiceValidationError(
                "indoor_humidity_entity_id is required when indoor humidity bounds are specified for a temperature controller."
            )

    device_eid = str(params.get("device_entity_id") or defaults["device_entity_id"] or "").strip() or None
    if not device_eid:
        raise ServiceValidationError("No target device entity configured or specified.")

    # Infer default direction when user overrides a specific cool/heat/humidity/dehumidity device
    if device_eid in (defaults.get("device_cool_entity_id"), defaults.get("device_dehumidity_entity_id")):
        is_heating_default = False
    elif device_eid in (defaults.get("device_heat_entity_id"), defaults.get("device_humidity_entity_id")):
        is_heating_default = True

    outdoor_eid = str(params.get("outdoor_entity_id") or defaults["outdoor_sensor"] or "").strip() or None

    vent_override = params.get("ventilation_entity_ids")
    if isinstance(vent_override, str):
        vent_override = [vent_override] if vent_override.strip() else []
    if vent_override:
        vent_eids = [str(v).strip() for v in vent_override if v and str(v).strip()]
        vent_mode = VENT_MODE_SENSORS
        vent_sensors = vent_eids
        vent_switch = None
    else:
        vent_mode = str(defaults["vent_mode"])
        vent_sensors = list(defaults["vent_sensors"])
        vent_switch = defaults["vent_switch"]
        vent_eids = list(dict.fromkeys([*(vent_sensors or []), *([vent_switch] if vent_switch else [])]))
        if vent_mode == VENT_MODE_DISABLED:
            vent_eids = []

    indoor_meas_ovr = (params.get("indoor_measurement") or "").strip() or None
    outdoor_meas_ovr = (params.get("outdoor_measurement") or "").strip() or None
    device_meas_ovr = (params.get("device_measurement") or "").strip() or None
    vent_meas_ovr = (params.get("ventilation_measurement") or "").strip() or None

    end_dt = dt_util.utcnow().replace(second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    client = InfluxDBv1Client(
        hass,
        url=url,
        database=database,
        username=username,
        password=password,
    )

    warnings: list[str] = []

    # 1. Pre-flight measurement & field resolution
    temp_spec: _SeriesSpec | None = None
    hum_spec: _SeriesSpec | None = None

    if is_humidity:
        hum_spec = await _resolve_series_spec(
            client,
            indoor_hum_eid,  # type: ignore[arg-type]
            indoor_meas_ovr,
            _HUMIDITY_MEASUREMENT_CANDIDATES,
            ("value", "state"),
            start_ms,
            end_ms,
        )
        if hum_spec is None:
            raise HomeAssistantError(
                f"No historical humidity data found in InfluxDB for '{indoor_hum_eid}' over the last {days} days."
            )
        if indoor_temp_eid:
            temp_spec = await _resolve_series_spec(
                client,
                indoor_temp_eid,
                None,
                _TEMP_MEASUREMENT_CANDIDATES,
                ("value",),
                start_ms,
                end_ms,
                is_temperature=True,
            )
            if temp_spec is None:
                msg = f"Auxiliary/indoor temperature sensor '{indoor_temp_eid}' had no InfluxDB data; humidity replay will use RH% fallback."
                warnings.append(msg)
                if min_temp is not None or max_temp is not None:
                    raise HomeAssistantError(
                        f"Indoor temperature sensor '{indoor_temp_eid}' had no data for requested temperature filter."
                    )
            elif temp_spec.is_fahrenheit:
                warnings.append(f"Converted '{indoor_temp_eid}' values from °F to °C.")
    else:
        temp_spec = await _resolve_series_spec(
            client,
            indoor_temp_eid,  # type: ignore[arg-type]
            indoor_meas_ovr,
            _TEMP_MEASUREMENT_CANDIDATES,
            ("value", "state"),
            start_ms,
            end_ms,
            is_temperature=True,
        )
        if temp_spec is None:
            raise HomeAssistantError(
                f"No historical temperature data found in InfluxDB for '{indoor_temp_eid}' over the last {days} days."
            )
        if temp_spec.is_fahrenheit:
            warnings.append(f"Converted '{indoor_temp_eid}' values from °F to °C.")
        if indoor_hum_eid:
            hum_spec = await _resolve_series_spec(
                client,
                indoor_hum_eid,
                None,
                _HUMIDITY_MEASUREMENT_CANDIDATES,
                ("value", "state"),
                start_ms,
                end_ms,
            )
            if hum_spec is None:
                raise HomeAssistantError(
                    f"Indoor humidity sensor '{indoor_hum_eid}' had no data in InfluxDB for humidity filtering."
                )

    device_fields = (device_field, "state") if device_field != "state" else ("state", "value")
    device_spec = await _resolve_series_spec(
        client,
        device_eid,
        device_meas_ovr,
        _DEVICE_MEASUREMENT_CANDIDATES,
        device_fields,
        start_ms,
        end_ms,
    )
    if device_spec is None:
        raise HomeAssistantError(
            f"No historical device activity data found in InfluxDB for '{device_eid}' over the last {days} days."
        )

    outdoor_spec: _SeriesSpec | None = None
    if outdoor_eid:
        outdoor_cands = _HUMIDITY_MEASUREMENT_CANDIDATES if is_humidity else _TEMP_MEASUREMENT_CANDIDATES
        outdoor_spec = await _resolve_series_spec(
            client,
            outdoor_eid,
            outdoor_meas_ovr,
            outdoor_cands,
            ("value", "state"),
            start_ms,
            end_ms,
            is_temperature=not is_humidity,
        )
        if outdoor_spec is None:
            warnings.append(f"Outdoor sensor '{outdoor_eid}' had no InfluxDB data; proceeding with outdoor=None.")
        elif outdoor_spec.is_fahrenheit:
            warnings.append(f"Converted '{outdoor_eid}' values from °F to °C.")

    vent_specs: dict[str, _SeriesSpec] = {}
    for veid in vent_eids:
        vspec = await _resolve_series_spec(
            client,
            veid,
            vent_meas_ovr,
            _VENT_MEASUREMENT_CANDIDATES,
            ("value", "state"),
            start_ms,
            end_ms,
        )
        if vspec is not None:
            vent_specs[veid] = vspec
        else:
            warnings.append(f"Ventilation entity '{veid}' had no InfluxDB data.")

    # 2. Prepare isolated working snapshot (never mutates live runtime until dry_run=False)
    tau_em, coast_win, peak_delta = runtime._resolve_coasting_settings()
    if reset_existing:
        snapshot = LearningStateSnapshot(
            inertia_profile=runtime.inertia_profile,
            tau_em_min=tau_em,
            max_coast_window_min=coast_win,
            peak_confirm_delta=peak_delta,
        )
    else:
        snapshot = LearningStateSnapshot.from_dict(runtime.snapshot.to_dict())
        snapshot.coasting.apply_profile_settings(
            runtime.inertia_profile,
            tau_em_min=tau_em,
            max_coast_window_min=coast_win,
            peak_confirm_delta=peak_delta,
        )

    rls = snapshot.hum_rls if is_humidity else snapshot.temp_rls
    coasting = snapshot.coasting
    safety = snapshot.safety

    init_accepted = coasting.accepted_count
    init_rejected = dict(coasting.rejected_counts)
    init_rls_rejected = rls.rejected_outliers
    rls_updates = 0

    vent_guard = VentilationGuard(
        mode=vent_mode if vent_specs else VENT_MODE_DISABLED,
        sensors=[s for s in vent_sensors if s in vent_specs],
        logic=VENT_LOGIC_ANY if vent_override else str(defaults["vent_logic"]),
        switch_entity=vent_switch if (vent_switch and vent_switch in vent_specs) else None,
        debounce_sec=int(defaults["vent_debounce_sec"]),
        cooldown_min=max(20, int(defaults["vent_cooldown_min"])) if vent_override else int(defaults["vent_cooldown_min"]),
    )
    replay_hass = _ReplayHass()

    # Tracking state across 30-day chunks
    last_temp_val: float | None = None
    last_temp_min: int | None = None
    last_hum_val: float | None = None
    last_hum_min: int | None = None
    last_outdoor_val: float | None = None
    last_outdoor_min: int | None = None

    cur_dev_active: bool = False
    cur_dev_heating: bool = is_heating_default
    has_dev_seed: bool = False

    cur_vent_states: dict[str, str] = {veid: "off" for veid in vent_specs}
    has_vent_seed: dict[str, bool] = {veid: False for veid in vent_specs}

    # 5-minute RLS block accumulator
    block_minutes = 0
    block_start_val: float | None = None
    block_end_val: float | None = None
    block_u_sum = 0.0
    block_out_diff_sum = 0.0
    block_contaminated = False

    total_minutes_scanned = 0
    in_range_minutes = 0
    skipped_out_of_range_minutes = 0
    global_min = 0

    def _reset_rls_block() -> None:
        nonlocal block_minutes, block_start_val, block_end_val, block_u_sum, block_out_diff_sum, block_contaminated
        block_minutes = 0
        block_start_val = None
        block_end_val = None
        block_u_sum = 0.0
        block_out_diff_sum = 0.0
        block_contaminated = False

    # 3. Sequential 30-day chunked querying & 1-minute replay
    chunk_start = start_dt
    while chunk_start < end_dt:
        chunk_end = min(end_dt, chunk_start + timedelta(days=INFLUXDB_CHUNK_DAYS))
        c_start_ms = int(chunk_start.timestamp() * 1000)
        c_end_ms = int(chunk_end.timestamp() * 1000)
        chunk_minutes = int((chunk_end - chunk_start).total_seconds() // 60)
        if chunk_minutes <= 0:
            break

        # Pre-chunk LOCF seed queries for device & ventilation entities
        seed_dev = await _fetch_pre_chunk_state(client, device_spec, c_start_ms)
        if seed_dev is not None:
            cur_dev_active = _parse_device_active(seed_dev)
            if isinstance(seed_dev, str):
                s_low = seed_dev.strip().lower()
                if s_low in _HEATING_STRINGS:
                    cur_dev_heating = True
                elif s_low in _COOLING_STRINGS:
                    cur_dev_heating = False
            has_dev_seed = True
        elif not has_dev_seed:
            cur_dev_active = False

        for veid, vspec in vent_specs.items():
            seed_v = await _fetch_pre_chunk_state(client, vspec, c_start_ms)
            if seed_v is not None:
                cur_vent_states[veid] = _parse_vent_state(seed_v)
                has_vent_seed[veid] = True
            elif not has_vent_seed[veid]:
                cur_vent_states[veid] = "off"

        # Query aggregated 1-minute sensor series and raw transition events
        temp_map = await _fetch_numeric_chunk(client, temp_spec, c_start_ms, c_end_ms) if temp_spec else {}
        hum_map = await _fetch_numeric_chunk(client, hum_spec, c_start_ms, c_end_ms) if hum_spec else {}
        outdoor_map = await _fetch_numeric_chunk(client, outdoor_spec, c_start_ms, c_end_ms) if outdoor_spec else {}
        dev_events = await _fetch_raw_chunk(client, device_spec, c_start_ms, c_end_ms)
        vent_events: dict[str, list[tuple[int, Any]]] = {
            veid: await _fetch_raw_chunk(client, vspec, c_start_ms, c_end_ms)
            for veid, vspec in vent_specs.items()
        }

        dev_ptr = 0
        dev_len = len(dev_events)
        vent_ptrs: dict[str, int] = {veid: 0 for veid in vent_specs}

        for m in range(chunk_minutes):
            cur_dt = chunk_start + timedelta(minutes=m)

            # Advance device state transitions up to current minute m
            while dev_ptr < dev_len and dev_events[dev_ptr][0] <= m:
                raw_dev = dev_events[dev_ptr][1]
                cur_dev_active = _parse_device_active(raw_dev)
                if isinstance(raw_dev, str):
                    s_low = raw_dev.strip().lower()
                    if s_low in _HEATING_STRINGS:
                        cur_dev_heating = True
                    elif s_low in _COOLING_STRINGS:
                        cur_dev_heating = False
                dev_ptr += 1

            # Advance ventilation states up to current minute m
            if vent_specs:
                for veid, ev_list in vent_events.items():
                    ptr = vent_ptrs[veid]
                    ev_len = len(ev_list)
                    while ptr < ev_len and ev_list[ptr][0] <= m:
                        cur_vent_states[veid] = _parse_vent_state(ev_list[ptr][1])
                        ptr += 1
                    vent_ptrs[veid] = ptr
                    replay_hass.states.set_state(veid, cur_vent_states[veid])
                vent_status = vent_guard.evaluate(replay_hass, now=cur_dt)
            else:
                vent_status = VentStatus(VentState.NORMAL, False, False, 0, False)

            # Update LOCF for numeric sensors
            if m in temp_map:
                last_temp_val = temp_map[m]
                last_temp_min = global_min
            if m in hum_map:
                last_hum_val = hum_map[m]
                last_hum_min = global_min
            if m in outdoor_map:
                last_outdoor_val = outdoor_map[m]
                last_outdoor_min = global_min

            temp_fresh = (
                last_temp_val is not None
                and last_temp_min is not None
                and (global_min - last_temp_min) <= INFLUXDB_MAX_SENSOR_GAP_MIN
            )
            hum_fresh = (
                last_hum_val is not None
                and last_hum_min is not None
                and (global_min - last_hum_min) <= INFLUXDB_MAX_SENSOR_GAP_MIN
            )
            outdoor_fresh = (
                last_outdoor_val is not None
                and last_outdoor_min is not None
                and (global_min - last_outdoor_min) <= INFLUXDB_MAX_SENSOR_GAP_MIN
            )

            temp_now = last_temp_val if temp_fresh else None
            hum_now = last_hum_val if hum_fresh else None
            out_now = last_outdoor_val if outdoor_fresh else None

            # Evaluate regime segmentation bounds & sensor freshness (> 30 min gap -> out_of_range)
            in_range = True
            if is_humidity:
                if hum_now is None:
                    in_range = False
                elif min_hum is not None and hum_now < min_hum:
                    in_range = False
                elif max_hum is not None and hum_now > max_hum:
                    in_range = False

                if in_range and (min_temp is not None or max_temp is not None):
                    if temp_now is None:
                        in_range = False
                    elif min_temp is not None and temp_now < min_temp:
                        in_range = False
                    elif max_temp is not None and temp_now > max_temp:
                        in_range = False
            else:
                if temp_now is None:
                    in_range = False
                elif min_temp is not None and temp_now < min_temp:
                    in_range = False
                elif max_temp is not None and temp_now > max_temp:
                    in_range = False

                if in_range and (min_hum is not None or max_hum is not None):
                    if hum_now is None:
                        in_range = False
                    elif min_hum is not None and hum_now < min_hum:
                        in_range = False
                    elif max_hum is not None and hum_now > max_hum:
                        in_range = False

            total_minutes_scanned += 1

            if not in_range:
                skipped_out_of_range_minutes += 1
                if coasting.phase != CyclePhase.IDLE or block_minutes > 0 or coasting.emitter_charge != 0.0:
                    coasting.abort_cycle_to_idle()
                _reset_rls_block()
                global_min += 1
                continue

            # Resolve value in target learning domain (RH -> AH if humidity controller with temp)
            if is_humidity:
                assert hum_now is not None
                if temp_now is not None:
                    val_in = rh_to_ah(temp_now, hum_now)
                    val_out = rh_to_ah(temp_now, out_now) if out_now is not None else None
                else:
                    val_in = hum_now
                    val_out = out_now
            else:
                assert temp_now is not None
                val_in = temp_now
                val_out = out_now

            in_range_minutes += 1
            blocked = vent_status.should_block_learning
            block_reason = (
                "ventilation_degraded" if vent_status.degraded else "ventilation_contamination"
            )
            u_effort = 1.0 if cur_dev_active else 0.0

            # 1-minute DynamicCoastingLearner step
            coasting.step_cycle(
                is_active=cur_dev_active,
                u_effort=u_effort,
                value=val_in,
                is_heating=cur_dev_heating,
                dt_min=1.0,
                blocked_for_learning=blocked,
                block_reason=block_reason,
                target_val=None,
                solar_rising_active=False,
            )
            completed = coasting.consume_completed_cycle()
            if completed is not None:
                safety.record_prediction_comparison(
                    predicted_coast=completed["predicted_coast"],
                    observed_coast=completed["observed_coast"],
                )

            # 5-minute HuberRLS block accumulation
            if blocked:
                block_contaminated = True
            if block_start_val is None:
                block_start_val = val_in
            block_end_val = val_in
            signed_u = u_effort if cur_dev_heating else -u_effort
            block_u_sum += signed_u
            block_out_diff_sum += (val_in - val_out) if val_out is not None else 0.0
            block_minutes += 1

            if block_minutes >= 5:
                if not block_contaminated and block_start_val is not None and block_end_val is not None:
                    dy_per_min = (block_end_val - block_start_val) / float(block_minutes)
                    u_avg = block_u_sum / float(block_minutes)
                    diff_avg = block_out_diff_sum / float(block_minutes)
                    if is_humidity:
                        ok = rls.update([abs(u_avg), -diff_avg], dy_per_min if cur_dev_heating else -dy_per_min)
                    else:
                        ok = rls.update([u_avg, -diff_avg, 0.0], dy_per_min)
                    if ok:
                        rls_updates += 1
                _reset_rls_block()

            global_min += 1

        chunk_start = chunk_end

    if in_range_minutes == 0:
        warnings.append(
            "Zero minutes fell within the specified indoor temperature/humidity bounds; model parameters remain unchanged."
        )

    recommended = runtime.tuner.compute(
        rls,
        prediction_mode=runtime.prediction_mode,
        baseline=runtime.baseline,
    )
    stage, _ = safety.get_confidence_stage(
        rls_samples=rls.sample_count,
        valid_cycles=coasting.accepted_count,
    )

    applied = False
    if not dry_run:
        await runtime.async_apply_bootstrapped_snapshot(snapshot)
        applied = True

    rejected_delta: dict[str, int] = {}
    for k, v in coasting.rejected_counts.items():
        diff = v - init_rejected.get(k, 0)
        if diff > 0:
            rejected_delta[k] = diff

    coast_factor = (
        round(coasting.k_afterglow, 4)
        if coasting.inertia_profile in (INERTIA_HIGH_INERTIA, INERTIA_CUSTOM)
        else round(coasting.tau_coast, 4)
    )

    return {
        "applied": applied,
        "dry_run": dry_run,
        "period_days": days,
        "total_minutes_scanned": total_minutes_scanned,
        "in_range_minutes": in_range_minutes,
        "skipped_out_of_range_minutes": skipped_out_of_range_minutes,
        "rls_updates": rls_updates,
        "rls_rejected_outliers": max(0, rls.rejected_outliers - init_rls_rejected),
        "accepted_cycles": max(0, coasting.accepted_count - init_accepted),
        "rejected_cycles": rejected_delta,
        "learned_coast_factor": coast_factor,
        "recommended_kp": round(recommended.kp, 4),
        "recommended_ki": round(recommended.ki, 4),
        "recommended_ke": round(recommended.ke, 4),
        "confidence_tier": stage.value,
        "warnings": warnings,
    }
