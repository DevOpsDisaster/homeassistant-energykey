"""Data coordinator for the EnergyKey integration."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EnergyKeyAuthError,
    EnergyKeyClient,
    EnergyKeyConnectionError,
    EnergyKeyError,
    EnergyKeyProtocolError,
)
from .const import (
    DATA_UPDATE_INTERVAL,
    HISTORY_MONTHS,
    MAX_HISTORY_REQUESTS,
    RECENT_HISTORY_DAYS,
)
from .models import (
    ConsumptionPoint,
    ConsumptionResult,
    ConsumptionSeries,
    ConsumptionView,
    EnergyKeyData,
    EnergyKeyMeter,
    MeterKind,
    MeterSnapshot,
    MetricKind,
    MetricSnapshot,
)
from .statistics import async_import_statistics
from .storage import EnergyKeyStore

_LOGGER = logging.getLogger(__name__)

_UNIT_MAP: dict[str, tuple[MeterKind, str]] = {
    "cubicmeter": (MeterKind.WATER, UnitOfVolume.CUBIC_METERS),
    "m3": (MeterKind.WATER, UnitOfVolume.CUBIC_METERS),
    "liter": (MeterKind.WATER, UnitOfVolume.LITERS),
    "litre": (MeterKind.WATER, UnitOfVolume.LITERS),
    "joule": (MeterKind.ENERGY, UnitOfEnergy.JOULE),
    "j": (MeterKind.ENERGY, UnitOfEnergy.JOULE),
    "kilojoule": (MeterKind.ENERGY, UnitOfEnergy.KILO_JOULE),
    "kj": (MeterKind.ENERGY, UnitOfEnergy.KILO_JOULE),
    "megajoule": (MeterKind.ENERGY, UnitOfEnergy.MEGA_JOULE),
    "mj": (MeterKind.ENERGY, UnitOfEnergy.MEGA_JOULE),
    "gigajoule": (MeterKind.ENERGY, UnitOfEnergy.GIGA_JOULE),
    "gj": (MeterKind.ENERGY, UnitOfEnergy.GIGA_JOULE),
    "watthour": (MeterKind.ENERGY, UnitOfEnergy.WATT_HOUR),
    "wh": (MeterKind.ENERGY, UnitOfEnergy.WATT_HOUR),
    "kilowatthour": (MeterKind.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "kwh": (MeterKind.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "megawatthour": (MeterKind.ENERGY, UnitOfEnergy.MEGA_WATT_HOUR),
    "mwh": (MeterKind.ENERGY, UnitOfEnergy.MEGA_WATT_HOUR),
}

_TEMPERATURE_UNIT_MAP: dict[str, str] = {
    "c": UnitOfTemperature.CELSIUS,
    "celcius": UnitOfTemperature.CELSIUS,
    "celsius": UnitOfTemperature.CELSIUS,
}


@dataclass(frozen=True, slots=True)
class _MeterPlan:
    """Views selected for one active EnergyKey meter."""

    meter: EnergyKeyMeter
    consumption_view: ConsumptionView
    volume_view: ConsumptionView | None = None
    temperature_view: ConsumptionView | None = None


def _diagnostic_exception_name(exception: BaseException | None) -> str:
    """Return the deepest non-sensitive exception class in a failure chain."""
    if exception is None:
        return "unknown"
    current = exception
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        nested = current.__cause__ or current.__context__
        if nested is None:
            break
        current = nested
    return type(current).__name__


class EnergyKeyCoordinator(DataUpdateCoordinator[EnergyKeyData]):
    """Coordinate meter discovery, history merging, and daily refreshes."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: EnergyKeyClient,
        store: EnergyKeyStore,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="EnergyKey",
            update_interval=DATA_UPDATE_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.store = store
        self._supported: list[_MeterPlan] = []
        self._history_semaphore = asyncio.Semaphore(MAX_HISTORY_REQUESTS)
        self.unsupported_meter_count = 0
        self.history_error_count = 0
        self.last_successful_refresh: datetime | None = None
        self.last_refresh_duration_seconds: float | None = None
        self.refresh_attempts = 0
        self.consecutive_refresh_failures = 0
        self.last_refresh_error: str | None = None
        self.discovered_meters: tuple[MeterSnapshot, ...] = ()

    async def async_refresh(self) -> None:
        """Refresh data and retain privacy-safe operational measurements."""
        self.refresh_attempts += 1
        started = monotonic()
        await super().async_refresh()
        self.last_refresh_duration_seconds = round(monotonic() - started, 3)
        if self.last_update_success:
            self.consecutive_refresh_failures = 0
            self.last_refresh_error = None
            return
        self.consecutive_refresh_failures += 1
        exception = self.last_exception
        self.last_refresh_error = _diagnostic_exception_name(exception)

    async def async_prepare(self) -> None:
        """Discover devices without blocking setup on consumption history."""
        await self._async_setup()
        discovered: list[MeterSnapshot] = []
        for plan in self._supported:
            normalized = _view_unit(plan.consumption_view)
            if normalized is None:
                raise RuntimeError("Prepared consumption view has an unsupported unit")
            kind, native_unit = normalized
            discovered.append(
                MeterSnapshot(
                    meter_key=plan.meter.key,
                    device_name="",
                    kind=kind,
                    native_unit=native_unit,
                    metrics=(),
                    portal_name=plan.meter.portal_name,
                    meter_number=plan.meter.meter_number,
                )
            )
        self.discovered_meters = tuple(_assign_device_names(discovered))
        self.async_set_updated_data(EnergyKeyData(meters=()))

    async def _async_setup(self) -> None:
        """Discover meters and select only unambiguous supported views."""
        try:
            meters = await self.client.async_get_meters()
            supported: list[_MeterPlan] = []
            unsupported = 0
            for meter in meters:
                views = await self.client.async_get_views(meter)
                consumption_view = _select_supported_view(views, meter.kind_hint)
                if consumption_view is None:
                    unsupported += 1
                    continue
                supported.append(
                    _MeterPlan(
                        meter=meter,
                        consumption_view=consumption_view,
                        volume_view=(
                            _select_heat_volume_view(views)
                            if meter.kind_hint is MeterKind.ENERGY
                            else None
                        ),
                        temperature_view=(
                            _select_temperature_view(views)
                            if meter.kind_hint is MeterKind.ENERGY
                            else None
                        ),
                    )
                )
            if not supported:
                raise EnergyKeyProtocolError(
                    "No EnergyKey meters expose an unambiguous daily consumption view"
                )
            self._supported = supported
            self.unsupported_meter_count = unsupported
        except EnergyKeyAuthError as err:
            raise ConfigEntryAuthFailed("The EnergyKey session has expired") from err
        except EnergyKeyError as err:
            raise UpdateFailed(f"Could not discover EnergyKey meters: {err}") from err

    async def _async_update_data(self) -> EnergyKeyData:
        """Fetch recent data, repair history, and create immutable snapshots."""
        results = await asyncio.gather(
            *(self._async_update_meter(plan) for plan in self._supported),
            return_exceptions=True,
        )
        snapshots: list[MeterSnapshot] = []
        unsupported_keys: set[str] = set()
        for plan, result in zip(self._supported, results, strict=True):
            meter = plan.meter
            if isinstance(result, EnergyKeyAuthError):
                raise ConfigEntryAuthFailed(
                    "The EnergyKey session has expired"
                ) from result
            if isinstance(result, EnergyKeyConnectionError):
                raise UpdateFailed(
                    f"Could not update EnergyKey data: {result}"
                ) from result
            if isinstance(result, EnergyKeyProtocolError):
                if self.last_successful_refresh is None:
                    _LOGGER.warning(
                        "Skipping an unsupported EnergyKey meter response: %s", result
                    )
                    unsupported_keys.add(meter.key)
                    self.unsupported_meter_count += 1
                    continue
                raise UpdateFailed(
                    f"Could not update EnergyKey data: {result}"
                ) from result
            if isinstance(result, BaseException):
                raise result
            snapshots.append(result)

        if unsupported_keys:
            self._supported = [
                candidate
                for candidate in self._supported
                if candidate.meter.key not in unsupported_keys
            ]
        if not snapshots:
            raise UpdateFailed("EnergyKey returned no supported meter data")

        named_snapshots = _assign_device_names(snapshots)
        histories = {
            _history_key(snapshot.meter_key, metric.kind): metric.history
            for snapshot in named_snapshots
            for metric in snapshot.metrics
        }
        offsets = {
            _history_key(snapshot.meter_key, metric.kind): metric.statistics_offset
            for snapshot in named_snapshots
            for metric in snapshot.metrics
            if metric.kind in {MetricKind.ACTUAL_CONSUMPTION, MetricKind.HEAT_VOLUME}
        }
        await self.store.async_set_histories_and_offsets(histories, offsets)
        data = EnergyKeyData(meters=tuple(named_snapshots))
        async_import_statistics(self.hass, data)
        await self.store.async_set_cookies(self.client.cookie_state)
        self.last_successful_refresh = datetime.now(UTC)
        return data

    async def _async_fetch_view_results(
        self,
        meter: EnergyKeyMeter,
        view: ConsumptionView,
        history_metrics: tuple[MetricKind, ...],
        history_start: date,
        recent_start: date,
        recent_end: date,
        local_today: date,
        timezone: ZoneInfo,
    ) -> list[ConsumptionResult]:
        """Fetch recent data and any missing calendar-month chunks for one view."""
        recent_result = await self.client.async_get_consumption(
            meter,
            view,
            _local_midnight(recent_start, timezone),
            _local_midnight(recent_end, timezone),
        )
        available_metrics = tuple(
            metric_kind
            for metric_kind in history_metrics
            if _series_for_metric(recent_result, metric_kind) is not None
        )
        if not available_metrics:
            return [recent_result]
        histories = tuple(
            self.store.history(_history_key(meter.key, metric_kind))
            for metric_kind in available_metrics
        )
        missing_windows = _missing_month_windows_many(
            histories,
            history_start,
            recent_start,
            local_today,
            timezone,
        )
        if not missing_windows:
            return [recent_result]

        async def fetch_window(start: datetime, end: datetime) -> ConsumptionResult:
            async with self._history_semaphore:
                return await self.client.async_get_consumption(meter, view, start, end)

        older_results = await asyncio.gather(
            *(fetch_window(start, end) for start, end in missing_windows),
            return_exceptions=True,
        )
        results = [recent_result]
        for result in older_results:
            if isinstance(result, BaseException):
                if isinstance(result, EnergyKeyAuthError):
                    raise result
                self.history_error_count += 1
                continue
            results.append(result)
        return results

    async def _async_update_meter(self, plan: _MeterPlan) -> MeterSnapshot:
        """Update every supported metric for one meter."""
        meter = plan.meter
        timezone = _home_assistant_timezone(self.hass.config.time_zone)
        local_today = datetime.now(timezone).date()
        history_start = _month_start(_shift_months(local_today, -(HISTORY_MONTHS - 1)))
        recent_start = local_today - timedelta(days=RECENT_HISTORY_DAYS)
        recent_end = local_today + timedelta(days=1)
        prune_before = _local_midnight(history_start, timezone)

        fetches = [
            self._async_fetch_view_results(
                meter,
                plan.consumption_view,
                (
                    MetricKind.ACTUAL_CONSUMPTION,
                    MetricKind.EXPECTED_CONSUMPTION,
                ),
                history_start,
                recent_start,
                recent_end,
                local_today,
                timezone,
            )
        ]
        if plan.volume_view is not None:
            fetches.append(
                self._async_fetch_view_results(
                    meter,
                    plan.volume_view,
                    (MetricKind.HEAT_VOLUME,),
                    history_start,
                    recent_start,
                    recent_end,
                    local_today,
                    timezone,
                )
            )
        if plan.temperature_view is not None:
            fetches.append(
                self._async_fetch_view_results(
                    meter,
                    plan.temperature_view,
                    (
                        MetricKind.FLOW_TEMPERATURE,
                        MetricKind.RETURN_TEMPERATURE,
                    ),
                    history_start,
                    recent_start,
                    recent_end,
                    local_today,
                    timezone,
                )
            )

        fetched_results = await asyncio.gather(*fetches, return_exceptions=True)
        primary_raw = fetched_results[0]
        if isinstance(primary_raw, BaseException):
            raise primary_raw
        primary_results = _matching_unit_results(primary_raw, _normalized_unit)
        kind, native_unit = _normalized_unit(primary_results[0])

        metrics: list[MetricSnapshot] = []
        actual = self._build_metric(
            meter.key,
            MetricKind.ACTUAL_CONSUMPTION,
            native_unit,
            primary_results,
            lambda result: result.usage_series,
            prune_before,
            require_complete=True,
            required=True,
            statistics=True,
        )
        assert actual is not None
        metrics.append(actual)
        expected = self._build_metric(
            meter.key,
            MetricKind.EXPECTED_CONSUMPTION,
            native_unit,
            primary_results,
            lambda result: result.budget_series,
            prune_before,
            require_complete=True,
        )
        if expected is not None:
            metrics.append(expected)

        result_index = 1
        if plan.volume_view is not None:
            volume_raw = fetched_results[result_index]
            result_index += 1
            volume_results = self._optional_results(
                volume_raw, _normalized_unit, MeterKind.WATER
            )
            if volume_results:
                _volume_kind, volume_unit = _normalized_unit(volume_results[0])
                volume = self._build_metric(
                    meter.key,
                    MetricKind.HEAT_VOLUME,
                    volume_unit,
                    volume_results,
                    lambda result: result.usage_series,
                    prune_before,
                    require_complete=True,
                    statistics=True,
                )
                if volume is not None:
                    metrics.append(volume)

        if plan.temperature_view is not None:
            temperature_raw = fetched_results[result_index]
            temperature_results = self._optional_results(
                temperature_raw, _normalized_temperature_unit
            )
            if temperature_results:
                temperature_unit = _normalized_temperature_unit(temperature_results[0])
                completed_usage_periods = {
                    (point.start, point.end)
                    for point in actual.history
                    if point.complete
                }
                for metric_kind, token in (
                    (MetricKind.FLOW_TEMPERATURE, "forwardVolumeTemp"),
                    (MetricKind.RETURN_TEMPERATURE, "returnVolumeTemp"),
                ):
                    temperature = self._build_metric(
                        meter.key,
                        metric_kind,
                        temperature_unit,
                        temperature_results,
                        lambda result, token=token: result.matching_series(token),
                        prune_before,
                        require_complete=False,
                        completed_periods=completed_usage_periods,
                    )
                    if temperature is not None:
                        metrics.append(temperature)

        return MeterSnapshot(
            meter_key=meter.key,
            device_name="",
            kind=kind,
            native_unit=native_unit,
            metrics=tuple(metrics),
            portal_name=meter.portal_name,
            meter_number=meter.meter_number,
        )

    def _optional_results(
        self,
        raw: list[ConsumptionResult] | BaseException,
        normalizer: Callable[[ConsumptionResult], object],
        expected_kind: MeterKind | None = None,
    ) -> list[ConsumptionResult]:
        if isinstance(raw, EnergyKeyAuthError):
            raise raw
        if isinstance(raw, BaseException):
            if self.last_successful_refresh is not None:
                raise raw
            self.history_error_count += 1
            return []
        try:
            results = _matching_unit_results(raw, normalizer)
        except EnergyKeyProtocolError:
            if self.last_successful_refresh is not None:
                raise
            self.history_error_count += 1
            return []
        if expected_kind is not None:
            kind, _unit = _normalized_unit(results[0])
            if kind is not expected_kind:
                return []
        return results

    def _build_metric(
        self,
        meter_key: str,
        metric_kind: MetricKind,
        native_unit: str,
        results: list[ConsumptionResult],
        selector: Callable[[ConsumptionResult], ConsumptionSeries | None],
        prune_before: datetime,
        *,
        require_complete: bool,
        required: bool = False,
        statistics: bool = False,
        completed_periods: set[tuple[datetime, datetime]] | None = None,
    ) -> MetricSnapshot | None:
        history_key = _history_key(meter_key, metric_kind)
        stored = self.store.history(history_key)
        recent_series = selector(results[0])
        if recent_series is None:
            if required or stored:
                raise EnergyKeyProtocolError(
                    "The EnergyKey response has no required consumption series"
                )
            return None
        fetched: list[ConsumptionPoint] = []
        for result in results:
            series = selector(result)
            if series is not None:
                fetched.extend(series.points)
        offset = self.store.statistics_offset(history_key) if statistics else 0.0
        history, offset = _merge_history_and_offset(
            stored, fetched, prune_before, offset
        )
        if not history:
            if required:
                raise EnergyKeyProtocolError("EnergyKey returned no usable history")
            return None
        if completed_periods is not None:
            candidates = [
                point
                for point in history
                if (point.start, point.end) in completed_periods
            ]
        elif require_complete:
            candidates = [point for point in history if point.complete]
        else:
            candidates = list(history)
        latest = max(
            candidates, key=lambda point: (point.end, point.start), default=None
        )
        return MetricSnapshot(
            kind=metric_kind,
            native_unit=native_unit,
            latest_value=latest.value if latest else None,
            latest_period_start=latest.start if latest else None,
            latest_period_end=latest.end if latest else None,
            history=history,
            statistics_offset=offset,
        )


