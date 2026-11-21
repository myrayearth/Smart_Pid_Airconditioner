"""Stage 1 unit & regression tests: PID stability, bug fixes, OptionsFlowWithReload, ExtraStoredData."""

from __future__ import annotations

from datetime import timedelta
import warnings

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
import homeassistant.util.dt as dt_util

from custom_components.smart_pid_airconditioner import (
    async_migrate_entry,
    async_setup_entry,
)
from custom_components.smart_pid_airconditioner.climate import (
    SmartPIDExtraStoredData,
    SmartPIDThermostatEntity,
)
from custom_components.smart_pid_airconditioner.config_flow import (
    SmartPIDThermostatConfigFlow,
    SmartPIDThermostatOptionsFlow,
)
from custom_components.smart_pid_airconditioner.const import (
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
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
)
from custom_components.smart_pid_airconditioner.humidifier import SmartPIDHumidifierEntity


def _make_climate_entry(
    *,
    control_mode: str = CONTROL_MODE_CLIMATE_DELTA,
    thermostat_type: str = THERMOSTAT_TYPE_COOLER,
    extra_data: dict | None = None,
) -> ConfigEntry:
    data = {
        CONF_CONTROL_MODE: control_mode,
        CONF_THERMOSTAT_TYPE: thermostat_type,
        CONF_INDOOR_SENSOR: "sensor.room_temp",
        CONF_OUTDOOR_SENSOR: "sensor.out_temp",
        CONF_TARGET_CLIMATE: "climate.ac_slave",
        CONF_TARGET_DEVICE: "switch.heater_plug",
        CONF_INITIAL_TARGET_TEMP: 24.0,
        CONF_KP: 20.0,
        CONF_KI: 0.6,
        CONF_MAX_I: 20.0,
        CONF_KD: 5.0,
        CONF_KE: 0.5,
        CONF_SAMPLE_PERIOD: 60,
    }
    if extra_data:
        data.update(extra_data)
    return ConfigEntry(entry_id="entry_1", title="Test AC", data=data, version=2)


@pytest.mark.asyncio
async def test_climate_delta_regression_and_attr_humidity_fix(hass) -> None:
    """Verify climate_delta control, section mapping, and ATTR_HUMIDITY fix."""
    hass.states.async_set("sensor.room_temp", "26.0")
    hass.states.async_set("sensor.out_temp", "30.0")
    hass.states.async_set(
        "climate.ac_slave",
        "cool",
        {"hvac_modes": ["off", "cool", "dry", "fan_only"], "fan_modes": ["low", "medium", "high"], "min_temp": 18, "max_temp": 30},
    )

    entry = _make_climate_entry()
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    await entity.async_added_to_hass()
    await entity.async_set_hvac_mode(HVACMode.COOL)

    # Error = 26 - 24 = 2.0 -> P=40, E=0.5*(30-24)=3.0 -> output > 40% -> Section 2
    assert entity._active_section == 2
    assert entity._pid_output > 40.0

    # Calling async_set_humidity should not raise NameError for ATTR_HUMIDITY
    await entity.async_set_humidity(55)
    assert entity._target_temp == 25.0 or entity._target_temp == 30.0  # clamped to max_temp 30


@pytest.mark.asyncio
async def test_humidity_type_does_not_overwrite_temp_variables(hass) -> None:
    """Verify is_humidity_type stores values in _indoor_humidity/_outdoor_humidity only."""
    hass.states.async_set("sensor.room_hum", "65.0")
    hass.states.async_set("sensor.out_hum", "75.0")

    entry = _make_climate_entry(thermostat_type=THERMOSTAT_TYPE_DEHUMIDIFIER)
    entry.data[CONF_INDOOR_SENSOR] = "sensor.room_hum"
    entry.data[CONF_OUTDOOR_SENSOR] = "sensor.out_hum"
    entry.data[CONF_INITIAL_TARGET_TEMP] = 50.0

    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    entity._update_sensor_values()

    assert entity._indoor_humidity == 65.0
    assert entity._outdoor_humidity == 75.0
    assert entity._indoor_temp is None
    assert entity._outdoor_temp is None


