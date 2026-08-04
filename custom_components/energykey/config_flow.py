"""Config and reauthentication flows for EnergyKey."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from aiohttp import CookieJar
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    EnergyKeyAuthError,
    EnergyKeyClient,
    EnergyKeyConnectionError,
    EnergyKeyProtocolError,
    account_unique_id,
    normalize_base_url,
    parse_cookie_header,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_BASE_URL,
    CONF_COOKIES,
    DEFAULT_BASE_URL,
    DOMAIN,
)
from .storage import EnergyKeyStore

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ValidatedInput:
    """Normalized config-flow input."""

    base_url: str
    cookies: dict[str, str]
    account_id: str


def _user_schema(default_url: str = DEFAULT_BASE_URL) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_BASE_URL, default=default_url): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Required(CONF_COOKIES): TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.PASSWORD,
                    autocomplete="off",
                )
            ),
        }
    )


def _reauth_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_COOKIES): TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.PASSWORD,
                    autocomplete="off",
                )
            )
        }
    )


async def async_validate_input(
    hass: HomeAssistant, base_url: str, raw_cookies: str
) -> ValidatedInput:
    """Validate an EnergyKey URL and cookie session without retaining it."""
    normalized_url = normalize_base_url(base_url)
    cookies = parse_cookie_header(raw_cookies)
    session = async_create_clientsession(
        hass,
        cookie_jar=CookieJar(),
        auto_cleanup=False,
    )
    try:
        client = EnergyKeyClient(session, normalized_url, cookies)
        await client.async_validate_session()
        return ValidatedInput(
            base_url=normalized_url,
            cookies=client.cookie_state,
            account_id=account_unique_id(
                normalized_url, client.metadata.account_identifier
            ),
        )
    finally:
        session.detach()


class EnergyKeyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle EnergyKey setup and reauthentication."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a new EnergyKey account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                validated = await async_validate_input(
                    self.hass,
                    user_input[CONF_BASE_URL],
                    user_input[CONF_COOKIES],
                )
            except ValueError as err:
                errors["base"] = (
                    "invalid_url"
                    if "portal" in str(err).casefold()
                    else "invalid_cookie"
                )
            except EnergyKeyAuthError:
                errors["base"] = "invalid_auth"
            except EnergyKeyConnectionError:
                errors["base"] = "cannot_connect"
            except EnergyKeyProtocolError:
                _LOGGER.warning(
                    "EnergyKey validation failed because the portal response "
                    "was unsupported"
                )
                errors["base"] = "invalid_response"
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "Unexpected exception while validating EnergyKey (%s)",
                    type(err).__name__,
                )
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(validated.account_id)
                self._abort_if_unique_id_configured()
                hostname = urlsplit(validated.base_url).hostname or "EnergyKey"
                return self.async_create_entry(
                    title=f"EnergyKey ({hostname})",
                    data={
                        CONF_BASE_URL: validated.base_url,
                        CONF_COOKIES: validated.cookies,
                        CONF_ACCOUNT_ID: validated.account_id,
                    },
                )

        default_url = (
            str(user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL))
            if user_input
            else DEFAULT_BASE_URL
        )
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(default_url),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an expired cookie session."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement cookies and reload the existing entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                validated = await async_validate_input(
                    self.hass,
                    str(entry.data[CONF_BASE_URL]),
                    user_input[CONF_COOKIES],
                )
            except ValueError:
                errors["base"] = "invalid_cookie"
            except EnergyKeyAuthError:
                errors["base"] = "invalid_auth"
            except EnergyKeyConnectionError:
                errors["base"] = "cannot_connect"
            except EnergyKeyProtocolError:
                _LOGGER.warning(
                    "EnergyKey reauthentication failed because the portal "
                    "response was unsupported"
                )
                errors["base"] = "invalid_response"
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "Unexpected exception while reauthenticating EnergyKey (%s)",
                    type(err).__name__,
                )
                errors["base"] = "unknown"
            else:
                if validated.account_id != entry.unique_id:
                    errors["base"] = "wrong_account"
                else:
                    store = EnergyKeyStore(self.hass, entry.entry_id)
                    await store.async_load()
                    await store.async_set_cookies(validated.cookies)
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_COOKIES: validated.cookies,
                            CONF_ACCOUNT_ID: validated.account_id,
                        },
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),
            errors=errors,
            description_placeholders={"name": entry.title},
        )
