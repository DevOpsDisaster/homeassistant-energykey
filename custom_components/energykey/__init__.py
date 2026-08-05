"""The EnergyKey integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from aiohttp import CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
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
    normalize_base_url,
)
from .const import (
    CONF_BASE_URL,
    CONF_COOKIES,
    DOMAIN,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_RETRY_INTERVAL,
    REQUIRED_COOKIES,
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


def _valid_cookies(value: object) -> dict[str, str] | None:
    """Return cookies only when the authenticated session requirements are met."""
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) and item
        for key, item in value.items()
    ):
        return None
    cookies = dict(value)
    return cookies if REQUIRED_COOKIES.issubset(cookies) else None


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate legacy config entries while preserving their identity."""
    if entry.version > 2:
        return False
    if entry.version == 2:
        return True

    data = dict(entry.data)
    try:
        data[CONF_BASE_URL] = normalize_base_url(str(data[CONF_BASE_URL]))
    except (KeyError, ValueError):
        return False

    cookies = _valid_cookies(data.pop(CONF_COOKIES, None))
    if cookies is not None:
        store = EnergyKeyStore(hass, entry.entry_id)
        await store.async_load()
        await store.async_set_cookies(cookies)
    else:
        return False

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        version=2,
        minor_version=1,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EnergyKeyConfigEntry) -> bool:
    """Set up EnergyKey from a config entry."""
    store = EnergyKeyStore(hass, entry.entry_id)
    await store.async_load()
    cookies = _valid_cookies(store.cookies) or _valid_cookies(
        entry.data.get(CONF_COOKIES)
    )
    if cookies is None:
        raise ConfigEntryNotReady("EnergyKey session state is missing")
    if CONF_COOKIES in entry.data:
        await store.async_set_cookies(cookies)
        data = dict(entry.data)
        data.pop(CONF_COOKIES)
        hass.config_entries.async_update_entry(entry, data=data)

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
        await coordinator.async_prepare()
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
    runtime.heartbeat_task = entry.async_create_background_task(
        hass,
        _async_heartbeat_loop(hass, entry),
        "EnergyKey session heartbeat",
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
    if runtime.heartbeat_task is not None:
        runtime.heartbeat_task.cancel()
        try:
            await runtime.heartbeat_task
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


async def _async_heartbeat_loop(
    hass: HomeAssistant, entry: EnergyKeyConfigEntry
) -> None:
    """Keep the cookie session alive independently of entity polling."""
    runtime = entry.runtime_data
    normal_wait = HEARTBEAT_INTERVAL.total_seconds()
    retry_wait = HEARTBEAT_RETRY_INTERVAL.total_seconds()

    while True:
        wait_seconds = max(1.0, normal_wait - runtime.client.seconds_since_activity)
        await asyncio.sleep(wait_seconds)
        runtime.heartbeat_attempts += 1
        try:
            await runtime.client.async_heartbeat()
            await runtime.store.async_set_cookies(runtime.client.cookie_state)
        except EnergyKeyAuthError:
            runtime.consecutive_heartbeat_failures += 1
            runtime.last_heartbeat_error = "authentication"
            if not runtime.reauth_started:
                runtime.reauth_started = True
                entry.async_start_reauth(hass)
            return
        except EnergyKeyRateLimitError as err:
            runtime.consecutive_heartbeat_failures += 1
            runtime.last_heartbeat_error = "rate_limited"
            server_wait = err.retry_after or retry_wait
            await asyncio.sleep(max(retry_wait, min(server_wait, normal_wait)))
            continue
        except (EnergyKeyConnectionError, EnergyKeyProtocolError):
            runtime.consecutive_heartbeat_failures += 1
            runtime.last_heartbeat_error = "connection"
            await asyncio.sleep(retry_wait)
            continue
        runtime.last_successful_heartbeat = datetime.now(UTC)
        runtime.last_heartbeat_error = None
        runtime.consecutive_heartbeat_failures = 0
        async_dispatcher_send(hass, heartbeat_update_signal(entry.entry_id))
