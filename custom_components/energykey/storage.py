"""Persistent session and history storage for EnergyKey."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_PREFIX, STORAGE_VERSION
from .models import ConsumptionPoint


class _EnergyKeyVersionedStore(Store[dict[str, Any]]):
    """Migrate persisted integration state between storage versions."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: Any,
    ) -> dict[str, Any]:
        """Upgrade the version-one state without exposing its contents."""
        if old_major_version != 1 or not isinstance(old_data, dict):
            raise ValueError(
                f"Unsupported EnergyKey storage version {old_major_version}"
            )
        migrated = dict(old_data)
        migrated.setdefault("statistics_offsets", {})
        return migrated


class EnergyKeyStore:
    """Serialize sensitive session state and normalized meter history."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize an entry-scoped store."""
        self._store: Store[dict[str, Any]] = _EnergyKeyVersionedStore(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry_id}",
            private=True,
        )
        self._lock = asyncio.Lock()
        self._cookies: dict[str, str] = {}
        self._history: dict[str, tuple[ConsumptionPoint, ...]] = {}
        self._statistics_offsets: dict[str, float] = {}

    @property
    def cookies(self) -> dict[str, str]:
        """Return a copy of persisted cookies."""
        return dict(self._cookies)

    def history(self, meter_key: str) -> tuple[ConsumptionPoint, ...]:
        """Return persisted history for one opaque meter key."""
        return self._history.get(meter_key, ())

    @property
    def history_keys(self) -> tuple[str, ...]:
        """Return opaque history stream keys for cleanup."""
        return tuple(self._history)

    def statistics_offset(self, history_key: str) -> float:
        """Return the cumulative statistics offset for one history stream."""
        return self._statistics_offsets.get(history_key, 0.0)

    async def async_load(self) -> None:
        """Load and validate persisted state."""
        raw = await self._store.async_load()
        if not isinstance(raw, dict):
            return
        cookies = raw.get("cookies")
        if isinstance(cookies, dict) and all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in cookies.items()
        ):
            self._cookies = dict(cookies)

        history = raw.get("history")
        if not isinstance(history, dict):
            return
        parsed: dict[str, tuple[ConsumptionPoint, ...]] = {}
        for meter_key, points in history.items():
            if not isinstance(meter_key, str) or not isinstance(points, list):
                continue
            valid_points: list[ConsumptionPoint] = []
            for point in points:
                if not isinstance(point, dict):
                    continue
                try:
                    valid_points.append(ConsumptionPoint.from_storage(point))
                except (KeyError, TypeError, ValueError):
                    continue
            parsed[meter_key] = tuple(
                sorted(valid_points, key=lambda item: (item.start, item.end))
            )
        self._history = parsed

        offsets = raw.get("statistics_offsets")
        if isinstance(offsets, dict):
            self._statistics_offsets = {
                key: float(value)
                for key, value in offsets.items()
                if isinstance(key, str)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
            }

    async def async_set_cookies(self, cookies: dict[str, str]) -> None:
        """Atomically replace and persist session cookies."""
        async with self._lock:
            self._cookies = dict(cookies)
            await self._async_save_locked()

    async def async_set_history(
        self, meter_key: str, points: tuple[ConsumptionPoint, ...]
    ) -> None:
        """Atomically replace and persist one meter's history."""
        async with self._lock:
            self._history[meter_key] = points
            await self._async_save_locked()

    async def async_set_histories(
        self, history: dict[str, tuple[ConsumptionPoint, ...]]
    ) -> None:
        """Atomically replace and persist all meter history."""
        async with self._lock:
            self._history = dict(history)
            await self._async_save_locked()

    async def async_set_histories_and_offsets(
        self,
        history: dict[str, tuple[ConsumptionPoint, ...]],
        statistics_offsets: dict[str, float],
    ) -> None:
        """Atomically persist histories and their cumulative statistics offsets."""
        async with self._lock:
            self._history = dict(history)
            self._statistics_offsets = dict(statistics_offsets)
            await self._async_save_locked()

    async def async_save(self) -> None:
        """Persist all current state."""
        async with self._lock:
            await self._async_save_locked()

    async def async_remove(self) -> None:
        """Remove all persisted state for the entry."""
        async with self._lock:
            self._cookies = {}
            self._history = {}
            self._statistics_offsets = {}
            await self._store.async_remove()

    async def _async_save_locked(self) -> None:
        await self._store.async_save(
            {
                "cookies": self._cookies,
                "history": {
                    meter_key: [point.to_storage() for point in points]
                    for meter_key, points in self._history.items()
                },
                "statistics_offsets": self._statistics_offsets,
            }
        )
