"""Config switch platform for Smart PID Air Conditioner (Sunrise Saving)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up config switches for Smart PID Air Conditioner (heating types only)."""
    runtime: SmartPIDRuntime = entry.runtime_data
    if not is_heating_type(runtime.thermostat_type):
        return
    async_add_entities([SmartPIDSunriseSavingSwitch(entry, runtime)], True)


class SmartPIDSunriseSavingSwitch(SmartPIDRuntimeEntityMixin, SwitchEntity):
    """Live toggle switch for Sunrise Saving mode on heating controllers."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        self._init_runtime_entity(entry, runtime, "sunrise_saving")

    @property
    def is_on(self) -> bool:
        return self._runtime.sunrise_saving_enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "sunrise_saving_active": self._runtime.is_sunrise_saving_active(),
            "start_time": self._runtime.sunrise_saving_start.strftime("%H:%M:%S"),
            "end_time": self._runtime.sunrise_saving_end.strftime("%H:%M:%S"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._runtime.async_set_sunrise_saving_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._runtime.async_set_sunrise_saving_enabled(False)
