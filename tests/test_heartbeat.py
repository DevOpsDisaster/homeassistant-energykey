"""Tests for EnergyKey session and consumption keepalive lifecycle behavior."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from custom_components.energykey import (
    _async_consumption_keepalive_loop,
    _async_shutdown_heartbeat,
    async_unload_entry,
)
from custom_components.energykey.api import (
    EnergyKeyAuthError,
    EnergyKeyConnectionError,
    EnergyKeyProtocolError,
    EnergyKeyRateLimitError,
    EnergyKeyStaleResourceError,
)
from custom_components.energykey.const import (
    CONSUMPTION_KEEPALIVE_INTERVAL,
    CONSUMPTION_KEEPALIVE_LOOKBACK_DAYS,
    CONSUMPTION_KEEPALIVE_RETRY_INTERVAL,
    DATA_UPDATE_INTERVAL,
    SHUTDOWN_HEARTBEAT_TIMEOUT_SECONDS,
    heartbeat_update_signal,
)


def _completed_future() -> asyncio.Future[None]:
    future = asyncio.get_running_loop().create_future()
    future.set_result(None)
    return future


def _runtime(coordinator, *, initial_refresh=None) -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(cookie_state={"wt3SessionId": "rotated"}),
        coordinator=coordinator,
        store=SimpleNamespace(async_set_cookies=AsyncMock()),
        data_refresh_task=(
            initial_refresh if initial_refresh is not None else _completed_future()
        ),
        reauth_started=False,
        last_heartbeat_error=None,
        last_successful_heartbeat=None,
    )


def _entry(runtime) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="synthetic-entry",
        runtime_data=runtime,
        async_start_reauth=Mock(),
    )


async def test_consumption_keepalive_waits_for_due_delay() -> None:
    coordinator = SimpleNamespace(
        seconds_until_consumption_keepalive=Mock(return_value=123.0),
        async_run_consumption_keepalive=AsyncMock(),
    )
    runtime = _runtime(coordinator)
    entry = _entry(runtime)

    with patch(
        "custom_components.energykey.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    ) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await _async_consumption_keepalive_loop(SimpleNamespace(), entry)

    sleep.assert_awaited_once_with(123.0)
    coordinator.async_run_consumption_keepalive.assert_not_awaited()


async def test_consumption_keepalive_success_updates_diagnostics() -> None:
    coordinator = SimpleNamespace(
        seconds_until_consumption_keepalive=Mock(side_effect=[0.0, 0.0, 1200.0]),
        async_run_consumption_keepalive=AsyncMock(return_value=2),
    )
    runtime = _runtime(coordinator)
    entry = _entry(runtime)
    hass = SimpleNamespace()

    with patch(
        "custom_components.energykey.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    ):
        with patch(
            "custom_components.energykey.async_dispatcher_send"
        ) as dispatcher_send:
            with pytest.raises(asyncio.CancelledError):
                await _async_consumption_keepalive_loop(hass, entry)

    coordinator.async_run_consumption_keepalive.assert_awaited_once_with()
    runtime.store.async_set_cookies.assert_not_awaited()
    assert runtime.last_successful_heartbeat is not None
    assert runtime.last_heartbeat_error is None
    dispatcher_send.assert_called_once_with(
        hass, heartbeat_update_signal(entry.entry_id)
    )


async def test_full_refresh_during_delay_suppresses_due_keepalive() -> None:
    coordinator = SimpleNamespace(
        seconds_until_consumption_keepalive=Mock(side_effect=[10.0, 600.0, 600.0]),
        async_run_consumption_keepalive=AsyncMock(),
    )
    runtime = _runtime(coordinator)
    entry = _entry(runtime)

    with patch(
        "custom_components.energykey.asyncio.sleep",
        AsyncMock(side_effect=[None, asyncio.CancelledError]),
    ) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await _async_consumption_keepalive_loop(SimpleNamespace(), entry)

    assert sleep.await_args_list == [call(10.0), call(600.0)]
    coordinator.async_run_consumption_keepalive.assert_not_awaited()
    runtime.store.async_set_cookies.assert_not_awaited()
    assert runtime.last_successful_heartbeat is None


async def test_atomic_due_recheck_does_not_report_a_probe() -> None:
    coordinator = SimpleNamespace(
        seconds_until_consumption_keepalive=Mock(side_effect=[0.0, 0.0, 1200.0]),
        async_run_consumption_keepalive=AsyncMock(return_value=0),
    )
    runtime = _runtime(coordinator)
    entry = _entry(runtime)

    with patch(
        "custom_components.energykey.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    ):
        with patch(
            "custom_components.energykey.async_dispatcher_send"
        ) as dispatcher_send:
            with pytest.raises(asyncio.CancelledError):
                await _async_consumption_keepalive_loop(SimpleNamespace(), entry)

    coordinator.async_run_consumption_keepalive.assert_awaited_once_with()
    runtime.store.async_set_cookies.assert_not_awaited()
    assert runtime.last_successful_heartbeat is None
    dispatcher_send.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(EnergyKeyConnectionError("offline"), id="connection"),
        pytest.param(EnergyKeyProtocolError("invalid response"), id="protocol"),
    ],
)
async def test_consumption_keepalive_retries_transient_failure(error) -> None:
    coordinator = SimpleNamespace(
        seconds_until_consumption_keepalive=Mock(
            side_effect=[0.0, 0.0, 0.0, 0.0, 1200.0]
        ),
        async_run_consumption_keepalive=AsyncMock(side_effect=[error, 2]),
    )
    runtime = _runtime(coordinator)
    entry = _entry(runtime)

    with patch(
        "custom_components.energykey.asyncio.sleep",
        AsyncMock(side_effect=[None, asyncio.CancelledError]),
    ) as sleep:
        with patch("custom_components.energykey.async_dispatcher_send"):
            with pytest.raises(asyncio.CancelledError):
                await _async_consumption_keepalive_loop(SimpleNamespace(), entry)

    assert sleep.await_args_list == [
        call(CONSUMPTION_KEEPALIVE_RETRY_INTERVAL.total_seconds()),
        call(CONSUMPTION_KEEPALIVE_INTERVAL.total_seconds()),
    ]
    assert coordinator.async_run_consumption_keepalive.await_count == 2
    runtime.store.async_set_cookies.assert_not_awaited()
    assert runtime.last_heartbeat_error is None


@pytest.mark.parametrize("retry_after", [7 * 60, 30 * 60])
async def test_consumption_keepalive_respects_bounded_rate_limit_retry(
    retry_after: float,
) -> None:
    coordinator = SimpleNamespace(
        seconds_until_consumption_keepalive=Mock(side_effect=[0.0, 0.0, 0.0, 0.0]),
        async_run_consumption_keepalive=AsyncMock(
            side_effect=[EnergyKeyRateLimitError(retry_after), asyncio.CancelledError]
        ),
    )
    runtime = _runtime(coordinator)
    entry = _entry(runtime)

    with patch(
        "custom_components.energykey.asyncio.sleep", AsyncMock(return_value=None)
    ) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await _async_consumption_keepalive_loop(SimpleNamespace(), entry)

    sleep.assert_awaited_once_with(retry_after)
    assert coordinator.async_run_consumption_keepalive.await_count == 2
    assert runtime.last_heartbeat_error == "rate_limited"
    runtime.store.async_set_cookies.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "diagnostic_error"),
    [
        pytest.param(
            EnergyKeyAuthError("expired"), "authentication", id="authentication"
        ),
        pytest.param(
            EnergyKeyStaleResourceError("stale item"),
            "stale_resource",
            id="stale-resource",
        ),
    ],
)
async def test_auth_and_stale_resource_start_exactly_one_reauth(
    error, diagnostic_error: str
) -> None:
    coordinator = SimpleNamespace(
        seconds_until_consumption_keepalive=Mock(return_value=0.0),
        async_run_consumption_keepalive=AsyncMock(side_effect=error),
    )
    runtime = _runtime(coordinator)
    entry = _entry(runtime)
    hass = SimpleNamespace()

    await _async_consumption_keepalive_loop(hass, entry)
    await _async_consumption_keepalive_loop(hass, entry)

    assert runtime.reauth_started is True
    assert runtime.last_heartbeat_error == diagnostic_error
    entry.async_start_reauth.assert_called_once_with(hass)
    coordinator.async_run_consumption_keepalive.assert_awaited_once_with()


async def test_consumption_keepalive_cancellation_while_waiting_for_refresh() -> None:
    initial_refresh = asyncio.get_running_loop().create_future()
    coordinator = SimpleNamespace(
        seconds_until_consumption_keepalive=Mock(),
        async_run_consumption_keepalive=AsyncMock(),
    )
    runtime = _runtime(coordinator, initial_refresh=initial_refresh)
    entry = _entry(runtime)

    task = asyncio.create_task(
        _async_consumption_keepalive_loop(SimpleNamespace(), entry)
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    coordinator.seconds_until_consumption_keepalive.assert_not_called()
    coordinator.async_run_consumption_keepalive.assert_not_awaited()


async def test_unload_cancels_consumption_keepalive_task() -> None:
    async def wait_forever() -> None:
        await asyncio.Future()

    data_refresh_task = asyncio.create_task(asyncio.sleep(0))
    await data_refresh_task
    keepalive_task = asyncio.create_task(wait_forever())
    await asyncio.sleep(0)
    client = SimpleNamespace(cookie_state={}, detach=Mock())
    store = SimpleNamespace(async_set_cookies=AsyncMock())
    runtime = SimpleNamespace(
        client=client,
        store=store,
        data_refresh_task=data_refresh_task,
        consumption_keepalive_task=keepalive_task,
    )
    entry = SimpleNamespace(runtime_data=runtime)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(return_value=True)
        )
    )

    assert await async_unload_entry(hass, entry)

    assert keepalive_task.cancelled()
    store.async_set_cookies.assert_awaited_once_with(client.cookie_state)
    client.detach.assert_called_once_with()


def test_production_intervals_match_consumption_session_design() -> None:
    assert CONSUMPTION_KEEPALIVE_INTERVAL == timedelta(minutes=20)
    assert CONSUMPTION_KEEPALIVE_RETRY_INTERVAL == timedelta(minutes=1)
    assert CONSUMPTION_KEEPALIVE_LOOKBACK_DAYS == 7
    assert DATA_UPDATE_INTERVAL == timedelta(hours=6)
    assert SHUTDOWN_HEARTBEAT_TIMEOUT_SECONDS == 5


async def test_shutdown_heartbeat_persists_rotated_cookies() -> None:
    client = SimpleNamespace(
        async_heartbeat=AsyncMock(),
        cookie_state={"wt3SessionId": "rotated"},
    )
    runtime = _runtime(SimpleNamespace(), initial_refresh=_completed_future())
    runtime.client = client
    entry = SimpleNamespace(runtime_data=runtime)

    await _async_shutdown_heartbeat(Mock(), entry)

    client.async_heartbeat.assert_awaited_once_with()
    runtime.store.async_set_cookies.assert_awaited_once_with(client.cookie_state)


async def test_shutdown_heartbeat_failure_does_not_escape(caplog) -> None:
    client = SimpleNamespace(
        async_heartbeat=AsyncMock(side_effect=EnergyKeyConnectionError("offline")),
        cookie_state={},
    )
    runtime = _runtime(SimpleNamespace(), initial_refresh=_completed_future())
    runtime.client = client
    entry = SimpleNamespace(runtime_data=runtime)

    with caplog.at_level("DEBUG", logger="custom_components.energykey"):
        await _async_shutdown_heartbeat(Mock(), entry)

    assert "Could not send EnergyKey shutdown heartbeat" in caplog.text
