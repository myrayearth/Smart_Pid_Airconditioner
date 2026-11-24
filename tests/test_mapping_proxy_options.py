"""Regression tests verifying ConfigEntry MappingProxyType handling and OptionsFlow defaults."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest
import voluptuous as vol

from custom_components.smart_pid_airconditioner import async_setup_entry, async_unload_entry
from custom_components.smart_pid_airconditioner.config_flow import SmartPIDThermostatOptionsFlow
from custom_components.smart_pid_airconditioner.config_utils import get_entry_setting
from custom_components.smart_pid_airconditioner.const import (
    CONF_AUX_TEMP_SENSOR,
    CONF_CONTROL_MODE,
    CONF_COUPLED_DEHUMIDIFIER,
    CONF_INDOOR_SENSOR,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_OUTDOOR_SENSOR,
    CONF_PWM_DURATION,
    CONF_SEC0_ACTION,
    CONF_SEC1_DELTA,
    CONF_SEC1_MAX,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_TEMP_STEP,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONF_VENT_MODE,
    CONF_VENT_SENSORS,
    CONF_VENT_SWITCH,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    SEC0_ACTION_OFF,
    THERMOSTAT_TYPE_HEATER,
    TUNING_MODE_AUTO,
    VENT_MODE_SENSORS,
    duration_to_seconds,
    resolve_thermostat_type,
)
from custom_components.smart_pid_airconditioner.switch import (
    SmartPIDSunriseSavingSwitch,
    async_setup_entry as async_setup_switch,
)
from custom_components.smart_pid_airconditioner.time import (
    SmartPIDSunriseSavingEndTime,
    SmartPIDSunriseSavingStartTime,
    async_setup_entry as async_setup_time,
)
from homeassistant.config_entries import ConfigEntry


def _find_marker(schema_obj: vol.Schema, field_name: str) -> vol.Marker:
    for marker in schema_obj.schema:
        if getattr(marker, "schema", marker) == field_name:
            return marker
    raise AssertionError(f"Field '{field_name}' not found in schema: {list(schema_obj.schema)}")


def _marker_default(marker: vol.Marker) -> Any:
    if marker.default is vol.UNDEFINED:
        return vol.UNDEFINED
    return marker.default() if callable(marker.default) else marker.default


def test_mapping_proxy_get_entry_setting_and_resolve_thermostat_type() -> None:
    """get_entry_setting and resolve_thermostat_type work on nested MappingProxyType structures."""
    entry = ConfigEntry(
        entry_id="mp_test_1",
        title="MappingProxy Boiler",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_TARGET_DEVICE: "switch.heat",
            CONF_INDOOR_SENSOR: "sensor.living_temp",
            CONF_KP: 25.0,
            "ki_group": MappingProxyType({CONF_KI: 0.4, CONF_MAX_I: 18.0}),
            "adaptive_learning": MappingProxyType({CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER}),
            "section_0": MappingProxyType({CONF_SEC0_ACTION: SEC0_ACTION_OFF}),
            "section_1": MappingProxyType({CONF_SEC1_MAX: 35.0, CONF_SEC1_DELTA: 1.5}),
        },
        options={
            CONF_KP: 32.0,
            "ventilation": MappingProxyType(
                {CONF_VENT_MODE: VENT_MODE_SENSORS, CONF_VENT_SENSORS: ["binary_sensor.win1"]}
            ),
        },
        version=2,
    )

    assert isinstance(entry.data, MappingProxyType)
    assert isinstance(entry.options, MappingProxyType)
    assert resolve_thermostat_type(entry) == THERMOSTAT_TYPE_HEATER

    # options overrides data
    assert get_entry_setting(entry, CONF_KP) == 32.0
    # data top-level fallback
    assert get_entry_setting(entry, CONF_TARGET_DEVICE) == "switch.heat"
    # nested MappingProxyType sections
    assert get_entry_setting(entry, CONF_KI) == 0.4
    assert get_entry_setting(entry, CONF_MAX_I) == 18.0
    assert get_entry_setting(entry, CONF_VENT_MODE) == VENT_MODE_SENSORS
    assert get_entry_setting(entry, CONF_VENT_SENSORS) == ["binary_sensor.win1"]
    assert get_entry_setting(entry, CONF_SEC0_ACTION) == SEC0_ACTION_OFF
    assert get_entry_setting(entry, CONF_SEC1_MAX) == 35.0
    assert get_entry_setting(entry, CONF_SEC1_DELTA) == 1.5
    # duration_to_seconds accepts MappingProxyType
    assert duration_to_seconds(MappingProxyType({"hours": 0, "minutes": 15, "seconds": 30})) == 930


@pytest.mark.asyncio
async def test_heater_sunrise_entities_load_with_mapping_proxy_entry(hass) -> None:
    """Heater ConfigEntry with MappingProxyType loads Sunrise Saving switch & time entities."""
    entry = ConfigEntry(
        entry_id="mp_heater_sunrise",
        title="Heater Sunrise",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_TARGET_DEVICE: "switch.heat",
            CONF_INDOOR_SENSOR: "sensor.living_temp",
        },
        version=2,
    )
    assert await async_setup_entry(hass, entry) is True
    assert entry.runtime_data.thermostat_type == THERMOSTAT_TYPE_HEATER

    switches: list = []
    times: list = []
    await async_setup_switch(hass, entry, lambda ents, _=False: switches.extend(ents))
    await async_setup_time(hass, entry, lambda ents, _=False: times.extend(ents))

    assert any(isinstance(s, SmartPIDSunriseSavingSwitch) for s in switches)
    assert any(isinstance(t, SmartPIDSunriseSavingStartTime) for t in times)
    assert any(isinstance(t, SmartPIDSunriseSavingEndTime) for t in times)
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_options_flow_populates_defaults_and_omits_empty_optional_entities(hass) -> None:
    """OptionsFlow step 1 (init) & step 2 (pwm / climate) prefill values from MappingProxyType."""
    pwm_entry = ConfigEntry(
        entry_id="mp_opt_pwm",
        title="PWM Heater Opts",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_TARGET_DEVICE: "switch.heat",
            CONF_INDOOR_SENSOR: "sensor.living_temp",
            CONF_OUTDOOR_SENSOR: "",
            CONF_TEMP_STEP: 0.5,  # numeric float stored in legacy entries
            CONF_KP: 18.5,
            CONF_KI: 0.35,
            CONF_MAX_I: 25.0,
            CONF_KD: 4.5,
            CONF_KE: 0.7,
            CONF_PWM_DURATION: 600,
            CONF_MIN_ON_TIME: 120,
            CONF_MIN_OFF_TIME: 240,
            CONF_VENT_MODE: VENT_MODE_SENSORS,
            CONF_VENT_SENSORS: ["binary_sensor.door_1"],
            CONF_VENT_SWITCH: None,
            CONF_TUNING_MODE: TUNING_MODE_AUTO,
            CONF_COUPLED_DEHUMIDIFIER: "",
        },
        version=2,
    )

    flow = SmartPIDThermostatOptionsFlow()
    flow.hass = hass
    flow._config_entry_internal = pwm_entry

    res_init = await flow.async_step_init()
    assert res_init["type"] == "form"
    assert res_init["step_id"] == "init"
    s1: vol.Schema = res_init["data_schema"]

    assert _marker_default(_find_marker(s1, CONF_CONTROL_MODE)) == CONTROL_MODE_SWITCH_PWM
    assert _marker_default(_find_marker(s1, CONF_THERMOSTAT_TYPE)) == THERMOSTAT_TYPE_HEATER
    assert _marker_default(_find_marker(s1, CONF_TARGET_DEVICE)) == "switch.heat"
    assert _marker_default(_find_marker(s1, CONF_INDOOR_SENSOR)) == "sensor.living_temp"
    assert _marker_default(_find_marker(s1, CONF_TEMP_STEP)) == "0.5"
    assert _marker_default(_find_marker(s1, CONF_KP)) == 18.5
    assert _marker_default(_find_marker(s1, "ki_group")) == {CONF_KI: 0.35, CONF_MAX_I: 25.0}
    assert _marker_default(_find_marker(s1, CONF_KD)) == 4.5
    assert _marker_default(_find_marker(s1, CONF_KE)) == 0.7

    # Empty outdoor_sensor must NOT emit suggested_value=None or ""
    outdoor_marker = _find_marker(s1, CONF_OUTDOOR_SENSOR)
    assert _marker_default(outdoor_marker) is vol.UNDEFINED
    assert not (outdoor_marker.description or {}).get("suggested_value")

    # Nested ventilation & adaptive_learning sections prefilled, empty optional entities omitted
    vent_schema: vol.Schema = s1.schema["ventilation"]
    assert _marker_default(_find_marker(vent_schema, CONF_VENT_MODE)) == VENT_MODE_SENSORS
    assert _marker_default(_find_marker(vent_schema, CONF_VENT_SENSORS)) == ["binary_sensor.door_1"]
    vent_sw_marker = _find_marker(vent_schema, CONF_VENT_SWITCH)
    assert not (vent_sw_marker.description or {}).get("suggested_value")

    al_schema: vol.Schema = s1.schema["adaptive_learning"]
    assert _marker_default(_find_marker(al_schema, CONF_TUNING_MODE)) == TUNING_MODE_AUTO
    coupled_marker = _find_marker(al_schema, CONF_COUPLED_DEHUMIDIFIER)
    assert not (coupled_marker.description or {}).get("suggested_value")

    # PWM step 2 prefilled
    res_pwm = await flow.async_step_sections_pwm()
    assert res_pwm["type"] == "form"
    assert res_pwm["step_id"] == "sections_pwm"
    s2_pwm: vol.Schema = res_pwm["data_schema"]
    assert _marker_default(_find_marker(s2_pwm, CONF_PWM_DURATION)) == {
        "hours": 0,
        "minutes": 10,
        "seconds": 0,
    }
    assert _marker_default(_find_marker(s2_pwm, CONF_MIN_ON_TIME)) == {
        "hours": 0,
        "minutes": 2,
        "seconds": 0,
    }
    assert _marker_default(_find_marker(s2_pwm, CONF_MIN_OFF_TIME)) == {
        "hours": 0,
        "minutes": 4,
        "seconds": 0,
    }

    # Climate Delta step 1 & step 2 prefilled (including non-empty outdoor_sensor)
    clim_entry = ConfigEntry(
        entry_id="mp_opt_clim",
        title="Climate Heater Opts",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_CLIMATE_DELTA,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_TARGET_DEVICE: "climate.boilreo",
            CONF_TARGET_CLIMATE: "climate.boilreo",
            CONF_INDOOR_SENSOR: "sensor.living_temp",
            CONF_OUTDOOR_SENSOR: "sensor.out_temp",
            CONF_SEC1_MAX: 28.0,
            CONF_SEC1_DELTA: 1.5,
        },
        options={
            "section_0": MappingProxyType({CONF_SEC0_ACTION: SEC0_ACTION_OFF}),
        },
        version=2,
    )
    clim_flow = SmartPIDThermostatOptionsFlow()
    clim_flow.hass = hass
    clim_flow._config_entry_internal = clim_entry

    res_c1 = await clim_flow.async_step_init()
    c1_out_marker = _find_marker(res_c1["data_schema"], CONF_OUTDOOR_SENSOR)
    assert c1_out_marker.description == {"suggested_value": "sensor.out_temp"}

    res_c2 = await clim_flow.async_step_sections_climate()
    assert res_c2["step_id"] == "sections_climate"
    s2_clim: vol.Schema = res_c2["data_schema"]
    assert _marker_default(_find_marker(s2_clim.schema["section_0"], CONF_SEC0_ACTION)) == SEC0_ACTION_OFF
    assert _marker_default(_find_marker(s2_clim.schema["section_1"], CONF_SEC1_MAX)) == 28.0
    assert _marker_default(_find_marker(s2_clim.schema["section_1"], CONF_SEC1_DELTA)) == 1.5
