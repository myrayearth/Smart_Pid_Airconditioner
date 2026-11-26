"""Regression tests covering all 14 GPT 6.2 Astra review defects + VirtualClock harness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
import re
from typing import Any

import pytest

from custom_components.smart_pid_airconditioner import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.actuator import SwitchCommandGuard
from custom_components.smart_pid_airconditioner.climate import (
    SmartPIDThermostatEntity,
    async_setup_entry as async_setup_climate,
)
from custom_components.smart_pid_airconditioner.config_utils import (
    parse_humidity_state,
    parse_temperature_state,
)
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_DYNAMIC_MIN_OFF_MULTIPLIER,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KD,
    CONF_KE,
    CONF_KEEP_ALIVE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_OUTDOOR_SENSOR,
    CONF_OUTPUT_SAFETY,
    CONF_PWM_DURATION,
    CONF_SAMPLE_PERIOD,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_HUMIDITY,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONTROL_MODE_SWITCH_PWM,
    DEFAULT_SENSOR_STALE_TIMEOUT_SEC,
    DOMAIN,
    MODE_AUTO,
    MODE_DEHUMIDITY,
    MODE_HUMIDITY,
    MODE_OFF,
    SERVICE_BOOTSTRAP_INFLUXDB,
    SERVICE_RESET_LEARNING,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    TUNING_MODE_AUTO,
)
from custom_components.smart_pid_airconditioner.flow_schemas import process_pwm_sections_input
from custom_components.smart_pid_airconditioner.humidifier import (
    SmartPIDHumidifierEntity,
    async_setup_entry as async_setup_humidifier,
)
from custom_components.smart_pid_airconditioner.learner import HuberRLS
from custom_components.smart_pid_airconditioner.pid_core import (
    compute_pid_step,
    evaluate_sensor_recovery,
)
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.storage import (
    LearningStateSnapshot,
    ModelStore,
)
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform, UnitOfTemperature
from homeassistant.core import State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
import homeassistant.util.dt as dt_util


def _patch_fast_guard(monkeypatch) -> None:
    """Use short deterministic timeouts in SwitchCommandGuard for tests."""
    orig_init = SwitchCommandGuard.__init__

    def _fast_init(
        self,
        hass,
        *,
        confirm_timeout: float = 0.015,
        off_retry_delays: tuple[float, ...] = (0.005, 0.005),
    ) -> None:
        orig_init(
            self,
            hass,
            confirm_timeout=confirm_timeout,
            off_retry_delays=off_retry_delays,
        )

    monkeypatch.setattr(SwitchCommandGuard, "__init__", _fast_init)


class _SwitchSimulator:
    """Simulated switch/homeassistant service backend recording exact calls and failures."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self.calls: list[tuple[str, str, str, datetime]] = []
        self.fail_off: set[str] = set()
        self.raise_off: set[str] = set()
        self.missing_entities: set[str] = set()
        self.flaky_off_attempts: dict[str, int] = {}
        self.flaky_off_succeed_on: dict[str, int] = {}

        def _make_on(dom: str):
            async def _handle_on(call) -> None:
                eid = call.data["entity_id"]
                self.calls.append((dom, "turn_on", eid, dt_util.utcnow()))
                self.hass.states.async_set(eid, "on")

            return _handle_on

        def _make_off(dom: str):
            async def _handle_off(call) -> None:
                eid = call.data["entity_id"]
                self.calls.append((dom, "turn_off", eid, dt_util.utcnow()))
                if eid in self.raise_off:
                    raise RuntimeError(f"simulated turn_off failure for {eid}")
                if eid in self.fail_off or eid in self.missing_entities:
                    return
                if eid in self.flaky_off_succeed_on:
                    count = self.flaky_off_attempts.get(eid, 0) + 1
                    self.flaky_off_attempts[eid] = count
                    if count < self.flaky_off_succeed_on[eid]:
                        return
                self.hass.states.async_set(eid, "off")

            return _handle_off

        for dom in ("switch", "homeassistant"):
            hass.services.handlers[(dom, "turn_on")] = _make_on(dom)
            hass.services.handlers[(dom, "turn_off")] = _make_off(dom)

    def on_calls(self, entity_id: str | None = None) -> list[tuple[str, str, str, datetime]]:
        return [
            c
            for c in self.calls
            if c[1] == "turn_on" and (entity_id is None or c[2] == entity_id)
        ]

    def off_calls(self, entity_id: str | None = None) -> list[tuple[str, str, str, datetime]]:
        return [
            c
            for c in self.calls
            if c[1] == "turn_off" and (entity_id is None or c[2] == entity_id)
        ]


async def _setup_climate_entity(
    hass,
    *,
    entry_id: str,
    data: dict[str, Any],
    entity_id: str | None = None,
) -> tuple[ConfigEntry, SmartPIDThermostatEntity]:
    """Setup entry and mount SmartPIDThermostatEntity into hass."""
    entry = ConfigEntry(entry_id=entry_id, title=entry_id, data=data, version=2)
    if isinstance(hass.config_entries.entries, dict):
        hass.config_entries.entries[entry_id] = entry
    assert await async_setup_entry(hass, entry) is True
    ents: list[SmartPIDThermostatEntity] = []
    await async_setup_climate(hass, entry, lambda items, _=False: ents.extend(items))
    assert len(ents) == 1
    ent = ents[0]
    ent.hass = hass
    ent.entity_id = entity_id or f"climate.{entry_id}"
    er.async_get(hass).async_register(
        ent.entity_id,
        entry_id,
        platform=Platform.CLIMATE,
        unique_id=f"{entry_id}_climate",
    )
    await ent.async_added_to_hass()
    return entry, ent


async def _setup_humidifier_entity(
    hass,
    *,
    entry_id: str,
    data: dict[str, Any],
    entity_id: str | None = None,
) -> tuple[ConfigEntry, SmartPIDHumidifierEntity]:
    """Setup entry and mount SmartPIDHumidifierEntity into hass."""
    entry = ConfigEntry(entry_id=entry_id, title=entry_id, data=data, version=2)
    if isinstance(hass.config_entries.entries, dict):
        hass.config_entries.entries[entry_id] = entry
    assert await async_setup_entry(hass, entry) is True
    ents: list[SmartPIDHumidifierEntity] = []
    await async_setup_humidifier(hass, entry, lambda items, _=False: ents.extend(items))
    assert len(ents) == 1
    ent = ents[0]
    ent.hass = hass
    ent.entity_id = entity_id or f"humidifier.{entry_id}"
    er.async_get(hass).async_register(
        ent.entity_id,
        entry_id,
        platform=Platform.HUMIDIFIER,
        unique_id=f"{entry_id}_humidifier",
    )
    await ent.async_added_to_hass()
    return entry, ent


def _report_sensor_state(
    hass, entity_id: str, value: str, *, attrs: dict[str, Any] | None = None
) -> None:
    """Emit a sensor reading with explicit current last_reported even if numeric value repeats."""
    now = dt_util.utcnow()
    old_state = hass.states.get(entity_id)
    merged_attrs = (
        dict(attrs)
        if attrs is not None
        else (dict(old_state.attributes) if old_state else {})
    )
    new_state = State(
        entity_id,
        value,
        attributes=merged_attrs,
        last_changed=old_state.last_changed if (old_state and old_state.state == value) else now,
        last_updated=now,
        last_reported=now,
    )
    hass.states._states[entity_id] = new_state
    if hasattr(hass, "bus") and hass.bus is not None:
        hass.bus.async_fire(
            "state_reported",
            {
                "entity_id": entity_id,
                "new_state": new_state,
                "last_reported": now,
                "old_last_reported": old_state.last_reported if old_state else None,
            },
        )


