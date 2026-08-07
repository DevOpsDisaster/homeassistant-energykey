"""Tests for EnergyKey input validation and response parsing."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import pytest
from aiohttp import CookieJar

from custom_components.energykey.api import (
    EnergyKeyAuthError,
    EnergyKeyClient,
    EnergyKeyConnectionError,
    EnergyKeyProtocolError,
    EnergyKeyRateLimitError,
    EnergyKeyStaleResourceError,
    _extract_meter_ids,
    _extract_meters,
    _find_nested_value,
    _parse_consumption,
    _parse_retry_after,
    _parse_views,
    account_unique_id,
    normalize_base_url,
    opaque_meter_key,
    parse_cookie_header,
    session_metadata,
)
from custom_components.energykey.const import (
    MAX_COOKIE_COUNT,
    MAX_COOKIE_HEADER_LENGTH,
    MAX_COOKIE_VALUE_LENGTH,
    MAX_ERROR_BODY_LOG_BYTES,
    MAX_LOGIN_METADATA_DEPTH,
    MAX_RESPONSE_BODY_BYTES,
)
from custom_components.energykey.models import (
    ConsumptionView,
    EnergyKeyMeter,
    MeterKind,
)


class _FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._offset = 0

    async def read(self, limit: int) -> bytes:
        chunk = self._body[self._offset : self._offset + limit]
        self._offset += len(chunk)
        return chunk


class _FakeResponse:
    def __init__(
        self,
        status: int,
        body: str | bytes = "",
        *,
        headers: dict[str, str] | None = None,
        charset: str | None = "utf-8",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.charset = charset
        self.content = _FakeContent(body.encode() if isinstance(body, str) else body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


def _request_client(response: _FakeResponse) -> tuple[EnergyKeyClient, AsyncMock]:
    login = quote('{"id":"synthetic-user","languageId":"da-DK"}')
    request = AsyncMock(return_value=response)
    session = SimpleNamespace(cookie_jar=CookieJar(), request=request)
    return (
        EnergyKeyClient(
            session,
            "https://tenant.wt.energykey.dk",
            {"wt3SessionId": "session", "wt3login": login},
        ),
        request,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://dinforsyning.wt.energykey.dk",
            "https://dinforsyning.wt.energykey.dk",
        ),
        (
            "HTTPS://TENANT.WT.ENERGYKEY.DK/",
            "https://tenant.wt.energykey.dk",
        ),
        ("https://tenant.wt.energykey.dk:443", "https://tenant.wt.energykey.dk"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://tenant.wt.energykey.dk",
        "https://wt.energykey.dk",
        "https://energykey.dk",
        "https://user:secret@tenant.wt.energykey.dk",
        "https://tenant.wt.energykey.dk/path",
        "https://tenant.wt.energykey.dk?query=yes",
        "https://tenant.wt.energykey.dk#fragment",
        "https://tenant.wt.energykey.dk:8443",
        "https://tenant.wt.energykey.dk:invalid",
        "https://tenant.wt.energykey.dk\n.evil.example",
    ],
)
def test_rejects_unsafe_base_url(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_base_url(raw)


def test_parse_cookie_header_and_complete_line() -> None:
    expected = {"wt3SessionId": "session", "wt3login": "login", "rotated": "1"}
    raw = "wt3SessionId=session; wt3login=login; rotated=1"
    assert parse_cookie_header(raw) == expected
    assert parse_cookie_header(f"Cookie: {raw}") == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "wt3SessionId=session",
        "wt3SessionId=; wt3login=login",
        "wt3SessionId=session; wt3login=",
        "wt3SessionId=first; wt3SessionId=second; wt3login=login",
        "wt3SessionId=session; malformed; wt3login=login",
        "wt3SessionId=session; wt3login=login\r\nX-Injected: yes",
    ],
)
def test_rejects_invalid_cookie_headers(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_cookie_header(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "x" * (MAX_COOKIE_HEADER_LENGTH + 1),
        f"wt3SessionId=session; wt3login={'x' * (MAX_COOKIE_VALUE_LENGTH + 1)}",
        "wt3SessionId=session; wt3login=login; "
        + "; ".join(f"cookie{index}=value" for index in range(MAX_COOKIE_COUNT)),
    ],
)
def test_rejects_unreasonably_large_cookie_input(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_cookie_header(raw)


def test_session_metadata_and_opaque_ids() -> None:
    login = quote(
        '{"id":"synthetic-user","customerDatabaseNumber":"synthetic-db",'
        '"contextId":"ctx","languageId":"en"}'
    )
    metadata = session_metadata({"wt3SessionId": "session", "wt3login": login})
    assert metadata.account_identifier == "synthetic-db"
    assert metadata.customer_database_number == "synthetic-db"
    assert metadata.context_id == "ctx"
    assert metadata.language_id == "en"

    account = account_unique_id(
        "https://tenant.wt.energykey.dk", metadata.account_identifier
    )
    assert len(account) == 64
    assert "synthetic" not in account
    assert account == account_unique_id(
        "https://TENANT.wt.energykey.dk/", metadata.account_identifier
    )
    assert len(opaque_meter_key("synthetic-meter")) == 64


def test_user_id_is_account_identifier_without_customer_database_cookie() -> None:
    login = quote('{"id":"synthetic-user","languageId":"da-DK"}')
    metadata = session_metadata({"wt3SessionId": "session", "wt3login": login})

    assert metadata.account_identifier == "synthetic-user"
    assert metadata.customer_database_number is None
    assert metadata.language_id == "da-DK"


async def test_customer_database_header_is_optional_during_cookie_bootstrap() -> None:
    login = quote('{"id":"synthetic-user","languageId":"da-DK"}')
    session = SimpleNamespace(cookie_jar=CookieJar())
    client = EnergyKeyClient(
        session,
        "https://tenant.wt.energykey.dk",
        {"wt3SessionId": "session", "wt3login": login},
    )

    headers = client._headers()

    assert headers["session-id"] == "session"
    assert headers["language-id"] == "da-DK"
    assert "customer-database-number" not in headers


async def test_auth_rejection_prevents_subsequent_network_requests() -> None:
    """A definitive rejection remains latched until a new client is created."""
    login = quote('{"id":"synthetic-user","languageId":"da-DK"}')
    response = AsyncMock()
    response.status = 403
    response.headers = {}
    response.__aenter__.return_value = response
    response.__aexit__.return_value = None
    session = SimpleNamespace(
        cookie_jar=CookieJar(),
        request=AsyncMock(return_value=response),
    )
    client = EnergyKeyClient(
        session,
        "https://tenant.wt.energykey.dk",
        {"wt3SessionId": "session", "wt3login": login},
    )

    with pytest.raises(EnergyKeyAuthError):
        await client.async_heartbeat()
    with pytest.raises(EnergyKeyAuthError):
        await client.async_get_meters()

    assert client.authentication_rejected is True
    session.request.assert_awaited_once()


async def test_successful_json_response_records_authenticated_activity() -> None:
    client, request = _request_client(
        _FakeResponse(200, "true", headers={"Content-Type": "application/json"})
    )

    await client.async_heartbeat()

    assert client.seconds_since_activity < 1
    request.assert_awaited_once()


async def test_rate_limit_exposes_bounded_retry_hint() -> None:
    client, _ = _request_client(_FakeResponse(429, headers={"Retry-After": "99999"}))

    with pytest.raises(EnergyKeyRateLimitError) as raised:
        await client.async_heartbeat()

    assert raised.value.retry_after == 3600


async def test_server_error_is_retried_and_recovers() -> None:
    client, request = _request_client(_FakeResponse(500))
    request.side_effect = [
        _FakeResponse(500),
        _FakeResponse(200, "true", headers={"Content-Type": "application/json"}),
    ]

    with patch("custom_components.energykey.api.asyncio.sleep", AsyncMock()) as sleep:
        await client.async_heartbeat()

    assert request.await_count == 2
    sleep.assert_awaited_once_with(1.0)


async def test_server_error_reports_endpoint_after_retries() -> None:
    client, request = _request_client(_FakeResponse(500))

    with (
        patch("custom_components.energykey.api.asyncio.sleep", AsyncMock()) as sleep,
        pytest.raises(
            EnergyKeyConnectionError,
            match=r"HTTP 500 for GET /wts/heartbeat after 3 attempts",
        ),
    ):
        await client.async_heartbeat()

    assert request.await_count == 3
    assert sleep.await_count == 2


async def test_error_1101_marks_consumption_resource_stale_without_reauth() -> None:
    client, request = _request_client(
        _FakeResponse(
            500,
            '{"errorCode":1101,"errorMsg":"Could not get item.","metadata":{}}',
        )
    )
    meter = EnergyKeyMeter(item_id="meter", key="meter-key")
    view = ConsumptionView("view", "usage", "kWh", "kWh", "month_by_days")

    with pytest.raises(EnergyKeyStaleResourceError, match="item or view"):
        await client.async_get_consumption(
            meter,
            view,
            datetime(2026, 8, 1, tzinfo=UTC),
            datetime(2026, 8, 2, tzinfo=UTC),
        )

    assert client.authentication_rejected is False
    request.assert_awaited_once()


async def test_server_error_body_is_logged_at_debug_and_bounded(caplog) -> None:
    body = "useful detail\n" + "x" * MAX_ERROR_BODY_LOG_BYTES
    client, _ = _request_client(_FakeResponse(500, body))

    with (
        patch("custom_components.energykey.api.asyncio.sleep", AsyncMock()),
        caplog.at_level("DEBUG", logger="custom_components.energykey.api"),
        pytest.raises(EnergyKeyConnectionError),
    ):
        await client.async_heartbeat()

    assert "useful detail\\n" in caplog.text
    assert "<truncated>" in caplog.text
    assert body not in caplog.text


async def test_server_error_body_with_unknown_charset_still_retries(caplog) -> None:
    client, request = _request_client(
        _FakeResponse(500, "useful detail", charset="unknown-charset")
    )

    with (
        patch("custom_components.energykey.api.asyncio.sleep", AsyncMock()),
        caplog.at_level("DEBUG", logger="custom_components.energykey.api"),
        pytest.raises(EnergyKeyConnectionError),
    ):
        await client.async_heartbeat()

    assert request.await_count == 3
    assert "useful detail" in caplog.text


@pytest.mark.parametrize("value", [None, "invalid", "-1", "nan"])
def test_invalid_retry_after_is_ignored(value: str | None) -> None:
    assert _parse_retry_after(value) is None


def test_http_date_retry_after_is_supported() -> None:
    assert _parse_retry_after("Tue, 04 Aug 2026 16:00:00 GMT") is not None


async def test_login_html_is_authentication_failure_even_with_plain_content_type() -> (
    None
):
    client, _ = _request_client(
        _FakeResponse(
            200,
            "<!doctype html><html><body>MitID login</body></html>",
            headers={"Content-Type": "text/plain"},
        )
    )

    with pytest.raises(EnergyKeyAuthError):
        await client.async_heartbeat()

    assert client.authentication_rejected is True


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            _FakeResponse(302, headers={"Location": "https://evil.example/login"}),
            EnergyKeyProtocolError,
        ),
        (
            _FakeResponse(
                302,
                headers={"Location": "/login"},
            ),
            EnergyKeyAuthError,
        ),
        (_FakeResponse(500), EnergyKeyConnectionError),
        (
            _FakeResponse(
                200,
                "maintenance",
                headers={"Content-Type": "text/plain"},
            ),
            EnergyKeyProtocolError,
        ),
        (
            _FakeResponse(
                200,
                "{",
                headers={"Content-Type": "application/json"},
            ),
            EnergyKeyProtocolError,
        ),
        (
            _FakeResponse(
                200,
                b"\xff",
                headers={"Content-Type": "application/json"},
            ),
            EnergyKeyProtocolError,
        ),
    ],
)
async def test_request_failures_are_safely_classified(
    response: _FakeResponse, error: type[Exception]
) -> None:
    client, _ = _request_client(response)

    with pytest.raises(error):
        await client.async_heartbeat()


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(
            200,
            "{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(MAX_RESPONSE_BODY_BYTES + 1),
            },
        ),
        _FakeResponse(
            200,
            "x" * (MAX_RESPONSE_BODY_BYTES + 1),
            headers={"Content-Type": "application/json"},
        ),
    ],
)
async def test_oversized_response_is_rejected(response: _FakeResponse) -> None:
    client, _ = _request_client(response)

    with pytest.raises(EnergyKeyProtocolError, match="oversized"):
        await client.async_heartbeat()


def test_nested_login_metadata_search_has_a_depth_limit() -> None:
    nested: object = {"customerDatabaseNumber": "too-deep"}
    for _ in range(MAX_LOGIN_METADATA_DEPTH + 1):
        nested = {"nested": nested}

    assert _find_nested_value(nested, {"customerdatabasenumber"}) is None


def test_missing_account_metadata_is_protocol_error() -> None:
    with pytest.raises(EnergyKeyProtocolError):
        session_metadata({"wt3SessionId": "session", "wt3login": "{}"})


def test_meter_and_view_discovery(load_fixture) -> None:
    payload = load_fixture("item_groups.json")
    meter_ids = _extract_meter_ids(payload)
    assert meter_ids == {"synthetic-water-meter", "synthetic-heat-meter"}
    meters = _extract_meters(payload)
    water = next(meter for meter in meters if meter.kind_hint is MeterKind.WATER)
    heat = next(meter for meter in meters if meter.kind_hint is MeterKind.ENERGY)
    assert water.portal_name == "Garden meter"
    assert water.meter_number == "12345678"
    assert heat.portal_name is None
    assert heat.meter_number == "87654321"

    views = _parse_views(load_fixture("water_views.json"))
    assert len(views) == 1
    assert views[0].view_id == "synthetic-water-view"
    assert views[0].zoom_level == "month_by_days"
    assert views[0].unit_id == "cubic_meter"


def test_invalid_view_schema_is_rejected() -> None:
    with pytest.raises(EnergyKeyProtocolError):
        _parse_views({"unexpected": []})


def test_consumption_model_preserves_incomplete_period(load_fixture) -> None:
    view = ConsumptionView(
        view_id="redacted-view",
        name_type="usageConsumption",
        unit_id="cubic_meter",
        unit_name="m3",
        zoom_level="month_by_days",
    )
    result = _parse_consumption(load_fixture("water_consumption.json"), view)
    assert result.unit_id == "cubic_meter"
    assert result.usage_series is not None
    assert len(result.usage_series.points) == 3
    assert result.usage_series.points[-1].complete is False
    assert result.usage_series.points[-1].counter_value is None


def test_ambiguous_series_is_not_guessed(load_fixture) -> None:
    view = ConsumptionView(
        view_id="redacted-view",
        name_type="usageConsumption",
        unit_id="kWh",
        unit_name="kWh",
        zoom_level="month_by_days",
    )
    payload = load_fixture("heat_consumption.json")
    payload["series"].append(dict(payload["series"][0]))
    result = _parse_consumption(payload, view)
    assert result.usage_series is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"series": "not-a-list"},
        {"series": []},
        {"series": [{"key": "usage", "serieData": {"datapoints": []}}]},
    ],
)
def test_unknown_consumption_schema_is_rejected(payload: object) -> None:
    view = ConsumptionView("view", "usage", "kWh", "kWh", "month_by_days")
    with pytest.raises(EnergyKeyProtocolError):
        _parse_consumption(payload, view)
