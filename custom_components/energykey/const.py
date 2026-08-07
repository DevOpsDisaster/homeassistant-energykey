"""Constants for the EnergyKey integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "energykey"

CONF_BASE_URL: Final = "base_url"
CONF_COOKIES: Final = "cookies"
CONF_ACCOUNT_ID: Final = "account_id"

DEFAULT_BASE_URL: Final = "https://dinforsyning.wt.energykey.dk"
ENERGYKEY_HOST_SUFFIX: Final = ".wt.energykey.dk"

HEARTBEAT_INTERVAL: Final = timedelta(minutes=15)
HEARTBEAT_RETRY_INTERVAL: Final = timedelta(minutes=1)
SHUTDOWN_HEARTBEAT_TIMEOUT_SECONDS: Final = 5
# Keep the consumption resource active between refreshes. EnergyKey can keep
# accepting heartbeat requests after its consumption context has gone stale.
DATA_UPDATE_INTERVAL: Final = timedelta(hours=1)
RECENT_HISTORY_DAYS: Final = 45
HISTORY_MONTHS: Final = 13
REQUEST_TIMEOUT_SECONDS: Final = 30
REQUEST_RETRY_ATTEMPTS: Final = 3
REQUEST_RETRY_BASE_SECONDS: Final = 1.0
MAX_HISTORY_REQUESTS: Final = 2
MAX_COOKIE_HEADER_LENGTH: Final = 32_768
MAX_COOKIE_COUNT: Final = 128
MAX_COOKIE_VALUE_LENGTH: Final = 16_384
MAX_LOGIN_METADATA_DEPTH: Final = 16
MAX_RESPONSE_BODY_BYTES: Final = 5 * 1024 * 1024
MAX_ERROR_BODY_LOG_BYTES: Final = 2 * 1024
MAX_RETRY_AFTER_SECONDS: Final = 60 * 60

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}.session"

ITEM_CATEGORY: Final = "EnergyKeyMeter"
ZOOM_LEVEL_DAY_CANDIDATE: Final = "month_by_days"

REQUIRED_COOKIES: Final = frozenset({"wt3SessionId", "wt3login"})
SENSITIVE_CONFIG_KEYS: Final = {CONF_COOKIES, CONF_ACCOUNT_ID}


def heartbeat_update_signal(entry_id: str) -> str:
    """Return the dispatcher signal for one entry's heartbeat state."""
    return f"{DOMAIN}_heartbeat_update_{entry_id}"