# ---------------------------------------------------------------------------
# Low: VirtualClock harness verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_virtual_clock_executes_due_timers_skips_cancelled_and_dispatches_state_events(
    hass, virtual_clock
) -> None:
    """VirtualClock runs due call_later/point_in_time/interval timers, skips cancelled, runs state events."""
    events: list[str] = []

    async def _later_cb(_now) -> None:
        events.append("later")

    async def _cancelled_cb(_now) -> None:
        events.append("cancelled")

    async def _point_cb(_now) -> None:
        events.append("point")

    async def _interval_cb(_now) -> None:
        events.append("interval")

    async def _state_cb(event) -> None:
        events.append(f"state:{event.data['entity_id']}:{event.data['new_state'].state}")

    unsub_later = async_call_later(hass, 10.0, _later_cb)
    unsub_cancel = async_call_later(hass, 5.0, _cancelled_cb)
    unsub_cancel()
    unsub_point = async_track_point_in_time(
        hass, _point_cb, dt_util.utcnow() + timedelta(seconds=15)
    )
    unsub_interval = async_track_time_interval(hass, _interval_cb, timedelta(seconds=20))
    unsub_state = async_track_state_change_event(hass, ["sensor.clock_test"], _state_cb)

    hass.states.async_set("sensor.clock_test", "42")
    await virtual_clock.advance(25.0)
    await virtual_clock.advance(20.0)

    assert "cancelled" not in events
    assert events.count("later") == 1
    assert events.count("point") == 1
    assert events.count("interval") == 2
    assert "state:sensor.clock_test:42" in events

    unsub_later()
    unsub_point()
    unsub_interval()
    unsub_state()


# ---------------------------------------------------------------------------
# Defect #1 (Critical): Dual-relay interlock before ON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_01_climate_heat_cool_interlock_blocks_on_for_stuck_unavail_and_missing(
    hass, virtual_clock, monkeypatch
) -> None:
    """Climate HEAT_COOL must never turn ON heat relay if cool relay cannot be confirmed OFF."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    hass.states.async_set("sensor.indoor", "18.0")
    hass.states.async_set("switch.heat_relay", "off")
    hass.states.async_set("switch.cool_relay", "on")
    sim.fail_off.add("switch.cool_relay")

    entry, ent = await _setup_climate_entity(
        hass,
        entry_id="d1_clim_interlock",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.indoor",
            CONF_TARGET_DEVICE_HEAT: "switch.heat_relay",
            CONF_TARGET_DEVICE_COOL: "switch.cool_relay",
            CONF_INITIAL_TARGET_TEMP: 23.0,
            CONF_KP: 30.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
        },
    )

    try:
        # 1) Opposite relay stuck "on" despite turn_off retries
        await ent.async_set_hvac_mode(HVACMode.HEAT)
        assert len(sim.off_calls("switch.cool_relay")) >= 1
        assert len(sim.on_calls("switch.heat_relay")) == 0
        assert ent._pwm_state is False

        # 2) Opposite relay is "unavailable"
        sim.calls.clear()
        hass.states.async_set("switch.cool_relay", "unavailable")
        await ent._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.heat_relay")) == 0
        assert ent._pwm_state is False

        # 3) Opposite relay entity missing completely (state None)
        sim.calls.clear()
        sim.fail_off.discard("switch.cool_relay")
        sim.missing_entities.add("switch.cool_relay")
        hass.states._states.pop("switch.cool_relay", None)
        await ent._async_calculate_and_control(force=True)
        assert len(sim.off_calls("switch.cool_relay")) >= 1
        assert len(sim.on_calls("switch.heat_relay")) == 0
        assert ent._pwm_state is False
    finally:
        await ent.async_will_remove_from_hass()
        await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_defect_01_humidifier_dual_interlock_blocks_on_for_stuck_unavail_and_missing(
    hass, virtual_clock, monkeypatch
) -> None:
    """Humidifier HUMIDITY_DEHUMIDITY must never turn ON active relay if opposite relay is unconfirmed OFF."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    hass.states.async_set("sensor.room_hum", "30.0")
    hass.states.async_set("switch.hum_relay", "off")
    hass.states.async_set("switch.dehum_relay", "on")
    sim.fail_off.add("switch.dehum_relay")

    entry, ent = await _setup_humidifier_entity(
        hass,
        entry_id="d1_hum_interlock",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.room_hum",
            CONF_TARGET_DEVICE_HUMIDITY: "switch.hum_relay",
            CONF_TARGET_DEVICE_DEHUMIDITY: "switch.dehum_relay",
            CONF_KP: 30.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
        },
    )

    try:
        await ent.async_set_humidity(55)
        await ent.async_set_mode(MODE_AUTO)
        await ent.async_turn_on()
        sim.calls.clear()

        # 1) Opposite dehumidifier stuck "on"
        await ent._async_calculate_and_control(force=True)
        assert len(sim.off_calls("switch.dehum_relay")) >= 1
        assert len(sim.on_calls("switch.hum_relay")) == 0
        assert ent._pwm_state is False

        # 2) Opposite dehumidifier "unavailable"
        sim.calls.clear()
        hass.states.async_set("switch.dehum_relay", "unavailable")
        await ent._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.hum_relay")) == 0
        assert ent._pwm_state is False

        # 3) Opposite dehumidifier missing (None)
        sim.calls.clear()
        sim.fail_off.discard("switch.dehum_relay")
        sim.missing_entities.add("switch.dehum_relay")
        hass.states._states.pop("switch.dehum_relay", None)
        await ent._async_calculate_and_control(force=True)
        assert len(sim.off_calls("switch.dehum_relay")) >= 1
        assert len(sim.on_calls("switch.hum_relay")) == 0
        assert ent._pwm_state is False

        # 4) Reverse direction: MODE_DEHUMIDITY when hum_relay is stuck "on"
        sim.missing_entities.discard("switch.dehum_relay")
        hass.states.async_set("switch.dehum_relay", "off")
        hass.states.async_set("switch.hum_relay", "on")
        sim.fail_off.add("switch.hum_relay")
        _report_sensor_state(hass, "sensor.room_hum", "80.0")
        sim.calls.clear()
        await ent.async_set_mode(MODE_DEHUMIDITY)
        await ent._async_calculate_and_control(force=True)
        assert len(sim.off_calls("switch.hum_relay")) >= 1
        assert len(sim.on_calls("switch.dehum_relay")) == 0
    finally:
        await ent.async_will_remove_from_hass()
        await async_unload_entry(hass, entry)


