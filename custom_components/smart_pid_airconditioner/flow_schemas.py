"""Shared schema builders, selectors, and validators for ConfigFlow and OptionsFlow."""

from __future__ import annotations

from typing import Any, Callable

import voluptuous as vol

from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import SensorDeviceClass
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

from .config_utils import resolve_type_label
from .const import (
    ALL_FAN_MODES,
    ALL_HUMIDITY_MODES,
    ALL_HVAC_MODES,
    CONF_AUX_TEMP_SENSOR,
    CONF_AVAILABLE_FAN_MODES,
    CONF_AVAILABLE_HVAC_MODES,
    CONF_CONTROL_MODE,
    CONF_COUPLED_AC,
    CONF_COUPLED_DEHUMIDIFIER,
    CONF_CUSTOM_COAST_WINDOW_MIN,
    CONF_CUSTOM_TAU_EM_MIN,
    CONF_DYNAMIC_MIN_OFF_MULTIPLIER,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KD,
    CONF_KE,
    CONF_KEEP_ALIVE,
    CONF_KI,
    CONF_KP,
    CONF_MANUAL_OVERRIDE_MIN,
    CONF_MAX_I,
    CONF_MAX_RUN_MIN,
    CONF_MAX_TEMP,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_MIN_TEMP,
    CONF_OFF_TEMP_ACTION,
    CONF_OUTDOOR_SENSOR,
    CONF_OUTPUT_SAFETY,
    CONF_PREDICTION_MODE,
    CONF_PWM_DURATION,
    CONF_RECORDER_BOOTSTRAP,
    CONF_SAFETY_REST_MIN,
    CONF_SAMPLE_PERIOD,
    CONF_SEC0_ACTION,
    CONF_SEC0_DELTA,
    CONF_SEC1_MAX,
    CONF_SEC2_MAX,
    CONF_SEC3_MAX,
    CONF_SOLAR_GUARD,
    CONF_SYNC_SLAVE_STATE,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_HUMIDITY,
    CONF_TEMP_STEP,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONF_VENT_COOLDOWN_MIN,
    CONF_VENT_DEBOUNCE_SEC,
    CONF_VENT_LOGIC,
    CONF_VENT_MODE,
    CONF_VENT_SENSORS,
    CONF_VENT_SWITCH,
    CONTROL_MODES,
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
    DEFAULT_AVAILABLE_FAN_MODES,
    DEFAULT_AVAILABLE_HUMIDITY_MODES,
    DEFAULT_AVAILABLE_HVAC_MODES,
    DEFAULT_CONTROL_MODE,
    DEFAULT_CUSTOM_COAST_WINDOW_MIN,
    DEFAULT_CUSTOM_TAU_EM_MIN,
    DEFAULT_DYNAMIC_MIN_OFF_MULTIPLIER,
    DEFAULT_HUMIDITY_STEP,
    DEFAULT_INERTIA_PROFILE,
    DEFAULT_INITIAL_TARGET_HUMIDITY,
    DEFAULT_INITIAL_TARGET_TEMP,
    DEFAULT_KD,
    DEFAULT_KE,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MANUAL_OVERRIDE_MIN,
    DEFAULT_MAX_HUMIDITY,
    DEFAULT_MAX_I,
    DEFAULT_MAX_RUN_MIN,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_HUMIDITY,
    DEFAULT_MIN_OFF_TIME,
    DEFAULT_MIN_ON_TIME,
    DEFAULT_MIN_TEMP,
    DEFAULT_OFF_TEMP_ACTION,
    DEFAULT_OUTPUT_SAFETY,
    DEFAULT_PREDICTION_MODE,
    DEFAULT_PWM_DURATION_SWITCH,
    DEFAULT_RECORDER_BOOTSTRAP,
    DEFAULT_SAFETY_REST_MIN,
    DEFAULT_SAMPLE_PERIOD,
    DEFAULT_SEC0_ACTION,
    DEFAULT_SEC0_DELTA_COOL,
    DEFAULT_SEC0_DELTA_DEHUMIDIFY,
    DEFAULT_SEC0_DELTA_HEAT,
    DEFAULT_SEC0_DELTA_HUMIDIFY,
    DEFAULT_SEC1_DELTA,
    DEFAULT_SEC1_MAX,
    DEFAULT_SEC2_DELTA,
    DEFAULT_SEC2_MAX,
    DEFAULT_SEC3_DELTA,
    DEFAULT_SEC3_MAX,
    DEFAULT_SEC4_DELTA,
    DEFAULT_SEC_ACTION,
    DEFAULT_SEC_APPLY_ACTION,
    DEFAULT_SEC_APPLY_FAN,
    DEFAULT_SEC_APPLY_HVAC_MODE,
    DEFAULT_SEC_APPLY_MODE,
    DEFAULT_SEC_APPLY_TEMP,
    DEFAULT_SOLAR_GUARD,
    DEFAULT_SYNC_SLAVE_STATE,
    DEFAULT_TEMP_STEP,
    DEFAULT_THERMOSTAT_TYPE,
    DEFAULT_TUNING_MODE,
    DEFAULT_VENT_COOLDOWN_MIN,
    DEFAULT_VENT_DEBOUNCE_SEC,
    DEFAULT_VENT_LOGIC,
    DEFAULT_VENT_MODE,
    DOMAIN,
    MODE_AUTO,
    MODE_DEHUMIDITY,
    MODE_FAN_ONLY,
    MODE_HUMIDITY,
    MODE_OFF,
    MODE_SILENT,
    OFF_ACTION_SYNC_ONLY,
    OFF_ACTION_TURN_ON,
    SEC0_ACTIONS,
    THERMOSTAT_TYPES,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    TUNING_MODES,
    VENT_LOGICS,
    VENT_MODES,
    duration_to_seconds,
    is_humidity_type,
    seconds_to_duration_dict,
)
from .learner import INERTIA_PROFILES, PREDICTION_MODES

