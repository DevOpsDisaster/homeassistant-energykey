"""Tests for typed EnergyKey models."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.energykey.models import (
    ConsumptionPoint,
    ConsumptionResult,
    ConsumptionSeries,
)


def test_consumption_point_storage_round_trip() -> None:
    point = ConsumptionPoint(
        start=datetime(2026, 7, 30, tzinfo=UTC),
        end=datetime(2026, 7, 31, tzinfo=UTC),
        value=1.25,
        complete=False,
        counter_value=42.5,
        counter_date=datetime(2026, 7, 31, 1, tzinfo=UTC),
    )
    restored = ConsumptionPoint.from_storage(point.to_storage())
    assert restored == point
    assert restored.public_history() == {
        "start": "2026-07-30T00:00:00+00:00",
        "end": "2026-07-31T00:00:00+00:00",
        "value": 1.25,
        "complete": False,
    }


def test_stable_actual_budget_and_temperature_series_are_selected() -> None:
    point = ConsumptionPoint(
        start=datetime(2026, 7, 30, tzinfo=UTC),
        end=datetime(2026, 7, 31, tzinfo=UTC),
        value=1,
        complete=True,
    )
    actual = ConsumptionSeries("usageConsumption.actual", "Localized", (point,))
    budget = ConsumptionSeries("usageBudget.expected", "Localized", (point,))
    flow = ConsumptionSeries("forwardVolumeTemp.actual", "Localized", (point,))
    result = ConsumptionResult("kWh", "kWh", (actual, budget, flow))
    assert result.usage_series is actual
    assert result.budget_series is budget
    assert result.matching_series("forwardVolumeTemp") is flow
    assert result.matching_series("does-not-exist") is None
