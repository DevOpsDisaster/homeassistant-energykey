"""Asynchronous client for EnergyKey's private read-only endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any, Never
from urllib.parse import quote, unquote, urlsplit

from aiohttp import ClientError, ClientSession
from yarl import URL

from .const import (
    ENERGYKEY_HOST_SUFFIX,
    ITEM_CATEGORY,
    MAX_COOKIE_COUNT,
    MAX_COOKIE_HEADER_LENGTH,
    MAX_COOKIE_VALUE_LENGTH,
    MAX_ERROR_BODY_LOG_BYTES,
    MAX_LOGIN_METADATA_DEPTH,
    MAX_RESPONSE_BODY_BYTES,
    MAX_RETRY_AFTER_SECONDS,
    REQUEST_RETRY_ATTEMPTS,
    REQUEST_RETRY_BASE_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    REQUIRED_COOKIES,
    ZOOM_LEVEL_DAY_CANDIDATE,
)
from .models import (
    ConsumptionPoint,
    ConsumptionResult,
    ConsumptionSeries,
    ConsumptionView,
    EnergyKeyMeter,
    MeterKind,
    SessionMetadata,
)

_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_EPOCH_WRAPPER = re.compile(r"^/Date\((?P<value>-?\d+)(?:[+-]\d{4})?\)/$")
_LOGIN_MARKERS = ("aliaslogintoken", "mitid", "nemlog-in", "login")

_LOGGER = logging.getLogger(__name__)

_CUSTOMER_KEYS = {
    "customerdatabasenumber",
    "customerdatabaseid",
    "customerdbnumber",
    "databasenumber",
}
_CONTEXT_KEYS = {"contextid"}
_LANGUAGE_KEYS = {"languageid"}


class EnergyKeyError(Exception):
    """Base exception for EnergyKey failures."""


class EnergyKeyAuthError(EnergyKeyError):
    """The EnergyKey session is missing, expired, or rejected."""


class EnergyKeyConnectionError(EnergyKeyError):
    """EnergyKey could not be reached."""


class EnergyKeyStaleResourceError(EnergyKeyConnectionError):
    """EnergyKey no longer recognizes a previously discovered item or view."""


class _EnergyKeyTransientError(EnergyKeyConnectionError):
    """A request failure which is safe to retry immediately."""


class EnergyKeyRateLimitError(EnergyKeyConnectionError):
    """EnergyKey temporarily rejected requests because of rate limiting."""

    def __init__(self, retry_after: float | None = None) -> None:
        """Initialize the error with a bounded server retry hint."""
        super().__init__("EnergyKey temporarily rate limited requests")
        self.retry_after = retry_after


class EnergyKeyProtocolError(EnergyKeyError):
    """EnergyKey returned an unsupported or malformed response."""


def normalize_base_url(raw_url: str) -> str:
    """Validate and normalize an EnergyKey tenant URL."""
    if not isinstance(raw_url, str) or any(
        ord(char) < 32 or ord(char) == 127 for char in raw_url
    ):
        raise ValueError("The portal URL contains invalid characters")

    value = raw_url.strip()
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https":
        raise ValueError("The portal URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("The portal URL must not contain credentials")
    try:
        port = parsed.port
    except ValueError as err:
        raise ValueError("The portal URL contains an invalid port") from err
    if port not in (None, 443):
        raise ValueError("The portal URL must use the default HTTPS port")
    if not _HOST.fullmatch(hostname) or not hostname.endswith(ENERGYKEY_HOST_SUFFIX):
        raise ValueError("The portal must be hosted below wt.energykey.dk")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("The portal URL must not contain a path, query, or fragment")
    return f"https://{hostname}"


def parse_cookie_header(raw_header: str) -> dict[str, str]:
    """Parse a strict Cookie request header without decoding its values."""
    if not isinstance(raw_header, str) or any(
        ord(char) < 32 or ord(char) == 127 for char in raw_header
    ):
        raise ValueError("The Cookie header contains invalid characters")

    if len(raw_header) > MAX_COOKIE_HEADER_LENGTH:
        raise ValueError("The Cookie header is too large")

    value = raw_header.strip()
    if value[:7].casefold() == "cookie:":
        value = value[7:].strip()
    if not value:
        raise ValueError("The Cookie header is empty")

    cookies: dict[str, str] = {}
    for part in value.split(";"):
        pair = part.strip()
        if not pair or "=" not in pair:
            raise ValueError("The Cookie header contains an invalid cookie pair")
        name, cookie_value = pair.split("=", 1)
        name = name.strip()
        cookie_value = cookie_value.strip()
        if not _COOKIE_NAME.fullmatch(name):
            raise ValueError("The Cookie header contains an invalid cookie name")
        if name in cookies:
            raise ValueError("The Cookie header contains a duplicate cookie")
        if len(cookie_value) > MAX_COOKIE_VALUE_LENGTH:
            raise ValueError("The Cookie header contains an oversized cookie")
        cookies[name] = cookie_value
        if len(cookies) > MAX_COOKIE_COUNT:
            raise ValueError("The Cookie header contains too many cookies")

    missing = REQUIRED_COOKIES.difference(cookies)
    if missing or any(not cookies[name] for name in REQUIRED_COOKIES):
        raise ValueError("The Cookie header is missing required EnergyKey cookies")
    return cookies


def account_unique_id(base_url: str, account_identifier: str) -> str:
    """Create a stable, opaque account identifier."""
    source = f"{normalize_base_url(base_url)}|{account_identifier}"
    return hashlib.sha256(source.encode()).hexdigest()


def opaque_meter_key(item_id: str) -> str:
    """Create an opaque meter key suitable for registries and storage."""
    return hashlib.sha256(item_id.encode()).hexdigest()


def session_metadata(cookies: Mapping[str, str]) -> SessionMetadata:
    """Extract required request metadata without exposing the login cookie."""
    folded = {key.casefold(): value for key, value in cookies.items()}
    login_data = _decode_login_cookie(cookies.get("wt3login", ""))

    customer = _first_non_empty(
        folded.get("wt3customerdatabasenumber"),
        _find_nested_value(login_data, _CUSTOMER_KEYS),
    )
    user_id = (
        _first_non_empty(
            login_data.get("id"),
            login_data.get("userId"),
            login_data.get("userID"),
        )
        if isinstance(login_data, dict)
        else None
    )
    account_identifier = _first_non_empty(customer, user_id)
    if account_identifier is None:
        raise EnergyKeyProtocolError(
            "The EnergyKey login cookie has no stable account identifier"
        )

    return SessionMetadata(
        account_identifier=account_identifier,
        customer_database_number=customer,
        context_id=_first_non_empty(
            folded.get("wt3contextid"),
            _find_nested_value(login_data, _CONTEXT_KEYS),
        ),
        language_id=_first_non_empty(
            folded.get("wt3languageid"),
            _find_nested_value(login_data, _LANGUAGE_KEYS),
        ),
    )


class EnergyKeyClient:
    """Read-only asynchronous EnergyKey API client."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        cookies: Mapping[str, str],
    ) -> None:
        """Initialize the client with a private cookie-enabled session."""
        self._session = session
        self.base_url = normalize_base_url(base_url)
        self._origin = URL(self.base_url).origin()
        self._request_lock = asyncio.Lock()
        self._last_activity = 0.0
        self._authentication_rejected = False
        self._metadata = session_metadata(cookies)
        self._session.cookie_jar.update_cookies(dict(cookies), URL(self.base_url))

    @property
    def metadata(self) -> SessionMetadata:
        """Return non-secret session metadata."""
        return self._metadata

    @property
    def seconds_since_activity(self) -> float:
        """Return seconds since the last authenticated response."""
        if self._last_activity == 0:
            return float("inf")
        return monotonic() - self._last_activity

    @property
    def cookie_state(self) -> dict[str, str]:
        """Return the current same-origin cookies for persistence."""
        return {
            morsel.key: morsel.value
            for morsel in self._session.cookie_jar
            if not morsel["domain"]
            or self._origin.host == morsel["domain"].lstrip(".")
            or self._origin.host.endswith(f".{morsel['domain'].lstrip('.')}")
        }

    def detach(self) -> None:
        """Detach the private session from Home Assistant's shared connector."""
        if not self._session.closed:
            self._session.detach()

    @property
    def authentication_rejected(self) -> bool:
        """Return whether the server definitively rejected this session."""
        return self._authentication_rejected

    async def async_validate_session(self) -> list[EnergyKeyMeter]:
        """Validate authentication and return the account's meters."""
        await self.async_heartbeat()
        meters = await self.async_get_meters()
        if not meters:
            raise EnergyKeyProtocolError("EnergyKey returned no meters")
        return meters

    async def async_heartbeat(self) -> None:
        """Validate and keep the current session active."""
        result = await self._request_json("GET", "/wts/heartbeat")
        if result is not True:
            self._reject_authentication("EnergyKey rejected the current session")

    async def async_recover_token_session(self) -> None:
        """Exchange the portal token for a fresh WebTools session."""
        current_cookies = self.cookie_state
        login_data = _decode_login_cookie(current_cookies.get("wt3login", ""))
        token = login_data.get("token") if isinstance(login_data, dict) else None
        if not isinstance(token, str) or not token:
            self._reject_authentication(
                "The EnergyKey token session cannot be renewed automatically"
            )

        result = await self._request_json(
            "POST", "/wts/loginToken", data={"token": token}
        )
        if not isinstance(result, dict):
            self._reject_authentication(
                "EnergyKey returned an invalid token-login response"
            )
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            self._reject_authentication("EnergyKey did not return a renewed session")

        renewed_login = dict(result)
        renewed_login["token"] = token
        encoded_login = quote(
            json.dumps(renewed_login, ensure_ascii=False, separators=(",", ":")),
            safe="",
        )
        renewed_cookies = {
            **current_cookies,
            "wt3SessionId": session_id,
            "wt3login": encoded_login,
        }
        renewed_metadata = session_metadata(renewed_cookies)
        if renewed_metadata.account_identifier != self._metadata.account_identifier:
            self._reject_authentication(
                "EnergyKey renewed the session for a different account"
            )
        self._session.cookie_jar.update_cookies(
            {
                "wt3SessionId": session_id,
                "wt3login": encoded_login,
            },
            URL(self.base_url),
        )
        self._metadata = renewed_metadata

    async def async_get_meters(self) -> list[EnergyKeyMeter]:
        """Discover every EnergyKey meter in the account."""
        payload = await self._request_json("GET", "/wts/itemGroups")
        return _extract_meters(payload)

    async def async_get_views(self, meter: EnergyKeyMeter) -> list[ConsumptionView]:
        """Return visible consumption views for a meter."""
        payload = await self._request_json(
            "GET",
            "/wts/consumptionView/list/",
            params={"itemId": meter.item_id, "itemCategory": ITEM_CATEGORY},
        )
        return _parse_views(payload)

    async def async_get_consumption(
        self,
        meter: EnergyKeyMeter,
        view: ConsumptionView,
        start: datetime,
        end: datetime,
    ) -> ConsumptionResult:
        """Fetch one time range from a consumption view."""
        if view.zoom_level is None:
            raise EnergyKeyProtocolError("The consumption view has no daily zoom level")
        payload = await self._request_json(
            "POST",
            "/wts/consumptionView/data",
            data={
                "viewId": view.view_id,
                "itemId": meter.item_id,
                "itemCategory": ITEM_CATEGORY,
                "zoomLevel": view.zoom_level,
                "prescaleUnitId": view.unit_id,
                "start": str(_epoch_milliseconds(start)),
                "end": str(_epoch_milliseconds(end)),
            },
        )
        return _parse_consumption(payload, view)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
    ) -> Any:
        url = URL(self.base_url).join(URL(path))
        for attempt in range(1, REQUEST_RETRY_ATTEMPTS + 1):
            try:
                return await self._request_json_once(
                    method, url, path, params=params, data=data
                )
            except _EnergyKeyTransientError as err:
                if attempt == REQUEST_RETRY_ATTEMPTS:
                    raise EnergyKeyConnectionError(
                        f"{err} after {attempt} attempts"
                    ) from err
                delay = REQUEST_RETRY_BASE_SECONDS * 2 ** (attempt - 1)
                _LOGGER.warning(
                    "Temporary EnergyKey request failure for %s %s "
                    "(attempt %d/%d): %s; retrying in %.1f seconds",
                    method,
                    path,
                    attempt,
                    REQUEST_RETRY_ATTEMPTS,
                    err,
                    delay,
                )
                await asyncio.sleep(delay)
        raise AssertionError("EnergyKey retry loop completed unexpectedly")

    async def _request_json_once(
        self,
        method: str,
        url: URL,
        path: str,
        *,
        params: Mapping[str, str] | None,
        data: Mapping[str, str] | None,
    ) -> Any:
        """Perform one request attempt and classify retryable failures."""
        headers = self._headers()
        try:
            async with self._request_lock, asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                if self._authentication_rejected:
                    raise EnergyKeyAuthError(
                        "EnergyKey previously rejected the current session"
                    )
                response = await self._session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                    allow_redirects=False,
                )
                async with response:
                    if response.status in (401, 403):
                        self._reject_authentication(
                            "EnergyKey rejected the current session"
                        )
                    if 300 <= response.status < 400:
                        location = response.headers.get("Location", "")
                        if location:
                            redirect = URL(location)
                            if not redirect.is_absolute():
                                redirect = url.join(redirect)
                            if redirect.origin() != self._origin:
                                raise EnergyKeyProtocolError(
                                    "EnergyKey attempted a cross-origin redirect"
                                )
                        self._reject_authentication(
                            "EnergyKey redirected to a login flow"
                        )
                    if response.status == 429:
                        raise EnergyKeyRateLimitError(
                            _parse_retry_after(response.headers.get("Retry-After"))
                        )
                    if response.status >= 500:
                        error_body, error_excerpt = await _async_read_error_body(
                            response
                        )
                        _LOGGER.debug(
                            "EnergyKey HTTP %d response body for %s %s: %r",
                            response.status,
                            method,
                            path,
                            error_excerpt,
                        )
                        if _is_stale_consumption_resource(path, error_body):
                            raise EnergyKeyStaleResourceError(
                                "EnergyKey no longer recognizes a discovered "
                                "consumption item or view"
                            )
                        raise _EnergyKeyTransientError(
                            f"EnergyKey returned HTTP {response.status} "
                            f"for {method} {path}"
                        )
                    if response.status >= 400:
                        raise EnergyKeyConnectionError(
                            f"EnergyKey returned HTTP {response.status} "
                            f"for {method} {path}"
                        )

                    content_type = response.headers.get("Content-Type", "").casefold()
                    text = await _async_read_response_text(response)
                    if "json" not in content_type:
                        folded_text = text.casefold()
                        looks_like_html = (
                            "html" in content_type
                            or folded_text.lstrip().startswith(
                                ("<!doctype html", "<html")
                            )
                        )
                        if looks_like_html and any(
                            marker in folded_text for marker in _LOGIN_MARKERS
                        ):
                            self._reject_authentication(
                                "EnergyKey returned a login page"
                            )
                        raise EnergyKeyProtocolError(
                            "EnergyKey returned a non-JSON response"
                        )
                    try:
                        result = json.loads(text)
                    except (TypeError, json.JSONDecodeError) as err:
                        raise EnergyKeyProtocolError(
                            "EnergyKey returned malformed JSON"
                        ) from err
                    self._last_activity = monotonic()
                    return result
        except EnergyKeyError:
            raise
        except (TimeoutError, ClientError) as err:
            raise _EnergyKeyTransientError(
                f"Could not communicate with EnergyKey for {method} {path} "
                f"({type(err).__name__})"
            ) from err

    def _reject_authentication(self, message: str) -> Never:
        """Latch a definitive rejection and raise the public auth exception."""
        self._authentication_rejected = True
        raise EnergyKeyAuthError(message)

    def _headers(self) -> dict[str, str]:
        cookies = self.cookie_state
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"{self.base_url}/forbrug",
            "X-Requested-With": "XMLHttpRequest",
        }
        session_id = cookies.get("wt3SessionId")
        if session_id:
            headers["session-id"] = session_id
        if self._metadata.customer_database_number:
            headers["customer-database-number"] = (
                self._metadata.customer_database_number
            )
        if self._metadata.context_id:
            headers["context-id"] = self._metadata.context_id
        if self._metadata.language_id:
            headers["language-id"] = self._metadata.language_id
        return headers


