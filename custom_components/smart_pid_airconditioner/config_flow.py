"""Config flow for PID Cooling Thermostat integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    DurationSelector,
    DurationSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    Selector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    ALL_FAN_MODES,
    ALL_HVAC_MODES,
    CONF_AVAILABLE_FAN_MODES,
    CONF_AVAILABLE_HVAC_MODES,
    CONF_CONTROL_MODE,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KEEP_ALIVE,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_MIN_TEMP,
    CONF_MAX_TEMP,
    CONF_INDOOR_SENSOR,
    CONF_KD,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_OFF_TEMP_ACTION,
    CONF_OUTDOOR_SENSOR,
    CONF_OUTPUT_SAFETY,
    CONF_PWM_DURATION,
    CONF_SAMPLE_PERIOD,
    CONF_SEC1_APPLY_TEMP,
    CONF_SEC1_APPLY_DELTA,
    CONF_SEC1_APPLY_FAN,
    CONF_SEC1_APPLY_HVAC_MODE,
    CONF_SEC1_APPLY_ACTION,
    CONF_SEC1_ACTION,
    CONF_SEC1_APPLY_MODE,
    CONF_SEC1_MODE,
    CONF_SEC1_DELTA,
    CONF_SEC1_FAN,
    CONF_SEC1_HVAC_MODE,
    CONF_SEC1_MAX,
    CONF_SEC2_APPLY_TEMP,
    CONF_SEC2_APPLY_DELTA,
    CONF_SEC2_APPLY_FAN,
    CONF_SEC2_APPLY_HVAC_MODE,
    CONF_SEC2_APPLY_ACTION,
    CONF_SEC2_ACTION,
    CONF_SEC2_APPLY_MODE,
    CONF_SEC2_MODE,
    CONF_SEC2_DELTA,
    CONF_SEC2_FAN,
    CONF_SEC2_HVAC_MODE,
    CONF_SEC2_MAX,
    CONF_SEC3_APPLY_TEMP,
    CONF_SEC3_APPLY_DELTA,
    CONF_SEC3_APPLY_FAN,
    CONF_SEC3_APPLY_HVAC_MODE,
    CONF_SEC3_APPLY_ACTION,
    CONF_SEC3_ACTION,
    CONF_SEC3_APPLY_MODE,
    CONF_SEC3_MODE,
    CONF_SEC3_DELTA,
    CONF_SEC3_FAN,
    CONF_SEC3_HVAC_MODE,
    CONF_SEC3_MAX,
    CONF_SEC4_APPLY_TEMP,
    CONF_SEC4_APPLY_DELTA,
    CONF_SEC4_APPLY_FAN,
    CONF_SEC4_APPLY_HVAC_MODE,
    CONF_SEC4_APPLY_ACTION,
    CONF_SEC4_ACTION,
    CONF_SEC4_APPLY_MODE,
    CONF_SEC4_MODE,
    CONF_SEC4_DELTA,
    CONF_SEC4_FAN,
    CONF_SEC4_HVAC_MODE,
    CONF_SYNC_SLAVE_STATE,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TEMP_STEP,
    CONF_THERMOSTAT_TYPE,
    CONTROL_MODES,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    DEFAULT_AVAILABLE_FAN_MODES,
    DEFAULT_AVAILABLE_HVAC_MODES,
    DEFAULT_CONTROL_MODE,
    DEFAULT_INITIAL_TARGET_TEMP,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MIN_OFF_TIME,
    DEFAULT_MIN_ON_TIME,
    DEFAULT_MIN_TEMP,
    DEFAULT_MAX_TEMP,
    DEFAULT_KD,
    DEFAULT_KE,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MAX_I,
    DEFAULT_OFF_TEMP_ACTION,
    DEFAULT_OUTPUT_SAFETY,
    DEFAULT_PWM_DURATION,
    DEFAULT_PWM_DURATION_SWITCH,
    DEFAULT_SAMPLE_PERIOD,
    DEFAULT_SEC1_DELTA,
    DEFAULT_SEC1_FAN,
    DEFAULT_SEC1_MAX,
    DEFAULT_SEC2_DELTA,
    DEFAULT_SEC2_FAN,
    DEFAULT_SEC2_MAX,
    DEFAULT_SEC3_DELTA,
    DEFAULT_SEC3_FAN,
    DEFAULT_SEC3_MAX,
    DEFAULT_SEC4_DELTA,
    DEFAULT_SEC4_FAN,
    DEFAULT_SEC_APPLY_TEMP,
    DEFAULT_SEC_APPLY_DELTA,
    DEFAULT_SEC_APPLY_FAN,
    DEFAULT_SEC_APPLY_HVAC_MODE,
    DEFAULT_SEC_APPLY_ACTION,
    DEFAULT_SEC_ACTION,
    DEFAULT_SEC_APPLY_MODE,
    DEFAULT_SEC_MODE,
    DEFAULT_SEC_HVAC_MODE,
    DEFAULT_SYNC_SLAVE_STATE,
    DEFAULT_TEMP_STEP,
    DEFAULT_THERMOSTAT_TYPE,
    DOMAIN,
    OFF_ACTION_SYNC_ONLY,
    OFF_ACTION_TURN_ON,
    THERMOSTAT_TYPES,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    ALL_HUMIDITY_MODES,
    DEFAULT_AVAILABLE_HUMIDITY_MODES,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HUMIDITY,
    DEFAULT_INITIAL_TARGET_HUMIDITY,
    DEFAULT_MIN_HUMIDITY,
    DEFAULT_MAX_HUMIDITY,
    DEFAULT_HUMIDITY_STEP,
    MODE_OFF,
    MODE_AUTO,
    MODE_HUMIDITY,
    MODE_DEHUMIDITY,
    MODE_FAN_ONLY,
    MODE_SILENT,
    is_humidity_type,
    is_dual_controller,
    duration_to_seconds,
    seconds_to_duration_dict,
)

_LOGGER = logging.getLogger(__name__)

FALLBACK_FAN_MODES = ["low", "medium", "high", "turbo", "auto"]


def get_fan_modes(hass, climate_entity_id: str | None) -> list[str]:
    """Fetch fan modes supported by the slave climate entity."""
    if climate_entity_id:
        state = hass.states.get(climate_entity_id)
        if state and "fan_modes" in state.attributes:
            modes = state.attributes.get("fan_modes")
            if isinstance(modes, list) and modes:
                return list(dict.fromkeys(str(m) for m in modes))
    return list(FALLBACK_FAN_MODES)


def get_hvac_modes(hass, climate_entity_id: str | None, default_modes: list[str]) -> list[str]:
    """Fetch hvac modes supported by the slave climate entity."""
    if climate_entity_id:
        state = hass.states.get(climate_entity_id)
        if state and "hvac_modes" in state.attributes:
            modes = state.attributes.get("hvac_modes")
            if isinstance(modes, list) and modes:
                return list(dict.fromkeys(str(m) for m in modes))
    return default_modes


def get_humidifier_modes(hass, humidifier_entity_id: str | None, default_modes: list[str]) -> list[str]:
    """Fetch operating modes supported by the slave humidifier entity."""
    if humidifier_entity_id:
        state = hass.states.get(humidifier_entity_id)
        if state and "available_modes" in state.attributes:
            modes = state.attributes.get("available_modes")
            if isinstance(modes, list) and modes:
                return list(dict.fromkeys(str(m) for m in modes))
    return default_modes


MODE_LABELS: dict[str, str] = {
    "off": "꺼짐 (Off)",
    "auto": "자동 (Auto)",
    "cool": "냉방 (Cool)",
    "heat": "난방 (Heat)",
    "fan_only": "송풍 (Fan Only)",
    "dry": "제습 (Dry)",
    "dry_cool": "제습냉방 (Dry Cool)",
    "humidity": "가습 (Humidifier)",
    "dehumidity": "제습 (Dehumidifier)",
    "silent": "저소음 / 정음 (Silent)",
    "sleep": "수면 (Sleep)",
    "eco": "절전 (Eco)",
    "normal": "일반 (Normal)",
    "boost": "부스트 (Boost)",
    "turbo": "터보 (Turbo)",
    "heat_cool": "냉난방 (Heat/Cool)",
    "continuous": "연속 (Continuous)",
    "baby": "유아 (Baby)",
    "comfortable": "쾌적 (Comfortable)",
    "wind": "자연풍 (Wind)",
    "clean": "청정 (Clean)",
    "purify": "공기청정 (Purify)",
    "smart": "스마트 (Smart)",
    "pet": "반려동물 (Pet)",
    "ai": "AI 스마트 (AI)",
}

FAN_MODE_LABELS: dict[str, str] = {
    "auto": "자동 (Auto)",
    "low": "미풍 / 약 (Low)",
    "medium": "약풍 / 중 (Medium)",
    "high": "강풍 / 강 (High)",
    "turbo": "터보 (Turbo)",
    "silent": "저소음 (Silent)",
    "quiet": "조용 (Quiet)",
    "mid": "약풍 / 중 (Mid)",
    "middle": "약풍 / 중 (Middle)",
    "min": "미풍 / 약 (Min)",
    "max": "강풍 / 강 (Max)",
    "strong": "강 (Strong)",
    "diffuse": "확산 (Diffuse)",
}

ACTION_OPTIONS: list[SelectOptionDict] = [
    SelectOptionDict(value="on", label="켜기 (On)"),
    SelectOptionDict(value="off", label="끄기 (Off)"),
]


def format_mode_options(modes: list[str]) -> list[SelectOptionDict]:
    """Format mode options with friendly Korean/English labels."""
    return [
        SelectOptionDict(
            value=str(m),
            label=MODE_LABELS.get(str(m).lower().strip(), str(m)),
        )
        for m in modes
    ]


def format_fan_options(fan_modes: list[str]) -> list[SelectOptionDict]:
    """Format fan mode options with friendly Korean/English labels."""
    return [
        SelectOptionDict(
            value=str(f),
            label=FAN_MODE_LABELS.get(str(f).lower().strip(), str(f)),
        )
        for f in fan_modes
    ]


try:
    from homeassistant.data_entry_flow import section
except ImportError:
    section = None


class KiGridSelector(Selector):
    """Custom selector that serializes Ki and Max I as a 2-column (5:5) ha-form-grid."""

    selector_type = "grid"

    def __init__(
        self,
        ki_default: float = DEFAULT_KI,
        max_i_default: float = DEFAULT_MAX_I,
    ) -> None:
        """Initialize the Ki grid selector."""
        self._ki_default = float(ki_default)
        self._max_i_default = float(max_i_default)
        self.config = {
            CONF_KI: self._ki_default,
            CONF_MAX_I: self._max_i_default,
        }

    def serialize(self) -> dict[str, Any]:
        """Serialize into an ha-form-grid schema."""
        return {
            "type": "grid",
            "schema": [
                {
                    "name": CONF_KI,
                    "required": True,
                    "default": self._ki_default,
                    "selector": {
                        "number": {
                            "min": 0.0,
                            "max": 100.0,
                            "step": 0.001,
                            "mode": "box",
                        }
                    },
                },
                {
                    "name": CONF_MAX_I,
                    "required": False,
                    "default": self._max_i_default,
                    "selector": {
                        "number": {
                            "min": 0.0,
                            "max": 100.0,
                            "step": 1.0,
                            "mode": "box",
                            "unit_of_measurement": "%",
                        }
                    },
                },
            ],
        }

    def __call__(self, data: Any) -> dict[str, float]:
        """Validate the submitted ki_group dictionary."""
        if not isinstance(data, dict):
            raise vol.Invalid("Expected a dictionary for ki_group")
        try:
            ki_raw = data.get(CONF_KI, self._ki_default)
            ki_val = float(self._ki_default if ki_raw in (None, "") else ki_raw)
            max_i_raw = data.get(CONF_MAX_I, self._max_i_default)
            max_i_val = float(
                self._max_i_default if max_i_raw in (None, "") else max_i_raw
            )
        except (ValueError, TypeError) as err:
            raise vol.Invalid(f"Invalid numeric value in ki_group: {err}") from err
        return {
            CONF_KI: max(0.0, min(100.0, ki_val)),
            CONF_MAX_I: max(0.0, min(100.0, max_i_val)),
        }


def _flatten_sections_input(data: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten nested section dictionary into top-level key-values."""
    if not data:
        return {}
    flat = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def _build_sections_schema(
    sec1_dict: dict[Any, Any],
    sec2_dict: dict[Any, Any],
    sec3_dict: dict[Any, Any],
    sec4_dict: dict[Any, Any],
) -> vol.Schema:
    """Build section schema using native section cards if supported, or flat fallback."""
    if section is not None:
        return vol.Schema(
            {
                "section_1": section(
                    vol.Schema(sec1_dict), {"collapsed": False}
                ),
                "section_2": section(
                    vol.Schema(sec2_dict), {"collapsed": True}
                ),
                "section_3": section(
                    vol.Schema(sec3_dict), {"collapsed": True}
                ),
                "section_4": section(
                    vol.Schema(sec4_dict), {"collapsed": True}
                ),
            }
        )
    combined = {}
    combined.update(sec1_dict)
    combined.update(sec2_dict)
    combined.update(sec3_dict)
    combined.update(sec4_dict)
    return vol.Schema(combined)


class SmartPIDThermostatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart PID Thermostat."""

    VERSION = 1

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

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_device_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle step 2: Target device, sensors, and basic PID parameters."""
        errors: dict[str, str] = {}
        control_mode = self._data.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        thermostat_type = self._data.get(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)
        is_dual_heat_cool_pwm = (
            control_mode == CONTROL_MODE_SWITCH_PWM
            and thermostat_type == THERMOSTAT_TYPE_HEAT_COOL
        )
        is_dual_humidity_pwm = (
            control_mode == CONTROL_MODE_SWITCH_PWM
            and thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY
        )
        is_dual_pwm = is_dual_heat_cool_pwm or is_dual_humidity_pwm
        is_hum = is_humidity_type(thermostat_type)

        if is_hum:
            if thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                hvac_modes_default = [MODE_OFF, MODE_HUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
            elif thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                hvac_modes_default = [MODE_OFF, MODE_DEHUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
            else:
                hvac_modes_default = DEFAULT_AVAILABLE_HUMIDITY_MODES
        else:
            hvac_modes_default = DEFAULT_AVAILABLE_HVAC_MODES

        if user_input is not None:
            def_min = DEFAULT_MIN_HUMIDITY if is_hum else DEFAULT_MIN_TEMP
            def_max = DEFAULT_MAX_HUMIDITY if is_hum else DEFAULT_MAX_TEMP
            try:
                min_temp = float(user_input.get(CONF_MIN_TEMP, def_min))
                max_temp = float(user_input.get(CONF_MAX_TEMP, def_max))
                if min_temp >= max_temp:
                    errors["base"] = "min_temp_greater_than_max"
            except (ValueError, TypeError):
                errors["base"] = "min_temp_greater_than_max"

            target_dev = ""
            target_cool = ""
            target_heat = ""
            target_dehumidity = ""
            target_humidity = ""

            if is_dual_heat_cool_pwm:
                target_cool = user_input.get(CONF_TARGET_DEVICE_COOL, "")
                target_heat = user_input.get(CONF_TARGET_DEVICE_HEAT, "")

                if not target_cool:
                    errors[CONF_TARGET_DEVICE_COOL] = "target_device_cool_required"
                else:
                    domain_cool = target_cool.split(".")[0] if "." in target_cool else ""
                    if domain_cool not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE_COOL] = "target_device_cool_must_be_switch"

                if not target_heat:
                    errors[CONF_TARGET_DEVICE_HEAT] = "target_device_heat_required"
                else:
                    domain_heat = target_heat.split(".")[0] if "." in target_heat else ""
                    if domain_heat not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE_HEAT] = "target_device_heat_must_be_switch"

                target_dev = target_cool or target_heat
            elif is_dual_humidity_pwm:
                target_dehumidity = user_input.get(CONF_TARGET_DEVICE_DEHUMIDITY, "")
                target_humidity = user_input.get(CONF_TARGET_DEVICE_HUMIDITY, "")

                if not target_dehumidity:
                    errors[CONF_TARGET_DEVICE_DEHUMIDITY] = "target_device_dehumidity_required"
                else:
                    domain_dehum = target_dehumidity.split(".")[0] if "." in target_dehumidity else ""
                    if domain_dehum not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE_DEHUMIDITY] = "target_device_dehumidity_must_be_switch"

                if not target_humidity:
                    errors[CONF_TARGET_DEVICE_HUMIDITY] = "target_device_humidity_required"
                else:
                    domain_hum = target_humidity.split(".")[0] if "." in target_humidity else ""
                    if domain_hum not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE_HUMIDITY] = "target_device_humidity_must_be_switch"

                target_dev = target_dehumidity or target_humidity
            else:
                target_dev = user_input.get(CONF_TARGET_DEVICE, "")
                if not target_dev:
                    errors[CONF_TARGET_DEVICE] = "target_device_required"
                else:
                    domain = target_dev.split(".")[0] if "." in target_dev else ""
                    if control_mode == CONTROL_MODE_CLIMATE_DELTA:
                        if is_hum and domain != "humidifier":
                            errors[CONF_TARGET_DEVICE] = "target_device_must_be_humidifier"
                        elif not is_hum and domain != "climate":
                            errors[CONF_TARGET_DEVICE] = "target_device_must_be_climate"
                    elif control_mode == CONTROL_MODE_SWITCH_PWM and domain not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE] = "target_device_must_be_switch"

            if not errors:
                flat_user_input = _flatten_sections_input(user_input)
                self._data.update(flat_user_input)
                self._data[CONF_OUTDOOR_SENSOR] = user_input.get(CONF_OUTDOOR_SENSOR) or ""
                self._data[CONF_TARGET_CLIMATE] = target_dev
                self._data[CONF_TARGET_DEVICE] = target_dev

                if control_mode == CONTROL_MODE_CLIMATE_DELTA:
                    if is_hum:
                        self._data[CONF_AVAILABLE_HVAC_MODES] = get_humidifier_modes(
                            self.hass, target_dev, hvac_modes_default
                        )
                        self._data[CONF_AVAILABLE_FAN_MODES] = []
                    else:
                        self._data[CONF_AVAILABLE_HVAC_MODES] = get_hvac_modes(
                            self.hass, target_dev, hvac_modes_default
                        )
                        self._data[CONF_AVAILABLE_FAN_MODES] = get_fan_modes(
                            self.hass, target_dev
                        )

                if is_dual_heat_cool_pwm:
                    self._data[CONF_TARGET_DEVICE_COOL] = target_cool
                    self._data[CONF_TARGET_DEVICE_HEAT] = target_heat
                    self._data[CONF_TARGET_DEVICE_DEHUMIDITY] = ""
                    self._data[CONF_TARGET_DEVICE_HUMIDITY] = ""
                elif is_dual_humidity_pwm:
                    self._data[CONF_TARGET_DEVICE_COOL] = ""
                    self._data[CONF_TARGET_DEVICE_HEAT] = ""
                    self._data[CONF_TARGET_DEVICE_DEHUMIDITY] = target_dehumidity
                    self._data[CONF_TARGET_DEVICE_HUMIDITY] = target_humidity
                else:
                    self._data[CONF_TARGET_DEVICE_COOL] = ""
                    self._data[CONF_TARGET_DEVICE_HEAT] = ""
                    self._data[CONF_TARGET_DEVICE_DEHUMIDITY] = ""
                    self._data[CONF_TARGET_DEVICE_HUMIDITY] = ""

                if control_mode == CONTROL_MODE_SWITCH_PWM:
                    return await self.async_step_sections_pwm()
                return await self.async_step_sections_climate()

        schema_dict = {}
        if is_dual_heat_cool_pwm:
            schema_dict[
                vol.Required(CONF_TARGET_DEVICE_COOL)
            ] = EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
            schema_dict[
                vol.Required(CONF_TARGET_DEVICE_HEAT)
            ] = EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
        elif is_dual_humidity_pwm:
            schema_dict[
                vol.Required(CONF_TARGET_DEVICE_DEHUMIDITY)
            ] = EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
            schema_dict[
                vol.Required(CONF_TARGET_DEVICE_HUMIDITY)
            ] = EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
        else:
            if control_mode == CONTROL_MODE_CLIMATE_DELTA:
                domain_choice = "humidifier" if is_hum else "climate"
            else:
                domain_choice = ["switch", "input_boolean"]
            schema_dict[
                vol.Required(CONF_TARGET_DEVICE)
            ] = EntitySelector(
                EntitySelectorConfig(domain=domain_choice)
            )

        if is_hum:
            indoor_selector = EntitySelector(
                EntitySelectorConfig(
                    domain="sensor",
                    device_class=str(SensorDeviceClass.HUMIDITY.value),
                )
            )
            outdoor_selector = EntitySelector(
                EntitySelectorConfig(
                    filter=[
                        {
                            "domain": "sensor",
                            "device_class": str(SensorDeviceClass.HUMIDITY.value),
                        },
                        {
                            "domain": "weather",
                        },
                    ]
                )
            )
            if thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                hvac_modes_default = [MODE_OFF, MODE_HUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
            elif thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                hvac_modes_default = [MODE_OFF, MODE_DEHUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
            else:
                hvac_modes_default = DEFAULT_AVAILABLE_HUMIDITY_MODES
            hvac_options = ALL_HUMIDITY_MODES
            initial_target_default = DEFAULT_INITIAL_TARGET_HUMIDITY
            min_target_default = DEFAULT_MIN_HUMIDITY
            max_target_default = DEFAULT_MAX_HUMIDITY
            step_default = DEFAULT_HUMIDITY_STEP
            step_options = ["0.5", "1.0", "2.0"]
            unit_meas = "%"
        else:
            indoor_selector = EntitySelector(
                EntitySelectorConfig(
                    domain="sensor",
                    device_class=str(SensorDeviceClass.TEMPERATURE.value),
                )
            )
            outdoor_selector = EntitySelector(
                EntitySelectorConfig(
                    filter=[
                        {
                            "domain": "sensor",
                            "device_class": str(SensorDeviceClass.TEMPERATURE.value),
                        },
                        {
                            "domain": "weather",
                        },
                    ]
                )
            )
            hvac_modes_default = DEFAULT_AVAILABLE_HVAC_MODES
            hvac_options = ALL_HVAC_MODES
            initial_target_default = DEFAULT_INITIAL_TARGET_TEMP
            min_target_default = DEFAULT_MIN_TEMP
            max_target_default = DEFAULT_MAX_TEMP
            step_default = DEFAULT_TEMP_STEP
            step_options = ["0.1", "0.5", "1.0"]
            unit_meas = "°C"

        schema_dict.update(
            {
                vol.Required(CONF_INDOOR_SENSOR): indoor_selector,
                vol.Optional(CONF_OUTDOOR_SENSOR): outdoor_selector,
            }
        )

        if control_mode == CONTROL_MODE_SWITCH_PWM:
            schema_dict.update(
                {
                    vol.Required(
                        CONF_AVAILABLE_HVAC_MODES, default=hvac_modes_default
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=hvac_options,
                            multiple=True,
                            translation_key="available_hvac_modes",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_AVAILABLE_FAN_MODES, default=DEFAULT_AVAILABLE_FAN_MODES
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=ALL_FAN_MODES,
                            multiple=True,
                            translation_key="available_fan_modes",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )

        schema_dict.update(
            {
                vol.Required(
                    CONF_INITIAL_TARGET_TEMP, default=initial_target_default
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10.0 if not is_hum else 10.0,
                        max=35.0 if not is_hum else 100.0,
                        step=0.5 if not is_hum else 1.0,
                        unit_of_measurement=unit_meas,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_MIN_TEMP, default=min_target_default
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10.0 if not is_hum else 10.0,
                        max=25.0 if not is_hum else 50.0,
                        step=0.5 if not is_hum else 1.0,
                        unit_of_measurement=unit_meas,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_MAX_TEMP, default=max_target_default
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=25.0 if not is_hum else 50.0,
                        max=35.0 if not is_hum else 100.0,
                        step=0.5 if not is_hum else 1.0,
                        unit_of_measurement=unit_meas,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_TEMP_STEP, default=step_default
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=step_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_OFF_TEMP_ACTION, default=DEFAULT_OFF_TEMP_ACTION
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[OFF_ACTION_SYNC_ONLY, OFF_ACTION_TURN_ON],
                        translation_key=CONF_OFF_TEMP_ACTION,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SYNC_SLAVE_STATE, default=DEFAULT_SYNC_SLAVE_STATE
                ): BooleanSelector(),
                vol.Required(CONF_KP, default=DEFAULT_KP): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=100.0,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    "ki_group",
                    default={CONF_KI: DEFAULT_KI, CONF_MAX_I: DEFAULT_MAX_I},
                ): KiGridSelector(DEFAULT_KI, DEFAULT_MAX_I),
                vol.Required(CONF_KD, default=DEFAULT_KD): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=100.0,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_KE, default=DEFAULT_KE): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=5.0,
                        step=0.05,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_SAMPLE_PERIOD, default=DEFAULT_SAMPLE_PERIOD
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10,
                        max=600,
                        step=1,
                        unit_of_measurement="초",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        schema = vol.Schema(schema_dict)
        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(
            step_id="device_sensor",
            data_schema=schema,
            errors=errors,
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
            pwm_dur = duration_to_seconds(user_input.get(CONF_PWM_DURATION), DEFAULT_PWM_DURATION_SWITCH)
            min_on = duration_to_seconds(user_input.get(CONF_MIN_ON_TIME), DEFAULT_MIN_ON_TIME)
            min_off = duration_to_seconds(user_input.get(CONF_MIN_OFF_TIME), DEFAULT_MIN_OFF_TIME)
            keep_alive = duration_to_seconds(user_input.get(CONF_KEEP_ALIVE), DEFAULT_KEEP_ALIVE)
            output_safety = float(user_input.get(CONF_OUTPUT_SAFETY, DEFAULT_OUTPUT_SAFETY))

            if min_on + min_off > pwm_dur:
                errors["base"] = "min_times_exceed_pwm_duration"

            if not errors:
                processed_input = {
                    **user_input,
                    CONF_PWM_DURATION: pwm_dur,
                    CONF_MIN_ON_TIME: min_on,
                    CONF_MIN_OFF_TIME: min_off,
                    CONF_KEEP_ALIVE: keep_alive,
                    CONF_OUTPUT_SAFETY: output_safety,
                }
                full_data = {**self._data, **processed_input}
                target_dev = full_data.get(CONF_TARGET_DEVICE, full_data.get(CONF_TARGET_CLIMATE, "Thermostat"))
                thermostat_type = full_data.get(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)
                if thermostat_type == THERMOSTAT_TYPE_HEATER:
                    type_label = "Heater"
                elif thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                    type_label = "Heat/Cool"
                elif thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                    type_label = "Dehumidifier"
                elif thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                    type_label = "Humidifier"
                elif thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                    type_label = "Humidity/Dehumidity"
                else:
                    type_label = "Cooler"

                control_mode = full_data.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
                if control_mode == CONTROL_MODE_SWITCH_PWM and thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                    cool_dev = full_data.get(CONF_TARGET_DEVICE_COOL, "")
                    heat_dev = full_data.get(CONF_TARGET_DEVICE_HEAT, "")
                    dev_label = f"{cool_dev} / {heat_dev}" if (cool_dev and heat_dev) else (cool_dev or heat_dev or target_dev)
                elif control_mode == CONTROL_MODE_SWITCH_PWM and thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                    dehum_dev = full_data.get(CONF_TARGET_DEVICE_DEHUMIDITY, "")
                    hum_dev = full_data.get(CONF_TARGET_DEVICE_HUMIDITY, "")
                    dev_label = f"{dehum_dev} / {hum_dev}" if (dehum_dev and hum_dev) else (dehum_dev or hum_dev or target_dev)
                else:
                    dev_label = target_dev

                return self.async_create_entry(
                    title=f"Smart PID PWM {type_label} ({dev_label})",
                    data=full_data,
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PWM_DURATION,
                    default=seconds_to_duration_dict(DEFAULT_PWM_DURATION_SWITCH),
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
                vol.Required(
                    CONF_MIN_ON_TIME,
                    default=seconds_to_duration_dict(DEFAULT_MIN_ON_TIME),
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
                vol.Required(
                    CONF_MIN_OFF_TIME,
                    default=seconds_to_duration_dict(DEFAULT_MIN_OFF_TIME),
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
                vol.Required(
                    CONF_KEEP_ALIVE,
                    default=seconds_to_duration_dict(DEFAULT_KEEP_ALIVE),
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
                vol.Required(
                    CONF_OUTPUT_SAFETY, default=DEFAULT_OUTPUT_SAFETY
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=50.0,
                        step=1.0,
                        unit_of_measurement="%",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(
            step_id="sections_pwm",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_sections_climate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle step 2: Section mapping, HVAC mode, and fan mode settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            flat_input = _flatten_sections_input(user_input)
            try:
                sec1 = float(flat_input.get(CONF_SEC1_MAX, DEFAULT_SEC1_MAX))
                sec2 = float(flat_input.get(CONF_SEC2_MAX, DEFAULT_SEC2_MAX))
                sec3 = float(flat_input.get(CONF_SEC3_MAX, DEFAULT_SEC3_MAX))
                if not (sec1 < sec2 < sec3):
                    errors["base"] = "invalid_section_order"
            except (ValueError, TypeError):
                errors["base"] = "invalid_section_order"

            if not errors:
                for i in (1, 2, 3, 4):
                    apply_temp_val = flat_input.get(f"sec{i}_apply_temp", DEFAULT_SEC_APPLY_TEMP)
                    flat_input[f"sec{i}_apply_delta"] = apply_temp_val
                full_data = {**self._data, **flat_input}
                if isinstance(user_input, dict):
                    for sec in ("section_1", "section_2", "section_3", "section_4"):
                        if sec in user_input and isinstance(user_input[sec], dict):
                            full_data[sec] = dict(user_input[sec])
                            sec_idx = sec.split("_")[1]
                            full_data[sec][f"sec{sec_idx}_apply_delta"] = full_data[sec].get(
                                f"sec{sec_idx}_apply_temp", DEFAULT_SEC_APPLY_TEMP
                            )
                target_climate = full_data.get(CONF_TARGET_CLIMATE, "Thermostat")
                thermostat_type = full_data.get(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)
                if thermostat_type == THERMOSTAT_TYPE_HEATER:
                    type_label = "Heater"
                elif thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
                    type_label = "Heat/Cool"
                elif thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                    type_label = "Dehumidifier"
                elif thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                    type_label = "Humidifier"
                elif thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY:
                    type_label = "Humidity/Dehumidity"
                else:
                    type_label = "Cooler"
                return self.async_create_entry(
                    title=f"Smart PID {type_label} ({target_climate})",
                    data=full_data,
                )

        # Filter options based on step 1 user selections
        selected_hvac_modes = self._data.get(
            CONF_AVAILABLE_HVAC_MODES, DEFAULT_AVAILABLE_HVAC_MODES
        )
        if not selected_hvac_modes:
            selected_hvac_modes = DEFAULT_AVAILABLE_HVAC_MODES

        selected_fan_modes = self._data.get(
            CONF_AVAILABLE_FAN_MODES, DEFAULT_AVAILABLE_FAN_MODES
        )
        if not selected_fan_modes:
            selected_fan_modes = DEFAULT_AVAILABLE_FAN_MODES

        thermostat_type = self._data.get(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)
        is_hum = is_humidity_type(thermostat_type)
        is_heater = (thermostat_type == THERMOSTAT_TYPE_HEATER or thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER)

        # Section HVAC options: only the modes selected/supported
        section_hvac_options = [
            m for m in selected_hvac_modes if m != "follow"
        ]
        if not section_hvac_options:
            section_hvac_options = [MODE_DEHUMIDITY if is_hum else HVACMode.COOL.value]

        delta_unit = "%" if is_hum else "°C"
        delta_min = -20.0 if is_hum else -5.0
        delta_max = 20.0 if is_hum else 5.0
        delta_step = 0.5

        if is_hum:
            default_sec1_delta = 2.0 if is_heater else -2.0
            default_sec2_delta = 5.0 if is_heater else -5.0
            default_sec3_delta = 10.0 if is_heater else -10.0
            default_sec4_delta = 15.0 if is_heater else -15.0
        else:
            default_sec1_delta = 1.0 if is_heater else DEFAULT_SEC1_DELTA
            default_sec2_delta = 2.0 if is_heater else DEFAULT_SEC2_DELTA
            default_sec3_delta = 3.0 if is_heater else DEFAULT_SEC3_DELTA
            default_sec4_delta = 4.0 if is_heater else DEFAULT_SEC4_DELTA

        if is_hum:
            if thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER and MODE_HUMIDITY in section_hvac_options:
                default_hvac = MODE_HUMIDITY
            elif thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER and MODE_DEHUMIDITY in section_hvac_options:
                default_hvac = MODE_DEHUMIDITY
            elif MODE_AUTO in section_hvac_options:
                default_hvac = MODE_AUTO
            else:
                default_hvac = section_hvac_options[0]
        else:
            if is_heater and HVACMode.HEAT.value in section_hvac_options:
                default_hvac = HVACMode.HEAT.value
            elif not is_heater and HVACMode.COOL.value in section_hvac_options:
                default_hvac = HVACMode.COOL.value
            else:
                default_hvac = section_hvac_options[0]

        # Section fan options: only the selected fan modes
        fan_mode_options = list(selected_fan_modes)

        default_fan1 = (
            "low" if "low" in fan_mode_options else (fan_mode_options[0] if fan_mode_options else "auto")
        )
        default_fan2 = (
            "medium"
            if "medium" in fan_mode_options
            else (fan_mode_options[min(1, len(fan_mode_options) - 1)] if fan_mode_options else "auto")
        )
        default_fan3 = (
            "high" if "high" in fan_mode_options else (fan_mode_options[-1] if fan_mode_options else "auto")
        )
        default_fan4 = (
            "turbo"
            if "turbo" in fan_mode_options
            else ("high" if "high" in fan_mode_options else (fan_mode_options[-1] if fan_mode_options else "auto"))
        )

        sec1_dict = {
            vol.Required(CONF_SEC1_MAX, default=DEFAULT_SEC1_MAX): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=100.0,
                    step=1.0,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEC1_APPLY_TEMP, default=DEFAULT_SEC_APPLY_TEMP
            ): BooleanSelector(),
            vol.Required(
                CONF_SEC1_DELTA, default=default_sec1_delta
            ): NumberSelector(
                NumberSelectorConfig(
                    min=delta_min,
                    max=delta_max,
                    step=delta_step,
                    unit_of_measurement=delta_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if is_hum:
            sec1_dict[vol.Required(CONF_SEC1_APPLY_ACTION, default=DEFAULT_SEC_APPLY_ACTION)] = BooleanSelector()
            sec1_dict[vol.Required(CONF_SEC1_ACTION, default=DEFAULT_SEC_ACTION)] = SelectSelector(
                SelectSelectorConfig(
                    options=ACTION_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec1_dict[vol.Required(CONF_SEC1_APPLY_MODE, default=DEFAULT_SEC_APPLY_MODE)] = BooleanSelector()
            sec1_dict[vol.Required(CONF_SEC1_MODE, default=default_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec1_dict[vol.Required(CONF_SEC1_APPLY_FAN, default=DEFAULT_SEC_APPLY_FAN)] = BooleanSelector()
            sec1_dict[vol.Required(CONF_SEC1_FAN, default=default_fan1)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec1_dict[vol.Required(CONF_SEC1_APPLY_HVAC_MODE, default=DEFAULT_SEC_APPLY_HVAC_MODE)] = BooleanSelector()
            sec1_dict[vol.Required(CONF_SEC1_HVAC_MODE, default=default_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        sec2_dict = {
            vol.Required(CONF_SEC2_MAX, default=DEFAULT_SEC2_MAX): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=100.0,
                    step=1.0,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEC2_APPLY_TEMP, default=DEFAULT_SEC_APPLY_TEMP
            ): BooleanSelector(),
            vol.Required(
                CONF_SEC2_DELTA, default=default_sec2_delta
            ): NumberSelector(
                NumberSelectorConfig(
                    min=delta_min,
                    max=delta_max,
                    step=delta_step,
                    unit_of_measurement=delta_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if is_hum:
            sec2_dict[vol.Required(CONF_SEC2_APPLY_ACTION, default=DEFAULT_SEC_APPLY_ACTION)] = BooleanSelector()
            sec2_dict[vol.Required(CONF_SEC2_ACTION, default=DEFAULT_SEC_ACTION)] = SelectSelector(
                SelectSelectorConfig(
                    options=ACTION_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec2_dict[vol.Required(CONF_SEC2_APPLY_MODE, default=DEFAULT_SEC_APPLY_MODE)] = BooleanSelector()
            sec2_dict[vol.Required(CONF_SEC2_MODE, default=default_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec2_dict[vol.Required(CONF_SEC2_APPLY_FAN, default=DEFAULT_SEC_APPLY_FAN)] = BooleanSelector()
            sec2_dict[vol.Required(CONF_SEC2_FAN, default=default_fan2)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec2_dict[vol.Required(CONF_SEC2_APPLY_HVAC_MODE, default=DEFAULT_SEC_APPLY_HVAC_MODE)] = BooleanSelector()
            sec2_dict[vol.Required(CONF_SEC2_HVAC_MODE, default=default_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        sec3_dict = {
            vol.Required(CONF_SEC3_MAX, default=DEFAULT_SEC3_MAX): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=100.0,
                    step=1.0,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEC3_APPLY_TEMP, default=DEFAULT_SEC_APPLY_TEMP
            ): BooleanSelector(),
            vol.Required(
                CONF_SEC3_DELTA, default=default_sec3_delta
            ): NumberSelector(
                NumberSelectorConfig(
                    min=delta_min,
                    max=delta_max,
                    step=delta_step,
                    unit_of_measurement=delta_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if is_hum:
            sec3_dict[vol.Required(CONF_SEC3_APPLY_ACTION, default=DEFAULT_SEC_APPLY_ACTION)] = BooleanSelector()
            sec3_dict[vol.Required(CONF_SEC3_ACTION, default=DEFAULT_SEC_ACTION)] = SelectSelector(
                SelectSelectorConfig(
                    options=ACTION_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec3_dict[vol.Required(CONF_SEC3_APPLY_MODE, default=DEFAULT_SEC_APPLY_MODE)] = BooleanSelector()
            sec3_dict[vol.Required(CONF_SEC3_MODE, default=default_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec3_dict[vol.Required(CONF_SEC3_APPLY_FAN, default=DEFAULT_SEC_APPLY_FAN)] = BooleanSelector()
            sec3_dict[vol.Required(CONF_SEC3_FAN, default=default_fan3)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec3_dict[vol.Required(CONF_SEC3_APPLY_HVAC_MODE, default=DEFAULT_SEC_APPLY_HVAC_MODE)] = BooleanSelector()
            sec3_dict[vol.Required(CONF_SEC3_HVAC_MODE, default=default_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        sec4_dict = {
            vol.Required(
                CONF_SEC4_APPLY_TEMP, default=DEFAULT_SEC_APPLY_TEMP
            ): BooleanSelector(),
            vol.Required(
                CONF_SEC4_DELTA, default=default_sec4_delta
            ): NumberSelector(
                NumberSelectorConfig(
                    min=delta_min,
                    max=delta_max,
                    step=delta_step,
                    unit_of_measurement=delta_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if is_hum:
            sec4_dict[vol.Required(CONF_SEC4_APPLY_ACTION, default=DEFAULT_SEC_APPLY_ACTION)] = BooleanSelector()
            sec4_dict[vol.Required(CONF_SEC4_ACTION, default=DEFAULT_SEC_ACTION)] = SelectSelector(
                SelectSelectorConfig(
                    options=ACTION_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec4_dict[vol.Required(CONF_SEC4_APPLY_MODE, default=DEFAULT_SEC_APPLY_MODE)] = BooleanSelector()
            sec4_dict[vol.Required(CONF_SEC4_MODE, default=default_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec4_dict[vol.Required(CONF_SEC4_APPLY_FAN, default=DEFAULT_SEC_APPLY_FAN)] = BooleanSelector()
            sec4_dict[vol.Required(CONF_SEC4_FAN, default=default_fan4)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec4_dict[vol.Required(CONF_SEC4_APPLY_HVAC_MODE, default=DEFAULT_SEC_APPLY_HVAC_MODE)] = BooleanSelector()
            sec4_dict[vol.Required(CONF_SEC4_HVAC_MODE, default=default_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        schema = _build_sections_schema(sec1_dict, sec2_dict, sec3_dict, sec4_dict)

        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(
            step_id="sections_climate",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get options flow for this handler."""
        return SmartPIDThermostatOptionsFlow(config_entry)


class SmartPIDThermostatOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Smart PID Thermostat."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._options_data: dict[str, Any] = {}

    def _get_val(self, key: str, default: Any = None) -> Any:
        """Get configuration value from options or data, searching top-level and nested sections."""
        val = self._config_entry.options.get(key)
        if val is not None:
            return val
        for sec in ("ki_group", "section_1", "section_2", "section_3", "section_4"):
            sec_dict = self._config_entry.options.get(sec)
            if isinstance(sec_dict, dict):
                sec_val = sec_dict.get(key)
                if sec_val is not None:
                    return sec_val
        val = self._config_entry.data.get(key)
        if val is not None:
            return val
        for sec in ("ki_group", "section_1", "section_2", "section_3", "section_4"):
            sec_dict = self._config_entry.data.get(sec)
            if isinstance(sec_dict, dict):
                sec_val = sec_dict.get(key)
                if sec_val is not None:
                    return sec_val
        return default

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
        is_dual_heat_cool_pwm = (
            active_control_mode == CONTROL_MODE_SWITCH_PWM
            and active_thermostat_type == THERMOSTAT_TYPE_HEAT_COOL
        )
        is_dual_humidity_pwm = (
            active_control_mode == CONTROL_MODE_SWITCH_PWM
            and active_thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY
        )
        is_dual_pwm = is_dual_heat_cool_pwm or is_dual_humidity_pwm
        is_hum = is_humidity_type(active_thermostat_type)

        if is_hum:
            if active_thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                def_modes = [MODE_OFF, MODE_HUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
            elif active_thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                def_modes = [MODE_OFF, MODE_DEHUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
            else:
                def_modes = DEFAULT_AVAILABLE_HUMIDITY_MODES
            hvac_modes_default = self._get_val(CONF_AVAILABLE_HVAC_MODES, def_modes)
        else:
            hvac_modes_default = self._get_val(
                CONF_AVAILABLE_HVAC_MODES, DEFAULT_AVAILABLE_HVAC_MODES
            )

        if user_input is not None:
            def_min = DEFAULT_MIN_HUMIDITY if is_hum else DEFAULT_MIN_TEMP
            def_max = DEFAULT_MAX_HUMIDITY if is_hum else DEFAULT_MAX_TEMP
            try:
                min_temp = float(user_input.get(CONF_MIN_TEMP, def_min))
                max_temp = float(user_input.get(CONF_MAX_TEMP, def_max))
                if min_temp >= max_temp:
                    errors["base"] = "min_temp_greater_than_max"
            except (ValueError, TypeError):
                errors["base"] = "min_temp_greater_than_max"

            target_dev = ""
            target_cool = ""
            target_heat = ""
            target_dehumidity = ""
            target_humidity = ""

            if is_dual_heat_cool_pwm:
                target_cool = user_input.get(CONF_TARGET_DEVICE_COOL, "")
                target_heat = user_input.get(CONF_TARGET_DEVICE_HEAT, "")

                if not target_cool:
                    errors[CONF_TARGET_DEVICE_COOL] = "target_device_cool_required"
                else:
                    domain_cool = target_cool.split(".")[0] if "." in target_cool else ""
                    if domain_cool not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE_COOL] = "target_device_cool_must_be_switch"

                if not target_heat:
                    errors[CONF_TARGET_DEVICE_HEAT] = "target_device_heat_required"
                else:
                    domain_heat = target_heat.split(".")[0] if "." in target_heat else ""
                    if domain_heat not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE_HEAT] = "target_device_heat_must_be_switch"

                target_dev = target_cool or target_heat
            elif is_dual_humidity_pwm:
                target_dehumidity = user_input.get(CONF_TARGET_DEVICE_DEHUMIDITY, "")
                target_humidity = user_input.get(CONF_TARGET_DEVICE_HUMIDITY, "")

                if not target_dehumidity:
                    errors[CONF_TARGET_DEVICE_DEHUMIDITY] = "target_device_dehumidity_required"
                else:
                    domain_dehum = target_dehumidity.split(".")[0] if "." in target_dehumidity else ""
                    if domain_dehum not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE_DEHUMIDITY] = "target_device_dehumidity_must_be_switch"

                if not target_humidity:
                    errors[CONF_TARGET_DEVICE_HUMIDITY] = "target_device_humidity_required"
                else:
                    domain_hum = target_humidity.split(".")[0] if "." in target_humidity else ""
                    if domain_hum not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE_HUMIDITY] = "target_device_humidity_must_be_switch"

                target_dev = target_dehumidity or target_humidity
            else:
                target_dev = user_input.get(CONF_TARGET_DEVICE, "")
                if not target_dev:
                    errors[CONF_TARGET_DEVICE] = "target_device_required"
                else:
                    domain = target_dev.split(".")[0] if "." in target_dev else ""
                    if active_control_mode == CONTROL_MODE_CLIMATE_DELTA:
                        if is_hum and domain != "humidifier":
                            errors[CONF_TARGET_DEVICE] = "target_device_must_be_humidifier"
                        elif not is_hum and domain != "climate":
                            errors[CONF_TARGET_DEVICE] = "target_device_must_be_climate"
                    elif active_control_mode == CONTROL_MODE_SWITCH_PWM and domain not in ("switch", "input_boolean"):
                        errors[CONF_TARGET_DEVICE] = "target_device_must_be_switch"

            if not errors:
                flat_user_input = _flatten_sections_input(user_input)
                self._options_data = dict(flat_user_input)
                self._options_data[CONF_OUTDOOR_SENSOR] = user_input.get(CONF_OUTDOOR_SENSOR) or ""
                self._options_data[CONF_TARGET_CLIMATE] = target_dev
                self._options_data[CONF_TARGET_DEVICE] = target_dev

                if active_control_mode == CONTROL_MODE_CLIMATE_DELTA:
                    if is_hum:
                        self._options_data[CONF_AVAILABLE_HVAC_MODES] = get_humidifier_modes(
                            self.hass, target_dev, hvac_modes_default
                        )
                        self._options_data[CONF_AVAILABLE_FAN_MODES] = []
                    else:
                        self._options_data[CONF_AVAILABLE_HVAC_MODES] = get_hvac_modes(
                            self.hass, target_dev, hvac_modes_default
                        )
                        self._options_data[CONF_AVAILABLE_FAN_MODES] = get_fan_modes(
                            self.hass, target_dev
                        )

                if is_dual_heat_cool_pwm:
                    self._options_data[CONF_TARGET_DEVICE_COOL] = target_cool
                    self._options_data[CONF_TARGET_DEVICE_HEAT] = target_heat
                    self._options_data[CONF_TARGET_DEVICE_DEHUMIDITY] = ""
                    self._options_data[CONF_TARGET_DEVICE_HUMIDITY] = ""
                elif is_dual_humidity_pwm:
                    self._options_data[CONF_TARGET_DEVICE_COOL] = ""
                    self._options_data[CONF_TARGET_DEVICE_HEAT] = ""
                    self._options_data[CONF_TARGET_DEVICE_DEHUMIDITY] = target_dehumidity
                    self._options_data[CONF_TARGET_DEVICE_HUMIDITY] = target_humidity
                else:
                    self._options_data[CONF_TARGET_DEVICE_COOL] = ""
                    self._options_data[CONF_TARGET_DEVICE_HEAT] = ""
                    self._options_data[CONF_TARGET_DEVICE_DEHUMIDITY] = ""
                    self._options_data[CONF_TARGET_DEVICE_HUMIDITY] = ""

                if active_control_mode == CONTROL_MODE_SWITCH_PWM:
                    return await self.async_step_sections_pwm()
                return await self.async_step_sections_climate()

        schema_dict = {
            vol.Required(
                CONF_CONTROL_MODE,
                default=active_control_mode,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=CONTROL_MODES,
                    translation_key="control_mode",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_THERMOSTAT_TYPE,
                default=active_thermostat_type,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=THERMOSTAT_TYPES,
                    translation_key="thermostat_type",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }

        if is_dual_heat_cool_pwm:
            schema_dict[
                vol.Required(
                    CONF_TARGET_DEVICE_COOL,
                    default=self._get_val(CONF_TARGET_DEVICE_COOL, ""),
                )
            ] = EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
            schema_dict[
                vol.Required(
                    CONF_TARGET_DEVICE_HEAT,
                    default=self._get_val(CONF_TARGET_DEVICE_HEAT, ""),
                )
            ] = EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
        elif is_dual_humidity_pwm:
            schema_dict[
                vol.Required(
                    CONF_TARGET_DEVICE_DEHUMIDITY,
                    default=self._get_val(CONF_TARGET_DEVICE_DEHUMIDITY, ""),
                )
            ] = EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
            schema_dict[
                vol.Required(
                    CONF_TARGET_DEVICE_HUMIDITY,
                    default=self._get_val(CONF_TARGET_DEVICE_HUMIDITY, ""),
                )
            ] = EntitySelector(
                EntitySelectorConfig(domain=["switch", "input_boolean"])
            )
        else:
            if active_control_mode == CONTROL_MODE_CLIMATE_DELTA:
                domain_choice = "humidifier" if is_hum else "climate"
            else:
                domain_choice = ["switch", "input_boolean"]
            schema_dict[
                vol.Required(
                    CONF_TARGET_DEVICE,
                    default=self._get_val(
                        CONF_TARGET_DEVICE, self._get_val(CONF_TARGET_CLIMATE, "")
                    ),
                )
            ] = EntitySelector(
                EntitySelectorConfig(domain=domain_choice)
            )

        if is_hum:
            indoor_selector = EntitySelector(
                EntitySelectorConfig(
                    domain="sensor",
                    device_class=str(SensorDeviceClass.HUMIDITY.value),
                )
            )
            outdoor_selector = EntitySelector(
                EntitySelectorConfig(
                    filter=[
                        {
                            "domain": "sensor",
                            "device_class": str(SensorDeviceClass.HUMIDITY.value),
                        },
                        {
                            "domain": "weather",
                        },
                    ]
                )
            )
            if active_thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
                def_modes = [MODE_OFF, MODE_HUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
            elif active_thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
                def_modes = [MODE_OFF, MODE_DEHUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
            else:
                def_modes = DEFAULT_AVAILABLE_HUMIDITY_MODES
            hvac_modes_default = self._get_val(CONF_AVAILABLE_HVAC_MODES, def_modes)
            hvac_options = ALL_HUMIDITY_MODES
            min_target_default = self._get_val(CONF_MIN_TEMP, DEFAULT_MIN_HUMIDITY)
            max_target_default = self._get_val(CONF_MAX_TEMP, DEFAULT_MAX_HUMIDITY)
            step_default = self._get_val(CONF_TEMP_STEP, DEFAULT_HUMIDITY_STEP)
            step_options = ["0.5", "1.0", "2.0"]
            unit_meas = "%"
        else:
            indoor_selector = EntitySelector(
                EntitySelectorConfig(
                    domain="sensor",
                    device_class=str(SensorDeviceClass.TEMPERATURE.value),
                )
            )
            outdoor_selector = EntitySelector(
                EntitySelectorConfig(
                    filter=[
                        {
                            "domain": "sensor",
                            "device_class": str(SensorDeviceClass.TEMPERATURE.value),
                        },
                        {
                            "domain": "weather",
                        },
                    ]
                )
            )
            hvac_modes_default = self._get_val(
                CONF_AVAILABLE_HVAC_MODES, DEFAULT_AVAILABLE_HVAC_MODES
            )
            hvac_options = ALL_HVAC_MODES
            min_target_default = self._get_val(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
            max_target_default = self._get_val(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
            step_default = self._get_val(CONF_TEMP_STEP, DEFAULT_TEMP_STEP)
            step_options = ["0.1", "0.5", "1.0"]
            unit_meas = "°C"

        outdoor_val = self._get_val(CONF_OUTDOOR_SENSOR, "")
        outdoor_field = (
            vol.Optional(
                CONF_OUTDOOR_SENSOR,
                description={"suggested_value": outdoor_val},
            )
            if outdoor_val
            else vol.Optional(CONF_OUTDOOR_SENSOR)
        )

        schema_dict.update(
            {
                vol.Required(
                    CONF_INDOOR_SENSOR,
                    default=self._get_val(CONF_INDOOR_SENSOR, ""),
                ): indoor_selector,
                outdoor_field: outdoor_selector,
            }
        )

        if active_control_mode == CONTROL_MODE_SWITCH_PWM:
            schema_dict.update(
                {
                    vol.Required(
                        CONF_AVAILABLE_HVAC_MODES,
                        default=hvac_modes_default,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=hvac_options,
                            multiple=True,
                            translation_key="available_hvac_modes",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_AVAILABLE_FAN_MODES,
                        default=self._get_val(
                            CONF_AVAILABLE_FAN_MODES, DEFAULT_AVAILABLE_FAN_MODES
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=ALL_FAN_MODES,
                            multiple=True,
                            translation_key="available_fan_modes",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )

        schema_dict.update(
            {
                vol.Required(
                    CONF_MIN_TEMP,
                    default=min_target_default,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10.0 if not is_hum else 10.0,
                        max=25.0 if not is_hum else 50.0,
                        step=0.5 if not is_hum else 1.0,
                        unit_of_measurement=unit_meas,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_MAX_TEMP,
                    default=max_target_default,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=25.0 if not is_hum else 50.0,
                        max=35.0 if not is_hum else 100.0,
                        step=0.5 if not is_hum else 1.0,
                        unit_of_measurement=unit_meas,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_TEMP_STEP,
                    default=step_default,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=step_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_OFF_TEMP_ACTION,
                    default=self._get_val(
                        CONF_OFF_TEMP_ACTION, DEFAULT_OFF_TEMP_ACTION
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[OFF_ACTION_SYNC_ONLY, OFF_ACTION_TURN_ON],
                        translation_key=CONF_OFF_TEMP_ACTION,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SYNC_SLAVE_STATE,
                    default=self._get_val(
                        CONF_SYNC_SLAVE_STATE, DEFAULT_SYNC_SLAVE_STATE
                    ),
                ): BooleanSelector(),
                vol.Required(
                    CONF_KP,
                    default=self._get_val(CONF_KP, DEFAULT_KP),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=100.0,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    "ki_group",
                    default={
                        CONF_KI: float(self._get_val(CONF_KI, DEFAULT_KI)),
                        CONF_MAX_I: float(self._get_val(CONF_MAX_I, DEFAULT_MAX_I)),
                    },
                ): KiGridSelector(
                    float(self._get_val(CONF_KI, DEFAULT_KI)),
                    float(self._get_val(CONF_MAX_I, DEFAULT_MAX_I)),
                ),
                vol.Required(
                    CONF_KD,
                    default=self._get_val(CONF_KD, DEFAULT_KD),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=100.0,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_KE,
                    default=self._get_val(CONF_KE, DEFAULT_KE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=5.0,
                        step=0.05,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_SAMPLE_PERIOD,
                    default=self._get_val(
                        CONF_SAMPLE_PERIOD, DEFAULT_SAMPLE_PERIOD
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10,
                        max=600,
                        step=1,
                        unit_of_measurement="초",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        schema = vol.Schema(schema_dict)
        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

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
            pwm_dur = duration_to_seconds(user_input.get(CONF_PWM_DURATION), DEFAULT_PWM_DURATION_SWITCH)
            min_on = duration_to_seconds(user_input.get(CONF_MIN_ON_TIME), DEFAULT_MIN_ON_TIME)
            min_off = duration_to_seconds(user_input.get(CONF_MIN_OFF_TIME), DEFAULT_MIN_OFF_TIME)
            keep_alive = duration_to_seconds(user_input.get(CONF_KEEP_ALIVE), DEFAULT_KEEP_ALIVE)
            output_safety = float(user_input.get(CONF_OUTPUT_SAFETY, DEFAULT_OUTPUT_SAFETY))

            if min_on + min_off > pwm_dur:
                errors["base"] = "min_times_exceed_pwm_duration"

            if not errors:
                processed_input = {
                    **user_input,
                    CONF_PWM_DURATION: pwm_dur,
                    CONF_MIN_ON_TIME: min_on,
                    CONF_MIN_OFF_TIME: min_off,
                    CONF_KEEP_ALIVE: keep_alive,
                    CONF_OUTPUT_SAFETY: output_safety,
                }
                new_options = {
                    **self._config_entry.options,
                    **self._options_data,
                    **processed_input,
                }
                return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PWM_DURATION,
                    default=seconds_to_duration_dict(
                        self._get_val(CONF_PWM_DURATION, DEFAULT_PWM_DURATION_SWITCH)
                    ),
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
                vol.Required(
                    CONF_MIN_ON_TIME,
                    default=seconds_to_duration_dict(
                        self._get_val(CONF_MIN_ON_TIME, DEFAULT_MIN_ON_TIME)
                    ),
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
                vol.Required(
                    CONF_MIN_OFF_TIME,
                    default=seconds_to_duration_dict(
                        self._get_val(CONF_MIN_OFF_TIME, DEFAULT_MIN_OFF_TIME)
                    ),
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
                vol.Required(
                    CONF_KEEP_ALIVE,
                    default=seconds_to_duration_dict(
                        self._get_val(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE)
                    ),
                ): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
                vol.Required(
                    CONF_OUTPUT_SAFETY,
                    default=self._get_val(CONF_OUTPUT_SAFETY, DEFAULT_OUTPUT_SAFETY),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=50.0,
                        step=1.0,
                        unit_of_measurement="%",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(
            step_id="sections_pwm",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_sections_climate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle options step 2: Section mapping, HVAC mode, and fan settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            flat_input = _flatten_sections_input(user_input)
            try:
                sec1 = float(flat_input.get(CONF_SEC1_MAX, DEFAULT_SEC1_MAX))
                sec2 = float(flat_input.get(CONF_SEC2_MAX, DEFAULT_SEC2_MAX))
                sec3 = float(flat_input.get(CONF_SEC3_MAX, DEFAULT_SEC3_MAX))
                if not (sec1 < sec2 < sec3):
                    errors["base"] = "invalid_section_order"
            except (ValueError, TypeError):
                errors["base"] = "invalid_section_order"

            if not errors:
                for i in (1, 2, 3, 4):
                    apply_temp_val = flat_input.get(
                        f"sec{i}_apply_temp",
                        self._get_val(f"sec{i}_apply_temp", DEFAULT_SEC_APPLY_TEMP),
                    )
                    flat_input[f"sec{i}_apply_delta"] = apply_temp_val
                new_options = {
                    **self._config_entry.options,
                    **self._options_data,
                    **flat_input,
                }
                if isinstance(user_input, dict):
                    for sec in ("section_1", "section_2", "section_3", "section_4"):
                        if sec in user_input and isinstance(user_input[sec], dict):
                            new_options[sec] = dict(user_input[sec])
                            sec_idx = sec.split("_")[1]
                            new_options[sec][f"sec{sec_idx}_apply_delta"] = new_options[sec].get(
                                f"sec{sec_idx}_apply_temp", DEFAULT_SEC_APPLY_TEMP
                            )
                return self.async_create_entry(title="", data=new_options)

        # Filter options based on step 1 user selections
        selected_hvac_modes = self._options_data.get(
            CONF_AVAILABLE_HVAC_MODES,
            self._get_val(
                CONF_AVAILABLE_HVAC_MODES, DEFAULT_AVAILABLE_HVAC_MODES
            ),
        )
        if not selected_hvac_modes:
            selected_hvac_modes = DEFAULT_AVAILABLE_HVAC_MODES

        selected_fan_modes = self._options_data.get(
            CONF_AVAILABLE_FAN_MODES,
            self._get_val(
                CONF_AVAILABLE_FAN_MODES, DEFAULT_AVAILABLE_FAN_MODES
            ),
        )
        if not selected_fan_modes:
            selected_fan_modes = DEFAULT_AVAILABLE_FAN_MODES

        thermostat_type = self._options_data.get(
            CONF_THERMOSTAT_TYPE,
            self._get_val(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE),
        )
        is_hum = is_humidity_type(thermostat_type)
        is_heater = (thermostat_type == THERMOSTAT_TYPE_HEATER or thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER)

        # Section fan options: only the selected fan modes
        fan_mode_options = list(selected_fan_modes)

        # Section HVAC options: only the modes selected/supported
        section_hvac_options = [
            m for m in selected_hvac_modes if m != "follow"
        ]
        if not section_hvac_options:
            section_hvac_options = [MODE_DEHUMIDITY if is_hum else HVACMode.COOL.value]

        delta_unit = "%" if is_hum else "°C"
        delta_min = -20.0 if is_hum else -5.0
        delta_max = 20.0 if is_hum else 5.0
        delta_step = 0.5

        if is_hum:
            default_sec1_delta = 2.0 if is_heater else -2.0
            default_sec2_delta = 5.0 if is_heater else -5.0
            default_sec3_delta = 10.0 if is_heater else -10.0
            default_sec4_delta = 15.0 if is_heater else -15.0
        else:
            default_sec1_delta = 1.0 if is_heater else DEFAULT_SEC1_DELTA
            default_sec2_delta = 2.0 if is_heater else DEFAULT_SEC2_DELTA
            default_sec3_delta = 3.0 if is_heater else DEFAULT_SEC3_DELTA
            default_sec4_delta = 4.0 if is_heater else DEFAULT_SEC4_DELTA

        if is_hum:
            if thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER and MODE_HUMIDITY in section_hvac_options:
                default_hvac_fallback = MODE_HUMIDITY
            elif thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER and MODE_DEHUMIDITY in section_hvac_options:
                default_hvac_fallback = MODE_DEHUMIDITY
            elif MODE_AUTO in section_hvac_options:
                default_hvac_fallback = MODE_AUTO
            else:
                default_hvac_fallback = section_hvac_options[0]
        else:
            if is_heater and HVACMode.HEAT.value in section_hvac_options:
                default_hvac_fallback = HVACMode.HEAT.value
            elif not is_heater and HVACMode.COOL.value in section_hvac_options:
                default_hvac_fallback = HVACMode.COOL.value
            else:
                default_hvac_fallback = section_hvac_options[0]

        FAN_ALIAS_LOOKUP = {
            "medium": ["medium", "med", "mid", "middle", "중", "중풍", "2단"],
            "mid": ["mid", "medium", "med", "middle", "중", "중풍", "2단"],
            "med": ["med", "mid", "medium", "middle", "중", "중풍", "2단"],
            "middle": ["middle", "medium", "mid", "med", "중", "중풍", "2단"],
            "low": ["low", "min", "quiet", "silent", "약", "약풍", "미풍", "1단"],
            "high": ["high", "max", "strong", "강", "강풍", "3단"],
            "turbo": ["turbo", "max", "highest", "터보", "파워", "강력"],
            "auto": ["auto", "smart", "자동"],
        }

        # Helper to validate and pick fallback for default
        def _get_valid_fan(key: str, fallback: str) -> str:
            val = self._get_val(key, fallback)
            if val in fan_mode_options:
                return str(val)
            val_lower = str(val).lower().strip()
            for cand in FAN_ALIAS_LOOKUP.get(val_lower, []):
                if cand in fan_mode_options:
                    return cand
            return fan_mode_options[0] if fan_mode_options else "auto"

        def _get_valid_hvac(
            key: str, fallback_key: str | None = None, fallback: str = default_hvac_fallback
        ) -> str:
            val = self._get_val(key, None)
            if val is None and fallback_key:
                val = self._get_val(fallback_key, None)
            if val in section_hvac_options:
                return str(val)
            return fallback

        def _get_valid_action(key: str, fallback_hvac_key: str) -> str:
            val = self._get_val(key, None)
            if val in ("on", "off"):
                return str(val)
            hvac_val = self._get_val(fallback_hvac_key, None)
            if hvac_val == "off":
                return "off"
            return DEFAULT_SEC_ACTION

        default_fan1 = _get_valid_fan(CONF_SEC1_FAN, DEFAULT_SEC1_FAN)
        default_fan2 = _get_valid_fan(CONF_SEC2_FAN, DEFAULT_SEC2_FAN)
        default_fan3 = _get_valid_fan(CONF_SEC3_FAN, DEFAULT_SEC3_FAN)
        default_fan4 = _get_valid_fan(CONF_SEC4_FAN, DEFAULT_SEC4_FAN)

        default_hvac1 = _get_valid_hvac(CONF_SEC1_HVAC_MODE, fallback=default_hvac_fallback)
        default_hvac2 = _get_valid_hvac(CONF_SEC2_HVAC_MODE, fallback=default_hvac_fallback)
        default_hvac3 = _get_valid_hvac(CONF_SEC3_HVAC_MODE, fallback=default_hvac_fallback)
        default_hvac4 = _get_valid_hvac(CONF_SEC4_HVAC_MODE, fallback=default_hvac_fallback)

        default_mode1 = _get_valid_hvac(CONF_SEC1_MODE, fallback_key=CONF_SEC1_HVAC_MODE, fallback=default_hvac1)
        default_mode2 = _get_valid_hvac(CONF_SEC2_MODE, fallback_key=CONF_SEC2_HVAC_MODE, fallback=default_hvac2)
        default_mode3 = _get_valid_hvac(CONF_SEC3_MODE, fallback_key=CONF_SEC3_HVAC_MODE, fallback=default_hvac3)
        default_mode4 = _get_valid_hvac(CONF_SEC4_MODE, fallback_key=CONF_SEC4_HVAC_MODE, fallback=default_hvac4)

        default_act1 = _get_valid_action(CONF_SEC1_ACTION, CONF_SEC1_HVAC_MODE)
        default_act2 = _get_valid_action(CONF_SEC2_ACTION, CONF_SEC2_HVAC_MODE)
        default_act3 = _get_valid_action(CONF_SEC3_ACTION, CONF_SEC3_HVAC_MODE)
        default_act4 = _get_valid_action(CONF_SEC4_ACTION, CONF_SEC4_HVAC_MODE)

        sec1_dict = {
            vol.Required(
                CONF_SEC1_MAX,
                default=self._get_val(CONF_SEC1_MAX, DEFAULT_SEC1_MAX),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=100.0,
                    step=1.0,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEC1_APPLY_TEMP,
                default=self._get_val(
                    CONF_SEC1_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP
                ),
            ): BooleanSelector(),
            vol.Required(
                CONF_SEC1_DELTA,
                default=self._get_val(CONF_SEC1_DELTA, default_sec1_delta),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=delta_min,
                    max=delta_max,
                    step=delta_step,
                    unit_of_measurement=delta_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if is_hum:
            sec1_dict[vol.Required(
                CONF_SEC1_APPLY_ACTION,
                default=self._get_val(CONF_SEC1_APPLY_ACTION, DEFAULT_SEC_APPLY_ACTION),
            )] = BooleanSelector()
            sec1_dict[vol.Required(
                CONF_SEC1_ACTION,
                default=default_act1,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=ACTION_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec1_dict[vol.Required(
                CONF_SEC1_APPLY_MODE,
                default=self._get_val(
                    CONF_SEC1_APPLY_MODE,
                    self._get_val(CONF_SEC1_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_MODE),
                ),
            )] = BooleanSelector()
            sec1_dict[vol.Required(
                CONF_SEC1_MODE,
                default=default_mode1,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec1_dict[vol.Required(
                CONF_SEC1_APPLY_FAN,
                default=self._get_val(
                    CONF_SEC1_APPLY_FAN, DEFAULT_SEC_APPLY_FAN
                ),
            )] = BooleanSelector()
            sec1_dict[vol.Required(
                CONF_SEC1_FAN,
                default=default_fan1,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec1_dict[vol.Required(
                CONF_SEC1_APPLY_HVAC_MODE,
                default=self._get_val(
                    CONF_SEC1_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_HVAC_MODE
                ),
            )] = BooleanSelector()
            sec1_dict[vol.Required(
                CONF_SEC1_HVAC_MODE,
                default=default_hvac1,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        sec2_dict = {
            vol.Required(
                CONF_SEC2_MAX,
                default=self._get_val(CONF_SEC2_MAX, DEFAULT_SEC2_MAX),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=100.0,
                    step=1.0,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEC2_APPLY_TEMP,
                default=self._get_val(
                    CONF_SEC2_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP
                ),
            ): BooleanSelector(),
            vol.Required(
                CONF_SEC2_DELTA,
                default=self._get_val(CONF_SEC2_DELTA, default_sec2_delta),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=delta_min,
                    max=delta_max,
                    step=delta_step,
                    unit_of_measurement=delta_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if is_hum:
            sec2_dict[vol.Required(
                CONF_SEC2_APPLY_ACTION,
                default=self._get_val(CONF_SEC2_APPLY_ACTION, DEFAULT_SEC_APPLY_ACTION),
            )] = BooleanSelector()
            sec2_dict[vol.Required(
                CONF_SEC2_ACTION,
                default=default_act2,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=ACTION_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec2_dict[vol.Required(
                CONF_SEC2_APPLY_MODE,
                default=self._get_val(
                    CONF_SEC2_APPLY_MODE,
                    self._get_val(CONF_SEC2_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_MODE),
                ),
            )] = BooleanSelector()
            sec2_dict[vol.Required(
                CONF_SEC2_MODE,
                default=default_mode2,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec2_dict[vol.Required(
                CONF_SEC2_APPLY_FAN,
                default=self._get_val(
                    CONF_SEC2_APPLY_FAN, DEFAULT_SEC_APPLY_FAN
                ),
            )] = BooleanSelector()
            sec2_dict[vol.Required(
                CONF_SEC2_FAN,
                default=default_fan2,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec2_dict[vol.Required(
                CONF_SEC2_APPLY_HVAC_MODE,
                default=self._get_val(
                    CONF_SEC2_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_HVAC_MODE
                ),
            )] = BooleanSelector()
            sec2_dict[vol.Required(
                CONF_SEC2_HVAC_MODE,
                default=default_hvac2,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        sec3_dict = {
            vol.Required(
                CONF_SEC3_MAX,
                default=self._get_val(CONF_SEC3_MAX, DEFAULT_SEC3_MAX),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0,
                    max=100.0,
                    step=1.0,
                    unit_of_measurement="%",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEC3_APPLY_TEMP,
                default=self._get_val(
                    CONF_SEC3_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP
                ),
            ): BooleanSelector(),
            vol.Required(
                CONF_SEC3_DELTA,
                default=self._get_val(CONF_SEC3_DELTA, default_sec3_delta),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=delta_min,
                    max=delta_max,
                    step=delta_step,
                    unit_of_measurement=delta_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if is_hum:
            sec3_dict[vol.Required(
                CONF_SEC3_APPLY_ACTION,
                default=self._get_val(CONF_SEC3_APPLY_ACTION, DEFAULT_SEC_APPLY_ACTION),
            )] = BooleanSelector()
            sec3_dict[vol.Required(
                CONF_SEC3_ACTION,
                default=default_act3,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=ACTION_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec3_dict[vol.Required(
                CONF_SEC3_APPLY_MODE,
                default=self._get_val(
                    CONF_SEC3_APPLY_MODE,
                    self._get_val(CONF_SEC3_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_MODE),
                ),
            )] = BooleanSelector()
            sec3_dict[vol.Required(
                CONF_SEC3_MODE,
                default=default_mode3,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec3_dict[vol.Required(
                CONF_SEC3_APPLY_FAN,
                default=self._get_val(
                    CONF_SEC3_APPLY_FAN, DEFAULT_SEC_APPLY_FAN
                ),
            )] = BooleanSelector()
            sec3_dict[vol.Required(
                CONF_SEC3_FAN,
                default=default_fan3,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec3_dict[vol.Required(
                CONF_SEC3_APPLY_HVAC_MODE,
                default=self._get_val(
                    CONF_SEC3_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_HVAC_MODE
                ),
            )] = BooleanSelector()
            sec3_dict[vol.Required(
                CONF_SEC3_HVAC_MODE,
                default=default_hvac3,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        sec4_dict = {
            vol.Required(
                CONF_SEC4_APPLY_TEMP,
                default=self._get_val(
                    CONF_SEC4_APPLY_TEMP, DEFAULT_SEC_APPLY_TEMP
                ),
            ): BooleanSelector(),
            vol.Required(
                CONF_SEC4_DELTA,
                default=self._get_val(CONF_SEC4_DELTA, default_sec4_delta),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=delta_min,
                    max=delta_max,
                    step=delta_step,
                    unit_of_measurement=delta_unit,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if is_hum:
            sec4_dict[vol.Required(
                CONF_SEC4_APPLY_ACTION,
                default=self._get_val(CONF_SEC4_APPLY_ACTION, DEFAULT_SEC_APPLY_ACTION),
            )] = BooleanSelector()
            sec4_dict[vol.Required(
                CONF_SEC4_ACTION,
                default=default_act4,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=ACTION_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec4_dict[vol.Required(
                CONF_SEC4_APPLY_MODE,
                default=self._get_val(
                    CONF_SEC4_APPLY_MODE,
                    self._get_val(CONF_SEC4_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_MODE),
                ),
            )] = BooleanSelector()
            sec4_dict[vol.Required(
                CONF_SEC4_MODE,
                default=default_mode4,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec4_dict[vol.Required(
                CONF_SEC4_APPLY_FAN,
                default=self._get_val(
                    CONF_SEC4_APPLY_FAN, DEFAULT_SEC_APPLY_FAN
                ),
            )] = BooleanSelector()
            sec4_dict[vol.Required(
                CONF_SEC4_FAN,
                default=default_fan4,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec4_dict[vol.Required(
                CONF_SEC4_APPLY_HVAC_MODE,
                default=self._get_val(
                    CONF_SEC4_APPLY_HVAC_MODE, DEFAULT_SEC_APPLY_HVAC_MODE
                ),
            )] = BooleanSelector()
            sec4_dict[vol.Required(
                CONF_SEC4_HVAC_MODE,
                default=default_hvac4,
            )] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        schema = _build_sections_schema(sec1_dict, sec2_dict, sec3_dict, sec4_dict)

        if errors and user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        else:
            nested_suggested: dict[str, Any] = {}
            valid_fans = [default_fan1, default_fan2, default_fan3, default_fan4]
            valid_hvacs = [default_hvac1, default_hvac2, default_hvac3, default_hvac4]
            valid_modes = [default_mode1, default_mode2, default_mode3, default_mode4]
            valid_actions = [default_act1, default_act2, default_act3, default_act4]

            for sec_idx, sec_name in enumerate(("section_1", "section_2", "section_3", "section_4"), start=1):
                sec_vals: dict[str, Any] = {}
                for key in (
                    f"sec{sec_idx}_max",
                    f"sec{sec_idx}_apply_temp",
                    f"sec{sec_idx}_delta",
                    f"sec{sec_idx}_apply_fan",
                    f"sec{sec_idx}_fan",
                    f"sec{sec_idx}_apply_hvac_mode",
                    f"sec{sec_idx}_hvac_mode",
                    f"sec{sec_idx}_apply_action",
                    f"sec{sec_idx}_action",
                    f"sec{sec_idx}_apply_mode",
                    f"sec{sec_idx}_mode",
                ):
                    default_for_key = True if "apply" in key else None
                    val = self._get_val(key, default_for_key)
                    if key == f"sec{sec_idx}_fan":
                        val = valid_fans[sec_idx - 1]
                    elif key == f"sec{sec_idx}_hvac_mode":
                        val = valid_hvacs[sec_idx - 1]
                    elif key == f"sec{sec_idx}_mode":
                        val = valid_modes[sec_idx - 1]
                    elif key == f"sec{sec_idx}_action":
                        val = valid_actions[sec_idx - 1]
                    if val is not None:
                        sec_vals[key] = val
                if sec_vals:
                    nested_suggested[sec_name] = sec_vals
            if nested_suggested:
                schema = self.add_suggested_values_to_schema(schema, nested_suggested)

        return self.async_show_form(
            step_id="sections_climate",
            data_schema=schema,
            errors=errors,
        )
