"""Shared PID computation, section mapping, sensor recovery, and PWM helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

from .const import (
    DEFAULT_TAU_D,
    HYSTERESIS_BAND,
    MIN_DERIVATIVE_EVENT_DT,
    MIN_DWELL_ACTIVE,
    MIN_DWELL_SEC0,
)


@dataclass
class PIDStepResult:
    """Output of a single PID calculation step."""

    error: float
    p_term: float
    i_term: float
    d_term: float
    e_term: float
    raw_output: float
    final_output: float
    integral: float
    filtered_d: float
    last_d_calc_time: datetime | None
    prev_val: float | None


def _is_finite_float(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def compute_pid_step(
    *,
    indoor_val: float,
    target_val: float,
    outdoor_val: float | None,
    has_outdoor_sensor: bool,
    is_heating: bool,
    kp: float,
    ki: float,
    kd: float,
    ke: float,
    max_i: float,
    dt: float,
    sample_period: int,
    now: datetime,
    force: bool,
    integral: float,
    filtered_d: float,
    last_d_calc_time: datetime | None,
    prev_val: float | None,
    freeze_integral: bool,
    per_minute_integral: bool = True,
) -> PIDStepResult:
    """Compute one PID + outdoor feedforward step with LPF derivative and anti-windup."""
    safe_integral = float(integral) if _is_finite_float(integral) else 0.0
    safe_filtered_d = float(filtered_d) if _is_finite_float(filtered_d) else 0.0
    safe_prev_val = float(prev_val) if (prev_val is not None and _is_finite_float(prev_val)) else None
    safe_outdoor = float(outdoor_val) if (outdoor_val is not None and _is_finite_float(outdoor_val)) else None

    if not (
        _is_finite_float(indoor_val)
        and _is_finite_float(target_val)
        and _is_finite_float(kp)
        and _is_finite_float(ki)
        and _is_finite_float(kd)
        and _is_finite_float(ke)
        and _is_finite_float(max_i)
        and _is_finite_float(dt)
    ):
        return PIDStepResult(
            error=0.0,
            p_term=0.0,
            i_term=0.0,
            d_term=0.0,
            e_term=0.0,
            raw_output=0.0,
            final_output=0.0,
            integral=safe_integral,
            filtered_d=safe_filtered_d,
            last_d_calc_time=last_d_calc_time,
            prev_val=safe_prev_val,
        )

    integral = safe_integral
    filtered_d = safe_filtered_d
    prev_val = safe_prev_val
    outdoor_val = safe_outdoor

    if is_heating:
        error = target_val - indoor_val
        e_term = ke * (target_val - outdoor_val) if (has_outdoor_sensor and outdoor_val is not None) else 0.0
    else:
        error = indoor_val - target_val
        e_term = ke * (outdoor_val - target_val) if (has_outdoor_sensor and outdoor_val is not None) else 0.0

    p_term = kp * error

    # Derivative on measurement with 1st-order LPF (tau_d=120s) and <10s spike guard
    d_dt = (now - last_d_calc_time).total_seconds() if last_d_calc_time is not None else dt
    new_filtered_d = filtered_d
    new_last_d_calc_time = last_d_calc_time
    new_prev_val = prev_val

    if not force and last_d_calc_time is not None and d_dt < MIN_DERIVATIVE_EVENT_DT:
        d_term = filtered_d
    else:
        effective_d_dt = max(MIN_DERIVATIVE_EVENT_DT, d_dt)
        d_dt_norm = (effective_d_dt / float(sample_period)) if sample_period > 0 else 1.0
        d_dt_norm = max(0.1, d_dt_norm)
        if prev_val is not None:
            diff_val = indoor_val - prev_val
            raw_d = -kd * (diff_val / d_dt_norm) if is_heating else kd * (diff_val / d_dt_norm)
            alpha = effective_d_dt / (DEFAULT_TAU_D + effective_d_dt)
            new_filtered_d = alpha * raw_d + (1.0 - alpha) * filtered_d
            d_term = new_filtered_d
        else:
            d_term = 0.0
        new_last_d_calc_time = now
        new_prev_val = indoor_val

    # Integral with bidirectional [-max_i, +max_i] clamping and anti-windup
    max_i_limit = max(0.0, float(max_i))
    new_integral = integral

    if per_minute_integral:
        if freeze_integral:
            new_integral = max(-max_i_limit, min(max_i_limit, integral))
        else:
            dt_min = dt / 60.0
            candidate_i = max(-max_i_limit, min(max_i_limit, integral + (ki * error * dt_min)))
            candidate_raw = p_term + candidate_i + d_term + e_term
            if (candidate_raw >= 100.0 and error > 0) or (candidate_raw <= 0.0 and error < 0):
                new_integral = max(-max_i_limit, min(max_i_limit, integral))
            else:
                new_integral = candidate_i
        i_term = new_integral
    else:
        dt_norm = (dt / float(sample_period)) if sample_period > 0 else 1.0
        if dt_norm < 0.05:
            dt_norm = 0.05
        if freeze_integral:
            raw_i_term = ki * integral
            i_term = max(-max_i_limit, min(max_i_limit, raw_i_term))
        else:
            tentative_output = p_term + (ki * integral) + d_term + e_term
            is_saturated_high = tentative_output >= 100.0
            is_saturated_low = tentative_output <= 0.0
            if not ((is_saturated_high and error > 0) or (is_saturated_low and error < 0)):
                new_integral = integral + error * dt_norm
                if ki > 0:
                    max_int_raw = max_i_limit / ki
                    new_integral = max(-max_int_raw, min(max_int_raw, new_integral))
                elif max_i_limit == 0.0:
                    new_integral = 0.0
            raw_i_term = ki * new_integral
            i_term = max(-max_i_limit, min(max_i_limit, raw_i_term))

    raw_output = p_term + i_term + d_term + e_term
    if not (
        math.isfinite(raw_output)
        and math.isfinite(error)
        and math.isfinite(p_term)
        and math.isfinite(i_term)
        and math.isfinite(d_term)
        and math.isfinite(e_term)
        and math.isfinite(new_integral)
        and math.isfinite(new_filtered_d)
    ):
        return PIDStepResult(
            error=0.0,
            p_term=0.0,
            i_term=0.0,
            d_term=0.0,
            e_term=0.0,
            raw_output=0.0,
            final_output=0.0,
            integral=safe_integral,
            filtered_d=safe_filtered_d,
            last_d_calc_time=last_d_calc_time,
            prev_val=safe_prev_val,
        )
    final_output = max(0.0, min(100.0, raw_output))

    return PIDStepResult(
        error=error,
        p_term=p_term,
        i_term=i_term,
        d_term=d_term,
        e_term=e_term,
        raw_output=raw_output,
        final_output=final_output,
        integral=new_integral,
        filtered_d=new_filtered_d,
        last_d_calc_time=new_last_d_calc_time,
        prev_val=new_prev_val,
    )


def determine_active_section(
    *,
    output: float,
    current_sec: int,
    sec1_max: float,
    sec2_max: float,
    sec3_max: float,
    sec0_threshold: float = 0.0,
    section_entered_at: datetime | None = None,
    now: datetime | None = None,
    force_sec0: bool = False,
    allow_sec0: bool = True,
    band: float = HYSTERESIS_BAND,
) -> int:
    """Determine active section (0..4 or 1..4) with hysteresis deadband and dwell protection."""
    if force_sec0 and allow_sec0:
        return 0

    if current_sec not in (0, 1, 2, 3, 4):
        if output <= sec1_max:
            return 1
        if output <= sec2_max:
            return 2
        if output <= sec3_max:
            return 3
        return 4

    if allow_sec0 and current_sec == 0:
        if (
            section_entered_at is not None
            and now is not None
            and (now - section_entered_at).total_seconds() < MIN_DWELL_SEC0
        ):
            return 0
        if output <= sec0_threshold + band:
            return 0
        if output <= sec1_max:
            return 1
        if output <= sec2_max:
            return 2
        if output <= sec3_max:
            return 3
        return 4

    if (
        allow_sec0
        and output <= sec0_threshold
        and section_entered_at is not None
        and now is not None
        and (now - section_entered_at).total_seconds() >= MIN_DWELL_ACTIVE
    ):
        return 0

    if current_sec == 1:
        if output > sec3_max + band:
            return 4
        if output > sec2_max + band:
            return 3
        if output > sec1_max + band:
            return 2
        return 1

    if current_sec == 2:
        if output > sec3_max + band:
            return 4
        if output > sec2_max + band:
            return 3
        if output < sec1_max - band:
            return 1
        return 2

    if current_sec == 3:
        if output > sec3_max + band:
            return 4
        if output < sec1_max - band:
            return 1
        if output < sec2_max - band:
            return 2
        return 3

    if current_sec == 4:
        if output < sec1_max - band:
            return 1
        if output < sec2_max - band:
            return 2
        if output < sec3_max - band:
            return 3
        return 4

    return current_sec


def compute_effective_min_off(
    min_off_time: float,
    last_run_duration_sec: float,
    dynamic_multiplier: float,
) -> float:
    """Compute effective minimum OFF time including dynamic run-duration scaling."""
    base = max(0.0, float(min_off_time))
    mult = max(0.0, float(dynamic_multiplier))
    if mult <= 0.0 or last_run_duration_sec <= 0.0:
        return base
    return max(base, float(last_run_duration_sec) * mult)


def evaluate_sensor_recovery(
    *,
    parsed: float | None,
    fault_active: bool,
    recovery_first_at: datetime | None,
    now: datetime,
    min_recovery_gap_sec: float = 30.0,
    report_at: datetime | None = None,
) -> tuple[bool, bool, datetime | None]:
    """Evaluate 2-reading sensor fault recovery.

    Returns (accept_value, new_fault_active, new_recovery_first_at).
    """
    if parsed is None or not _is_finite_float(parsed):
        return False, True, None

    if not fault_active:
        return True, False, None

    sample_at = report_at if report_at is not None else now
    if recovery_first_at is None:
        return False, True, sample_at

    if (sample_at - recovery_first_at).total_seconds() >= min_recovery_gap_sec:
        return True, False, None

    return False, True, recovery_first_at