def _decode_login_cookie(value: str) -> Any:
    candidate = value.strip()
    for _ in range(3):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
        candidate = candidate[1:-1]
    try:
        return json.loads(candidate)
    except (TypeError, json.JSONDecodeError):
        return {}


def _is_stale_consumption_resource(path: str, body: str) -> bool:
    """Recognize EnergyKey's item-not-found response from its data endpoint."""
    if path != "/wts/consumptionView/data":
        return False
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and str(payload.get("errorCode")) == "1101"


def _find_nested_value(data: Any, keys: set[str], *, _depth: int = 0) -> str | None:
    if _depth >= MAX_LOGIN_METADATA_DEPTH:
        return None
    if isinstance(data, dict):
        for key, value in data.items():
            canonical = _canonical(str(key))
            if canonical in keys and value not in (None, ""):
                return str(value)
        for value in data.values():
            if found := _find_nested_value(value, keys, _depth=_depth + 1):
                return found
    elif isinstance(data, list):
        for item in data:
            if found := _find_nested_value(item, keys, _depth=_depth + 1):
                return found
    return None


async def _async_read_response_text(response: Any) -> str:
    """Read a bounded response body and decode it without exposing its content."""
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_RESPONSE_BODY_BYTES:
                raise EnergyKeyProtocolError("EnergyKey returned an oversized response")
        except ValueError:
            pass

    body = bytearray()
    while len(body) <= MAX_RESPONSE_BODY_BYTES:
        chunk = await response.content.read(
            min(64 * 1024, MAX_RESPONSE_BODY_BYTES + 1 - len(body))
        )
        if not chunk:
            break
        body.extend(chunk)
    if len(body) > MAX_RESPONSE_BODY_BYTES:
        raise EnergyKeyProtocolError("EnergyKey returned an oversized response")
    try:
        return bytes(body).decode(response.charset or "utf-8")
    except (LookupError, UnicodeDecodeError) as err:
        raise EnergyKeyProtocolError(
            "EnergyKey returned an undecodable response"
        ) from err


