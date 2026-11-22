"""Stage 6 tests: 2-step coast confirmation, custom inertia, live MAE/rollback, solar guard, platforms, and switch guard."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from custom_components.smart_pid_airconditioner import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smart_pid_airconditioner.actuator import SwitchCommandGuard
from custom_components.smart_pid_airconditioner.button import (
    SmartPIDResetLearningButton,
    async_setup_entry as async_setup_button,
)
from custom_components.smart_pid_airconditioner.climate import SmartPIDThermostatEntity
from custom_components.smart_pid_airconditioner.const import (
    CONF_CONTROL_MODE,
    CONF_CUSTOM_COAST_WINDOW_MIN,
    CONF_CUSTOM_TAU_EM_MIN,
    CONF_INDOOR_SENSOR,
    CONF_INERTIA_PROFILE,
    CONF_INITIAL_TARGET_TEMP,
    CONF_KE,
    CONF_KI,
    CONF_KP,
    CONF_MAX_I,
    CONF_PREDICTION_MODE,
    CONF_SOLAR_GUARD,
    CONF_TARGET_DEVICE,
    CONF_THERMOSTAT_TYPE,
    CONF_TUNING_MODE,
    CONTROL_MODE_SWITCH_PWM,
    THERMOSTAT_TYPE_COOLER,
    THERMOSTAT_TYPE_HEATER,
    TUNING_MODE_AUTO,
    TUNING_MODE_SHADOW,
)
from custom_components.smart_pid_airconditioner.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.smart_pid_airconditioner.learner import (
    CyclePhase,
    DynamicCoastingLearner,
    INERTIA_CUSTOM,
    INERTIA_FAST_AIR,
)
from custom_components.smart_pid_airconditioner.runtime import SmartPIDRuntime
from custom_components.smart_pid_airconditioner.select import (
    SmartPIDPredictionModeSelect,
    SmartPIDTuningModeSelect,
    async_setup_entry as async_setup_select,
)
from custom_components.smart_pid_airconditioner.sensor import (
    SmartPIDConfidenceSensor,
    SmartPIDCyclePhaseSensor,
    async_setup_entry as async_setup_sensor,
)
from custom_components.smart_pid_airconditioner.ventilation import VentState, VentStatus
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
import homeassistant.util.dt as dt_util


def _step(
    learner: DynamicCoastingLearner,
    is_active: bool,
    u_effort: float,
    value: float,
    is_heating: bool = True,
    dt_min: float = 1.0,
    blocked: bool = False,
    **kwargs,
) -> CyclePhase:
    return learner.step_cycle(
        is_active=is_active,
        u_effort=u_effort,
        value=value,
        is_heating=is_heating,
        dt_min=dt_min,
        blocked_for_learning=blocked,
        **kwargs,
    )


def _obs(
    rt: SmartPIDRuntime,
    indoor_val: float,
    outdoor_val: float | None,
    target_val: float | None,
    u_effort: float,
    is_active: bool,
    is_heating: bool,
    vent_status: VentStatus,
    **kwargs,
) -> None:
    rt.update_observation(
        indoor_val=indoor_val,
        outdoor_val=outdoor_val,
        target_val=target_val,
        u_effort=u_effort,
        is_active=is_active,
        is_heating=is_heating,
        vent_status=vent_status,
        **kwargs,
    )


def test_coasting_two_step_peak_confirmation() -> None:
    """Single dip must not terminate coasting early; 2 consecutive steps confirm peak."""
    learner = DynamicCoastingLearner(
        inertia_profile=INERTIA_FAST_AIR,
        peak_confirm_delta=0.1,
    )
    # 5 min active heating
    for i in range(5):
        _step(learner, True, 1.0, 20.0 + i * 0.1)
    # Stop -> enter POST_STOP_COAST (stop_value = 20.5)
    _step(learner, False, 0.0, 20.5)
    assert learner.phase == CyclePhase.POST_STOP_COAST

    # Coast up to 20.8, then single noisy dip to 20.68 followed by bounce to 20.76
    _step(learner, False, 0.0, 20.80)
    _step(learner, False, 0.0, 20.68)
    assert learner.phase == CyclePhase.POST_STOP_COAST
    assert learner.accepted_count == 0

    _step(learner, False, 0.0, 20.76)
    assert learner.phase == CyclePhase.POST_STOP_COAST
    assert learner.accepted_count == 0

    # Continue peaking at 20.90, then 2 consecutive reversal drops >= 0.1 each
    _step(learner, False, 0.0, 20.90)
    _step(learner, False, 0.0, 20.78)
    assert learner.phase == CyclePhase.POST_STOP_COAST  # 1st step
    _step(learner, False, 0.0, 20.66)
    assert learner.phase == CyclePhase.PASSIVE_DRIFT  # 2nd step confirmed
    assert learner.accepted_count == 1
    assert learner.history[-1]["obs_coast"] == pytest.approx(0.4, abs=1e-3)


@pytest.mark.asyncio
async def test_custom_inertia_profile_and_storage_persistence(hass) -> None:
    """Verify custom inertia tau/window configuration and reload persistence."""
    entry = ConfigEntry(
        entry_id="custom_inertia_entry",
        title="Custom Radiator",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INERTIA_PROFILE: INERTIA_CUSTOM,
            CONF_CUSTOM_TAU_EM_MIN: 42,
            CONF_CUSTOM_COAST_WINDOW_MIN: 150,
            CONF_TUNING_MODE: TUNING_MODE_SHADOW,
            CONF_KP: 15.0,
            CONF_KI: 0.4,
            CONF_KE: 0.5,
        },
        version=2,
    )
    rt = SmartPIDRuntime(hass, entry)
    await rt.async_setup()

    assert rt._resolve_coasting_settings() == (42.0, 150.0, 0.1)
    assert rt.snapshot.coasting.inertia_profile == INERTIA_CUSTOM
    assert rt.snapshot.coasting.tau_em_min == 42.0
    assert rt.snapshot.coasting.max_coast_window_min == 150.0

    # Active heating to charge emitter, then stop and verify coast tracks beyond 45m up to 150m
    c = rt.snapshot.coasting
    for i in range(10):
        _step(c, True, 1.0, 20.0 + i * 0.05)
    _step(c, False, 0.0, 20.5)
    for _ in range(80):
        _step(c, False, 0.0, 20.55)
    assert c.phase == CyclePhase.POST_STOP_COAST

    for _ in range(70):
        _step(c, False, 0.0, 20.55)
    assert c.phase == CyclePhase.PASSIVE_DRIFT

    await rt.store.async_save(rt.snapshot)
    await rt.async_unload()

    # Reload from storage and verify custom parameters remain intact
    rt2 = SmartPIDRuntime(hass, entry)
    await rt2.async_setup()
    assert rt2.snapshot.coasting.inertia_profile == INERTIA_CUSTOM
    assert rt2.snapshot.coasting.tau_em_min == 42.0
    assert rt2.snapshot.coasting.max_coast_window_min == 150.0
    await rt2.async_unload()


def test_outlier_coasting_quarantine_and_corroboration() -> None:
    """Single extreme coast excursion is quarantined until a second matching event corroborates it."""
    learner = DynamicCoastingLearner(
        inertia_profile=INERTIA_FAST_AIR,
        peak_confirm_delta=0.1,
    )
    # 3 baseline cycles of ~0.4°C coasting
    for _ in range(3):
        for i in range(5):
            _step(learner, True, 1.0, 20.0 + i * 0.1)
        _step(learner, False, 0.0, 20.5)
        _step(learner, False, 0.0, 20.9)
        _step(learner, False, 0.0, 20.78)
        _step(learner, False, 0.0, 20.66)
        learner.emitter_charge = 0.0
        _step(learner, False, 0.0, 20.6)

    assert learner.accepted_count == 3

    # Cycle 4: uncorroborated extreme coast (2.4°C vs median 0.4°C)
    for i in range(5):
        _step(learner, True, 1.0, 20.0 + i * 0.1)
    _step(learner, False, 0.0, 20.5)
    _step(learner, False, 0.0, 22.9)
    _step(learner, False, 0.0, 22.78)
    _step(learner, False, 0.0, 22.65)
    assert learner.last_quality_reason == "inconsistent_coast_outlier"
    assert learner.accepted_count == 3

    # Cycle 5: similar 2.4°C coasting corroborates the outlier -> accepted!
    learner.emitter_charge = 0.0
    _step(learner, False, 0.0, 20.5)
    for i in range(5):
        _step(learner, True, 1.0, 20.0 + i * 0.1)
    _step(learner, False, 0.0, 20.5)
    _step(learner, False, 0.0, 22.9)
    _step(learner, False, 0.0, 22.78)
    _step(learner, False, 0.0, 22.65)
    assert learner.last_quality_reason == "accepted"
    assert learner.accepted_count == 4


@pytest.mark.asyncio
async def test_live_cycle_completion_updates_mae_and_triggers_auto_rollback(hass) -> None:
    """Completed cycles in async_step_learning feed safety MAE and trigger 5-cycle auto-rollback."""
    entry = ConfigEntry(
        entry_id="live_rollback_entry",
        title="Auto Rollback Room",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INERTIA_PROFILE: INERTIA_FAST_AIR,
            CONF_TUNING_MODE: TUNING_MODE_AUTO,
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KE: 0.6,
        },
        version=2,
    )
    rt = SmartPIDRuntime(hass, entry)
    await rt.async_setup()

    # Train enough samples/cycles to reach ConfidenceStage.TUNED
    rls = rt._active_rls()
    for _ in range(30):
        rls.update([1.0, -5.0, 0.0], 0.08)
    rt.recommended = rt.tuner.compute(rls, prediction_mode=rt.prediction_mode, baseline=rt.baseline)

    c = rt.snapshot.coasting
    for _ in range(10):
        c.history.append({"obs_coast": 0.3, "coast_minutes": 3.0})
    c.accepted_count = 10
    c.tau_coast = 1.0
    rt.snapshot.safety.record_prediction_comparison(0.25, 0.30)

    # Enter adaptive_auto application
    eff_kp, eff_ki, eff_ke, reason = rt.get_effective_pid_params(20.0, 0.5, 0.6)
    assert reason is None
    assert rt.snapshot.safety.active_snapshot is not None
    assert (eff_kp, eff_ki, eff_ke) != (20.0, 0.5, 0.6) or rt._applied_auto_params is not None

    normal_vent = VentStatus(VentState.NORMAL, False, False, False, 0)

    # Complete 5 degraded cycles live through async_step_learning() (overshoot > 0.4 * 1.3 = 0.52)
    cycles_completed = 0
    for _ in range(8):
        if cycles_completed >= 5:
            break
        c.phase = CyclePhase.IDLE
        c.emitter_charge = 0.0
        for step_i in range(5):
            _obs(rt, 20.0 + step_i * 0.1, 15.0, 21.0, 1.0, True, True, normal_vent)
            await rt.async_step_learning()

        # Stop near target, then overshoot heavily to 21.8 (target=21.0 -> overshoot=0.8)
        for val in (20.5, 21.8, 21.65, 21.50):
            _obs(rt, val, 15.0, 21.0, 0.0, False, True, normal_vent)
            await rt.async_step_learning()
        if c.last_quality_reason == "accepted":
            cycles_completed += 1

    assert cycles_completed == 5
    assert rt.snapshot.safety.accuracy_count >= 5
    assert rt.snapshot.safety.mean_absolute_error is not None
    assert rt.snapshot.safety.rollback_count == 1
    assert rt.fallback_reason == "auto_rollback_performance_degraded"
    reverted = rt.get_effective_pid_params(20.0, 0.5, 0.6)
    assert reverted[:3] == (20.0, 0.5, 0.6)
    await rt.async_unload()


@pytest.mark.asyncio
async def test_solar_guard_rejects_daytime_heating_coast(hass) -> None:
    """Verify solar_guard rejects daytime heating coasting surges caused by sun.sun."""
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 25.0})
    entry = ConfigEntry(
        entry_id="solar_guard_entry",
        title="Sunny Living Room",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_HEATER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_SOLAR_GUARD: True,
        },
        version=2,
    )
    rt = SmartPIDRuntime(hass, entry)
    await rt.async_setup()
    normal_vent = VentStatus(VentState.NORMAL, False, False, False, 0)

    for i in range(5):
        _obs(rt, 20.0 + i * 0.1, 10.0, 21.0, 1.0, True, True, normal_vent)
        await rt.async_step_learning()

    _obs(rt, 20.5, 10.0, 21.0, 0.0, False, True, normal_vent)
    await rt.async_step_learning()
    # Huge solar surge (+1.2°C after stop) during daytime
    _obs(rt, 21.7, 10.0, 21.0, 0.0, False, True, normal_vent)
    await rt.async_step_learning()

    assert rt.snapshot.coasting.last_quality_reason == "solar_gain_contamination"
    assert rt.snapshot.coasting.accepted_count == 0
    await rt.async_unload()


@pytest.mark.asyncio
async def test_button_select_sensor_and_diagnostics_platforms(hass) -> None:
    """Verify button, select, sensor attributes, and Download Diagnostics end-to-end."""
    entry = ConfigEntry(
        entry_id="ui_platforms_entry",
        title="Study AC",
        data={
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_INERTIA_PROFILE: INERTIA_CUSTOM,
            CONF_CUSTOM_TAU_EM_MIN: 30,
            CONF_CUSTOM_COAST_WINDOW_MIN: 120,
            CONF_TUNING_MODE: TUNING_MODE_SHADOW,
            CONF_PREDICTION_MODE: "balanced",
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_KE: 0.5,
        },
        version=2,
    )
    assert await async_setup_entry(hass, entry) is True
    runtime: SmartPIDRuntime = entry.runtime_data
    runtime._active_rls().update([0.8, 2.0, 0.0], -0.05)
    runtime.snapshot.safety.record_prediction_comparison(0.4, 0.35)

    buttons: list[SmartPIDResetLearningButton] = []
    selects: list = []
    sensors: list = []

    await async_setup_button(hass, entry, lambda ents, update=False: buttons.extend(ents))
    await async_setup_select(hass, entry, lambda ents, update=False: selects.extend(ents))
    await async_setup_sensor(hass, entry, lambda ents, update=False: sensors.extend(ents))

    assert len(buttons) == 1
    assert len(selects) == 2
    tuning_sel = next(s for s in selects if isinstance(s, SmartPIDTuningModeSelect))
    pred_sel = next(s for s in selects if isinstance(s, SmartPIDPredictionModeSelect))
    conf_sensor = next(s for s in sensors if isinstance(s, SmartPIDConfidenceSensor))
    phase_sensor = next(s for s in sensors if isinstance(s, SmartPIDCyclePhaseSensor))

    # Live reload-free mode updates via select entities
    await tuning_sel.async_select_option(TUNING_MODE_AUTO)
    await pred_sel.async_select_option("comfort")
    assert runtime.tuning_mode == TUNING_MODE_AUTO
    assert runtime.prediction_mode == "comfort"
    assert entry.options[CONF_TUNING_MODE] == TUNING_MODE_AUTO
    assert entry.options[CONF_PREDICTION_MODE] == "comfort"

    # Sensor attributes include new MAE and custom coast timing fields
    conf_attrs = conf_sensor.extra_state_attributes
    assert conf_attrs["mean_absolute_error"] == pytest.approx(0.05)
    assert conf_attrs["comparison_count"] == 1
    assert conf_attrs["last_predicted_coast"] == 0.4
    assert conf_attrs["last_observed_coast"] == 0.35

    phase_attrs = phase_sensor.extra_state_attributes
    assert phase_attrs["inertia_profile"] == INERTIA_CUSTOM
    assert phase_attrs["tau_em_min"] == 30.0
    assert phase_attrs["max_coast_window_min"] == 120.0

    # Diagnostics payload
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["runtime"]["confidence"]["percent"] >= 0.0
    assert diag["runtime"]["runtime_config"]["tau_em_min"] == 30.0
    assert diag["runtime"]["mean_absolute_error"] == pytest.approx(0.05)

    # Reset button clears learned data & MAE
    await buttons[0].async_press()
    assert runtime._active_rls().sample_count == 0
    assert runtime.snapshot.safety.accuracy_count == 0
    assert runtime.snapshot.safety.mean_absolute_error is None

    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_switch_command_guard_preemption_retry_and_recovery(hass) -> None:
    """Verify SwitchCommandGuard handles ON failure OFF recovery, OFF preemption, and OFF retry."""
    guard = SwitchCommandGuard(
        hass,
        confirm_timeout=0.08,
        off_retry_delays=(0.01, 0.02),
    )

    # 1. ON failure (domain + homeassistant fallback) triggers automatic safety OFF recovery
    hass.states.async_set("switch.guard_plug", "off")
    off_calls: list[str] = []

    async def fail_on(call):
        raise RuntimeError("zigbee timeout")

    async def handle_off(call):
        off_calls.append(call.data["entity_id"])
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.handlers[("switch", "turn_on")] = fail_on
    hass.services.handlers[("homeassistant", "turn_on")] = fail_on
    hass.services.handlers[("switch", "turn_off")] = handle_off

    ok_on = await guard.async_turn_on("switch.guard_plug", confirm=False)
    assert ok_on is False
    assert off_calls == ["switch.guard_plug"]

    # 2. OFF preempts pending ON confirmation
    hass.states.async_set("switch.guard_plug", "off")

    async def slow_on(call):
        return None

    hass.services.handlers[("switch", "turn_on")] = slow_on
    hass.services.handlers[("homeassistant", "turn_on")] = slow_on
    on_task = asyncio.create_task(guard.async_turn_on("switch.guard_plug", confirm=True))
    await asyncio.sleep(0.02)
    off_ok = await guard.async_turn_off("switch.guard_plug", confirm=True)
    on_res = await on_task
    assert off_ok is True
    assert on_res is False

    # 3. OFF retry recovers when first turn_off fails transiently on both switch & fallback
    hass.states.async_set("switch.guard_plug", "on")
    attempts = 0

    async def flaky_off(call):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise RuntimeError("transient drop")
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.handlers[("switch", "turn_off")] = flaky_off
    hass.services.handlers[("homeassistant", "turn_off")] = flaky_off
    assert await guard.async_turn_off("switch.guard_plug", confirm=True) is True
    assert attempts >= 3


@pytest.mark.asyncio
async def test_device_unavailable_grace_period(hass) -> None:
    """Verify brief <60s switch unavailability keeps PWM state and only >=60s marks learning blocked."""
    t0 = dt_util.utcnow()
    hass.states.async_set("sensor.room_temp", "26.0")
    hass.states.async_set("switch.ac_plug", "on")

    entry = ConfigEntry(
        entry_id="unavail_grace_entry",
        title="Grace Plug",
        data={
            CONF_CONTROL_MODE: CONTROL_MODE_SWITCH_PWM,
            CONF_THERMOSTAT_TYPE: THERMOSTAT_TYPE_COOLER,
            CONF_INDOOR_SENSOR: "sensor.room_temp",
            CONF_TARGET_DEVICE: "switch.ac_plug",
            CONF_INITIAL_TARGET_TEMP: 24.0,
            CONF_KP: 20.0,
            CONF_KI: 0.5,
            CONF_MAX_I: 20.0,
            CONF_KE: 0.0,
        },
        version=2,
    )
    assert await async_setup_entry(hass, entry) is True
    rt: SmartPIDRuntime = entry.runtime_data
    entity = SmartPIDThermostatEntity(hass, entry)
    entity.hass = hass
    entity._switch_guard = SwitchCommandGuard(hass, confirm_timeout=0.02, off_retry_delays=(0.01,))
    await entity.async_added_to_hass()
    await entity.async_set_hvac_mode(HVACMode.COOL)
    entity._pwm_state = True

    # Active learning step puts coasting learner into ACTIVE phase
    _obs(
        rt,
        26.0,
        None,
        24.0,
        0.5,
        True,
        False,
        VentStatus(VentState.NORMAL, False, False, False, 0),
        device_unavailable=False,
    )
    await rt.async_step_learning()
    assert rt.snapshot.coasting.phase == CyclePhase.ACTIVE

    # Brief unavailability (< 60s): _pwm_state stays True and learning is not yet blocked
    hass.states.async_set("switch.ac_plug", "unavailable")
    entity._async_entity_changed(
        type("Ev", (), {"data": {"entity_id": "switch.ac_plug", "new_state": hass.states.get("switch.ac_plug")}, "context": None})()
    )
    assert entity._pwm_state is True
    assert entity._device_unavailable is True

    _obs(
        rt,
        26.0,
        None,
        24.0,
        0.5,
        True,
        False,
        VentStatus(VentState.NORMAL, False, False, False, 0),
        device_unavailable=True,
    )
    assert rt._obs_device_unavailable is False

    # After 65s still unavailable -> learning marked device_unavailable
    dt_util.set_utcnow(t0 + timedelta(seconds=65))
    _obs(
        rt,
        26.0,
        None,
        24.0,
        0.5,
        True,
        False,
        VentStatus(VentState.NORMAL, False, False, False, 0),
        device_unavailable=True,
    )
    assert rt._obs_device_unavailable is True
    assert rt.snapshot.coasting.last_quality_reason == "device_unavailable"
    await async_unload_entry(hass, entry)
