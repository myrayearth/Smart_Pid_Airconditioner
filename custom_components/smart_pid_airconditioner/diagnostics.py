"""Diagnostics support for Smart PID Air Conditioner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .runtime import SmartPIDRuntime

REDACT_KEYS = {"unique_id"}


def _redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Redact sensitive keys recursively from entry configuration mappings."""
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if key in REDACT_KEYS:
            redacted[key] = "**REDACTED**"
        elif isinstance(value, Mapping):
            redacted[key] = _redact_mapping(value)
        else:
            redacted[key] = value
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics snapshot for Home Assistant Download Diagnostics."""
    runtime: SmartPIDRuntime | None = getattr(entry, "runtime_data", None)
    payload: dict[str, Any] = {
        "config_entry": {
            "title": entry.title,
            "version": entry.version,
            "data": _redact_mapping(dict(entry.data)),
            "options": _redact_mapping(dict(entry.options)),
        }
    }
    if runtime is not None:
        payload["runtime"] = runtime.get_diagnostics_data()
    return payload