async def _async_read_error_body(response: Any) -> tuple[str, str]:
    """Return bounded classification text and a small safe logging excerpt."""
    body = bytearray()
    while len(body) <= MAX_RESPONSE_BODY_BYTES:
        chunk = await response.content.read(
            min(64 * 1024, MAX_RESPONSE_BODY_BYTES + 1 - len(body))
        )
        if not chunk:
            break
        body.extend(chunk)
    oversized = len(body) > MAX_RESPONSE_BODY_BYTES
    bounded = bytes(body[:MAX_RESPONSE_BODY_BYTES])
    try:
        text = bounded.decode(response.charset or "utf-8", errors="replace")
    except LookupError:
        text = bounded.decode("utf-8", errors="replace")
    excerpt = text[:MAX_ERROR_BODY_LOG_BYTES]
    if not excerpt:
        excerpt = "<empty>"
    if oversized or len(text) > MAX_ERROR_BODY_LOG_BYTES:
        excerpt = f"{excerpt}… <truncated>"
    return ("" if oversized else text), excerpt


def _parse_retry_after(value: str | None) -> float | None:
    """Return a bounded delta-seconds Retry-After value when provided."""
    if value is None:
        return None
    stripped = value.strip()
    try:
        retry_after = float(stripped)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(stripped)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        retry_after = max(
            0.0, (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        )
    if not math.isfinite(retry_after) or retry_after < 0:
        return None
    return min(retry_after, MAX_RETRY_AFTER_SECONDS)


def _extract_meter_ids(payload: Any) -> set[str]:
    """Return active meter IDs for response-schema tests."""
    return {meter.item_id for meter in _extract_meters(payload)}


def _extract_meters(payload: Any) -> list[EnergyKeyMeter]:
    meters: dict[str, EnergyKeyMeter] = {}

    def visit(node: Any, inside_items: bool = False) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, inside_items)
            return
        if not isinstance(node, dict):
            return

        item_id = _first_mapping_value(node, "id", "itemId", "itemID")
        category = _first_mapping_value(
            node, "itemCategory", "itemType", "category", "type"
        )
        is_meter = item_id is not None and (
            _canonical(str(category or "")) == _canonical(ITEM_CATEGORY)
            or (
                inside_items
                and any("meter" in _canonical(str(value)) for value in node.values())
            )
        )
        if is_meter and not _is_inactive(node.get("active")):
            item_id_text = str(item_id)
            key = opaque_meter_key(item_id_text)
            meters[key] = EnergyKeyMeter(
                item_id=item_id_text,
                key=key,
                portal_name=_safe_optional_text(node.get("customName")),
                meter_number=_safe_optional_text(node.get("label")),
                kind_hint=_meter_kind_hint(node.get("itemType")),
            )

        for key, value in node.items():
            canonical = _canonical(str(key))
            visit(
                value,
                inside_items
                or canonical in {"items", "meters", "energykeymeters", "itemlist"},
            )

    visit(payload)
    return sorted(meters.values(), key=lambda meter: meter.key)


