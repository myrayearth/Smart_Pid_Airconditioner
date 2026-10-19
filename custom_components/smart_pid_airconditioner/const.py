"""Constants for the Smart PID Thermostat integration."""

from typing import Any

from homeassistant.components.climate import HVACMode
from homeassistant.const import Platform


DOMAIN = "smart_pid_airconditioner"
PLATFORMS = [Platform.CLIMATE, Platform.HUMIDIFIER]

# Config & Option Keys - Step 1: Base settings
CONF_CONTROL_MODE = "control_mode"
CONTROL_MODE_CLIMATE_DELTA = "climate_delta"
CONTROL_MODE_SWITCH_PWM = "switch_pwm"
CONTROL_MODES = [
    CONTROL_MODE_CLIMATE_DELTA,
    CONTROL_MODE_SWITCH_PWM,
]
DEFAULT_CONTROL_MODE = CONTROL_MODE_CLIMATE_DELTA

CONF_TARGET_DEVICE = "target_device"
CONF_TARGET_CLIMATE = "target_climate"
CONF_TARGET_DEVICE_COOL = "target_device_cool"
CONF_TARGET_DEVICE_HEAT = "target_device_heat"
CONF_TARGET_DEVICE_DEHUMIDITY = "target_device_dehumidity"
CONF_TARGET_DEVICE_HUMIDITY = "target_device_humidity"
CONF_THERMOSTAT_TYPE = "thermostat_type"
CONF_INDOOR_SENSOR = "indoor_sensor"
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_INITIAL_TARGET_TEMP = "initial_target_temp"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_TEMP_STEP = "temp_step"
CONF_OFF_TEMP_ACTION = "off_temp_action"
CONF_KP = "kp"
CONF_KI = "ki"
CONF_MAX_I = "max_i"
CONF_KD = "kd"
CONF_KE = "ke"
CONF_PWM_DURATION = "pwm_duration"
CONF_SAMPLE_PERIOD = "sample_period"
CONF_SYNC_SLAVE_STATE = "sync_slave_state"
CONF_AVAILABLE_HVAC_MODES = "available_hvac_modes"
CONF_AVAILABLE_FAN_MODES = "available_fan_modes"

# PWM Mode Settings
CONF_MIN_ON_TIME = "min_on_time"
CONF_MIN_OFF_TIME = "min_off_time"
CONF_KEEP_ALIVE = "keep_alive"
CONF_OUTPUT_SAFETY = "output_safety"

DEFAULT_PWM_DURATION_SWITCH = 900
DEFAULT_MIN_ON_TIME = 180
DEFAULT_MIN_OFF_TIME = 180
DEFAULT_KEEP_ALIVE = 60
DEFAULT_OUTPUT_SAFETY = 0.0

# Controller Types (Cooler vs Heater vs Heat/Cool vs Dehumidifier vs Humidifier vs Humidity/Dehumidity)
THERMOSTAT_TYPE_COOLER = "cooler"
THERMOSTAT_TYPE_HEATER = "heater"
THERMOSTAT_TYPE_HEAT_COOL = "heat_cool"
THERMOSTAT_TYPE_DEHUMIDIFIER = "dehumidifier"
THERMOSTAT_TYPE_HUMIDIFIER = "humidifier"
THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY = "humidity_dehumidity"
THERMOSTAT_TYPES = [
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_HEATER,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDIFIER,
    THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
]
DEFAULT_THERMOSTAT_TYPE = THERMOSTAT_TYPE_COOLER

# OFF Temperature Actions
OFF_ACTION_SYNC_ONLY = "sync_only"
OFF_ACTION_TURN_ON = "turn_on"
DEFAULT_OFF_TEMP_ACTION = OFF_ACTION_SYNC_ONLY
DEFAULT_SYNC_SLAVE_STATE = True

# Mode strings
MODE_OFF = "off"
MODE_AUTO = "auto"
MODE_COOL = "cool"
MODE_HEAT = "heat"
MODE_FAN_ONLY = "fan_only"
MODE_DRY = "dry"
MODE_HUMIDITY = "humidity"
MODE_DEHUMIDITY = "dehumidity"
MODE_SILENT = "silent"

# All Available Modes
ALL_HVAC_MODES = [
    HVACMode.OFF.value,
    HVACMode.AUTO.value,
    HVACMode.COOL.value,
    HVACMode.HEAT.value,
    HVACMode.FAN_ONLY.value,
    HVACMode.DRY.value,
]