# ---------------------------------------------------------------------------
# Defect #2 (High): Non-finite sensor values (NaN / Inf / -Inf)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_02_nonfinite_sensor_values_rejected_and_recover_cleanly(
    hass, virtual_clock, monkeypatch
) -> None:
    """NaN/Inf/-Inf readings must be rejected cleanly, never output 100%, and recover to finite state."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    # Unit checks on parsers & compute_pid_step
    for bad in ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"):
        assert parse_temperature_state(State("sensor.t", bad)) is None
        assert parse_humidity_state(State("sensor.h", bad)) is None

    step = compute_pid_step(
        indoor_val=float("nan"),
        target_val=22.0,
        outdoor_val=float("nan"),
        has_outdoor_sensor=True,
        is_heating=True,
        kp=20.0,
        ki=1.0,
        kd=5.0,
        ke=0.5,
        max_i=50.0,
        dt=60.0,
        sample_period=60,
        now=dt_util.utcnow(),
        force=True,
        integral=float("nan"),
        filtered_d=float("inf"),
        last_d_calc_time=None,
        prev_val=float("nan"),
        freeze_integral=False,
    )
    assert step.final_output == 0.0
    assert all(
        math.isfinite(v)
        for v in (
            step.error,
            step.p_term,
            step.i_term,
            step.d_term,
            step.e_term,
            step.raw_output,
            step.final_output,
            step.integral,
            step.filtered_d,
        )
    )

    # Climate heater with output_safety = 0
    hass.states.async_set("sensor.indoor_nan", "20.0")
    hass.states.async_set("sensor.outdoor_nan", "5.0")
    hass.states.async_set("switch.heat_nan", "off")
    entry_c, ent_c = await _setup_climate_entity(
        hass,
        entry_id="d2_clim_nan",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.indoor_nan",
            CONF_OUTDOOR_SENSOR: "sensor.outdoor_nan",
            CONF_TARGET_DEVICE: "switch.heat_nan",
            CONF_INITIAL_TARGET_TEMP: 22.0,
            CONF_OUTPUT_SAFETY: 0,
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KD: 1.0,
            CONF_KE: 0.5,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
        },
    )
    # Humidifier dehumidifier with output_safety = 0
    hass.states.async_set("sensor.hum_nan", "50.0")
    hass.states.async_set("switch.dehum_nan", "off")
    entry_h, ent_h = await _setup_humidifier_entity(
        hass,
        entry_id="d2_hum_nan",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.hum_nan",
            CONF_TARGET_DEVICE: "switch.dehum_nan",
            CONF_OUTPUT_SAFETY: 0,
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KD: 1.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
        },
    )

    try:
        # Activate HEAT / DEHUMIDITY modes while ensuring switches are initially off
        await ent_c.async_set_hvac_mode(HVACMode.HEAT)
        await ent_c._async_turn_off_switch(force=True)
        await ent_h.async_set_humidity(45)
        await ent_h.async_turn_on()
        await ent_h._async_turn_off_switch("switch.dehum_nan")
        await virtual_clock.advance(70.0)
        sim.calls.clear()

        # Inject NaN, Inf, -Inf into outdoor sensor first while indoor is valid 20.0
        for bad in ("nan", "inf", "-inf"):
            _report_sensor_state(hass, "sensor.outdoor_nan", bad)
            await ent_c._async_calculate_and_control(force=True)
            assert math.isfinite(ent_c._pid_output)
            assert ent_c._outdoor_temp is None

        # Restore outdoor, then stop switches cleanly and inject NaN/Inf/-Inf into indoor sensors
        _report_sensor_state(hass, "sensor.outdoor_nan", "5.0")
        await ent_c._async_turn_off_switch(force=True)
        await virtual_clock.advance(70.0)
        sim.calls.clear()

        for bad in ("nan", "inf", "-inf"):
            _report_sensor_state(hass, "sensor.indoor_nan", bad)
            await ent_c._async_calculate_and_control(force=True)
            assert ent_c._fail_safe_active is True
            assert ent_c._sensor_fault_active is True
            assert ent_c._pid_output == 0.0
            assert ent_c._overheat_latched is False
            assert len(sim.on_calls("switch.heat_nan")) == 0

            _report_sensor_state(hass, "sensor.hum_nan", bad)
            await ent_h._async_calculate_and_control(force=True)
            assert ent_h._fail_safe_active is True
            assert ent_h._sensor_fault_active is True
            assert ent_h._pid_output == 0.0
            assert len(sim.on_calls("switch.dehum_nan")) == 0

        # Recover with two distinct valid finite readings >= 30s apart
        _report_sensor_state(hass, "sensor.indoor_nan", "19.0")
        _report_sensor_state(hass, "sensor.hum_nan", "60.0")
        await ent_c._async_calculate_and_control(force=True)
        await ent_h._async_calculate_and_control(force=True)
        assert ent_c._sensor_fault_active is True
        assert ent_h._sensor_fault_active is True
        assert len(sim.on_calls("switch.heat_nan")) == 0
        assert len(sim.on_calls("switch.dehum_nan")) == 0

        await virtual_clock.advance(35.0)
        _report_sensor_state(hass, "sensor.indoor_nan", "19.0")
        _report_sensor_state(hass, "sensor.hum_nan", "60.0")
        await ent_c._async_calculate_and_control(force=True)
        await ent_h._async_calculate_and_control(force=True)

        assert ent_c._fail_safe_active is False
        assert ent_c._sensor_fault_active is False
        assert ent_h._fail_safe_active is False
        assert ent_h._sensor_fault_active is False
        assert all(
            math.isfinite(v)
            for v in (
                ent_c._pid_output,
                ent_c._pid_error or 0.0,
                ent_c._integral,
                ent_c._filtered_d,
                ent_c._pid_i,
                ent_c._pid_d,
            )
        )
        assert all(
            math.isfinite(v)
            for v in (
                ent_h._pid_output,
                ent_h._pid_error or 0.0,
                ent_h._integral,
                ent_h._filtered_d,
                ent_h._pid_i,
                ent_h._pid_d,
            )
        )
    finally:
        await ent_c.async_will_remove_from_hass()
        await ent_h.async_will_remove_from_hass()
        await async_unload_entry(hass, entry_c)
        await async_unload_entry(hass, entry_h)


# ---------------------------------------------------------------------------
# Defect #3 (High): EVENT_HOMEASSISTANT_STOP shuts down PWM on both platforms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_03_ha_stop_event_turns_off_pwm_and_blocks_future_on_both_platforms(
    hass, virtual_clock, monkeypatch
) -> None:
    """EVENT_HOMEASSISTANT_STOP must turn off active PWM switches and block future ON without removal."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    hass.states.async_set("sensor.stop_temp", "18.0")
    hass.states.async_set("switch.stop_heat", "off")
    hass.states.async_set("sensor.stop_hum", "75.0")
    hass.states.async_set("switch.stop_dehum", "off")

    entry_c, ent_c = await _setup_climate_entity(
        hass,
        entry_id="d3_stop_clim",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.stop_temp",
            CONF_TARGET_DEVICE: "switch.stop_heat",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_KP: 30.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 600,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
            CONF_KEEP_ALIVE: 30,
        },
    )
    entry_h, ent_h = await _setup_humidifier_entity(
        hass,
        entry_id="d3_stop_hum",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.stop_hum",
            CONF_TARGET_DEVICE: "switch.stop_dehum",
            CONF_KP: 30.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 600,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
            CONF_KEEP_ALIVE: 30,
        },
    )

    try:
        await ent_c.async_set_hvac_mode(HVACMode.HEAT)
        await ent_h.async_set_humidity(45)
        await ent_h.async_turn_on()
        assert ent_c._pwm_state is True
        assert ent_h._pwm_state is True
        assert hass.states.get("switch.stop_heat").state == "on"
        assert hass.states.get("switch.stop_dehum").state == "on"

        sim.calls.clear()
        # Fire EVENT_HOMEASSISTANT_STOP directly WITHOUT calling async_will_remove_from_hass
        await hass.bus.async_fire_and_wait(EVENT_HOMEASSISTANT_STOP)
        await virtual_clock.flush()

        assert ent_c._shutting_down is True
        assert ent_h._shutting_down is True
        assert ent_c._pwm_state is False
        assert ent_h._pwm_state is False
        assert len(sim.off_calls("switch.stop_heat")) >= 1
        assert len(sim.off_calls("switch.stop_dehum")) >= 1
        assert hass.states.get("switch.stop_heat").state == "off"
        assert hass.states.get("switch.stop_dehum").state == "off"

        # Advance virtual clock and send new sensor demand -> zero future ON calls
        sim.calls.clear()
        _report_sensor_state(hass, "sensor.stop_temp", "15.0")
        _report_sensor_state(hass, "sensor.stop_hum", "85.0")
        await ent_c._async_calculate_and_control(force=True)
        await ent_h._async_calculate_and_control(force=True)
        await virtual_clock.advance(120.0)

        assert len(sim.on_calls("switch.stop_heat")) == 0
        assert len(sim.on_calls("switch.stop_dehum")) == 0
    finally:
        await ent_c.async_will_remove_from_hass()
        await ent_h.async_will_remove_from_hass()
        await async_unload_entry(hass, entry_c)
        await async_unload_entry(hass, entry_h)