def _is_inactive(value: Any) -> bool:
    if value is False:
        return True
    return isinstance(value, str) and _canonical(value) in {"false", "inactive", "0"}


def _safe_optional_text(value: Any) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not text or len(text) > 128 or any(ord(char) < 32 for char in text):
        return None
    return text


def _meter_kind_hint(value: Any) -> MeterKind | None:
    canonical = _canonical(str(value or ""))
    if canonical in {"water", "watermeter"}:
        return MeterKind.WATER
    if canonical in {
        "districtheat",
        "districtheating",
        "heat",
        "heating",
        "heatingmeter",
    }:
        return MeterKind.ENERGY
    return None


def _parse_views(payload: Any) -> list[ConsumptionView]:
    if not isinstance(payload, dict) or not isinstance(payload.get("views"), list):
        raise EnergyKeyProtocolError("EnergyKey returned an invalid view list")

    views: list[ConsumptionView] = []
    for raw in payload["views"]:
        if not isinstance(raw, dict) or raw.get("visible", True) is False:
            continue
        view_id = _first_mapping_value(raw, "id", "viewId")
        if view_id is None:
            continue
        unit_raw = raw.get("unit")
        unit_id = ""
        unit_name = ""
        if isinstance(unit_raw, dict):
            unit_id = str(_first_mapping_value(unit_raw, "id", "unitId") or "")
            unit_name = str(
                _first_mapping_value(unit_raw, "unit", "name", "symbol") or unit_id
            )
        elif unit_raw is not None:
            unit_id = unit_name = str(unit_raw)
        if not unit_id:
            continue
        name_type = str(
            _first_mapping_value(raw, "nameType", "key", "name", "type") or ""
        )
        views.append(
            ConsumptionView(
                view_id=str(view_id),
                name_type=name_type,
                unit_id=unit_id,
                unit_name=unit_name,
                zoom_level=_find_daily_zoom_level(raw),
            )
        )
    return views