DEFAULT_AVAILABLE_HVAC_MODES = [
    HVACMode.OFF.value,
    HVACMode.AUTO.value,
    HVACMode.COOL.value,
    HVACMode.HEAT.value,
    HVACMode.FAN_ONLY.value,
    HVACMode.DRY.value,
]

ALL_HUMIDITY_MODES = [
    MODE_OFF,
    MODE_HUMIDITY,
    MODE_DEHUMIDITY,
    MODE_FAN_ONLY,
    MODE_SILENT,
    MODE_AUTO,
]

DEFAULT_AVAILABLE_HUMIDITY_MODES = [
    MODE_OFF,
    MODE_HUMIDITY,
    MODE_DEHUMIDITY,
    MODE_FAN_ONLY,
    MODE_SILENT,
    MODE_AUTO,
]

ALL_FAN_MODES = ["low", "medium", "high", "turbo", "silent", "auto"]
DEFAULT_AVAILABLE_FAN_MODES = ["low", "medium", "high", "turbo", "auto"]

# Supported HVAC Modes
SUPPORTED_HVAC_MODES = [
    HVACMode.OFF,
    HVACMode.COOL,
    HVACMode.HEAT,
    HVACMode.AUTO,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
]

# Config & Option Keys - Step 2: Section & Fan settings
CONF_SEC1_MAX = "sec1_max"
CONF_SEC1_APPLY_TEMP = "sec1_apply_temp"
CONF_SEC1_APPLY_DELTA = "sec1_apply_delta"
CONF_SEC1_DELTA = "sec1_delta"
CONF_SEC1_APPLY_FAN = "sec1_apply_fan"
CONF_SEC1_FAN = "sec1_fan"
CONF_SEC1_APPLY_HVAC_MODE = "sec1_apply_hvac_mode"
CONF_SEC1_HVAC_MODE = "sec1_hvac_mode"

CONF_SEC2_MAX = "sec2_max"
CONF_SEC2_APPLY_TEMP = "sec2_apply_temp"
CONF_SEC2_APPLY_DELTA = "sec2_apply_delta"
CONF_SEC2_DELTA = "sec2_delta"
CONF_SEC2_APPLY_FAN = "sec2_apply_fan"
CONF_SEC2_FAN = "sec2_fan"
CONF_SEC2_APPLY_HVAC_MODE = "sec2_apply_hvac_mode"
CONF_SEC2_HVAC_MODE = "sec2_hvac_mode"

CONF_SEC3_MAX = "sec3_max"
CONF_SEC3_APPLY_TEMP = "sec3_apply_temp"
CONF_SEC3_APPLY_DELTA = "sec3_apply_delta"
CONF_SEC3_DELTA = "sec3_delta"
CONF_SEC3_APPLY_FAN = "sec3_apply_fan"
CONF_SEC3_FAN = "sec3_fan"
CONF_SEC3_APPLY_HVAC_MODE = "sec3_apply_hvac_mode"
CONF_SEC3_HVAC_MODE = "sec3_hvac_mode"

CONF_SEC4_APPLY_TEMP = "sec4_apply_temp"
CONF_SEC4_APPLY_DELTA = "sec4_apply_delta"
CONF_SEC4_DELTA = "sec4_delta"
CONF_SEC4_APPLY_FAN = "sec4_apply_fan"
CONF_SEC4_FAN = "sec4_fan"
CONF_SEC4_APPLY_HVAC_MODE = "sec4_apply_hvac_mode"
CONF_SEC4_HVAC_MODE = "sec4_hvac_mode"

DEFAULT_SEC_HVAC_MODE = HVACMode.COOL.value
DEFAULT_SEC_APPLY_TEMP = True
DEFAULT_SEC_APPLY_DELTA = True
DEFAULT_SEC_APPLY_FAN = True
DEFAULT_SEC_APPLY_HVAC_MODE = True
DEFAULT_SEC_APPLY_ACTION = True
DEFAULT_SEC_ACTION = "on"
DEFAULT_SEC_APPLY_MODE = True
DEFAULT_SEC_MODE = MODE_AUTO

CONF_SEC1_APPLY_ACTION = "sec1_apply_action"
CONF_SEC1_ACTION = "sec1_action"
CONF_SEC1_APPLY_MODE = "sec1_apply_mode"
CONF_SEC1_MODE = "sec1_mode"