@pytest.mark.asyncio
async def test_d_term_lpf_and_short_event_spike_blocked(hass) -> None:
    """Verify <10s event updates do not trigger 20x D-term spike and LPF smooths D-term."""
    hass.states.async_set("sensor.room_temp", "25.0")
    entry = _make_climate_entry(extra_data={CONF_KD: 10.0, CONF_KP: 0.0, CONF_KI: 0.0, CONF_KE: 0.0})
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    await entity.async_added_to_hass()
    await entity.async_set_hvac_mode(HVACMode.COOL)

    t0 = dt_util.utcnow()
    # Advance 60s, temp rises +0.5°C -> valid D calculation with LPF alpha=60/(120+60)=1/3
    t1 = t0 + timedelta(seconds=60)
    dt_util.set_utcnow(t1)
    hass.states.async_set("sensor.room_temp", "25.5")
    await entity._async_calculate_and_control(now=t1, force=False)
    d_after_60s = entity._pid_d
    # raw_d = 10 * (0.5 / 1.0) = 5.0; filtered = (1/3)*5.0 = 1.67
    assert 1.5 <= d_after_60s <= 1.8

    # Rapid 2s sensor noise spike +0.5°C -> MUST be blocked (< 10s), D stays at filtered value
    t2 = t1 + timedelta(seconds=2)
    dt_util.set_utcnow(t2)
    hass.states.async_set("sensor.room_temp", "26.0")
    await entity._async_calculate_and_control(now=t2, force=False)
    assert entity._pid_d == d_after_60s


@pytest.mark.asyncio
async def test_i_term_per_minute_and_v1_migration(hass) -> None:
    """Verify I-term uses per-minute dt and v1->v2 migration scales Ki for climate entries."""
    v1_entry = ConfigEntry(
        entry_id="mig_1",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_KI: 0.05,
            "ki_group": {CONF_KI: 0.05, CONF_MAX_I: 20.0},
        },
        options={CONF_KI: 0.02},
        version=1,
    )
    ok = await async_migrate_entry(hass, v1_entry)
    assert ok is True
    assert v1_entry.version == 2
    assert v1_entry.data[CONF_KI] == pytest.approx(3.0)
    assert v1_entry.data["ki_group"][CONF_KI] == pytest.approx(3.0)
    assert v1_entry.options[CONF_KI] == pytest.approx(1.2)

    # Verify per-minute I accumulation: error=1.0, ki=0.6/min, dt=60s -> +0.6
    hass.states.async_set("sensor.room_temp", "25.0")
    entry = _make_climate_entry(extra_data={CONF_KP: 0.0, CONF_KD: 0.0, CONF_KE: 0.0, CONF_KI: 0.6})
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    await entity.async_added_to_hass()
    await entity.async_set_hvac_mode(HVACMode.COOL)
    entity._integral = 0.0
    t_next = dt_util.utcnow() + timedelta(seconds=60)
    dt_util.set_utcnow(t_next)
    await entity._async_calculate_and_control(now=t_next, force=False)
    assert entity._integral == pytest.approx(0.6, abs=0.01)


@pytest.mark.asyncio
async def test_options_flow_with_reload_no_warnings(hass) -> None:
    """Verify OptionsFlowWithReload and async_setup_entry produce zero deprecation warnings."""
    entry = _make_climate_entry()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        await async_setup_entry(hass, entry)
        flow = SmartPIDThermostatConfigFlow.async_get_options_flow(entry)
        assert isinstance(flow, SmartPIDThermostatOptionsFlow)
        assert len(entry._update_listeners) == 0