# ---------------------------------------------------------------------------
# Defect #4 (High): Stale report timestamp & 2 distinct reports for recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_04_stale_sensor_timeout_heartbeat_and_two_distinct_reports_recovery(
    hass, virtual_clock, monkeypatch
) -> None:
    """Detect frozen last_reported even when value remains numeric; allow live heartbeat; require 2 distinct reports."""
    _patch_fast_guard(monkeypatch)
    _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    # Unit test on evaluate_sensor_recovery requiring distinct report timestamps
    t0 = dt_util.utcnow()
    accept, fault, rec_first = evaluate_sensor_recovery(
        parsed=19.0,
        fault_active=True,
        recovery_first_at=None,
        now=t0,
        report_at=t0,
    )
    assert accept is False and fault is True and rec_first == t0
    # Repeated call 40s later with SAME report_at must NOT accept recovery
    accept, fault, rec_first = evaluate_sensor_recovery(
        parsed=19.0,
        fault_active=True,
        recovery_first_at=rec_first,
        now=t0 + timedelta(seconds=40),
        report_at=t0,
    )
    assert accept is False and fault is True and rec_first == t0
    # Distinct second report >= 30s later recovers cleanly
    t1 = t0 + timedelta(seconds=35)
    accept, fault, rec_first = evaluate_sensor_recovery(
        parsed=19.0,
        fault_active=True,
        recovery_first_at=rec_first,
        now=t1,
        report_at=t1,
    )
    assert accept is True and fault is False and rec_first is None

    hass.states.async_set("sensor.stale_temp", "18.0")
    hass.states.async_set("switch.stale_heat", "off")
    hass.states.async_set("sensor.stale_hum", "72.0")
    hass.states.async_set("switch.stale_dehum", "off")

    entry_c, ent_c = await _setup_climate_entity(
        hass,
        entry_id="d4_stale_clim",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.stale_temp",
            CONF_TARGET_DEVICE: "switch.stale_heat",
            CONF_INITIAL_TARGET_TEMP: 22.0,
            CONF_OUTPUT_SAFETY: 0,
            CONF_SAMPLE_PERIOD: 60,
            CONF_PWM_DURATION: 600,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
        },
    )
    entry_h, ent_h = await _setup_humidifier_entity(
        hass,
        entry_id="d4_stale_hum",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.stale_hum",
            CONF_TARGET_DEVICE: "switch.stale_dehum",
            CONF_OUTPUT_SAFETY: 0,
            CONF_SAMPLE_PERIOD: 60,
            CONF_PWM_DURATION: 600,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
        },
    )

    try:
        await ent_c.async_set_hvac_mode(HVACMode.HEAT)
        await ent_h.async_set_humidity(50)
        await ent_h.async_turn_on()
        assert ent_c._fail_safe_active is False
        assert ent_h._fail_safe_active is False

        # Case 2: Identical-value live heartbeat keeps controllers healthy past stale timeout
        for _ in range(3):
            await virtual_clock.advance(700.0)
            _report_sensor_state(hass, "sensor.stale_temp", "18.0")
            _report_sensor_state(hass, "sensor.stale_hum", "72.0")
            await virtual_clock.flush()
            assert ent_c._fail_safe_active is False
            assert ent_h._fail_safe_active is False

        # Case 1: Complete silence -> last_reported frozen while value stays "18.0"/"72.0"
        await virtual_clock.advance(DEFAULT_SENSOR_STALE_TIMEOUT_SEC + 120.0)
        assert ent_c._fail_safe_active is True
        assert ent_c._sensor_fault_active is True
        assert ent_h._fail_safe_active is True
        assert ent_h._sensor_fault_active is True

        # Case 3: Single report after fault must NOT recover even after >30s without a 2nd report
        _report_sensor_state(hass, "sensor.stale_temp", "18.5")
        _report_sensor_state(hass, "sensor.stale_hum", "70.0")
        await virtual_clock.flush()
        assert ent_c._sensor_fault_active is True
        assert ent_h._sensor_fault_active is True

        await virtual_clock.advance(70.0)
        assert ent_c._sensor_fault_active is True
        assert ent_c._fail_safe_active is True
        assert ent_h._sensor_fault_active is True
        assert ent_h._fail_safe_active is True

        # Second distinct report >= 30s after first -> recovers!
        _report_sensor_state(hass, "sensor.stale_temp", "18.5")
        _report_sensor_state(hass, "sensor.stale_hum", "70.0")
        await ent_c._async_calculate_and_control(force=True)
        await ent_h._async_calculate_and_control(force=True)
        assert ent_c._sensor_fault_active is False
        assert ent_c._fail_safe_active is False
        assert ent_h._sensor_fault_active is False
        assert ent_h._fail_safe_active is False
    finally:
        await ent_c.async_will_remove_from_hass()
        await ent_h.async_will_remove_from_hass()
        await async_unload_entry(hass, entry_c)
        await async_unload_entry(hass, entry_h)


# ---------------------------------------------------------------------------
# Defect #5 (High): Predictive ON blocked during sensor fail-safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_05_predictive_on_blocked_during_fail_safe_and_recovery(
    hass, virtual_clock, monkeypatch
) -> None:
    """Positive preheat_margin must NOT flip output_safety=0% into ON pulse when sensor fails or recovers."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    hass.states.async_set("sensor.pred_temp", "22.0")
    hass.states.async_set("switch.pred_heat", "off")

    entry, ent = await _setup_climate_entity(
        hass,
        entry_id="d5_pred_failsafe",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.pred_temp",
            CONF_TARGET_DEVICE: "switch.pred_heat",
            CONF_INITIAL_TARGET_TEMP: 22.0,
            CONF_OUTPUT_SAFETY: 0,
            CONF_TUNING_MODE: TUNING_MODE_AUTO,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
        },
    )
    rt: SmartPIDRuntime = entry.runtime_data
    monkeypatch.setattr(rt, "get_predictive_on_margin", lambda: 2.0)

    try:
        # While sensor is valid and warm (25.0°C > target 22.0°C -> err=-3.0, no preheat), switch starts OFF
        _report_sensor_state(hass, "sensor.pred_temp", "25.0")
        await ent.async_set_hvac_mode(HVACMode.HEAT)
        assert hass.states.get("switch.pred_heat").state == "off"
        sim.calls.clear()

        # Transition sensor to unavailable -> fail-safe active (output_safety = 0%, err = 0.0)
        _report_sensor_state(hass, "sensor.pred_temp", "unavailable")
        await ent._async_calculate_and_control(force=True)
        await virtual_clock.advance(320.0)
        assert len(sim.on_calls("switch.pred_heat")) == 0
        assert hass.states.get("switch.pred_heat").state == "off"

        # Single recovery report -> recovery pending -> still must NOT trigger predictive ON
        _report_sensor_state(hass, "sensor.pred_temp", "21.5")
        await ent._async_calculate_and_control(force=True)
        await virtual_clock.advance(320.0)
        assert len(sim.on_calls("switch.pred_heat")) == 0
        assert hass.states.get("switch.pred_heat").state == "off"
    finally:
        await ent.async_will_remove_from_hass()
        await async_unload_entry(hass, entry)


# ---------------------------------------------------------------------------
# Defect #6 (High): Dehumidifier default min_off_time=180s enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_06_dehumidifier_default_min_off_180s_and_off_timestamp_tracking(
    hass, virtual_clock, monkeypatch
) -> None:
    """Default min_off_time=180s must hold with multiplier=0.0 across PWM, manual OFF, and direction switches."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    # Case A: Partial PWM duty (50% of 600s = 300s ON / 300s OFF) with min_off=180 & multiplier=0.0
    hass.states.async_set("sensor.d6_hum", "75.0")
    hass.states.async_set("switch.d6_dehum", "off")
    entry, ent = await _setup_humidifier_entity(
        hass,
        entry_id="d6_min_off_single",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.d6_hum",
            CONF_TARGET_DEVICE: "switch.d6_dehum",
            CONF_KP: 2.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 600,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 180,
            CONF_DYNAMIC_MIN_OFF_MULTIPLIER: 0.0,
            CONF_SAMPLE_PERIOD: 600,
        },
    )

    # 90% duty sub-case: 900s period with min_off=180 (90s off < 180s -> promotes to full ON)
    hass.states.async_set("sensor.d6_90_hum", "95.0")
    hass.states.async_set("switch.d6_90_dehum", "off")
    entry_90, ent_90 = await _setup_humidifier_entity(
        hass,
        entry_id="d6_90_pct",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.d6_90_hum",
            CONF_TARGET_DEVICE: "switch.d6_90_dehum",
            CONF_KP: 2.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 900,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 180,
            CONF_DYNAMIC_MIN_OFF_MULTIPLIER: 0.0,
            CONF_SAMPLE_PERIOD: 600,
        },
    )

    # Dual humidity/dehumidity entity for direction-switch test
    hass.states.async_set("sensor.d6_dual_hum", "80.0")
    hass.states.async_set("switch.d6_hum_dev", "off")
    hass.states.async_set("switch.d6_dehum_dev", "off")
    entry_d, ent_d = await _setup_humidifier_entity(
        hass,
        entry_id="d6_min_off_dual",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.d6_dual_hum",
            CONF_TARGET_DEVICE_HUMIDITY: "switch.d6_hum_dev",
            CONF_TARGET_DEVICE_DEHUMIDITY: "switch.d6_dehum_dev",
            CONF_KP: 30.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 180,
            CONF_DYNAMIC_MIN_OFF_MULTIPLIER: 0.0,
            CONF_SAMPLE_PERIOD: 600,
        },
    )

    try:
        # 1) Normal PWM partial cycle: ON at t=0, OFF at t=300s -> blocked 90s later -> allowed after 180s
        await ent.async_set_humidity(50)  # err=25 * kp=2 = 50% -> 300s ON, 300s OFF
        await ent.async_turn_on()
        assert len(sim.on_calls("switch.d6_dehum")) == 1
        assert hass.states.get("switch.d6_dehum").state == "on"

        await virtual_clock.advance(310.0)  # OFF fired at 300s
        assert hass.states.get("switch.d6_dehum").state == "off"
        sim.calls.clear()

        # Advance +80s (now 90s after OFF < 180s min_off): forced cycle start must NOT turn ON
        await virtual_clock.advance(80.0)
        await ent._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.d6_dehum")) == 0

        # Advance +110s (total 200s >= 180s min_off): forced cycle start now turns ON
        await virtual_clock.advance(110.0)
        await ent._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.d6_dehum")) == 1

        # Keep live heartbeat fresh on all sensors after 500s
        _report_sensor_state(hass, "sensor.d6_hum", "75.0")
        _report_sensor_state(hass, "sensor.d6_90_hum", "95.0")
        _report_sensor_state(hass, "sensor.d6_dual_hum", "80.0")

        # 2) 90% duty (900s * 10% = 90s OFF < 180s min_off) stays Full ON rather than short-cycling 90s
        await ent_90.async_set_humidity(50)  # err=45 * kp=2 = 90%
        await ent_90.async_turn_on()
        assert hass.states.get("switch.d6_90_dehum").state == "on"
        assert ent_90._pwm_turn_off_cancel is None
        await virtual_clock.advance(850.0)
        assert hass.states.get("switch.d6_90_dehum").state == "on"
        await ent_90.async_turn_off()

        # Re-fresh heat/hum sensors
        _report_sensor_state(hass, "sensor.d6_hum", "75.0")
        _report_sensor_state(hass, "sensor.d6_dual_hum", "80.0")

        # 3) Case B: Manual OFF -> ON within 180s blocked, after 180s allowed
        await virtual_clock.advance(300.0)
        _report_sensor_state(hass, "sensor.d6_hum", "75.0")
        await ent._async_calculate_and_control(force=True)
        assert hass.states.get("switch.d6_dehum").state == "on"
        await ent.async_set_mode(MODE_OFF)
        assert hass.states.get("switch.d6_dehum").state == "off"
        sim.calls.clear()

        await virtual_clock.advance(100.0)
        await ent.async_set_mode(MODE_DEHUMIDITY)
        await ent._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.d6_dehum")) == 0

        await virtual_clock.advance(90.0)  # 190s >= 180s
        _report_sensor_state(hass, "sensor.d6_hum", "75.0")
        await ent._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.d6_dehum")) >= 1

        # 4) Case C: Dual-relay direction roundtrip (dehum ON -> humidity -> dehum < 180s)
        _report_sensor_state(hass, "sensor.d6_dual_hum", "80.0")
        await ent_d.async_set_humidity(50)
        await ent_d.async_set_mode(MODE_DEHUMIDITY)
        await ent_d.async_turn_on()
        assert hass.states.get("switch.d6_dehum_dev").state == "on"

        _report_sensor_state(hass, "sensor.d6_dual_hum", "30.0")
        await ent_d.async_set_mode(MODE_HUMIDITY)
        assert hass.states.get("switch.d6_dehum_dev").state == "off"
        assert "switch.d6_dehum_dev" in ent_d._last_switch_off_times

        sim.calls.clear()
        await virtual_clock.advance(90.0)  # < 180s
        _report_sensor_state(hass, "sensor.d6_dual_hum", "80.0")
        await ent_d.async_set_mode(MODE_DEHUMIDITY)
        await ent_d._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.d6_dehum_dev")) == 0

        await virtual_clock.advance(100.0)  # 190s >= 180s
        _report_sensor_state(hass, "sensor.d6_dual_hum", "80.0")
        await ent_d._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.d6_dehum_dev")) >= 1
    finally:
        await ent.async_will_remove_from_hass()
        await ent_90.async_will_remove_from_hass()
        await ent_d.async_will_remove_from_hass()
        await async_unload_entry(hass, entry)
        await async_unload_entry(hass, entry_90)
        await async_unload_entry(hass, entry_d)


