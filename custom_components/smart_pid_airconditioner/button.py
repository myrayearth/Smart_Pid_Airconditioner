"""Button platform for Smart PID Air Conditioner."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime import SmartPIDRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up config buttons for Smart PID Air Conditioner."""
    runtime: SmartPIDRuntime = entry.runtime_data
    async_add_entities([SmartPIDResetLearningButton(entry, runtime)], True)


class SmartPIDResetLearningButton(ButtonEntity):
    """Button entity to reset all learned models and coasting history."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "reset_learning"
    _attr_icon = "mdi:restart"

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_reset_learning"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Smart PID",
            model="Smart PID Controller",
        )

    async def async_press(self) -> None:
        """Reset all learned thermal/humidity models and safety baseline."""
        await self._runtime.async_reset_learning()
