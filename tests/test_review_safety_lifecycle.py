"""Regression tests for P1/P2 code review fixes: PWM shutdown, actuator fail-safe, unload/setup lifecycle."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import ConfigEntryError
import homeassistant.util.dt as dt_util

from custom_components.smart_pid_airconditioner import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.actuator import SwitchCommandGuard
from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_INDOOR_SENSOR,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_PWM_DURATION,
    CONF_RECORDER_BOOTSTRAP,
    CONF_TARGET_DEVICE,
    CONF_TARGET_DEVICE_COOL,
    CONF_TARGET_DEVICE_HEAT,
    CONF_THERMOSTAT_TYPE,
    CONTROL_MODE_SWITCH_PWM,
    DOMAIN,
    THERMOSTAT_TYPE_DEHUMIDIFIER,
    THERMOSTAT_TYPE_HEAT_COOL,
    THERMOSTAT_TYPE_HEATER,
)
from custom_components.smart_pid_airconditioner.humidifier import SmartPIDHumidifierEntity
import custom_components.smart_pid_airconditioner.runtime as runtime_mod


def _register_switch_services(hass) -> tuple[list[str], list[str]]:
    on_calls: list[str] = []
    off_calls: list[str] = []

    async def handle_on(call):
        eid = call.data["entity_id"]
        on_calls.append(eid)
        hass.states.async_set(eid, STATE_ON)

    async def handle_off(call):
        eid = call.data["entity_id"]
        off_calls.append(eid)
        hass.states.async_set(eid, STATE_OFF)

    hass.services.async_register("switch", "turn_on", handle_on)
    hass.services.async_register("switch", "turn_off", handle_off)
    hass.services.async_register("homeassistant", "turn_on", handle_on)
    hass.services.async_register("homeassistant", "turn_off", handle_off)
    return on_calls, off_calls


@pytest.mark.asyncio
async def test_pwm_removal_safe_shutdown_single_dual_humidifier_and_error_handling(
    hass, caplog
) -> None:
    """P1-A: Removal turns off active PWM switches cleanly, updates reload_off, and logs errors safely."""
    t0 = dt_util.utcnow()
    _, off_calls = _register_switch_services(hass)

    # 1) Single heater PWM climate entity active at removal
    hass.states.async_set("sensor.room_temp_rm", "18.0")
    hass.states.async_set("switch.heat_rm", STATE_OFF)
    entry = ConfigEntry(
        entry_id="rm_pwm_clim_1",
        title="Remove PWM Heat",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_INDOOR_SENSOR: "sensor.room_temp_rm",
            CONF_TARGET_DEVICE: "switch.heat_rm",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_PWM_DURATION: 900,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 180,
            CONF_KP: 25.0,
            CONF_KI: 0.0,
            CONF_KE: 0.0,
        },
        version=2,
    )
    assert await async_setup_entry(hass, entry) is True
    clim = SmartPIDThermostatEntity(hass, entry)
    clim.hass = hass
    clim._switch_guard = SwitchCommandGuard(
        hass, confirm_timeout=0.05, off_retry_delays=(0.01,)
    )
    await clim.async_added_to_hass()
    dt_util.set_utcnow(t0 + timedelta(seconds=200))
    await clim.async_set_hvac_mode(HVACMode.HEAT)
    assert clim._pwm_state is True
    assert hass.states.get("switch.heat_rm").state == STATE_ON

    t_remove = t0 + timedelta(seconds=320)
    dt_util.set_utcnow(t_remove)
    off_calls.clear()
    await clim.async_will_remove_from_hass()
    assert "switch.heat_rm" in off_calls
    assert hass.states.get("switch.heat_rm").state == STATE_OFF
    assert clim._pwm_state is False
    reload_off = hass.data[DOMAIN]["reload_off"]
    assert reload_off["switch.heat_rm"] == (
        t_remove,
        t_remove + timedelta(seconds=180),
    )
    await async_unload_entry(hass, entry)

    # 2) Dual heat_cool PWM where both switches are ON at removal
    hass.states.async_set("sensor.room_dual_rm", "22.0")
    hass.states.async_set("switch.cool_dual_rm", STATE_ON)
    hass.states.async_set("switch.heat_dual_rm", STATE_ON)
    dual_entry = ConfigEntry(
        entry_id="rm_pwm_dual_1",
        title="Remove PWM Dual",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEAT_COOL,
            CONF_INDOOR_SENSOR: "sensor.room_dual_rm",
            CONF_TARGET_DEVICE_COOL: "switch.cool_dual_rm",
            CONF_TARGET_DEVICE_HEAT: "switch.heat_dual_rm",
            CONF_INITIAL_TARGET_TEMP: 22.0,
            CONF_MIN_OFF_TIME: 120,
        },
        version=2,
    )
    assert await async_setup_entry(hass, dual_entry) is True
    dual_clim = SmartPIDThermostatEntity(hass, dual_entry)
    dual_clim.hass = hass
    dual_clim._switch_guard = SwitchCommandGuard(
        hass, confirm_timeout=0.05, off_retry_delays=(0.01,)
    )
    await dual_clim.async_added_to_hass()
    hass.states.async_set("switch.cool_dual_rm", STATE_ON)
    hass.states.async_set("switch.heat_dual_rm", STATE_ON)
    off_calls.clear()
    t_dual = t_remove + timedelta(seconds=30)
    dt_util.set_utcnow(t_dual)
    await dual_clim.async_will_remove_from_hass()
    assert set(off_calls) == {"switch.cool_dual_rm", "switch.heat_dual_rm"}
    assert hass.states.get("switch.cool_dual_rm").state == STATE_OFF
    assert hass.states.get("switch.heat_dual_rm").state == STATE_OFF
    assert "switch.cool_dual_rm" in hass.data[DOMAIN]["reload_off"]
    assert "switch.heat_dual_rm" in hass.data[DOMAIN]["reload_off"]
    await async_unload_entry(hass, dual_entry)

    # 3) Humidifier PWM active at removal
    hass.states.async_set("sensor.room_hum_rm", "72.0")
    hass.states.async_set("switch.dehum_rm", STATE_OFF)
    hum_entry = ConfigEntry(
        entry_id="rm_pwm_hum_1",
        title="Remove PWM Hum",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_DEHUMIDIFIER,
            CONF_INDOOR_SENSOR: "sensor.room_hum_rm",
            CONF_TARGET_DEVICE: "switch.dehum_rm",
            CONF_INITIAL_TARGET_TEMP: 50.0,
            CONF_PWM_DURATION: 900,
            CONF_MIN_ON_TIME: 60,
            CONF_MIN_OFF_TIME: 180,
            CONF_KP: 20.0,
            CONF_KI: 0.0,
            CONF_KE: 0.0,
        },
        version=2,
    )
    assert await async_setup_entry(hass, hum_entry) is True
    hum = SmartPIDHumidifierEntity(hass, hum_entry)
    hum.hass = hass
    hum._switch_guard = SwitchCommandGuard(
        hass, confirm_timeout=0.05, off_retry_delays=(0.01,)
    )
    await hum.async_added_to_hass()
    dt_util.set_utcnow(t_dual + timedelta(seconds=200))
    await hum.async_turn_on()
    assert hum._pwm_state is True
    assert hass.states.get("switch.dehum_rm").state == STATE_ON

    off_calls.clear()
    t_hum = t_dual + timedelta(seconds=240)
    dt_util.set_utcnow(t_hum)
    await hum.async_will_remove_from_hass()
    assert "switch.dehum_rm" in off_calls
    assert hass.states.get("switch.dehum_rm").state == STATE_OFF
    assert hum._pwm_state is False
    assert hass.data[DOMAIN]["reload_off"]["switch.dehum_rm"] == (
        t_hum,
        t_hum + timedelta(seconds=180),
    )
    await async_unload_entry(hass, hum_entry)

    # 4) turn_off failure during removal logs ERROR and does not block unload
    async def failing_off(call):
        raise RuntimeError("zigbee link down")

    hass.services.async_register("switch", "turn_off", failing_off)
    hass.services.async_register("homeassistant", "turn_off", failing_off)
    hass.states.async_set("switch.fail_rm", STATE_ON)

    fail_entry = ConfigEntry(
        entry_id="rm_pwm_fail_1",
        title="Remove PWM Fail",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_INDOOR_SENSOR: "sensor.room_temp_rm",
            CONF_TARGET_DEVICE: "switch.fail_rm",
            CONF_MIN_OFF_TIME: 180,
        },
        version=2,
    )
    assert await async_setup_entry(hass, fail_entry) is True
    fail_clim = SmartPIDThermostatEntity(hass, fail_entry)
    fail_clim.hass = hass
    fail_clim._switch_guard = SwitchCommandGuard(
        hass, confirm_timeout=0.02, off_retry_delays=(0.005,)
    )
    await fail_clim.async_added_to_hass()
    fail_clim._pwm_state = True

    caplog.clear()
    t_fail = t_hum + timedelta(seconds=50)
    dt_util.set_utcnow(t_fail)
    with caplog.at_level(logging.ERROR):
        await fail_clim.async_will_remove_from_hass()

    assert any(
        r.levelno == logging.ERROR and "switch.fail_rm" in r.getMessage()
        for r in caplog.records
    )
    assert "switch.fail_rm" not in hass.data[DOMAIN]["reload_off"]
    assert "switch.fail_rm" in hass.data[DOMAIN]["reload_unverified_off"]
    await async_unload_entry(hass, fail_entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_state", [None, STATE_UNKNOWN, STATE_UNAVAILABLE, ""])
async def test_actuator_unverified_states_fail_safe(hass, caplog, initial_state: str | None) -> None:
    """P1-B: Missing/unknown/unavailable/empty states fail OFF check, trigger retries, and record warnings."""
    eid = "switch.unverified_plug"
    if initial_state is None:
        hass.states._states.pop(eid, None)
    else:
        hass.states.async_set(eid, initial_state)

    turn_off_calls = 0

    async def noop_off(call):
        nonlocal turn_off_calls
        turn_off_calls += 1

    hass.services.async_register("switch", "turn_off", noop_off)
    hass.services.async_register("homeassistant", "turn_off", noop_off)

    guard = SwitchCommandGuard(
        hass, confirm_timeout=0.02, off_retry_delays=(0.005, 0.005)
    )
    assert guard._is_confirmed_off(eid) is False

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        ok = await guard.async_turn_off(eid, confirm=True)

    assert ok is False
    assert turn_off_calls == 3
    assert guard.failure_counts[eid] == 1
    assert guard.last_error[eid] == "unverified_off"
    assert any(
        r.levelno == logging.WARNING and eid in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_actuator_recovers_only_on_explicit_off_state(hass) -> None:
    """P1-B: Explicit STATE_OFF clears errors and succeeds even after initial unknown state."""
    eid = "switch.recover_plug"
    hass.states.async_set(eid, STATE_UNKNOWN)

    calls = 0

    async def recover_off(call):
        nonlocal calls
        calls += 1
        if calls >= 2:
            hass.states.async_set(eid, STATE_OFF)

    hass.services.async_register("switch", "turn_off", recover_off)
    hass.services.async_register("homeassistant", "turn_off", recover_off)

    guard = SwitchCommandGuard(
        hass, confirm_timeout=0.02, off_retry_delays=(0.005, 0.005)
    )
    assert guard._is_confirmed_off(eid) is False
    assert await guard.async_turn_off(eid, confirm=True) is True
    assert calls == 2
    assert guard._is_confirmed_off(eid) is True
    assert eid not in guard.last_error
    assert eid not in guard.failure_counts


@pytest.mark.asyncio
async def test_unload_failure_preserves_runtime_and_ownership(
    hass, monkeypatch, caplog
) -> None:
    """P2-C: Returning False from async_unload_platforms keeps runtime, data, and ownership intact."""
    entry = ConfigEntry(
        entry_id="unload_fail_1",
        title="Unload Fail Entry",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_INDOOR_SENSOR: "sensor.temp_uf",
            CONF_TARGET_DEVICE: "switch.owned_unload",
        },
        version=2,
    )
    assert await async_setup_entry(hass, entry) is True
    runtime = entry.runtime_data
    unload_calls = 0
    orig_runtime_unload = runtime.async_unload

    async def tracked_unload() -> None:
        nonlocal unload_calls
        unload_calls += 1
        await orig_runtime_unload()

    runtime.async_unload = tracked_unload  # type: ignore[method-assign]

    async def fail_unload_platforms(*_args, **_kwargs) -> bool:
        return False

    orig_unload_platforms = hass.config_entries.async_unload_platforms
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", fail_unload_platforms
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        res = await async_unload_entry(hass, entry)

    assert res is False
    assert unload_calls == 0
    assert hass.data[DOMAIN][entry.entry_id] is runtime
    assert hass.data[DOMAIN]["owners"]["switch.owned_unload"] == entry.entry_id
    assert any(
        r.levelno == logging.WARNING and entry.entry_id in r.getMessage()
        for r in caplog.records
    )

    # Duplicate setup while unload failed must remain blocked by preserved ownership
    dup_entry = ConfigEntry(
        entry_id="unload_fail_dup",
        title="Dup",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_INDOOR_SENSOR: "sensor.temp_uf",
            CONF_TARGET_DEVICE: "switch.owned_unload",
        },
        version=2,
    )
    with pytest.raises(ConfigEntryError):
        await async_setup_entry(hass, dup_entry)

    # Normal unload after recovery tears down runtime and releases ownership
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", orig_unload_platforms
    )
    assert await async_unload_entry(hass, entry) is True
    assert unload_calls == 1
    assert entry.entry_id not in hass.data[DOMAIN]
    assert "switch.owned_unload" not in hass.data[DOMAIN]["owners"]


@pytest.mark.asyncio
async def test_setup_failure_rolls_back_runtime_tasks_and_ownership(
    hass, monkeypatch
) -> None:
    """P2-D: Forward setup failure unloads started runtime, cancels tasks, and releases ownership."""
    tick_unsub_calls = 0
    bootstrap_cancelled = False
    captured_tasks: list[asyncio.Task[Any]] = []

    def fake_track_interval(_hass, _cb, _td):
        def _unsub() -> None:
            nonlocal tick_unsub_calls
            tick_unsub_calls += 1

        return _unsub

    async def slow_bootstrap(self) -> None:
        nonlocal bootstrap_cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            bootstrap_cancelled = True
            raise

    monkeypatch.setattr(runtime_mod, "async_track_time_interval", fake_track_interval)
    monkeypatch.setattr(
        runtime_mod.SmartPIDRuntime, "_async_recorder_bootstrap", slow_bootstrap
    )

    orig_create_task = hass.async_create_task

    def spy_create_task(coro, name=None):
        task = orig_create_task(coro, name=name)
        captured_tasks.append(task)
        return task

    monkeypatch.setattr(hass, "async_create_task", spy_create_task)

    orig_forward = hass.config_entries.async_forward_entry_setups

    async def failing_forward(_entry, _platforms) -> None:
        await asyncio.sleep(0)
        raise RuntimeError("platform load failed")

    monkeypatch.setattr(
        hass.config_entries, "async_forward_entry_setups", failing_forward
    )

    entry = ConfigEntry(
        entry_id="setup_fail_rb_1",
        title="Setup Rollback",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_INDOOR_SENSOR: "sensor.temp_rb",
            CONF_TARGET_DEVICE: "switch.rollback_dev",
            CONF_RECORDER_BOOTSTRAP: True,
        },
        version=2,
    )

    with pytest.raises(RuntimeError, match="platform load failed"):
        await async_setup_entry(hass, entry)

    assert tick_unsub_calls == 1
    assert len(captured_tasks) == 1
    assert captured_tasks[0].done() and captured_tasks[0].cancelled()
    assert bootstrap_cancelled is True
    assert entry.entry_id not in hass.data[DOMAIN]
    assert entry.runtime_data is None
    assert "switch.rollback_dev" not in hass.data[DOMAIN]["owners"]

    # Subsequent setup with same target device succeeds after clean rollback
    monkeypatch.setattr(
        hass.config_entries, "async_forward_entry_setups", orig_forward
    )
    retry_entry = ConfigEntry(
        entry_id="setup_fail_rb_2",
        title="Setup Rollback Retry",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_INDOOR_SENSOR: "sensor.temp_rb",
            CONF_TARGET_DEVICE: "switch.rollback_dev",
        },
        version=2,
    )
    assert await async_setup_entry(hass, retry_entry) is True
    assert hass.data[DOMAIN]["owners"]["switch.rollback_dev"] == retry_entry.entry_id
    assert await async_unload_entry(hass, retry_entry) is True