# ---------------------------------------------------------------------------
# Defect #7 (High): °F / K normalization & overheat latch threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_07_fahrenheit_and_kelvin_normalization_prevents_false_overheat_latch(
    hass, virtual_clock, monkeypatch
) -> None:
    """68°F numeric + unit attr and '68°F' string must match 20°C identically without false 35°C latch."""
    _patch_fast_guard(monkeypatch)
    _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    assert parse_temperature_state(
        State("sensor.f1", "68", {"unit_of_measurement": UnitOfTemperature.FAHRENHEIT})
    ) == pytest.approx(20.0)
    assert parse_temperature_state(State("sensor.f2", "68°F")) == pytest.approx(20.0)
    assert parse_temperature_state(
        State("sensor.k1", "293.15", {"unit_of_measurement": "K"})
    ) == pytest.approx(20.0)
    assert parse_temperature_state(
        State("sensor.f3", "68"), default_unit=UnitOfTemperature.FAHRENHEIT
    ) == pytest.approx(20.0)

    hass.states.async_set("sensor.t_c", "20.0", {"unit_of_measurement": UnitOfTemperature.CELSIUS})
    hass.states.async_set(
        "sensor.t_f_attr", "68", {"unit_of_measurement": UnitOfTemperature.FAHRENHEIT}
    )
    hass.states.async_set("sensor.t_f_str", "68°F")
    hass.states.async_set(
        "sensor.out_f", "50", {"unit_of_measurement": UnitOfTemperature.FAHRENHEIT}
    )
    for sw in ("switch.c_c", "switch.c_fa", "switch.c_fs", "switch.h_fa"):
        hass.states.async_set(sw, "off")

    entities: list[tuple[ConfigEntry, SmartPIDThermostatEntity]] = []
    for idx, (sens, sw) in enumerate(
        (
            ("sensor.t_c", "switch.c_c"),
            ("sensor.t_f_attr", "switch.c_fa"),
            ("sensor.t_f_str", "switch.c_fs"),
        )
    ):
        entities.append(
            await _setup_climate_entity(
                hass,
                entry_id=f"d7_cool_{idx}",
                data={
                    CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
                    CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
                    CONF_INDOOR_SENSOR: sens,
                    CONF_OUTDOOR_SENSOR: "sensor.out_f",
                    CONF_TARGET_DEVICE: sw,
                    CONF_INITIAL_TARGET_TEMP: 24.0,
                    CONF_KP: 20.0,
                    CONF_KI: 0.0,
                    CONF_KD: 0.0,
                    CONF_KE: 0.5,
                },
            )
        )

    entry_h, ent_h = await _setup_climate_entity(
        hass,
        entry_id="d7_heat_f",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.t_f_attr",
            CONF_OUTDOOR_SENSOR: "sensor.out_f",
            CONF_TARGET_DEVICE: "switch.h_fa",
            CONF_INITIAL_TARGET_TEMP: 22.0,
            CONF_KP: 20.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
        },
    )

    try:
        for _, ent in entities:
            await ent.async_set_hvac_mode(HVACMode.COOL)
            assert ent._indoor_temp == pytest.approx(20.0)
            assert ent._outdoor_temp == pytest.approx(10.0)
            assert ent._pid_error == pytest.approx(-4.0)
            assert ent._overheat_latched is False
            assert ent._pwm_state is False

        # Heater at 68°F (20°C): normal demand (+2.0°C error), NO false 35°C overheat latch!
        await ent_h.async_set_hvac_mode(HVACMode.HEAT)
        assert ent_h._indoor_temp == pytest.approx(20.0)
        assert ent_h._pid_error == pytest.approx(2.0)
        assert ent_h._overheat_latched is False

        # Verify actual overheat >= 35°C (96.8°F = 36.0°C) DOES latch overheat in heating mode
        _report_sensor_state(
            hass,
            "sensor.t_f_attr",
            "96.8",
            attrs={"unit_of_measurement": UnitOfTemperature.FAHRENHEIT},
        )
        await ent_h._async_calculate_and_control(force=True)
        assert ent_h._indoor_temp == pytest.approx(36.0)
        assert ent_h._overheat_latched is True

        # Weather entity outdoor temperature in °F also normalizes to °C
        _, ent_c = entities[0]
        ent_c._outdoor_sensor_id = "weather.home"
        hass.states.async_set(
            "weather.home",
            "sunny",
            {"temperature": 50.0, "temperature_unit": UnitOfTemperature.FAHRENHEIT},
        )
        ent_c._update_sensor_values(now=dt_util.utcnow())
        assert ent_c._outdoor_temp == pytest.approx(10.0)
    finally:
        await ent_h.async_will_remove_from_hass()
        await async_unload_entry(hass, entry_h)
        for entry, ent in entities:
            await ent.async_will_remove_from_hass()
            await async_unload_entry(hass, entry)


