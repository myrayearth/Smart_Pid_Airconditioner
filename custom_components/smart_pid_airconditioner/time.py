"""Config time platform for Smart PID Air Conditioner (Sunrise Saving schedule)."""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, is_heating_type
from .runtime import SmartPIDRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up config time entities for Sunrise Saving (heating types only)."""
    runtime: SmartPIDRuntime = entry.runtime_data
    if not is_heating_type(runtime.thermostat_type):
        return
    async_add_entities(
        [
            SmartPIDSunriseSavingStartTime(entry, runtime),
            SmartPIDSunriseSavingEndTime(entry, runtime),
        ],
        True,
    )


class _BaseSmartPIDConfigTime(TimeEntity):
    """Base config time entity subscribed to SmartPIDRuntime."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime, key: str) -> None:
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Smart PID",
            model="Smart PID Controller",
        )
        self._unsub: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub = self._runtime.subscribe(self._on_runtime_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _on_runtime_update(self) -> None:
        self.async_write_ha_state()


class SmartPIDSunriseSavingStartTime(_BaseSmartPIDConfigTime):
    """Start time for Sunrise Saving window."""

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "sunrise_saving_start")

    @property
    def native_value(self) -> dt_time:
        return self._runtime.sunrise_saving_start

    async def async_set_value(self, value: dt_time) -> None:
        await self._runtime.async_set_sunrise_saving_start(value)


class SmartPIDSunriseSavingEndTime(_BaseSmartPIDConfigTime):
    """End time for Sunrise Saving window."""

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "sunrise_saving_end")

    @property
    def native_value(self) -> dt_time:
        return self._runtime.sunrise_saving_end

    async def async_set_value(self, value: dt_time) -> None:
        await self._runtime.async_set_sunrise_saving_end(value)
