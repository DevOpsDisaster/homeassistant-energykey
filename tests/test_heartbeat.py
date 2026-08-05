"""Tests for EnergyKey heartbeat lifecycle behavior."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from custom_components.energykey import _async_heartbeat_loop
from custom_components.energykey.api import (
    EnergyKeyAuthError,
    EnergyKeyConnectionError,
    EnergyKeyRateLimitError,
)
from custom_components.energykey.const import (
    HEARTBEAT_INTERVAL,
    HEARTBEAT_RETRY_INTERVAL,
    heartbeat_update_signal,
)


def _runtime(client) -> SimpleNamespace:
    return SimpleNamespace(
        client=client,
        store=SimpleNamespace(async_set_cookies=AsyncMock()),
        reauth_started=False,
        last_heartbeat_error=None,
        last_successful_heartbeat=None,
        heartbeat_attempts=0,
        consecutive_heartbeat_failures=0,
    )


async def test_recent_authenticated_activity_suppresses_heartbeat() -> None:
    client = SimpleNamespace(
        seconds_since_activity=5 * 60,
        async_heartbeat=AsyncMock(),
        cookie_state={},
    )
    entry = SimpleNamespace(runtime_data=_runtime(client))
    with patch(
        "custom_components.energykey.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    ) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await _async_heartbeat_loop(SimpleNamespace(), entry)

    sleep.assert_awaited_once_with(
        HEARTBEAT_INTERVAL.total_seconds() - client.seconds_since_activity
    )
    client.async_heartbeat.assert_not_awaited()


async def test_auth_rejection_starts_one_reauth_and_stops() -> None:
    client = SimpleNamespace(
        seconds_since_activity=0,
        async_heartbeat=AsyncMock(side_effect=EnergyKeyAuthError),
        cookie_state={},
    )
    runtime = _runtime(client)
    entry = SimpleNamespace(
        runtime_data=runtime,
        async_start_reauth=Mock(),
    )
    with patch(
        "custom_components.energykey.asyncio.sleep", AsyncMock(return_value=None)
    ):
        await _async_heartbeat_loop(SimpleNamespace(), entry)
        await _async_heartbeat_loop(SimpleNamespace(), entry)

    assert runtime.reauth_started is True
    assert runtime.last_heartbeat_error == "authentication"
    entry.async_start_reauth.assert_called_once()
    assert client.async_heartbeat.await_count == 2


async def test_transient_failure_retries_and_persists_rotated_cookies() -> None:
    client = SimpleNamespace(
        seconds_since_activity=0,
        async_heartbeat=AsyncMock(
            side_effect=[EnergyKeyConnectionError(), None, asyncio.CancelledError()]
        ),
        cookie_state={"wt3SessionId": "rotated"},
    )
    runtime = _runtime(client)
    entry = SimpleNamespace(entry_id="synthetic-entry", runtime_data=runtime)
    hass = SimpleNamespace()
    with patch(
        "custom_components.energykey.asyncio.sleep", AsyncMock(return_value=None)
    ) as sleep:
        with patch(
            "custom_components.energykey.async_dispatcher_send"
        ) as dispatcher_send:
            with pytest.raises(asyncio.CancelledError):
                await _async_heartbeat_loop(hass, entry)

    assert sleep.await_args_list[:3] == [
        call(HEARTBEAT_INTERVAL.total_seconds()),
        call(HEARTBEAT_RETRY_INTERVAL.total_seconds()),
        call(HEARTBEAT_INTERVAL.total_seconds()),
    ]
    runtime.store.async_set_cookies.assert_awaited_once_with(client.cookie_state)
    assert runtime.last_heartbeat_error is None
    assert runtime.last_successful_heartbeat is not None
    dispatcher_send.assert_called_once_with(
        hass, heartbeat_update_signal(entry.entry_id)
    )


async def test_rate_limit_respects_bounded_server_retry() -> None:
    client = SimpleNamespace(
        seconds_since_activity=0,
        async_heartbeat=AsyncMock(
            side_effect=[EnergyKeyRateLimitError(7 * 60), asyncio.CancelledError()]
        ),
        cookie_state={},
    )
    runtime = _runtime(client)
    entry = SimpleNamespace(entry_id="synthetic-entry", runtime_data=runtime)
    with patch(
        "custom_components.energykey.asyncio.sleep", AsyncMock(return_value=None)
    ) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await _async_heartbeat_loop(SimpleNamespace(), entry)

    assert sleep.await_args_list[:3] == [
        call(HEARTBEAT_INTERVAL.total_seconds()),
        call(7 * 60),
        call(HEARTBEAT_INTERVAL.total_seconds()),
    ]
    assert runtime.last_heartbeat_error == "rate_limited"


def test_fixed_intervals_match_session_design() -> None:
    assert HEARTBEAT_INTERVAL == timedelta(minutes=15)
    assert HEARTBEAT_RETRY_INTERVAL == timedelta(minutes=1)
