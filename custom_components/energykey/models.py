"""Typed models used by the EnergyKey integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class MeterKind(StrEnum):
    """Supported EnergyKey meter kinds."""

    WATER = "water"
    ENERGY = "energy"


class MetricKind(StrEnum):
    """Supported normalized EnergyKey metric kinds."""

    ACTUAL_CONSUMPTION = "actual_consumption"
    EXPECTED_CONSUMPTION = "expected_consumption"
    HEAT_VOLUME = "heat_volume"
    FLOW_TEMPERATURE = "flow_temperature"
    RETURN_TEMPERATURE = "return_temperature"


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """Non-secret metadata extracted from an authenticated session."""

    account_identifier: str
    customer_database_number: str | None = None
    context_id: str | None = None
    language_id: str | None = None


@dataclass(frozen=True, slots=True)
class EnergyKeyMeter:
    """An EnergyKey meter discovered through item groups."""

    item_id: str = field(repr=False)
    key: str
    portal_name: str | None = field(default=None, repr=False)
    meter_number: str | None = field(default=None, repr=False)
    kind_hint: MeterKind | None = None


@dataclass(frozen=True, slots=True)
class ConsumptionView:
    """A visible EnergyKey consumption view."""

    view_id: str = field(repr=False)
    name_type: str
    unit_id: str
    unit_name: str
    zoom_level: str | None


@dataclass(frozen=True, slots=True)
class ConsumptionPoint:
    """A normalized EnergyKey time-series point."""

    start: datetime
    end: datetime
    value: float
    complete: bool
    counter_value: float | None = None
    counter_date: datetime | None = None

    def to_storage(self) -> dict[str, Any]:
        """Serialize a point for Home Assistant storage."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "value": self.value,
            "complete": self.complete,
            "counter_value": self.counter_value,
            "counter_date": (
                self.counter_date.isoformat() if self.counter_date is not None else None
            ),
        }

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> ConsumptionPoint:
        """Deserialize a stored point."""
        start = datetime.fromisoformat(str(data["start"]))
        end = datetime.fromisoformat(str(data["end"]))
        counter_date_raw = data.get("counter_date")
        counter_date = (
            datetime.fromisoformat(str(counter_date_raw))
            if counter_date_raw is not None
            else None
        )
        return cls(
            start=_as_utc(start),
            end=_as_utc(end),
            value=float(data["value"]),
            complete=bool(data["complete"]),
            counter_value=(
                float(data["counter_value"])
                if data.get("counter_value") is not None
                else None
            ),
            counter_date=_as_utc(counter_date) if counter_date is not None else None,
        )

    def public_history(self) -> dict[str, Any]:
        """Return the documented, non-sensitive state attribute shape."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "value": self.value,
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class ConsumptionSeries:
    """One series returned by a consumption view."""

    key: str
    name: str
    points: tuple[ConsumptionPoint, ...]


@dataclass(frozen=True, slots=True)
class ConsumptionResult:
    """Normalized result from a consumption data request."""

    unit_id: str
    unit_name: str
    series: tuple[ConsumptionSeries, ...]

    @property
    def usage_series(self) -> ConsumptionSeries | None:
        """Return the unambiguous actual-consumption series."""
        matches = [
            item for item in self.series if "usageconsumption" in _canonical(item.key)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches and len(self.series) == 1:
            return self.series[0]
        return None

    @property
    def budget_series(self) -> ConsumptionSeries | None:
        """Return the unambiguous expected/budget consumption series."""
        matches = [
            item for item in self.series if "usagebudget" in _canonical(item.key)
        ]
        return matches[0] if len(matches) == 1 else None

    def matching_series(self, token: str) -> ConsumptionSeries | None:
        """Return one series matching a stable EnergyKey internal token."""
        canonical_token = _canonical(token)
        matches = [
            item for item in self.series if canonical_token in _canonical(item.key)
        ]
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    """One normalized metric and its retained portal history."""

    kind: MetricKind
    native_unit: str
    latest_value: float | None
    latest_period_start: datetime | None
    latest_period_end: datetime | None
    history: tuple[ConsumptionPoint, ...]
    statistics_offset: float = field(default=0.0, repr=False)


@dataclass(frozen=True, slots=True)
class MeterSnapshot:
    """The complete public state for one supported meter."""

    meter_key: str
    device_name: str
    kind: MeterKind
    native_unit: str
    metrics: tuple[MetricSnapshot, ...]
    portal_name: str | None = field(default=None, repr=False)
    meter_number: str | None = field(default=None, repr=False)

    def metric(self, kind: MetricKind) -> MetricSnapshot | None:
        """Return a supported metric by kind."""
        return next((metric for metric in self.metrics if metric.kind is kind), None)


@dataclass(frozen=True, slots=True)
class EnergyKeyData:
    """Coordinator data shared by all EnergyKey entities."""

    meters: tuple[MeterSnapshot, ...]

    def meter(self, meter_key: str) -> MeterSnapshot | None:
        """Return a meter by its opaque key."""
        return next(
            (meter for meter in self.meters if meter.meter_key == meter_key), None
        )


def _canonical(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
