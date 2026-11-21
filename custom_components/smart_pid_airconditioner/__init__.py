"""Smart PID Thermostat custom component for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_KI,
    CONF_THERMOSTAT_TYPE,
    DEFAULT_THERMOSTAT_TYPE,
    DOMAIN,
    PLATFORMS,
    SERVICE_RESET_LEARNING,
    is_humidity_type,
)
from .runtime import SmartPIDRuntime

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Smart PID Thermostat component."""
    hass.data.setdefault(DOMAIN, {})

    async def _async_handle_reset_learning(call: ServiceCall) -> None:
        target_entry_id = call.data.get("entry_id")
        target_entity_id = call.data.get("entity_id")
        runtimes: dict[str, SmartPIDRuntime] = hass.data.get(DOMAIN, {})

        if target_entry_id and target_entry_id in runtimes:
            await runtimes[target_entry_id].async_reset_learning()
            return

        if target_entity_id:
            for rt in runtimes.values():
                await rt.async_reset_learning()
            return

        for rt in list(runtimes.values()):
            await rt.async_reset_learning()

    if not hass.services.has_service(DOMAIN, SERVICE_RESET_LEARNING):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RESET_LEARNING,
            _async_handle_reset_learning,
        )

    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries to the latest version."""
    if config_entry.version == 1:
        _LOGGER.info(
            "Migrating Smart PID config entry %s from v1 to v2 (per-minute Ki normalization)",
            config_entry.entry_id,
        )
        new_data = dict(config_entry.data)
        new_options = dict(config_entry.options)
        thermostat_type = new_options.get(
            CONF_THERMOSTAT_TYPE,
            new_data.get(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE),
        )

        if not is_humidity_type(thermostat_type):
            def _scale_ki_in_dict(d: dict[str, Any]) -> None:
                if CONF_KI in d and d[CONF_KI] is not None:
                    try:
                        d[CONF_KI] = round(min(100.0, float(d[CONF_KI]) * 60.0), 4)
                    except (ValueError, TypeError):
                        pass
                ki_group = d.get("ki_group")
                if isinstance(ki_group, dict) and CONF_KI in ki_group and ki_group[CONF_KI] is not None:
                    try:
                        ki_group[CONF_KI] = round(min(100.0, float(ki_group[CONF_KI]) * 60.0), 4)
                    except (ValueError, TypeError):
                        pass

            _scale_ki_in_dict(new_data)
            _scale_ki_in_dict(new_options)

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            options=new_options,
            version=2,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart PID Thermostat from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await async_setup(hass, {})

    runtime = SmartPIDRuntime(hass, entry)
    await runtime.async_setup()
    entry.runtime_data = runtime
    hass.data[DOMAIN][entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime: SmartPIDRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        await runtime.async_unload()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
