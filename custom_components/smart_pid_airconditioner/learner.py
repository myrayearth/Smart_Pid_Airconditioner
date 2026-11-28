"""Pure-Python self-learning engine: Huber-RLS, Dynamic Coasting, SIMC Auto-Tuner & Safety Manager."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import logging
import math
from statistics import median
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

INERTIA_FAST_AIR = "fast_air"
INERTIA_HIGH_INERTIA = "high_inertia"
INERTIA_CUSTOM = "custom"
INERTIA_PROFILES = (INERTIA_FAST_AIR, INERTIA_HIGH_INERTIA, INERTIA_CUSTOM)

PREDICTION_MODES = ("eco", "balanced", "comfort")
_TAU_C_FACTORS = {
    "eco": 2.0,
    "balanced": 1.5,
    "comfort": 1.0,
}

PEAK_CONFIRM_EPSILON = 1e-5
LOW_SLOPE_DELAYED_THRESHOLD = 0.002
COAST_CONFIDENCE_ABSTAIN_THRESHOLD = 0.25
OUTDOOR_BAND_WIDTH_C = 5.0
OUTDOOR_BAND_MIN_SAMPLES = 3
OUTDOOR_COAST_CORRECTION_CLAMP = 0.3

# Stage 9: Dual-layer memory (Current short-term / Long-term promotion & freshness decay)
MEMORY_CURRENT_ALPHA = 0.2
MEMORY_LONG_TERM_ALPHA = 0.02
MEMORY_CURRENT_MAX_AGE_SEC = 30 * 86400.0
MEMORY_MIN_PROMOTION_SAMPLES = 5
MEMORY_PROMOTION_CONFIDENCE = 0.7
MEMORY_MIN_PREDICTION_CONFIDENCE = 0.25

# Stage 10: Signed coast bias correction & early-stop shortfall defense
COAST_BIAS_ALPHA = 0.2
COAST_BIAS_CLAMP = 0.3
SHORTFALL_TOLERANCE = 0.15
SHORTFALL_DECAY_FACTOR = 0.85
SHORTFALL_RECOVERY_STEP = 0.05
SHORTFALL_MIN_SCALE = 0.5


@dataclass
class StatBucket:
    """Single-layer EWMA mean/variance bucket with freshness decay and promotion."""

    mean: float
    variance: float | None = None
    samples: int = 0
    updated_at: float = 0.0
    promotions: int = 0

    def update(self, value: float, now_ts: float) -> None:
        """Update short-term bucket with dormancy reset after 30 days."""
        val = float(value)
        ts = float(now_ts)
        if not math.isfinite(val) or not math.isfinite(ts):
            return

        if self.samples > 0 and (ts - self.updated_at) >= MEMORY_CURRENT_MAX_AGE_SEC:
            self.mean = val
            self.variance = 0.0
            self.samples = 1
            self.updated_at = ts
            return

        alpha = MEMORY_CURRENT_ALPHA
        if self.samples == 0:
            self.mean = (1.0 - alpha) * self.mean + alpha * val
            self.variance = 0.0
            self.samples = 1
        else:
            delta = val - self.mean
            self.mean += alpha * delta
            self.variance = (1.0 - alpha) * ((self.variance or 0.0) + alpha * delta * delta)
            self.samples += 1
        self.updated_at = ts

    def confidence(self, now_ts: float, *, long_term: bool = False, scale: float = 0.3) -> float:
        """Compute bucket confidence in [0.0, 1.0] with 30-day linear freshness decay for current."""
        if self.variance is None or self.samples < 3:
            return 0.0
        freshness = (
            1.0
            if long_term
            else max(
                0.0,
                1.0 - max(0.0, float(now_ts) - self.updated_at) / MEMORY_CURRENT_MAX_AGE_SEC,
            )
        )
        var = max(0.0, float(self.variance))
        sc = max(1e-6, float(scale))
        return min(self.samples / 8.0, 1.0) / (1.0 + math.sqrt(var) / sc) * freshness

    def stable(self, now_ts: float, *, scale: float = 0.3) -> bool:
        """Return True when bucket has enough low-variance samples to promote to long-term."""
        return (
            self.samples >= MEMORY_MIN_PROMOTION_SAMPLES
            and self.confidence(now_ts, scale=scale) >= MEMORY_PROMOTION_CONFIDENCE
        )

    def promote(self, current: StatBucket, now_ts: float) -> None:
        """Blend a stable current bucket into this long-term bucket."""
        if self.promotions == 0:
            self.mean = current.mean
            self.variance = current.variance
        else:
            alpha = MEMORY_LONG_TERM_ALPHA
            self.mean = (1.0 - alpha) * self.mean + alpha * current.mean
            if current.variance is not None:
                prev_v = 0.0 if self.variance is None else self.variance
                self.variance = (1.0 - alpha) * prev_v + alpha * current.variance
        self.samples = max(self.samples, current.samples)
        self.updated_at = float(now_ts)
        self.promotions += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": round(float(self.mean), 6),
            "variance": round(float(self.variance), 6) if self.variance is not None else None,
            "samples": int(self.samples),
            "updated_at": float(self.updated_at),
            "promotions": int(self.promotions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StatBucket:
        if not isinstance(data, dict):
            raise ValueError("StatBucket payload must be a dict")
        mean = float(data["mean"])
        if not math.isfinite(mean):
            raise ValueError("StatBucket mean is not finite")
        raw_var = data.get("variance")
        if raw_var is None:
            variance: float | None = None
        else:
            variance = float(raw_var)
            if not math.isfinite(variance) or variance < 0.0:
                raise ValueError("StatBucket variance must be finite and >= 0")
        samples = int(data.get("samples", 0))
        if samples < 0:
            raise ValueError("StatBucket samples must be >= 0")
        updated_at = float(data.get("updated_at", 0.0))
        if not math.isfinite(updated_at):
            raise ValueError("StatBucket updated_at is not finite")
        promotions = int(data.get("promotions", 0))
        if promotions < 0:
            raise ValueError("StatBucket promotions must be >= 0")
        return cls(
            mean=mean,
            variance=variance,
            samples=samples,
            updated_at=updated_at,
            promotions=promotions,
        )


class DualLayerMemory:
    """Two-tier memory blending fast-adapting current stats with promoted long-term baseline."""

    def __init__(
        self,
        *,
        scale: float = 0.3,
        current_weight_limit: float = 1.0,
        current: StatBucket | None = None,
        long_term: StatBucket | None = None,
    ) -> None:
        self.scale = max(1e-6, float(scale))
        self.current_weight_limit = max(0.0, min(1.0, float(current_weight_limit)))
        self.current: StatBucket | None = current
        self.long_term: StatBucket | None = long_term

    def learn(self, value: float, now_ts: float) -> bool:
        """Update current bucket and promote into long-term when stable. Returns True if promoted."""
        val = float(value)
        ts = float(now_ts)
        if not math.isfinite(val) or not math.isfinite(ts):
            return False

        if self.current is None:
            self.current = StatBucket(mean=val, variance=0.0, samples=1, updated_at=ts)
        else:
            self.current.update(val, ts)

        if self.current.stable(ts, scale=self.scale):
            if self.long_term is None:
                self.long_term = StatBucket(
                    mean=self.current.mean,
                    variance=self.current.variance,
                    samples=0,
                    updated_at=ts,
                    promotions=0,
                )
            self.long_term.promote(self.current, ts)
            return True
        return False

    def estimate(self, now_ts: float) -> tuple[float, float, str, float] | None:
        """Return (mean, confidence, source, current_weight) or None if neither layer is ready."""
        ts = float(now_ts)
        c_conf = (
            self.current.confidence(ts, long_term=False, scale=self.scale)
            if self.current is not None
            else 0.0
        )
        l_conf = (
            self.long_term.confidence(ts, long_term=True, scale=self.scale)
            if self.long_term is not None
            else 0.0
        )
        c_ok = self.current is not None and c_conf >= MEMORY_MIN_PREDICTION_CONFIDENCE
        l_ok = self.long_term is not None and l_conf >= MEMORY_MIN_PREDICTION_CONFIDENCE

        if c_ok and l_ok:
            assert self.current is not None and self.long_term is not None
            weight = min(c_conf, self.current_weight_limit)
            mean = weight * self.current.mean + (1.0 - weight) * self.long_term.mean
            conf = weight * c_conf + (1.0 - weight) * l_conf
            return mean, conf, "blended", round(weight, 4)
        if c_ok:
            assert self.current is not None
            return self.current.mean, c_conf, "current", 1.0
        if l_ok:
            assert self.long_term is not None
            return self.long_term.mean, l_conf, "long_term", 0.0
        return None

    def summary(self, now_ts: float) -> dict[str, Any]:
        """Return compact diagnostics summary of both layers."""
        ts = float(now_ts)
        def _layer_info(bucket: StatBucket | None, is_lt: bool) -> dict[str, Any] | None:
            if bucket is None:
                return None
            return {
                "mean": round(bucket.mean, 4),
                "samples": bucket.samples,
                "promotions": bucket.promotions,
                "confidence": round(bucket.confidence(ts, long_term=is_lt, scale=self.scale), 4),
            }
        return {
            "current": _layer_info(self.current, False),
            "long_term": _layer_info(self.long_term, True),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "scale": self.scale,
            "current_weight_limit": self.current_weight_limit,
            "current": self.current.to_dict() if self.current is not None else None,
            "long_term": self.long_term.to_dict() if self.long_term is not None else None,
        }

    @classmethod
    def from_dict(
        cls,
        data: Any,
        *,
        scale: float = 0.3,
        current_weight_limit: float = 1.0,
    ) -> DualLayerMemory:
        mem = cls(scale=scale, current_weight_limit=current_weight_limit)
        if not isinstance(data, dict):
            _LOGGER.warning("Discarding corrupted DualLayerMemory payload (expected dict)")
            return mem
        raw_cur = data.get("current")
        if raw_cur is not None:
            try:
                mem.current = StatBucket.from_dict(raw_cur)
            except (ValueError, TypeError, KeyError) as err:
                _LOGGER.warning("Discarding corrupted current StatBucket: %s", err)
        raw_lt = data.get("long_term")
        if raw_lt is not None:
            try:
                mem.long_term = StatBucket.from_dict(raw_lt)
            except (ValueError, TypeError, KeyError) as err:
                _LOGGER.warning("Discarding corrupted long_term StatBucket: %s", err)
        return mem



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
    def from_dict(cls, data: Mapping[str, Any]) -> HuberRLS:
        if not isinstance(data, Mapping):
            raise ValueError("HuberRLS snapshot must be a mapping")

        def _fin(val: Any, name: str) -> float:
            if isinstance(val, bool):
                raise ValueError(f"Invalid boolean for {name}")
            try:
                fval = float(val)
            except (ValueError, TypeError) as err:
                raise ValueError(f"Invalid float for {name}: {val!r}") from err
            if not math.isfinite(fval):
                raise ValueError(f"Non-finite value for {name}: {val!r}")
            return fval

        def _int(val: Any, name: str) -> int:
            if isinstance(val, bool):
                raise ValueError(f"Invalid boolean for {name}")
            try:
                return int(val)
            except (ValueError, TypeError) as err:
                raise ValueError(f"Invalid integer for {name}: {val!r}") from err

        n_features = _int(data.get("n_features", 2), "n_features")
        if n_features < 1:
            raise ValueError(f"Invalid n_features={n_features}")

        forgetting = _fin(data.get("forgetting", 0.992), "forgetting")
        if not (0.0 < forgetting <= 1.0):
            raise ValueError(f"Invalid forgetting={forgetting}")

        p_min = _fin(data.get("p_min", 1e-6), "p_min")
        p_max = _fin(data.get("p_max", 50.0), "p_max")
        if p_min <= 0.0 or p_max < p_min:
            raise ValueError(f"Invalid covariance bounds p_min={p_min}, p_max={p_max}")

        warmup_samples = _int(data.get("warmup_samples", 20), "warmup_samples")
        if warmup_samples < 0:
            raise ValueError(f"Invalid warmup_samples={warmup_samples}")

        huber_delta = _fin(data.get("huber_delta", 0.04), "huber_delta")
        hard_reject = _fin(data.get("hard_reject", 0.12), "hard_reject")
        if huber_delta <= 0.0 or hard_reject <= 0.0:
            raise ValueError(f"Invalid huber thresholds ({huber_delta}, {hard_reject})")

        parsed_theta: list[float] | None = None
        if "theta" in data:
            raw_theta = data["theta"]
            if not isinstance(raw_theta, list) or len(raw_theta) != n_features:
                raise ValueError("Invalid RLS theta length")
            parsed_theta = [_fin(v, "theta") for v in raw_theta]

        parsed_bounds: list[tuple[float, float]] | None = None
        if "bounds" in data:
            raw_bounds = data["bounds"]
            if not isinstance(raw_bounds, list) or len(raw_bounds) != n_features:
                raise ValueError("Invalid RLS bounds length")
            parsed_bounds = []
            for b in raw_bounds:
                if not isinstance(b, (list, tuple)) or len(b) != 2:
                    raise ValueError("Invalid RLS bound entry")
                lo = _fin(b[0], "bounds.lo")
                hi = _fin(b[1], "bounds.hi")
                if lo > hi:
                    raise ValueError("Invalid RLS bound ordering")
                parsed_bounds.append((lo, hi))

        obj = cls(
            n_features=n_features,
            initial_theta=parsed_theta,
            bounds=parsed_bounds,
            forgetting=forgetting,
            p_min=p_min,
            p_max=p_max,
            warmup_samples=warmup_samples,
            huber_delta=huber_delta,
            hard_reject=hard_reject,
        )

        if "P" in data:
            raw_p = data["P"]
            if not isinstance(raw_p, list) or len(raw_p) != n_features:
                raise ValueError("Invalid RLS covariance matrix P dimensions")
            parsed_p: list[list[float]] = []
            for row in raw_p:
                if not isinstance(row, list) or len(row) != n_features:
                    raise ValueError("Invalid RLS covariance matrix P row dimensions")
                parsed_p.append([_fin(v, "P") for v in row])
            obj.P = parsed_p

        sample_count = _int(data.get("sample_count", 0), "sample_count")
        rejected_outliers = _int(data.get("rejected_outliers", 0), "rejected_outliers")
        if sample_count < 0 or rejected_outliers < 0:
            raise ValueError("Negative RLS sample/outlier count")
        obj.sample_count = sample_count
        obj.rejected_outliers = rejected_outliers
        return obj


class DynamicCoastingLearner:
    """Tracks 4-phase cycles and learns coasting (afterglow) for fast_air, high_inertia & custom profiles."""

    @staticmethod
    def _defaults_for_profile(inertia_profile: str) -> tuple[float, float]:
        if inertia_profile == INERTIA_HIGH_INERTIA:
            return 35.0, 180.0
        if inertia_profile == INERTIA_CUSTOM:
            return 25.0, 90.0
        return 10.0, 45.0

    def __init__(
        self,
        inertia_profile: str = INERTIA_FAST_AIR,
        tau_em_min: float | None = None,
        ewma_alpha: float = 0.25,
        min_run_min: float = 5.0,
        max_coast_window_min: float | None = None,
        max_pred: float = 3.0,
        min_slope: float = 0.005,
        min_charge: float = 0.05,
        peak_confirm_delta: float = 0.1,
    ) -> None:
        self.inertia_profile = (
            inertia_profile if inertia_profile in INERTIA_PROFILES else INERTIA_FAST_AIR
        )
        def_tau_em, def_coast_win = self._defaults_for_profile(self.inertia_profile)
        self.tau_em_min = max(1.0, float(def_tau_em if tau_em_min is None else tau_em_min))
        self.ewma_alpha = float(ewma_alpha)
        self.min_run_min = float(min_run_min)
        self.max_coast_window_min = max(
            5.0, float(def_coast_win if max_coast_window_min is None else max_coast_window_min)
        )
        self.max_pred = float(max_pred)
        self.min_slope = float(min_slope)
        self.min_charge = float(min_charge)
        self.peak_confirm_delta = max(0.01, float(peak_confirm_delta))
        self.response_threshold: float = 0.1

        self.phase: CyclePhase = CyclePhase.IDLE
        self.tau_coast: float = 3.0 if self.inertia_profile == INERTIA_FAST_AIR else 1.5
        self.k_afterglow: float = 1.0
        self.emitter_charge: float = 0.0

        # Stage 8: Measured response delay (Dead Time θ) & passive drift learning
        self.response_delay_min: float | None = None
        self.response_delay_samples: int = 0
        self.passive_drift_rate: float = 0.0
        self.passive_drift_samples: int = 0
        self.passive_drift_by_band: dict[str, dict[str, float]] = {}

        # Stage 9: Dual-layer memory (current / long_term)
        self.k_afterglow_memory = DualLayerMemory(scale=0.35)
        self.tau_coast_memory = DualLayerMemory(scale=1.5)
        self.response_delay_memory = DualLayerMemory(scale=5.0)
        self.outdoor_band_memories: dict[str, DualLayerMemory] = {}
        self.last_memory_source: str = "legacy"
        self.last_memory_current_weight: float = 0.0

        # Stage 10: Signed coast bias & early-stop shortfall defense
        self.coast_bias: float = 0.0
        self.coast_bias_samples: int = 0
        self.shortfall_margin_scale: float = 1.0
        self.consecutive_shortfalls: int = 0

        self.accepted_count: int = 0
        self.rejected_counts: dict[str, int] = {}
        self.last_quality_reason: str | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=50)
        self._quarantined_outliers: deque[dict[str, float]] = deque(maxlen=10)
        self._last_completed_cycle: dict[str, Any] | None = None

        # Cycle tracking state
        self._active_minutes: float = 0.0
        self._coast_minutes: float = 0.0
        self._recent_values: deque[float] = deque(maxlen=5)
        self._active_start_value: float | None = None
        self._response_detected: bool = False
        self._passive_start_value: float | None = None
        self._passive_elapsed_min: float = 0.0
        self._passive_outdoor_sum: float = 0.0
        self._passive_outdoor_count: int = 0
        self._stop_value: float | None = None
        self._peak_coast_value: float | None = None
        self._stop_slope: float = 0.0
        self._stop_charge: float = 0.0
        self._predicted_coast_at_stop: float = 0.0
        self._raw_predicted_coast_at_stop: float = 0.0
        self._stopped_by_early_stop: bool = False
        self._stop_target: float | None = None
        self._stop_outdoor: float | None = None
        self._reversal_step: int = 0
        self._reversal_anchor: float | None = None
        self._cycle_direction_heating: bool = False
        self._cycle_contaminated: bool = False

    def apply_profile_settings(
        self,
        inertia_profile: str,
        *,
        tau_em_min: float | None = None,
        max_coast_window_min: float | None = None,
        peak_confirm_delta: float | None = None,
    ) -> None:
        """Update inertia profile and timing parameters without discarding learned history."""
        self.inertia_profile = (
            inertia_profile if inertia_profile in INERTIA_PROFILES else INERTIA_FAST_AIR
        )
        def_tau_em, def_coast_win = self._defaults_for_profile(self.inertia_profile)
        self.tau_em_min = max(1.0, float(def_tau_em if tau_em_min is None else tau_em_min))
        self.max_coast_window_min = max(
            5.0, float(def_coast_win if max_coast_window_min is None else max_coast_window_min)
        )
        if peak_confirm_delta is not None:
            self.peak_confirm_delta = max(0.01, float(peak_confirm_delta))

    def consume_completed_cycle(self) -> dict[str, Any] | None:
        """Return and clear the latest completed cycle comparison metrics."""
        res = self._last_completed_cycle
        self._last_completed_cycle = None
        return res

    def _reset_passive_drift_window(self, start_value: float | None = None) -> None:
        self._passive_start_value = start_value
        self._passive_elapsed_min = 0.0
        self._passive_outdoor_sum = 0.0
        self._passive_outdoor_count = 0
        self._passive_sample_recorded = False

    def _start_active_cycle(
        self, value: float, dt: float, is_heating: bool, blocked_for_learning: bool
    ) -> None:
        self.phase = CyclePhase.ACTIVE
        self._active_minutes = dt
        self._cycle_direction_heating = is_heating
        self._cycle_contaminated = blocked_for_learning
        self._active_start_value = value
        self._response_detected = False
        self._reset_passive_drift_window(None)
        self._recent_values.clear()
        self._recent_values.append(value)

    def _enter_passive_drift(
        self, value: float, blocked_for_learning: bool, solar_rising_active: bool
    ) -> None:
        self.phase = CyclePhase.PASSIVE_DRIFT
        if blocked_for_learning or solar_rising_active:
            self._reset_passive_drift_window(None)
        else:
            self._reset_passive_drift_window(value)

    def _record_passive_drift_sample(
        self, value: float, outdoor_temp: float | None
    ) -> bool:
        """Evaluate accumulated PASSIVE_DRIFT window and update drift EWMA if valid."""
        if self._passive_start_value is None or self._passive_elapsed_min < 15.0:
            return False
        drift_delta = (
            (self._passive_start_value - value)
            if self._cycle_direction_heating
            else (value - self._passive_start_value)
        )
        if drift_delta + PEAK_CONFIRM_EPSILON < 0.15:
            return False

        rate = min(0.2, max(0.0, abs(drift_delta) / self._passive_elapsed_min))
        if self.passive_drift_samples == 0:
            self.passive_drift_rate = rate
        else:
            self.passive_drift_rate = (
                (1.0 - self.ewma_alpha) * self.passive_drift_rate + self.ewma_alpha * rate
            )
        self.passive_drift_samples += 1

        avg_out: float | None = None
        if self._passive_outdoor_count > 0:
            avg_out = self._passive_outdoor_sum / self._passive_outdoor_count
        elif outdoor_temp is not None:
            avg_out = float(outdoor_temp)

        if avg_out is not None:
            band_key = str(
                math.floor(avg_out / OUTDOOR_BAND_WIDTH_C) * int(OUTDOOR_BAND_WIDTH_C)
            )
            entry = self.passive_drift_by_band.get(band_key)
            if not isinstance(entry, dict) or int(entry.get("samples", 0)) == 0:
                self.passive_drift_by_band[band_key] = {"rate": rate, "samples": 1}
            else:
                prev_rate = float(entry.get("rate", rate))
                prev_n = int(entry.get("samples", 0))
                new_rate = (1.0 - self.ewma_alpha) * prev_rate + self.ewma_alpha * rate
                self.passive_drift_by_band[band_key] = {
                    "rate": new_rate,
                    "samples": prev_n + 1,
                }

        self._reset_passive_drift_window(value)
        return True

    def get_effective_dead_time(
        self, default: float = 2.0, now_ts: float | None = None
    ) -> float:
        """Return learned response delay (dead time θ) in minutes using dual-layer memory or legacy."""
        ts = time.time() if now_ts is None else float(now_ts)
        est = self.response_delay_memory.estimate(ts)
        if est is not None:
            return max(0.5, min(60.0, float(est[0])))
        if self.response_delay_samples >= 3 and self.response_delay_min is not None:
            return max(0.5, min(60.0, float(self.response_delay_min)))
        return float(default)

    def predict_preheat_drop(
        self, outdoor_temp: float | None = None, now_ts: float | None = None
    ) -> float:
        """Predict temperature drop during dead time using outdoor-band or overall passive drift."""
        ts = time.time() if now_ts is None else float(now_ts)
        has_delay = (
            self.response_delay_samples >= 3
            or self.response_delay_memory.estimate(ts) is not None
        )
        if not has_delay:
            return 0.0
        rate = 0.0
        if outdoor_temp is not None:
            band_key = str(
                math.floor(float(outdoor_temp) / OUTDOOR_BAND_WIDTH_C)
                * int(OUTDOOR_BAND_WIDTH_C)
            )
            band_entry = self.passive_drift_by_band.get(band_key)
            if (
                isinstance(band_entry, dict)
                and int(band_entry.get("samples", 0)) >= OUTDOOR_BAND_MIN_SAMPLES
            ):
                rate = float(band_entry.get("rate", 0.0))
        if rate <= 0.0 and self.passive_drift_samples >= 3:
            rate = float(self.passive_drift_rate)
        if rate <= 0.0:
            return 0.0
        return round(
            min(1.5, max(0.0, rate * self.get_effective_dead_time(now_ts=ts))), 4
        )

    def abort_cycle_to_idle(self) -> None:
        """Abort any in-progress cycle cleanly to IDLE while keeping learned history/factors."""
        self.phase = CyclePhase.IDLE
        self.emitter_charge = 0.0
        self._active_minutes = 0.0
        self._coast_minutes = 0.0
        self._recent_values.clear()
        self._active_start_value = None
        self._response_detected = False
        self._reset_passive_drift_window(None)
        self._stop_value = None
        self._peak_coast_value = None
        self._stop_slope = 0.0
        self._stop_charge = 0.0
        self._predicted_coast_at_stop = 0.0
        self._raw_predicted_coast_at_stop = 0.0
        self._stopped_by_early_stop = False
        self._stop_target = None
        self._stop_outdoor = None
        self._reversal_step = 0
        self._reversal_anchor = None
        self._cycle_contaminated = False
        self._last_completed_cycle = None

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
        target_val: float | None = None,
        solar_rising_active: bool = False,
        outdoor_temp: float | None = None,
        response_threshold: float | None = None,
        now_ts: float | None = None,
        early_stop_active: bool = False,
    ) -> CyclePhase:
        """Advance cycle state by dt_min (typically 1.0 min) and learn coasting upon completion."""
        ts = time.time() if now_ts is None else float(now_ts)
        dt = max(0.01, float(dt_min))
        u = max(0.0, min(1.0, float(u_effort)))

        # Always update 1st-order virtual emitter charge for high_inertia / custom
        alpha_em = dt / (self.tau_em_min + dt)
        self.emitter_charge += alpha_em * (u - self.emitter_charge)

        if blocked_for_learning and self.phase in (CyclePhase.ACTIVE, CyclePhase.POST_STOP_COAST):
            self._cycle_contaminated = True
            self.last_quality_reason = block_reason

        if self.phase == CyclePhase.IDLE:
            self._recent_values.append(value)
            if is_active:
                self._start_active_cycle(value, dt, is_heating, blocked_for_learning)

        elif self.phase == CyclePhase.ACTIVE:
            self._recent_values.append(value)
            if is_active:
                self._active_minutes += dt
                if (
                    not self._response_detected
                    and not self._cycle_contaminated
                    and self._active_start_value is not None
                ):
                    progress = (
                        (value - self._active_start_value)
                        if self._cycle_direction_heating
                        else (self._active_start_value - value)
                    )
                    thresh = (
                        self.response_threshold
                        if response_threshold is None
                        else max(0.01, float(response_threshold))
                    )
                    if progress + PEAK_CONFIRM_EPSILON >= thresh:
                        obs_delay = max(0.5, min(120.0, self._active_minutes))
                        if self.response_delay_samples == 0 or self.response_delay_min is None:
                            self.response_delay_min = obs_delay
                        else:
                            self.response_delay_min = (
                                (1.0 - self.ewma_alpha) * self.response_delay_min
                                + self.ewma_alpha * obs_delay
                            )
                        self.response_delay_samples += 1
                        self.response_delay_memory.learn(obs_delay, ts)
                        self._response_detected = True
            else:
                # Transition ACTIVE -> POST_STOP_COAST
                if self._cycle_contaminated:
                    self._reject_cycle(self.last_quality_reason or "ventilation_contamination")
                    self._enter_passive_drift(value, blocked_for_learning, solar_rising_active)
                elif self._active_minutes < self.min_run_min:
                    self._reject_cycle("short_run")
                    self._enter_passive_drift(value, blocked_for_learning, solar_rising_active)
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
                    self._stop_target = float(target_val) if target_val is not None else None
                    self._stop_outdoor = float(outdoor_temp) if outdoor_temp is not None else None
                    self._stopped_by_early_stop = bool(early_stop_active)
                    self._raw_predicted_coast_at_stop = self.predict_coast(
                        abs_slope=slope,
                        outdoor_temp=self._stop_outdoor,
                        now_ts=ts,
                        apply_bias=False,
                        apply_shortfall_scale=False,
                    )
                    self._predicted_coast_at_stop = self.predict_coast(
                        abs_slope=slope,
                        outdoor_temp=self._stop_outdoor,
                        now_ts=ts,
                        apply_bias=True,
                        apply_shortfall_scale=False,
                    )
                    self._reversal_step = 0
                    self._reversal_anchor = None
                    self._coast_minutes = 0.0
                    self.phase = CyclePhase.POST_STOP_COAST

        elif self.phase == CyclePhase.POST_STOP_COAST:
            if is_active:
                # Re-started before coasting finished
                self._finalize_coast(interrupted=True, now_ts=ts)
                self._start_active_cycle(value, dt, is_heating, blocked_for_learning)
            elif self._cycle_contaminated:
                self._reject_cycle(self.last_quality_reason or "ventilation_contamination")
                self._enter_passive_drift(value, blocked_for_learning, solar_rising_active)
            else:
                self._coast_minutes += dt
                # Guard against solar daytime temperature surge corrupting heating afterglow peak.
                # Once coasting is learned, only reject when value exceeds expected_peak + 0.5°C.
                if (
                    solar_rising_active
                    and self._cycle_direction_heating
                    and self._stop_value is not None
                ):
                    has_learned_pred = (
                        self.accepted_count > 0 and self._predicted_coast_at_stop > 0.0
                    )
                    if has_learned_pred:
                        expected_peak = self._stop_value + self._predicted_coast_at_stop
                        solar_exceeded = value > (expected_peak + 0.5)
                    else:
                        solar_exceeded = (value - self._stop_value) > 0.5
                    if solar_exceeded:
                        self._reject_cycle("solar_gain_contamination")
                        self._enter_passive_drift(value, blocked_for_learning, solar_rising_active)
                        return self.phase

                if self._peak_coast_value is None:
                    self._peak_coast_value = value

                peak_extended = (
                    (value > self._peak_coast_value)
                    if self._cycle_direction_heating
                    else (value < self._peak_coast_value)
                )
                peak_confirmed = False
                if peak_extended:
                    self._peak_coast_value = value
                    self._reversal_step = 0
                    self._reversal_anchor = None
                else:
                    drop = (
                        (self._peak_coast_value - value)
                        if self._cycle_direction_heating
                        else (value - self._peak_coast_value)
                    )
                    if self._reversal_step == 0:
                        if drop + PEAK_CONFIRM_EPSILON >= self.peak_confirm_delta:
                            self._reversal_step = 1
                            self._reversal_anchor = value
                    elif self._reversal_anchor is not None:
                        step_drop = (
                            (self._reversal_anchor - value)
                            if self._cycle_direction_heating
                            else (value - self._reversal_anchor)
                        )
                        if step_drop + PEAK_CONFIRM_EPSILON >= self.peak_confirm_delta:
                            peak_confirmed = True
                        elif drop < self.peak_confirm_delta * 0.5:
                            self._reversal_step = 0
                            self._reversal_anchor = None

                if (peak_confirmed and self._coast_minutes >= 2.0) or (
                    self._coast_minutes >= self.max_coast_window_min
                ):
                    self._finalize_coast(interrupted=False, now_ts=ts)
                    self._enter_passive_drift(value, blocked_for_learning, solar_rising_active)

        elif self.phase == CyclePhase.PASSIVE_DRIFT:
            self._recent_values.append(value)
            if is_active:
                if not blocked_for_learning and not solar_rising_active:
                    self._record_passive_drift_sample(value, outdoor_temp)
                self._start_active_cycle(value, dt, is_heating, blocked_for_learning)
            else:
                recorded = False
                if blocked_for_learning or solar_rising_active:
                    self._reset_passive_drift_window(None)
                else:
                    if self._passive_start_value is None:
                        self._reset_passive_drift_window(value)
                    else:
                        self._passive_elapsed_min += dt
                        if outdoor_temp is not None:
                            self._passive_outdoor_sum += float(outdoor_temp)
                            self._passive_outdoor_count += 1
                        recorded = self._record_passive_drift_sample(value, outdoor_temp)

                if self.emitter_charge < 0.05 and (
                    recorded
                    or blocked_for_learning
                    or solar_rising_active
                    or self._passive_elapsed_min >= 30.0
                ):
                    self.phase = CyclePhase.IDLE

        return self.phase

    def _finalize_coast(self, interrupted: bool = False, now_ts: float | None = None) -> None:
        ts = time.time() if now_ts is None else float(now_ts)
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

        # Quarantine extreme uncorroborated coasting spikes until seen twice
        if len(self.history) >= 3:
            med_coast = median(float(h.get("obs_coast", 0.0)) for h in self.history)
            delays = [float(h["coast_minutes"]) for h in self.history if h.get("coast_minutes") is not None]
            med_delay = median(delays) if delays else 0.0
            is_extreme_coast = obs_coast > max(med_coast * 2.5, med_coast + 1.0)
            is_extreme_delay = med_delay >= 2.0 and self._coast_minutes > max(med_delay * 3.5, 90.0)
            if is_extreme_coast or is_extreme_delay:
                corroborated = any(
                    abs(o["obs_coast"] - obs_coast) <= max(0.35, obs_coast * 0.3)
                    and abs(o["coast_minutes"] - self._coast_minutes) <= 30.0
                    for o in self._quarantined_outliers
                )
                self._quarantined_outliers.append(
                    {"obs_coast": obs_coast, "coast_minutes": self._coast_minutes}
                )
                if not corroborated:
                    self._reject_cycle("inconsistent_coast_outlier")
                    return

        if self.inertia_profile in (INERTIA_HIGH_INERTIA, INERTIA_CUSTOM):
            if self._stop_charge < self.min_charge:
                self._reject_cycle("low_slope_or_charge")
                return
            sample_k = max(0.0, min(10.0, obs_coast / self._stop_charge))
            self.k_afterglow = (1.0 - self.ewma_alpha) * self.k_afterglow + self.ewma_alpha * sample_k
            self.k_afterglow_memory.learn(sample_k, ts)
        else:
            if self._stop_slope < self.min_slope:
                if (
                    self._stop_slope <= LOW_SLOPE_DELAYED_THRESHOLD
                    and self._stop_charge >= self.min_charge
                    and self._active_minutes >= self.min_run_min
                    and obs_coast > 0.0
                ):
                    # Delayed heating with near-zero stop slope: learn via emitter_charge safely
                    sample_k = max(0.0, min(10.0, obs_coast / self._stop_charge))
                    self.k_afterglow = (
                        (1.0 - self.ewma_alpha) * self.k_afterglow
                        + self.ewma_alpha * sample_k
                    )
                    self.k_afterglow_memory.learn(sample_k, ts)
                else:
                    self._reject_cycle("low_slope_or_charge")
                    return
            else:
                sample_tau = max(0.0, min(30.0, obs_coast / self._stop_slope))
                self.tau_coast = (1.0 - self.ewma_alpha) * self.tau_coast + self.ewma_alpha * sample_tau
                self.tau_coast_memory.learn(sample_tau, ts)

        if self._stop_outdoor is not None:
            band_key = str(
                math.floor(float(self._stop_outdoor) / OUTDOOR_BAND_WIDTH_C)
                * int(OUTDOOR_BAND_WIDTH_C)
            )
            band_mem = self.outdoor_band_memories.get(band_key)
            if band_mem is None:
                band_mem = DualLayerMemory(scale=0.3, current_weight_limit=0.5)
                self.outdoor_band_memories[band_key] = band_mem
            band_mem.learn(obs_coast, ts)

        pred_coast = float(self._predicted_coast_at_stop)
        raw_pred_coast = float(self._raw_predicted_coast_at_stop)
        coast_error = abs(obs_coast - pred_coast)
        signed_err = obs_coast - raw_pred_coast
        overshoot = 0.0
        shortfall = 0.0
        if self._stop_target is not None:
            if self._cycle_direction_heating:
                overshoot = max(0.0, self._peak_coast_value - self._stop_target)
                shortfall = max(0.0, self._stop_target - self._peak_coast_value)
            else:
                overshoot = max(0.0, self._stop_target - self._peak_coast_value)
                shortfall = max(0.0, self._peak_coast_value - self._stop_target)

        if not interrupted:
            # Update signed coast bias EWMA against raw (unbiased) model prediction
            if self.coast_bias_samples == 0:
                raw_bias = COAST_BIAS_ALPHA * signed_err
            else:
                raw_bias = (1.0 - COAST_BIAS_ALPHA) * self.coast_bias + COAST_BIAS_ALPHA * signed_err
            self.coast_bias = max(-COAST_BIAS_CLAMP, min(COAST_BIAS_CLAMP, raw_bias))
            self.coast_bias_samples += 1

            # Shortfall defense for early-stop cycles
            if self._stop_target is not None:
                if self._stopped_by_early_stop and shortfall > SHORTFALL_TOLERANCE:
                    self.consecutive_shortfalls += 1
                    self.shortfall_margin_scale = round(
                        max(SHORTFALL_MIN_SCALE, self.shortfall_margin_scale * SHORTFALL_DECAY_FACTOR),
                        4,
                    )
                elif shortfall <= SHORTFALL_TOLERANCE:
                    self.consecutive_shortfalls = 0
                    self.shortfall_margin_scale = round(
                        min(1.0, self.shortfall_margin_scale + SHORTFALL_RECOVERY_STEP),
                        4,
                    )

        self.accepted_count += 1
        self.last_quality_reason = "accepted"
        self.history.append(
            {
                "obs_coast": round(obs_coast, 4),
                "coast_minutes": round(self._coast_minutes, 1),
                "stop_slope": round(self._stop_slope, 4),
                "stop_charge": round(self._stop_charge, 4),
                "tau_coast": round(self.tau_coast, 4),
                "k_afterglow": round(self.k_afterglow, 4),
                "interrupted": interrupted,
                "outdoor_at_stop": (
                    round(self._stop_outdoor, 2) if self._stop_outdoor is not None else None
                ),
            }
        )
        self._last_completed_cycle = {
            "predicted_coast": round(pred_coast, 4),
            "observed_coast": round(obs_coast, 4),
            "coast_error": round(coast_error, 4),
            "signed_coast_error": round(obs_coast - pred_coast, 4),
            "overshoot": round(overshoot, 4),
            "shortfall": round(shortfall, 4),
            "stopped_by_early_stop": self._stopped_by_early_stop,
            "coast_minutes": round(self._coast_minutes, 1),
            "peak_value": round(self._peak_coast_value, 4),
            "stop_value": round(self._stop_value, 4),
            "target_value": round(self._stop_target, 4) if self._stop_target is not None else None,
            "interrupted": interrupted,
        }

    def get_coast_confidence(self) -> float:
        """Return coasting confidence in [0.0, 1.0] based on sample count and obs_coast std."""
        vals = [float(h.get("obs_coast", 0.0)) for h in self.history]
        n = len(vals)
        if n == 0:
            return 0.0
        mean_val = sum(vals) / n
        variance = sum((v - mean_val) ** 2 for v in vals) / n
        std = math.sqrt(variance)
        return round(min(n / 8.0, 1.0) / (1.0 + std / 0.5), 4)

    def get_outdoor_coast_correction(
        self, outdoor_temp: float | None, now_ts: float | None = None
    ) -> tuple[float, str]:
        """Return (correction_delta, status) using 5°C outdoor temperature bands."""
        if outdoor_temp is None:
            return 0.0, "missing"
        ts = time.time() if now_ts is None else float(now_ts)
        target_band = math.floor(float(outdoor_temp) / OUTDOOR_BAND_WIDTH_C) * int(OUTDOOR_BAND_WIDTH_C)
        all_vals: list[float] = []
        band_vals: list[float] = []
        for h in self.history:
            obs = float(h.get("obs_coast", 0.0))
            all_vals.append(obs)
            h_out = h.get("outdoor_at_stop")
            if h_out is not None:
                try:
                    h_band = math.floor(float(h_out) / OUTDOOR_BAND_WIDTH_C) * int(OUTDOOR_BAND_WIDTH_C)
                    if h_band == target_band:
                        band_vals.append(obs)
                except (TypeError, ValueError):
                    continue

        overall_avg = (sum(all_vals) / len(all_vals)) if all_vals else None
        band_mem = self.outdoor_band_memories.get(str(target_band))
        if band_mem is not None and overall_avg is not None:
            est = band_mem.estimate(ts)
            if est is not None:
                correction = max(
                    -OUTDOOR_COAST_CORRECTION_CLAMP,
                    min(OUTDOOR_COAST_CORRECTION_CLAMP, est[0] - overall_avg),
                )
                return round(correction, 4), "learned"

        if len(band_vals) < OUTDOOR_BAND_MIN_SAMPLES or overall_avg is None:
            return 0.0, "untrained"
        band_avg = sum(band_vals) / len(band_vals)
        correction = max(
            -OUTDOOR_COAST_CORRECTION_CLAMP,
            min(OUTDOOR_COAST_CORRECTION_CLAMP, band_avg - overall_avg),
        )
        return round(correction, 4), "learned"

    def predict_coast(
        self,
        abs_slope: float | None = None,
        outdoor_temp: float | None = None,
        now_ts: float | None = None,
        *,
        apply_bias: bool = True,
        apply_shortfall_scale: bool = False,
    ) -> float:
        """Predict coasting excursion using dual-layer memory, outdoor band, signed bias & shortfall scale."""
        ts = time.time() if now_ts is None else float(now_ts)
        if self.inertia_profile in (INERTIA_HIGH_INERTIA, INERTIA_CUSTOM):
            est = self.k_afterglow_memory.estimate(ts)
            if est is not None:
                k_val = est[0]
                self.last_memory_source = est[2]
                self.last_memory_current_weight = est[3]
            else:
                k_val = self.k_afterglow
                self.last_memory_source = "legacy"
                self.last_memory_current_weight = 0.0
            base_pred = max(0.0, k_val * self.emitter_charge)
        else:
            if abs_slope is None:
                if len(self._recent_values) >= 2:
                    abs_slope = abs(self._recent_values[-1] - self._recent_values[0]) / max(
                        1.0, float(len(self._recent_values) - 1)
                    )
                else:
                    abs_slope = self._stop_slope
            est = self.tau_coast_memory.estimate(ts)
            if est is not None:
                tau_val = est[0]
                self.last_memory_source = est[2]
                self.last_memory_current_weight = est[3]
            else:
                tau_val = self.tau_coast
                self.last_memory_source = "legacy"
                self.last_memory_current_weight = 0.0
            base_pred = max(0.0, tau_val * max(0.0, float(abs_slope)))
        if base_pred <= 0.0:
            return 0.0
        correction, _ = self.get_outdoor_coast_correction(outdoor_temp, now_ts=ts)
        bias_adj = self.coast_bias if (apply_bias and self.coast_bias_samples >= 3) else 0.0
        if bias_adj < 0.0:
            bias_adj = max(-0.8 * base_pred, bias_adj)
        pred = min(self.max_pred, max(0.0, base_pred + correction + bias_adj))
        if apply_shortfall_scale:
            pred *= self.shortfall_margin_scale
        return pred

    def get_memory_summary(self, now_ts: float | None = None) -> dict[str, Any]:
        """Return diagnostics summary of all dual-layer memories."""
        ts = time.time() if now_ts is None else float(now_ts)
        return {
            "k_afterglow": self.k_afterglow_memory.summary(ts),
            "tau_coast": self.tau_coast_memory.summary(ts),
            "response_delay": self.response_delay_memory.summary(ts),
            "outdoor_bands": {
                k: m.summary(ts) for k, m in self.outdoor_band_memories.items()
            },
            "last_memory_source": self.last_memory_source,
            "last_memory_current_weight": round(self.last_memory_current_weight, 4),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "inertia_profile": self.inertia_profile,
            "tau_em_min": self.tau_em_min,
            "max_coast_window_min": self.max_coast_window_min,
            "peak_confirm_delta": self.peak_confirm_delta,
            "phase": self.phase.value,
            "tau_coast": self.tau_coast,
            "k_afterglow": self.k_afterglow,
            "emitter_charge": self.emitter_charge,
            "response_delay_min": self.response_delay_min,
            "response_delay_samples": self.response_delay_samples,
            "passive_drift_rate": self.passive_drift_rate,
            "passive_drift_samples": self.passive_drift_samples,
            "passive_drift_by_band": {
                str(k): {
                    "rate": float(v.get("rate", 0.0)),
                    "samples": int(v.get("samples", 0)),
                }
                for k, v in self.passive_drift_by_band.items()
                if isinstance(v, dict)
            },
            "k_afterglow_memory": self.k_afterglow_memory.to_dict(),
            "tau_coast_memory": self.tau_coast_memory.to_dict(),
            "response_delay_memory": self.response_delay_memory.to_dict(),
            "outdoor_band_memories": {
                str(k): m.to_dict() for k, m in self.outdoor_band_memories.items()
            },
            "coast_bias": round(self.coast_bias, 6),
            "coast_bias_samples": self.coast_bias_samples,
            "shortfall_margin_scale": round(self.shortfall_margin_scale, 4),
            "consecutive_shortfalls": self.consecutive_shortfalls,
            "accepted_count": self.accepted_count,
            "rejected_counts": dict(self.rejected_counts),
            "last_quality_reason": self.last_quality_reason,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DynamicCoastingLearner:
        obj = cls(
            inertia_profile=str(data.get("inertia_profile", INERTIA_FAST_AIR)),
            tau_em_min=float(data["tau_em_min"]) if "tau_em_min" in data and data["tau_em_min"] is not None else None,
            max_coast_window_min=(
                float(data["max_coast_window_min"])
                if "max_coast_window_min" in data and data["max_coast_window_min"] is not None
                else None
            ),
            peak_confirm_delta=float(data.get("peak_confirm_delta", 0.1)),
        )
        try:
            obj.phase = CyclePhase(data.get("phase", CyclePhase.IDLE.value))
        except ValueError:
            obj.phase = CyclePhase.IDLE
        obj.tau_coast = float(data.get("tau_coast", obj.tau_coast))
        obj.k_afterglow = float(data.get("k_afterglow", obj.k_afterglow))
        obj.emitter_charge = float(data.get("emitter_charge", 0.0))
        raw_delay = data.get("response_delay_min")
        obj.response_delay_min = float(raw_delay) if raw_delay is not None else None
        obj.response_delay_samples = int(data.get("response_delay_samples", 0))
        obj.passive_drift_rate = max(0.0, float(data.get("passive_drift_rate", 0.0)))
        obj.passive_drift_samples = int(data.get("passive_drift_samples", 0))
        raw_bands = data.get("passive_drift_by_band")
        if isinstance(raw_bands, dict):
            obj.passive_drift_by_band = {
                str(k): {
                    "rate": max(0.0, float(v.get("rate", 0.0))),
                    "samples": int(v.get("samples", 0)),
                }
                for k, v in raw_bands.items()
                if isinstance(v, dict)
            }
        if "k_afterglow_memory" in data and data["k_afterglow_memory"] is not None:
            obj.k_afterglow_memory = DualLayerMemory.from_dict(
                data["k_afterglow_memory"], scale=0.35
            )
        if "tau_coast_memory" in data and data["tau_coast_memory"] is not None:
            obj.tau_coast_memory = DualLayerMemory.from_dict(
                data["tau_coast_memory"], scale=1.5
            )
        if "response_delay_memory" in data and data["response_delay_memory"] is not None:
            obj.response_delay_memory = DualLayerMemory.from_dict(
                data["response_delay_memory"], scale=5.0
            )
        raw_out_mems = data.get("outdoor_band_memories")
        if isinstance(raw_out_mems, dict):
            for k, v in raw_out_mems.items():
                obj.outdoor_band_memories[str(k)] = DualLayerMemory.from_dict(
                    v, scale=0.3, current_weight_limit=0.5
                )
        elif raw_out_mems is not None:
            _LOGGER.warning("Discarding corrupted outdoor_band_memories payload")
        try:
            raw_bias = float(data.get("coast_bias", 0.0))
            obj.coast_bias = (
                max(-COAST_BIAS_CLAMP, min(COAST_BIAS_CLAMP, raw_bias))
                if math.isfinite(raw_bias)
                else 0.0
            )
        except (TypeError, ValueError):
            obj.coast_bias = 0.0
        try:
            obj.coast_bias_samples = max(0, int(data.get("coast_bias_samples", 0)))
        except (TypeError, ValueError):
            obj.coast_bias_samples = 0
        try:
            raw_scale = float(data.get("shortfall_margin_scale", 1.0))
            obj.shortfall_margin_scale = (
                max(SHORTFALL_MIN_SCALE, min(1.0, raw_scale))
                if math.isfinite(raw_scale)
                else 1.0
            )
        except (TypeError, ValueError):
            obj.shortfall_margin_scale = 1.0
        try:
            obj.consecutive_shortfalls = max(0, int(data.get("consecutive_shortfalls", 0)))
        except (TypeError, ValueError):
            obj.consecutive_shortfalls = 0
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
    dead_time_min: float = 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp": self.kp,
            "ki": self.ki,
            "ke": self.ke,
            "ti_min": self.ti_min,
            "learned_rate": self.learned_rate,
            "learned_loss": self.learned_loss,
            "dead_time_min": self.dead_time_min,
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
            dead_time_min=float(data.get("dead_time_min", 2.0)),
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
            dead_time_min=round(theta, 2),
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
        self.applied_params: TuningParameters | None = None
        self.last_fallback_reason: str | None = None

        # Cumulative coasting prediction accuracy (MAE) tracking
        self.accuracy_count: int = 0
        self.accuracy_abs_err_sum: float = 0.0
        self.last_predicted_coast: float | None = None
        self.last_observed_coast: float | None = None
        self.last_coast_error: float | None = None
        self.last_signed_coast_error: float | None = None

    @property
    def mean_absolute_error(self) -> float | None:
        if self.accuracy_count <= 0:
            return None
        return round(self.accuracy_abs_err_sum / float(self.accuracy_count), 4)

    def record_prediction_comparison(
        self, predicted_coast: float, observed_coast: float
    ) -> float:
        """Accumulate real-world post-stop coast prediction error (MAE) and signed error."""
        pred = float(predicted_coast)
        obs = float(observed_coast)
        signed_err = obs - pred
        err = abs(signed_err)
        self.accuracy_count += 1
        self.accuracy_abs_err_sum += err
        self.last_predicted_coast = round(pred, 4)
        self.last_observed_coast = round(obs, 4)
        self.last_coast_error = round(err, 4)
        self.last_signed_coast_error = round(signed_err, 4)
        return self.last_coast_error

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
        applied_params: TuningParameters | None = None,
    ) -> None:
        """Save snapshot before applying new SIMC parameters."""
        self.active_snapshot = TuningSnapshot(
            params=prev_params,
            baseline_mae=max(0.05, float(baseline_mae)),
            baseline_overshoot=max(0.05, float(baseline_overshoot)),
        )
        if applied_params is not None:
            self.applied_params = applied_params
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
            self.applied_params = snap.params
            return snap.params

        self.last_fallback_reason = None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "season_applies": self.season_applies,
            "lifetime_applies": self.lifetime_applies,
            "rollback_count": self.rollback_count,
            "last_fallback_reason": self.last_fallback_reason,
            "accuracy_count": self.accuracy_count,
            "accuracy_abs_err_sum": self.accuracy_abs_err_sum,
            "last_predicted_coast": self.last_predicted_coast,
            "last_observed_coast": self.last_observed_coast,
            "last_coast_error": self.last_coast_error,
            "last_signed_coast_error": self.last_signed_coast_error,
            "active_snapshot": (
                self.active_snapshot.to_dict() if self.active_snapshot is not None else None
            ),
            "applied_params": (
                self.applied_params.to_dict() if self.applied_params is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TuningSafetyManager:
        obj = cls()
        obj.season_applies = int(data.get("season_applies", 0))
        obj.lifetime_applies = int(data.get("lifetime_applies", 0))
        obj.rollback_count = int(data.get("rollback_count", 0))
        obj.last_fallback_reason = data.get("last_fallback_reason")
        obj.accuracy_count = int(data.get("accuracy_count", 0))
        obj.accuracy_abs_err_sum = float(data.get("accuracy_abs_err_sum", 0.0))
        obj.last_predicted_coast = (
            float(data["last_predicted_coast"])
            if data.get("last_predicted_coast") is not None
            else None
        )
        obj.last_observed_coast = (
            float(data["last_observed_coast"])
            if data.get("last_observed_coast") is not None
            else None
        )
        obj.last_coast_error = (
            float(data["last_coast_error"])
            if data.get("last_coast_error") is not None
            else None
        )
        obj.last_signed_coast_error = (
            float(data["last_signed_coast_error"])
            if data.get("last_signed_coast_error") is not None
            else None
        )
        snap_raw = data.get("active_snapshot")
        if isinstance(snap_raw, Mapping):
            obj.active_snapshot = TuningSnapshot.from_dict(dict(snap_raw))
        applied_raw = data.get("applied_params")
        if isinstance(applied_raw, Mapping):
            try:
                obj.applied_params = TuningParameters.from_dict(dict(applied_raw))
            except (TypeError, ValueError):
                obj.applied_params = None
        return obj
