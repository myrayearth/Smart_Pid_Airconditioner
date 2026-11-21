"""Pytest configuration and lightweight Home Assistant 2025.12-2026 stubs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import sys
import types
from pathlib import Path
from typing import Any, Callable
import warnings

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_module(name: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        sub = ".".join(parts[:i])
        if sub not in sys.modules:
            mod = types.ModuleType(sub)
            if i < len(parts):
                mod.__path__ = []
            sys.modules[sub] = mod
            if i > 1:
                parent = sys.modules[".".join(parts[: i - 1])]
                setattr(parent, parts[i - 1], mod)
    return sys.modules[name]


def _install_ha_stubs() -> None:
    if "homeassistant" in sys.modules:
        return

    # --- homeassistant.util.dt ---
    dt_mod = _ensure_module("homeassistant.util.dt")
    _now_ref = [datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)]

    def utcnow() -> datetime:
        return _now_ref[0]

    def set_utcnow(dt: datetime) -> None:
        _now_ref[0] = dt

    def parse_datetime(val: str) -> datetime | None:
        try:
            return datetime.fromisoformat(val)
        except Exception:
            return None

    dt_mod.utcnow = utcnow
    dt_mod.set_utcnow = set_utcnow
    dt_mod.parse_datetime = parse_datetime
    dt_mod.UTC = timezone.utc

    # --- homeassistant.const ---
    const_mod = _ensure_module("homeassistant.const")

    class Platform(str, Enum):
        CLIMATE = "climate"
        HUMIDIFIER = "humidifier"
        SENSOR = "sensor"

    class EntityCategory(str, Enum):
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    class UnitOfTemperature(str, Enum):
        CELSIUS = "°C"
        FAHRENHEIT = "°F"

    const_mod.Platform = Platform
    const_mod.EntityCategory = EntityCategory
    const_mod.UnitOfTemperature = UnitOfTemperature
    const_mod.PERCENTAGE = "%"
    const_mod.ATTR_ENTITY_ID = "entity_id"
    const_mod.ATTR_TEMPERATURE = "temperature"
    const_mod.SERVICE_TURN_ON = "turn_on"
    const_mod.SERVICE_TURN_OFF = "turn_off"
    const_mod.STATE_ON = "on"
    const_mod.STATE_OFF = "off"
    const_mod.STATE_UNAVAILABLE = "unavailable"
    const_mod.STATE_UNKNOWN = "unknown"

    # --- homeassistant.core ---
    core_mod = _ensure_module("homeassistant.core")

    @dataclass
    class Context:
        id: str = "ctx_default"
        user_id: str | None = None
        parent_id: str | None = None

    @dataclass
    class State:
        entity_id: str
        state: str
        attributes: dict[str, Any] = field(default_factory=dict)
        last_changed: datetime = field(default_factory=utcnow)
        last_updated: datetime = field(default_factory=utcnow)
        context: Context = field(default_factory=Context)

    @dataclass
    class Event:
        event_type: str = "state_changed"
        data: dict[str, Any] = field(default_factory=dict)
        context: Context = field(default_factory=Context)

    class EventStateChangedData(dict):
        pass

    class StateMachine:
        def __init__(self) -> None:
            self._states: dict[str, State] = {}
            self._listeners: list[tuple[set[str], Callable]] = []

        def get(self, entity_id: str | None) -> State | None:
            if not entity_id:
                return None
            return self._states.get(entity_id)

        def async_set(
            self,
            entity_id: str,
            new_state: str,
            attributes: dict[str, Any] | None = None,
            context: Context | None = None,
        ) -> None:
            old_state = self._states.get(entity_id)
            now = utcnow()
            st = State(
                entity_id=entity_id,
                state=str(new_state),
                attributes=dict(attributes or {}),
                last_changed=now,
                last_updated=now,
                context=context or Context(),
            )
            self._states[entity_id] = st
            ev = Event(
                event_type="state_changed",
                data={
                    "entity_id": entity_id,
                    "old_state": old_state,
                    "new_state": st,
                },
                context=st.context,
            )
            for entities, cb in list(self._listeners):
                if entity_id in entities:
                    cb(ev)

        def async_entity_ids(self, domain: str | None = None) -> list[str]:
            if domain is None:
                return list(self._states.keys())
            prefix = f"{domain}."
            return [k for k in self._states if k.startswith(prefix)]

    class ServiceRegistry:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, Any], bool, Context | None]] = []
            self.handlers: dict[tuple[str, str], Callable] = {}

        async def async_call(
            self,
            domain: str,
            service: str,
            service_data: dict[str, Any] | None = None,
            blocking: bool = False,
            context: Context | None = None,
            **kwargs: Any,
        ) -> None:
            self.calls.append((domain, service, dict(service_data or {}), blocking, context))
            handler = self.handlers.get((domain, service))
            if handler:
                res = handler(types.SimpleNamespace(data=dict(service_data or {}), context=context))
                if asyncio.iscoroutine(res):
                    await res

        def async_register(
            self,
            domain: str,
            service: str,
            service_func: Callable,
            schema: Any = None,
        ) -> None:
            self.handlers[(domain, service)] = service_func

        def async_remove(self, domain: str, service: str) -> None:
            self.handlers.pop((domain, service), None)

        def has_service(self, domain: str, service: str) -> bool:
            return (domain, service) in self.handlers

    class ConfigEntriesManager:
        def __init__(self, hass: HomeAssistant) -> None:
            self.hass = hass
            self.entries: dict[str, Any] = {}
            self.forwarded: list[tuple[str, list[Any]]] = []
            self.reloaded: list[str] = []

        async def async_forward_entry_setups(self, entry: Any, platforms: list[Any]) -> None:
            self.forwarded.append((entry.entry_id, list(platforms)))

        async def async_unload_platforms(self, entry: Any, platforms: list[Any]) -> bool:
            return True

        async def async_reload(self, entry_id: str) -> None:
            self.reloaded.append(entry_id)

        def async_get_entry(self, entry_id: str) -> Any:
            return self.entries.get(entry_id)

        def async_update_entry(
            self, entry: Any, *, data: dict | None = None, options: dict | None = None, version: int | None = None
        ) -> None:
            if data is not None:
                entry.data = dict(data)
            if options is not None:
                entry.options = dict(options)
            if version is not None:
                entry.version = version

    class HomeAssistant:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}
            self.states = StateMachine()
            self.services = ServiceRegistry()
            self.config_entries = ConfigEntriesManager(self)
            self._tasks: list[asyncio.Task] = []

        def async_create_task(self, coro: Any, name: str | None = None) -> asyncio.Task:
            task = asyncio.create_task(coro)
            self._tasks.append(task)
            return task

        async def async_block_till_done(self) -> None:
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
                self._tasks.clear()

    def callback(func: Callable) -> Callable:
        return func

    @dataclass
    class ServiceCall:
        domain: str = ""
        service: str = ""
        data: dict[str, Any] = field(default_factory=dict)
        context: Context = field(default_factory=Context)

    core_mod.HomeAssistant = HomeAssistant
    core_mod.State = State
    core_mod.Event = Event
    core_mod.Context = Context
    core_mod.ServiceCall = ServiceCall
    core_mod.EventStateChangedData = EventStateChangedData
    core_mod.CALLBACK_TYPE = Callable[[], None]
    core_mod.callback = callback

    # --- homeassistant.config_entries ---
    ce_mod = _ensure_module("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(
            self,
            entry_id: str = "test_entry_1",
            title: str = "Test Smart PID",
            data: dict[str, Any] | None = None,
            options: dict[str, Any] | None = None,
            version: int = 2,
            minor_version: int = 1,
            domain: str = "smart_pid_airconditioner",
        ) -> None:
            self.entry_id = entry_id
            self.title = title
            self.data = dict(data or {})
            self.options = dict(options or {})
            self.version = version
            self.minor_version = minor_version
            self.domain = domain
            self.runtime_data: Any = None
            self._on_unload: list[Callable] = []
            self._update_listeners: list[Callable] = []

        def async_on_unload(self, func: Callable) -> None:
            self._on_unload.append(func)

        def add_update_listener(self, listener: Callable) -> Callable:
            warnings.warn(
                "add_update_listener should not be used with OptionsFlowWithReload",
                DeprecationWarning,
                stacklevel=2,
            )
            self._update_listeners.append(listener)
            return lambda: None

    class ConfigFlow:
        def __init_subclass__(cls, domain: str = "", **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            cls.domain = domain

        def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "create_entry", **kwargs}

        def add_suggested_values_to_schema(self, data_schema: Any, suggested_values: Any) -> Any:
            return data_schema

    class OptionsFlow:
        def __init__(self) -> None:
            self._config_entry_internal: ConfigEntry | None = None

        @property
        def config_entry(self) -> ConfigEntry:
            assert self._config_entry_internal is not None
            return self._config_entry_internal

        @config_entry.setter
        def config_entry(self, value: ConfigEntry) -> None:
            warnings.warn(
                "Setting config_entry on OptionsFlow is deprecated",
                DeprecationWarning,
                stacklevel=2,
            )
            self._config_entry_internal = value

        def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "create_entry", **kwargs}

        def add_suggested_values_to_schema(self, data_schema: Any, suggested_values: Any) -> Any:
            return data_schema

    class OptionsFlowWithReload(OptionsFlow):
        pass

    ce_mod.ConfigEntry = ConfigEntry
    ce_mod.ConfigFlow = ConfigFlow
    ce_mod.OptionsFlow = OptionsFlow
    ce_mod.OptionsFlowWithReload = OptionsFlowWithReload
    ce_mod.ConfigFlowResult = dict[str, Any]

    # --- homeassistant.data_entry_flow ---
    def_mod = _ensure_module("homeassistant.data_entry_flow")

    def section(schema: Any, options: dict[str, Any] | None = None) -> Any:
        return schema

    def_mod.section = section

    # --- homeassistant.components.climate ---
    clim_mod = _ensure_module("homeassistant.components.climate")
    clim_const_mod = _ensure_module("homeassistant.components.climate.const")

    class HVACMode(str, Enum):
        OFF = "off"
        HEAT = "heat"
        COOL = "cool"
        HEAT_COOL = "heat_cool"
        AUTO = "auto"
        DRY = "dry"
        FAN_ONLY = "fan_only"

    class HVACAction(str, Enum):
        OFF = "off"
        HEATING = "heating"
        COOLING = "cooling"
        DRYING = "drying"
        IDLE = "idle"
        FAN = "fan"

    class ClimateEntityFeature(int, Enum):
        TARGET_TEMPERATURE = 1
        TARGET_HUMIDITY = 8
        FAN_MODE = 16
        TURN_OFF = 128
        TURN_ON = 256

    class ClimateEntity:
        hass: HomeAssistant
        _attr_hvac_mode: HVACMode | str = HVACMode.OFF
        _attr_extra_state_attributes: dict[str, Any] = {}
        _attr_device_info: Any = None

        def async_write_ha_state(self) -> None:
            pass

    clim_mod.ClimateEntity = ClimateEntity
    clim_mod.ClimateEntityFeature = ClimateEntityFeature
    clim_mod.HVACAction = HVACAction
    clim_mod.HVACMode = HVACMode

    clim_const_mod.ATTR_FAN_MODE = "fan_mode"
    clim_const_mod.ATTR_HUMIDITY = "humidity"
    clim_const_mod.ATTR_HVAC_MODE = "hvac_mode"
    clim_const_mod.SERVICE_SET_FAN_MODE = "set_fan_mode"
    clim_const_mod.SERVICE_SET_HVAC_MODE = "set_hvac_mode"
    clim_const_mod.SERVICE_SET_TEMPERATURE = "set_temperature"

    # --- homeassistant.components.humidifier ---
    hum_mod = _ensure_module("homeassistant.components.humidifier")

    class HumidifierAction(str, Enum):
        HUMIDIFYING = "humidifying"
        DRYING = "drying"
        IDLE = "idle"
        OFF = "off"

    class HumidifierDeviceClass(str, Enum):
        HUMIDIFIER = "humidifier"
        DEHUMIDIFIER = "dehumidifier"

    class HumidifierEntityFeature(int, Enum):
        MODES = 1

    class HumidifierEntity:
        hass: HomeAssistant
        _attr_device_info: Any = None

        def async_write_ha_state(self) -> None:
            pass

    hum_mod.HumidifierAction = HumidifierAction
    hum_mod.HumidifierDeviceClass = HumidifierDeviceClass
    hum_mod.HumidifierEntity = HumidifierEntity
    hum_mod.HumidifierEntityFeature = HumidifierEntityFeature
    hum_mod.ATTR_HUMIDITY = "humidity"
    hum_mod.ATTR_MODE = "mode"
    hum_mod.SERVICE_SET_HUMIDITY = "set_humidity"
    hum_mod.SERVICE_SET_MODE = "set_mode"

    # --- homeassistant.components.sensor ---
    sens_mod = _ensure_module("homeassistant.components.sensor")

    class SensorDeviceClass(str, Enum):
        ENUM = "enum"
        TEMPERATURE = "temperature"
        HUMIDITY = "humidity"

    class SensorStateClass(str, Enum):
        MEASUREMENT = "measurement"

    class SensorEntity:
        hass: HomeAssistant
        _attr_native_value: Any = None
        _attr_extra_state_attributes: dict[str, Any] = {}
        _attr_entity_category: Any = None
        _attr_device_info: Any = None

        @property
        def entity_category(self) -> Any:
            return self._attr_entity_category

        @property
        def device_info(self) -> Any:
            return self._attr_device_info

        def async_write_ha_state(self) -> None:
            pass

    sens_mod.SensorDeviceClass = SensorDeviceClass
    sens_mod.SensorStateClass = SensorStateClass
    sens_mod.SensorEntity = SensorEntity

    # --- homeassistant.helpers.device_registry ---
    dr_mod = _ensure_module("homeassistant.helpers.device_registry")

    @dataclass
    class DeviceInfo:
        identifiers: set[tuple[str, str]] = field(default_factory=set)
        name: str | None = None
        manufacturer: str | None = None
        model: str | None = None

    dr_mod.DeviceInfo = DeviceInfo

    # --- homeassistant.helpers.entity_registry ---
    er_mod = _ensure_module("homeassistant.helpers.entity_registry")

    class FakeRegistry:
        def __init__(self) -> None:
            self._entities: dict[tuple[Any, str, str], str] = {}

        def async_get_entity_id(self, platform: Any, domain: str, unique_id: str) -> str | None:
            return self._entities.get((platform, domain, unique_id))

        def async_remove(self, entity_id: str) -> None:
            to_del = [k for k, v in self._entities.items() if v == entity_id]
            for k in to_del:
                del self._entities[k]

    _reg = FakeRegistry()
    er_mod.async_get = lambda hass: _reg

    # --- homeassistant.helpers.entity_platform ---
    ep_mod = _ensure_module("homeassistant.helpers.entity_platform")
    ep_mod.AddEntitiesCallback = Callable[[list[Any], bool], None]

    # --- homeassistant.helpers.event ---
    ev_mod = _ensure_module("homeassistant.helpers.event")

    def async_track_state_change_event(
        hass: HomeAssistant, entity_ids: list[str] | str, action: Callable
    ) -> Callable[[], None]:
        ids = {entity_ids} if isinstance(entity_ids, str) else set(entity_ids)
        entry = (ids, action)
        hass.states._listeners.append(entry)

        def _unsub() -> None:
            if entry in hass.states._listeners:
                hass.states._listeners.remove(entry)

        return _unsub

    def async_track_time_interval(
        hass: HomeAssistant, action: Callable, interval: timedelta
    ) -> Callable[[], None]:
        return lambda: None

    def async_call_later(
        hass: HomeAssistant, delay: float, action: Callable
    ) -> Callable[[], None]:
        return lambda: None

    def async_track_point_in_time(
        hass: HomeAssistant, action: Callable, point_in_time: datetime
    ) -> Callable[[], None]:
        return lambda: None

    ev_mod.async_track_state_change_event = async_track_state_change_event
    ev_mod.async_track_time_interval = async_track_time_interval
    ev_mod.async_call_later = async_call_later
    ev_mod.async_track_point_in_time = async_track_point_in_time

    # --- homeassistant.helpers.restore_state ---
    rs_mod = _ensure_module("homeassistant.helpers.restore_state")

    class ExtraStoredData:
        def as_dict(self) -> dict[str, Any]:
            return {}

    class RestoreEntity:
        _mock_last_state: State | None = None
        _mock_last_extra_data: ExtraStoredData | None = None

        async def async_added_to_hass(self) -> None:
            pass

        async def async_get_last_state(self) -> State | None:
            return getattr(self, "_mock_last_state", None)

        async def async_get_last_extra_data(self) -> ExtraStoredData | None:
            return getattr(self, "_mock_last_extra_data", None)

        @property
        def extra_restore_state_data(self) -> ExtraStoredData | None:
            return None

    rs_mod.ExtraStoredData = ExtraStoredData
    rs_mod.RestoreEntity = RestoreEntity

    # --- homeassistant.helpers.storage ---
    st_mod = _ensure_module("homeassistant.helpers.storage")

    class Store:
        _memory_backing: dict[str, dict[str, Any]] = {}

        def __init__(
            self,
            hass: HomeAssistant,
            version: int,
            key: str,
            *,
            minor_version: int = 1,
            atomic_writes: bool = True,
            serialize_in_event_loop: bool = True,
        ) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self.minor_version = minor_version
            self.atomic_writes = atomic_writes
            self.serialize_in_event_loop = serialize_in_event_loop
            self.delay_save_calls: list[float] = []

        async def async_load(self) -> dict[str, Any] | None:
            return Store._memory_backing.get(self.key)

        async def async_save(self, data: dict[str, Any]) -> None:
            Store._memory_backing[self.key] = data

        def async_delay_save(self, data_func: Callable[[], dict[str, Any]], delay: float = 0) -> None:
            self.delay_save_calls.append(delay)
            Store._memory_backing[self.key] = data_func()

        async def async_remove(self) -> None:
            Store._memory_backing.pop(self.key, None)

    st_mod.Store = Store

    # --- homeassistant.helpers.selector ---
    sel_mod = _ensure_module("homeassistant.helpers.selector")

    class _BaseSelector:
        def __init__(self, config: Any = None) -> None:
            self.config = config

        def __call__(self, value: Any) -> Any:
            return value

    for name in (
        "Selector",
        "BooleanSelector",
        "BooleanSelectorConfig",
        "DurationSelector",
        "DurationSelectorConfig",
        "EntitySelector",
        "EntitySelectorConfig",
        "NumberSelector",
        "NumberSelectorConfig",
        "NumberSelectorMode",
        "SelectOptionDict",
        "SelectSelector",
        "SelectSelectorConfig",
        "SelectSelectorMode",
        "TextSelector",
        "TextSelectorConfig",
    ):
        if name.endswith("Config"):
            setattr(sel_mod, name, lambda **kw: kw)
        elif name == "NumberSelectorMode":
            setattr(sel_mod, name, types.SimpleNamespace(BOX="box", SLIDER="slider"))
        elif name == "SelectSelectorMode":
            setattr(sel_mod, name, types.SimpleNamespace(DROPDOWN="dropdown", LIST="list"))
        elif name == "SelectOptionDict":
            setattr(sel_mod, name, dict)
        else:
            setattr(sel_mod, name, _BaseSelector)

    # --- homeassistant.helpers.config_validation ---
    cv_mod = _ensure_module("homeassistant.helpers.config_validation")
    cv_mod.entity_id = str
    cv_mod.string = str

    # --- homeassistant.components.persistent_notification ---
    pn_mod = _ensure_module("homeassistant.components.persistent_notification")
    pn_notifications: list[dict[str, Any]] = []

    def async_create(
        hass: HomeAssistant,
        message: str,
        title: str | None = None,
        notification_id: str | None = None,
    ) -> None:
        pn_notifications.append(
            {
                "message": message,
                "title": title,
                "notification_id": notification_id,
            }
        )

    pn_mod.async_create = async_create
    pn_mod._notifications = pn_notifications

    # --- homeassistant.components.recorder ---
    rec_mod = _ensure_module("homeassistant.components.recorder")
    rec_hist_mod = _ensure_module("homeassistant.components.recorder.history")

    class FakeRecorder:
        async def async_add_executor_job(self, func: Callable, *args: Any) -> Any:
            return func(*args)

    rec_mod.get_instance = lambda hass: FakeRecorder()
    rec_hist_mod.get_significant_states = lambda *args, **kwargs: {}


_install_ha_stubs()

import pytest
import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store


@pytest.fixture(autouse=True)
def _reset_test_env():
    dt_util.set_utcnow(datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))
    Store._memory_backing.clear()
    import homeassistant.components.persistent_notification as pn
    pn._notifications.clear()
    yield


@pytest.fixture
def hass() -> HomeAssistant:
    return HomeAssistant()