# ---------------------------------------------------------------------------
# Defect #8 (High): Deferred ON cancelled & blocked when OFF window expires
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_08_deferred_on_cancelled_and_blocked_when_off_window_arrives(
    hass, virtual_clock, monkeypatch
) -> None:
    """Deferred ON must skip if min_off >= ON window, and cancel/block if OFF window arrives first."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    hass.states.async_set("sensor.d8_temp", "22.0")
    hass.states.async_set("switch.d8_heat", "off")

    entry, ent = await _setup_climate_entity(
        hass,
        entry_id="d8_deferred_cancel",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.d8_temp",
            CONF_TARGET_DEVICE: "switch.d8_heat",
            CONF_INITIAL_TARGET_TEMP: 22.0,
            CONF_KP: 20.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 180,
            CONF_SAMPLE_PERIOD: 600,
        },
    )

    try:
        await ent.async_set_hvac_mode(HVACMode.HEAT)

        # Sub-case (a): 120s remaining min_off > 60s ON window -> deferred ON must never fire!
        t_off = dt_util.utcnow()
        ent._last_switch_off_times["switch.d8_heat"] = t_off
        ent._last_switch_off_time = t_off
        await virtual_clock.advance(60.0)  # 120s min_off remaining

        sim.calls.clear()
        _report_sensor_state(hass, "sensor.d8_temp", "21.0")  # err=1.0 * kp=20 = 20% = 60s ON
        await ent._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.d8_heat")) == 0
        assert ent._pwm_turn_on_cancel is None  # proactively skipped!

        await virtual_clock.advance(70.0)
        assert ent._pwm_state is False
        await virtual_clock.advance(70.0)
        assert len(sim.on_calls("switch.d8_heat")) == 0
        assert hass.states.get("switch.d8_heat").state == "off"

        # Sub-case (b): 40s remaining min_off < 120s ON window -> deferred ON scheduled, then OFF cancels it
        t_off2 = dt_util.utcnow()
        ent._last_switch_off_times["switch.d8_heat"] = t_off2
        ent._last_switch_off_time = t_off2
        await virtual_clock.advance(140.0)  # 40s min_off remaining

        sim.calls.clear()
        _report_sensor_state(hass, "sensor.d8_temp", "20.0")  # err=2.0 * kp=20 = 40% = 120s ON
        await ent._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.d8_heat")) == 0
        assert ent._pwm_turn_on_cancel is not None

        # Before 40s elapses, turn OFF at +20s -> must cancel deferred ON so +45s sees 0 turn_on calls!
        await virtual_clock.advance(20.0)
        await ent._async_turn_off_switch(force=True)
        assert ent._pwm_turn_on_cancel is None
        await virtual_clock.advance(30.0)
        assert len(sim.on_calls("switch.d8_heat")) == 0
        assert hass.states.get("switch.d8_heat").state == "off"
    finally:
        await ent.async_will_remove_from_hass()
        await async_unload_entry(hass, entry)


# ---------------------------------------------------------------------------
# Defect #9 (High): Short PWM validation & preserving min_on/min_off at runtime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_09_short_pwm_validation_and_runtime_min_on_preservation(
    hass, virtual_clock, monkeypatch
) -> None:
    """Reject infeasible min_on + min_off >= pwm_duration in flow, and preserve physical min_on in runtime."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    # Schema validation checks (returns (errors, processed))
    err_exceed, _ = process_pwm_sections_input(
        {"pwm_duration": 300, "min_on_time": 180, "min_off_time": 180}
    )
    assert err_exceed.get("base") == "min_times_exceed_pwm_duration"

    err_boundary, _ = process_pwm_sections_input(
        {"pwm_duration": 300, "min_on_time": 120, "min_off_time": 180}
    )
    assert err_boundary.get("base") == "min_times_exceed_pwm_duration"

    err_ok, _ = process_pwm_sections_input(
        {"pwm_duration": 300, "min_on_time": 100, "min_off_time": 180}
    )
    assert err_ok == {}

    # Runtime preservation on pre-existing infeasible config (pwm=300, min_on=180, min_off=180)
    hass.states.async_set("sensor.d9_temp", "21.5")  # err=0.5 * kp=20 = 10% (30s < min_on=180s)
    hass.states.async_set("switch.d9_heat", "off")
    entry, ent = await _setup_climate_entity(
        hass,
        entry_id="d9_preserve_min_on",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.d9_temp",
            CONF_TARGET_DEVICE: "switch.d9_heat",
            CONF_INITIAL_TARGET_TEMP: 22.0,
            CONF_KP: 20.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_PWM_DURATION: 300,
            CONF_MIN_ON_TIME: 180,
            CONF_MIN_OFF_TIME: 180,
            CONF_SAMPLE_PERIOD: 600,
        },
    )

    try:
        # 1) 10% duty (30s < physical 180s min_on): must stay OFF (never short-pulse 30s!)
        await ent.async_set_hvac_mode(HVACMode.HEAT)
        assert len(sim.on_calls("switch.d9_heat")) == 0
        assert hass.states.get("switch.d9_heat").state == "off"

        # 2) Increase demand to >=60% (3.5°C * 20 = 70%) -> ON, and physical 180s min_on is preserved
        _report_sensor_state(hass, "sensor.d9_temp", "18.5")
        await ent._async_calculate_and_control(force=True)
        assert hass.states.get("switch.d9_heat").state == "on"

        await virtual_clock.advance(30.0)
        await ent._async_turn_off_switch(force=False)
        assert hass.states.get("switch.d9_heat").state == "on"  # NOT shrunk to 15s!

        # 3) Urgent force=True OFF (overheat >= 35°C) turns OFF immediately regardless of min_on
        _report_sensor_state(hass, "sensor.d9_temp", "36.0")
        await ent._async_calculate_and_control(force=True)
        assert ent._overheat_latched is True
        assert hass.states.get("switch.d9_heat").state == "off"
    finally:
        await ent.async_will_remove_from_hass()
        await async_unload_entry(hass, entry)


# ---------------------------------------------------------------------------
# Defect #10 (High): reset_learning strict target validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_10_reset_learning_invalid_targets_raise_and_reset_zero_entries(hass) -> None:
    """Explicit invalid/empty/mismatched reset_learning target must raise and reset 0 entries."""
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))
    hass.states.async_set("sensor.r_temp", "21.0")
    hass.states.async_set("switch.r_a", "off")
    hass.states.async_set("switch.r_b", "off")

    entry_a, ent_a = await _setup_climate_entity(
        hass,
        entry_id="d10_entry_a",
        entity_id="climate.room_a",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.r_temp",
            CONF_TARGET_DEVICE: "switch.r_a",
        },
    )
    entry_b, ent_b = await _setup_climate_entity(
        hass,
        entry_id="d10_entry_b",
        entity_id="climate.room_b",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.r_temp",
            CONF_TARGET_DEVICE: "switch.r_b",
        },
    )
    rt_a: SmartPIDRuntime = entry_a.runtime_data
    rt_b: SmartPIDRuntime = entry_b.runtime_data

    def _seed_samples() -> None:
        for _ in range(5):
            rt_a._active_rls().update([1.0, -2.0, 0.0], 0.05)
            rt_b._active_rls().update([1.0, -2.0, 0.0], 0.04)

    try:
        _seed_samples()
        assert rt_a._active_rls().sample_count == 5
        assert rt_b._active_rls().sample_count == 5

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, SERVICE_RESET_LEARNING, {"entity_id": "climate.deleted"}
            )
        assert rt_a._active_rls().sample_count == 5
        assert rt_b._active_rls().sample_count == 5

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, SERVICE_RESET_LEARNING, {"entity_id": ""})
        assert rt_a._active_rls().sample_count == 5
        assert rt_b._active_rls().sample_count == 5

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, SERVICE_RESET_LEARNING, {"entry_id": "missing_entry"}
            )
        assert rt_a._active_rls().sample_count == 5
        assert rt_b._active_rls().sample_count == 5

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RESET_LEARNING,
                {"entry_id": "d10_entry_a", "entity_id": "climate.room_b"},
            )
        assert rt_a._active_rls().sample_count == 5
        assert rt_b._active_rls().sample_count == 5

        await hass.services.async_call(
            DOMAIN, SERVICE_RESET_LEARNING, {"entity_id": "climate.room_a"}
        )
        assert rt_a._active_rls().sample_count == 0
        assert rt_b._active_rls().sample_count == 5

        _seed_samples()
        await hass.services.async_call(DOMAIN, SERVICE_RESET_LEARNING, {})
        assert rt_a._active_rls().sample_count == 0
        assert rt_b._active_rls().sample_count == 0
    finally:
        await ent_a.async_will_remove_from_hass()
        await ent_b.async_will_remove_from_hass()
        await async_unload_entry(hass, entry_a)
        await async_unload_entry(hass, entry_b)


