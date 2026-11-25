"""Smart PID Thermostat custom component for Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
import homeassistant.util.dt as dt_util

from .const import (
    CONF_KI,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_HUMIDITY,
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
from .storage import ModelStore

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_TARGET_DEVICE_KEYS = (
    CONF_TARGET_DEVICE,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HUMIDITY,
)


def extract_target_devices(data: dict[str, Any], options: dict[str, Any] | None = None) -> set[str]:
    """Return set of all configured target device entity IDs from options/data."""
    devices: set[str] = set()
    opts = options or {}
    for key in _TARGET_DEVICE_KEYS:
        val = opts.get(key)
        if not val:
            val = data.get(key)
        if isinstance(val, str) and val.strip():
            devices.add(val.strip())
    return devices


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
        vol.Optional("device_active_values"): vol.Any(None, "", list, str, int, float, bool),
        vol.Optional("device_inactive_values"): vol.Any(None, "", list, str, int, float, bool),
        vol.Optional("ventilation_open_values"): vol.Any(None, "", list, str, int, float, bool),
        vol.Optional("ventilation_closed_values"): vol.Any(None, "", list, str, int, float, bool),
        vol.Optional("ventilation_sensor_map"): vol.Any(None, "", dict),
        vol.Optional("indoor_measurement"): vol.Any(None, str),
        vol.Optional("outdoor_measurement"): vol.Any(None, str),
        vol.Optional("device_measurement"): vol.Any(None, str),
        vol.Optional("ventilation_measurement"): vol.Any(None, str),
        vol.Optional("reset_existing"): vol.Any(None, bool),
        vol.Optional("dry_run"): vol.Any(None, bool),
    },
    extra=vol.ALLOW_EXTRA,
)


def _iter_runtimes(hass: HomeAssistant) -> dict[str, SmartPIDRuntime]:
    """Return mapping of entry_id -> SmartPIDRuntime, excluding metadata keys like 'owners'."""
    domain_data = hass.data.get(DOMAIN, {})
    return {
        k: v
        for k, v in domain_data.items()
        if isinstance(v, SmartPIDRuntime)
    }


def _resolve_runtime_for_entity(
    hass: HomeAssistant, entity_id: str
) -> SmartPIDRuntime | None:
    """Find the SmartPIDRuntime managing a given climate or humidifier entity ID."""
    runtimes = _iter_runtimes(hass)
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
    hass.data[DOMAIN].setdefault("owners", {})
    hass.data[DOMAIN].setdefault("reload_off", {})

    async def _async_handle_reset_learning(call: ServiceCall) -> None:
        target_entry_id = call.data.get("entry_id")
        target_entity_id = call.data.get("entity_id")
        runtimes = _iter_runtimes(hass)

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
                raw_ki_group = d.get("ki_group")
                if isinstance(raw_ki_group, Mapping) and CONF_KI in raw_ki_group and raw_ki_group[CONF_KI] is not None:
                    ki_group = dict(raw_ki_group)
                    d["ki_group"] = ki_group
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
    owners: dict[str, str] = hass.data[DOMAIN].setdefault("owners", {})
    hass.data[DOMAIN].setdefault("reload_off", {})
    await async_setup(hass, {})

    target_devices = extract_target_devices(dict(entry.data), dict(entry.options))
    for dev_id in target_devices:
        existing_owner = owners.get(dev_id)
        if existing_owner is not None and existing_owner != entry.entry_id:
            raise ConfigEntryError(
                f"Target device '{dev_id}' is already controlled by another Smart PID entry ({existing_owner})."
            )

    for dev_id in target_devices:
        owners[dev_id] = entry.entry_id

    runtime = SmartPIDRuntime(hass, entry)
    try:
        await runtime.async_setup()
        entry.runtime_data = runtime
        hass.data[DOMAIN][entry.entry_id] = runtime
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        try:
            await runtime.async_unload()
        except Exception as unload_err:
            _LOGGER.warning(
                "Error rolling back runtime during failed setup for %s: %s",
                entry.entry_id,
                unload_err,
            )
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if getattr(entry, "runtime_data", None) is runtime:
            entry.runtime_data = None
        for dev_id in target_devices:
            if owners.get(dev_id) == entry.entry_id:
                owners.pop(dev_id, None)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        _LOGGER.warning(
            "Failed to unload platforms for Smart PID entry %s; preserving runtime and device ownership",
            entry.entry_id,
        )
        return False

    runtime: SmartPIDRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        await runtime.async_unload()
    domain_data = hass.data.get(DOMAIN, {})
    domain_data.pop(entry.entry_id, None)
    owners: dict[str, str] = domain_data.get("owners", {})
    for dev_id in [k for k, v in owners.items() if v == entry.entry_id]:
        owners.pop(dev_id, None)
    reload_off = domain_data.get("reload_off")
    if isinstance(reload_off, Mapping):
        now = dt_util.utcnow()
        for dev_id in [
            k
            for k, val in reload_off.items()
            if not (
                isinstance(val, tuple)
                and len(val) == 2
                and isinstance(val[1], datetime)
                and val[1] > now
            )
        ]:
            reload_off.pop(dev_id, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up persisted learning storage when a config entry is deleted."""
    await ModelStore(hass, entry.entry_id).async_remove()
