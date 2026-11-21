"""Stage 5 unit & integration tests: adaptive_auto, early-stop, rollback, manual override, watchdog, cross-coupling, recorder."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, State
import homeassistant.util.dt as dt_util

from custom_components.smart_pid_airconditioner import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_COUPLED_AC,
    CONF_COUPLED_DEHUMIDIFIER,
    CONF_INDOOR_SENSOR,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MANUAL_OVERRIDE_MIN,
    CONF_MAX_I,
    CONF_MAX_RUN_MIN,
    CONF_OUTDOOR_SENSOR,
    CONF_RECORDER_BOOTSTRAP,
    CONF_SAFETY_REST_MIN,
    CONF_SAMPLE_PERIOD,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HEAT_COOL,
    TUNING_MODE_AUTO,
)
from custom_components.smart_pid_airconditioner.humidifier import SmartPIDHumidifierEntity
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime


@pytest.mark.asyncio
async def test_adaptive_auto_gating_early_stop_and_rollback(hass) -> None:
    """Verify adaptive_auto confidence gating, switch_pwm/climate_delta early-stop, and 5-cycle rollback."""
    hass.states.async_set("sensor.room_temp", "24.2")
    hass.states.async_set("sensor.out_temp", "28.0")
    hass.states.async_set("switch.ac_plug", "off")

    entry = ConfigEntry(
        entry_id="auto_pwm",
        title="Auto PWM",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_OUTDOOR_SENSOR: "sensor.out_temp",
            CONF_TARGET_DEVICE: "switch.ac_plug",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KE: 0.6,
            CONF_TUNING_MODE: TUNING_MODE_AUTO,
        },
        version=2,
    )
    await async_setup_entry(hass, entry)
    rt: SmartPIDRuntime = entry.runtime_data

    # Below tuned tier -> falls back to manual PID with fallback_reason
    kp, ki, ke, fb = rt.get_effective_pid_params(20.0, 0.5, 0.6)
    assert kp == 20.0
    assert fb == "insufficient_valid_cycles"

    # Meet tuned tier (>= 20 RLS samples, >= 10 valid cycles)
    rt.snapshot.temp_rls.sample_count = 25
    rt.snapshot.coasting.accepted_count = 12
    rt.snapshot.coasting.tau_coast = 5.0
    rt.snapshot.coasting._stop_slope = 0.1  # pred_coast = 0.5°C

    kp_auto, _, _, fb_auto = rt.get_effective_pid_params(20.0, 0.5, 0.6)
    assert fb_auto is None
    assert 10.0 <= kp_auto <= 30.0  # ±50% drift clamping around 20.0
    assert rt.get_early_stop_prediction() > 0.2

    # switch_pwm early-stop: error = 24.2 - 24.0 = 0.2 <= pred_coast (0.5) -> stops PWM
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    await entity.async_added_to_hass()
    await entity.async_set_hvac_mode(HVACMode.COOL)
    assert entity._pwm_state is False

    # 5-cycle >30% degradation triggers auto rollback + persistent notification
    for _ in range(4):
        assert rt.record_cycle_performance(cycle_mae=0.8, cycle_overshoot=1.0) is False
    assert rt.record_cycle_performance(cycle_mae=0.8, cycle_overshoot=1.0) is True
    assert rt.fallback_reason == "auto_rollback_performance_degraded"
    import homeassistant.components.persistent_notification as pn
    assert len(pn._notifications) == 1

    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_manual_override_context_and_safety_watchdog(hass) -> None:
    """Verify external context triggers 30-min manual override and watchdog enforces 15-min rest."""
    hass.states.async_set("sensor.room_temp", "27.0")
    hass.states.async_set(
        "climate.ac_slave",
        "cool",
        {"hvac_modes": ["off", "cool"], "fan_modes": ["low", "high"], "min_temp": 18, "max_temp": 30},
    )

    entry = ConfigEntry(
        entry_id="wd_test",
        title="Watchdog",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_CLIMATE: "climate.ac_slave",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_MANUAL_OVERRIDE_MIN: 30,
            CONF_MAX_RUN_MIN: 120,
            CONF_SAFETY_REST_MIN: 15,
        },
        version=2,
    )
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    await entity.async_added_to_hass()
    await entity.async_set_hvac_mode(HVACMode.COOL)

    # Simulate external user manipulating slave climate
    hass.states.async_set(
        "climate.ac_slave",
        "fan_only",
        {"hvac_modes": ["off", "cool", "fan_only"]},
        context=Context(id="ext_1", user_id="user_123"),
    )
    assert entity._manual_override_until is not None

    # Advance 31 minutes -> manual override expires
    t1 = dt_util.utcnow() + timedelta(minutes=31)
    dt_util.set_utcnow(t1)
    await entity._async_calculate_and_control(now=t1, force=False)
    assert entity._manual_override_until is None

    # Watchdog: simulate 121 min continuous run -> forces Section 0 rest for 15 min
    entity._continuous_run_start = t1 - timedelta(minutes=121)
    t2 = t1 + timedelta(minutes=1)
    dt_util.set_utcnow(t2)
    await entity._async_calculate_and_control(now=t2, force=False)
    assert entity._watchdog_rest_until is not None
    assert entity._active_section == 0


@pytest.mark.asyncio
async def test_cross_coupling_and_dual_deadband(hass) -> None:
    """Verify dehumidifier u_aux_heat, AC cooling interlock, and 2.0°C dual deadband."""
    hass.states.async_set("sensor.room_temp", "24.5")
    hass.states.async_set("humidifier.dehum", "on")
    hass.states.async_set("climate.main_ac", "cool", {"hvac_action": "cooling"})

    # 1. Temperature entity with coupled dehumidifier -> u_aux_heat = 1.0 in RLS block
    ac_entry = ConfigEntry(
        entry_id="ac_coupled",
        title="AC Coupled",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_CLIMATE: "climate.main_ac",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_COUPLED_DEHUMIDIFIER: "humidifier.dehum",
        },
        version=2,
    )
    await async_setup_entry(hass, ac_entry)
    rt_ac: SmartPIDRuntime = ac_entry.runtime_data
    assert rt_ac._is_coupled_dehumidifier_on() == 1.0

    # Dual deadband: target=24.0, half_db=1.0 -> at 24.5°C while cooling, stays cooling;
    # doesn't flip to heating until < 23.0°C
    e_dual = SmartPIDThermostatEntity(hass, ac_entry)
    e_dual.hass = hass
    e_dual._is_heating = False
    e_dual._attr_hvac_mode = HVACMode.AUTO
    await e_dual._async_calculate_and_control(force=True)
    assert e_dual._is_heating is False

    # 2. Dehumidifier entity with coupled AC cooling -> inhibits dehumidifier
    hass.states.async_set("sensor.room_hum", "65.0")
    hum_entry = ConfigEntry(
        entry_id="hum_coupled",
        title="Hum Coupled",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_INDOOR_SENSOR: "sensor.room_hum",
            CONF_TARGET_DEVICE: "humidifier.dehum",
            CONF_INITIAL_TARGET_TEMP: 50.0,
            CONF_COUPLED_AC: "climate.main_ac",
        },
        version=2,
    )
    await async_setup_entry(hass, hum_entry)
    h_ent = SmartPIDHumidifierEntity(hass, hum_entry)
    h_ent.hass = hass
    h_ent._is_on = True
    await h_ent.async_added_to_hass()
    assert hum_entry.runtime_data.is_coupled_ac_cooling() is True
    assert h_ent._pwm_state is False

    await async_unload_entry(hass, ac_entry)
    await async_unload_entry(hass, hum_entry)


@pytest.mark.asyncio
async def test_recorder_bootstrap_mock_and_graceful_fallback(hass) -> None:
    """Verify optional Recorder bootstrap via get_instance().async_add_executor_job and graceful skip."""
    t0 = dt_util.utcnow() - timedelta(hours=1)
    mock_states = {
        "sensor.room_temp": [
            State("sensor.room_temp", "25.0", last_changed=t0),
            State("sensor.room_temp", "24.8", last_changed=t0 + timedelta(minutes=5)),
            State("sensor.room_temp", "24.6", last_changed=t0 + timedelta(minutes=10)),
        ]
    }
    entry = ConfigEntry(
        entry_id="rec_test",
        title="Recorder Test",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_RECORDER_BOOTSTRAP: True,
        },
        version=2,
    )

    with patch(
        "homeassistant.components.recorder.history.get_significant_states",
        return_value=mock_states,
    ):
        await async_setup_entry(hass, entry)
        await hass.async_block_till_done()
        rt: SmartPIDRuntime = entry.runtime_data
        assert rt._active_rls().sample_count >= 2
        await async_unload_entry(hass, entry)