def _select_supported_view(
    views: list[ConsumptionView], kind_hint: MeterKind | None = None
) -> ConsumptionView | None:
    candidates: list[tuple[int, str, ConsumptionView]] = []
    for view in views:
        normalized = _view_unit(view)
        if (
            view.zoom_level is None
            or normalized is None
            or (kind_hint is not None and normalized[0] is not kind_hint)
        ):
            continue
        name = _canonical(view.name_type)
        score = 100 if "usageconsumption" in name else 0
        if "consumption" in name or "usage" in name:
            score += 25
        if "lastthreeyears" in name:
            score -= 50
        candidates.append((score, view.view_id, view))
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][2]


def _select_heat_volume_view(
    views: list[ConsumptionView],
) -> ConsumptionView | None:
    """Select the current actual district-heating volume view."""
    candidates = [
        view
        for view in views
        if view.zoom_level is not None
        and (_view_unit(view) or (None, None))[0] is MeterKind.WATER
        and "volum" in _canonical(view.name_type)
        and "lastthreeyears" not in _canonical(view.name_type)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _select_temperature_view(
    views: list[ConsumptionView],
) -> ConsumptionView | None:
    """Select the combined flow/return temperature view."""
    candidates = [
        view
        for view in views
        if view.zoom_level is not None
        and _view_temperature_unit(view) is not None
        and "returntempwithtemp" in _canonical(view.name_type)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _view_unit(view: ConsumptionView) -> tuple[MeterKind, str] | None:
    for candidate in (view.unit_id, view.unit_name):
        if result := _UNIT_MAP.get(_canonical(candidate)):
            return result
    return None


def _normalized_unit(result: ConsumptionResult) -> tuple[MeterKind, str]:
    for candidate in (result.unit_id, result.unit_name):
        if normalized := _UNIT_MAP.get(_canonical(candidate)):
            return normalized
    raise EnergyKeyProtocolError("EnergyKey returned an unsupported consumption unit")


def _view_temperature_unit(view: ConsumptionView) -> str | None:
    for candidate in (view.unit_id, view.unit_name):
        canonical = _canonical(candidate)
        if normalized := _TEMPERATURE_UNIT_MAP.get(canonical):
            return normalized
        for key, value in _TEMPERATURE_UNIT_MAP.items():
            if canonical.endswith(key):
                return value
    return None


def _normalized_temperature_unit(result: ConsumptionResult) -> str:
    for candidate in (result.unit_id, result.unit_name):
        canonical = _canonical(candidate)
        if normalized := _TEMPERATURE_UNIT_MAP.get(canonical):
            return normalized
        for key, value in _TEMPERATURE_UNIT_MAP.items():
            if canonical.endswith(key):
                return value
    raise EnergyKeyProtocolError("EnergyKey returned an unsupported temperature unit")


def _matching_unit_results(
    results: list[ConsumptionResult],
    normalizer: Callable[[ConsumptionResult], object],
) -> list[ConsumptionResult]:
    expected = normalizer(results[0])
    matching = [results[0]]
    for result in results[1:]:
        try:
            if normalizer(result) == expected:
                matching.append(result)
        except EnergyKeyProtocolError:
            continue
    return matching


def _history_key(meter_key: str, metric_kind: MetricKind) -> str:
    if metric_kind is MetricKind.ACTUAL_CONSUMPTION:
        return meter_key
    return f"{meter_key}:{metric_kind.value}"


def _series_for_metric(
    result: ConsumptionResult, metric_kind: MetricKind
) -> ConsumptionSeries | None:
    """Select a series using only verified EnergyKey internal keys."""
    if metric_kind in {
        MetricKind.ACTUAL_CONSUMPTION,
        MetricKind.HEAT_VOLUME,
    }:
        return result.usage_series
    if metric_kind is MetricKind.EXPECTED_CONSUMPTION:
        return result.budget_series
    if metric_kind is MetricKind.FLOW_TEMPERATURE:
        return result.matching_series("forwardVolumeTemp")
    if metric_kind is MetricKind.RETURN_TEMPERATURE:
        return result.matching_series("returnVolumeTemp")
    return None


def _merge_history(
    stored: tuple[ConsumptionPoint, ...],
    fetched: list[ConsumptionPoint],
    prune_before: datetime,
) -> tuple[ConsumptionPoint, ...]:
    merged = {(point.start, point.end): point for point in stored}
    merged.update({(point.start, point.end): point for point in fetched})
    return tuple(
        point
        for point in sorted(merged.values(), key=lambda item: (item.start, item.end))
        if point.end >= prune_before
    )


def _merge_history_and_offset(
    stored: tuple[ConsumptionPoint, ...],
    fetched: list[ConsumptionPoint],
    prune_before: datetime,
    offset: float,
) -> tuple[tuple[ConsumptionPoint, ...], float]:
    removed = [point for point in stored if point.end < prune_before and point.complete]
    return (
        _merge_history(stored, fetched, prune_before),
        offset + sum(point.value for point in removed),
    )


def _missing_month_windows(
    stored: tuple[ConsumptionPoint, ...],
    history_start: date,
    recent_start: date,
    local_today: date,
    timezone: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    return _missing_month_windows_many(
        (stored,), history_start, recent_start, local_today, timezone
    )


def _missing_month_windows_many(
    histories: tuple[tuple[ConsumptionPoint, ...], ...],
    history_start: date,
    recent_start: date,
    local_today: date,
    timezone: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    month_sets = [
        {point.start.astimezone(timezone).date().replace(day=1) for point in history}
        for history in histories
    ]
    stored_months = set.intersection(*month_sets) if month_sets else set()
    windows: list[tuple[datetime, datetime]] = []
    cursor = history_start
    while cursor < recent_start:
        next_month = _shift_months(cursor, 1)
        if cursor not in stored_months:
            windows.append(
                (
                    _local_midnight(cursor, timezone),
                    _local_midnight(
                        min(next_month, local_today + timedelta(days=1)), timezone
                    ),
                )
            )
        cursor = next_month
    return windows


def _assign_device_names(snapshots: list[MeterSnapshot]) -> list[MeterSnapshot]:
    counts = Counter(snapshot.kind for snapshot in snapshots)
    seen: Counter[MeterKind] = Counter()
    result: list[MeterSnapshot] = []
    for snapshot in sorted(
        snapshots, key=lambda item: (item.kind.value, item.meter_key)
    ):
        seen[snapshot.kind] += 1
        label = "Heat meter" if snapshot.kind is MeterKind.ENERGY else "Water meter"
        if snapshot.portal_name and snapshot.portal_name != snapshot.meter_number:
            label = f"{label} – {snapshot.portal_name}"
        if snapshot.meter_number:
            label = f"{label} ({snapshot.meter_number})"
        elif counts[snapshot.kind] > 1:
            label = f"{label} {seen[snapshot.kind]}"
        result.append(replace(snapshot, device_name=label))
    return result


def _home_assistant_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _local_midnight(value: date, timezone: ZoneInfo) -> datetime:
    return datetime.combine(value, time.min, timezone).astimezone(UTC)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_months(value: date, months: int) -> date:
    absolute = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(absolute, 12)
    return date(year, zero_based_month + 1, 1)


def _canonical(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())