def _find_daily_zoom_level(data: Any) -> str | None:
    strings: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if _canonical(str(key)) in {
                    "zoomlevel",
                    "zoomlevelid",
                    "id",
                    "key",
                    "name",
                    "value",
                } and isinstance(value, str):
                    strings.add(value)
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(data)
    if ZOOM_LEVEL_DAY_CANDIDATE in strings:
        return ZOOM_LEVEL_DAY_CANDIDATE
    daily = sorted(
        value
        for value in strings
        if "day" in _canonical(value) and "month" in _canonical(value)
    )
    return daily[0] if daily else None


def _parse_consumption(payload: Any, view: ConsumptionView) -> ConsumptionResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("series"), list):
        raise EnergyKeyProtocolError("EnergyKey returned invalid consumption data")

    series: list[ConsumptionSeries] = []
    for raw_series in payload["series"]:
        if not isinstance(raw_series, dict):
            continue
        key = str(_first_mapping_value(raw_series, "key", "id") or "")
        name = str(_first_mapping_value(raw_series, "name", "label") or key)
        series_data = raw_series.get("serieData", raw_series.get("seriesData", {}))
        raw_points = (
            series_data.get("datapoints", []) if isinstance(series_data, dict) else []
        )
        if not isinstance(raw_points, list):
            continue
        points = tuple(
            point
            for raw_point in raw_points
            if (point := _parse_point(raw_point)) is not None
        )
        if points:
            series.append(ConsumptionSeries(key=key, name=name, points=points))

    if not series:
        raise EnergyKeyProtocolError("EnergyKey returned no consumption points")

    unit_id = view.unit_id
    unit_name = view.unit_name
    keyfigure = payload.get("keyfigure")
    if isinstance(keyfigure, dict):
        prescale = keyfigure.get("prescaleUnit")
        if isinstance(prescale, dict):
            unit_id = str(_first_mapping_value(prescale, "id", "unitId") or unit_id)
            unit_name = str(
                _first_mapping_value(prescale, "unit", "name", "symbol") or unit_name
            )
    return ConsumptionResult(unit_id=unit_id, unit_name=unit_name, series=tuple(series))


