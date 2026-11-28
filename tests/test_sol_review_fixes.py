"""Regression tests for all 17 GPT 6.2 Sol code review fixes (S01-S08, M01-M05, C01-C04)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
import types
from typing import Any

import pytest
import voluptuous as vol

from custom_components.smart_pid_airconditioner import (
    async_migrate_entry,
    async_remove_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    extract_target_devices,
)
from custom_components.smart_pid_airconditioner.actuator import SwitchCommandGuard
from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.config_flow import (
    SmartPIDThermostatConfigFlow,
    SmartPIDThermostatOptionsFlow,
)
from custom_components.smart_pid_airconditioner.config_utils import (
    SmartPIDExtraStoredData,
    convert_temperature_from_celsius,
    convert_temperature_to_celsius,
)
from custom_components.smart_pid_airconditioner.const import (
    CONF_AUX_TEMP_SENSOR,
    CONF_AVAILABLE_FAN_MODES,
    CONF_AVAILABLE_HVAC_MODES,
    CONF_CONTROL_MODE,
    CONF_COUPLED_AC,
    CONF_COUPLED_DEHUMIDIFIER,
    CONF_DYNAMIC_MIN_OFF_MULTIPLIER,
    CONF_INDOOR_SENSOR,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KD,
    CONF_KE,
    CONF_KEEP_ALIVE,
    CONF_KI,
    CONF_KP,
    CONF_MANUAL_OVERRIDE_MIN,
    CONF_MAX_I,
    CONF_MAX_RUN_MIN,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_OUTDOOR_SENSOR,
    CONF_OUTPUT_SAFETY,
    CONF_PREDICTION_MODE,
    CONF_PWM_DURATION,
    CONF_SAFETY_REST_MIN,
    CONF_SAMPLE_PERIOD,
    CONF_SEC0_ACTION,
    CONF_SEC0_DELTA,
    CONF_SEC0_THRESHOLD,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_HUMIDITY,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONF_VENT_SENSORS,
    CONF_VENT_SWITCH,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    DOMAIN,
    MODE_DEHUMIDITY,
    OVERHEAT_LATCH_TEMP_C,
    OVERHEAT_RESET_TEMP_C,
    SEC0_ACTION_OFF,
    SEC0_ACTION_SETBACK,
    SERVICE_BOOTSTRAP_INFLUXDB,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    TUNING_MODE_AUTO,
)
from custom_components.smart_pid_airconditioner.humidifier import SmartPIDHumidifierEntity
from custom_components.smart_pid_airconditioner.influxdb_bootstrap import (
    async_bootstrap_from_influxdb,
)
from custom_components.smart_pid_airconditioner.learner import (
    TuningParameters,
    TuningSafetyManager,
)
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.storage import (
    LearningStateSnapshot,
    ModelStore,
)
from custom_components.smart_pid_airconditioner.ventilation import VentState, VentStatus
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_OFF, STATE_ON, Platform, UnitOfTemperature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context
from homeassistant.exceptions import (
    ConfigEntryError,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util


def _patch_fast_guard(monkeypatch) -> None:
    orig_init = SwitchCommandGuard.__init__

    def _fast_init(
        self,
        hass,
        *,
        confirm_timeout: float = 0.02,
        off_retry_delays: tuple[float, ...] = (0.005, 0.005),
        service_timeout: float = 0.05,
    ) -> None:
        orig_init(
            self,
            hass,
            confirm_timeout=confirm_timeout,
            off_retry_delays=off_retry_delays,
            service_timeout=service_timeout,
        )

    monkeypatch.setattr(SwitchCommandGuard, "__init__", _fast_init)


def _register_switch_services(hass) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def _on(call) -> None:
        eid = call.data["entity_id"]
        calls.append(("on", eid))
        hass.states.async_set(eid, STATE_ON)

    async def _off(call) -> None:
        eid = call.data["entity_id"]
        calls.append(("off", eid))
        hass.states.async_set(eid, STATE_OFF)

    for dom in ("switch", "homeassistant"):
        hass.services.async_register(dom, "turn_on", _on)
        hass.services.async_register(dom, "turn_off", _off)
    return calls


# ---------------------------------------------------------------------------
# S01: In-flight ON preemption by turn_off and safe shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s01_inflight_on_preempted_by_turn_off_and_shutdown(
    hass, monkeypatch
) -> None:
    """Slow turn_on preempted by turn_off must run corrective OFF; shutdown checks has_inflight_on."""
    _patch_fast_guard(monkeypatch)
    hass.states.async_set("switch.s1_slow", STATE_OFF)

    on_started = asyncio.Event()
    release_on = asyncio.Event()
    calls: list[tuple[str, str]] = []

    async def _slow_on(call) -> None:
        eid = call.data["entity_id"]
        calls.append(("turn_on_start", eid))
        on_started.set()
        await release_on.wait()
        hass.states.async_set(eid, STATE_ON)
        calls.append(("turn_on_done", eid))

    async def _fast_off(call) -> None:
        eid = call.data["entity_id"]
        calls.append(("turn_off", eid))
        hass.states.async_set(eid, STATE_OFF)

    for dom in ("switch", "homeassistant"):
        hass.services.async_register(dom, "turn_on", _slow_on)
        hass.services.async_register(dom, "turn_off", _fast_off)

    guard = SwitchCommandGuard(
        hass, confirm_timeout=0.05, off_retry_delays=(0.005,), service_timeout=1.0
    )

    on_task = asyncio.create_task(guard.async_turn_on("switch.s1_slow", confirm=False))
    await on_started.wait()
    assert guard.has_inflight_on("switch.s1_slow") is True

    off_ok = await guard.async_turn_off("switch.s1_slow", confirm=True)
    assert off_ok is True

    release_on.set()
    on_res = await on_task
    assert on_res is False
    assert guard.has_inflight_on("switch.s1_slow") is False
    assert hass.states.get("switch.s1_slow").state == STATE_OFF
    assert any(c == ("turn_off", "switch.s1_slow") for c in calls)

    # Now verify _async_safe_shutdown_pwm_on_remove sees has_inflight_on even while state is "off"
    on_started.clear()
    release_on.clear()
    calls.clear()

    hass.states.async_set("sensor.s1_temp", "20.0")
    entry = ConfigEntry(
        entry_id="s01_clim",
        title="S01",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.s1_temp",
            CONF_TARGET_DEVICE: "switch.s1_slow",
            CONF_INITIAL_TARGET_TEMP: 24.0,
        },
        version=3,
    )
    ent = SmartPIDThermostatEntity(hass, entry)
    ent.hass = hass
    await ent.async_added_to_hass()
    slow_task = asyncio.create_task(
        ent._switch_guard.async_turn_on("switch.s1_slow", confirm=False)
    )
    await on_started.wait()
    assert ent._switch_guard.has_inflight_on("switch.s1_slow") is True

    shutdown_task = asyncio.create_task(ent.async_will_remove_from_hass())
    await asyncio.sleep(0.02)
    release_on.set()
    await asyncio.gather(slow_task, shutdown_task)
    await hass.async_block_till_done()
    assert hass.states.get("switch.s1_slow").state == STATE_OFF
    assert any(c == ("turn_off", "switch.s1_slow") for c in calls)


# ---------------------------------------------------------------------------
# S02 + S03: Overheat latch persistence, hysteresis, heat_cool & cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s02_s03_overheat_latch_blocks_heat_allows_cool_and_persists(
    hass, monkeypatch
) -> None:
    """Overheat latch persists across reload, blocks HEAT/AUTO, allows COOL, clears at <= 34.0C."""
    _patch_fast_guard(monkeypatch)
    _register_switch_services(hass)
    await async_setup(hass, {})

    hass.states.async_set("sensor.oh_temp", "20.0")
    hass.states.async_set(
        "climate.oh_slave",
        "heat",
        {"hvac_modes": ["off", "cool", "heat", "auto"], "min_temp": 16, "max_temp": 32},
    )

    clim_calls: list[tuple[str, str]] = []

    async def _clim_service(svc: str):
        async def _h(call) -> None:
            eid = call.data["entity_id"]
            if svc == "set_hvac_mode":
                mode = call.data["hvac_mode"]
                clim_calls.append(("set_hvac_mode", mode))
                cur = hass.states.get(eid)
                hass.states.async_set(eid, mode, dict(cur.attributes) if cur else {})
            elif svc == "turn_off":
                clim_calls.append(("turn_off", eid))
                cur = hass.states.get(eid)
                hass.states.async_set(eid, "off", dict(cur.attributes) if cur else {})

        return _h

    hass.services.async_register("climate", "set_hvac_mode", await _clim_service("set_hvac_mode"))
    hass.services.async_register("climate", "turn_off", await _clim_service("turn_off"))

    entry = ConfigEntry(
        entry_id="s02_oh",
        title="S02 HeatCool",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_INDOOR_SENSOR: "sensor.oh_temp",
            CONF_TARGET_CLIMATE: "climate.oh_slave",
            CONF_INITIAL_TARGET_TEMP: 24.0,
        },
        version=3,
    )
    ent = SmartPIDThermostatEntity(hass, entry)
    ent.hass = hass
    await ent.async_added_to_hass()
    await ent.async_set_hvac_mode(HVACMode.HEAT)

    try:
        # Exceed overheat threshold (35.5 >= 35.0) while heating -> triggers latch
        hass.states.async_set("sensor.oh_temp", str(OVERHEAT_LATCH_TEMP_C + 0.5))
        await ent._async_calculate_and_control(force=True)
        assert ent._overheat_latched is True
        assert hass.data[DOMAIN]["overheat_latched"]["s02_oh"] is True
        assert hass.states.get("climate.oh_slave").state == "off"

        # Trying to re-enable HEAT or slave heat mode while latched must be blocked
        clim_calls.clear()
        await ent.async_set_hvac_mode(HVACMode.HEAT)
        await ent._async_set_slave_hvac_mode("heat")
        assert not any(c == ("set_hvac_mode", "heat") for c in clim_calls)

        # Cooling in HEAT_COOL mode MUST still be permitted while latched
        await ent.async_set_hvac_mode(HVACMode.COOL)
        await ent._async_calculate_and_control(force=True)
        assert any(c == ("set_hvac_mode", "cool") for c in clim_calls)

        # Capture extra data and simulate reload at 34.5°C (> 34.0°C reset threshold)
        stored_extra = ent.extra_restore_state_data
        assert stored_extra.overheat_latched is True
        await ent.async_will_remove_from_hass()

        hass.states.async_set("sensor.oh_temp", str(OVERHEAT_RESET_TEMP_C + 0.5))
        ent2 = SmartPIDThermostatEntity(hass, entry)
        ent2.hass = hass

        async def _fake_extra():
            return stored_extra

        ent2.async_get_last_extra_data = _fake_extra  # type: ignore[method-assign]
        await ent2.async_added_to_hass()
        assert ent2._overheat_latched is True

        # Drops below 34.0°C -> latch clears
        hass.states.async_set("sensor.oh_temp", str(OVERHEAT_RESET_TEMP_C - 0.1))
        await ent2._async_calculate_and_control(force=True)
        assert ent2._overheat_latched is False
        assert hass.data[DOMAIN]["overheat_latched"]["s02_oh"] is False

        await ent2.async_will_remove_from_hass()
        await async_remove_entry(hass, entry)
        assert "s02_oh" not in hass.data[DOMAIN]["overheat_latched"]
    finally:
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# S04: Per-device run durations & external state/keep-alive tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s04_dual_per_device_run_durations_and_external_tracking(
    hass, virtual_clock, monkeypatch
) -> None:
    """Dual PWM climate/humidifier isolate _last_run_durations per device and track external toggles."""
    _patch_fast_guard(monkeypatch)
    _register_switch_services(hass)
    t0 = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(t0)

    hass.states.async_set("sensor.s4_temp", "18.0")
    hass.states.async_set("switch.s4_heat", STATE_OFF)
    hass.states.async_set("switch.s4_cool", STATE_OFF)

    c_entry = ConfigEntry(
        entry_id="s04_clim",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.s4_temp",
            CONF_TARGET_DEVICE_HEAT: "switch.s4_heat",
            CONF_TARGET_DEVICE_COOL: "switch.s4_cool",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_MIN_OFF_TIME: 60,
            CONF_DYNAMIC_MIN_OFF_MULTIPLIER: 0.5,
            CONF_SAMPLE_PERIOD: 600,
            CONF_KEEP_ALIVE: 0,
        },
        version=3,
    )
    c_ent = SmartPIDThermostatEntity(hass, c_entry)
    c_ent.hass = hass
    await c_ent.async_added_to_hass()
    await c_ent.async_set_hvac_mode(HVACMode.HEAT)

    hass.states.async_set("sensor.s4_hum", "30.0")
    hass.states.async_set("switch.s4_hum_sw", STATE_OFF)
    hass.states.async_set("switch.s4_dehum_sw", STATE_OFF)
    h_entry = ConfigEntry(
        entry_id="s04_hum",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.s4_hum",
            CONF_TARGET_DEVICE_HUMIDITY: "switch.s4_hum_sw",
            CONF_TARGET_DEVICE_DEHUMIDITY: "switch.s4_dehum_sw",
            CONF_INITIAL_TARGET_TEMP: 50.0,
            CONF_MIN_OFF_TIME: 60,
            CONF_DYNAMIC_MIN_OFF_MULTIPLIER: 0.5,
            CONF_SAMPLE_PERIOD: 600,
            CONF_KEEP_ALIVE: 0,
        },
        version=3,
    )
    h_ent = SmartPIDHumidifierEntity(hass, h_entry)
    h_ent.hass = hass
    await h_ent.async_added_to_hass()
    await h_ent.async_turn_on()

    try:
        # Advance 400s while heating / humidifying run
        await virtual_clock.advance(400.0)
        t1 = dt_util.utcnow()
        c_ent._record_confirmed_off("switch.s4_heat", t1, run_dur_override=400.0)
        c_ent._record_confirmed_off("switch.s4_cool", t1, run_dur_override=60.0)
        assert c_ent._last_run_durations["switch.s4_heat"] == pytest.approx(400.0)
        assert c_ent._last_run_durations["switch.s4_cool"] == pytest.approx(60.0)
        assert c_ent._compute_effective_min_off(device_id="switch.s4_heat") == pytest.approx(200.0)
        assert c_ent._compute_effective_min_off(device_id="switch.s4_cool") == pytest.approx(60.0)

        h_ent._record_confirmed_off("switch.s4_hum_sw", t1, run_dur_override=400.0)
        h_ent._record_confirmed_off("switch.s4_dehum_sw", t1, run_dur_override=80.0)
        assert h_ent._last_run_durations["switch.s4_hum_sw"] == pytest.approx(400.0)
        assert h_ent._last_run_durations["switch.s4_dehum_sw"] == pytest.approx(80.0)
        assert h_ent._compute_effective_min_off(device_id="switch.s4_hum_sw") == pytest.approx(200.0)
        assert h_ent._compute_effective_min_off(device_id="switch.s4_dehum_sw") == pytest.approx(60.0)

        # ExtraStoredData round-trips last_run_durations
        extra_h = SmartPIDExtraStoredData.from_dict(h_ent.extra_restore_state_data.as_dict())
        assert extra_h is not None
        assert extra_h.last_run_durations["switch.s4_dehum_sw"] == pytest.approx(80.0)

        # External ON -> OFF state_changed updates timestamps when unrecorded (switch off inactive interlock first)
        await c_ent.async_set_hvac_mode(HVACMode.OFF)
        await hass.async_block_till_done()
        await virtual_clock.advance(1.0)
        hass.states.async_set("switch.s4_heat", STATE_ON)
        await hass.async_block_till_done()
        assert "switch.s4_heat" in c_ent._last_switch_on_times
        await virtual_clock.advance(50.0)
        hass.states.async_set("switch.s4_heat", STATE_OFF)
        await hass.async_block_till_done()
        assert c_ent._last_run_durations["switch.s4_heat"] == pytest.approx(50.0)
    finally:
        await c_ent.async_will_remove_from_hass()
        await h_ent.async_will_remove_from_hass()
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# S05: Humidifier PWM min_on_time & deferred turn_on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s05_humidifier_min_on_and_deferred_on(
    hass, virtual_clock, monkeypatch
) -> None:
    """PWM force=False respects min_on_time; force=True turns OFF at once; deferred ON fires after min_off."""
    _patch_fast_guard(monkeypatch)
    _register_switch_services(hass)
    t0 = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(t0)

    hass.states.async_set("sensor.s5_hum", "80.0")
    hass.states.async_set("switch.s5_dehum", STATE_OFF)

    entry = ConfigEntry(
        entry_id="s05_hum",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.s5_hum",
            CONF_TARGET_DEVICE: "switch.s5_dehum",
            CONF_INITIAL_TARGET_TEMP: 50.0,
            CONF_KP: 5.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 120,
            CONF_MIN_OFF_TIME: 180,
            CONF_DYNAMIC_MIN_OFF_MULTIPLIER: 0.0,
            CONF_SAMPLE_PERIOD: 600,
            CONF_KEEP_ALIVE: 0,
        },
        version=3,
    )
    ent = SmartPIDHumidifierEntity(hass, entry)
    ent.hass = hass
    await ent.async_added_to_hass()
    await ent.async_turn_on()
    assert hass.states.get("switch.s5_dehum").state == STATE_ON

    try:
        # After 40s (< 120s min_on), non-forced OFF defers switch OFF
        await virtual_clock.advance(40.0)
        res_defer = await ent._async_turn_off_switch("switch.s5_dehum", force=False)
        assert res_defer is False
        assert hass.states.get("switch.s5_dehum").state == STATE_ON

        # Advance 90s (total 130s >= 120s min_on) -> deferred OFF fires
        await virtual_clock.advance(90.0)
        assert hass.states.get("switch.s5_dehum").state == STATE_OFF

        # Advance past min_off (190s >= 180s) -> ON starts, then force=True (async_turn_off) turns OFF before min_on
        await virtual_clock.advance(190.0)
        hass.states.async_set("sensor.s5_hum", "80.0")
        await ent._async_calculate_and_control(force=True)
        assert hass.states.get("switch.s5_dehum").state == STATE_ON

        await virtual_clock.advance(20.0)
        await ent.async_turn_off()
        assert hass.states.get("switch.s5_dehum").state == STATE_OFF

        # Turn back on 60s after OFF -> blocked initially, then deferred ON executes at +180s
        await virtual_clock.advance(60.0)
        hass.states.async_set("sensor.s5_hum", "80.0")
        await ent.async_turn_on()
        assert hass.states.get("switch.s5_dehum").state == STATE_OFF

        await virtual_clock.advance(125.0)
        assert hass.states.get("switch.s5_dehum").state == STATE_ON
    finally:
        await ent.async_will_remove_from_hass()
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# S06: Humidifier Section 0 and climate_delta fail-safe slave protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s06_humidifier_sec0_and_failsafe_slave_guard(
    hass, virtual_clock, monkeypatch
) -> None:
    """Fail-safe must never wake an OFF slave humidifier; Sec0 setbacks and freezes integral."""
    _patch_fast_guard(monkeypatch)
    t0 = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(t0)

    hum_calls: list[tuple[str, Any]] = []

    async def _on(call) -> None:
        eid = call.data["entity_id"]
        hum_calls.append(("turn_on", eid))
        hass.states.async_set(eid, STATE_ON)

    async def _off(call) -> None:
        eid = call.data["entity_id"]
        hum_calls.append(("turn_off", eid))
        hass.states.async_set(eid, STATE_OFF)

    async def _set_hum(call) -> None:
        hum_calls.append(("set_humidity", call.data["humidity"]))

    hass.services.async_register("humidifier", "turn_on", _on)
    hass.services.async_register("humidifier", "turn_off", _off)
    hass.services.async_register("humidifier", "set_humidity", _set_hum)
    hass.services.async_register("homeassistant", "turn_off", _off)

    hass.states.async_set("sensor.s6_hum", "50.2")
    hass.states.async_set("humidifier.s6_slave", STATE_OFF)

    entry = ConfigEntry(
        entry_id="s06_hum",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_INDOOR_SENSOR: "sensor.s6_hum",
            CONF_TARGET_DEVICE: "humidifier.s6_slave",
            CONF_INITIAL_TARGET_TEMP: 50.0,
            CONF_KP: 10.0,
            CONF_KI: 1.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_SEC0_ACTION: SEC0_ACTION_SETBACK,
            CONF_SEC0_DELTA: 5.0,
            CONF_SEC0_THRESHOLD: 5.0,
            CONF_SAMPLE_PERIOD: 60,
        },
        version=3,
    )
    ent = SmartPIDHumidifierEntity(hass, entry)
    ent.hass = hass
    await ent.async_added_to_hass()

    try:
        # 1) Sensor unavailable + slave OFF -> fail-safe stays Section 0 and NEVER wakes OFF slave
        ent._is_on = True
        hass.states.async_set("sensor.s6_hum", "unavailable")
        ent._sensor_stale_count = 3
        hum_calls.clear()
        await ent._async_calculate_and_control(now=t0, force=True)
        assert ent._fail_safe_active is True
        assert ent._active_section == 0
        assert not any(c[0] == "turn_on" for c in hum_calls)

        # 2) Recover with demand > sec0_threshold + hysteresis (>6.5%): after MIN_DWELL_SEC0 enters Sec 1,
        # then drops to 2% (<= 5%) and after MIN_DWELL_ACTIVE enters Sec0 setback (+5% -> 55%) & freezes I
        t_rec = t0 + timedelta(seconds=310)
        dt_util.set_utcnow(t_rec)
        hass.states.async_set("sensor.s6_hum", "51.5")  # 1.5 * 10 = 15% output -> Sec 1
        hass.states.async_set("humidifier.s6_slave", STATE_ON)
        ent._sensor_stale_count = 0
        ent._sensor_fault_active = False
        ent._fail_safe_active = False
        await ent._async_calculate_and_control(now=t_rec, force=True)
        assert ent._active_section == 1

        t_dwell = t_rec + timedelta(seconds=260)
        dt_util.set_utcnow(t_dwell)
        ent._integral = 0.0
        hass.states.async_set("sensor.s6_hum", "50.2")  # 0.2 * 10 = 2% <= 5% -> Sec 0
        await ent._async_calculate_and_control(now=t_dwell, force=False)
        assert ent._active_section == 0
        assert ent._dispatched_humidity == 55
        frozen_i = ent._integral

        t_freeze = t_dwell + timedelta(seconds=60)
        dt_util.set_utcnow(t_freeze)
        hass.states.async_set("sensor.s6_hum", "51.0")
        await ent._async_calculate_and_control(now=t_freeze, force=False)
        assert ent._integral == pytest.approx(frozen_i)

        # 3) If sec0_action == "off" and fail-safe hits while slave is ON -> turns slave OFF
        ent._sec0_action = SEC0_ACTION_OFF
        hass.states.async_set("sensor.s6_hum", "unavailable")
        ent._sensor_stale_count = 3
        hum_calls.clear()
        t_fs = t_freeze + timedelta(seconds=10)
        dt_util.set_utcnow(t_fs)
        await ent._async_calculate_and_control(now=t_fs, force=True)
        assert hass.states.get("humidifier.s6_slave").state == STATE_OFF
    finally:
        await ent.async_will_remove_from_hass()
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# S07: Safety watchdog measures physical running state & beats manual_override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s07_watchdog_uses_physical_state_and_overrides_manual(
    hass, monkeypatch
) -> None:
    """Idle slave must NOT trip watchdog; physically running >= max_run_min overrides manual_override."""
    _patch_fast_guard(monkeypatch)
    t0 = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(t0)

    async def _clim_off(call) -> None:
        eid = call.data["entity_id"]
        cur = hass.states.get(eid)
        hass.states.async_set(eid, "off", dict(cur.attributes) if cur else {})

    async def _hum_off(call) -> None:
        eid = call.data["entity_id"]
        cur = hass.states.get(eid)
        hass.states.async_set(eid, "off", dict(cur.attributes) if cur else {})

    async def _clim_set_mode(call) -> None:
        eid = call.data["entity_id"]
        cur = hass.states.get(eid)
        hass.states.async_set(eid, call.data["hvac_mode"], dict(cur.attributes) if cur else {})

    hass.services.async_register("climate", "turn_off", _clim_off)
    hass.services.async_register("climate", "set_hvac_mode", _clim_set_mode)
    hass.services.async_register("climate", "set_temperature", lambda c: None)
    hass.services.async_register("humidifier", "turn_off", _hum_off)
    hass.services.async_register("humidifier", "set_humidity", lambda c: None)
    hass.services.async_register("homeassistant", "turn_off", _hum_off)

    hass.states.async_set("sensor.wd7_temp", "28.0")
    hass.states.async_set(
        "climate.wd7_ac",
        "cool",
        {"hvac_action": "idle", "hvac_modes": ["off", "cool"], "min_temp": 16, "max_temp": 30},
    )
    c_entry = ConfigEntry(
        entry_id="s07_c",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_INDOOR_SENSOR: "sensor.wd7_temp",
            CONF_TARGET_CLIMATE: "climate.wd7_ac",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_MAX_RUN_MIN: 30,
            CONF_SAFETY_REST_MIN: 10,
            CONF_MANUAL_OVERRIDE_MIN: 60,
        },
        version=3,
    )
    c_ent = SmartPIDThermostatEntity(hass, c_entry)
    c_ent.hass = hass
    await c_ent.async_added_to_hass()
    await c_ent.async_set_hvac_mode(HVACMode.COOL)

    hass.states.async_set("sensor.wd7_hum", "75.0")
    hass.states.async_set("humidifier.wd7_dh", "on", {"action": "idle"})
    h_entry = ConfigEntry(
        entry_id="s07_h",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_INDOOR_SENSOR: "sensor.wd7_hum",
            CONF_TARGET_DEVICE: "humidifier.wd7_dh",
            CONF_INITIAL_TARGET_TEMP: 50.0,
            CONF_MAX_RUN_MIN: 30,
            CONF_SAFETY_REST_MIN: 10,
            CONF_MANUAL_OVERRIDE_MIN: 60,
        },
        version=3,
    )
    h_ent = SmartPIDHumidifierEntity(hass, h_entry)
    h_ent.hass = hass
    await h_ent.async_added_to_hass()
    await h_ent.async_turn_on()

    try:
        # Slave idle -> even after >30 min, watchdog does NOT trip
        c_ent._continuous_run_start = None
        h_ent._continuous_run_start = None
        t1 = t0 + timedelta(minutes=20)
        dt_util.set_utcnow(t1)
        hass.states.async_set("sensor.wd7_temp", "28.0")
        hass.states.async_set("sensor.wd7_hum", "75.0")
        t_idle_check = t1 + timedelta(minutes=15)
        dt_util.set_utcnow(t_idle_check)
        await c_ent._async_calculate_and_control(now=t_idle_check, force=False)
        await h_ent._async_calculate_and_control(now=t_idle_check, force=False)
        assert c_ent._watchdog_rest_until is None
        assert h_ent._watchdog_rest_until is None

        # Active physical running + manual override -> after >= 30 min, watchdog overrides manual_override and forces OFF
        hass.states.async_set(
            "climate.wd7_ac",
            "cool",
            {"hvac_action": "cooling", "hvac_modes": ["off", "cool"]},
        )
        hass.states.async_set("humidifier.wd7_dh", "on", {"action": "drying"})
        hass.states.async_set("sensor.wd7_temp", "28.0")
        hass.states.async_set("sensor.wd7_hum", "75.0")
        c_ent._manual_override_until = t_idle_check + timedelta(minutes=45)
        h_ent._manual_override_until = t_idle_check + timedelta(minutes=45)
        c_ent._continuous_run_start = t_idle_check
        h_ent._continuous_run_start = t_idle_check

        t2 = t_idle_check + timedelta(minutes=20)
        dt_util.set_utcnow(t2)
        hass.states.async_set("sensor.wd7_temp", "28.0")
        hass.states.async_set("sensor.wd7_hum", "75.0")
        t3 = t_idle_check + timedelta(minutes=31)
        dt_util.set_utcnow(t3)
        await c_ent._async_calculate_and_control(now=t3, force=False)
        await h_ent._async_calculate_and_control(now=t3, force=False)
        assert c_ent._watchdog_rest_until is not None
        assert h_ent._watchdog_rest_until is not None
        assert c_ent._active_section == 0
        assert h_ent._active_section == 0
        assert hass.states.get("climate.wd7_ac").state == "off"
        assert hass.states.get("humidifier.wd7_dh").state == "off"
    finally:
        await c_ent.async_will_remove_from_hass()
        await h_ent.async_will_remove_from_hass()
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# S08: Bounded SwitchCommandGuard service_timeout & parallel dual shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s08_service_timeout_and_parallel_shutdown(hass, monkeypatch) -> None:
    """Hung turn_off service aborts within service_timeout/deadline_timeout; dual shutdown runs in parallel."""
    _patch_fast_guard(monkeypatch)

    async def _hung_off(call) -> None:
        await asyncio.sleep(5.0)

    hass.services.async_register("switch", "turn_off", _hung_off)
    hass.services.async_register("homeassistant", "turn_off", _hung_off)
    hass.states.async_set("switch.s8_hung", STATE_ON)

    guard = SwitchCommandGuard(
        hass,
        confirm_timeout=0.05,
        off_retry_delays=(0.01,),
        service_timeout=0.03,
    )
    t_start = time.monotonic()
    ok = await guard.async_turn_off(
        "switch.s8_hung", confirm=True, deadline_timeout=0.09
    )
    elapsed = time.monotonic() - t_start
    assert ok is False
    assert elapsed < 0.6

    # Parallel shutdown check on dual switches sleeping 0.08s each -> finishes together < 0.15s
    async def _slow_off(call) -> None:
        await asyncio.sleep(0.08)
        hass.states.async_set(call.data["entity_id"], STATE_OFF)

    hass.services.async_register("switch", "turn_off", _slow_off)
    hass.services.async_register("homeassistant", "turn_off", _slow_off)
    hass.states.async_set("switch.s8_cool", STATE_ON)
    hass.states.async_set("switch.s8_heat", STATE_ON)
    hass.states.async_set("sensor.s8_temp", "25.0")

    entry = ConfigEntry(
        entry_id="s08_dual",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.s8_temp",
            CONF_TARGET_DEVICE_COOL: "switch.s8_cool",
            CONF_TARGET_DEVICE_HEAT: "switch.s8_heat",
            CONF_INITIAL_TARGET_TEMP: 24.0,
        },
        version=3,
    )
    ent = SmartPIDThermostatEntity(hass, entry)
    ent.hass = hass
    ent._switch_guard = SwitchCommandGuard(
        hass, confirm_timeout=0.2, off_retry_delays=(0.01,), service_timeout=0.2
    )
    t_par = time.monotonic()
    await ent.async_will_remove_from_hass()
    par_elapsed = time.monotonic() - t_par
    assert par_elapsed < 0.25
    assert hass.states.get("switch.s8_cool").state == STATE_OFF
    assert hass.states.get("switch.s8_heat").state == STATE_OFF

    # Also test humidifier _async_turn_off_all_devices parallel shutdown
    hass.states.async_set("switch.s8_hum", STATE_ON)
    hass.states.async_set("switch.s8_dehum", STATE_ON)
    hass.states.async_set("sensor.s8_hum", "60.0")
    h_entry = ConfigEntry(
        entry_id="s08_hum",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.s8_hum",
            CONF_TARGET_DEVICE_HUMIDITY: "switch.s8_hum",
            CONF_TARGET_DEVICE_DEHUMIDITY: "switch.s8_dehum",
            CONF_INITIAL_TARGET_TEMP: 50.0,
        },
        version=3,
    )
    h_ent = SmartPIDHumidifierEntity(hass, h_entry)
    h_ent.hass = hass
    h_ent._switch_guard = SwitchCommandGuard(
        hass, confirm_timeout=0.2, off_retry_delays=(0.01,), service_timeout=0.2
    )
    t_hpar = time.monotonic()
    await h_ent._async_turn_off_all_devices(force=True)
    assert (time.monotonic() - t_hpar) < 0.25
    assert hass.states.get("switch.s8_hum").state == STATE_OFF
    assert hass.states.get("switch.s8_dehum").state == STATE_OFF
    await h_ent.async_will_remove_from_hass()
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# M01: Fahrenheit & Kelvin temperature unit normalization and dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m01_fahrenheit_and_kelvin_normalization_and_dispatch(
    hass, monkeypatch
) -> None:
    """HA configured in °F reads °F sensor as °C internally and converts slave set_temperature back to °F."""
    _patch_fast_guard(monkeypatch)
    assert convert_temperature_to_celsius(77.0, UnitOfTemperature.FAHRENHEIT) == pytest.approx(25.0)
    assert convert_temperature_to_celsius(298.15, "K") == pytest.approx(25.0)
    assert convert_temperature_from_celsius(22.0, "K") == pytest.approx(295.15)
    assert convert_temperature_from_celsius(22.0, UnitOfTemperature.FAHRENHEIT) == pytest.approx(71.6)

    hass.config.units.temperature_unit = UnitOfTemperature.FAHRENHEIT
    dispatched_temps: list[float] = []

    async def _set_temp(call) -> None:
        dispatched_temps.append(float(call.data["temperature"]))

    hass.services.async_register("climate", "set_temperature", _set_temp)
    hass.services.async_register("climate", "set_hvac_mode", lambda c: None)
    hass.states.async_set(
        "sensor.m01_temp",
        "77.0",
        {"unit_of_measurement": "°F"},
    )
    hass.states.async_set(
        "climate.m01_slave",
        "cool",
        {
            "hvac_modes": ["off", "cool"],
            "min_temp": 60.0,
            "max_temp": 86.0,
            "temperature_unit": "°F",
        },
    )

    entry = ConfigEntry(
        entry_id="m01_f",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_INDOOR_SENSOR: "sensor.m01_temp",
            CONF_TARGET_CLIMATE: "climate.m01_slave",
            CONF_INITIAL_TARGET_TEMP: 24.0,  # °C default
            CONF_KP: 20.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
        },
        version=3,
    )
    ent = SmartPIDThermostatEntity(hass, entry)
    ent.hass = hass

    async def _restored_f_state():
        return types.SimpleNamespace(
            state="cool",
            attributes={"temperature": 71.6, "temperature_unit": "°F"},
        )

    ent.async_get_last_state = _restored_f_state  # type: ignore[method-assign]
    await ent.async_added_to_hass()
    await ent.async_set_hvac_mode(HVACMode.COOL)

    try:
        assert ent._indoor_temp == pytest.approx(25.0, abs=0.01)
        assert ent._target_temp == pytest.approx(22.0, abs=0.01)
        # Error is 25.0 - 22.0 = +3.0°C (NOT +5.4°F!)
        assert ent._pid_error == pytest.approx(3.0, abs=0.05)
        assert dispatched_temps, "Expected climate.set_temperature dispatch in °F"
        assert dispatched_temps[-1] > 50.0  # Dispatched in °F
    finally:
        await ent.async_will_remove_from_hass()
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# M02: Learning unit ("ah" vs "rh") consistency & reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m02_ah_unit_mismatch_reset_runtime_and_early_stop(hass) -> None:
    """ModelStore resets mismatched learning_unit; AH runtime skips missing aux_temp and converts coast to %RH."""
    store = ModelStore(hass, "m02_store")
    rh_snap = LearningStateSnapshot(learning_unit="rh")
    rh_snap.hum_rls.update([2.0, 0.5], 0.1)
    assert rh_snap.hum_rls.sample_count == 1
    await store.async_save(rh_snap)

    loaded_same = await store.async_load(expected_learning_unit="rh")
    assert loaded_same.hum_rls.sample_count == 1
    loaded_ah = await store.async_load(expected_learning_unit="ah")
    assert loaded_ah.learning_unit == "ah"
    assert loaded_ah.hum_rls.sample_count == 0

    # Runtime with aux_temp_sensor -> learning_unit == "ah"
    hass.states.async_set("sensor.m02_hum", "65.0")
    hass.states.async_set("sensor.m02_temp", "unavailable")
    entry = ConfigEntry(
        entry_id="m02_rt",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.m02_hum",
            CONF_AUX_TEMP_SENSOR: "sensor.m02_temp",
            CONF_TARGET_DEVICE: "switch.m02_sw",
        },
        version=3,
    )
    assert await async_setup_entry(hass, entry) is True
    rt: SmartPIDRuntime = entry.runtime_data
    try:
        assert rt.learning_unit == "ah"
        rt.update_observation(
            indoor_val=65.0,
            outdoor_val=None,
            target_val=50.0,
            u_effort=0.8,
            is_active=True,
            is_heating=False,
            vent_status=VentStatus(VentState.NORMAL, False, False, 0, False),
        )
        # aux_temp unavailable -> learning step skips instead of poisoning AH model with raw %RH
        await rt.async_step_learning(now=dt_util.utcnow())
        assert rt.snapshot.hum_rls.sample_count == 0

        # Valid aux_temp -> early-stop converts g/m3 prediction to %RH
        hass.states.async_set("sensor.m02_temp", "22.0")
        rt.tuning_mode = TUNING_MODE_AUTO
        rt.snapshot.coasting.accepted_count = 15
        rt.snapshot.hum_rls.sample_count = 25
        rt.snapshot.coasting._recent_values.extend([12.0, 11.6, 11.2])
        rt.snapshot.safety.accuracy_count = 6
        rt._obs_indoor = 55.0
        rt._obs_is_heating = False
        pred_ah = rt.get_early_stop_prediction()
        pred_rh = rt.get_early_stop_prediction_in_control_units(
            current_rh=55.0, is_heating=False
        )
        assert pred_ah > 0.0
        assert pred_rh > pred_ah

        # InfluxDB bootstrap for "ah" must raise when temperature series is missing
        from test_influxdb_bootstrap import FakeInfluxSession

        session = FakeInfluxSession()
        hass._mock_http_session = session
        now_ms = int(dt_util.utcnow().timestamp() * 1000)
        session.add_point("%", "m02_hum", "value", now_ms - 60000, 60.0)
        session.add_point("switch", "m02_sw", "value", now_ms - 60000, 1)
        with pytest.raises(HomeAssistantError):
            await async_bootstrap_from_influxdb(
                hass, rt, {"days": 1, "dry_run": True, "reset_existing": False}
            )
    finally:
        await async_unload_entry(hass, entry)
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# M03: ConfigEntry v2 -> v3 humidity Ki scaling & legacy integral conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m03_v2_to_v3_humidity_ki_and_legacy_integral_once(hass) -> None:
    """v2->v3 scales humidity Ki by 60/sample_period only; legacy ExtraStoredData integral converts once."""
    hum_v2 = ConfigEntry(
        entry_id="m03_hum",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.m03_h",
            CONF_TARGET_DEVICE: "switch.m03_h",
            CONF_KP: 20.0,
            CONF_KI: 0.05,
            CONF_SAMPLE_PERIOD: 30,
        },
        options={"ki_group": {CONF_KI: 0.05}},
        version=2,
    )
    clim_v2 = ConfigEntry(
        entry_id="m03_clim",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.m03_t",
            CONF_TARGET_DEVICE: "switch.m03_t",
            CONF_KI: 0.05,
            CONF_SAMPLE_PERIOD: 30,
        },
        version=2,
    )

    assert await async_migrate_entry(hass, hum_v2) is True
    assert hum_v2.version == 3
    assert hum_v2.data[CONF_KI] == pytest.approx(0.10)
    assert hum_v2.options["ki_group"][CONF_KI] == pytest.approx(0.10)

    assert await async_migrate_entry(hass, clim_v2) is True
    assert clim_v2.version == 3
    assert clim_v2.data[CONF_KI] == pytest.approx(0.05)

    # Legacy ExtraStoredData (integral=10.0) converts once -> 10.0 * 0.10 * (30/60) = 0.5
    hass.states.async_set("sensor.m03_h", "55.0")
    hass.states.async_set("switch.m03_h", STATE_OFF)
    ent = SmartPIDHumidifierEntity(hass, hum_v2)
    ent.hass = hass

    async def _last_state():
        return types.SimpleNamespace(state=STATE_ON, attributes={"humidity": 50})

    async def _legacy_extra():
        return SmartPIDExtraStoredData(
            last_switch_on_times={},
            last_switch_off_times={},
            active_section=1,
            integral=10.0,
            integral_format=None,
        )

    ent.async_get_last_state = _last_state  # type: ignore[method-assign]
    ent.async_get_last_extra_data = _legacy_extra  # type: ignore[method-assign]
    await ent.async_added_to_hass()
    assert ent._integral == pytest.approx(0.5)
    saved = ent.extra_restore_state_data
    assert saved.integral_format == "per_minute"
    await ent.async_will_remove_from_hass()

    # Second reload preserves 0.5 without rescaling again
    ent2 = SmartPIDHumidifierEntity(hass, hum_v2)
    ent2.hass = hass
    ent2.async_get_last_state = _last_state  # type: ignore[method-assign]

    async def _per_min_extra():
        return saved

    ent2.async_get_last_extra_data = _per_min_extra  # type: ignore[method-assign]
    await ent2.async_added_to_hass()
    assert ent2._integral == pytest.approx(0.5)
    await ent2.async_will_remove_from_hass()
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# M04: TuningSafetyManager applied_params persistence & prediction_mode gain guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m04_applied_params_rollback_and_prediction_mode_guard(hass) -> None:
    """applied_params persists/rolls back via TuningSafetyManager; identical gains skip begin_application."""
    p_old = TuningParameters(kp=20.0, ki=0.05, ke=0.6)
    p_new = TuningParameters(kp=35.0, ki=0.12, ke=0.9)
    mgr = TuningSafetyManager(eval_cycles=2, degradation_threshold=0.3)
    mgr.begin_application(p_old, baseline_mae=0.2, baseline_overshoot=0.2, applied_params=p_new)
    restored = TuningSafetyManager.from_dict(mgr.to_dict())
    restored.eval_cycles = 2
    assert restored.applied_params == p_new

    assert restored.record_post_apply_cycle(cycle_mae=0.6, cycle_overshoot=0.5) is None
    rb = restored.record_post_apply_cycle(cycle_mae=0.7, cycle_overshoot=0.6)
    assert rb == p_old
    assert restored.applied_params == p_old

    # SmartPIDRuntime.async_set_prediction_mode with unchanged gains does not trigger begin_application
    entry = ConfigEntry(
        entry_id="m04_rt",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_INDOOR_SENSOR: "sensor.m04_t",
            CONF_TARGET_CLIMATE: "climate.m04_c",
            CONF_TUNING_MODE: TUNING_MODE_AUTO,
            CONF_PREDICTION_MODE: "balanced",
        },
        version=3,
    )
    rt = SmartPIDRuntime(hass, entry)
    await rt.async_setup()
    try:
        # Force identical _recompute_recommended across modes
        fixed = TuningParameters(kp=24.0, ki=0.08, ke=0.5)
        rt._applied_auto_params = fixed
        rt._recompute_recommended = lambda rls=None: fixed  # type: ignore[method-assign]
        rt.snapshot.coasting.accepted_count = 20
        rt.snapshot.temp_rls.sample_count = 30
        await rt.async_set_prediction_mode("eco")
        assert rt.snapshot.safety.season_applies == 0
    finally:
        await rt.async_unload()
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# M05: Bootstrap executor offload, generation check, and unload cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m05_bootstrap_generation_guard_executor_and_unload_cancel(
    hass, monkeypatch
) -> None:
    """Bootstrap uses executor, rejects stale reset generation, and unload cancels active task."""
    executor_calls: list[Any] = []
    orig_exec = hass.async_add_executor_job

    async def _spy_exec(func, *args):
        executor_calls.append(getattr(func, "__name__", str(func)))
        return await orig_exec(func, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", _spy_exec)

    from test_influxdb_bootstrap import FakeInfluxSession

    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)
    entry = ConfigEntry(
        entry_id="m05_bs",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.m05_t",
            CONF_TARGET_DEVICE: "switch.m05_ac",
        },
        version=3,
    )
    assert await async_setup_entry(hass, entry) is True
    rt: SmartPIDRuntime = entry.runtime_data
    session = FakeInfluxSession()
    hass._mock_http_session = session
    t0 = now - timedelta(hours=2)
    for m in range(10):
        session.add_point("switch", "m05_ac", "value", int((t0 + timedelta(minutes=m)).timestamp() * 1000), 1)
        session.add_point("°C", "m05_t", "value", int((t0 + timedelta(minutes=m)).timestamp() * 1000), 26.0 - 0.1 * m)

    try:
        res = await async_bootstrap_from_influxdb(
            hass, rt, {"days": 1, "dry_run": True, "reset_existing": False}
        )
        assert res["dry_run"] is True
        assert "_replay_chunk_sync" in executor_calls

        # Stale generation check
        snap_gen = rt._snapshot_generation
        reset_gen = rt._reset_generation
        await rt.async_reset_learning()
        with pytest.raises(HomeAssistantError, match="reset"):
            await rt.async_apply_bootstrapped_snapshot(
                LearningStateSnapshot(),
                expected_snapshot_generation=snap_gen,
                expected_reset_generation=reset_gen,
            )

        # Register active bootstrap task -> async_unload cancels it cleanly
        cancelled_ev = asyncio.Event()

        async def _long_task() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled_ev.set()
                raise

        task = asyncio.create_task(_long_task())
        rt.register_bootstrap_task(task)
        await asyncio.sleep(0)
        await async_unload_entry(hass, entry)
        assert cancelled_ev.is_set()
        assert rt.is_active_runtime() is False
    finally:
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# C01: Duplicate dual target device rejection across flows & setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c01_duplicate_dual_target_rejected(hass) -> None:
    """ConfigFlow, OptionsFlow, and async_setup_entry reject same device in dual roles."""
    flow = SmartPIDThermostatConfigFlow()
    flow.hass = hass
    await flow.async_step_user(
        {
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
        }
    )
    res_cf = await flow.async_step_device_sensor(
        {
            CONF_INDOOR_SENSOR: "sensor.c1_t",
            CONF_TARGET_DEVICE_COOL: "switch.same",
            CONF_TARGET_DEVICE_HEAT: "switch.same",
        }
    )
    assert res_cf["type"] == "form"
    assert res_cf["errors"].get("base") == "duplicate_dual_target_device"

    hum_entry = ConfigEntry(
        entry_id="c01_dup_hum",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.c1_h",
            CONF_TARGET_DEVICE_DEHUMIDITY: "switch.same",
            CONF_TARGET_DEVICE_HUMIDITY: "switch.same",
        },
        version=3,
    )
    opt = SmartPIDThermostatOptionsFlow()
    opt.hass = hass
    opt._config_entry_internal = hum_entry
    res_opt = await opt.async_step_init(
        {
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
            CONF_INDOOR_SENSOR: "sensor.c1_h",
            CONF_TARGET_DEVICE_DEHUMIDITY: "switch.same",
            CONF_TARGET_DEVICE_HUMIDITY: "switch.same",
        }
    )
    assert res_opt["type"] == "form"
    assert res_opt["errors"].get("base") == "duplicate_dual_target_device"

    with pytest.raises(ConfigEntryError):
        await async_setup_entry(hass, hum_entry)


# ---------------------------------------------------------------------------
# C02: async_setup_entry rollback on platform forward failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c02_setup_entry_rollback_on_forward_failure(hass, monkeypatch) -> None:
    """Failing async_forward_entry_setups rolls back runtime_data, hass.data, and calls async_unload."""
    unloaded = False
    orig_unload = SmartPIDRuntime.async_unload

    async def _spy_unload(self) -> None:
        nonlocal unloaded
        unloaded = True
        await orig_unload(self)

    monkeypatch.setattr(SmartPIDRuntime, "async_unload", _spy_unload)

    async def _boom(entry, platforms) -> None:
        raise RuntimeError("forward failed")

    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", _boom)

    entry = ConfigEntry(
        entry_id="c02_fail",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.c2_t",
            CONF_TARGET_DEVICE: "switch.c2_sw",
        },
        version=3,
    )
    with pytest.raises(RuntimeError, match="forward failed"):
        await async_setup_entry(hass, entry)

    assert entry.runtime_data is None
    assert "c02_fail" not in hass.data.get(DOMAIN, {})
    assert unloaded is True
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# C03: OptionsFlow clearing, nested section sync, section_0 & PWM stripping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c03_options_flow_clear_nested_sync_sec0_and_pwm_strip(hass) -> None:
    """OptionsFlow clears optional fields, syncs nested dicts, saves section_0, strips PWM modes."""
    entry = ConfigEntry(
        entry_id="c03_opt",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.c3_h",
            CONF_TARGET_DEVICE: "switch.c3_sw",
            CONF_OUTDOOR_SENSOR: "sensor.old_out",
            CONF_VENT_SWITCH: "input_boolean.old_vent",
            CONF_VENT_SENSORS: ["binary_sensor.old_win"],
            CONF_AUX_TEMP_SENSOR: "sensor.old_aux",
            CONF_COUPLED_AC: "climate.old_ac",
        },
        options={
            CONF_AVAILABLE_HVAC_MODES: ["cool", "off"],
            CONF_AVAILABLE_FAN_MODES: ["low"],
            "ventilation": {
                CONF_VENT_SWITCH: "input_boolean.old_vent",
                CONF_VENT_SENSORS: ["binary_sensor.old_win"],
            },
            "adaptive_learning": {
                CONF_AUX_TEMP_SENSOR: "sensor.old_aux",
                CONF_COUPLED_AC: "climate.old_ac",
            },
        },
        version=3,
    )
    opt = SmartPIDThermostatOptionsFlow()
    opt.hass = hass
    opt._config_entry_internal = entry

    step2 = await opt.async_step_init(
        {
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDIFIER,
            CONF_INDOOR_SENSOR: "sensor.c3_h",
            CONF_TARGET_DEVICE: "switch.c3_sw",
        }
    )
    assert step2["type"] == "form"
    assert step2["step_id"] == "sections_pwm"

    created = await opt.async_step_sections_pwm({})
    assert created["type"] == "create_entry"
    opts = created["data"]
    assert opts[CONF_OUTDOOR_SENSOR] == ""
    assert opts[CONF_VENT_SWITCH] == ""
    assert opts[CONF_VENT_SENSORS] == []
    assert opts[CONF_AUX_TEMP_SENSOR] == ""
    assert opts[CONF_COUPLED_AC] == ""
    assert opts["ventilation"][CONF_VENT_SWITCH] == ""
    assert opts["ventilation"][CONF_VENT_SENSORS] == []
    assert opts["adaptive_learning"][CONF_AUX_TEMP_SENSOR] == ""
    assert opts["adaptive_learning"][CONF_COUPLED_AC] == ""
    assert CONF_AVAILABLE_HVAC_MODES not in opts
    assert CONF_AVAILABLE_FAN_MODES not in opts

    # Climate-delta OptionsFlow saves both top-level and nested section_0
    clim_entry = ConfigEntry(
        entry_id="c03_clim",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_INDOOR_SENSOR: "sensor.c3_t",
            CONF_TARGET_CLIMATE: "climate.c3_ac",
        },
        version=3,
    )
    opt_c = SmartPIDThermostatOptionsFlow()
    opt_c.hass = hass
    opt_c._config_entry_internal = clim_entry
    step2_c = await opt_c.async_step_init(
        {
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.c3_t",
            CONF_TARGET_DEVICE: "climate.c3_ac",
        }
    )
    assert step2_c["step_id"] == "sections_climate"
    created_c = await opt_c.async_step_sections_climate(
        {
            "section_0": {
                CONF_SEC0_ACTION: SEC0_ACTION_SETBACK,
                CONF_SEC0_THRESHOLD: 6.0,
                CONF_SEC0_DELTA: 3.0,
            }
        }
    )
    assert created_c["type"] == "create_entry"
    opts_c = created_c["data"]
    assert opts_c[CONF_SEC0_ACTION] == SEC0_ACTION_SETBACK
    assert opts_c[CONF_SEC0_THRESHOLD] == pytest.approx(6.0)
    assert opts_c[CONF_SEC0_DELTA] == pytest.approx(3.0)
    assert opts_c["section_0"][CONF_SEC0_DELTA] == pytest.approx(3.0)

    # extract_target_devices honors explicitly cleared target options
    assert extract_target_devices(
        {CONF_TARGET_DEVICE: "switch.old"},
        {CONF_TARGET_DEVICE: ""},
    ) == set()


# ---------------------------------------------------------------------------
# C04: Service schema strict bool & single entity validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c04_bootstrap_service_strict_bool_and_single_entity(hass) -> None:
    """bootstrap_from_influxdb rejects non-bool dry_run/reset_existing and multi/empty entity_id."""
    assert await async_setup(hass, {}) is True

    for bad_payload in (
        {"dry_run": None},
        {"dry_run": "false"},
        {"reset_existing": "true"},
        {"entity_id": ["climate.a", "climate.b"]},
        {"entity_id": ""},
    ):
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN, SERVICE_BOOTSTRAP_INFLUXDB, bad_payload, return_response=True
            )

    entry = ConfigEntry(
        entry_id="c04_bs",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.c4_t",
            CONF_TARGET_DEVICE: "switch.c4_sw",
        },
        version=3,
    )
    rt = SmartPIDRuntime(hass, entry)
    await rt.async_setup()
    try:
        with pytest.raises(ServiceValidationError):
            await async_bootstrap_from_influxdb(hass, rt, {"dry_run": "false"})
        with pytest.raises(ServiceValidationError):
            await async_bootstrap_from_influxdb(hass, rt, {"reset_existing": "true"})
    finally:
        await rt.async_unload()
        await hass.async_block_till_done()
