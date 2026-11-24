"""Config switch platform for Smart PID Air Conditioner (Sunrise Saving)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_utils import SmartPIDRuntimeEntityMixin
from .const import is_heating_type, is_humidity_type
from .runtime import SmartPIDRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up config switches for Smart PID Air Conditioner."""
    runtime: SmartPIDRuntime = entry.runtime_data
    entities: list[SwitchEntity] = []
    if is_heating_type(runtime.thermostat_type):
        entities.append(SmartPIDSunriseSavingSwitch(entry, runtime))
    if not is_humidity_type(runtime.thermostat_type):
        entities.append(SmartPIDPredictiveOnSwitch(entry, runtime))
    if entities:
        async_add_entities(entities, True)


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


class SmartPIDPredictiveOnSwitch(SmartPIDRuntimeEntityMixin, SwitchEntity):
    """Live toggle switch for Predictive ON (pre-heat/pre-cool) on temperature controllers."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime: SmartPIDRuntime) -> None:
        self._init_runtime_entity(entry, runtime, "predictive_on")

    @property
    def is_on(self) -> bool:
        return self._runtime.predictive_on_enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coasting = self._runtime.snapshot.coasting
        return {
            "predicted_preheat_drop": round(
                coasting.predict_preheat_drop(self._runtime._obs_outdoor), 4
            ),
            "effective_dead_time_min": round(coasting.get_effective_dead_time(), 2),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._runtime.async_set_predictive_on_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._runtime.async_set_predictive_on_enabled(False)
