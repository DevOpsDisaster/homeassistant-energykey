"""Tests for the EnergyKey setup and reauthentication flows."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energykey.api import (
    EnergyKeyAuthError,
    EnergyKeyConnectionError,
    EnergyKeyProtocolError,
)
from custom_components.energykey.config_flow import ValidatedInput, async_validate_input
from custom_components.energykey.const import (
    CONF_ACCOUNT_ID,
    CONF_BASE_URL,
    CONF_COOKIES,
    DOMAIN,
)

BASE_URL = "https://tenant.wt.energykey.dk"
ACCOUNT_ID = "a" * 64
COOKIES = {"wt3SessionId": "synthetic-session", "wt3login": "synthetic-login"}
RAW_COOKIES = "wt3SessionId=synthetic-session; wt3login=synthetic-login"
VALIDATED = ValidatedInput(BASE_URL, COOKIES, ACCOUNT_ID)


async def test_validate_input_detaches_session_and_uses_rotated_cookies(hass) -> None:
    session = Mock()
    client = Mock(
        async_validate_session=AsyncMock(),
        cookie_state={"wt3SessionId": "rotated", "wt3login": "rotated-login"},
        metadata=SimpleNamespace(account_identifier="synthetic-account"),
    )
    with (
        patch(
            "custom_components.energykey.config_flow.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.energykey.config_flow.EnergyKeyClient",
            return_value=client,
        ),
    ):
        result = await async_validate_input(hass, BASE_URL, RAW_COOKIES)

    client.async_validate_session.assert_awaited_once()
    session.detach.assert_called_once()
    assert result.base_url == BASE_URL
    assert result.cookies == client.cookie_state
    assert len(result.account_id) == 64


async def test_user_flow_success(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM

    with patch(
        "custom_components.energykey.config_flow.async_validate_input",
        AsyncMock(return_value=VALIDATED),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BASE_URL: BASE_URL, CONF_COOKIES: RAW_COOKIES},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "EnergyKey (tenant.wt.energykey.dk)"
    assert result["data"] == {
        CONF_BASE_URL: BASE_URL,
        CONF_COOKIES: COOKIES,
        CONF_ACCOUNT_ID: ACCOUNT_ID,
    }
    assert result["result"].unique_id == ACCOUNT_ID


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError("The Cookie header is invalid"), "invalid_cookie"),
        (ValueError("The portal URL is invalid"), "invalid_url"),
        (EnergyKeyAuthError(), "invalid_auth"),
        (EnergyKeyConnectionError(), "cannot_connect"),
        (
            EnergyKeyProtocolError("EnergyKey returned malformed JSON"),
            "invalid_response",
        ),
    ],
)
async def test_user_flow_errors(hass, error: Exception, expected: str) -> None:
    with patch(
        "custom_components.energykey.config_flow.async_validate_input",
        AsyncMock(side_effect=error),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_BASE_URL: BASE_URL, CONF_COOKIES: RAW_COOKIES},
        )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": expected}


@pytest.mark.parametrize(
    "error",
    [
        EnergyKeyProtocolError("private-cookie-value"),
        RuntimeError("private-cookie-value"),
    ],
)
async def test_user_flow_never_logs_exception_messages(
    hass, caplog, error: Exception
) -> None:
    caplog.set_level(logging.DEBUG)
    with patch(
        "custom_components.energykey.config_flow.async_validate_input",
        AsyncMock(side_effect=error),
    ):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_BASE_URL: BASE_URL, CONF_COOKIES: RAW_COOKIES},
        )

    assert "private-cookie-value" not in caplog.text


async def test_duplicate_account_is_rejected(hass) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=ACCOUNT_ID, data={}).add_to_hass(hass)
    with patch(
        "custom_components.energykey.config_flow.async_validate_input",
        AsyncMock(return_value=VALIDATED),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_BASE_URL: BASE_URL, CONF_COOKIES: RAW_COOKIES},
        )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_rejects_different_account(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_ID,
        title="EnergyKey test",
        data={
            CONF_BASE_URL: BASE_URL,
            CONF_COOKIES: COOKIES,
            CONF_ACCOUNT_ID: ACCOUNT_ID,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=dict(entry.data),
    )

    other = ValidatedInput(BASE_URL, COOKIES, "b" * 64)
    with patch(
        "custom_components.energykey.config_flow.async_validate_input",
        AsyncMock(return_value=other),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COOKIES: RAW_COOKIES}
        )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_account"}


async def test_reauth_updates_existing_entry(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_ID,
        title="EnergyKey test",
        data={
            CONF_BASE_URL: BASE_URL,
            CONF_COOKIES: COOKIES,
            CONF_ACCOUNT_ID: ACCOUNT_ID,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=dict(entry.data),
    )
    rotated = {"wt3SessionId": "rotated", "wt3login": "rotated-login"}
    validated = ValidatedInput(BASE_URL, rotated, ACCOUNT_ID)
    with (
        patch(
            "custom_components.energykey.config_flow.async_validate_input",
            AsyncMock(return_value=validated),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COOKIES: RAW_COOKIES}
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_COOKIES] == rotated


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError(), "invalid_cookie"),
        (EnergyKeyAuthError(), "invalid_auth"),
        (EnergyKeyConnectionError(), "cannot_connect"),
        (EnergyKeyProtocolError(), "invalid_response"),
        (RuntimeError(), "unknown"),
    ],
)
async def test_reauth_errors(hass, error: Exception, expected: str) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_ID,
        title="EnergyKey test",
        data={
            CONF_BASE_URL: BASE_URL,
            CONF_COOKIES: COOKIES,
            CONF_ACCOUNT_ID: ACCOUNT_ID,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=dict(entry.data),
    )
    with patch(
        "custom_components.energykey.config_flow.async_validate_input",
        AsyncMock(side_effect=error),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COOKIES: RAW_COOKIES}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": expected}
