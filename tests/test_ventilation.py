"""Stage 2 unit tests: VentilationGuard 4-state machine, Section 0 dwell, and integral freeze."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
import homeassistant.util.dt as dt_util

from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_INDOOR_SENSOR,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_SAMPLE_PERIOD,
    CONF_SEC0_ACTION,
    CONF_SEC0_DELTA,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONF_VENT_COOLDOWN_MIN,
    CONF_VENT_DEBOUNCE_SEC,
    CONF_VENT_LOGIC,
    CONF_VENT_MODE,
    CONF_VENT_SENSORS,
    CONF_VENT_SWITCH,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    SEC0_ACTION_FAN_ONLY,
    SEC0_ACTION_SETBACK,
    THERMOSTAT_TYPE_COOLER,
    VENT_LOGIC_ALL,
    VENT_LOGIC_ANY,
    VENT_MODE_COMBINED,
    VENT_MODE_SENSORS,
    VENT_MODE_SWITCH,
)
from custom_components.smart_pid_airconditioner.ventilation import VentState, VentilationGuard


def test_ventilation_all_any_switch_combined(hass) -> None:
    """Test all, any, switch, and combined ventilation logic."""
    t0 = dt_util.utcnow()
    hass.states.async_set("binary_sensor.win1", "on")
    hass.states.async_set("binary_sensor.win2", "off")
    hass.states.async_set("input_boolean.vent", "off")

    # ALL: win1=on, win2=off -> not open
    g_all = VentilationGuard(
        mode=VENT_MODE_SENSORS,
        sensors=["binary_sensor.win1", "binary_sensor.win2"],
        logic=VENT_LOGIC_ALL,
        debounce_sec=0,
    )
    assert g_all.evaluate(hass, t0).state == VentState.NORMAL

    hass.states.async_set("binary_sensor.win2", "on")
    assert g_all.evaluate(hass, t0).state == VentState.VENTILATING

    # ANY: win1=on, win2=off -> open
    hass.states.async_set("binary_sensor.win2", "off")
    g_any = VentilationGuard(
        mode=VENT_MODE_SENSORS,
        sensors=["binary_sensor.win1", "binary_sensor.win2"],
        logic=VENT_LOGIC_ANY,
        debounce_sec=0,
    )
    assert g_any.evaluate(hass, t0).state == VentState.VENTILATING

    # SWITCH: input_boolean.vent=on -> open
    g_sw = VentilationGuard(mode=VENT_MODE_SWITCH, switch_entity="input_boolean.vent", debounce_sec=0)
    assert g_sw.evaluate(hass, t0).state == VentState.NORMAL
    hass.states.async_set("input_boolean.vent", "on")
    assert g_sw.evaluate(hass, t0).state == VentState.VENTILATING

    # COMBINED: sensors closed, switch on -> open
    hass.states.async_set("binary_sensor.win1", "off")
    g_comb = VentilationGuard(
        mode=VENT_MODE_COMBINED,
        sensors=["binary_sensor.win1", "binary_sensor.win2"],
        logic=VENT_LOGIC_ALL,
        switch_entity="input_boolean.vent",
        debounce_sec=0,
    )
    assert g_comb.evaluate(hass, t0).state == VentState.VENTILATING


def test_ventilation_debounce_cooldown_and_unavailable_grace(hass) -> None:
    """Test 45s debounce, 20m cooldown, and 5m unavailable grace -> degraded."""
    t0 = dt_util.utcnow()
    hass.states.async_set("binary_sensor.win1", "on")
    guard = VentilationGuard(
        mode=VENT_MODE_SENSORS,
        sensors=["binary_sensor.win1"],
        debounce_sec=45,
        cooldown_min=20,
        unavailable_grace_sec=300,
    )

    # t0: open -> OPEN_DEBOUNCE (control not paused, learning blocked)
    st = guard.evaluate(hass, t0)
    assert st.state == VentState.OPEN_DEBOUNCE
    assert st.should_pause_control is False
    assert st.should_block_learning is True

    # t0 + 30s: still OPEN_DEBOUNCE
    st = guard.evaluate(hass, t0 + timedelta(seconds=30))
    assert st.state == VentState.OPEN_DEBOUNCE

    # t0 + 46s: transitions to VENTILATING
    st = guard.evaluate(hass, t0 + timedelta(seconds=46))
    assert st.state == VentState.VENTILATING
    assert st.should_pause_control is True
    assert st.should_block_learning is True

    # Close window -> POST_CLOSE_COOLDOWN (control resumes immediately, learning blocked 20m)
    t_close = t0 + timedelta(seconds=60)
    hass.states.async_set("binary_sensor.win1", "off")
    st = guard.evaluate(hass, t_close)
    assert st.state == VentState.POST_CLOSE_COOLDOWN
    assert st.should_pause_control is False
    assert st.should_block_learning is True
    assert st.cooldown_remaining_sec == 1200

    # After 20 min -> NORMAL
    st = guard.evaluate(hass, t_close + timedelta(minutes=20))
    assert st.state == VentState.NORMAL
    assert st.should_block_learning is False

    # Unavailable <= 5 min holds last state; > 5 min -> degraded (learning blocked, control active)
    t_unavail = t_close + timedelta(minutes=21)
    hass.states.async_set("binary_sensor.win1", "unavailable")
    st = guard.evaluate(hass, t_unavail)
    assert st.degraded is False
    assert st.should_block_learning is False

    st = guard.evaluate(hass, t_unavail + timedelta(seconds=301))
    assert st.degraded is True
    assert st.should_pause_control is False
    assert st.should_block_learning is True


@pytest.mark.asyncio
async def test_integral_freeze_and_pwm_off_during_ventilation(hass) -> None:
    """Verify VENTILATING freezes integral (does not reset to 0) and turns off PWM switch."""
    hass.states.async_set("sensor.room_temp", "26.0")
    hass.states.async_set("binary_sensor.win1", "off")
    hass.states.async_set("switch.ac_plug", "on")

    async def _turn_on(call) -> None:
        eid = call.data.get("entity_id")
        if isinstance(eid, str):
            hass.states.async_set(eid, "on")

    async def _turn_off(call) -> None:
        eid = call.data.get("entity_id")
        if isinstance(eid, str):
            hass.states.async_set(eid, "off")

    hass.services.async_register("switch", "turn_on", _turn_on)
    hass.services.async_register("switch", "turn_off", _turn_off)

    entry = ConfigEntry(
        entry_id="vent_pwm",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_DEVICE: "switch.ac_plug",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_KP: 20.0,
            CONF_KI: 1.0,
            CONF_MAX_I: 20.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_SAMPLE_PERIOD: 60,
            CONF_VENT_MODE: VENT_MODE_SENSORS,
            CONF_VENT_SENSORS: ["binary_sensor.win1"],
            CONF_VENT_DEBOUNCE_SEC: 0,
            CONF_VENT_COOLDOWN_MIN: 20,
        },
        version=2,
    )
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    await entity.async_added_to_hass()
    await entity.async_set_hvac_mode(HVACMode.COOL)

    # Accumulate integral
    entity._integral = 5.0
    t1 = dt_util.utcnow() + timedelta(seconds=10)
    dt_util.set_utcnow(t1)
    hass.states.async_set("binary_sensor.win1", "on")
    await entity._async_calculate_and_control(now=t1, force=False)

    assert entity._vent_status.state == VentState.VENTILATING
    assert entity._integral == pytest.approx(5.0)
    assert entity._pwm_state is False
    assert entity.extra_state_attributes["ventilation_state"] == "ventilating"
    assert entity.extra_state_attributes["learning_blocked"] is True


@pytest.mark.asyncio
async def test_section_0_transitions_dwell_and_integral_freeze(hass) -> None:
    """Verify climate_delta Section 0 standby transitions, 240s/300s dwell, and integral freeze."""
    hass.states.async_set("sensor.room_temp", "24.1")
    hass.states.async_set(
        "climate.ac_slave",
        "cool",
        {"hvac_modes": ["off", "cool", "fan_only"], "fan_modes": ["low", "high"], "min_temp": 18, "max_temp": 30},
    )

    entry = ConfigEntry(
        entry_id="sec0_test",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_CLIMATE: "climate.ac_slave",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_KP: 20.0,
            CONF_KI: 1.0,
            CONF_MAX_I: 20.0,
            CONF_KD: 0.0,
            CONF_KE: 0.0,
            CONF_SAMPLE_PERIOD: 60,
            CONF_SEC0_ACTION: SEC0_ACTION_SETBACK,
            CONF_SEC0_DELTA: 2.0,
        },
        version=2,
    )
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    await entity.async_added_to_hass()
    await entity.async_set_hvac_mode(HVACMode.COOL)
    assert entity._active_section == 1

    # Output = 20 * 0.1 = 2.0 <= 5.0, but min_dwell_active (240s) has NOT elapsed -> stay in Section 1
    t1 = dt_util.utcnow() + timedelta(seconds=60)
    dt_util.set_utcnow(t1)
    await entity._async_calculate_and_control(now=t1, force=False)
    assert entity._active_section == 1

    # After >= 240s -> enters Section 0 with setback +2.0°C (target 24 + 2 = 26)
    t2 = t1 + timedelta(seconds=250)
    dt_util.set_utcnow(t2)
    await entity._async_calculate_and_control(now=t2, force=False)
    assert entity._active_section == 0
    assert entity._dispatched_temperature == 26
    frozen_i = entity._integral

    # While in Section 0, even if temp rises slightly, integral is frozen and min_dwell_sec0 (300s) holds Section 0
    t3 = t2 + timedelta(seconds=100)
    dt_util.set_utcnow(t3)
    hass.states.async_set("sensor.room_temp", "25.0")
    await entity._async_calculate_and_control(now=t3, force=False)
    assert entity._active_section == 0
    assert entity._integral == pytest.approx(frozen_i)

    # After >= 300s -> exits Section 0 back to active section
    t4 = t2 + timedelta(seconds=310)
    dt_util.set_utcnow(t4)
    await entity._async_calculate_and_control(now=t4, force=False)
    assert entity._active_section in (1, 2, 3, 4)
