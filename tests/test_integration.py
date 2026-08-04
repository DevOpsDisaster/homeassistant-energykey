"""Integration-level tests for setup, entities, storage, and diagnostics."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energykey.api import _parse_consumption
from custom_components.energykey.const import (
    CONF_ACCOUNT_ID,
    CONF_BASE_URL,
    CONF_COOKIES,
    DOMAIN,
)
from custom_components.energykey.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.energykey.models import (
    ConsumptionView,
    EnergyKeyMeter,
    MeterKind,
)
from custom_components.energykey.sensor import EnergyKeySensor
from custom_components.energykey.storage import EnergyKeyStore

BASE_URL = "https://tenant.wt.energykey.dk"
ACCOUNT_ID = "a" * 64
BOOTSTRAP_COOKIES = {
    "wt3SessionId": "never-log-this-session",
    "wt3login": "never-log-this-login",
}


class FakeEnergyKeyClient:
    """Deterministic client backed by synthetic response fixtures."""

    def __init__(self, load_fixture) -> None:
        self.water_meter = EnergyKeyMeter(
            "raw-water-id",
            "1" * 64,
            "Garden meter",
            "12345678",
            MeterKind.WATER,
        )
        self.heat_meter = EnergyKeyMeter(
            "raw-heat-id",
            "2" * 64,
            None,
            "87654321",
            MeterKind.ENERGY,
        )
        self.water_view = ConsumptionView(
            "raw-water-view",
            "usageConsumption",
            "cubic_meter",
            "m3",
            "month_by_days",
        )
        self.heat_view = ConsumptionView(
            "raw-heat-view",
            "usageConsumption",
            "kWh",
            "kWh",
            "month_by_days",
        )
        self.heat_volume_view = ConsumptionView(
            "raw-heat-volume-view",
            "consumptionview.views.volumen",
            "cubic_meter",
            "m3",
            "month_by_days",
        )
        self.heat_temperature_view = ConsumptionView(
            "raw-heat-temperature-view",
            "consumptionview.views.returntempwithtemp",
            "celcius",
            "celcius",
            "month_by_days",
        )
        self.water_result = _parse_consumption(
            load_fixture("water_consumption.json"), self.water_view
        )
        self.heat_result = _parse_consumption(
            load_fixture("heat_consumption.json"), self.heat_view
        )
        self.heat_volume_result = _parse_consumption(
            load_fixture("heat_volume.json"), self.heat_volume_view
        )
        self.heat_temperature_result = _parse_consumption(
            load_fixture("heat_temperature.json"), self.heat_temperature_view
        )
        self.async_heartbeat = AsyncMock()
        self.detach_called = False
        self.authentication_rejected = False
        self.consumption_started = asyncio.Event()
        self.consumption_gate: asyncio.Event | None = None

    @property
    def cookie_state(self) -> dict[str, str]:
        return {
            "wt3SessionId": "rotated-session",
            "wt3login": "rotated-login",
        }

    @property
    def seconds_since_activity(self) -> float:
        return 0

    async def async_get_meters(self) -> list[EnergyKeyMeter]:
        return [self.water_meter, self.heat_meter]

    async def async_get_views(self, meter: EnergyKeyMeter) -> list[ConsumptionView]:
        if meter == self.water_meter:
            return [self.water_view]
        return [self.heat_view, self.heat_volume_view, self.heat_temperature_view]

    async def async_get_consumption(self, meter, view, start, end):
        self.consumption_started.set()
        if self.consumption_gate is not None:
            await self.consumption_gate.wait()
        if meter == self.water_meter:
            return self.water_result
        if view == self.heat_volume_view:
            return self.heat_volume_result
        if view == self.heat_temperature_view:
            return self.heat_temperature_result
        return self.heat_result

    def detach(self) -> None:
        self.detach_called = True


async def _setup_entry(hass, load_fixture):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_ID,
        title="EnergyKey test",
        data={
            CONF_BASE_URL: BASE_URL,
            CONF_COOKIES: BOOTSTRAP_COOKIES,
            CONF_ACCOUNT_ID: ACCOUNT_ID,
        },
    )
    entry.add_to_hass(hass)
    fake_client = FakeEnergyKeyClient(load_fixture)
    with patch("custom_components.energykey.EnergyKeyClient", return_value=fake_client):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.runtime_data.data_refresh_task is not None
    await entry.runtime_data.data_refresh_task
    await hass.async_block_till_done()
    return entry, fake_client


async def test_setup_returns_before_consumption_history_download(
    hass, load_fixture
) -> None:
    """Device discovery completes before the initial data refresh."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_ID,
        title="EnergyKey test",
        data={
            CONF_BASE_URL: BASE_URL,
            CONF_COOKIES: BOOTSTRAP_COOKIES,
            CONF_ACCOUNT_ID: ACCOUNT_ID,
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for translation_key in ("meter_reading", "last_reading"):
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"deprecated_{translation_key}",
            config_entry=entry,
            translation_key=translation_key,
        )
    client = FakeEnergyKeyClient(load_fixture)
    client.consumption_gate = asyncio.Event()

    with patch("custom_components.energykey.EnergyKeyClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)

    await asyncio.wait_for(client.consumption_started.wait(), timeout=1)
    assert entry.runtime_data.data_refresh_task is not None
    assert not entry.runtime_data.data_refresh_task.done()
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 2
    entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert [item.translation_key for item in entries] == ["last_successful_heartbeat"]

    client.consumption_gate.set()
    await entry.runtime_data.data_refresh_task
    await hass.async_block_till_done()
    entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(entries) == 8
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_all_meters_and_sensor_metadata(hass, load_fixture) -> None:
    entry, client = await _setup_entry(hass, load_fixture)
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(entries) == 8
    assert EnergyKeySensor._unrecorded_attributes == frozenset({"data"})
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 2
    assert {
        (device.name, device.model, device.serial_number) for device in devices
    } == {
        ("Water meter – Garden meter (12345678)", "Water meter", "12345678"),
        ("Heat meter (87654321)", "Heat meter", "87654321"),
    }

    # There are two entries per key; inspect all of them instead of relying on IDs.
    daily = [
        item for item in entries if item.translation_key == "latest_daily_consumption"
    ]
    expected = [
        item for item in entries if item.translation_key == "expected_daily_consumption"
    ]
    heat_volume = [
        item for item in entries if item.translation_key == "latest_daily_heat_volume"
    ]
    temperatures = [
        item
        for item in entries
        if item.translation_key
        in {"latest_flow_temperature", "latest_return_temperature"}
    ]
    heartbeat = [
        item for item in entries if item.translation_key == "last_successful_heartbeat"
    ]
    assert len(daily) == 2
    assert len(expected) == 2
    assert len(heat_volume) == 1
    assert len(temperatures) == 2
    assert len(heartbeat) == 1
    assert not any(
        item.translation_key in {"meter_reading", "last_reading"} for item in entries
    )
    assert heartbeat[0].disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert heartbeat[0].entity_category is EntityCategory.DIAGNOSTIC
    assert heartbeat[0].device_id is None
    assert all("raw-" not in item.unique_id for item in entries)
    assert all(
        "12345678" not in item.entity_id and "87654321" not in item.entity_id
        for item in entries
    )

    daily_states = [hass.states.get(item.entity_id) for item in daily]
    assert all("state_class" not in state.attributes for state in daily_states)
    assert all(state.attributes["data"] for state in daily_states)
    water_state = next(
        state
        for state in daily_states
        if state.attributes["device_class"] == SensorDeviceClass.WATER
    )
    assert water_state.attributes["data"][-1]["complete"] is False
    assert set(water_state.attributes["data"][-1]) == {
        "start",
        "end",
        "value",
        "complete",
    }

    expected_states = [hass.states.get(item.entity_id) for item in expected]
    assert {state.state for state in expected_states} == {"0.25", "7.9"}
    volume_state = hass.states.get(heat_volume[0].entity_id)
    assert volume_state.state == "2.7"
    assert volume_state.attributes["device_class"] == SensorDeviceClass.WATER
    assert volume_state.attributes["unit_of_measurement"] == "m³"
    temperature_states = [hass.states.get(item.entity_id) for item in temperatures]
    assert {state.state for state in temperature_states} == {"62.5", "34.2"}
    assert all(
        state.attributes["device_class"] == SensorDeviceClass.TEMPERATURE
        and "state_class" not in state.attributes
        and state.attributes["unit_of_measurement"] == "°C"
        for state in temperature_states
    )
    assert all(
        set(state.attributes["data"][-1]) == {"start", "end", "value", "complete"}
        for state in temperature_states
    )
    assert all(
        state.attributes["data"][-1]["value"] == 0 for state in temperature_states
    )
    assert all(
        state.attributes["data"][-1]["complete"] is False
        for state in temperature_states
    )

    first_data = entry.runtime_data.coordinator.data
    await entry.runtime_data.coordinator.async_refresh()
    assert entry.runtime_data.coordinator.data == first_data

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert client.detach_called


async def test_rotated_session_and_history_survive_restart(hass, load_fixture) -> None:
    entry, _ = await _setup_entry(hass, load_fixture)
    first_meter = entry.runtime_data.coordinator.data.meters[0]
    expected_history = first_meter.metrics[0].history
    restored = EnergyKeyStore(hass, entry.entry_id)
    await restored.async_load()
    assert restored.cookies["wt3SessionId"] == "rotated-session"
    meter_key = first_meter.meter_key
    assert restored.history(meter_key) == expected_history
    for metric in first_meter.metrics:
        history_key = (
            meter_key
            if metric.kind.value == "actual_consumption"
            else f"{meter_key}:{metric.kind.value}"
        )
        assert restored.history(history_key) == metric.history
        if metric.kind.value in {"actual_consumption", "heat_volume"}:
            assert restored.statistics_offset(history_key) == metric.statistics_offset
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_redacts_identifiers_and_values(hass, load_fixture) -> None:
    entry, _ = await _setup_entry(hass, load_fixture)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics, default=str)
    assert "never-log-this" not in serialized
    assert "rotated-session" not in serialized
    assert "raw-water" not in serialized
    assert "raw-heat" not in serialized
    assert "100.59" not in serialized
    assert "2500.4" not in serialized
    assert diagnostics["entry"][CONF_COOKIES] == "**REDACTED**"
    assert diagnostics["coordinator"]["unsupported_meter_count"] == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_store_ignores_malformed_history(hass) -> None:
    store = EnergyKeyStore(hass, "malformed-test")
    await store._store.async_save(  # noqa: SLF001
        {
            "cookies": {"wt3SessionId": "safe", "wt3login": "safe"},
            "history": {"opaque": [{"unexpected": "shape"}]},
        }
    )
    await store.async_load()
    assert store.history("opaque") == ()
    await store.async_remove()


async def test_store_mutators_round_trip_and_return_copies(hass) -> None:
    store = EnergyKeyStore(hass, "mutator-test")
    cookies = {"wt3SessionId": "synthetic", "wt3login": "synthetic"}
    await store.async_set_cookies(cookies)
    cookies["wt3SessionId"] = "changed-outside-store"
    assert store.cookies["wt3SessionId"] == "synthetic"

    await store.async_set_history("first", ())
    assert store.history_keys == ("first",)
    assert store.statistics_offset("missing") == 0
    await store.async_set_histories({"second": ()})
    await store.async_save()

    restored = EnergyKeyStore(hass, "mutator-test")
    await restored.async_load()
    assert restored.history_keys == ("second",)
    await restored.async_remove()
    assert restored.cookies == {}
    assert restored.history_keys == ()
