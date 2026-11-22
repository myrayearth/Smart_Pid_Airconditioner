"""Select platform for Smart PID Air Conditioner runtime modes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, TUNING_MODES
from .learner import PREDICTION_MODES
from .runtime import SmartPIDRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up config select entities for Smart PID Air Conditioner."""
    runtime: SmartPIDRuntime = entry.runtime_data
    async_add_entities(
        [
            SmartPIDTuningModeSelect(entry, runtime),
            SmartPIDPredictionModeSelect(entry, runtime),
        ],
        True,
    )


class _BaseSmartPIDSelect(SelectEntity):
    """Base config select entity subscribed to SmartPIDRuntime."""

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


class SmartPIDTuningModeSelect(_BaseSmartPIDSelect):
    """Select entity to switch tuning_mode live without reload."""

    _attr_options = list(TUNING_MODES)

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "tuning_mode")

    @property
    def current_option(self) -> str:
        return self._runtime.tuning_mode

    async def async_select_option(self, option: str) -> None:
        await self._runtime.async_set_tuning_mode(option)


class SmartPIDPredictionModeSelect(_BaseSmartPIDSelect):
    """Select entity to switch SIMC prediction_mode live without reload."""

    _attr_options = list(PREDICTION_MODES)

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        super().__init__(entry, runtime, "prediction_mode")

    @property
    def current_option(self) -> str:
        return self._runtime.prediction_mode

    async def async_select_option(self, option: str) -> None:
        await self._runtime.async_set_prediction_mode(option)
