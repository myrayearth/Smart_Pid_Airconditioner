"""Smart PID Thermostat custom component for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_KI,
    CONF_THERMOSTAT_TYPE,
    DEFAULT_THERMOSTAT_TYPE,
    DOMAIN,
    PLATFORMS,
    SERVICE_BOOTSTRAP_INFLUXDB,
    SERVICE_RESET_LEARNING,
    is_humidity_type,
)
from .influxdb_bootstrap import async_bootstrap_from_influxdb
from .runtime import SmartPIDRuntime

_LOGGER = logging.getLogger(__name__)

_OPT_FLOAT = vol.Any(None, "", vol.Coerce(float))
_OPT_INT = vol.Any(None, "", vol.All(vol.Coerce(float), vol.Coerce(int)))

_BOOTSTRAP_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): vol.Any(str, [str]),
        vol.Optional("url"): vol.Any(None, str),
        vol.Optional("database"): vol.Any(None, str),
        vol.Optional("username"): vol.Any(None, str),
        vol.Optional("password"): vol.Any(None, str),
        vol.Optional("days"): _OPT_INT,
        vol.Optional("min_indoor_temp"): _OPT_FLOAT,
        vol.Optional("max_indoor_temp"): _OPT_FLOAT,
        vol.Optional("min_indoor_humidity"): _OPT_FLOAT,
        vol.Optional("max_indoor_humidity"): _OPT_FLOAT,
        vol.Optional("indoor_temp_entity_id"): vol.Any(None, str),
        vol.Optional("indoor_humidity_entity_id"): vol.Any(None, str),
        vol.Optional("device_entity_id"): vol.Any(None, str),
        vol.Optional("outdoor_entity_id"): vol.Any(None, str),
        vol.Optional("ventilation_entity_ids"): vol.Any(None, str, [str]),
        vol.Optional("device_field"): vol.Any(None, str),
        vol.Optional("indoor_measurement"): vol.Any(None, str),
        vol.Optional("outdoor_measurement"): vol.Any(None, str),
        vol.Optional("device_measurement"): vol.Any(None, str),
        vol.Optional("ventilation_measurement"): vol.Any(None, str),
        vol.Optional("reset_existing"): vol.Any(None, bool),
        vol.Optional("dry_run"): vol.Any(None, bool),
    },
    extra=vol.ALLOW_EXTRA,
)


def _resolve_runtime_for_entity(
    hass: HomeAssistant, entity_id: str
) -> SmartPIDRuntime | None:
    """Find the SmartPIDRuntime managing a given climate or humidifier entity ID."""
    runtimes: dict[str, SmartPIDRuntime] = hass.data.get(DOMAIN, {})
    if not runtimes or not entity_id:
        return None

    reg = er.async_get(hass)
    if reg is not None:
        async_get = getattr(reg, "async_get", None)
        if callable(async_get):
            reg_entry = async_get(entity_id)
            if reg_entry is not None:
                entry_id = getattr(reg_entry, "config_entry_id", None)
                if entry_id and entry_id in runtimes:
                    return runtimes[entry_id]

        for entry_id, rt in runtimes.items():
            for platform, suffix in (
                (Platform.CLIMATE, "thermostat"),
                (Platform.HUMIDIFIER, "humidifier"),
            ):
                if reg.async_get_entity_id(platform, DOMAIN, f"{entry_id}_{suffix}") == entity_id:
                    return rt

    return None


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
            rt = _resolve_runtime_for_entity(hass, target_entity_id)
            if rt is not None:
                await rt.async_reset_learning()
                return
            for rt in list(runtimes.values()):
                await rt.async_reset_learning()
            return

        for rt in list(runtimes.values()):
            await rt.async_reset_learning()

    async def _async_handle_bootstrap_influxdb(call: ServiceCall) -> dict[str, Any]:
        raw_eid = call.data.get("entity_id", "")
        if isinstance(raw_eid, list):
            target_entity_id = str(raw_eid[0]).strip() if raw_eid else ""
        else:
            target_entity_id = str(raw_eid).strip()
        runtime = _resolve_runtime_for_entity(hass, target_entity_id)
        if runtime is None:
            raise HomeAssistantError(
                f"Could not find an active Smart PID controller for entity '{target_entity_id}'."
            )
        return await async_bootstrap_from_influxdb(hass, runtime, dict(call.data))

    if not hass.services.has_service(DOMAIN, SERVICE_RESET_LEARNING):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RESET_LEARNING,
            _async_handle_reset_learning,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_BOOTSTRAP_INFLUXDB):
        hass.services.async_register(
            DOMAIN,
            SERVICE_BOOTSTRAP_INFLUXDB,
            _async_handle_bootstrap_influxdb,
            schema=_BOOTSTRAP_SERVICE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
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
