"""Stage 7 unit tests: high-inertia boiler accuracy, dynamic min-off, outdoor coast, and sunrise saving."""

from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from unittest.mock import patch

import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.smart_pid_airconditioner import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.climate import (
    SmartPIDExtraStoredData,
    SmartPIDThermostatEntity,
)
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_DYNAMIC_MIN_OFF_MULTIPLIER,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_OUTDOOR_SENSOR,
    CONF_PWM_DURATION,
    CONF_SOLAR_GUARD,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONTROL_MODE_SWITCH_PWM,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_HEATER,
    TUNING_MODE_AUTO,
)
from custom_components.smart_pid_airconditioner.learner import (
    CyclePhase,
    DynamicCoastingLearner,
    INERTIA_FAST_AIR,
    INERTIA_HIGH_INERTIA,
)
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.sensor import SmartPIDPredictedCoastSensor
from custom_components.smart_pid_airconditioner.switch import (
    SmartPIDSunriseSavingSwitch,
    async_setup_entry as async_setup_switch,
)
from custom_components.smart_pid_airconditioner.time import (
    SmartPIDSunriseSavingEndTime,
    SmartPIDSunriseSavingStartTime,
    async_setup_entry as async_setup_time,
)


def test_peak_confirm_float_epsilon_and_low_slope_delayed_heating() -> None:
    """Floating-point 0.1 drop confirms peak; <=0.002 slope delayed heating learns via emitter_charge."""
    # 1. Floating-point epsilon: 23.7 - 23.6 == 0.09999999999999787 < 0.1 without epsilon
    assert (23.7 - 23.6) < 0.1
    learner = DynamicCoastingLearner(
        inertia_profile=INERTIA_HIGH_INERTIA,
        tau_em_min=20.0,
        peak_confirm_delta=0.1,
    )
    # Run active for 10 min
    for i in range(10):
        learner.step_cycle(
            is_active=True, u_effort=1.0, value=22.0 + 0.1 * i, is_heating=True
        )
    # Stop at 23.0, coast to 23.7, then drop 23.7 -> 23.6 -> 23.5
    learner.step_cycle(is_active=False, u_effort=0.0, value=23.0, is_heating=True)
    learner.step_cycle(is_active=False, u_effort=0.0, value=23.7, is_heating=True)
    learner.step_cycle(is_active=False, u_effort=0.0, value=23.6, is_heating=True)
    assert learner._reversal_step == 1
    phase = learner.step_cycle(is_active=False, u_effort=0.0, value=23.5, is_heating=True)
    assert phase == CyclePhase.PASSIVE_DRIFT
    assert learner.accepted_count == 1
    assert learner.last_quality_reason == "accepted"

    # 2. Low-slope (0.0 °C/min) delayed heating in fast_air profile learns safely via emitter_charge
    delayed = DynamicCoastingLearner(
        inertia_profile=INERTIA_FAST_AIR,
        tau_em_min=15.0,
        peak_confirm_delta=0.1,
    )
    for _ in range(8):
        delayed.step_cycle(is_active=True, u_effort=1.0, value=21.0, is_heating=True)
    # Stop with slope == 0.0, then delayed afterglow rises to 21.6 and drops
    delayed.step_cycle(is_active=False, u_effort=0.0, value=21.0, is_heating=True)
    delayed.step_cycle(is_active=False, u_effort=0.0, value=21.6, is_heating=True)
    delayed.step_cycle(is_active=False, u_effort=0.0, value=21.5, is_heating=True)
    delayed.step_cycle(is_active=False, u_effort=0.0, value=21.4, is_heating=True)
    assert delayed.accepted_count == 1
    assert delayed.last_quality_reason == "accepted"
    assert delayed.k_afterglow > 0.0


@pytest.mark.asyncio
async def test_high_coast_variance_abstains_from_early_stop(hass: HomeAssistant) -> None:
    """When coast_confidence < 0.25 due to high variance, early-stop abstains and sets fallback_reason."""
    entry = ConfigEntry(
        entry_id="stage7_variance",
        title="Variance Boiler",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_TARGET_DEVICE: "switch.boiler_var",
            CONF_INDOOR_SENSOR: "sensor.temp_var",
            CONF_INERTIA_PROFILE: INERTIA_HIGH_INERTIA,
            CONF_TUNING_MODE: TUNING_MODE_AUTO,
        },
        options={},
    )
    rt = SmartPIDRuntime(hass, entry)
    rt.snapshot.temp_rls.sample_count = 250
    rt.snapshot.coasting.accepted_count = 12
    rt.snapshot.coasting.emitter_charge = 0.8
    rt.snapshot.coasting.k_afterglow = 1.2
    # Wildly fluctuating obs_coast -> std ≈ 1.45 -> confidence < 0.25
    for val in (0.1, 3.0, 0.1, 3.0, 0.1, 3.0):
        rt.snapshot.coasting.history.append({"obs_coast": val, "outdoor_at_stop": None})

    conf = rt.snapshot.coasting.get_coast_confidence()
    assert conf < 0.25
    pred = rt.get_early_stop_prediction()
    assert pred == 0.0
    assert rt.fallback_reason == "high_coast_variance"


