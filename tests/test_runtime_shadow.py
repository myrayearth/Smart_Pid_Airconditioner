"""Stage 4 integration tests: SmartPIDRuntime, shadow_learn, diagnostic sensors, and reset_learning."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
import homeassistant.util.dt as dt_util

from custom_components.smart_pid_airconditioner import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.const import (
    CONF_AUX_TEMP_SENSOR,
    CONF_CONTROL_MODE,
    CONF_INDOOR_SENSOR,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_OUTDOOR_SENSOR,
    CONF_SAMPLE_PERIOD,
    CONF_TARGET_CLIMATE,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONF_VENT_DEBOUNCE_SEC,
    CONF_VENT_MODE,
    CONF_VENT_SENSORS,
    CONTROL_MODE_CLIMATE_DELTA,
    DOMAIN,
    SERVICE_RESET_LEARNING,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    TUNING_MODE_MANUAL,
    TUNING_MODE_SHADOW,
    VENT_MODE_SENSORS,
)
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.sensor import (
    SmartPIDConfidenceSensor,
    SmartPIDCyclePhaseSensor,
    SmartPIDPredictedCoastSensor,
    SmartPIDRecommendedTuningSensor,
    SmartPIDVentilationStateSensor,
)


@pytest.mark.asyncio
async def test_shadow_learn_matches_manual_pid_100_percent(hass) -> None:
    """Verify shadow_learn dispatches identical control commands to manual_pid while updating diagnostics."""
    hass.states.async_set("sensor.room_temp", "26.0")
    hass.states.async_set("sensor.out_temp", "31.0")
    hass.states.async_set(
        "climate.ac_slave",
        "cool",
        {"hvac_modes": ["off", "cool"], "fan_modes": ["low", "medium", "high"], "min_temp": 18, "max_temp": 30},
    )

    base_data = {
        CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
        CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
        CONF_INDOOR_SENSOR: "sensor.room_temp",
        CONF_OUTDOOR_SENSOR: "sensor.out_temp",
        CONF_TARGET_CLIMATE: "climate.ac_slave",
        CONF_INITIAL_TARGET_TEMP: 24.0,
        CONF_KP: 20.0,
        CONF_KI: 0.6,
        CONF_MAX_I: 20.0,
        CONF_KD: 5.0,
        CONF_KE: 0.5,
        CONF_SAMPLE_PERIOD: 60,
    }

    manual_entry = ConfigEntry(
        entry_id="manual_e",
        title="Manual",
        data={**base_data, CONF_TUNING_MODE: TUNING_MODE_MANUAL},
        version=2,
    )
    shadow_entry = ConfigEntry(
        entry_id="shadow_e",
        title="Shadow",
        data={**base_data, CONF_TUNING_MODE: TUNING_MODE_SHADOW},
        version=2,
    )

    await async_setup_entry(hass, manual_entry)
    await async_setup_entry(hass, shadow_entry)

    e_man = SmartPIDThermostatEntity(hass, manual_entry)
    e_man.hass = hass
    e_shd = SmartPIDThermostatEntity(hass, shadow_entry)
    e_shd.hass = hass

    await e_man.async_added_to_hass()
    await e_shd.async_added_to_hass()
    await e_man.async_set_hvac_mode(HVACMode.COOL)
    await e_shd.async_set_hvac_mode(HVACMode.COOL)

    assert e_shd._pid_output == e_man._pid_output
    assert e_shd._dispatched_temperature == e_man._dispatched_temperature
    assert e_shd._active_section == e_man._active_section

    # Advance 5 minutes of learning ticks in shadow_learn
    rt: SmartPIDRuntime = shadow_entry.runtime_data
    for _ in range(5):
        await rt.async_step_learning()

    assert rt._active_rls().sample_count == 1
    assert len(rt.store._store.delay_save_calls) >= 1

    # Diagnostic sensors reflect runtime state & have EntityCategory.DIAGNOSTIC
    s_conf = SmartPIDConfidenceSensor(shadow_entry, rt)
    s_phase = SmartPIDCyclePhaseSensor(shadow_entry, rt)
    s_coast = SmartPIDPredictedCoastSensor(shadow_entry, rt)
    s_rec = SmartPIDRecommendedTuningSensor(shadow_entry, rt)
    s_vent = SmartPIDVentilationStateSensor(shadow_entry, rt)

    for s in (s_conf, s_phase, s_coast, s_rec, s_vent):
        assert s.entity_category == EntityCategory.DIAGNOSTIC
        assert (DOMAIN, "shadow_e") in s.device_info.identifiers

    assert s_conf.extra_state_attributes["rls_samples"] == 1
    assert s_rec.native_value > 0.0

    # Reset learning service
    await hass.services.async_call(
        DOMAIN, SERVICE_RESET_LEARNING, {"entry_id": "shadow_e"}, blocking=True
    )
    assert rt._active_rls().sample_count == 0

    await async_unload_entry(hass, manual_entry)
    await async_unload_entry(hass, shadow_entry)


@pytest.mark.asyncio
async def test_humidity_aux_temp_fallback_and_vent_block(hass) -> None:
    """Verify AH conversion with aux_temp_sensor, RH fallback when unavailable, and block contamination."""
    hass.states.async_set("sensor.room_hum", "60.0")
    hass.states.async_set("sensor.aux_temp", "24.0")
    hass.states.async_set("binary_sensor.win", "off")

    entry = ConfigEntry(
        entry_id="hum_rt",
        title="Hum RT",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_INDOOR_SENSOR: "sensor.room_hum",
            CONF_AUX_TEMP_SENSOR: "sensor.aux_temp",
            CONF_TUNING_MODE: TUNING_MODE_SHADOW,
            CONF_VENT_MODE: VENT_MODE_SENSORS,
            CONF_VENT_SENSORS: ["binary_sensor.win"],
            CONF_VENT_DEBOUNCE_SEC: 0,
        },
        version=2,
    )
    await async_setup_entry(hass, entry)
    rt: SmartPIDRuntime = entry.runtime_data

    from custom_components.smart_pid_airconditioner.ventilation import VentStatus, VentState
    rt.update_observation(
        indoor_val=60.0,
        outdoor_val=None,
        target_val=50.0,
        u_effort=0.5,
        is_active=True,
        is_heating=False,
        vent_status=VentStatus(VentState.NORMAL, False, False, 0, False),
    )
    # Valid aux temp -> AH mode, no fallback_reason
    val_in, _, fb = rt._resolve_learning_values()
    assert fb is None
    assert 12.0 < val_in < 14.0

    # Unavailable aux temp -> RH% fallback
    hass.states.async_set("sensor.aux_temp", "unavailable")
    val_in_fb, _, fb2 = rt._resolve_learning_values()
    assert val_in_fb == 60.0
    assert fb2 == "aux_temp_unavailable_rh_fallback"

    # Ventilation block contaminates 5-min RLS block
    rt.update_observation(
        indoor_val=60.0,
        outdoor_val=None,
        target_val=50.0,
        u_effort=0.5,
        is_active=True,
        is_heating=False,
        vent_status=VentStatus(VentState.VENTILATING, True, True, 0, False),
    )
    for _ in range(5):
        await rt.async_step_learning()
    assert rt._active_rls().sample_count == 0

    await async_unload_entry(hass, entry)
