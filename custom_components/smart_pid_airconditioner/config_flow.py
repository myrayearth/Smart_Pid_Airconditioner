"""Config flow and Options flow for Smart PID Air Conditioner integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .config_utils import get_entry_setting
from .const import (
    CONF_AVAILABLE_FAN_MODES,
    CONF_AVAILABLE_HVAC_MODES,
    CONF_CONTROL_MODE,
    CONF_THERMOSTAT_TYPE,
    CONTROL_MODES,
    CONTROL_MODE_SWITCH_PWM,
    DEFAULT_AVAILABLE_FAN_MODES,
    DEFAULT_AVAILABLE_HVAC_MODES,
    DEFAULT_CONTROL_MODE,
    DEFAULT_THERMOSTAT_TYPE,
    DOMAIN,
    THERMOSTAT_TYPES,
)
from .flow_schemas import (
    ACTION_OPTIONS,
    FALLBACK_FAN_MODES,
    FAN_MODE_LABELS,
    MODE_LABELS,
    KiGridSelector,
    build_adaptive_learning_section as _build_adaptive_learning_section,
    build_climate_sections_schema,
    build_device_sensor_schema,
    build_entry_title,
    build_pwm_sections_schema,
    build_sec0_dict as _build_sec0_dict,
    build_sections_schema as _build_sections_schema,
    build_ventilation_section as _build_ventilation_section,
    default_sec0_delta as _default_sec0_delta,
    find_duplicate_target_device as _find_duplicate_target_device,
    flatten_sections_input as _flatten_sections_input,
    format_fan_options,
    format_mode_options,
    get_fan_modes,
    get_humidifier_modes,
    get_hvac_modes,
    process_pwm_sections_input,
    validate_and_prepare_climate_sections,
    validate_device_sensor_input,
)

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "ACTION_OPTIONS",
    "FALLBACK_FAN_MODES",
    "FAN_MODE_LABELS",
    "MODE_LABELS",
    "KiGridSelector",
    "SmartPIDThermostatConfigFlow",
    "SmartPIDThermostatOptionsFlow",
    "format_fan_options",
    "format_mode_options",
    "get_fan_modes",
    "get_humidifier_modes",
    "get_hvac_modes",
    "_build_adaptive_learning_section",
    "_build_sec0_dict",
    "_build_sections_schema",
    "_build_ventilation_section",
    "_default_sec0_delta",
    "_find_duplicate_target_device",
    "_flatten_sections_input",
]


class SmartPIDThermostatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart PID Thermostat."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle step 1: Select control mode and thermostat type."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data = dict(user_input)
            try:
                return await self.async_step_device_sensor()
            except Exception as ex:
                _LOGGER.exception("Error progressing to step_device_sensor: %s", ex)
                errors["base"] = f"Error: {ex}"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONTROL_MODE, default=DEFAULT_CONTROL_MODE
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=CONTROL_MODES,
                        translation_key="control_mode",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_THERMOSTAT_TYPE, default=DEFAULT_THERMOSTAT_TYPE
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=THERMOSTAT_TYPES,
                        translation_key="thermostat_type",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_device_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle step 2: Target device, sensors, and basic PID parameters."""
        errors: dict[str, str] = {}
        control_mode = self._data.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        thermostat_type = self._data.get(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)

        if user_input is not None:
            errors, payload = validate_device_sensor_input(
                self.hass, user_input, control_mode, thermostat_type
            )
            if not errors:
                self._data.update(payload)
                if control_mode == CONTROL_MODE_SWITCH_PWM:
                    return await self.async_step_sections_pwm()
                return await self.async_step_sections_climate()

        schema = build_device_sensor_schema(
            control_mode=control_mode,
            thermostat_type=thermostat_type,
            get_val=lambda k, d=None: self._data.get(k, d),
            is_options=False,
        )
        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(
            step_id="device_sensor", data_schema=schema, errors=errors
        )

    async def async_step_sections(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Alias for async_step_sections_climate."""
        return await self.async_step_sections_climate(user_input)

    async def async_step_sections_pwm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle step 2 for switch PWM mode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, processed = process_pwm_sections_input(user_input)
            if not errors:
                full_data = {**self._data, **processed}
                return self.async_create_entry(
                    title=build_entry_title(full_data, is_pwm=True),
                    data=full_data,
                )

        schema = build_pwm_sections_schema(lambda k, d=None: d)
        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="sections_pwm", data_schema=schema, errors=errors
        )

    async def async_step_sections_climate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle step 2: Section mapping, HVAC mode, and fan mode settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, prepared = validate_and_prepare_climate_sections(
                user_input, lambda k, d=None: d
            )
            if not errors:
                full_data = {**self._data, **prepared}
                return self.async_create_entry(
                    title=build_entry_title(full_data, is_pwm=False),
                    data=full_data,
                )

        selected_hvac_modes = (
            self._data.get(CONF_AVAILABLE_HVAC_MODES) or DEFAULT_AVAILABLE_HVAC_MODES
        )
        selected_fan_modes = (
            self._data.get(CONF_AVAILABLE_FAN_MODES) or DEFAULT_AVAILABLE_FAN_MODES
        )
        thermostat_type = self._data.get(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)

        schema = build_climate_sections_schema(
            thermostat_type=thermostat_type,
            selected_hvac_modes=list(selected_hvac_modes),
            selected_fan_modes=list(selected_fan_modes),
            get_val=lambda k, d=None: self._data.get(k, d),
        )
        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="sections_climate", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SmartPIDThermostatOptionsFlow:
        """Return the options flow."""
        return SmartPIDThermostatOptionsFlow()


class SmartPIDThermostatOptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle options flow for Smart PID Thermostat."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._options_data: dict[str, Any] = {}

    def _get_val(self, key: str, default: Any = None) -> Any:
        """Get configuration value from options or data, searching top-level and nested sections."""
        return get_entry_setting(self.config_entry, key, default)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options step 1: Base parameters."""
        errors: dict[str, str] = {}
        curr_control_mode = self._get_val(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        curr_thermostat_type = self._get_val(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)

        active_control_mode = (
            user_input.get(CONF_CONTROL_MODE, curr_control_mode)
            if user_input is not None
            else curr_control_mode
        )
        active_thermostat_type = (
            user_input.get(CONF_THERMOSTAT_TYPE, curr_thermostat_type)
            if user_input is not None
            else curr_thermostat_type
        )

        if user_input is not None:
            errors, payload = validate_device_sensor_input(
                self.hass,
                user_input,
                active_control_mode,
                active_thermostat_type,
                exclude_entry_id=self.config_entry.entry_id,
            )
            if not errors:
                self._options_data = payload
                if active_control_mode == CONTROL_MODE_SWITCH_PWM:
                    return await self.async_step_sections_pwm()
                return await self.async_step_sections_climate()

        schema = build_device_sensor_schema(
            control_mode=active_control_mode,
            thermostat_type=active_thermostat_type,
            get_val=self._get_val,
            is_options=True,
        )
        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_sections(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Alias for async_step_sections_climate."""
        return await self.async_step_sections_climate(user_input)

    async def async_step_sections_pwm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options step 2 for switch PWM mode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, processed = process_pwm_sections_input(user_input)
            if not errors:
                new_options = {
                    **self.config_entry.options,
                    **self._options_data,
                    **processed,
                }
                return self.async_create_entry(title="", data=new_options)

        schema = build_pwm_sections_schema(self._get_val)
        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="sections_pwm", data_schema=schema, errors=errors
        )

    async def async_step_sections_climate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options step 2: Section mapping, HVAC mode, and fan settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, prepared = validate_and_prepare_climate_sections(
                user_input, self._get_val
            )
            if not errors:
                new_options = {
                    **self.config_entry.options,
                    **self._options_data,
                    **prepared,
                }
                return self.async_create_entry(title="", data=new_options)

        selected_hvac_modes = (
            self._options_data.get(
                CONF_AVAILABLE_HVAC_MODES,
                self._get_val(CONF_AVAILABLE_HVAC_MODES, DEFAULT_AVAILABLE_HVAC_MODES),
            )
            or DEFAULT_AVAILABLE_HVAC_MODES
        )
        selected_fan_modes = (
            self._options_data.get(
                CONF_AVAILABLE_FAN_MODES,
                self._get_val(CONF_AVAILABLE_FAN_MODES, DEFAULT_AVAILABLE_FAN_MODES),
            )
            or DEFAULT_AVAILABLE_FAN_MODES
        )
        thermostat_type = self._options_data.get(
            CONF_THERMOSTAT_TYPE,
            self._get_val(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE),
        )

        schema = build_climate_sections_schema(
            thermostat_type=thermostat_type,
            selected_hvac_modes=list(selected_hvac_modes),
            selected_fan_modes=list(selected_fan_modes),
            get_val=self._get_val,
        )
        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="sections_climate", data_schema=schema, errors=errors
        )
