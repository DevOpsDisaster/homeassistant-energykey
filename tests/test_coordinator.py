"""Tests for EnergyKey history and coordinator helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energykey.api import (
    EnergyKeyAuthError,
    EnergyKeyConnectionError,
    EnergyKeyProtocolError,
    EnergyKeyRateLimitError,
    EnergyKeyStaleResourceError,
)
from custom_components.energykey.const import (
    CONSUMPTION_KEEPALIVE_INTERVAL,
    CONSUMPTION_KEEPALIVE_LOOKBACK_DAYS,
    DOMAIN,
)
from custom_components.energykey.coordinator import (
    EnergyKeyCoordinator,
    _assign_device_names,
    _has_complete_primary_changes,
    _history_key,
    _local_midnight,
    _merge_history,
    _merge_history_and_offset,
    _MeterPlan,
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
    ConsumptionSeries,
    ConsumptionView,
    EnergyKeyMeter,
    MeterKind,
    MeterSnapshot,
    MetricKind,
)


def _point(day: int, value: float, *, complete: bool = True) -> ConsumptionPoint:
    return ConsumptionPoint(
        start=datetime(2026, 7, day, tzinfo=UTC),
        end=datetime(2026, 7, day + 1, tzinfo=UTC),
        value=value,
        complete=complete,
    )


def _primary_result(
    actual: tuple[ConsumptionPoint, ...],
    expected: tuple[ConsumptionPoint, ...] = (),
) -> ConsumptionResult:
    series = [ConsumptionSeries("usageConsumption.actual", "Actual", actual)]
    if expected:
        series.append(ConsumptionSeries("usageBudget.expected", "Expected", expected))
    return ConsumptionResult("cubic_meter", "m3", tuple(series))


class _KeepaliveStore:
    """Minimal in-memory store for coordinator keepalive tests."""

    def __init__(
        self,
        histories: dict[str, tuple[ConsumptionPoint, ...]] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.histories = histories or {}
        self.cookies = cookies or {}
        self.async_set_cookies = AsyncMock(side_effect=self._set_cookies)

    async def _set_cookies(self, cookies: dict[str, str]) -> None:
        self.cookies = dict(cookies)

    def history(self, history_key: str) -> tuple[ConsumptionPoint, ...]:
        return self.histories.get(history_key, ())


class _KeepaliveClient:
    """Per-meter activity and response fake for keepalive tests."""

    def __init__(
        self,
        ages: dict[str, float],
        outcomes: dict[str, ConsumptionResult | BaseException],
    ) -> None:
        self.ages = ages
        self.outcomes = outcomes
        self.calls: list[
            tuple[EnergyKeyMeter, ConsumptionView, datetime, datetime, int | None]
        ] = []
        self.cookie_state = {"wt3SessionId": "rotated"}

    def seconds_since_consumption(self, meter: EnergyKeyMeter) -> float:
        return self.ages[meter.key]

    async def async_get_consumption(
        self,
        meter: EnergyKeyMeter,
        view: ConsumptionView,
        start: datetime,
        end: datetime,
        *,
        request_attempts: int | None = None,
    ) -> ConsumptionResult:
        self.calls.append((meter, view, start, end, request_attempts))
        outcome = self.outcomes[meter.key]
        if isinstance(outcome, BaseException):
            raise outcome
        self.ages[meter.key] = 0
        return outcome


def _plan(meter_key: str) -> _MeterPlan:
    meter = EnergyKeyMeter(f"raw-{meter_key}", meter_key, kind_hint=MeterKind.WATER)
    view = ConsumptionView(
        f"view-{meter_key}",
        "usageConsumption",
        "cubic_meter",
        "m3",
        "month_by_days",
    )
    return _MeterPlan(meter, view)


def _keepalive_coordinator(hass, client, store, plans) -> EnergyKeyCoordinator:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = EnergyKeyCoordinator(hass, entry, client, store)
    coordinator._supported = plans
    coordinator.async_refresh = AsyncMock()
    return coordinator


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


def test_primary_change_detector_ignores_unchanged_and_provisional_points() -> None:
    actual = _point(1, 1.0)
    expected = _point(1, 2.0)
    store = _KeepaliveStore(
        {
            _history_key("meter", MetricKind.ACTUAL_CONSUMPTION): (actual,),
            _history_key("meter", MetricKind.EXPECTED_CONSUMPTION): (expected,),
        }
    )
    result = _primary_result(
        (actual, _point(2, 9.0, complete=False)),
        (expected, _point(2, 8.0, complete=False)),
    )

    assert not _has_complete_primary_changes(store, "meter", result)


@pytest.mark.parametrize(
    ("stored", "fetched"),
    [
        (_point(1, 1.0), _point(1, 1.5)),
        (_point(1, 1.0, complete=False), _point(1, 1.0)),
    ],
)
def test_primary_change_detector_finds_revision_or_completion(
    stored: ConsumptionPoint, fetched: ConsumptionPoint
) -> None:
    store = _KeepaliveStore({"meter": (stored,)})

    assert _has_complete_primary_changes(store, "meter", _primary_result((fetched,)))


def test_primary_change_detector_checks_expected_history() -> None:
    actual = _point(1, 1.0)
    store = _KeepaliveStore(
        {
            "meter": (actual,),
            "meter:expected_consumption": (_point(1, 2.0),),
        }
    )

    assert _has_complete_primary_changes(
        store, "meter", _primary_result((actual,), (_point(1, 2.5),))
    )


async def test_keepalive_delay_and_due_filter_are_per_meter(hass) -> None:
    interval = CONSUMPTION_KEEPALIVE_INTERVAL.total_seconds()
    recent = _plan("recent")
    due = _plan("due")
    provisional = _primary_result((_point(2, 3.0, complete=False),))
    client = _KeepaliveClient(
        {recent.meter.key: interval - 5, due.meter.key: interval},
        {recent.meter.key: provisional, due.meter.key: provisional},
    )
    store = _KeepaliveStore()
    coordinator = _keepalive_coordinator(hass, client, store, [recent, due])

    assert coordinator.seconds_until_consumption_keepalive() == 0
    assert await coordinator.async_run_consumption_keepalive() == 1
    assert [call[0].key for call in client.calls] == [due.meter.key]
    _meter, _view, start, end, attempts = client.calls[0]
    timezone = ZoneInfo(hass.config.time_zone)
    local_today = datetime.now(timezone).date()
    assert start == _local_midnight(
        local_today - timedelta(days=CONSUMPTION_KEEPALIVE_LOOKBACK_DAYS),
        timezone,
    )
    assert end == _local_midnight(local_today + timedelta(days=1), timezone)
    assert attempts == 1
    store.async_set_cookies.assert_awaited_once_with(client.cookie_state)
    coordinator.async_refresh.assert_not_awaited()
    assert coordinator.seconds_until_consumption_keepalive() == pytest.approx(5)


async def test_changed_keepalive_probes_coalesce_one_unlocked_refresh(hass) -> None:
    interval = CONSUMPTION_KEEPALIVE_INTERVAL.total_seconds()
    first = _plan("first")
    second = _plan("second")
    client = _KeepaliveClient(
        {first.meter.key: interval, second.meter.key: interval},
        {
            first.meter.key: _primary_result((_point(1, 1.0),)),
            second.meter.key: _primary_result((_point(1, 2.0),)),
        },
    )
    coordinator = _keepalive_coordinator(
        hass, client, _KeepaliveStore(), [first, second]
    )

    async def refresh_while_holding_operation_lock() -> None:
        async with coordinator._data_operation_lock:
            return

    coordinator.async_refresh.side_effect = refresh_while_holding_operation_lock

    assert await coordinator.async_run_consumption_keepalive() == 2
    assert [call[0].key for call in client.calls] == [
        first.meter.key,
        second.meter.key,
    ]
    coordinator.async_refresh.assert_awaited_once_with()


async def test_unchanged_keepalive_cookies_do_not_rewrite_storage(hass) -> None:
    interval = CONSUMPTION_KEEPALIVE_INTERVAL.total_seconds()
    plan = _plan("meter")
    result = _primary_result((_point(2, 3.0, complete=False),))
    client = _KeepaliveClient({plan.meter.key: interval}, {plan.meter.key: result})
    store = _KeepaliveStore(cookies=client.cookie_state)
    coordinator = _keepalive_coordinator(hass, client, store, [plan])

    assert await coordinator.async_run_consumption_keepalive() == 1
    store.async_set_cookies.assert_not_awaited()


async def test_changed_keepalive_surfaces_failed_followup_refresh(hass) -> None:
    interval = CONSUMPTION_KEEPALIVE_INTERVAL.total_seconds()
    plan = _plan("changed")
    client = _KeepaliveClient(
        {plan.meter.key: interval},
        {plan.meter.key: _primary_result((_point(1, 1.0),))},
    )
    coordinator = _keepalive_coordinator(hass, client, _KeepaliveStore(), [plan])
    refresh_error = RuntimeError("refresh failed")

    async def fail_refresh() -> None:
        coordinator.last_update_success = False
        coordinator.last_exception = refresh_error

    coordinator.async_refresh.side_effect = fail_refresh

    with pytest.raises(EnergyKeyConnectionError) as raised:
        await coordinator.async_run_consumption_keepalive()

    assert raised.value.__cause__ is refresh_error
    coordinator.async_refresh.assert_awaited_once_with()


async def test_keepalive_continues_after_meter_failure_then_surfaces_it(hass) -> None:
    interval = CONSUMPTION_KEEPALIVE_INTERVAL.total_seconds()
    failing = _plan("failing")
    successful = _plan("successful")
    error = EnergyKeyConnectionError("offline")
    client = _KeepaliveClient(
        {failing.meter.key: interval, successful.meter.key: interval},
        {
            failing.meter.key: error,
            successful.meter.key: _primary_result((_point(1, 2.0),)),
        },
    )
    store = _KeepaliveStore()
    coordinator = _keepalive_coordinator(hass, client, store, [failing, successful])

    with pytest.raises(EnergyKeyConnectionError) as raised:
        await coordinator.async_run_consumption_keepalive()

    assert raised.value is error
    assert [call[0].key for call in client.calls] == [
        failing.meter.key,
        successful.meter.key,
    ]
    coordinator.async_refresh.assert_awaited_once_with()
    store.async_set_cookies.assert_awaited_once_with(client.cookie_state)


@pytest.mark.parametrize(
    "error",
    [
        EnergyKeyAuthError("expired"),
        EnergyKeyRateLimitError(30),
        EnergyKeyStaleResourceError("stale item"),
    ],
)
async def test_keepalive_terminal_failure_stops_later_meter_and_remains_actionable(
    hass,
    error: EnergyKeyAuthError | EnergyKeyRateLimitError | EnergyKeyStaleResourceError,
) -> None:
    interval = CONSUMPTION_KEEPALIVE_INTERVAL.total_seconds()
    rejected = _plan("rejected")
    untouched = _plan("untouched")
    client = _KeepaliveClient(
        {rejected.meter.key: interval, untouched.meter.key: interval},
        {
            rejected.meter.key: error,
            untouched.meter.key: _primary_result((_point(1, 2.0),)),
        },
    )
    store = _KeepaliveStore()
    coordinator = _keepalive_coordinator(hass, client, store, [rejected, untouched])

    with pytest.raises(
        (EnergyKeyAuthError, EnergyKeyRateLimitError, EnergyKeyStaleResourceError)
    ) as raised:
        await coordinator.async_run_consumption_keepalive()

    assert raised.value is error
    assert [call[0].key for call in client.calls] == [rejected.meter.key]
    coordinator.async_refresh.assert_not_awaited()
    store.async_set_cookies.assert_awaited_once_with(client.cookie_state)


async def test_keepalive_unexpected_failure_propagates_without_side_effects(
    hass,
) -> None:
    interval = CONSUMPTION_KEEPALIVE_INTERVAL.total_seconds()
    plan = _plan("broken")
    error = RuntimeError("bug")
    client = _KeepaliveClient(
        {plan.meter.key: interval},
        {plan.meter.key: error},
    )
    store = _KeepaliveStore()
    coordinator = _keepalive_coordinator(hass, client, store, [plan])

    with pytest.raises(RuntimeError) as raised:
        await coordinator.async_run_consumption_keepalive()

    assert raised.value is error
    store.async_set_cookies.assert_not_awaited()
    coordinator.async_refresh.assert_not_awaited()
