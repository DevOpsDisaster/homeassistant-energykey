"""Redacted diagnostics for EnergyKey."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import SENSITIVE_CONFIG_KEYS
from .runtime import EnergyKeyRuntimeData


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[EnergyKeyRuntimeData]
) -> dict[str, Any]:
    """Return diagnostics without credentials, values, or provider identifiers."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    return {
        "entry": async_redact_data(dict(entry.data), SENSITIVE_CONFIG_KEYS),
        "session": {
            "last_successful_heartbeat": runtime.last_successful_heartbeat,
            "last_heartbeat_error": runtime.last_heartbeat_error,
            "reauth_started": runtime.reauth_started,
            "authentication_rejected": runtime.client.authentication_rejected,
        },
        "coordinator": {
            "last_successful_refresh": coordinator.last_successful_refresh,
            "last_update_success": coordinator.last_update_success,
            "unsupported_meter_count": coordinator.unsupported_meter_count,
            "history_error_count": coordinator.history_error_count,
        },
        "meters": [
            {
                "kind": meter.kind,
                "unit": meter.native_unit,
                "metrics": [
                    {
                        "kind": metric.kind,
                        "unit": metric.native_unit,
                        "history_points": len(metric.history),
                        "latest_period_end": metric.latest_period_end,
                    }
                    for metric in meter.metrics
                ],
            }
            for meter in (coordinator.data.meters if coordinator.data else ())
        ],
    }
