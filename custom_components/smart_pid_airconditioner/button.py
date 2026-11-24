"""Button platform for Smart PID Air Conditioner."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_utils import SmartPIDRuntimeEntityMixin
from .runtime import SmartPIDRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up config buttons for Smart PID Air Conditioner."""
    runtime: SmartPIDRuntime = entry.runtime_data
    async_add_entities([SmartPIDResetLearningButton(entry, runtime)], True)


class SmartPIDResetLearningButton(SmartPIDRuntimeEntityMixin, ButtonEntity):
    """Button entity to reset all learned models and coasting history."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        self._init_runtime_entity(entry, runtime, "reset_learning")

    @property
    def available(self) -> bool:
        """Disable reset button while the target device is actively running or dispatching."""
        return self._runtime.can_reset_learning

    async def async_press(self) -> None:
        """Reset all learned thermal/humidity models and safety baseline."""
        if not self._runtime.can_reset_learning:
            raise ServiceValidationError(
                "Cannot reset learning while the target device is active or has a pending command."
            )
        await self._runtime.async_reset_learning()