def _parse_point(raw: Any) -> ConsumptionPoint | None:
    if not isinstance(raw, dict):
        return None
    try:
        start = _parse_datetime(raw["start"])
        end = _parse_datetime(raw["end"])
        value = _parse_float(raw["value"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    counter_value_raw = raw.get("counterValue")
    counter_date_raw = raw.get("counterValueDate")
    try:
        counter_value = (
            _parse_float(counter_value_raw) if counter_value_raw is not None else None
        )
    except (TypeError, ValueError):
        counter_value = None
    try:
        counter_date = (
            _parse_datetime(counter_date_raw) if counter_date_raw is not None else None
        )
    except (TypeError, ValueError, OverflowError):
        counter_date = None
    try:
        complete = _parse_bool(raw.get("complete", False))
    except (TypeError, ValueError):
        return None
    return ConsumptionPoint(
        start=start,
        end=end,
        value=value,
        complete=complete,
        counter_value=counter_value,
        counter_date=counter_date,
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        milliseconds = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if match := _EPOCH_WRAPPER.fullmatch(stripped):
            milliseconds = float(match.group("value"))
        else:
            try:
                milliseconds = float(stripped)
            except ValueError:
                parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC)
    else:
        raise TypeError("Unsupported timestamp")
    return datetime.fromtimestamp(milliseconds / 1000, UTC)


def _parse_float(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError("Boolean is not numeric")
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Numeric values must be finite")
    return result


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, "0", "false", "False"):
        return False
    if value in (1, "1", "true", "True"):
        return True
    raise ValueError("Unsupported Boolean value")


def _epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return round(value.timestamp() * 1000)


def _first_mapping_value(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return None


def _canonical(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())
