"""Stage 11 tests: state_reported heartbeat, outage buffer replay, overheat latch, and reload min-off carry-over."""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from custom_components.smart_pid_airconditioner import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.actuator import SwitchCommandGuard
from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_INDOOR_SENSOR,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_MAX_TEMP,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_MIN_TEMP,
    CONF_OUTDOOR_SENSOR,
    CONF_PWM_DURATION,
    CONF_SEC1_HVAC_MODE,
    CONF_SEC2_HVAC_MODE,
    CONF_SEC3_HVAC_MODE,
    CONF_SEC4_HVAC_MODE,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    DOMAIN,
    OVERHEAT_LATCH_TEMP_C,
    OVERHEAT_RESET_TEMP_C,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HEAT_COOL,
)
from custom_components.smart_pid_airconditioner.humidifier import SmartPIDHumidifierEntity
from custom_components.smart_pid_airconditioner.learner import CyclePhase
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.ventilation import VentState, VentStatus
from homeassistant.components import persistent_notification
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Event, State
import homeassistant.util.dt as dt_util


class FakeBus:
    """Minimal Home Assistant event bus mock supporting async_listen and async_fire."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[[Event], None]]] = {}

    def async_listen(
        self, event_type: str, listener: Callable[[Event], None]
    ) -> Callable[[], None]:
        self._listeners.setdefault(event_type, []).append(listener)

        def _unsub() -> None:
            lst = self._listeners.get(event_type, [])
            if listener in lst:
                lst.remove(listener)

        return _unsub

    def async_fire(self, event_type: str, event_data: dict[str, Any]) -> None:
        ev = Event(event_type=event_type, data=dict(event_data))
        for cb in list(self._listeners.get(event_type, [])):
            cb(ev)

    def listener_count(self, event_type: str) -> int:
        return len(self._listeners.get(event_type, []))


def _make_obs(
    rt: SmartPIDRuntime,
    *,
    indoor_val: float,
    is_active: bool,
    u_effort: float,
    device_unavailable: bool = False,
    target_val: float = 22.0,
    outdoor_val: float = 10.0,
    is_heating: bool = True,
) -> None:
    rt.update_observation(
        indoor_val=indoor_val,
        outdoor_val=outdoor_val,
        target_val=target_val,
        u_effort=u_effort,
        is_active=is_active,
        is_heating=is_heating,
        vent_status=VentStatus(VentState.NORMAL, False, False, 0, False),
        device_unavailable=device_unavailable,
    )


@pytest.mark.asyncio
async def test_state_reported_refreshes_timestamps_and_prevents_failsafe_climate(hass) -> None:
    """1A) state_reported with unchanged temp refreshes update timestamps & resets stale count on climate."""
    t0 = dt_util.utcnow()
    hass.bus = FakeBus()
    hass.states.async_set("sensor.room_temp", "24.0")
    hass.states.async_set("sensor.out_temp", "12.0")
    hass.states.async_set("climate.slave_ac", "cool", {"hvac_modes": ["off", "cool"], "fan_modes": ["low", "high"]})

    entry = ConfigEntry(
        entry_id="sr_climate_1",
        title="SR Climate",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_OUTDOOR_SENSOR: "sensor.out_temp",
            CONF_TARGET_DEVICE: "climate.slave_ac",
            CONF_TARGET_CLIMATE: "climate.slave_ac",
            CONF_INITIAL_TARGET_TEMP: 24.0,
        },
        version=2,
    )
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    await entity.async_added_to_hass()
    assert hass.bus.listener_count("state_reported") == 1

    # Simulate stale condition accumulated before heartbeat arrives
    entity._sensor_stale_count = 2
    entity._last_indoor_update = t0 - timedelta(minutes=20)
    entity._last_outdoor_update = t0 - timedelta(minutes=20)

    t1 = t0 + timedelta(minutes=5)
    dt_util.set_utcnow(t1)
    # Periodic state_reported with identical values
    hass.bus.async_fire(
        "state_reported",
        {"entity_id": "sensor.room_temp", "new_state": hass.states.get("sensor.room_temp")},
    )
    hass.bus.async_fire(
        "state_reported",
        {"entity_id": "sensor.out_temp", "new_state": hass.states.get("sensor.out_temp")},
    )

    assert entity._last_indoor_update == t1
    assert entity._last_outdoor_update == t1
    assert entity._sensor_stale_count == 0

    await entity._async_calculate_and_control(now=t1)
    assert entity._fail_safe_active is False

    await entity.async_will_remove_from_hass()
    assert hass.bus.listener_count("state_reported") == 0


@pytest.mark.asyncio
async def test_state_reported_humidifier_and_missing_bus_compat(hass) -> None:
    """1B) state_reported works on humidifier and missing hass.bus does not raise."""
    t0 = dt_util.utcnow()
    hass.states.async_set("sensor.room_hum", "55.0")
    hass.states.async_set("sensor.out_hum", "65.0")
    hass.states.async_set("switch.dehum_plug", STATE_OFF)

    # First verify compatibility when hass has NO bus attribute (default stub)
    assert not hasattr(hass, "bus")
    entry_nobus = ConfigEntry(
        entry_id="sr_hum_nobus",
        title="NoBus Hum",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_INDOOR_SENSOR: "sensor.room_hum",
            CONF_TARGET_DEVICE: "switch.dehum_plug",
        },
        version=2,
    )
    hum_nobus = SmartPIDHumidifierEntity(hass, entry_nobus)
    hum_nobus.hass = hass
    await hum_nobus.async_added_to_hass()
    await hum_nobus.async_will_remove_from_hass()

    # Now attach FakeBus and verify state_reported heartbeat on humidifier
    hass.bus = FakeBus()
    entry = ConfigEntry(
        entry_id="sr_hum_1",
        title="SR Hum",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_INDOOR_SENSOR: "sensor.room_hum",
            CONF_OUTDOOR_SENSOR: "sensor.out_hum",
            CONF_TARGET_DEVICE: "switch.dehum_plug",
        },
        version=2,
    )
    hum = SmartPIDHumidifierEntity(hass, entry)
    hum.hass = hass
    await hum.async_added_to_hass()
    assert hass.bus.listener_count("state_reported") == 1

    hum._sensor_stale_count = 2
    hum._last_indoor_update = t0 - timedelta(minutes=15)
    hum._last_outdoor_update = t0 - timedelta(minutes=15)

    t1 = t0 + timedelta(minutes=3)
    dt_util.set_utcnow(t1)
    hass.bus.async_fire(
        "state_reported",
        {"entity_id": "sensor.room_hum", "new_state": hass.states.get("sensor.room_hum")},
    )
    hass.bus.async_fire(
        "state_reported",
        {"entity_id": "sensor.out_hum", "new_state": hass.states.get("sensor.out_hum")},
    )

    assert hum._last_indoor_update == t1
    assert hum._last_outdoor_update == t1
    assert hum._sensor_stale_count == 0

    await hum._async_calculate_and_control(now=t1)
    assert hum._fail_safe_active is False

    await hum.async_will_remove_from_hass()
    assert hass.bus.listener_count("state_reported") == 0


@pytest.mark.asyncio
async def test_outage_grace_buffer_replay_and_contamination_cases(hass) -> None:
    """2) 60s outage grace buffers up to 10 ticks; same-state recovery replays cleanly, mismatch/expiry contaminates."""
    t0 = dt_util.utcnow()
    entry = ConfigEntry(
        entry_id="outage_buf_1",
        title="Outage Buffer",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_DEVICE: "switch.boiler",
        },
        version=2,
    )

    # Case A: Same-state recovery within 60s replays buffered coasting completion
    rt = SmartPIDRuntime(hass, entry)
    await rt.async_setup()
    rt.snapshot.coasting.peak_confirm_delta = 0.05

    for m in range(6):
        dt_util.set_utcnow(t0 + timedelta(minutes=m))
        _make_obs(rt, indoor_val=20.0 + m * 0.2, is_active=True, u_effort=1.0)
        await rt.async_step_learning(now=dt_util.utcnow())

    # Transition to POST_STOP_COAST (stop_val = 21.0)
    dt_util.set_utcnow(t0 + timedelta(minutes=6))
    _make_obs(rt, indoor_val=21.0, is_active=False, u_effort=0.0)
    await rt.async_step_learning(now=dt_util.utcnow())
    assert rt.snapshot.coasting.phase == CyclePhase.POST_STOP_COAST

    # Device goes unavailable briefly while _obs_is_active is False
    t_out = t0 + timedelta(minutes=6, seconds=10)
    dt_util.set_utcnow(t_out)
    _make_obs(rt, indoor_val=21.5, is_active=True, u_effort=0.0, device_unavailable=True)
    assert rt._outage_pre_active is False

    # Buffer peak + 2 reversal ticks during <60s outage
    for idx, val in enumerate((21.5, 21.40, 21.30)):
        dt_util.set_utcnow(t_out + timedelta(seconds=10 + idx * 10))
        _make_obs(rt, indoor_val=val, is_active=True, u_effort=0.0, device_unavailable=True)
        await rt.async_step_learning(now=dt_util.utcnow())

    assert len(rt._outage_buffered_ticks) == 3
    assert rt.snapshot.coasting.accepted_count == 0
    assert rt.snapshot.coasting.phase == CyclePhase.POST_STOP_COAST

    # Recover within 60s with SAME state (is_active=False) -> sequential replay completes cycle!
    dt_util.set_utcnow(t_out + timedelta(seconds=45))
    _make_obs(rt, indoor_val=21.30, is_active=False, u_effort=0.0, device_unavailable=False)
    assert rt._outage_buffered_ticks == []
    assert rt._outage_pre_active is None
    assert rt.snapshot.coasting.accepted_count == 1
    assert rt.snapshot.coasting.last_quality_reason == "accepted"
    assert rt.snapshot.coasting.phase == CyclePhase.PASSIVE_DRIFT

    # Case B: Max 10 buffered ticks cap + DIFFERENT state recovery discards buffer & contaminates
    rt.snapshot.coasting.phase = CyclePhase.IDLE
    rt.snapshot.coasting.emitter_charge = 0.0
    t_b = t0 + timedelta(minutes=30)
    for m in range(5):
        dt_util.set_utcnow(t_b + timedelta(minutes=m))
        _make_obs(rt, indoor_val=20.0 + m * 0.1, is_active=True, u_effort=1.0)
        await rt.async_step_learning(now=dt_util.utcnow())
    dt_util.set_utcnow(t_b + timedelta(minutes=5))
    _make_obs(rt, indoor_val=20.5, is_active=False, u_effort=0.0)
    await rt.async_step_learning(now=dt_util.utcnow())
    assert rt.snapshot.coasting.phase == CyclePhase.POST_STOP_COAST

    t_out_b = t_b + timedelta(minutes=5, seconds=5)
    dt_util.set_utcnow(t_out_b)
    _make_obs(rt, indoor_val=20.7, is_active=False, u_effort=0.0, device_unavailable=True)
    for idx in range(12):
        dt_util.set_utcnow(t_out_b + timedelta(seconds=idx * 2))
        await rt.async_step_learning(now=dt_util.utcnow())
    assert len(rt._outage_buffered_ticks) == 10

    # Recover with DIFFERENT state (is_active=True != _outage_pre_active=False)
    dt_util.set_utcnow(t_out_b + timedelta(seconds=40))
    _make_obs(rt, indoor_val=20.7, is_active=True, u_effort=1.0, device_unavailable=False)
    assert rt._outage_buffered_ticks == []
    assert rt.snapshot.coasting.last_quality_reason == "device_unavailable"
    assert rt.snapshot.coasting.accepted_count == 1  # unchanged

    # Case C: Exceeding 60s grace discards buffer & contaminates
    rt.snapshot.coasting.phase = CyclePhase.IDLE
    rt.snapshot.coasting.emitter_charge = 0.0
    t_c = t0 + timedelta(minutes=60)
    for m in range(5):
        dt_util.set_utcnow(t_c + timedelta(minutes=m))
        _make_obs(rt, indoor_val=20.0 + m * 0.1, is_active=True, u_effort=1.0)
        await rt.async_step_learning(now=dt_util.utcnow())
    dt_util.set_utcnow(t_c + timedelta(minutes=5))
    _make_obs(rt, indoor_val=20.5, is_active=False, u_effort=0.0)
    await rt.async_step_learning(now=dt_util.utcnow())

    t_out_c = t_c + timedelta(minutes=5, seconds=5)
    dt_util.set_utcnow(t_out_c)
    _make_obs(rt, indoor_val=20.7, is_active=False, u_effort=0.0, device_unavailable=True)
    await rt.async_step_learning(now=dt_util.utcnow())
    assert len(rt._outage_buffered_ticks) == 1

    # >60s passes while still unavailable
    dt_util.set_utcnow(t_out_c + timedelta(seconds=65))
    _make_obs(rt, indoor_val=20.8, is_active=False, u_effort=0.0, device_unavailable=True)
    assert rt._outage_buffered_ticks == []
    assert rt._obs_device_unavailable is True
    assert rt.snapshot.coasting.last_quality_reason == "device_unavailable"

    await rt.async_unload()


@pytest.mark.asyncio
async def test_overheat_safety_latch_pwm_and_climate_delta(hass) -> None:
    """3) Overheat latch triggers at 35.0C, blocks at 34.5C, and resets at <=34.0C."""
    # Part A: Switch PWM Heater
    hass.states.async_set("sensor.room_temp", "25.0")
    hass.states.async_set("switch.boiler_oh", STATE_OFF)

    async def handle_on(call):
        hass.states.async_set(call.data["entity_id"], STATE_ON)

    async def handle_off(call):
        hass.states.async_set(call.data["entity_id"], STATE_OFF)

    hass.services.async_register("switch", "turn_on", handle_on)
    hass.services.async_register("switch", "turn_off", handle_off)
    hass.services.async_register("homeassistant", "turn_on", handle_on)
    hass.services.async_register("homeassistant", "turn_off", handle_off)

    entry_pwm = ConfigEntry(
        entry_id="overheat_pwm_1",
        title="Overheat Boiler",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_DEVICE: "switch.boiler_oh",
            CONF_INITIAL_TARGET_TEMP: 38.0,
            CONF_MIN_TEMP: 10.0,
            CONF_MAX_TEMP: 40.0,
            CONF_PWM_DURATION: 600,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 60,
            CONF_KP: 20.0,
            CONF_KI: 0.0,
            CONF_KE: 0.0,
        },
        version=2,
    )
    entity = SmartPIDThermostatEntity(hass, entry_pwm)
    entity.hass = hass
    entity._switch_guard = SwitchCommandGuard(hass, confirm_timeout=0.05, off_retry_delays=(0.01,))
    t_oh = dt_util.utcnow()
    await entity.async_added_to_hass()
    dt_util.set_utcnow(t_oh + timedelta(seconds=65))
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    assert entity._pwm_state is True
    assert entity._overheat_latched is False
    assert entity.extra_state_attributes["overheat_latched"] is False

    # Hit 35.0C -> Overheat latch triggers, forces OFF, emits persistent notification
    t_latch = t_oh + timedelta(seconds=130)
    dt_util.set_utcnow(t_latch)
    hass.states.async_set("sensor.room_temp", str(OVERHEAT_LATCH_TEMP_C))
    await entity._async_calculate_and_control(now=t_latch, force=True)
    assert entity._overheat_latched is True
    assert entity.extra_state_attributes["overheat_latched"] is True
    assert entity._pwm_state is False
    assert entity._pid_output == 0.0
    assert hass.states.get("switch.boiler_oh").state == STATE_OFF
    assert any(
        n["notification_id"] == f"{DOMAIN}_overheat_{entry_pwm.entry_id}"
        for n in persistent_notification._notifications
    )

    # Cool slightly to 34.5C (even past min_off_time) -> still latched, restart blocked
    t_mid = t_latch + timedelta(seconds=65)
    dt_util.set_utcnow(t_mid)
    hass.states.async_set("sensor.room_temp", "34.5")
    await entity._async_calculate_and_control(now=t_mid, force=True)
    assert entity._overheat_latched is True
    assert entity._pwm_state is False
    assert hass.states.get("switch.boiler_oh").state == STATE_OFF

    # Cool to 34.0C -> latch clears and heating resumes
    t_reset = t_mid + timedelta(seconds=65)
    dt_util.set_utcnow(t_reset)
    hass.states.async_set("sensor.room_temp", str(OVERHEAT_RESET_TEMP_C))
    await entity._async_calculate_and_control(now=t_reset, force=True)
    assert entity._overheat_latched is False
    assert entity.extra_state_attributes["overheat_latched"] is False
    assert entity._pwm_state is True
    assert hass.states.get("switch.boiler_oh").state == STATE_ON
    await entity.async_will_remove_from_hass()

    # Part B: Climate Delta Heat/Cool in heating direction
    hass.states.async_set("sensor.room_temp_hc", "25.0")
    hass.states.async_set(
        "climate.slave_hc",
        "heat",
        {"hvac_modes": ["off", "cool", "heat", "auto"], "temperature": 26, "min_temp": 16, "max_temp": 30},
    )

    async def handle_slave_mode(call):
        hass.states.async_set(
            call.data["entity_id"],
            call.data["hvac_mode"],
            hass.states.get(call.data["entity_id"]).attributes,
        )

    hass.services.async_register("climate", "set_hvac_mode", handle_slave_mode)
    hass.services.async_register("climate", "set_temperature", lambda call: None)
    hass.services.async_register("climate", "set_fan_mode", lambda call: None)

    entry_hc = ConfigEntry(
        entry_id="overheat_hc_1",
        title="Overheat HeatCool",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_INDOOR_SENSOR: "sensor.room_temp_hc",
            CONF_TARGET_DEVICE: "climate.slave_hc",
            CONF_TARGET_CLIMATE: "climate.slave_hc",
            CONF_INITIAL_TARGET_TEMP: 38.0,
            CONF_MIN_TEMP: 16.0,
            CONF_MAX_TEMP: 40.0,
            CONF_SEC1_HVAC_MODE: "heat",
            CONF_SEC2_HVAC_MODE: "heat",
            CONF_SEC3_HVAC_MODE: "heat",
            CONF_SEC4_HVAC_MODE: "heat",
        },
        version=2,
    )
    hc_entity = SmartPIDThermostatEntity(hass, entry_hc)
    hc_entity.hass = hass
    t_hc = dt_util.utcnow()
    await hc_entity.async_added_to_hass()
    await hc_entity.async_set_hvac_mode(HVACMode.HEAT)

    t_hc_latch = t_hc + timedelta(seconds=60)
    dt_util.set_utcnow(t_hc_latch)
    hass.states.async_set("sensor.room_temp_hc", "35.0")
    await hc_entity._async_calculate_and_control(now=t_hc_latch, force=True)
    assert hc_entity._overheat_latched is True
    assert hc_entity._active_section == 0
    assert hass.states.get("climate.slave_hc").state == "off"

    # Attempting to switch to HEAT at 34.5C remains blocked
    t_hc_mid = t_hc_latch + timedelta(seconds=60)
    dt_util.set_utcnow(t_hc_mid)
    hass.states.async_set("sensor.room_temp_hc", "34.5")
    await hc_entity.async_set_hvac_mode(HVACMode.HEAT)
    assert hc_entity._overheat_latched is True
    assert hass.states.get("climate.slave_hc").state == "off"

    # Drops to 34.0C after MIN_DWELL_SEC0 -> clears latch and restores heat mode
    t_hc_reset = t_hc_latch + timedelta(seconds=310)
    dt_util.set_utcnow(t_hc_reset)
    hass.states.async_set("sensor.room_temp_hc", "34.0")
    await hc_entity._async_calculate_and_control(now=t_hc_reset, force=True)
    assert hc_entity._overheat_latched is False
    assert hc_entity._active_section > 0
    assert hass.states.get("climate.slave_hc").state == "heat"
    await hc_entity.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_reload_min_off_carryover_climate_and_humidifier(hass) -> None:
    """4) Unload/reload carries over min_off protection deadline via hass.data[DOMAIN]['reload_off']."""
    t0 = dt_util.utcnow()

    async def handle_on(call):
        hass.states.async_set(call.data["entity_id"], STATE_ON)

    async def handle_off(call):
        hass.states.async_set(call.data["entity_id"], STATE_OFF)

    hass.services.async_register("switch", "turn_on", handle_on)
    hass.services.async_register("switch", "turn_off", handle_off)
    hass.services.async_register("homeassistant", "turn_on", handle_on)
    hass.services.async_register("homeassistant", "turn_off", handle_off)

    # Part A: Climate PWM entity running at unload
    hass.states.async_set("sensor.room_temp", "28.0")
    hass.states.async_set("switch.ac_reload", STATE_OFF)
    entry = ConfigEntry(
        entry_id="reload_clim_1",
        title="Reload AC",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_DEVICE: "switch.ac_reload",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_PWM_DURATION: 900,
            CONF_MIN_ON_TIME: 120,
            CONF_MIN_OFF_TIME: 180,
            CONF_KP: 25.0,
            CONF_KI: 0.0,
            CONF_KE: 0.0,
        },
        version=2,
    )
    assert await async_setup_entry(hass, entry) is True
    e1 = SmartPIDThermostatEntity(hass, entry)
    e1.hass = hass
    e1._switch_guard = SwitchCommandGuard(hass, confirm_timeout=0.05, off_retry_delays=(0.01,))
    await e1.async_added_to_hass()
    t_run = t0 + timedelta(seconds=200)
    dt_util.set_utcnow(t_run)
    await e1.async_set_hvac_mode(HVACMode.COOL)
    assert e1._pwm_state is True
    assert hass.states.get("switch.ac_reload").state == STATE_ON

    # Unload while switch is active -> reload_off records (t_run, t_run + 180s)
    await e1.async_will_remove_from_hass()
    await async_unload_entry(hass, entry)
    reload_off = hass.data[DOMAIN]["reload_off"]
    assert "switch.ac_reload" in reload_off
    off_at, deadline = reload_off["switch.ac_reload"]
    assert off_at == t_run
    assert deadline == t_run + timedelta(seconds=180)

    # Physical switch is now off; reload 30s later with COOL restored -> inherited min_off blocks immediate ON
    hass.states.async_set("switch.ac_reload", STATE_OFF)
    dt_util.set_utcnow(t_run + timedelta(seconds=30))
    assert await async_setup_entry(hass, entry) is True
    e2 = SmartPIDThermostatEntity(hass, entry)
    e2.hass = hass
    e2._switch_guard = SwitchCommandGuard(hass, confirm_timeout=0.05, off_retry_delays=(0.01,))
    e2._mock_last_state = State("climate.reload_ac", HVACMode.COOL)
    await e2.async_added_to_hass()
    assert e2._last_switch_off_times["switch.ac_reload"] == t_run
    assert "switch.ac_reload" not in hass.data[DOMAIN]["reload_off"]
    assert e2._pwm_state is False
    assert hass.states.get("switch.ac_reload").state == STATE_OFF
    assert e2._pwm_turn_on_cancel is not None

    # After min_off deadline passes (t_run + 185s), turning ON succeeds
    dt_util.set_utcnow(t_run + timedelta(seconds=185))
    await e2._async_turn_on_switch()
    assert e2._pwm_state is True
    assert hass.states.get("switch.ac_reload").state == STATE_ON
    await e2.async_will_remove_from_hass()
    await async_unload_entry(hass, entry)

    # Part B: Humidifier PWM entity recently stopped before unload
    t1 = dt_util.utcnow()
    hass.states.async_set("sensor.room_hum_r", "70.0")
    hass.states.async_set("switch.dehum_reload", STATE_OFF)
    hum_entry = ConfigEntry(
        entry_id="reload_hum_1",
        title="Reload Dehum",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_INDOOR_SENSOR: "sensor.room_hum_r",
            CONF_TARGET_DEVICE: "switch.dehum_reload",
            CONF_INITIAL_TARGET_TEMP: 50.0,
            CONF_PWM_DURATION: 900,
            CONF_MIN_ON_TIME: 120,
            CONF_MIN_OFF_TIME: 180,
            CONF_KP: 20.0,
            CONF_KI: 0.0,
            CONF_KE: 0.0,
        },
        version=2,
    )
    assert await async_setup_entry(hass, hum_entry) is True
    h1 = SmartPIDHumidifierEntity(hass, hum_entry)
    h1.hass = hass
    h1._switch_guard = SwitchCommandGuard(hass, confirm_timeout=0.05, off_retry_delays=(0.01,))
    h1._last_switch_off_times["switch.dehum_reload"] = t1 - timedelta(seconds=60)
    h1._last_switch_off_time = t1 - timedelta(seconds=60)
    await h1.async_will_remove_from_hass()
    await async_unload_entry(hass, hum_entry)

    assert "switch.dehum_reload" in hass.data[DOMAIN]["reload_off"]

    # Reload at t1 + 30s (still 90s left out of 180s) -> _async_turn_on_switch blocked
    dt_util.set_utcnow(t1 + timedelta(seconds=30))
    assert await async_setup_entry(hass, hum_entry) is True
    h2 = SmartPIDHumidifierEntity(hass, hum_entry)
    h2.hass = hass
    h2._switch_guard = SwitchCommandGuard(hass, confirm_timeout=0.05, off_retry_delays=(0.01,))
    await h2.async_added_to_hass()
    assert h2._last_switch_off_times["switch.dehum_reload"] == t1 - timedelta(seconds=60)
    await h2._async_turn_on_switch("switch.dehum_reload")
    assert hass.states.get("switch.dehum_reload").state == STATE_OFF

    # Past deadline (t1 + 125s -> 185s since off) -> allowed
    dt_util.set_utcnow(t1 + timedelta(seconds=125))
    await h2._async_turn_on_switch("switch.dehum_reload")
    assert hass.states.get("switch.dehum_reload").state == STATE_ON
    await h2.async_will_remove_from_hass()
    await async_unload_entry(hass, hum_entry)


def test_stage11_translations_synced() -> None:
    """5) Verify strings.json, translations/ko.json, and translations/en.json are valid JSON with Stage 11 keys."""
    base = Path(__file__).resolve().parents[1] / "custom_components" / "smart_pid_airconditioner"
    for rel in ("strings.json", "translations/ko.json", "translations/en.json"):
        data = json.loads((base / rel).read_text(encoding="utf-8"))
        attrs = data["entity"]["climate"]["smart_pid_airconditioner"]["state_attributes"]
        assert "overheat_latched" in attrs
        assert attrs["overheat_latched"]["name"]