# ---------------------------------------------------------------------------
# Defect #11 (High): Keep-alive routes through SwitchCommandGuard & interlock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defect_11_keep_alive_uses_guard_and_enforces_interlock_before_on(
    hass, virtual_clock, monkeypatch
) -> None:
    """Keep-alive must confirm opposite relay OFF through SwitchCommandGuard BEFORE sending active ON."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    hass.states.async_set("sensor.ka_temp", "18.0")
    hass.states.async_set("switch.ka_heat", "off")
    hass.states.async_set("switch.ka_cool", "off")

    entry, ent = await _setup_climate_entity(
        hass,
        entry_id="d11_keep_alive",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.ka_temp",
            CONF_TARGET_DEVICE_HEAT: "switch.ka_heat",
            CONF_TARGET_DEVICE_COOL: "switch.ka_cool",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_KP: 30.0,
            CONF_KI: 0.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_MIN_ON_TIME: 10,
            CONF_MIN_OFF_TIME: 10,
            CONF_KEEP_ALIVE: 60,
        },
    )

    try:
        await ent.async_set_hvac_mode(HVACMode.HEAT)
        assert ent._pwm_state is True

        # Case A: Opposite switch.ka_cool externally stuck "on" -> keep-alive tries OFF, never turns ON heat
        sim.fail_off.add("switch.ka_cool")
        hass.states._states["switch.ka_cool"] = State("switch.ka_cool", "on")
        hass.states._states["switch.ka_heat"] = State("switch.ka_heat", "off")
        sim.calls.clear()

        await ent._async_keep_alive_handler(dt_util.utcnow())
        assert len(sim.off_calls("switch.ka_cool")) >= 1
        assert len(sim.on_calls("switch.ka_heat")) == 0
        assert ent._pwm_state is False

        # Case B: Cool succeeds on 2nd OFF retry -> OFF must finish BEFORE ON!
        sim.fail_off.discard("switch.ka_cool")
        hass.states._states["switch.ka_cool"] = State("switch.ka_cool", "off")
        await virtual_clock.advance(20.0)
        _report_sensor_state(hass, "sensor.ka_temp", "18.0")
        await ent._async_calculate_and_control(force=True)
        assert ent._pwm_state is True

        hass.states._states["switch.ka_cool"] = State("switch.ka_cool", "on")
        hass.states._states["switch.ka_heat"] = State("switch.ka_heat", "off")
        sim.flaky_off_attempts.clear()
        sim.flaky_off_succeed_on["switch.ka_cool"] = 2
        sim.calls.clear()

        await ent._async_keep_alive_handler(dt_util.utcnow())
        off_indices = [
            idx
            for idx, c in enumerate(sim.calls)
            if c[1] == "turn_off" and c[2] == "switch.ka_cool"
        ]
        on_indices = [
            idx
            for idx, c in enumerate(sim.calls)
            if c[1] == "turn_on" and c[2] == "switch.ka_heat"
        ]
        assert len(off_indices) == 2
        assert len(on_indices) == 1
        assert max(off_indices) < min(on_indices)
    finally:
        await ent.async_will_remove_from_hass()
        await async_unload_entry(hass, entry)


# ---------------------------------------------------------------------------
# Defect #12 (High): Reload unverified OFF blocks ON until confirmed OFF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["stuck_on", "exception"])
async def test_defect_12_reload_off_failure_and_exception_preserve_unverified_and_block_on(
    hass, virtual_clock, monkeypatch, failure_mode: str
) -> None:
    """Failed/raising OFF at removal must NOT set reload_off, and must block ON until reload confirms OFF."""
    _patch_fast_guard(monkeypatch)
    sim = _SwitchSimulator(hass)
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    hass.states.async_set("sensor.rel_temp", "18.0")
    hass.states.async_set("switch.rel_heat", "off")
    hass.states.async_set("sensor.rel_hum", "80.0")
    hass.states.async_set("switch.rel_dehum", "off")

    clim_data = {
        CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
        CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
        CONF_INDOOR_SENSOR: "sensor.rel_temp",
        CONF_TARGET_DEVICE: "switch.rel_heat",
        CONF_INITIAL_TARGET_TEMP: 24.0,
        CONF_KP: 30.0,
        CONF_KI: 0.0,
        CONF_KD: 0.0,
        CONF_KE: 0.0,
        CONF_PWM_DURATION: 300,
        CONF_MIN_ON_TIME: 60,
        CONF_MIN_OFF_TIME: 180,
        CONF_SAMPLE_PERIOD: 600,
    }
    hum_data = {
        CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
        CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
        CONF_INDOOR_SENSOR: "sensor.rel_hum",
        CONF_TARGET_DEVICE: "switch.rel_dehum",
        CONF_KP: 30.0,
        CONF_KI: 0.0,
        CONF_KD: 0.0,
        CONF_KE: 0.0,
        CONF_PWM_DURATION: 300,
        CONF_MIN_ON_TIME: 60,
        CONF_MIN_OFF_TIME: 180,
        CONF_SAMPLE_PERIOD: 600,
    }

    entry_c, ent_c = await _setup_climate_entity(
        hass, entry_id=f"d12_c_{failure_mode}", data=clim_data
    )
    entry_h, ent_h = await _setup_humidifier_entity(
        hass, entry_id=f"d12_h_{failure_mode}", data=hum_data
    )

    await ent_c.async_set_hvac_mode(HVACMode.HEAT)
    await ent_h.async_set_humidity(45)
    await ent_h.async_turn_on()
    assert hass.states.get("switch.rel_heat").state == "on"
    assert hass.states.get("switch.rel_dehum").state == "on"

    if failure_mode == "stuck_on":
        sim.fail_off.update({"switch.rel_heat", "switch.rel_dehum"})
    else:
        sim.raise_off.update({"switch.rel_heat", "switch.rel_dehum"})

    # Unload / remove entities while OFF fails
    await ent_c.async_will_remove_from_hass()
    await ent_h.async_will_remove_from_hass()
    await async_unload_entry(hass, entry_c)
    await async_unload_entry(hass, entry_h)

    reload_off = hass.data[DOMAIN]["reload_off"]
    reload_unverified = hass.data[DOMAIN]["reload_unverified_off"]
    assert "switch.rel_heat" not in reload_off
    assert "switch.rel_dehum" not in reload_off
    assert "switch.rel_heat" in reload_unverified
    assert "switch.rel_dehum" in reload_unverified
    assert hass.states.get("switch.rel_heat").state == "on"
    assert hass.states.get("switch.rel_dehum").state == "on"

    # Advance 300s (beyond min_off=180s), keep sensor fresh, reload while relay STILL fails OFF
    await virtual_clock.advance(300.0)
    _report_sensor_state(hass, "sensor.rel_temp", "18.0")
    _report_sensor_state(hass, "sensor.rel_hum", "80.0")
    entry_c2, ent_c2 = await _setup_climate_entity(
        hass, entry_id=f"d12_c_{failure_mode}", data=clim_data
    )
    entry_h2, ent_h2 = await _setup_humidifier_entity(
        hass, entry_id=f"d12_h_{failure_mode}", data=hum_data
    )

    try:
        sim.calls.clear()
        await ent_c2.async_set_hvac_mode(HVACMode.HEAT)
        await ent_h2.async_set_humidity(45)
        await ent_h2.async_turn_on()
        # Still unverified OFF -> ON must NOT be sent
        assert len(sim.on_calls("switch.rel_heat")) == 0
        assert len(sim.on_calls("switch.rel_dehum")) == 0
        assert "switch.rel_heat" in ent_c2._unverified_off_devices
        assert "switch.rel_dehum" in ent_h2._unverified_off_devices

        # Now allow turn_off to succeed -> confirmation starts min_off window NOW!
        sim.fail_off.clear()
        sim.raise_off.clear()
        sim.calls.clear()
        await ent_c2._async_calculate_and_control(force=True)
        await ent_h2._async_calculate_and_control(force=True)

        assert hass.states.get("switch.rel_heat").state == "off"
        assert hass.states.get("switch.rel_dehum").state == "off"
        assert "switch.rel_heat" not in ent_c2._unverified_off_devices
        assert "switch.rel_dehum" not in ent_h2._unverified_off_devices
        assert len(sim.on_calls("switch.rel_heat")) == 0
        assert len(sim.on_calls("switch.rel_dehum")) == 0

        # 100s later (< 180s min_off since confirmation): still blocked!
        await virtual_clock.advance(100.0)
        _report_sensor_state(hass, "sensor.rel_temp", "18.0")
        _report_sensor_state(hass, "sensor.rel_hum", "80.0")
        await ent_c2._async_calculate_and_control(force=True)
        await ent_h2._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.rel_heat")) == 0
        assert len(sim.on_calls("switch.rel_dehum")) == 0

        # After 180s total from confirmation: ON is now permitted
        await virtual_clock.advance(90.0)
        _report_sensor_state(hass, "sensor.rel_temp", "18.0")
        _report_sensor_state(hass, "sensor.rel_hum", "80.0")
        await ent_c2._async_calculate_and_control(force=True)
        await ent_h2._async_calculate_and_control(force=True)
        assert len(sim.on_calls("switch.rel_heat")) >= 1
        assert len(sim.on_calls("switch.rel_dehum")) >= 1
    finally:
        await ent_c2.async_will_remove_from_hass()
        await ent_h2.async_will_remove_from_hass()
        await async_unload_entry(hass, entry_c2)
        await async_unload_entry(hass, entry_h2)


# ---------------------------------------------------------------------------
# Defect #13 (Medium): InfluxDB 0 in-range minutes preserves existing model
# ---------------------------------------------------------------------------


class _OutOfRangeInfluxSession:
    """Minimal mock InfluxQL session returning only hot out-of-range temperatures."""

    def __init__(self, series_points: list[tuple[int, float]]) -> None:
        self._points = series_points

    class _Resp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.status = 200
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def json(self) -> dict[str, Any]:
            return self._payload

    def get(
        self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ):
        q = str((params or {}).get("q", ""))
        qu = q.upper().strip()
        if qu.startswith("SHOW SERIES"):
            return self._Resp(
                {
                    "results": [
                        {
                            "statement_id": 0,
                            "series": [
                                {
                                    "columns": ["key"],
                                    "values": [
                                        ["°C,entity_id=living_temp"],
                                        ["state,entity_id=boiler"],
                                    ],
                                }
                            ],
                        }
                    ]
                }
            )
        if qu.startswith("SHOW FIELD KEYS"):
            m = re.search(r'FROM\s+"([^"]+)"', q)
            meas = m.group(1) if m else "°C"
            return self._Resp(
                {
                    "results": [
                        {
                            "statement_id": 0,
                            "series": [
                                {
                                    "name": meas,
                                    "columns": ["fieldKey", "fieldType"],
                                    "values": [["value", "float"]],
                                }
                            ],
                        }
                    ]
                }
            )
        if '"boiler"' in q or "living_temp" in q:
            if 'FROM "state"' in q or "boiler" in q:
                vals = [[ts, 0] for ts, _ in self._points]
            else:
                vals = [[ts, temp] for ts, temp in self._points]
            if "GROUP BY time(1m)" in q:
                col = "val" if "AS val" in q else "mean"
                return self._Resp(
                    {
                        "results": [
                            {
                                "statement_id": 0,
                                "series": [{"name": "°C", "columns": ["time", col], "values": vals}],
                            }
                        ]
                    }
                )
            return self._Resp(
                {
                    "results": [
                        {
                            "statement_id": 0,
                            "series": [{"name": "°C", "columns": ["time", "val"], "values": vals}],
                        }
                    ]
                }
            )
        return self._Resp({"results": [{"statement_id": 0, "series": []}]})


@pytest.mark.asyncio
async def test_defect_13_influxdb_zero_in_range_minutes_preserves_existing_model_and_store(
    hass,
) -> None:
    """When InfluxDB filter leaves in_range_minutes == 0, existing runtime & Store model must stay intact."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt_util.set_utcnow(now)

    entry, ent = await _setup_climate_entity(
        hass,
        entry_id="d13_influx_zero",
        entity_id="climate.d13_influx",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.living_temp",
            CONF_TARGET_DEVICE: "switch.boiler",
            CONF_INERTIA_PROFILE: "fast_air",
        },
    )
    rt: SmartPIDRuntime = entry.runtime_data

    # Train and persist existing model
    for i in range(6):
        rt.snapshot.temp_rls.update([1.0, -2.0 + i * 0.1, 0.0], 0.05)
    rt.snapshot.coasting.tau_coast = 3.75
    rt.snapshot.coasting.accepted_count = 4
    await rt.store.async_save(rt.snapshot)
    before_dict = rt.snapshot.to_dict()

    # Every InfluxDB point is 29.0°C (outside winter [16.0..23.5])
    pts = [
        (int((now - timedelta(minutes=30 - m)).timestamp() * 1000), 29.0)
        for m in range(30)
    ]
    hass._mock_http_session = _OutOfRangeInfluxSession(pts)

    try:
        res = await hass.services.async_call(
            DOMAIN,
            SERVICE_BOOTSTRAP_INFLUXDB,
            {
                "entity_id": ent.entity_id,
                "days": 30,
                "min_indoor_temp": 16.0,
                "max_indoor_temp": 23.5,
                "reset_existing": True,
                "dry_run": False,
            },
            return_response=True,
        )

        assert res["applied"] is False
        assert res["in_range_minutes"] == 0
        assert rt.snapshot.to_dict() == before_dict
        reloaded = await ModelStore(hass, entry.entry_id).async_load()
        assert reloaded is not None
        assert reloaded.to_dict() == before_dict
    finally:
        await ent.async_will_remove_from_hass()
        await async_unload_entry(hass, entry)


