"""Tests for EnergyKey history and coordinator helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.energykey.api import EnergyKeyProtocolError
from custom_components.energykey.coordinator import (
    _assign_device_names,
    _diagnostic_exception_name,
    _merge_history,
    _merge_history_and_offset,
    _missing_month_windows,
    _normalized_temperature_unit,
    _normalized_unit,
    _select_heat_volume_view,
    _select_supported_view,
    _select_temperature_view,
)
from custom_components.energykey.models import (
    ConsumptionPoint,
    ConsumptionResult,
    ConsumptionView,
    MeterKind,
    MeterSnapshot,
)


def test_diagnostic_exception_name_unwraps_failure_chain() -> None:
    try:
        try:
            raise ConnectionError("sensitive provider detail")
        except ConnectionError as err:
            raise UpdateFailed("wrapper") from err
    except UpdateFailed as err:
        assert _diagnostic_exception_name(err) == "ConnectionError"

    assert _diagnostic_exception_name(None) == "unknown"


def _point(day: int, value: float, *, complete: bool = True) -> ConsumptionPoint:
    return ConsumptionPoint(
        start=datetime(2026, 7, day, tzinfo=UTC),
        end=datetime(2026, 7, day + 1, tzinfo=UTC),
        value=value,
        complete=complete,
    )


@pytest.mark.parametrize(
    ("unit_id", "unit_name", "expected"),
    [
        ("cubic_meter", "m3", (MeterKind.WATER, UnitOfVolume.CUBIC_METERS)),
        ("litre", "liter", (MeterKind.WATER, UnitOfVolume.LITERS)),
        ("kWh", "kWh", (MeterKind.ENERGY, UnitOfEnergy.KILO_WATT_HOUR)),
        ("gigajoule", "GJ", (MeterKind.ENERGY, UnitOfEnergy.GIGA_JOULE)),
    ],
)
def test_unit_normalization(
    unit_id: str, unit_name: str, expected: tuple[MeterKind, str]
) -> None:
    result = ConsumptionResult(unit_id, unit_name, ())
    assert _normalized_unit(result) == expected


def test_unknown_unit_is_not_guessed() -> None:
    with pytest.raises(EnergyKeyProtocolError):
        _normalized_unit(ConsumptionResult("currency", "DKK", ()))


def test_temperature_unit_and_specialized_views() -> None:
    volume = ConsumptionView(
        "volume",
        "consumptionview.views.volumen",
        "cubic_meter",
        "m3",
        "month_by_days",
    )
    temperature = ConsumptionView(
        "temperature",
        "consumptionview.views.returntempwithtemp",
        "average@celcius",
        "celcius",
        "month_by_days",
    )
    assert _select_heat_volume_view([volume, temperature]) == volume
    assert _select_temperature_view([volume, temperature]) == temperature
    assert (
        _normalized_temperature_unit(
            ConsumptionResult("average@celcius", "celcius", ())
        )
        == "°C"
    )


def test_selects_only_an_unambiguous_supported_view() -> None:
    supported = ConsumptionView(
        "actual-view", "usageConsumption", "kWh", "kWh", "month_by_days"
    )
    unsupported = ConsumptionView(
        "billing-view", "billing", "currency", "DKK", "month_by_days"
    )
    assert _select_supported_view([unsupported, supported]) == supported

    duplicate = ConsumptionView(
        "other-actual", "usageConsumption", "kWh", "kWh", "month_by_days"
    )
    assert _select_supported_view([supported, duplicate]) is None

    water = ConsumptionView(
        "water-view", "usageConsumption", "cubic_meter", "m3", "month_by_days"
    )
    assert _select_supported_view([water, supported], MeterKind.ENERGY) == supported

    current = ConsumptionView(
        "current",
        "consumptionview.views.consumptionwithbudgetandbudgetcorrected",
        "giga@joule",
        "joule",
        "month_by_days",
    )
    comparison = ConsumptionView(
        "comparison",
        "consumptionview.views.consumptionlastthreeyears",
        "giga@joule",
        "joule",
        "month_by_days",
    )
    assert _select_supported_view([comparison, current], MeterKind.ENERGY) == current


def test_history_merging_replaces_revised_period_and_prunes() -> None:
    old = _point(1, 1.0)
    revised = _point(1, 1.5)
    current = _point(2, 2.0, complete=False)
    merged = _merge_history(
        (old,),
        [revised, current],
        datetime(2026, 7, 2, tzinfo=UTC),
    )
    assert merged == (revised, current)
    assert merged[-1].complete is False


def test_pruned_complete_history_advances_statistics_offset_once() -> None:
    old = _point(1, 1.5)
    current = _point(2, 2.0)
    history, offset = _merge_history_and_offset(
        (old, current),
        [],
        datetime(2026, 7, 2, 0, 0, 1, tzinfo=UTC),
        10.0,
    )
    assert history == (current,)
    assert offset == 11.5


def test_missing_history_is_requested_in_calendar_months() -> None:
    stored = (
        ConsumptionPoint(
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
            1,
            True,
        ),
    )
    windows = _missing_month_windows(
        stored,
        date(2025, 12, 1),
        date(2026, 3, 15),
        date(2026, 5, 1),
        ZoneInfo("UTC"),
    )
    assert windows == [
        (
            datetime(2025, 12, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 2, 1, tzinfo=UTC),
            datetime(2026, 3, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 4, 1, tzinfo=UTC),
        ),
    ]


def test_device_names_include_type_portal_name_and_meter_number() -> None:
    base = {
        "native_unit": "kWh",
        "metrics": (),
    }
    snapshots = [
        MeterSnapshot(
            "b",
            "",
            MeterKind.ENERGY,
            **base,
            portal_name="Basement",
            meter_number="87654321",
        ),
        MeterSnapshot("a", "", MeterKind.ENERGY, **base, meter_number="12345678"),
    ]
    named = _assign_device_names(snapshots)
    assert [(item.meter_key, item.device_name) for item in named] == [
        ("a", "Heat meter (12345678)"),
        ("b", "Heat meter – Basement (87654321)"),
    ]
