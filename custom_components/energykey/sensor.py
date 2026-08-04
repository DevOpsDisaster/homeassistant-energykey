"""Sensor platform for EnergyKey."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BASE_URL, DOMAIN, heartbeat_update_signal
from .coordinator import EnergyKeyCoordinator
from .models import MeterKind, MeterSnapshot, MetricKind
from .runtime import EnergyKeyRuntimeData


@dataclass(frozen=True, kw_only=True)
class EnergyKeySensorDescription(SensorEntityDescription):
    """Describe an EnergyKey sensor."""

    metric_kind: MetricKind | None = None


SENSOR_DESCRIPTIONS = (
    EnergyKeySensorDescription(
        key="latest_daily_consumption",
        translation_key="latest_daily_consumption",
        metric_kind=MetricKind.ACTUAL_CONSUMPTION,
    ),
    EnergyKeySensorDescription(
        key="expected_daily_consumption",
        translation_key="expected_daily_consumption",
        metric_kind=MetricKind.EXPECTED_CONSUMPTION,
    ),
    EnergyKeySensorDescription(
        key="latest_daily_heat_volume",
        translation_key="latest_daily_heat_volume",
        metric_kind=MetricKind.HEAT_VOLUME,
    ),
    EnergyKeySensorDescription(
        key="latest_flow_temperature",
        translation_key="latest_flow_temperature",
        metric_kind=MetricKind.FLOW_TEMPERATURE,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    EnergyKeySensorDescription(
        key="latest_return_temperature",
        translation_key="latest_return_temperature",
        metric_kind=MetricKind.RETURN_TEMPERATURE,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
)

REMOVED_SENSOR_TRANSLATION_KEYS = frozenset({"meter_reading", "last_reading"})

HEARTBEAT_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="last_successful_heartbeat",
    translation_key="last_successful_heartbeat",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[EnergyKeyRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnergyKey sensors from coordinator data."""
    coordinator = entry.runtime_data.coordinator
    entity_registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if registry_entry.translation_key in REMOVED_SENSOR_TRANSLATION_KEYS:
            entity_registry.async_remove(registry_entry.entity_id)

    known_entities: set[tuple[str, str]] = set()

    @callback
    def async_add_discovered_entities() -> None:
        """Add meter sensors after their live data shape has been verified."""
        new_entities: list[EnergyKeySensor] = []
        for meter in coordinator.data.meters:
            for description in SENSOR_DESCRIPTIONS:
                entity_key = (meter.meter_key, description.key)
                if entity_key in known_entities or (
                    description.metric_kind is not None
                    and meter.metric(description.metric_kind) is None
                ):
                    continue
                known_entities.add(entity_key)
                new_entities.append(
                    EnergyKeySensor(coordinator, entry, meter, description)
                )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(async_add_discovered_entities))
    async_add_discovered_entities()
    async_add_entities([EnergyKeyHeartbeatSensor(entry)])


class EnergyKeyHeartbeatSensor(SensorEntity):
    """Expose the latest successful session heartbeat timestamp."""

    _attr_has_entity_name = True
    entity_description = HEARTBEAT_SENSOR_DESCRIPTION

    def __init__(self, entry: ConfigEntry[EnergyKeyRuntimeData]) -> None:
        """Initialize the config-entry-level diagnostic sensor."""
        self._entry = entry
        self._attr_unique_id = (
            f"{entry.unique_id or entry.entry_id}_last_successful_heartbeat"
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the last successful heartbeat in UTC."""
        return self._entry.runtime_data.last_successful_heartbeat

    async def async_added_to_hass(self) -> None:
        """Subscribe to heartbeat updates after the entity is enabled."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                heartbeat_update_signal(self._entry.entry_id),
                self._async_handle_heartbeat_update,
            )
        )

    @callback
    def _async_handle_heartbeat_update(self) -> None:
        """Write a new state after a successful heartbeat."""
        self.async_write_ha_state()


class EnergyKeySensor(CoordinatorEntity[EnergyKeyCoordinator], SensorEntity):
    """Represent one EnergyKey meter value."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"data"})
    entity_description: EnergyKeySensorDescription

    def __init__(
        self,
        coordinator: EnergyKeyCoordinator,
        entry: ConfigEntry[EnergyKeyRuntimeData],
        meter: MeterSnapshot,
        description: EnergyKeySensorDescription,
    ) -> None:
        """Initialize a coordinator-backed sensor."""
        super().__init__(coordinator, context=meter.meter_key)
        self.entity_description = description
        self._meter_key = meter.meter_key
        self._attr_unique_id = f"{meter.meter_key}_{description.key}"
        self.entity_id = f"sensor.energykey_{meter.meter_key[:12]}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter.meter_key)},
            name=meter.device_name,
            manufacturer="EnergyKey",
            model="Heat meter" if meter.kind is MeterKind.ENERGY else "Water meter",
            serial_number=meter.meter_number,
            configuration_url=str(entry.data[CONF_BASE_URL]),
        )
        metric = (
            meter.metric(description.metric_kind)
            if description.metric_kind is not None
            else None
        )
        if description.device_class is None:
            if description.metric_kind is MetricKind.HEAT_VOLUME:
                self._attr_device_class = SensorDeviceClass.WATER
            else:
                self._attr_device_class = (
                    SensorDeviceClass.ENERGY
                    if meter.kind is MeterKind.ENERGY
                    else SensorDeviceClass.WATER
                )
        if metric is not None:
            self._attr_native_unit_of_measurement = metric.native_unit

    @property
    def native_value(self) -> StateType | datetime:
        """Return the current value from coordinator memory."""
        meter = self._meter
        if meter is None:
            return None
        metric_kind = self.entity_description.metric_kind
        if metric_kind is None:
            return None
        metric = meter.metric(metric_kind)
        return metric.latest_value if metric is not None else None

    @property
    def available(self) -> bool:
        """Return whether both the coordinator and this meter are available."""
        meter = self._meter
        if not super().available or meter is None:
            return False
        metric_kind = self.entity_description.metric_kind
        return metric_kind is None or meter.metric(metric_kind) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose normalized portal history on the daily consumption sensor."""
        metric_kind = self.entity_description.metric_kind
        if metric_kind is None:
            return None
        meter = self._meter
        if meter is None:
            return None
        metric = meter.metric(metric_kind)
        if metric is None:
            return None
        if metric_kind in {
            MetricKind.FLOW_TEMPERATURE,
            MetricKind.RETURN_TEMPERATURE,
        }:
            actual = meter.metric(MetricKind.ACTUAL_CONSUMPTION)
            completed_periods = (
                {(point.start, point.end) for point in actual.history if point.complete}
                if actual is not None
                else set()
            )
            return {
                "data": [
                    {
                        "start": point.start.isoformat(),
                        "end": point.end.isoformat(),
                        "value": point.value,
                        "complete": (point.start, point.end) in completed_periods,
                    }
                    for point in metric.history
                ]
            }
        return {"data": [point.public_history() for point in metric.history]}

    @property
    def _meter(self) -> MeterSnapshot | None:
        return self.coordinator.data.meter(self._meter_key)