try:
    from homeassistant.data_entry_flow import section
except ImportError:
    section = None

_OWNERSHIP_DEVICE_KEYS = (
    CONF_TARGET_DEVICE,
    CONF_TARGET_CLIMATE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_HEAT,
    CONF_TARGET_DEVICE_DEHUMIDITY,
    CONF_TARGET_DEVICE_HUMIDITY,
)

FALLBACK_FAN_MODES = ["low", "medium", "high", "turbo", "auto"]

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


def find_duplicate_target_device(
    hass: Any,
    candidate_devices: list[str],
    exclude_entry_id: str | None = None,
) -> str | None:
    """Return conflicting device entity_id if already owned by another Smart PID entry."""
    cands = {d.strip() for d in candidate_devices if isinstance(d, str) and d.strip()}
    if not cands or hass is None:
        return None
    entries_fn = getattr(getattr(hass, "config_entries", None), "async_entries", None)
    if callable(entries_fn):
        for entry in entries_fn(DOMAIN):
            if exclude_entry_id and getattr(entry, "entry_id", None) == exclude_entry_id:
                continue
            opts = getattr(entry, "options", {}) or {}
            data = getattr(entry, "data", {}) or {}
            for key in _OWNERSHIP_DEVICE_KEYS:
                val = opts.get(key) or data.get(key)
                if isinstance(val, str) and val.strip() in cands:
                    return val.strip()
    owners = (
        hass.data.get(DOMAIN, {}) if isinstance(getattr(hass, "data", None), dict) else {}
    ).get("owners", {})
    if isinstance(owners, dict):
        for dev_id in cands:
            owner_id = owners.get(dev_id)
            if owner_id and owner_id != exclude_entry_id:
                return dev_id
    return None


def get_fan_modes(hass: Any, climate_entity_id: str | None) -> list[str]:
    """Fetch fan modes supported by the slave climate entity."""
    if climate_entity_id:
        state = hass.states.get(climate_entity_id)
        if state and "fan_modes" in state.attributes:
            modes = state.attributes.get("fan_modes")
            if isinstance(modes, list) and modes:
                return list(dict.fromkeys(str(m) for m in modes))
    return list(FALLBACK_FAN_MODES)


def get_hvac_modes(hass: Any, climate_entity_id: str | None, default_modes: list[str]) -> list[str]:
    """Fetch hvac modes supported by the slave climate entity."""
    if climate_entity_id:
        state = hass.states.get(climate_entity_id)
        if state and "hvac_modes" in state.attributes:
            modes = state.attributes.get("hvac_modes")
            if isinstance(modes, list) and modes:
                return list(dict.fromkeys(str(m) for m in modes))
    return default_modes


def get_humidifier_modes(
    hass: Any, humidifier_entity_id: str | None, default_modes: list[str]
) -> list[str]:
    """Fetch operating modes supported by the slave humidifier entity."""
    if humidifier_entity_id:
        state = hass.states.get(humidifier_entity_id)
        if state and "available_modes" in state.attributes:
            modes = state.attributes.get("available_modes")
            if isinstance(modes, list) and modes:
                return list(dict.fromkeys(str(m) for m in modes))
    return default_modes


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


class KiGridSelector(Selector):
    """Custom selector that serializes Ki and Max I as a 2-column (5:5) ha-form-grid."""

    selector_type = "grid"

    def __init__(
        self,
        ki_default: float = DEFAULT_KI,
        max_i_default: float = DEFAULT_MAX_I,
    ) -> None:
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
            max_i_val = float(self._max_i_default if max_i_raw in (None, "") else max_i_raw)
        except (ValueError, TypeError) as err:
            raise vol.Invalid(f"Invalid numeric value in ki_group: {err}") from err
        return {
            CONF_KI: max(0.0, min(100.0, ki_val)),
            CONF_MAX_I: max(0.0, min(100.0, max_i_val)),
        }


