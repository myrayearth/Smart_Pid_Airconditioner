"""Regression tests for services.yaml YAML syntax and required service fields."""

from __future__ import annotations

from pathlib import Path

import yaml


SERVICES_YAML_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "smart_pid_airconditioner"
    / "services.yaml"
)


def test_services_yaml_valid_and_has_required_fields() -> None:
    """services.yaml must parse cleanly with yaml.safe_load and expose all expected fields."""
    raw = SERVICES_YAML_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)

    assert isinstance(data, dict)
    assert "reset_learning" in data
    assert "bootstrap_from_influxdb" in data

    # 1. reset_learning fields
    reset_svc = data["reset_learning"]
    assert isinstance(reset_svc.get("name"), str) and reset_svc["name"]
    assert isinstance(reset_svc.get("description"), str) and reset_svc["description"]
    reset_fields = reset_svc.get("fields")
    assert isinstance(reset_fields, dict)
    for key in ("entry_id", "entity_id"):
        assert key in reset_fields, f"Missing reset_learning field: {key}"
        assert isinstance(reset_fields[key].get("description"), str)
        assert isinstance(reset_fields[key].get("selector"), dict)

    # 2. bootstrap_from_influxdb fields
    boot_svc = data["bootstrap_from_influxdb"]
    assert isinstance(boot_svc.get("name"), str) and boot_svc["name"]
    assert isinstance(boot_svc.get("description"), str) and boot_svc["description"]
    boot_fields = boot_svc.get("fields")
    assert isinstance(boot_fields, dict)

    expected_boot_fields = (
        "entity_id",
        "url",
        "database",
        "username",
        "password",
        "days",
        "min_indoor_temp",
        "max_indoor_temp",
        "min_indoor_humidity",
        "max_indoor_humidity",
        "indoor_temp_entity_id",
        "indoor_humidity_entity_id",
        "device_entity_id",
        "outdoor_entity_id",
        "ventilation_entity_ids",
        "device_field",
        "device_active_values",
        "device_inactive_values",
        "ventilation_open_values",
        "ventilation_closed_values",
        "ventilation_sensor_map",
        "indoor_measurement",
        "outdoor_measurement",
        "device_measurement",
        "ventilation_measurement",
        "reset_existing",
        "dry_run",
    )
    for key in expected_boot_fields:
        assert key in boot_fields, f"Missing bootstrap_from_influxdb field: {key}"
        field_cfg = boot_fields[key]
        assert isinstance(field_cfg, dict)
        assert isinstance(field_cfg.get("name"), str) and field_cfg["name"]
        assert isinstance(field_cfg.get("description"), str) and field_cfg["description"]
        assert isinstance(field_cfg.get("selector"), dict) and field_cfg["selector"]

    assert boot_fields["entity_id"].get("required") is True
