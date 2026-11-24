"""Unit tests for extracted shared modules: config_utils, pid_core, and flow_schemas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.smart_pid_airconditioner.config_utils import (
    SmartPIDExtraStoredData,
    get_entry_setting,
    parse_humidity_state,
    parse_temperature_state,
    resolve_type_label,
)
from custom_components.smart_pid_airconditioner.const import (
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HUMIDIFIER,
)
from custom_components.smart_pid_airconditioner.flow_schemas import (
    build_climate_sections_schema,
    build_device_sensor_schema,
    build_entry_title,
    build_pwm_sections_schema,
    process_pwm_sections_input,
    validate_and_prepare_climate_sections,
)
from custom_components.smart_pid_airconditioner.pid_core import (
    compute_effective_min_off,
    compute_pid_step,
    determine_active_section,
    evaluate_sensor_recovery,
)


class _DummyEntry:
    def __init__(self, data=None, options=None) -> None:
        self.data = data or {}
        self.options = options or {}


class _DummyState:
    def __init__(self, state, attributes=None) -> None:
        self.state = state
        self.attributes = attributes or {}


def test_get_entry_setting_priority_and_nested_sections() -> None:
    entry = _DummyEntry(
        data={"kp": 10.0, "section_1": {"sec1_max": 25.0}, "ki_group": {"ki": 0.5}},
        options={"kp": 20.0, "section_1": {"sec1_max": 30.0}},
    )
    assert get_entry_setting(entry, "kp", 5.0) == 20.0
    assert get_entry_setting(entry, "sec1_max", 10.0) == 30.0
    assert get_entry_setting(entry, "ki", 0.1) == 0.5
    assert get_entry_setting(entry, "missing", 99.0) == 99.0


def test_parse_temperature_and_humidity_state() -> None:
    assert parse_temperature_state(_DummyState("23.5 °C")) == pytest.approx(23.5)
    assert parse_temperature_state(_DummyState("23,2C")) == pytest.approx(23.2)
    assert parse_temperature_state(_DummyState("sunny", {"temperature": 21.0})) == pytest.approx(21.0)
    assert parse_temperature_state(_DummyState("unavailable")) is None

    assert parse_humidity_state(_DummyState("55.5%")) == pytest.approx(55.5)
    assert parse_humidity_state(_DummyState("cloudy", {"humidity": 60})) == pytest.approx(60.0)
    assert parse_humidity_state(_DummyState("unknown")) is None


def test_extra_stored_data_roundtrip() -> None:
    orig = SmartPIDExtraStoredData(
        last_switch_on_times={"switch.a": "2026-11-24T00:00:00+00:00"},
        last_switch_off_times={"switch.a": "2026-11-24T00:10:00+00:00"},
        active_section=2,
        integral=4.5,
        last_run_duration_sec=180.0,
    )
    restored = SmartPIDExtraStoredData.from_dict(orig.as_dict())
    assert restored is not None
    assert restored.active_section == 2
    assert restored.integral == pytest.approx(4.5)
    assert restored.last_run_duration_sec == pytest.approx(180.0)


def test_compute_pid_step_and_section_hysteresis() -> None:
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    res = compute_pid_step(
        indoor_val=26.0,
        target_val=24.0,
        outdoor_val=30.0,
        has_outdoor_sensor=True,
        is_heating=False,
        kp=10.0,
        ki=1.0,
        kd=0.0,
        ke=0.5,
        max_i=20.0,
        dt=60.0,
        sample_period=60,
        now=t0,
        force=False,
        integral=0.0,
        filtered_d=0.0,
        last_d_calc_time=None,
        prev_val=None,
        freeze_integral=False,
        per_minute_integral=True,
    )
    # P = 10 * 2 = 20, E = 0.5 * 6 = 3, I = 1 * 2 * 1 = 2 -> 25
    assert res.final_output == pytest.approx(25.0)
    assert res.integral == pytest.approx(2.0)

    # Hysteresis: sec1_max=25, band=1.5 -> 26.0 stays in section 1, 27.0 moves to section 2
    assert (
        determine_active_section(
            output=26.0, current_sec=1, sec1_max=25.0, sec2_max=50.0, sec3_max=75.0, allow_sec0=False
        )
        == 1
    )
    assert (
        determine_active_section(
            output=27.0, current_sec=1, sec1_max=25.0, sec2_max=50.0, sec3_max=75.0, allow_sec0=False
        )
        == 2
    )


def test_dynamic_min_off_and_sensor_recovery() -> None:
    assert compute_effective_min_off(180.0, 300.0, 1.5) == pytest.approx(450.0)
    assert compute_effective_min_off(180.0, 300.0, 0.0) == pytest.approx(180.0)

    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    accept, fault, first_at = evaluate_sensor_recovery(
        parsed=None, fault_active=False, recovery_first_at=None, now=t0
    )
    assert (accept, fault, first_at) == (False, True, None)

    accept, fault, first_at = evaluate_sensor_recovery(
        parsed=22.0, fault_active=True, recovery_first_at=None, now=t0 + timedelta(seconds=10)
    )
    assert (accept, fault) == (False, True)
    assert first_at == t0 + timedelta(seconds=10)

    accept, fault, first_at = evaluate_sensor_recovery(
        parsed=22.1, fault_active=True, recovery_first_at=first_at, now=t0 + timedelta(seconds=45)
    )
    assert (accept, fault, first_at) == (True, False, None)


def test_flow_schemas_builders_and_validators() -> None:
    schema_cfg = build_device_sensor_schema(
        control_mode=CONTROL_MODE_CLIMATE_DELTA,
        thermostat_type=THERMOSTAT_TYPE_COOLER,
        get_val=lambda k, d=None: d,
        is_options=False,
    )
    schema_opt = build_device_sensor_schema(
        control_mode=CONTROL_MODE_SWITCH_PWM,
        thermostat_type=THERMOSTAT_TYPE_HEATER,
        get_val=lambda k, d=None: d,
        is_options=True,
    )
    assert schema_cfg is not None
    assert schema_opt is not None

    pwm_schema = build_pwm_sections_schema(lambda k, d=None: d)
    assert pwm_schema is not None
    errs, processed = process_pwm_sections_input(
        {"pwm_duration": 900, "min_on_time": 180, "min_off_time": 180}
    )
    assert errs == {}
    assert processed["pwm_duration"] == 900

    bad_errs, _ = process_pwm_sections_input(
        {"pwm_duration": 200, "min_on_time": 180, "min_off_time": 180}
    )
    assert bad_errs["base"] == "min_times_exceed_pwm_duration"

    sec_schema = build_climate_sections_schema(
        thermostat_type=THERMOSTAT_TYPE_HUMIDIFIER,
        selected_hvac_modes=["humidity", "auto"],
        selected_fan_modes=["low", "high"],
        get_val=lambda k, d=None: d,
    )
    assert sec_schema is not None
    sec_errs, prepared = validate_and_prepare_climate_sections(
        {"sec1_max": 25, "sec2_max": 50, "sec3_max": 75}, lambda k, d=None: d
    )
    assert sec_errs == {}
    assert prepared["sec1_max"] == 25

    assert resolve_type_label(THERMOSTAT_TYPE_HEATER) == "Heater"
    assert "Heater" in build_entry_title(
        {"thermostat_type": THERMOSTAT_TYPE_HEATER, "target_climate": "climate.boiler"},
        is_pwm=False,
    )
