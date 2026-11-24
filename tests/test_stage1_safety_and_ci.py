"""Stage 1 safety, HA standard compliance, and ownership unit tests."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
import homeassistant.util.dt as dt_util

from custom_components.smart_pid_airconditioner import (
    CONFIG_SCHEMA,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.actuator import SwitchCommandGuard
from custom_components.smart_pid_airconditioner.button import SmartPIDResetLearningButton
from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.config_flow import SmartPIDThermostatConfigFlow
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_INDOOR_SENSOR,
    CONF_INITIAL_TARGET_TEMP,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    DOMAIN,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_HEATER,
)
from custom_components.smart_pid_airconditioner.storage import ModelStore
from custom_components.smart_pid_airconditioner.ventilation import VentState, VentStatus


@pytest.mark.asyncio
async def test_asymmetric_off_preemption_blocks_turn_on_while_off_pending(hass) -> None:
    """1) async_turn_on() returns False without canceling pending OFF confirmation."""
    hass.states.async_set("switch.boiler", STATE_ON)
    guard = SwitchCommandGuard(hass, confirm_timeout=0.3, off_retry_delays=(0.05,))

    off_task = asyncio.create_task(guard.async_turn_off("switch.boiler", confirm=True))
    await asyncio.sleep(0.03)
    assert "switch.boiler" in guard._pending_off

    # Attempt turn_on while OFF is still pending -> MUST return False and NOT cancel OFF
    on_res = await guard.async_turn_on("switch.boiler", confirm=False)
    assert on_res is False
    assert "switch.boiler" in guard._pending_off

    # Confirm OFF state -> OFF completes and releases _pending_off
    hass.states.async_set("switch.boiler", STATE_OFF)
    off_res = await off_task
    assert off_res is True
    assert "switch.boiler" not in guard._pending_off

    # Now subsequent turn_on succeeds
    on_after = await guard.async_turn_on("switch.boiler", confirm=False)
    assert on_after is True


@pytest.mark.asyncio
async def test_reset_learning_button_guarded_while_device_active(hass) -> None:
    """2) Reset button is unavailable while active and raises ServiceValidationError on forced press."""
    entry = ConfigEntry(
        entry_id="btn_guard_1",
        title="Boiler Guard",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_INDOOR_SENSOR: "sensor.temp",
            CONF_TARGET_DEVICE: "switch.boiler_guard",
        },
        version=2,
    )
    await async_setup_entry(hass, entry)
    rt = entry.runtime_data
    btn = SmartPIDResetLearningButton(entry, rt)
    btn.hass = hass
    await btn.async_added_to_hass()

    # Idle -> available True
    assert rt.can_reset_learning is True
    assert btn.available is True

    # Device becomes active -> available False & press raises ServiceValidationError
    rt.update_observation(
        indoor_val=20.0,
        outdoor_val=5.0,
        target_val=22.0,
        u_effort=1.0,
        is_active=True,
        is_heating=True,
        vent_status=VentStatus(VentState.NORMAL, False, False, 0, False),
    )
    assert rt.can_reset_learning is False
    assert btn.available is False
    with pytest.raises(ServiceValidationError):
        await btn.async_press()

    # Pending command also blocks reset
    rt.update_observation(
        indoor_val=20.0,
        outdoor_val=5.0,
        target_val=22.0,
        u_effort=0.0,
        is_active=False,
        is_heating=True,
        vent_status=VentStatus(VentState.NORMAL, False, False, 0, False),
    )
    rt.set_pending_command(True)
    assert btn.available is False
    rt.set_pending_command(False)
    assert btn.available is True
    await btn.async_press()

    await btn.async_will_remove_from_hass()
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_sensor_recovery_requires_two_readings_30s_apart(hass) -> None:
    """3) Sensor fault recovery holds fault on <30s report and clears after 2nd report >= 30s."""
    t0 = dt_util.utcnow()
    hass.states.async_set("sensor.room_temp", "24.0")
    entry = ConfigEntry(
        entry_id="sens_rec_1",
        title="Sensor Rec",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_CLIMATE: "climate.ac_rec",
            CONF_INITIAL_TARGET_TEMP: 24.0,
        },
        version=2,
    )
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass

    # Normal initial boot -> immediate valid reading
    entity._update_sensor_values()
    assert entity._indoor_temp == 24.0
    assert entity._sensor_fault_active is False

    # Sensor goes unavailable -> fault active
    t1 = t0 + timedelta(seconds=60)
    dt_util.set_utcnow(t1)
    hass.states.async_set("sensor.room_temp", STATE_UNAVAILABLE)
    entity._update_sensor_values()
    assert entity._sensor_fault_active is True
    assert entity._indoor_temp is None

    # 1st valid report after fault -> still fault active (records recovery_first_at)
    t2 = t1 + timedelta(seconds=10)
    dt_util.set_utcnow(t2)
    hass.states.async_set("sensor.room_temp", "24.5")
    entity._update_sensor_values()
    assert entity._sensor_fault_active is True
    assert entity._indoor_temp is None

    # 2nd valid report < 30s later (15s) -> still fault active
    t3 = t2 + timedelta(seconds=15)
    dt_util.set_utcnow(t3)
    hass.states.async_set("sensor.room_temp", "24.6")
    entity._update_sensor_values()
    assert entity._sensor_fault_active is True
    assert entity._indoor_temp is None

    # Valid report >= 30s after 1st recovery report -> fault cleared!
    t4 = t2 + timedelta(seconds=31)
    dt_util.set_utcnow(t4)
    hass.states.async_set("sensor.room_temp", "24.7")
    entity._update_sensor_values()
    assert entity._sensor_fault_active is False
    assert entity._indoor_temp == 24.7


@pytest.mark.asyncio
async def test_duplicate_device_ownership_and_async_remove_entry(hass) -> None:
    """4) Block duplicate device ownership in config_flow & __init__, and remove ModelStore on delete."""
    assert callable(CONFIG_SCHEMA)

    entry1 = ConfigEntry(
        entry_id="owner_1",
        title="Owner 1",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_DEVICE: "climate.shared_ac",
            CONF_TARGET_CLIMATE: "climate.shared_ac",
        },
        version=2,
    )
    hass.config_entries.entries[entry1.entry_id] = entry1
    await async_setup_entry(hass, entry1)

    # ConfigFlow rejects duplicate target device
    cf = SmartPIDThermostatConfigFlow()
    cf.hass = hass
    await cf.async_step_user({CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA, CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER})
    res = await cf.async_step_device_sensor(
        {
            CONF_TARGET_DEVICE: "climate.shared_ac",
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_MIN_TEMP: 16.0,
            CONF_MAX_TEMP: 30.0,
        }
    )
    assert res["type"] == "form"
    assert res["errors"].get("base") == "duplicate_target_device"

    # __init__ async_setup_entry rejects duplicate owner with ConfigEntryError
    entry2 = ConfigEntry(
        entry_id="owner_2",
        title="Owner 2",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_DEVICE: "climate.shared_ac",
        },
        version=2,
    )
    with pytest.raises(ConfigEntryError):
        await async_setup_entry(hass, entry2)

    # Persist storage data for owner_1, unload, then remove entry -> store key deleted
    store = ModelStore(hass, entry1.entry_id)
    await store.async_save(entry1.runtime_data.snapshot)
    assert f"{DOMAIN}.{entry1.entry_id}" in store._store._memory_backing

    await async_unload_entry(hass, entry1)
    assert "climate.shared_ac" not in hass.data[DOMAIN]["owners"]

    await async_remove_entry(hass, entry1)
    assert f"{DOMAIN}.{entry1.entry_id}" not in store._store._memory_backing
