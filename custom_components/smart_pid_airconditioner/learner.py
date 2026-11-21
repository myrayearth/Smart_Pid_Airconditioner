"""Pure-Python self-learning engine: Huber-RLS, Dynamic Coasting, SIMC Auto-Tuner & Safety Manager."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any

INERTIA_FAST_AIR = "fast_air"
INERTIA_HIGH_INERTIA = "high_inertia"
INERTIA_PROFILES = (INERTIA_FAST_AIR, INERTIA_HIGH_INERTIA)

PREDICTION_MODES = ("eco", "balanced", "comfort")
_TAU_C_FACTORS = {
    "eco": 2.0,
    "balanced": 1.5,
    "comfort": 1.0,
}


class CyclePhase(str, Enum):
    """4-phase thermal/moisture cycle tracker."""

    IDLE = "idle"
    ACTIVE = "active"
    POST_STOP_COAST = "post_stop_coast"
    PASSIVE_DRIFT = "passive_drift"


class ConfidenceStage(str, Enum):
    """Learning confidence tiers."""

    COLLECTING = "collecting"
    STABLE = "stable"
    TUNED = "tuned"
    OPTIMIZED = "optimized"


class HuberRLS:
    """Pure-Python Recursive Least Squares with Huber loss & hard outlier rejection."""

    def __init__(
        self,
        n_features: int = 2,
        initial_theta: list[float] | None = None,
        bounds: list[tuple[float, float]] | None = None,
        forgetting: float = 0.992,
        p_min: float = 1e-6,
        p_max: float = 50.0,
        warmup_samples: int = 20,
        huber_delta: float = 0.04,
        hard_reject: float = 0.12,
    ) -> None:
        self.n_features = n_features
        self.forgetting = float(forgetting)
        self.p_min = float(p_min)
        self.p_max = float(p_max)
        self.warmup_samples = int(warmup_samples)
        self.huber_delta = float(huber_delta)
        self.hard_reject = float(hard_reject)

        if initial_theta is not None:
            self.theta = [float(v) for v in initial_theta]
        else:
            self.theta = [0.0] * n_features

        if bounds is not None:
            self.bounds = [(float(lo), float(hi)) for lo, hi in bounds]
        else:
            self.bounds = [(-1e6, 1e6)] * n_features

        # Diagonal-initialized covariance matrix
        self.P: list[list[float]] = [
            [10.0 if i == j else 0.0 for j in range(n_features)]
            for i in range(n_features)
        ]
        self.sample_count: int = 0
        self.rejected_outliers: int = 0
        self.last_error: float = 0.0

    def predict(self, x: list[float]) -> float:
        """Compute theta^T * x."""
        n = min(len(x), len(self.theta))
        return sum(self.theta[i] * float(x[i]) for i in range(n))

    def _clamp_theta(self) -> None:
        for i in range(min(len(self.theta), len(self.bounds))):
            lo, hi = self.bounds[i]
            self.theta[i] = max(lo, min(hi, self.theta[i]))

    def _clamp_cov(self) -> None:
        for i in range(self.n_features):
            self.P[i][i] = max(self.p_min, min(self.p_max, self.P[i][i]))

    def update(self, x: list[float], y: float) -> bool:
        """Update RLS with feature vector x and target y. Returns True if accepted."""
        if len(x) < self.n_features:
            x_vec = [float(v) for v in x] + [0.0] * (self.n_features - len(x))
        else:
            x_vec = [float(x[i]) for i in range(self.n_features)]

        y_val = float(y)
        if not math.isfinite(y_val) or any(not math.isfinite(v) for v in x_vec):
            return False

        y_hat = self.predict(x_vec)
        err = y_val - y_hat
        self.last_error = err
        abs_err = abs(err)

        # Outlier defense after warmup
        weight = 1.0
        if self.sample_count >= self.warmup_samples:
            if abs_err > self.hard_reject:
                self.rejected_outliers += 1
                return False
            if abs_err > self.huber_delta:
                weight = self.huber_delta / abs_err

        # Weighted RLS update: P_k = (P - k * x^T * P) / lambda
        # Effective forgetting & gain scaled by weight
        px = [
            sum(self.P[i][j] * x_vec[j] for j in range(self.n_features))
            for i in range(self.n_features)
        ]
        x_px = sum(x_vec[i] * px[i] for i in range(self.n_features))
        denom = (self.forgetting / weight) + x_px
        if denom <= 1e-12:
            return False

        k = [px[i] / denom for i in range(self.n_features)]

        for i in range(self.n_features):
            self.theta[i] += k[i] * err

        inv_lambda = 1.0 / self.forgetting
        new_p: list[list[float]] = [[0.0] * self.n_features for _ in range(self.n_features)]
        for i in range(self.n_features):
            for j in range(self.n_features):
                val = (self.P[i][j] - k[i] * px[j]) * inv_lambda
                new_p[i][j] = val
        # Symmetrize
        for i in range(self.n_features):
            for j in range(i + 1, self.n_features):
                avg = 0.5 * (new_p[i][j] + new_p[j][i])
                new_p[i][j] = avg
                new_p[j][i] = avg
        self.P = new_p
        self._clamp_cov()
        self._clamp_theta()
        self.sample_count += 1
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_features": self.n_features,
            "forgetting": self.forgetting,
            "p_min": self.p_min,
            "p_max": self.p_max,
            "warmup_samples": self.warmup_samples,
            "huber_delta": self.huber_delta,
            "hard_reject": self.hard_reject,
            "theta": list(self.theta),
            "bounds": [list(b) for b in self.bounds],
            "P": [list(row) for row in self.P],
            "sample_count": self.sample_count,
            "rejected_outliers": self.rejected_outliers,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HuberRLS:
        obj = cls(
            n_features=int(data.get("n_features", 2)),
            initial_theta=data.get("theta"),
            bounds=[tuple(b) for b in data["bounds"]] if "bounds" in data else None,
            forgetting=float(data.get("forgetting", 0.992)),
            p_min=float(data.get("p_min", 1e-6)),
            p_max=float(data.get("p_max", 50.0)),
            warmup_samples=int(data.get("warmup_samples", 20)),
            huber_delta=float(data.get("huber_delta", 0.04)),
            hard_reject=float(data.get("hard_reject", 0.12)),
        )
        if "P" in data and isinstance(data["P"], list):
            obj.P = [[float(v) for v in row] for row in data["P"]]
        obj.sample_count = int(data.get("sample_count", 0))
        obj.rejected_outliers = int(data.get("rejected_outliers", 0))
        return obj


class DynamicCoastingLearner:
    """Tracks 4-phase cycles and learns coasting (afterglow) for fast_air & high_inertia."""

    def __init__(
        self,
        inertia_profile: str = INERTIA_FAST_AIR,
        tau_em_min: float = 35.0,
        ewma_alpha: float = 0.25,
        min_run_min: float = 5.0,
        max_coast_window_min: float = 45.0,
        max_pred: float = 3.0,
        min_slope: float = 0.005,
        min_charge: float = 0.05,
    ) -> None:
        self.inertia_profile = (
            inertia_profile if inertia_profile in INERTIA_PROFILES else INERTIA_FAST_AIR
        )
        self.tau_em_min = float(tau_em_min)
        self.ewma_alpha = float(ewma_alpha)
        self.min_run_min = float(min_run_min)
        self.max_coast_window_min = float(max_coast_window_min)
        self.max_pred = float(max_pred)
        self.min_slope = float(min_slope)
        self.min_charge = float(min_charge)

        self.phase: CyclePhase = CyclePhase.IDLE
        self.tau_coast: float = 3.0 if self.inertia_profile == INERTIA_FAST_AIR else 1.5
        self.k_afterglow: float = 1.0
        self.emitter_charge: float = 0.0

        self.accepted_count: int = 0
        self.rejected_counts: dict[str, int] = {}
        self.last_quality_reason: str | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=50)

        # Cycle tracking state
        self._active_minutes: float = 0.0
        self._coast_minutes: float = 0.0
        self._recent_values: deque[float] = deque(maxlen=5)
        self._stop_value: float | None = None
        self._peak_coast_value: float | None = None
        self._stop_slope: float = 0.0
        self._stop_charge: float = 0.0
        self._cycle_direction_heating: bool = False
        self._cycle_contaminated: bool = False

    def _reject_cycle(self, reason: str) -> None:
        self.rejected_counts[reason] = self.rejected_counts.get(reason, 0) + 1
        self.last_quality_reason = reason

    def mark_contaminated(self, reason: str = "ventilation_contamination") -> None:
        """Mark the currently running or coasting cycle as contaminated."""
        if self.phase in (CyclePhase.ACTIVE, CyclePhase.POST_STOP_COAST):
            self._cycle_contaminated = True
            self.last_quality_reason = reason

    def step_cycle(
        self,
        *,
        is_active: bool,
        u_effort: float,
        value: float,
        is_heating: bool,
        dt_min: float = 1.0,
        blocked_for_learning: bool = False,
        block_reason: str = "ventilation_contamination",
    ) -> CyclePhase:
        """Advance cycle state by dt_min (typically 1.0 min) and learn coasting upon completion."""
        dt = max(0.01, float(dt_min))
        u = max(0.0, min(1.0, float(u_effort)))

        # Always update 1st-order virtual emitter charge for high_inertia
        alpha_em = dt / (self.tau_em_min + dt)
        self.emitter_charge += alpha_em * (u - self.emitter_charge)

        if blocked_for_learning and self.phase in (CyclePhase.ACTIVE, CyclePhase.POST_STOP_COAST):
            self._cycle_contaminated = True
            self.last_quality_reason = block_reason

        if self.phase == CyclePhase.IDLE:
            self._recent_values.append(value)
            if is_active:
                self.phase = CyclePhase.ACTIVE
                self._active_minutes = dt
                self._cycle_direction_heating = is_heating
                self._cycle_contaminated = blocked_for_learning
                self._recent_values.clear()
                self._recent_values.append(value)

        elif self.phase == CyclePhase.ACTIVE:
            self._recent_values.append(value)
            if is_active:
                self._active_minutes += dt
            else:
                # Transition ACTIVE -> POST_STOP_COAST
                if self._cycle_contaminated:
                    self._reject_cycle(self.last_quality_reason or "ventilation_contamination")
                    self.phase = CyclePhase.PASSIVE_DRIFT
                elif self._active_minutes < self.min_run_min:
                    self._reject_cycle("short_run")
                    self.phase = CyclePhase.PASSIVE_DRIFT
                else:
                    # Compute 4-5 min smoothed slope
                    if len(self._recent_values) >= 2:
                        slope = abs(self._recent_values[-1] - self._recent_values[0]) / max(
                            1.0, float(len(self._recent_values) - 1) * dt
                        )
                    else:
                        slope = 0.0
                    self._stop_slope = slope
                    self._stop_charge = self.emitter_charge
                    self._stop_value = value
                    self._peak_coast_value = value
                    self._coast_minutes = 0.0
                    self.phase = CyclePhase.POST_STOP_COAST

        elif self.phase == CyclePhase.POST_STOP_COAST:
            if is_active:
                # Re-started before coasting finished
                self._finalize_coast(interrupted=True)
                self.phase = CyclePhase.ACTIVE
                self._active_minutes = dt
                self._cycle_direction_heating = is_heating
                self._cycle_contaminated = blocked_for_learning
                self._recent_values.clear()
                self._recent_values.append(value)
            elif self._cycle_contaminated:
                self._reject_cycle(self.last_quality_reason or "ventilation_contamination")
                self.phase = CyclePhase.PASSIVE_DRIFT
            else:
                self._coast_minutes += dt
                # Track peak excursion in the active direction
                if self._peak_coast_value is None:
                    self._peak_coast_value = value
                if self._cycle_direction_heating:
                    if value > self._peak_coast_value:
                        self._peak_coast_value = value
                        coast_reversed = False
                    else:
                        coast_reversed = (self._peak_coast_value - value) > 0.02
                else:
                    if value < self._peak_coast_value:
                        self._peak_coast_value = value
                        coast_reversed = False
                    else:
                        coast_reversed = (value - self._peak_coast_value) > 0.02

                if (coast_reversed and self._coast_minutes >= 2.0) or (
                    self._coast_minutes >= self.max_coast_window_min
                ):
                    self._finalize_coast(interrupted=False)
                    self.phase = CyclePhase.PASSIVE_DRIFT

        elif self.phase == CyclePhase.PASSIVE_DRIFT:
            self._recent_values.append(value)
            if is_active:
                self.phase = CyclePhase.ACTIVE
                self._active_minutes = dt
                self._cycle_direction_heating = is_heating
                self._cycle_contaminated = blocked_for_learning
                self._recent_values.clear()
                self._recent_values.append(value)
            elif self.emitter_charge < 0.05:
                self.phase = CyclePhase.IDLE

        return self.phase

    def _finalize_coast(self, interrupted: bool = False) -> None:
        if self._stop_value is None or self._peak_coast_value is None:
            self._reject_cycle("invalid_duration")
            return
        if self._cycle_contaminated:
            self._reject_cycle(self.last_quality_reason or "ventilation_contamination")
            return
        if self._coast_minutes < 1.0:
            self._reject_cycle("invalid_duration")
            return

        obs_coast = abs(self._peak_coast_value - self._stop_value)

        if self.inertia_profile == INERTIA_HIGH_INERTIA:
            if self._stop_charge < self.min_charge:
                self._reject_cycle("low_slope_or_charge")
                return
            sample_k = max(0.0, min(10.0, obs_coast / self._stop_charge))
            self.k_afterglow = (1.0 - self.ewma_alpha) * self.k_afterglow + self.ewma_alpha * sample_k
        else:
            if self._stop_slope < self.min_slope:
                self._reject_cycle("low_slope_or_charge")
                return
            sample_tau = max(0.0, min(30.0, obs_coast / self._stop_slope))
            self.tau_coast = (1.0 - self.ewma_alpha) * self.tau_coast + self.ewma_alpha * sample_tau

        self.accepted_count += 1
        self.last_quality_reason = "accepted"
        self.history.append(
            {
                "obs_coast": round(obs_coast, 4),
                "stop_slope": round(self._stop_slope, 4),
                "stop_charge": round(self._stop_charge, 4),
                "tau_coast": round(self.tau_coast, 4),
                "k_afterglow": round(self.k_afterglow, 4),
                "interrupted": interrupted,
            }
        )

    def predict_coast(self, abs_slope: float | None = None) -> float:
        """Predict coasting excursion after stopping."""
        if self.inertia_profile == INERTIA_HIGH_INERTIA:
            return min(self.max_pred, max(0.0, self.k_afterglow * self.emitter_charge))
        if abs_slope is None:
            if len(self._recent_values) >= 2:
                abs_slope = abs(self._recent_values[-1] - self._recent_values[0]) / max(
                    1.0, float(len(self._recent_values) - 1)
                )
            else:
                abs_slope = self._stop_slope
        return min(self.max_pred, max(0.0, self.tau_coast * max(0.0, float(abs_slope))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "inertia_profile": self.inertia_profile,
            "phase": self.phase.value,
            "tau_coast": self.tau_coast,
            "k_afterglow": self.k_afterglow,
            "emitter_charge": self.emitter_charge,
            "accepted_count": self.accepted_count,
            "rejected_counts": dict(self.rejected_counts),
            "last_quality_reason": self.last_quality_reason,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DynamicCoastingLearner:
        obj = cls(inertia_profile=str(data.get("inertia_profile", INERTIA_FAST_AIR)))
        try:
            obj.phase = CyclePhase(data.get("phase", CyclePhase.IDLE.value))
        except ValueError:
            obj.phase = CyclePhase.IDLE
        obj.tau_coast = float(data.get("tau_coast", obj.tau_coast))
        obj.k_afterglow = float(data.get("k_afterglow", obj.k_afterglow))
        obj.emitter_charge = float(data.get("emitter_charge", 0.0))
        obj.accepted_count = int(data.get("accepted_count", 0))
        obj.rejected_counts = {
            str(k): int(v) for k, v in (data.get("rejected_counts") or {}).items()
        }
        obj.last_quality_reason = data.get("last_quality_reason")
        obj.history = deque(data.get("history") or [], maxlen=50)
        return obj


@dataclass
class TuningParameters:
    kp: float
    ki: float
    ke: float
    ti_min: float = 0.0
    learned_rate: float = 0.0
    learned_loss: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp": self.kp,
            "ki": self.ki,
            "ke": self.ke,
            "ti_min": self.ti_min,
            "learned_rate": self.learned_rate,
            "learned_loss": self.learned_loss,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TuningParameters:
        return cls(
            kp=float(data.get("kp", 20.0)),
            ki=float(data.get("ki", 0.5)),
            ke=float(data.get("ke", 0.6)),
            ti_min=float(data.get("ti_min", 0.0)),
            learned_rate=float(data.get("learned_rate", 0.0)),
            learned_loss=float(data.get("learned_loss", 0.0)),
        )


class SimcAutoTuner:
    """Derives FOPDT (K, tau, theta) from HuberRLS and computes Skogestad SIMC PI + Ke."""

    def compute(
        self,
        rls: HuberRLS,
        prediction_mode: str = "balanced",
        dead_time_min: float = 2.0,
        baseline: TuningParameters | None = None,
    ) -> TuningParameters:
        """Compute SIMC Kp, Ti, Ki (per-minute), and Ke from RLS parameters."""
        rate = max(1e-4, float(rls.theta[0]))  # °C/min or g/m³/min at u=1 (100% output)
        loss = max(1e-4, float(rls.theta[1])) if len(rls.theta) > 1 else 0.005  # 1/min heat loss coeff

        # FOPDT mapping: dT/dt = rate * u - loss * (T_in - T_out)
        # tau = 1 / loss, K = (rate / loss) / 100.0 (gain per 1% PID output)
        tau = max(5.0, min(600.0, 1.0 / loss))
        gain_k = max(0.01, (rate / loss) / 100.0)
        theta = max(0.5, float(dead_time_min))

        mode = prediction_mode if prediction_mode in PREDICTION_MODES else "balanced"
        tau_c = _TAU_C_FACTORS[mode] * theta

        kp = tau / (gain_k * (tau_c + theta))
        ti_min = min(tau, 4.0 * (tau_c + theta))
        ki = kp / max(1.0, ti_min)
        # Outdoor feedforward Ke (% output per °C outdoor delta): loss / (rate / 100)
        ke = (loss / rate) * 100.0

        # Apply ±50% drift clamping around physical baseline if provided
        if baseline is not None:
            kp = max(baseline.kp * 0.5, min(baseline.kp * 1.5, kp))
            ki = max(baseline.ki * 0.5, min(baseline.ki * 1.5, ki))
            ke = max(baseline.ke * 0.5, min(baseline.ke * 1.5, ke))

        return TuningParameters(
            kp=round(kp, 4),
            ki=round(ki, 4),
            ke=round(ke, 4),
            ti_min=round(ti_min, 2),
            learned_rate=round(rate, 5),
            learned_loss=round(loss, 5),
        )


@dataclass
class TuningSnapshot:
    params: TuningParameters
    baseline_mae: float
    baseline_overshoot: float
    cycles_evaluated: int = 0
    mae_accum: float = 0.0
    max_overshoot: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": self.params.to_dict(),
            "baseline_mae": self.baseline_mae,
            "baseline_overshoot": self.baseline_overshoot,
            "cycles_evaluated": self.cycles_evaluated,
            "mae_accum": self.mae_accum,
            "max_overshoot": self.max_overshoot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TuningSnapshot:
        return cls(
            params=TuningParameters.from_dict(data["params"]),
            baseline_mae=float(data.get("baseline_mae", 0.5)),
            baseline_overshoot=float(data.get("baseline_overshoot", 0.5)),
            cycles_evaluated=int(data.get("cycles_evaluated", 0)),
            mae_accum=float(data.get("mae_accum", 0.0)),
            max_overshoot=float(data.get("max_overshoot", 0.0)),
        )


class TuningSafetyManager:
    """Manages confidence stages, auto-apply limits, and 5-cycle 30% degradation rollback."""

    def __init__(
        self,
        min_valid_cycles: int = 5,
        season_limit: int = 5,
        lifetime_limit: int = 20,
        eval_cycles: int = 5,
        degradation_threshold: float = 0.30,
    ) -> None:
        self.min_valid_cycles = int(min_valid_cycles)
        self.season_limit = int(season_limit)
        self.lifetime_limit = int(lifetime_limit)
        self.eval_cycles = int(eval_cycles)
        self.degradation_threshold = float(degradation_threshold)

        self.season_applies: int = 0
        self.lifetime_applies: int = 0
        self.rollback_count: int = 0
        self.active_snapshot: TuningSnapshot | None = None
        self.last_fallback_reason: str | None = None

    def get_confidence_stage(
        self, rls_samples: int, valid_cycles: int
    ) -> tuple[ConfidenceStage, float]:
        """Compute confidence stage and percentage (0~100%)."""
        if valid_cycles < self.min_valid_cycles or rls_samples < 20:
            pct = min(49.0, (valid_cycles / max(1, self.min_valid_cycles)) * 25.0 + min(24.0, rls_samples))
            return ConfidenceStage.COLLECTING, round(pct, 1)
        if valid_cycles < self.min_valid_cycles * 2:
            pct = 50.0 + ((valid_cycles - self.min_valid_cycles) / self.min_valid_cycles) * 20.0
            return ConfidenceStage.STABLE, round(pct, 1)
        if valid_cycles < self.min_valid_cycles * 4:
            pct = 70.0 + ((valid_cycles - self.min_valid_cycles * 2) / (self.min_valid_cycles * 2)) * 20.0
            return ConfidenceStage.TUNED, round(pct, 1)
        return ConfidenceStage.OPTIMIZED, min(100.0, round(90.0 + min(10.0, valid_cycles - 20), 1))

    def can_auto_apply(self, rls_samples: int, valid_cycles: int) -> tuple[bool, str | None]:
        stage, _ = self.get_confidence_stage(rls_samples, valid_cycles)
        if stage in (ConfidenceStage.COLLECTING, ConfidenceStage.STABLE) and valid_cycles < self.min_valid_cycles:
            return False, "insufficient_valid_cycles"
        if stage == ConfidenceStage.COLLECTING:
            return False, "learning_collecting"
        if self.season_applies >= self.season_limit:
            return False, "season_apply_limit_reached"
        if self.lifetime_applies >= self.lifetime_limit:
            return False, "lifetime_apply_limit_reached"
        return True, None

    def begin_application(
        self,
        prev_params: TuningParameters,
        baseline_mae: float,
        baseline_overshoot: float,
    ) -> None:
        """Save snapshot before applying new SIMC parameters."""
        self.active_snapshot = TuningSnapshot(
            params=prev_params,
            baseline_mae=max(0.05, float(baseline_mae)),
            baseline_overshoot=max(0.05, float(baseline_overshoot)),
        )
        self.season_applies += 1
        self.lifetime_applies += 1

    def record_post_apply_cycle(
        self, cycle_mae: float, cycle_overshoot: float
    ) -> TuningParameters | None:
        """Record one cycle after auto-apply; returns rollback TuningParameters if degraded > 30%."""
        if self.active_snapshot is None:
            return None

        snap = self.active_snapshot
        snap.cycles_evaluated += 1
        snap.mae_accum += float(cycle_mae)
        snap.max_overshoot = max(snap.max_overshoot, float(cycle_overshoot))

        if snap.cycles_evaluated < self.eval_cycles:
            return None

        avg_mae = snap.mae_accum / snap.cycles_evaluated
        mae_degraded = avg_mae > snap.baseline_mae * (1.0 + self.degradation_threshold)
        overshoot_degraded = snap.max_overshoot > snap.baseline_overshoot * (
            1.0 + self.degradation_threshold
        )

        self.active_snapshot = None
        if mae_degraded or overshoot_degraded:
            self.rollback_count += 1
            self.last_fallback_reason = "auto_rollback_performance_degraded"
            return snap.params

        self.last_fallback_reason = None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "season_applies": self.season_applies,
            "lifetime_applies": self.lifetime_applies,
            "rollback_count": self.rollback_count,
            "last_fallback_reason": self.last_fallback_reason,
            "active_snapshot": (
                self.active_snapshot.to_dict() if self.active_snapshot is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TuningSafetyManager:
        obj = cls()
        obj.season_applies = int(data.get("season_applies", 0))
        obj.lifetime_applies = int(data.get("lifetime_applies", 0))
        obj.rollback_count = int(data.get("rollback_count", 0))
        obj.last_fallback_reason = data.get("last_fallback_reason")
        snap_raw = data.get("active_snapshot")
        if isinstance(snap_raw, dict):
            obj.active_snapshot = TuningSnapshot.from_dict(snap_raw)
        return obj
