"""Psychrometric calculations based on Magnus-Tetens (Bolton 1980)."""

from __future__ import annotations

import math

# Bolton (1980) constants
_BOLTON_A = 6.112  # hPa
_BOLTON_B = 17.67
_BOLTON_C = 243.5  # °C
_GAS_CONST_FACTOR = 2.1674  # (g * K) / (m³ * hPa)


def _clamp_rh(rh_pct: float) -> float:
    if not math.isfinite(rh_pct):
        return 0.0
    return max(0.0, min(100.0, float(rh_pct)))


def _safe_temp(temp_c: float) -> float:
    if not math.isfinite(temp_c):
        return 20.0
    return max(-100.0, min(100.0, float(temp_c)))


def saturation_vapor_pressure(temp_c: float) -> float:
    """Calculate saturation vapor pressure (hPa) using Bolton (1980)."""
    t = _safe_temp(temp_c)
    return _BOLTON_A * math.exp((_BOLTON_B * t) / (t + _BOLTON_C))


def rh_to_ah(temp_c: float, rh_pct: float) -> float:
    """Convert temperature (°C) and relative humidity (%) to absolute humidity (g/m³)."""
    t = _safe_temp(temp_c)
    rh = _clamp_rh(rh_pct)
    es = saturation_vapor_pressure(t)
    kelvin = t + 273.15
    if kelvin <= 0.0:
        return 0.0
    return (es * rh * _GAS_CONST_FACTOR) / kelvin


def ah_to_rh(temp_c: float, ah_gm3: float) -> float:
    """Convert temperature (°C) and absolute humidity (g/m³) back to relative humidity (%)."""
    if not math.isfinite(ah_gm3) or ah_gm3 <= 0.0:
        return 0.0
    t = _safe_temp(temp_c)
    es = saturation_vapor_pressure(t)
    kelvin = t + 273.15
    if es <= 0.0 or kelvin <= 0.0:
        return 0.0
    rh = (float(ah_gm3) * kelvin) / (es * _GAS_CONST_FACTOR)
    return _clamp_rh(rh)


def dew_point(temp_c: float, rh_pct: float) -> float:
    """Calculate dew point temperature (°C) using Magnus-Tetens (Bolton 1980)."""
    t = _safe_temp(temp_c)
    rh = max(1e-3, _clamp_rh(rh_pct))
    gamma = math.log(rh / 100.0) + (_BOLTON_B * t) / (t + _BOLTON_C)
    denom = _BOLTON_B - gamma
    if abs(denom) < 1e-9:
        return t
    return (_BOLTON_C * gamma) / denom