# ---------------------------------------------------------------------------
# Defect #14 (Medium): Corrupted HuberRLS covariance matrix P validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_p",
    [
        [],
        [[1.0, 0.0, 0.0], [0.0, 1.0]],
        [[1.0, float("nan"), 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[1.0, 0.0, 0.0], [0.0, float("inf"), 0.0], [0.0, 0.0, 1.0]],
    ],
)
async def test_defect_14_corrupt_rls_covariance_rejected_and_reset_for_next_update(
    hass, caplog, bad_p
) -> None:
    """Corrupt P matrix (empty/short/non-finite) must reset cleanly on load and allow next RLS update."""
    with pytest.raises(ValueError):
        HuberRLS.from_dict({"n_features": 3, "theta": [0.05, 0.005, 0.0], "P": bad_p})

    snap = LearningStateSnapshot()
    payload = snap.to_dict()
    payload["temp_rls"]["P"] = bad_p
    payload["temp_rls"]["sample_count"] = 25

    store = ModelStore(hass, "d14_corrupt_entry")
    await store._store.async_save(payload)

    with caplog.at_level(logging.WARNING):
        loaded = await store.async_load()

    assert loaded is not None
    assert loaded.temp_rls.sample_count == 0
    assert len(loaded.temp_rls.P) == 3
    assert all(len(row) == 3 and all(math.isfinite(v) for v in row) for row in loaded.temp_rls.P)

    loaded.temp_rls.update([1.0, -2.0, 0.0], 0.05)
    assert loaded.temp_rls.sample_count == 1
    assert all(math.isfinite(v) for v in loaded.temp_rls.theta)
