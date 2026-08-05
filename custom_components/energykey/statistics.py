"""Recorder statistics import for EnergyKey consumption history."""

from __future__ import annotations

import re
from collections.abc import Iterable

from homeassistant.components.recorder import DATA_INSTANCE, get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .const import DOMAIN
from .models import EnergyKeyData, MeterKind, MeterSnapshot, MetricKind, MetricSnapshot

_IMPORTED_METRICS = frozenset({MetricKind.ACTUAL_CONSUMPTION, MetricKind.HEAT_VOLUME})
_METER_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def async_import_statistics(hass: HomeAssistant, data: EnergyKeyData) -> None:
    """Insert or revise complete EnergyKey periods as external statistics."""
    if DATA_INSTANCE not in hass.data:
        return
    for meter in data.meters:
        for metric in meter.metrics:
            if metric.kind not in _IMPORTED_METRICS:
                continue
            statistics = _statistics_for_metric(metric)
            if not statistics:
                continue
            async_add_external_statistics(
                hass,
                _metadata_for_metric(meter, metric),
                statistics,
            )


def async_clear_statistics(hass: HomeAssistant, data: EnergyKeyData) -> None:
    """Queue removal of statistics owned by one EnergyKey config entry."""
    if DATA_INSTANCE not in hass.data:
        return
    statistic_ids = [
        _statistic_id(meter.meter_key, metric.kind)
        for meter in data.meters
        for metric in meter.metrics
        if metric.kind in _IMPORTED_METRICS
    ]
    if statistic_ids:
        get_instance(hass).async_clear_statistics(statistic_ids)


def async_clear_statistics_for_history_keys(
    hass: HomeAssistant, history_keys: Iterable[str]
) -> None:
    """Clear known statistics using only non-secret persisted stream keys."""
    if DATA_INSTANCE not in hass.data:
        return
    statistic_ids: list[str] = []
    for history_key in history_keys:
        meter_key, separator, metric_suffix = history_key.partition(":")
        if not _METER_KEY_PATTERN.fullmatch(meter_key):
            continue
        if not separator:
            statistic_ids.append(
                _statistic_id(meter_key, MetricKind.ACTUAL_CONSUMPTION)
            )
        elif metric_suffix == MetricKind.HEAT_VOLUME.value:
            statistic_ids.append(_statistic_id(meter_key, MetricKind.HEAT_VOLUME))
    if statistic_ids:
        get_instance(hass).async_clear_statistics(statistic_ids)


def _statistics_for_metric(metric: MetricSnapshot) -> list[StatisticData]:
    """Build deterministic running sums so re-imports revise existing rows."""
    running_sum = metric.statistics_offset
    statistics: list[StatisticData] = []
    for point in sorted(metric.history, key=lambda item: (item.start, item.end)):
        if not point.complete:
            continue
        running_sum += point.value
        # Recorder external statistics are hourly. EnergyKey daily periods always
        # begin on an hour boundary, including across daylight-saving changes.
        if any((point.start.minute, point.start.second, point.start.microsecond)):
            continue
        statistics.append(
            StatisticData(
                start=point.start,
                state=point.value,
                sum=running_sum,
            )
        )
    return statistics


def _metadata_for_metric(
    meter: MeterSnapshot, metric: MetricSnapshot
) -> StatisticMetaData:
    """Return current Recorder metadata for one imported metric."""
    if metric.kind is MetricKind.HEAT_VOLUME:
        suffix = "heat volume"
        unit_class = VolumeConverter.UNIT_CLASS
    else:
        suffix = "consumption"
        unit_class = (
            EnergyConverter.UNIT_CLASS
            if meter.kind is MeterKind.ENERGY
            else VolumeConverter.UNIT_CLASS
        )
    return StatisticMetaData(
        statistic_id=_statistic_id(meter.meter_key, metric.kind),
        source=DOMAIN,
        name=f"{meter.device_name} {suffix}",
        unit_of_measurement=metric.native_unit,
        unit_class=unit_class,
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
    )


def _statistic_id(meter_key: str, metric_kind: MetricKind) -> str:
    """Return an opaque, stable external statistic ID."""
    suffix = "heat_volume" if metric_kind is MetricKind.HEAT_VOLUME else "consumption"
    return f"{DOMAIN}:{meter_key}_{suffix}"