CONF_SEC2_APPLY_ACTION = "sec2_apply_action"
CONF_SEC2_ACTION = "sec2_action"
CONF_SEC2_APPLY_MODE = "sec2_apply_mode"
CONF_SEC2_MODE = "sec2_mode"

CONF_SEC3_APPLY_ACTION = "sec3_apply_action"
CONF_SEC3_ACTION = "sec3_action"
CONF_SEC3_APPLY_MODE = "sec3_apply_mode"
CONF_SEC3_MODE = "sec3_mode"

CONF_SEC4_APPLY_ACTION = "sec4_apply_action"
CONF_SEC4_ACTION = "sec4_action"
CONF_SEC4_APPLY_MODE = "sec4_apply_mode"
CONF_SEC4_MODE = "sec4_mode"

# Default values
DEFAULT_NAME = "Smart PID Air Conditioner"
DEFAULT_INITIAL_TARGET_TEMP = 24.0
DEFAULT_MIN_TEMP = 16.0
DEFAULT_MAX_TEMP = 30.0
DEFAULT_TEMP_STEP = "0.5"
DEFAULT_KP = 20.0
DEFAULT_KI = 0.05
DEFAULT_MAX_I = 20.0
DEFAULT_KD = 5.0
DEFAULT_KE = 0.6
DEFAULT_PWM_DURATION = 0
DEFAULT_SAMPLE_PERIOD = 60

# Humidity Defaults
DEFAULT_INITIAL_TARGET_HUMIDITY = 50.0
DEFAULT_MIN_HUMIDITY = 30.0
DEFAULT_MAX_HUMIDITY = 80.0
DEFAULT_HUMIDITY_STEP = "1.0"
DEFAULT_SLAVE_MIN_HUMIDITY = 10.0
DEFAULT_SLAVE_MAX_HUMIDITY = 100.0

DEFAULT_SEC1_MAX = 30.0
DEFAULT_SEC1_DELTA = -1.0
DEFAULT_SEC1_FAN = "low"

DEFAULT_SEC2_MAX = 60.0
DEFAULT_SEC2_DELTA = -2.0
DEFAULT_SEC2_FAN = "medium"

DEFAULT_SEC3_MAX = 90.0
DEFAULT_SEC3_DELTA = -3.0
DEFAULT_SEC3_FAN = "high"

DEFAULT_SEC4_DELTA = -4.0
DEFAULT_SEC4_FAN = "high"

# Hysteresis and Fail-safe constants
HYSTERESIS_BAND = 1.5
FAIL_SAFE_OUTPUT = 10.0
FAIL_SAFE_MULTIPLIER = 3

DEFAULT_SLAVE_MIN_TEMP = 18.0
DEFAULT_SLAVE_MAX_TEMP = 30.0


def is_humidity_type(controller_type: str | None) -> bool:
    """Return True if controller type is humidity, dehumidity, or dual."""
    return controller_type in (
        THERMOSTAT_TYPE_DEHUMIDIFIER,
        THERMOSTAT_TYPE_HUMIDIFIER,
        THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
        "dehumidity",
        "humidity",
    )


def is_dual_controller(controller_type: str | None) -> bool:
    """Return True if controller type is dual (heat/cool or dehumidity/humidity)."""
    return controller_type in (
        THERMOSTAT_TYPE_HEAT_COOL,
        THERMOSTAT_TYPE_HUMIDITY_DEHUMIDITY,
    )


def duration_to_seconds(value: Any, default: int = 0) -> int:
    """Convert DurationSelector output (dict, timedelta, or int/float) to integer seconds."""
    if value is None:
        return default
    if isinstance(value, dict):
        days = int(value.get("days", 0) or 0)
        hours = int(value.get("hours", 0) or 0)
        minutes = int(value.get("minutes", 0) or 0)
        seconds = int(value.get("seconds", 0) or 0)
        milliseconds = int(value.get("milliseconds", 0) or 0)
        return (
            days * 86400
            + hours * 3600
            + minutes * 60
            + seconds
            + (1 if milliseconds >= 500 else 0)
        )
    if hasattr(value, "total_seconds"):
        return int(value.total_seconds())
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def seconds_to_duration_dict(value: Any, default: int = 0) -> dict[str, int]:
    """Convert integer seconds or existing duration value into duration dict for DurationSelector."""
    total = duration_to_seconds(value, default)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return {"hours": hours, "minutes": minutes, "seconds": seconds}

