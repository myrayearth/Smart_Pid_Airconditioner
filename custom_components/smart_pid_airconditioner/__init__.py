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
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
import homeassistant.util.dt as dt_util

from .config_utils import get_entry_setting
from .const import (
    CONF_CONTROL_MODE,
    CONF_KI,
    CONF_SAMPLE_PERIOD,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_HUMIDITY,
    CONF_THERMOSTAT_TYPE,
    CONTROL_MODE_SWITCH_PWM,
    DEFAULT_CONTROL_MODE,
    DEFAULT_SAMPLE_PERIOD,
    DEFAULT_THERMOSTAT_TYPE,
    DOMAIN,
    PLATFORMS,
    SERVICE_BOOTSTRAP_INFLUXDB,
    SERVICE_RESET_LEARNING,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    is_humidity_type,
    resolve_thermostat_type,
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


def extract_target_devices(
    data: Mapping[str, Any], options: Mapping[str, Any] | None = None
) -> set[str]:
    """Return set of all configured target device entity IDs from options/data."""
    devices: set[str] = set()
    opts = options or {}
    for key in _TARGET_DEVICE_KEYS:
        val = opts[key] if key in opts else data.get(key)
        if isinstance(val, str) and val.strip():
            devices.add(val.strip())
    return devices


def validate_entry_target_devices(entry: ConfigEntry) -> None:
    """Reject dual PWM entries configured with the same device for opposing roles."""
    control_mode = str(get_entry_setting(entry, CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE))
    if control_mode != CONTROL_MODE_SWITCH_PWM:
        return
    thermostat_type = resolve_thermostat_type(entry)
    if thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
        cool_dev = str(get_entry_setting(entry, CONF_TARGET_DEVICE_COOL, "") or "").strip()
        heat_dev = str(get_entry_setting(entry, CONF_TARGET_DEVICE_HEAT, "") or "").strip()
        if cool_dev and heat_dev and cool_dev == heat_dev:
            raise ConfigEntryError(
                f"Dual PWM heat_cool entry '{entry.entry_id}' cannot assign '{cool_dev}' to both cool and heat."
            )
    elif thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
        dehum_dev = str(get_entry_setting(entry, CONF_TARGET_DEVICE_DEHUMIDITY, "") or "").strip()
        hum_dev = str(get_entry_setting(entry, CONF_TARGET_DEVICE_HUMIDITY, "") or "").strip()
        if dehum_dev and hum_dev and dehum_dev == hum_dev:
            raise ConfigEntryError(
                f"Dual PWM humidity_dehumidity entry '{entry.entry_id}' cannot assign '{dehum_dev}' to both humidity and dehumidity."
            )


def _strict_bool(val: Any) -> bool:
    """Validate that a service parameter is strictly a boolean."""
    if isinstance(val, bool):
        return val
    raise vol.Invalid(f"Expected boolean true/false, got {val!r}")


def _single_entity_id(val: Any) -> str:
    """Accept a single non-empty entity_id string or 1-element list and return stripped string."""
    if isinstance(val, str):
        stripped = val.strip()
        if stripped:
            return stripped
        raise vol.Invalid("entity_id must not be empty")
    if isinstance(val, (list, tuple)):
        if len(val) != 1 or not isinstance(val[0], str) or not val[0].strip():
            raise vol.Invalid("entity_id must contain a single non-empty entity ID")
        return val[0].strip()
    raise vol.Invalid("entity_id must be a single entity ID string")


_OPT_FLOAT = vol.Any(None, "", vol.Coerce(float))
_OPT_INT = vol.Any(None, "", vol.All(vol.Coerce(float), vol.Coerce(int)))

_BOOTSTRAP_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): _single_entity_id,
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
        vol.Optional("reset_existing"): _strict_bool,
        vol.Optional("dry_run"): _strict_bool,
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
    hass.data[DOMAIN].setdefault("reload_unverified_off", {})
    hass.data[DOMAIN].setdefault("overheat_latched", {})

    async def _async_handle_reset_learning(call: ServiceCall) -> None:
        raw_entry_id = call.data.get("entry_id")
        raw_entity_id = call.data.get("entity_id")
        entry_id_provided = "entry_id" in call.data and raw_entry_id is not None
        entity_id_provided = "entity_id" in call.data and raw_entity_id is not None
        runtimes = _iter_runtimes(hass)

        if entry_id_provided or entity_id_provided:
            selected_runtimes: list[SmartPIDRuntime] = []

            entry_runtime: SmartPIDRuntime | None = None
            if entry_id_provided:
                target_entry_id = str(raw_entry_id).strip()
                if not target_entry_id or target_entry_id not in runtimes:
                    raise ServiceValidationError(
                        f"Could not find an active Smart PID controller for entry_id '{raw_entry_id}'."
                    )
                entry_runtime = runtimes[target_entry_id]
                selected_runtimes.append(entry_runtime)

            if entity_id_provided:
                if isinstance(raw_entity_id, str):
                    target_entity_ids = [raw_entity_id.strip()] if raw_entity_id.strip() else []
                elif isinstance(raw_entity_id, (list, tuple, set)):
                    target_entity_ids = [
                        str(e).strip() for e in raw_entity_id if e is not None and str(e).strip()
                    ]
                else:
                    target_entity_ids = []

                if not target_entity_ids:
                    raise ServiceValidationError(
                        f"Invalid or empty entity_id '{raw_entity_id}' specified for reset_learning."
                    )

                entity_runtimes: list[SmartPIDRuntime] = []
                for eid in target_entity_ids:
                    rt = _resolve_runtime_for_entity(hass, eid)
                    if rt is None:
                        raise ServiceValidationError(
                            f"Could not find an active Smart PID controller for entity_id '{eid}'."
                        )
                    if entry_runtime is not None and rt is not entry_runtime:
                        raise ServiceValidationError(
                            f"Target entity_id '{eid}' does not match specified entry_id '{raw_entry_id}'."
                        )
                    entity_runtimes.append(rt)

                if not entry_id_provided:
                    selected_runtimes.extend(entity_runtimes)

            for rt in dict.fromkeys(selected_runtimes):
                await rt.async_reset_learning()
            return

        for rt in list(runtimes.values()):
            await rt.async_reset_learning()

    async def _async_handle_bootstrap_influxdb(call: ServiceCall) -> dict[str, Any]:
        raw_eid = call.data.get("entity_id")
        if isinstance(raw_eid, (list, tuple)):
            if len(raw_eid) != 1 or not isinstance(raw_eid[0], str) or not raw_eid[0].strip():
                raise ServiceValidationError(
                    "entity_id must be a single non-empty Smart PID entity ID."
                )
            target_entity_id = raw_eid[0].strip()
        elif isinstance(raw_eid, str) and raw_eid.strip():
            target_entity_id = raw_eid.strip()
        else:
            raise ServiceValidationError(
                "entity_id must be a single non-empty Smart PID entity ID."
            )

        for bool_key in ("dry_run", "reset_existing"):
            if bool_key in call.data and not isinstance(call.data[bool_key], bool):
                raise ServiceValidationError(
                    f"'{bool_key}' must be a boolean (true/false), got {call.data[bool_key]!r}."
                )

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

    if config_entry.version == 2:
        _LOGGER.info(
            "Migrating Smart PID config entry %s from v2 to v3 (humidity per-minute Ki normalization)",
            config_entry.entry_id,
        )
        new_data = dict(config_entry.data)
        new_options = dict(config_entry.options)
        thermostat_type = resolve_thermostat_type(config_entry)
        if is_humidity_type(thermostat_type):
            try:
                sample_period = max(
                    1,
                    int(
                        float(
                            get_entry_setting(
                                config_entry, CONF_SAMPLE_PERIOD, DEFAULT_SAMPLE_PERIOD
                            )
                        )
                    ),
                )
            except (ValueError, TypeError):
                sample_period = DEFAULT_SAMPLE_PERIOD
            scale = 60.0 / float(sample_period)
            if abs(scale - 1.0) > 1e-9:
                def _scale_hum_ki_in_dict(d: dict[str, Any]) -> None:
                    if CONF_KI in d and d[CONF_KI] is not None:
                        try:
                            d[CONF_KI] = round(
                                max(0.0, min(100.0, float(d[CONF_KI]) * scale)), 4
                            )
                        except (ValueError, TypeError):
                            pass
                    raw_ki_group = d.get("ki_group")
                    if (
                        isinstance(raw_ki_group, Mapping)
                        and CONF_KI in raw_ki_group
                        and raw_ki_group[CONF_KI] is not None
                    ):
                        ki_group = dict(raw_ki_group)
                        d["ki_group"] = ki_group
                        try:
                            ki_group[CONF_KI] = round(
                                max(0.0, min(100.0, float(ki_group[CONF_KI]) * scale)), 4
                            )
                        except (ValueError, TypeError):
                            pass

                _scale_hum_ki_in_dict(new_data)
                _scale_hum_ki_in_dict(new_options)

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            options=new_options,
            version=3,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart PID Thermostat from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    owners: dict[str, str] = hass.data[DOMAIN].setdefault("owners", {})
    hass.data[DOMAIN].setdefault("reload_off", {})
    hass.data[DOMAIN].setdefault("reload_unverified_off", {})
    hass.data[DOMAIN].setdefault("overheat_latched", {})
    await async_setup(hass, {})

    validate_entry_target_devices(entry)
    target_devices = extract_target_devices(dict(entry.data), dict(entry.options))
    for dev_id in target_devices:
        existing_owner = owners.get(dev_id)
        if existing_owner is not None and existing_owner != entry.entry_id:
            raise ConfigEntryError(
                f"Target device '{dev_id}' is already controlled by another Smart PID entry ({existing_owner})."
            )

    reserved_devices: list[str] = []
    runtime: SmartPIDRuntime | None = None
    runtime_started = False
    platforms_forwarded = False
    try:
        for dev_id in target_devices:
            owners[dev_id] = entry.entry_id
            reserved_devices.append(dev_id)

        runtime = SmartPIDRuntime(hass, entry)
        await runtime.async_setup()
        runtime_started = True
        entry.runtime_data = runtime
        hass.data[DOMAIN][entry.entry_id] = runtime
        platforms_forwarded = True
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        if platforms_forwarded:
            try:
                await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            except Exception as plat_err:
                _LOGGER.warning(
                    "Error unloading platforms during failed setup for %s: %s",
                    entry.entry_id,
                    plat_err,
                )
        if runtime is not None and runtime_started:
            try:
                await runtime.async_unload()
            except Exception as unload_err:
                _LOGGER.warning(
                    "Error rolling back runtime during failed setup for %s: %s",
                    entry.entry_id,
                    unload_err,
                )
        if runtime is not None:
            if hass.data[DOMAIN].get(entry.entry_id) is runtime:
                hass.data[DOMAIN].pop(entry.entry_id, None)
            if getattr(entry, "runtime_data", None) is runtime:
                entry.runtime_data = None
        for dev_id in reversed(reserved_devices):
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
    domain_data = hass.data.get(DOMAIN)
    if isinstance(domain_data, dict):
        latches = domain_data.get("overheat_latched")
        if isinstance(latches, dict):
            latches.pop(entry.entry_id, None)
    await ModelStore(hass, entry.entry_id).async_remove()
