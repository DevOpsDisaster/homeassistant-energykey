"""Runtime types for the EnergyKey integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from .api import EnergyKeyClient
from .coordinator import EnergyKeyCoordinator
from .storage import EnergyKeyStore


@dataclass(slots=True)
class EnergyKeyRuntimeData:
    """Objects and diagnostics tied to one config entry."""

    client: EnergyKeyClient
    coordinator: EnergyKeyCoordinator
    store: EnergyKeyStore
    data_refresh_task: asyncio.Task[None] | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    reauth_started: bool = False
    last_successful_heartbeat: datetime | None = None
    last_heartbeat_error: str | None = None
    heartbeat_attempts: int = 0
    consecutive_heartbeat_failures: int = 0