@pytest.mark.asyncio
async def test_extra_stored_data_restore_climate_and_humidifier(hass) -> None:
    """Verify ExtraStoredData restores switch protection timers, active_section, and integral."""
    t_on = dt_util.utcnow() - timedelta(minutes=3)
    t_off = dt_util.utcnow() - timedelta(minutes=1)
    saved = SmartPIDExtraStoredData(
        last_switch_on_times={"switch.heater_plug": t_on.isoformat()},
        last_switch_off_times={"switch.heater_plug": t_off.isoformat()},
        active_section=3,
        integral=7.5,
    )

    # Climate entity restore
    hass.states.async_set("sensor.room_temp", "25.0")
    c_entry = _make_climate_entry(control_mode=CONTROL_MODE_SWITCH_PWM)
    c_entity = SmartPIDThermostatEntity(hass, c_entry)
    c_entity.hass = hass
    from homeassistant.core import State
    c_entity._mock_last_state = State("climate.test", "cool", {"temperature": 24.0})
    c_entity._mock_last_extra_data = saved
    await c_entity.async_added_to_hass()

    assert "switch.heater_plug" in c_entity._last_switch_on_times
    assert "switch.heater_plug" in c_entity._last_switch_off_times
    assert c_entity._active_section == 3
    assert c_entity._integral >= 7.5

    # Humidifier entity restore
    hass.states.async_set("sensor.room_hum", "53.5")
    h_entry = _make_climate_entry(
        control_mode=CONTROL_MODE_SWITCH_PWM,
        thermostat_type=THERMOSTAT_TYPE_DEHUMIDIFIER,
        extra_data={CONF_INDOOR_SENSOR: "sensor.room_hum", CONF_INITIAL_TARGET_TEMP: 50.0},
    )
    h_entity = SmartPIDHumidifierEntity(hass, h_entry)
    h_entity.hass = hass
    h_entity._mock_last_state = State("humidifier.test", "on", {"humidity": 50})
    h_entity._mock_last_extra_data = saved
    await h_entity.async_added_to_hass()

    assert "switch.heater_plug" in h_entity._last_switch_on_times
    assert "switch.heater_plug" in h_entity._last_switch_off_times
    assert h_entity._active_section == 3
    assert h_entity.extra_restore_state_data.active_section == 3


@pytest.mark.asyncio
async def test_config_flow_and_options_flow_render(hass) -> None:
    """Verify ConfigFlow and OptionsFlow steps render without NameError or missing imports."""
    cf = SmartPIDThermostatConfigFlow()
    cf.hass = hass
    res1 = await cf.async_step_user({"control_mode": CONTROL_MODE_CLIMATE_DELTA, "thermostat_type": THERMOSTAT_TYPE_COOLER})
    assert res1["type"] == "form"
    assert res1["step_id"] == "device_sensor"
    res2 = await cf.async_step_device_sensor()
    assert res2["type"] == "form"
    assert res2["step_id"] == "device_sensor"

    entry = _make_climate_entry()
    of = SmartPIDThermostatConfigFlow.async_get_options_flow(entry)
    of.hass = hass
    of._config_entry_internal = entry
    res3 = await of.async_step_init()
    assert res3["type"] == "form"
    assert res3["step_id"] == "init"


@pytest.mark.asyncio
async def test_climate_entity_uses_native_temperature_properties(hass) -> None:
    """Verify SmartPIDThermostatEntity uses non-deprecated native_* temperature attributes."""
    assert "_attr_native_temperature_unit" in SmartPIDThermostatEntity.__dict__
    assert "native_current_temperature" in SmartPIDThermostatEntity.__dict__
    assert "native_target_temperature" in SmartPIDThermostatEntity.__dict__

    assert "_attr_temperature_unit" not in SmartPIDThermostatEntity.__dict__
    assert "current_temperature" not in SmartPIDThermostatEntity.__dict__
    assert "target_temperature" not in SmartPIDThermostatEntity.__dict__

    hass.states.async_set("sensor.room_temp", "26.5")
    entry = _make_climate_entry()
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    entity._update_sensor_values()

    assert entity._attr_native_temperature_unit == UnitOfTemperature.CELSIUS
    assert entity.native_target_temperature == 24.0
    assert entity.native_current_temperature == 26.5

