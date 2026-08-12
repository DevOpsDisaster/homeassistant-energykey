"""The EnergyKey integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from functools import partial

from aiohttp import CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import (
    EnergyKeyAuthError,
    EnergyKeyClient,
    EnergyKeyConnectionError,
    EnergyKeyProtocolError,
    EnergyKeyRateLimitError,
    EnergyKeyStaleResourceError,
)
from .const import (
    CONF_BASE_URL,
    CONF_COOKIES,
    CONSUMPTION_KEEPALIVE_RETRY_INTERVAL,
    DOMAIN,
    SHUTDOWN_HEARTBEAT_TIMEOUT_SECONDS,
    heartbeat_update_signal,
)
from .coordinator import EnergyKeyCoordinator
from .models import MeterKind
from .runtime import EnergyKeyRuntimeData
from .statistics import async_clear_statistics_for_history_keys
from .storage import EnergyKeyStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type EnergyKeyConfigEntry = ConfigEntry[EnergyKeyRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: EnergyKeyConfigEntry) -> bool:
    """Set up EnergyKey from a config entry."""
    store = EnergyKeyStore(hass, entry.entry_id)
    await store.async_load()
    cookies = store.cookies or dict(entry.data[CONF_COOKIES])

    session = async_create_clientsession(hass, cookie_jar=CookieJar())
    client = EnergyKeyClient(session, entry.data[CONF_BASE_URL], cookies)
    coordinator = EnergyKeyCoordinator(hass, entry, client, store)
    runtime = EnergyKeyRuntimeData(
        client=client,
        coordinator=coordinator,
        store=store,
    )
    entry.runtime_data = runtime

    try:
        # Touch the server before discovery so a session with a short idle timeout
        # is renewed as soon as Home Assistant starts the config entry.
        await client.async_heartbeat()
        await store.async_set_cookies(client.cookie_state)
        runtime.last_successful_heartbeat = datetime.now(UTC)
        await coordinator.async_prepare()
    except EnergyKeyAuthError as err:
        raise ConfigEntryAuthFailed("The EnergyKey session has expired") from err
    except (
        EnergyKeyConnectionError,
        EnergyKeyProtocolError,
        EnergyKeyRateLimitError,
    ) as err:
        raise ConfigEntryNotReady(str(err)) from err
    except UpdateFailed as err:
        raise ConfigEntryNotReady(str(err)) from err

    device_registry = dr.async_get(hass)
    for meter in coordinator.discovered_meters:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, meter.meter_key)},
            name=meter.device_name,
            manufacturer="EnergyKey",
            model=("Heat meter" if meter.kind is MeterKind.ENERGY else "Water meter"),
            serial_number=meter.meter_number,
            configuration_url=str(entry.data[CONF_BASE_URL]),
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    runtime.data_refresh_task = entry.async_create_background_task(
        hass,
        _async_initial_data_refresh(entry),
        "EnergyKey initial data refresh",
    )
    runtime.consumption_keepalive_task = entry.async_create_background_task(
        hass,
        _async_consumption_keepalive_loop(hass, entry),
        "EnergyKey consumption keepalive",
    )
    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            partial(_async_shutdown_heartbeat, entry=entry),
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyKeyConfigEntry) -> bool:
    """Unload an EnergyKey config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    runtime = entry.runtime_data
    if runtime.data_refresh_task is not None:
        runtime.data_refresh_task.cancel()
        try:
            await runtime.data_refresh_task
        except asyncio.CancelledError:
            pass
    if runtime.consumption_keepalive_task is not None:
        runtime.consumption_keepalive_task.cancel()
        try:
            await runtime.consumption_keepalive_task
        except asyncio.CancelledError:
            pass
    await runtime.store.async_set_cookies(runtime.client.cookie_state)
    runtime.client.detach()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted EnergyKey state when a config entry is deleted."""
    store = EnergyKeyStore(hass, entry.entry_id)
    await store.async_load()
    async_clear_statistics_for_history_keys(hass, store.history_keys)
    await store.async_remove()


async def _async_initial_data_refresh(entry: EnergyKeyConfigEntry) -> None:
    """Fetch current and historical data after config-entry setup returns."""
    await asyncio.sleep(0)
    await entry.runtime_data.coordinator.async_refresh()


async def _async_shutdown_heartbeat(_event: Event, entry: EnergyKeyConfigEntry) -> None:
    """Best-effort session renewal during a graceful Home Assistant shutdown."""
    runtime = entry.runtime_data
    try:
        async with asyncio.timeout(SHUTDOWN_HEARTBEAT_TIMEOUT_SECONDS):
            await runtime.client.async_heartbeat()
            await runtime.store.async_set_cookies(runtime.client.cookie_state)
    except Exception:  # noqa: BLE001 - shutdown must not be blocked by this request
        _LOGGER.debug("Could not send EnergyKey shutdown heartbeat", exc_info=True)


async def _async_consumption_keepalive_loop(
    hass: HomeAssistant, entry: EnergyKeyConfigEntry
) -> None:
    """Keep each discovered consumption item active between full refreshes."""
    runtime = entry.runtime_data
    if runtime.data_refresh_task is not None:
        await runtime.data_refresh_task

    while not runtime.reauth_started:
        delay = runtime.coordinator.seconds_until_consumption_keepalive()
        if delay > 0:
            await asyncio.sleep(delay)

        # A full coordinator refresh may have completed while this task slept.
        # Recheck before probing so the refresh and keepalive cannot race.
        if runtime.coordinator.seconds_until_consumption_keepalive() > 0:
            continue

        try:
            probes_sent = await runtime.coordinator.async_run_consumption_keepalive()
            if not probes_sent:
                continue
        except EnergyKeyStaleResourceError:
            runtime.last_heartbeat_error = "stale_resource"
            _start_reauth_once(hass, entry)
            return
        except EnergyKeyAuthError:
            runtime.last_heartbeat_error = "authentication"
            _start_reauth_once(hass, entry)
            return
        except EnergyKeyRateLimitError as err:
            runtime.last_heartbeat_error = "rate_limited"
            retry_after = (
                err.retry_after or CONSUMPTION_KEEPALIVE_RETRY_INTERVAL.total_seconds()
            )
            await asyncio.sleep(
                max(
                    CONSUMPTION_KEEPALIVE_RETRY_INTERVAL.total_seconds(),
                    retry_after,
                )
            )
            continue
        except EnergyKeyProtocolError:
            runtime.last_heartbeat_error = "protocol"
            await asyncio.sleep(CONSUMPTION_KEEPALIVE_RETRY_INTERVAL.total_seconds())
            continue
        except EnergyKeyConnectionError:
            runtime.last_heartbeat_error = "connection"
            await asyncio.sleep(CONSUMPTION_KEEPALIVE_RETRY_INTERVAL.total_seconds())
            continue
        runtime.last_successful_heartbeat = datetime.now(UTC)
        runtime.last_heartbeat_error = None
        async_dispatcher_send(hass, heartbeat_update_signal(entry.entry_id))


def _start_reauth_once(hass: HomeAssistant, entry: EnergyKeyConfigEntry) -> None:
    """Start one reauthentication flow for a rejected consumption session."""
    runtime = entry.runtime_data
    if runtime.reauth_started:
        return
    runtime.reauth_started = True
    entry.async_start_reauth(hass)
