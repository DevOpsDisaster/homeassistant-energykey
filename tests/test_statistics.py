"""Tests for EnergyKey Recorder statistics backfill."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock, patch

from homeassistant.components.recorder import DATA_INSTANCE
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from custom_components.energykey.models import (
    ConsumptionPoint,
    EnergyKeyData,
    MeterKind,
    MeterSnapshot,
    MetricKind,
    MetricSnapshot,
)
from custom_components.energykey.statistics import (
    _metadata_for_metric,
    _statistic_id,
    _statistics_for_metric,
    async_clear_statistics,
    async_clear_statistics_for_history_keys,
    async_import_statistics,
)


def _point(day: int, value: float, *, complete: bool = True) -> ConsumptionPoint:
    return ConsumptionPoint(
        start=datetime(2026, 7, day, tzinfo=UTC),
        end=datetime(2026, 7, day + 1, tzinfo=UTC),
        value=value,
        complete=complete,
    )


def _metric(kind: MetricKind) -> MetricSnapshot:
    return MetricSnapshot(
        kind=kind,
        native_unit="m³" if kind is MetricKind.HEAT_VOLUME else "kWh",
        latest_value=2,
        latest_period_start=datetime(2026, 7, 2, tzinfo=UTC),
        latest_period_end=datetime(2026, 7, 3, tzinfo=UTC),
        history=(_point(1, 1), _point(2, 2), _point(3, 3, complete=False)),
        statistics_offset=10,
    )


def _meter(*metrics: MetricSnapshot) -> MeterSnapshot:
    return MeterSnapshot(
        meter_key="a" * 64,
        device_name="Heat meter (12345678)",
        kind=MeterKind.ENERGY,
        native_unit="kWh",
        metrics=metrics,
    )


def test_statistics_are_idempotent_and_exclude_incomplete_periods() -> None:
    statistics = _statistics_for_metric(_metric(MetricKind.ACTUAL_CONSUMPTION))
    assert statistics == [
        {
            "start": datetime(2026, 7, 1, tzinfo=UTC),
            "state": 1,
            "sum": 11,
        },
        {
            "start": datetime(2026, 7, 2, tzinfo=UTC),
            "state": 2,
            "sum": 13,
        },
    ]


def test_metadata_uses_stable_opaque_ids_and_unit_classes() -> None:
    consumption = _metric(MetricKind.ACTUAL_CONSUMPTION)
    volume = _metric(MetricKind.HEAT_VOLUME)
    meter = _meter(consumption, volume)
    consumption_metadata = _metadata_for_metric(meter, consumption)
    volume_metadata = _metadata_for_metric(meter, volume)
    assert consumption_metadata["statistic_id"] == (f"energykey:{'a' * 64}_consumption")
    assert consumption_metadata["unit_class"] == EnergyConverter.UNIT_CLASS
    assert volume_metadata["unit_class"] == VolumeConverter.UNIT_CLASS
    assert volume_metadata["mean_type"] is StatisticMeanType.NONE
    assert volume_metadata["has_sum"] is True
    assert _statistic_id(meter.meter_key, MetricKind.HEAT_VOLUME).endswith(
        "_heat_volume"
    )


def test_import_is_skipped_without_recorder_and_queues_supported_metrics() -> None:
    actual = _metric(MetricKind.ACTUAL_CONSUMPTION)
    expected = _metric(MetricKind.EXPECTED_CONSUMPTION)
    data = EnergyKeyData((_meter(actual, expected),))
    hass = Mock(data={})
    with patch(
        "custom_components.energykey.statistics.async_add_external_statistics"
    ) as add_statistics:
        async_import_statistics(hass, data)
        add_statistics.assert_not_called()
        hass.data[DATA_INSTANCE] = object()
        async_import_statistics(hass, data)
        add_statistics.assert_called_once()


def test_cleanup_derives_only_known_statistics_from_opaque_history_keys() -> None:
    hass = Mock(data={DATA_INSTANCE: object()})
    recorder = Mock()
    meter_key = "b" * 64
    with patch(
        "custom_components.energykey.statistics.get_instance", return_value=recorder
    ):
        async_clear_statistics_for_history_keys(
            hass,
            (
                meter_key,
                f"{meter_key}:heat_volume",
                f"{meter_key}:expected_consumption",
                "not-an-opaque-meter-key",
            ),
        )
    recorder.async_clear_statistics.assert_called_once_with(
        [
            f"energykey:{meter_key}_consumption",
            f"energykey:{meter_key}_heat_volume",
        ]
    )


def test_clear_statistics_uses_only_supported_metrics() -> None:
    actual = _metric(MetricKind.ACTUAL_CONSUMPTION)
    expected = _metric(MetricKind.EXPECTED_CONSUMPTION)
    data = EnergyKeyData((_meter(actual, expected),))
    hass = Mock(data={DATA_INSTANCE: object()})
    recorder = Mock()
    with patch(
        "custom_components.energykey.statistics.get_instance", return_value=recorder
    ):
        async_clear_statistics(hass, data)

    recorder.async_clear_statistics.assert_called_once_with(
        [f"energykey:{'a' * 64}_consumption"]
    )


def test_clear_and_import_statistics_skip_empty_work() -> None:
    hass_without_recorder = Mock(data={})
    data = EnergyKeyData((_meter(_metric(MetricKind.ACTUAL_CONSUMPTION)),))
    async_clear_statistics(hass_without_recorder, data)

    incomplete = MetricSnapshot(
        kind=MetricKind.ACTUAL_CONSUMPTION,
        native_unit="kWh",
        latest_value=None,
        latest_period_start=None,
        latest_period_end=None,
        history=(_point(1, 1, complete=False),),
    )
    hass = Mock(data={DATA_INSTANCE: object()})
    with patch(
        "custom_components.energykey.statistics.async_add_external_statistics"
    ) as add_statistics:
        async_import_statistics(hass, EnergyKeyData((_meter(incomplete),)))
    add_statistics.assert_not_called()


def test_non_hour_aligned_period_is_not_imported() -> None:
    point = ConsumptionPoint(
        start=datetime(2026, 7, 1, 0, 30, tzinfo=UTC),
        end=datetime(2026, 7, 2, 0, 30, tzinfo=UTC),
        value=1,
        complete=True,
    )
    metric = MetricSnapshot(
        kind=MetricKind.ACTUAL_CONSUMPTION,
        native_unit="kWh",
        latest_value=1,
        latest_period_start=point.start,
        latest_period_end=point.end,
        history=(point,),
    )
    assert _statistics_for_metric(metric) == []
