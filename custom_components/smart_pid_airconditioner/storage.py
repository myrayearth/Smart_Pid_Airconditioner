"""Persistent storage wrapper for Smart PID self-learning state."""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .learner import (
    DynamicCoastingLearner,
    HuberRLS,
    INERTIA_FAST_AIR,
    TuningSafetyManager,
)

STORAGE_VERSION = 1
STORAGE_MINOR_VERSION = 1
DEFAULT_SAVE_DELAY = 600  # 10 minutes debounced save


class LearningStateSnapshot:
    """Serializable container for all per-entry learning state."""

    def __init__(
        self,
        temp_rls: HuberRLS | None = None,
        hum_rls: HuberRLS | None = None,
        coasting: DynamicCoastingLearner | None = None,
        safety: TuningSafetyManager | None = None,
        inertia_profile: str = INERTIA_FAST_AIR,
    ) -> None:
        self.temp_rls = temp_rls or HuberRLS(
            n_features=3,
            initial_theta=[0.05, 0.005, 0.0],
            bounds=[(0.001, 2.0), (0.0001, 0.2), (-0.5, 1.0)],
        )
        self.hum_rls = hum_rls or HuberRLS(
            n_features=2,
            initial_theta=[0.2, 0.01],
            bounds=[(0.001, 5.0), (0.0001, 0.5)],
        )
        self.coasting = coasting or DynamicCoastingLearner(inertia_profile=inertia_profile)
        self.safety = safety or TuningSafetyManager()

    def to_dict(self) -> dict[str, Any]:
        return {
            "temp_rls": self.temp_rls.to_dict(),
            "hum_rls": self.hum_rls.to_dict(),
            "coasting": self.coasting.to_dict(),
            "safety": self.safety.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearningStateSnapshot:
        return cls(
            temp_rls=HuberRLS.from_dict(data["temp_rls"]) if "temp_rls" in data else None,
            hum_rls=HuberRLS.from_dict(data["hum_rls"]) if "hum_rls" in data else None,
            coasting=(
                DynamicCoastingLearner.from_dict(data["coasting"])
                if "coasting" in data
                else None
            ),
            safety=(
                TuningSafetyManager.from_dict(data["safety"])
                if "safety" in data
                else None
            ),
        )


class ModelStore:
    """Wrapper around homeassistant.helpers.storage.Store with 10-min debouncing."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}",
            minor_version=STORAGE_MINOR_VERSION,
            atomic_writes=True,
            serialize_in_event_loop=True,
        )

    async def async_load(
        self, inertia_profile: str = INERTIA_FAST_AIR
    ) -> LearningStateSnapshot:
        """Load persisted learning state or return fresh defaults."""
        raw = await self._store.async_load()
        if isinstance(raw, dict):
            try:
                snap = LearningStateSnapshot.from_dict(raw)
                snap.coasting.inertia_profile = inertia_profile
                return snap
            except Exception:
                pass
        return LearningStateSnapshot(inertia_profile=inertia_profile)

    async def async_save(self, snapshot: LearningStateSnapshot) -> None:
        """Immediately persist learning state."""
        await self._store.async_save(snapshot.to_dict())

    def async_delay_save(
        self,
        data_func: Callable[[], dict[str, Any]],
        delay: float = DEFAULT_SAVE_DELAY,
    ) -> None:
        """Schedule debounced save (default 600s)."""
        self._store.async_delay_save(data_func, delay=delay)

    async def async_remove(self) -> None:
        """Remove persisted store data."""
        await self._store.async_remove()
