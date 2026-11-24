"""Config time platform for Smart PID Air Conditioner (Sunrise Saving schedule)."""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_utils import SmartPIDRuntimeEntityMixin
from .const import is_heating_type
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


class _BaseSmartPIDConfigTime(SmartPIDRuntimeEntityMixin, TimeEntity):
    """Base config time entity subscribed to SmartPIDRuntime."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime, key: str) -> None:
        self._init_runtime_entity(entry, runtime, key)


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