@pytest.mark.asyncio
async def test_dynamic_min_off_and_extra_stored_data_restore(hass: HomeAssistant) -> None:
    """Dynamic min-off scales with last_run_duration_sec and persists across ExtraStoredData restore."""
    entry = ConfigEntry(
        entry_id="stage7_dyn_off",
        title="Boiler DynOff",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_TARGET_DEVICE: "switch.boiler_dyn",
            CONF_INDOOR_SENSOR: "sensor.temp_dyn",
            CONF_PWM_DURATION: 900,
            CONF_MIN_ON_TIME: 120,
            CONF_MIN_OFF_TIME: 180,
            CONF_DYNAMIC_MIN_OFF_MULTIPLIER: 1.5,
        },
        options={},
    )
    async def _sw_on(call):
        hass.states.async_set(call.data["entity_id"], "on")

    async def _sw_off(call):
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.async_register("switch", "turn_on", _sw_on)
    hass.services.async_register("switch", "turn_off", _sw_off)
    hass.states.async_set("switch.boiler_dyn", "off")
    hass.states.async_set("sensor.temp_dyn", "20.0")
    entity = SmartPIDThermostatEntity(hass, entry)

    t0 = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_off = t0 + timedelta(seconds=600)  # Ran 600s -> dynamic min-off = 600 * 1.5 = 900s
    t_early_on = t_off + timedelta(seconds=400)  # 400s < 900s -> must be blocked

    with patch("custom_components.smart_pid_airconditioner.climate.dt_util.utcnow", return_value=t0):
        await entity._async_turn_on_switch(force=True)

    with patch("custom_components.smart_pid_airconditioner.climate.dt_util.utcnow", return_value=t_off):
        await entity._async_turn_off_switch(force=True)
    assert entity._last_run_duration_sec == pytest.approx(600.0)

    # ExtraStoredData round-trip preserves last_run_duration_sec
    extra = entity.extra_restore_state_data
    restored = SmartPIDExtraStoredData.from_dict(extra.as_dict())
    assert restored is not None
    assert restored.last_run_duration_sec == pytest.approx(600.0)

    # Attempting to turn ON at +400s should be delayed (blocked) because 400s < 900s
    with patch("custom_components.smart_pid_airconditioner.climate.dt_util.utcnow", return_value=t_early_on):
        await entity._async_turn_on_switch(force=False)
    assert entity._pwm_state is False
    if entity._pwm_turn_on_cancel is not None:
        entity._pwm_turn_on_cancel()


@pytest.mark.asyncio
async def test_outdoor_band_coast_correction_and_sensor_attributes(hass: HomeAssistant) -> None:
    """5°C outdoor bands apply clamped [-0.3, +0.3]°C correction and expose diagnostic attributes."""
    entry = ConfigEntry(
        entry_id="stage7_outdoor",
        title="Outdoor Boiler",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_TARGET_DEVICE: "switch.boiler_out",
            CONF_INDOOR_SENSOR: "sensor.temp_out_in",
            CONF_OUTDOOR_SENSOR: "sensor.temp_out_ext",
            CONF_INERTIA_PROFILE: INERTIA_HIGH_INERTIA,
        },
        options={},
    )
    rt = SmartPIDRuntime(hass, entry)
    c = rt.snapshot.coasting
    c.emitter_charge = 0.5
    c.k_afterglow = 1.0  # base_pred = 0.5

    # Missing outdoor temp
    rt._obs_outdoor = None
    assert rt.get_outdoor_coast_status() == (0.0, "missing")

    # Populate history: cold band [-5, 0) has lower coast (0.3), mild band [10, 15) has higher coast (1.1)
    for _ in range(3):
        c.history.append({"obs_coast": 0.3, "outdoor_at_stop": -2.0})
    for _ in range(3):
        c.history.append({"obs_coast": 1.1, "outdoor_at_stop": 12.0})

    # Untrained band [20, 25)
    rt._obs_outdoor = 22.0
    assert rt.get_outdoor_coast_status() == (0.0, "untrained")

    # Learned cold band [-5, 0): band_avg=0.3, overall_avg=0.7 -> raw -0.4 clamped to -0.3
    rt._obs_outdoor = -3.0
    corr, status = rt.get_outdoor_coast_status()
    assert status == "learned"
    assert corr == pytest.approx(-0.3)
    assert rt.get_predicted_coast() == pytest.approx(0.2)

    # Learned mild band [10, 15): band_avg=1.1, overall_avg=0.7 -> raw +0.4 clamped to +0.3
    rt._obs_outdoor = 13.0
    corr_mild, status_mild = rt.get_outdoor_coast_status()
    assert status_mild == "learned"
    assert corr_mild == pytest.approx(0.3)
    assert rt.get_predicted_coast() == pytest.approx(0.8)

    # Sensor attributes & diagnostics expose correction and status
    sensor = SmartPIDPredictedCoastSensor(entry, rt)
    attrs = sensor.extra_state_attributes
    assert attrs["outdoor_coast_correction"] == pytest.approx(0.3)
    assert attrs["outdoor_band_status"] == "learned"
    diag = rt.get_diagnostics_data()
    assert diag["outdoor_coast_correction"] == pytest.approx(0.3)
    assert diag["outdoor_band_status"] == "learned"