def flatten_sections_input(data: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten nested section dictionary into top-level key-values."""
    if not data:
        return {}
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def default_hvac_modes_for_type(thermostat_type: str) -> list[str]:
    """Return default available HVAC/humidifier modes for the given thermostat type."""
    if is_humidity_type(thermostat_type):
        if thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
            return [MODE_OFF, MODE_HUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
        if thermostat_type == THERMOSTAT_TYPE_DEHUMIDIFIER:
            return [MODE_OFF, MODE_DEHUMIDITY, MODE_FAN_ONLY, MODE_SILENT, MODE_AUTO]
        return list(DEFAULT_AVAILABLE_HUMIDITY_MODES)
    return list(DEFAULT_AVAILABLE_HVAC_MODES)


def build_ventilation_section(get_val: Callable[[str, Any], Any]) -> dict[Any, Any]:
    """Build collapsible ventilation section schema."""
    vent_schema = {
        vol.Optional(
            CONF_VENT_MODE, default=get_val(CONF_VENT_MODE, DEFAULT_VENT_MODE)
        ): SelectSelector(
            SelectSelectorConfig(
                options=VENT_MODES,
                translation_key="ventilation_mode",
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(
            CONF_VENT_SENSORS, default=get_val(CONF_VENT_SENSORS, []) or []
        ): EntitySelector(
            EntitySelectorConfig(domain="binary_sensor", multiple=True)
        ),
        vol.Optional(
            CONF_VENT_LOGIC, default=get_val(CONF_VENT_LOGIC, DEFAULT_VENT_LOGIC)
        ): SelectSelector(
            SelectSelectorConfig(
                options=VENT_LOGICS,
                translation_key="ventilation_logic",
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(
            CONF_VENT_SWITCH,
            description={"suggested_value": get_val(CONF_VENT_SWITCH, None)},
        ): EntitySelector(
            EntitySelectorConfig(domain=["switch", "input_boolean"], multiple=False)
        ),
        vol.Optional(
            CONF_VENT_DEBOUNCE_SEC,
            default=seconds_to_duration_dict(
                get_val(CONF_VENT_DEBOUNCE_SEC, DEFAULT_VENT_DEBOUNCE_SEC)
            ),
        ): DurationSelector(DurationSelectorConfig(enable_day=False)),
        vol.Optional(
            CONF_VENT_COOLDOWN_MIN,
            default=get_val(CONF_VENT_COOLDOWN_MIN, DEFAULT_VENT_COOLDOWN_MIN),
        ): NumberSelector(
            NumberSelectorConfig(
                min=0, max=120, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX
            )
        ),
    }
    if section is not None:
        return {"ventilation": section(vol.Schema(vent_schema), {"collapsed": True})}
    return vent_schema


def build_adaptive_learning_section(
    is_hum: bool, get_val: Callable[[str, Any], Any]
) -> dict[Any, Any]:
    """Build collapsible adaptive_learning section schema."""
    al_schema: dict[Any, Any] = {
        vol.Optional(
            CONF_TUNING_MODE, default=get_val(CONF_TUNING_MODE, DEFAULT_TUNING_MODE)
        ): SelectSelector(
            SelectSelectorConfig(
                options=TUNING_MODES,
                translation_key="tuning_mode",
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(
            CONF_PREDICTION_MODE,
            default=get_val(CONF_PREDICTION_MODE, DEFAULT_PREDICTION_MODE),
        ): SelectSelector(
            SelectSelectorConfig(
                options=list(PREDICTION_MODES),
                translation_key="prediction_mode",
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(
            CONF_INERTIA_PROFILE,
            default=get_val(CONF_INERTIA_PROFILE, DEFAULT_INERTIA_PROFILE),
        ): SelectSelector(
            SelectSelectorConfig(
                options=list(INERTIA_PROFILES),
                translation_key="inertia_profile",
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(
            CONF_CUSTOM_COAST_WINDOW_MIN,
            default=get_val(CONF_CUSTOM_COAST_WINDOW_MIN, DEFAULT_CUSTOM_COAST_WINDOW_MIN),
        ): NumberSelector(
            NumberSelectorConfig(
                min=15, max=720, step=5, unit_of_measurement="min", mode=NumberSelectorMode.BOX
            )
        ),
        vol.Optional(
            CONF_CUSTOM_TAU_EM_MIN,
            default=get_val(CONF_CUSTOM_TAU_EM_MIN, DEFAULT_CUSTOM_TAU_EM_MIN),
        ): NumberSelector(
            NumberSelectorConfig(
                min=5, max=180, step=5, unit_of_measurement="min", mode=NumberSelectorMode.BOX
            )
        ),
    }
    if is_hum:
        al_schema[
            vol.Optional(
                CONF_AUX_TEMP_SENSOR,
                description={"suggested_value": get_val(CONF_AUX_TEMP_SENSOR, None)},
            )
        ] = EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature"))
        al_schema[
            vol.Optional(
                CONF_COUPLED_AC,
                description={"suggested_value": get_val(CONF_COUPLED_AC, None)},
            )
        ] = EntitySelector(EntitySelectorConfig(domain="climate"))
    else:
        al_schema[
            vol.Optional(
                CONF_COUPLED_DEHUMIDIFIER,
                description={"suggested_value": get_val(CONF_COUPLED_DEHUMIDIFIER, None)},
            )
        ] = EntitySelector(EntitySelectorConfig(domain=["humidifier", "switch"]))
        al_schema[
            vol.Optional(
                CONF_SOLAR_GUARD,
                default=get_val(CONF_SOLAR_GUARD, DEFAULT_SOLAR_GUARD),
            )
        ] = BooleanSelector()

    al_schema.update(
        {
            vol.Optional(
                CONF_MANUAL_OVERRIDE_MIN,
                default=get_val(CONF_MANUAL_OVERRIDE_MIN, DEFAULT_MANUAL_OVERRIDE_MIN),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=180, step=5, unit_of_measurement="min", mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MAX_RUN_MIN,
                default=get_val(CONF_MAX_RUN_MIN, DEFAULT_MAX_RUN_MIN),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=480, step=10, unit_of_measurement="min", mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_SAFETY_REST_MIN,
                default=get_val(CONF_SAFETY_REST_MIN, DEFAULT_SAFETY_REST_MIN),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=5, max=120, step=5, unit_of_measurement="min", mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_RECORDER_BOOTSTRAP,
                default=get_val(CONF_RECORDER_BOOTSTRAP, DEFAULT_RECORDER_BOOTSTRAP),
            ): BooleanSelector(),
        }
    )
    if section is not None:
        return {"adaptive_learning": section(vol.Schema(al_schema), {"collapsed": True})}
    return al_schema


def default_sec0_delta(thermostat_type: str, is_hum: bool) -> float:
    """Return the default Section 0 setback delta for the thermostat type."""
    if thermostat_type == THERMOSTAT_TYPE_HEATER:
        return DEFAULT_SEC0_DELTA_HEAT
    if thermostat_type == THERMOSTAT_TYPE_HUMIDIFIER:
        return DEFAULT_SEC0_DELTA_HUMIDIFY
    if is_hum:
        return DEFAULT_SEC0_DELTA_DEHUMIDIFY
    return DEFAULT_SEC0_DELTA_COOL


def build_sec0_dict(
    thermostat_type: str, is_hum: bool, get_val: Callable[[str, Any], Any]
) -> dict[Any, Any]:
    """Build Section 0 standby/early-stop fields schema dict."""
    default_delta = default_sec0_delta(thermostat_type, is_hum)
    return {
        vol.Optional(
            CONF_SEC0_ACTION,
            default=get_val(CONF_SEC0_ACTION, DEFAULT_SEC0_ACTION),
        ): SelectSelector(
            SelectSelectorConfig(
                options=SEC0_ACTIONS,
                translation_key="sec0_action",
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(
            CONF_SEC0_DELTA,
            default=get_val(CONF_SEC0_DELTA, default_delta),
        ): NumberSelector(
            NumberSelectorConfig(
                min=-15.0 if is_hum else -10.0,
                max=15.0 if is_hum else 10.0,
                step=1.0 if is_hum else 0.5,
                unit_of_measurement="%" if is_hum else "°C",
                mode=NumberSelectorMode.BOX,
            )
        ),
    }


def build_sections_schema(
    sec1_dict: dict[Any, Any],
    sec2_dict: dict[Any, Any],
    sec3_dict: dict[Any, Any],
    sec4_dict: dict[Any, Any],
    sec0_dict: dict[Any, Any] | None = None,
) -> vol.Schema:
    """Build section schema using native section cards if supported, or flat fallback."""
    if section is not None:
        schema_map: dict[Any, Any] = {}
        if sec0_dict:
            schema_map["section_0"] = section(vol.Schema(sec0_dict), {"collapsed": True})
        schema_map.update(
            {
                "section_1": section(vol.Schema(sec1_dict), {"collapsed": False}),
                "section_2": section(vol.Schema(sec2_dict), {"collapsed": True}),
                "section_3": section(vol.Schema(sec3_dict), {"collapsed": True}),
                "section_4": section(vol.Schema(sec4_dict), {"collapsed": True}),
            }
        )
        return vol.Schema(schema_map)
    combined: dict[Any, Any] = {}
    if sec0_dict:
        combined.update(sec0_dict)
    combined.update(sec1_dict)
    combined.update(sec2_dict)
    combined.update(sec3_dict)
    combined.update(sec4_dict)
    return vol.Schema(combined)


def validate_device_sensor_input(
    hass: Any,
    user_input: dict[str, Any],
    control_mode: str,
    thermostat_type: str,
    *,
    exclude_entry_id: str | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Validate step 1 (device/sensor/PID) input and return (errors, normalized_payload)."""
    errors: dict[str, str] = {}
    is_hum = is_humidity_type(thermostat_type)
    is_dual_heat_cool_pwm = (
        control_mode == CONTROL_MODE_SWITCH_PWM
        and thermostat_type == THERMOSTAT_TYPE_HEAT_COOL
    )
    is_dual_humidity_pwm = (
        control_mode == CONTROL_MODE_SWITCH_PWM
        and thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY
    )

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
        elif (target_cool.split(".")[0] if "." in target_cool else "") not in ("switch", "input_boolean"):
            errors[CONF_TARGET_DEVICE_COOL] = "target_device_cool_must_be_switch"
        if not target_heat:
            errors[CONF_TARGET_DEVICE_HEAT] = "target_device_heat_required"
        elif (target_heat.split(".")[0] if "." in target_heat else "") not in ("switch", "input_boolean"):
            errors[CONF_TARGET_DEVICE_HEAT] = "target_device_heat_must_be_switch"
        target_dev = target_cool or target_heat
    elif is_dual_humidity_pwm:
        target_dehumidity = user_input.get(CONF_TARGET_DEVICE_DEHUMIDITY, "")
        target_humidity = user_input.get(CONF_TARGET_DEVICE_HUMIDITY, "")
        if not target_dehumidity:
            errors[CONF_TARGET_DEVICE_DEHUMIDITY] = "target_device_dehumidity_required"
        elif (target_dehumidity.split(".")[0] if "." in target_dehumidity else "") not in (
            "switch",
            "input_boolean",
        ):
            errors[CONF_TARGET_DEVICE_DEHUMIDITY] = "target_device_dehumidity_must_be_switch"
        if not target_humidity:
            errors[CONF_TARGET_DEVICE_HUMIDITY] = "target_device_humidity_required"
        elif (target_humidity.split(".")[0] if "." in target_humidity else "") not in (
            "switch",
            "input_boolean",
        ):
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
        dup = find_duplicate_target_device(
            hass,
            [target_dev, target_cool, target_heat, target_dehumidity, target_humidity],
            exclude_entry_id=exclude_entry_id,
        )
        if dup is not None:
            errors["base"] = "duplicate_target_device"

    if errors:
        return errors, {}

    hvac_modes_default = default_hvac_modes_for_type(thermostat_type)
    payload = flatten_sections_input(user_input)
    payload[CONF_OUTDOOR_SENSOR] = user_input.get(CONF_OUTDOOR_SENSOR) or ""
    payload[CONF_TARGET_CLIMATE] = target_dev
    payload[CONF_TARGET_DEVICE] = target_dev

    if control_mode == CONTROL_MODE_CLIMATE_DELTA:
        if is_hum:
            payload[CONF_AVAILABLE_HVAC_MODES] = get_humidifier_modes(
                hass, target_dev, hvac_modes_default
            )
            payload[CONF_AVAILABLE_FAN_MODES] = []
        else:
            payload[CONF_AVAILABLE_HVAC_MODES] = get_hvac_modes(
                hass, target_dev, hvac_modes_default
            )
            payload[CONF_AVAILABLE_FAN_MODES] = get_fan_modes(hass, target_dev)

    payload[CONF_TARGET_DEVICE_COOL] = target_cool if is_dual_heat_cool_pwm else ""
    payload[CONF_TARGET_DEVICE_HEAT] = target_heat if is_dual_heat_cool_pwm else ""
    payload[CONF_TARGET_DEVICE_DEHUMIDITY] = target_dehumidity if is_dual_humidity_pwm else ""
    payload[CONF_TARGET_DEVICE_HUMIDITY] = target_humidity if is_dual_humidity_pwm else ""
    return errors, payload


def build_device_sensor_schema(
    *,
    control_mode: str,
    thermostat_type: str,
    get_val: Callable[[str, Any], Any],
    is_options: bool,
) -> vol.Schema:
    """Build unified step 1 (ConfigFlow device_sensor / OptionsFlow init) schema."""
    is_hum = is_humidity_type(thermostat_type)
    is_dual_heat_cool_pwm = (
        control_mode == CONTROL_MODE_SWITCH_PWM
        and thermostat_type == THERMOSTAT_TYPE_HEAT_COOL
    )
    is_dual_humidity_pwm = (
        control_mode == CONTROL_MODE_SWITCH_PWM
        and thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY
    )

    schema_dict: dict[Any, Any] = {}
    if is_options:
        schema_dict[
            vol.Required(CONF_CONTROL_MODE, default=control_mode)
        ] = SelectSelector(
            SelectSelectorConfig(
                options=CONTROL_MODES,
                translation_key="control_mode",
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        schema_dict[
            vol.Required(CONF_THERMOSTAT_TYPE, default=thermostat_type)
        ] = SelectSelector(
            SelectSelectorConfig(
                options=THERMOSTAT_TYPES,
                translation_key="thermostat_type",
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

    def _req_entity(key: str, fallback: Any = None) -> Any:
        if is_options:
            return vol.Required(key, default=get_val(key, fallback))
        return vol.Required(key)

    if is_dual_heat_cool_pwm:
        schema_dict[_req_entity(CONF_TARGET_DEVICE_COOL, "")] = EntitySelector(
            EntitySelectorConfig(domain=["switch", "input_boolean"])
        )
        schema_dict[_req_entity(CONF_TARGET_DEVICE_HEAT, "")] = EntitySelector(
            EntitySelectorConfig(domain=["switch", "input_boolean"])
        )
    elif is_dual_humidity_pwm:
        schema_dict[_req_entity(CONF_TARGET_DEVICE_DEHUMIDITY, "")] = EntitySelector(
            EntitySelectorConfig(domain=["switch", "input_boolean"])
        )
        schema_dict[_req_entity(CONF_TARGET_DEVICE_HUMIDITY, "")] = EntitySelector(
            EntitySelectorConfig(domain=["switch", "input_boolean"])
        )
    else:
        if control_mode == CONTROL_MODE_CLIMATE_DELTA:
            domain_choice: Any = "humidifier" if is_hum else "climate"
        else:
            domain_choice = ["switch", "input_boolean"]
        if is_options:
            target_key = vol.Required(
                CONF_TARGET_DEVICE,
                default=get_val(CONF_TARGET_DEVICE, get_val(CONF_TARGET_CLIMATE, "")),
            )
        else:
            target_key = vol.Required(CONF_TARGET_DEVICE)
        schema_dict[target_key] = EntitySelector(EntitySelectorConfig(domain=domain_choice))

    sensor_cls = str(
        SensorDeviceClass.HUMIDITY.value if is_hum else SensorDeviceClass.TEMPERATURE.value
    )
    indoor_selector = EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class=sensor_cls)
    )
    outdoor_selector = EntitySelector(
        EntitySelectorConfig(
            filter=[
                {"domain": "sensor", "device_class": sensor_cls},
                {"domain": "weather"},
            ]
        )
    )

    if is_options:
        outdoor_val = get_val(CONF_OUTDOOR_SENSOR, "")
        outdoor_field = (
            vol.Optional(CONF_OUTDOOR_SENSOR, description={"suggested_value": outdoor_val})
            if outdoor_val
            else vol.Optional(CONF_OUTDOOR_SENSOR)
        )
        schema_dict[vol.Required(CONF_INDOOR_SENSOR, default=get_val(CONF_INDOOR_SENSOR, ""))] = (
            indoor_selector
        )
        schema_dict[outdoor_field] = outdoor_selector
    else:
        schema_dict[vol.Required(CONF_INDOOR_SENSOR)] = indoor_selector
        schema_dict[vol.Optional(CONF_OUTDOOR_SENSOR)] = outdoor_selector

    def_modes = default_hvac_modes_for_type(thermostat_type)
    hvac_modes_default = get_val(CONF_AVAILABLE_HVAC_MODES, def_modes) if is_options else def_modes
    hvac_options = ALL_HUMIDITY_MODES if is_hum else ALL_HVAC_MODES

    if control_mode == CONTROL_MODE_SWITCH_PWM:
        schema_dict[
            vol.Required(CONF_AVAILABLE_HVAC_MODES, default=hvac_modes_default)
        ] = SelectSelector(
            SelectSelectorConfig(
                options=hvac_options,
                multiple=True,
                translation_key="available_hvac_modes",
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        schema_dict[
            vol.Required(
                CONF_AVAILABLE_FAN_MODES,
                default=(
                    get_val(CONF_AVAILABLE_FAN_MODES, DEFAULT_AVAILABLE_FAN_MODES)
                    if is_options
                    else DEFAULT_AVAILABLE_FAN_MODES
                ),
            )
        ] = SelectSelector(
            SelectSelectorConfig(
                options=ALL_FAN_MODES,
                multiple=True,
                translation_key="available_fan_modes",
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

    unit_meas = "%" if is_hum else "°C"
    step_options = ["0.5", "1.0", "2.0"] if is_hum else ["0.1", "0.5", "1.0"]
    min_default = DEFAULT_MIN_HUMIDITY if is_hum else DEFAULT_MIN_TEMP
    max_default = DEFAULT_MAX_HUMIDITY if is_hum else DEFAULT_MAX_TEMP
    step_default = DEFAULT_HUMIDITY_STEP if is_hum else DEFAULT_TEMP_STEP

    if not is_options:
        schema_dict[
            vol.Required(
                CONF_INITIAL_TARGET_TEMP,
                default=DEFAULT_INITIAL_TARGET_HUMIDITY if is_hum else DEFAULT_INITIAL_TARGET_TEMP,
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=10.0,
                max=100.0 if is_hum else 35.0,
                step=1.0 if is_hum else 0.5,
                unit_of_measurement=unit_meas,
                mode=NumberSelectorMode.BOX,
            )
        )

    ki_def = float(get_val(CONF_KI, DEFAULT_KI)) if is_options else DEFAULT_KI
    max_i_def = float(get_val(CONF_MAX_I, DEFAULT_MAX_I)) if is_options else DEFAULT_MAX_I

    schema_dict.update(
        {
            vol.Required(
                CONF_MIN_TEMP, default=get_val(CONF_MIN_TEMP, min_default) if is_options else min_default
            ): NumberSelector(
                NumberSelectorConfig(
                    min=10.0,
                    max=50.0 if is_hum else 25.0,
                    step=1.0 if is_hum else 0.5,
                    unit_of_measurement=unit_meas,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_MAX_TEMP, default=get_val(CONF_MAX_TEMP, max_default) if is_options else max_default
            ): NumberSelector(
                NumberSelectorConfig(
                    min=50.0 if is_hum else 25.0,
                    max=100.0 if is_hum else 35.0,
                    step=1.0 if is_hum else 0.5,
                    unit_of_measurement=unit_meas,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_TEMP_STEP, default=get_val(CONF_TEMP_STEP, step_default) if is_options else step_default
            ): SelectSelector(
                SelectSelectorConfig(options=step_options, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(
                CONF_OFF_TEMP_ACTION,
                default=get_val(CONF_OFF_TEMP_ACTION, DEFAULT_OFF_TEMP_ACTION)
                if is_options
                else DEFAULT_OFF_TEMP_ACTION,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[OFF_ACTION_SYNC_ONLY, OFF_ACTION_TURN_ON],
                    translation_key=CONF_OFF_TEMP_ACTION,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_SYNC_SLAVE_STATE,
                default=get_val(CONF_SYNC_SLAVE_STATE, DEFAULT_SYNC_SLAVE_STATE)
                if is_options
                else DEFAULT_SYNC_SLAVE_STATE,
            ): BooleanSelector(),
            vol.Required(
                CONF_KP, default=get_val(CONF_KP, DEFAULT_KP) if is_options else DEFAULT_KP
            ): NumberSelector(
                NumberSelectorConfig(min=0.0, max=100.0, step=0.1, mode=NumberSelectorMode.BOX)
            ),
            vol.Required("ki_group", default={CONF_KI: ki_def, CONF_MAX_I: max_i_def}): KiGridSelector(
                ki_def, max_i_def
            ),
            vol.Required(
                CONF_KD, default=get_val(CONF_KD, DEFAULT_KD) if is_options else DEFAULT_KD
            ): NumberSelector(
                NumberSelectorConfig(min=0.0, max=100.0, step=0.1, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_KE, default=get_val(CONF_KE, DEFAULT_KE) if is_options else DEFAULT_KE
            ): NumberSelector(
                NumberSelectorConfig(min=0.0, max=5.0, step=0.05, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_SAMPLE_PERIOD,
                default=get_val(CONF_SAMPLE_PERIOD, DEFAULT_SAMPLE_PERIOD)
                if is_options
                else DEFAULT_SAMPLE_PERIOD,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=10, max=600, step=1, unit_of_measurement="초", mode=NumberSelectorMode.BOX
                )
            ),
        }
    )
    schema_dict.update(build_ventilation_section(get_val))
    schema_dict.update(build_adaptive_learning_section(is_hum, get_val))
    return vol.Schema(schema_dict)


def process_pwm_sections_input(
    user_input: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Validate and normalize PWM step 2 input."""
    errors: dict[str, str] = {}
    pwm_dur = duration_to_seconds(user_input.get(CONF_PWM_DURATION), DEFAULT_PWM_DURATION_SWITCH)
    min_on = duration_to_seconds(user_input.get(CONF_MIN_ON_TIME), DEFAULT_MIN_ON_TIME)
    min_off = duration_to_seconds(user_input.get(CONF_MIN_OFF_TIME), DEFAULT_MIN_OFF_TIME)
    dynamic_min_off_mult = max(
        0.0,
        min(
            3.0,
            float(
                user_input.get(
                    CONF_DYNAMIC_MIN_OFF_MULTIPLIER, DEFAULT_DYNAMIC_MIN_OFF_MULTIPLIER
                )
            ),
        ),
    )
    keep_alive = duration_to_seconds(user_input.get(CONF_KEEP_ALIVE), DEFAULT_KEEP_ALIVE)
    output_safety = float(user_input.get(CONF_OUTPUT_SAFETY, DEFAULT_OUTPUT_SAFETY))

    if min_on + min_off > pwm_dur:
        errors["base"] = "min_times_exceed_pwm_duration"
        return errors, {}

    processed = {
        **user_input,
        CONF_PWM_DURATION: pwm_dur,
        CONF_MIN_ON_TIME: min_on,
        CONF_MIN_OFF_TIME: min_off,
        CONF_DYNAMIC_MIN_OFF_MULTIPLIER: dynamic_min_off_mult,
        CONF_KEEP_ALIVE: keep_alive,
        CONF_OUTPUT_SAFETY: output_safety,
    }
    return errors, processed


def build_pwm_sections_schema(get_val: Callable[[str, Any], Any]) -> vol.Schema:
    """Build step 2 schema for switch PWM mode."""
    return vol.Schema(
        {
            vol.Required(
                CONF_PWM_DURATION,
                default=seconds_to_duration_dict(
                    get_val(CONF_PWM_DURATION, DEFAULT_PWM_DURATION_SWITCH)
                ),
            ): DurationSelector(DurationSelectorConfig(enable_day=False)),
            vol.Required(
                CONF_MIN_ON_TIME,
                default=seconds_to_duration_dict(get_val(CONF_MIN_ON_TIME, DEFAULT_MIN_ON_TIME)),
            ): DurationSelector(DurationSelectorConfig(enable_day=False)),
            vol.Required(
                CONF_MIN_OFF_TIME,
                default=seconds_to_duration_dict(get_val(CONF_MIN_OFF_TIME, DEFAULT_MIN_OFF_TIME)),
            ): DurationSelector(DurationSelectorConfig(enable_day=False)),
            vol.Optional(
                CONF_DYNAMIC_MIN_OFF_MULTIPLIER,
                default=get_val(CONF_DYNAMIC_MIN_OFF_MULTIPLIER, DEFAULT_DYNAMIC_MIN_OFF_MULTIPLIER),
            ): NumberSelector(
                NumberSelectorConfig(min=0.0, max=3.0, step=0.5, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_KEEP_ALIVE,
                default=seconds_to_duration_dict(get_val(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE)),
            ): DurationSelector(DurationSelectorConfig(enable_day=False)),
            vol.Required(
                CONF_OUTPUT_SAFETY,
                default=get_val(CONF_OUTPUT_SAFETY, DEFAULT_OUTPUT_SAFETY),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.0, max=100.0, step=1.0, unit_of_measurement="%", mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


def validate_and_prepare_climate_sections(
    user_input: dict[str, Any],
    get_val: Callable[[str, Any], Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Validate section ordering and sync sec{i}_apply_delta for climate/humidifier sections."""
    errors: dict[str, str] = {}
    flat_input = flatten_sections_input(user_input)
    try:
        sec1 = float(flat_input.get(CONF_SEC1_MAX, DEFAULT_SEC1_MAX))
        sec2 = float(flat_input.get(CONF_SEC2_MAX, DEFAULT_SEC2_MAX))
        sec3 = float(flat_input.get(CONF_SEC3_MAX, DEFAULT_SEC3_MAX))
        if not (sec1 < sec2 < sec3):
            errors["base"] = "invalid_section_order"
    except (ValueError, TypeError):
        errors["base"] = "invalid_section_order"

    if errors:
        return errors, {}

    for i in (1, 2, 3, 4):
        apply_temp_val = flat_input.get(
            f"sec{i}_apply_temp",
            get_val(f"sec{i}_apply_temp", DEFAULT_SEC_APPLY_TEMP),
        )
        flat_input[f"sec{i}_apply_delta"] = apply_temp_val

    nested_sections: dict[str, Any] = {}
    if isinstance(user_input, dict):
        for sec in ("section_1", "section_2", "section_3", "section_4"):
            if sec in user_input and isinstance(user_input[sec], dict):
                sec_copy = dict(user_input[sec])
                sec_idx = sec.split("_")[1]
                sec_copy[f"sec{sec_idx}_apply_delta"] = sec_copy.get(
                    f"sec{sec_idx}_apply_temp", DEFAULT_SEC_APPLY_TEMP
                )
                nested_sections[sec] = sec_copy

    return errors, {**flat_input, **nested_sections}


def build_climate_sections_schema(
    *,
    thermostat_type: str,
    selected_hvac_modes: list[str],
    selected_fan_modes: list[str],
    get_val: Callable[[str, Any], Any],
) -> vol.Schema:
    """Build Section 0..4 schema for climate/humidifier delta mode."""
    is_hum = is_humidity_type(thermostat_type)
    is_heater = thermostat_type in (THERMOSTAT_TYPE_HEATER, THERMOSTAT_TYPE_HUMIDIFIER)

    section_hvac_options = [m for m in selected_hvac_modes if m != "follow"]
    if not section_hvac_options:
        section_hvac_options = [MODE_DEHUMIDITY if is_hum else HVACMode.COOL.value]

    delta_unit = "%" if is_hum else "°C"
    delta_min = -20.0 if is_hum else -5.0
    delta_max = 20.0 if is_hum else 5.0
    delta_step = 0.5

    if is_hum:
        default_deltas = (
            (2.0, 5.0, 10.0, 15.0) if is_heater else (-2.0, -5.0, -10.0, -15.0)
        )
    else:
        default_deltas = (
            (1.0, 2.0, 3.0, 4.0)
            if is_heater
            else (DEFAULT_SEC1_DELTA, DEFAULT_SEC2_DELTA, DEFAULT_SEC3_DELTA, DEFAULT_SEC4_DELTA)
        )

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

    fan_mode_options = list(selected_fan_modes)
    default_fans = (
        "low" if "low" in fan_mode_options else (fan_mode_options[0] if fan_mode_options else "auto"),
        (
            "medium"
            if "medium" in fan_mode_options
            else (fan_mode_options[min(1, len(fan_mode_options) - 1)] if fan_mode_options else "auto")
        ),
        "high" if "high" in fan_mode_options else (fan_mode_options[-1] if fan_mode_options else "auto"),
        (
            "turbo"
            if "turbo" in fan_mode_options
            else ("high" if "high" in fan_mode_options else (fan_mode_options[-1] if fan_mode_options else "auto"))
        ),
    )

    default_maxes = (DEFAULT_SEC1_MAX, DEFAULT_SEC2_MAX, DEFAULT_SEC3_MAX, None)
    sec_dicts: list[dict[Any, Any]] = []

    for idx in range(1, 5):
        sec_d: dict[Any, Any] = {}
        def_max = default_maxes[idx - 1]
        if def_max is not None:
            sec_d[
                vol.Required(f"sec{idx}_max", default=get_val(f"sec{idx}_max", def_max))
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=0.0, max=100.0, step=1.0, unit_of_measurement="%", mode=NumberSelectorMode.BOX
                )
            )
        sec_d[
            vol.Required(
                f"sec{idx}_apply_temp",
                default=get_val(
                    f"sec{idx}_apply_temp",
                    get_val(f"sec{idx}_apply_delta", DEFAULT_SEC_APPLY_TEMP),
                ),
            )
        ] = BooleanSelector()
        sec_d[
            vol.Required(
                f"sec{idx}_delta",
                default=get_val(f"sec{idx}_delta", default_deltas[idx - 1]),
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=delta_min,
                max=delta_max,
                step=delta_step,
                unit_of_measurement=delta_unit,
                mode=NumberSelectorMode.BOX,
            )
        )

        if is_hum:
            sec_d[
                vol.Required(
                    f"sec{idx}_apply_action",
                    default=get_val(f"sec{idx}_apply_action", DEFAULT_SEC_APPLY_ACTION),
                )
            ] = BooleanSelector()
            sec_d[
                vol.Required(
                    f"sec{idx}_action",
                    default=get_val(f"sec{idx}_action", DEFAULT_SEC_ACTION),
                )
            ] = SelectSelector(
                SelectSelectorConfig(options=ACTION_OPTIONS, mode=SelectSelectorMode.DROPDOWN)
            )
            sec_d[
                vol.Required(
                    f"sec{idx}_apply_mode",
                    default=get_val(f"sec{idx}_apply_mode", DEFAULT_SEC_APPLY_MODE),
                )
            ] = BooleanSelector()
            saved_mode = get_val(f"sec{idx}_mode", default_hvac)
            if saved_mode not in section_hvac_options:
                saved_mode = default_hvac
            sec_d[vol.Required(f"sec{idx}_mode", default=saved_mode)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            sec_d[
                vol.Required(
                    f"sec{idx}_apply_fan",
                    default=get_val(f"sec{idx}_apply_fan", DEFAULT_SEC_APPLY_FAN),
                )
            ] = BooleanSelector()
            saved_fan = get_val(f"sec{idx}_fan", default_fans[idx - 1])
            if fan_mode_options and saved_fan not in fan_mode_options:
                saved_fan = default_fans[idx - 1]
            sec_d[vol.Required(f"sec{idx}_fan", default=saved_fan)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_fan_options(fan_mode_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            sec_d[
                vol.Required(
                    f"sec{idx}_apply_hvac_mode",
                    default=get_val(f"sec{idx}_apply_hvac_mode", DEFAULT_SEC_APPLY_HVAC_MODE),
                )
            ] = BooleanSelector()
            saved_hvac = get_val(f"sec{idx}_hvac_mode", default_hvac)
            if saved_hvac not in section_hvac_options:
                saved_hvac = default_hvac
            sec_d[vol.Required(f"sec{idx}_hvac_mode", default=saved_hvac)] = SelectSelector(
                SelectSelectorConfig(
                    options=format_mode_options(section_hvac_options),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        sec_dicts.append(sec_d)

    sec0_dict = build_sec0_dict(thermostat_type, is_hum, get_val)
    return build_sections_schema(sec_dicts[0], sec_dicts[1], sec_dicts[2], sec_dicts[3], sec0_dict)


def build_entry_title(full_data: dict[str, Any], *, is_pwm: bool) -> str:
    """Build human-readable ConfigEntry title for Smart PID."""
    thermostat_type = full_data.get(CONF_THERMOSTAT_TYPE, DEFAULT_THERMOSTAT_TYPE)
    type_label = resolve_type_label(thermostat_type)
    if not is_pwm:
        target_climate = full_data.get(CONF_TARGET_CLIMATE, "Thermostat")
        return f"Smart PID {type_label} ({target_climate})"

    target_dev = full_data.get(CONF_TARGET_DEVICE, full_data.get(CONF_TARGET_CLIMATE, "Thermostat"))
    control_mode = full_data.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
    if control_mode == CONTROL_MODE_SWITCH_PWM and thermostat_type == THERMOSTAT_TYPE_HEAT_COOL:
        cool_dev = full_data.get(CONF_TARGET_DEVICE_COOL, "")
        heat_dev = full_data.get(CONF_TARGET_DEVICE_HEAT, "")
        dev_label = (
            f"{cool_dev} / {heat_dev}"
            if (cool_dev and heat_dev)
            else (cool_dev or heat_dev or target_dev)
        )
    elif (
        control_mode == CONTROL_MODE_SWITCH_PWM
        and thermostat_type == THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY
    ):
        dehum_dev = full_data.get(CONF_TARGET_DEVICE_DEHUMIDITY, "")
        hum_dev = full_data.get(CONF_TARGET_DEVICE_HUMIDITY, "")
        dev_label = (
            f"{dehum_dev} / {hum_dev}"
            if (dehum_dev and hum_dev)
            else (dehum_dev or hum_dev or target_dev)
        )
    else:
        dev_label = target_dev
    return f"Smart PID PWM {type_label} ({dev_label})"