@pytest.mark.asyncio
async def test_solar_guard_refined_and_sunrise_saving_mode(hass: HomeAssistant) -> None:
    """Refined solar_guard respects learned expected_peak+0.5; Sunrise Saving handles midnight-wrap & sun.sun."""
    # 1. Refined solar_guard: learned prediction 1.0°C allows +1.2°C afterglow, rejects +1.6°C surge
    learner = DynamicCoastingLearner(
        inertia_profile=INERTIA_HIGH_INERTIA,
        tau_em_min=20.0,
        peak_confirm_delta=0.1,
    )
    learner.accepted_count = 5
    learner.k_afterglow = 2.0
    for _ in range(20):
        learner.step_cycle(is_active=True, u_effort=1.0, value=21.0, is_heating=True)
    # emitter_charge ~ 0.5 -> predicted_coast ~ 1.0
    learner.step_cycle(
        is_active=False, u_effort=0.0, value=21.0, is_heating=True, solar_rising_active=True
    )
    pred = learner._predicted_coast_at_stop
    assert pred > 0.8
    # Within expected_peak + 0.5 -> NOT rejected
    phase_ok = learner.step_cycle(
        is_active=False,
        u_effort=0.0,
        value=21.0 + pred + 0.3,
        is_heating=True,
        solar_rising_active=True,
    )
    assert phase_ok == CyclePhase.POST_STOP_COAST
    # Exceeding expected_peak + 0.5 -> rejected as solar_gain_contamination
    phase_rej = learner.step_cycle(
        is_active=False,
        u_effort=0.0,
        value=21.0 + pred + 0.6,
        is_heating=True,
        solar_rising_active=True,
    )
    assert phase_rej == CyclePhase.PASSIVE_DRIFT
    assert learner.last_quality_reason == "solar_gain_contamination"

    # 2. Sunrise Saving entities: created for heater, NOT for cooler
    cooler_entry = ConfigEntry(
        entry_id="stage7_cooler",
        title="Cooler",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_TARGET_DEVICE: "switch.ac_cool_s7",
            CONF_INDOOR_SENSOR: "sensor.temp_cool_s7",
        },
        options={},
    )
    cooler_entry.runtime_data = SmartPIDRuntime(hass, cooler_entry)
    added_cooler: list = []
    await async_setup_switch(hass, cooler_entry, lambda ents, _: added_cooler.extend(ents))
    await async_setup_time(hass, cooler_entry, lambda ents, _: added_cooler.extend(ents))
    assert added_cooler == []

    heater_entry = ConfigEntry(
        entry_id="stage7_heater",
        title="Heater",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_TARGET_DEVICE: "switch.boiler_s7",
            CONF_INDOOR_SENSOR: "sensor.temp_s7",
            CONF_SOLAR_GUARD: True,
        },
        options={},
    )
    rt = SmartPIDRuntime(hass, heater_entry)
    heater_entry.runtime_data = rt

    sw_list: list = []
    tm_list: list = []
    await async_setup_switch(hass, heater_entry, lambda ents, _: sw_list.extend(ents))
    await async_setup_time(hass, heater_entry, lambda ents, _: tm_list.extend(ents))
    assert len(sw_list) == 1 and isinstance(sw_list[0], SmartPIDSunriseSavingSwitch)
    assert len(tm_list) == 2
    assert isinstance(tm_list[0], SmartPIDSunriseSavingStartTime)
    assert isinstance(tm_list[1], SmartPIDSunriseSavingEndTime)

    sw = sw_list[0]
    t_start = tm_list[0]
    t_end = tm_list[1]

    # Default OFF
    assert sw.is_on is False
    assert rt.is_sunrise_saving_active() is False

    # Turn ON & test midnight-wrap schedule (22:00 -> 07:00) with sun below_horizon
    hass.states.async_set("sun.sun", "below_horizon")
    await sw.async_turn_on()
    await t_start.async_set_value(dt_time(22, 0, 0))
    await t_end.async_set_value(dt_time(7, 0, 0))

    assert rt.is_sunrise_saving_active(now=datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)) is True
    assert rt.is_sunrise_saving_active(now=datetime(2026, 1, 1, 6, 30, tzinfo=timezone.utc)) is True
    assert rt.is_sunrise_saving_active(now=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)) is False

    # sun.sun == above_horizon activates Sunrise Saving even outside time window
    hass.states.async_set("sun.sun", "above_horizon")
    assert rt.is_sunrise_saving_active(now=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)) is True

    # Full entry setup & unload sanity check with new platforms
    hass.states.async_set("switch.boiler_s7", "off")
    hass.states.async_set("sensor.temp_s7", "21.0")
    assert await async_setup_entry(hass, heater_entry) is True
    assert await async_unload_entry(hass, heater_entry) is True
