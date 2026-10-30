"""TASK-0021 — the charge and discharge efficiency sensors (ADR-023 decision 2).

``sensor.py`` is loaded for real under a private package name with Home
Assistant stubbed only while it loads (like ``test_history_recent_equivalence``),
so this file neither clobbers nor leaks into the other modules' stubs. The
coordinator is a small fake that uses the real restore functions of
``calculation.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from datetime import UTC as UTC_TZ
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_BASE = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_PKG = "effy_efficiency_sensor_under_test"
UTC = UTC_TZ


class _Stub:
    """Base for the entity stubs: records the state writes."""

    writes = 0

    def async_write_ha_state(self) -> None:
        self.writes += 1


class _SensorEntity(_Stub):
    pass


class _RestoreEntity(_Stub):
    restored: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        return None

    last_state: Any = None

    async def async_get_last_state(self) -> Any:
        return self.last_state

    async def async_get_last_extra_data(self) -> Any:
        if self.restored is None:
            return None
        data = self.restored
        return type("Restored", (), {"as_dict": lambda _self: dict(data)})()


class _Registry:
    def __init__(self, known: dict[tuple[str, str, str], str]) -> None:
        self.known = known
        self.removed: list[str] = []

    def async_get_entity_id(self, domain: str, platform: str, unique_id: str) -> str | None:
        return self.known.get((domain, platform, unique_id))

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)


_registry: list[_Registry] = [_Registry({})]


def _load() -> tuple[ModuleType, ModuleType]:
    stub_names = [
        "homeassistant",
        "homeassistant.core",
        "homeassistant.util",
        "homeassistant.config_entries",
        "homeassistant.components",
        "homeassistant.components.sensor",
        "homeassistant.helpers",
        "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.event",
        "homeassistant.helpers.restore_state",
    ]
    module_names = [
        f"{_PKG}.{n}"
        for n in (
            "const",
            "effy_exports",
            "inputs",
            "catalog",
            "calculation",
            "sensor_utils",
            "coordinator",
            "sensor",
        )
    ]
    touched = [*stub_names, _PKG, *module_names]
    saved = {name: sys.modules.get(name) for name in touched}

    def stub(name: str, **attrs: Any) -> None:
        mod = ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod

    class _DeviceClass:
        POWER = "power"
        TIMESTAMP = "timestamp"

    class _StateClass:
        TOTAL_INCREASING = "total_increasing"
        TOTAL = "total"
        MEASUREMENT = "measurement"

    try:
        stub("homeassistant")
        stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
        stub("homeassistant.util", slugify=lambda s: s.lower().replace(".", "_"))
        stub("homeassistant.config_entries", ConfigEntry=object)
        stub("homeassistant.components")
        stub(
            "homeassistant.components.sensor",
            SensorDeviceClass=_DeviceClass,
            SensorEntity=_SensorEntity,
            SensorStateClass=_StateClass,
        )
        stub("homeassistant.helpers")
        stub("homeassistant.helpers.entity", DeviceInfo=dict)
        stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
        stub("homeassistant.helpers.entity_registry", async_get=lambda _hass: _registry[0])
        sys.modules["homeassistant.helpers"].entity_registry = sys.modules[  # type: ignore[attr-defined]
            "homeassistant.helpers.entity_registry"
        ]
        stub("homeassistant.helpers.event", async_track_state_change_event=lambda *a, **k: None)
        stub(
            "homeassistant.helpers.restore_state",
            ExtraStoredData=object,
            RestoreEntity=_RestoreEntity,
        )

        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(_BASE)]
        pkg.__package__ = _PKG
        sys.modules[_PKG] = pkg

        fake_coordinator = ModuleType(f"{_PKG}.coordinator")
        fake_coordinator.EffyCoordinator = object  # type: ignore[attr-defined]
        fake_coordinator.__package__ = _PKG
        sys.modules[f"{_PKG}.coordinator"] = fake_coordinator
        pkg.coordinator = fake_coordinator  # type: ignore[attr-defined]

        loaded: dict[str, ModuleType] = {}
        for name in (
            "const",
            "effy_exports",
            "inputs",
            "catalog",
            "calculation",
            "sensor_utils",
            "sensor",
        ):
            spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _BASE / f"{name}.py")
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            mod.__package__ = _PKG
            sys.modules[f"{_PKG}.{name}"] = mod
            setattr(pkg, name, mod)
            spec.loader.exec_module(mod)
            loaded[name] = mod
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return loaded["sensor"], loaded["calculation"]


_sensor, _calc = _load()


class _Entry:
    entry_id = "entry1"


class _FakeCoordinator:
    def __init__(
        self,
        *,
        grid_import: str | None = "sensor.grid_in",
        charge: str | None = "sensor.charge",
        discharge: str | None = "sensor.discharge",
        grid_export: str | None = "sensor.grid_out",
        standby_estimate: float | None = 20.0,
        battery_enabled: bool = True,
    ) -> None:
        self.grid_import_id, self.charge_id = grid_import, charge
        self.discharge_id, self.grid_export_id = discharge, grid_export
        self.standby_state = _calc.DrainState(estimate=standby_estimate)
        self.battery_enabled = battery_enabled
        self.battery_hysteresis = 30.0
        self.efficiency_state: Any = None
        self.battery_state: Any = None
        self.restore_calls: list[Any] = []
        self.battery_restore_calls: list[Any] = []
        self.subscribers: list[Callable[[Any], None]] = []
        self.standby_subscribers: list[Callable[[Any], None]] = []
        self.battery_subscribers: list[Callable[[Any], None]] = []
        self.unsubscribed = 0
        self.standby_unsubscribed = 0
        self.battery_unsubscribed = 0
        self.published: list[dict[str, Any]] = []
        self.unpublished = 0

    def register_published(self, descriptor: dict[str, Any]) -> Callable[[], None]:
        self.published.append(dict(descriptor))

        def unregister() -> None:
            self.unpublished += 1

        return unregister

    def restore_efficiency(self, data: Any) -> Any:
        self.restore_calls.append(data)
        restored = _calc.run_state_from_restore(data) if data else None
        self.efficiency_state = restored if restored is not None else _calc.RunState()
        return self.efficiency_state

    def restore_battery(self, data: Any) -> Any:
        self.battery_restore_calls.append(data)
        restored = _calc.valley_state_from_restore(data, self.battery_hysteresis) if data else None
        self.battery_state = restored if restored is not None else _calc.ValleyState()
        return self.battery_state

    def subscribe_efficiency(self, cb: Callable[[Any], None]) -> Callable[[], None]:
        self.subscribers.append(cb)

        def unsubscribe() -> None:
            self.unsubscribed += 1

        return unsubscribe

    def subscribe_standby(self, cb: Callable[[Any], None]) -> Callable[[], None]:
        self.standby_subscribers.append(cb)

        def unsubscribe() -> None:
            self.standby_unsubscribed += 1

        return unsubscribe

    def subscribe_battery(self, cb: Callable[[Any], None]) -> Callable[[], None]:
        self.battery_subscribers.append(cb)

        def unsubscribe() -> None:
            self.battery_unsubscribed += 1

        return unsubscribe


def _entity(kind: str = "charging", coordinator: _FakeCoordinator | None = None) -> Any:
    return _sensor.EffyEfficiencySensor(object(), _Entry(), coordinator or _FakeCoordinator(), kind)


def _machine(charge: float | None = 0.9512, discharge: float | None = 0.8801) -> Any:
    """A machine state with the given estimates (num/den chosen to give them)."""

    def estimate(value: float | None, kwh: float, runs: int, last: float, end: int) -> Any:
        if value is None:
            return _calc.EffEstimate()
        return _calc.EffEstimate(
            ratio=_calc.DecayedRatio(num=value * 10.0, den=10.0),
            counted_kwh=kwh,
            counted_runs=runs,
            last_value=last,
            last_end=datetime(2026, 1, 11, end, 0, tzinfo=UTC),
        )

    return _calc.RunState(
        charge=estimate(charge, 12.34567, 4, 0.94321, 3),
        discharge=estimate(discharge, 20.5, 7, 0.87654, 4),
        last_slot=datetime(2026, 1, 11, 5, 0, tzinfo=UTC),
    )


def _valley(value: float | None = 0.9) -> Any:
    if value is None:
        return _calc.ValleyState()
    begin = datetime(2026, 1, 11, 1, 0, tzinfo=UTC)
    end = datetime(2026, 1, 11, 3, 0, tzinfo=UTC)
    estimate = _calc.ValleyEstimate(
        ratio=_calc.DecayedRatio(num=value, den=1.0),
        counted_kwh=42.34567,
        counted_spans=6,
        last_value=0.92345,
        last_span_begin=begin,
        last_span_end=end,
    )
    return _calc.ValleyState(
        estimate=estimate,
        last_slot=datetime(2026, 1, 11, 5, 0, tzinfo=UTC),
    )


class TestEntityShape:
    @pytest.mark.parametrize(
        ("kind", "entity_id", "unique_id", "name"),
        [
            (
                "charging",
                "sensor.effy_charge_efficiency",
                "entry1_charge_efficiency",
                "Charge efficiency",
            ),
            (
                "discharging",
                "sensor.effy_discharge_efficiency",
                "entry1_discharge_efficiency",
                "Discharge efficiency",
            ),
        ],
    )
    def test_it_is_a_recorded_percent_measurement_with_a_stable_id(
        self, kind: str, entity_id: str, unique_id: str, name: str
    ) -> None:
        entity = _entity(kind)
        assert entity.entity_id == entity_id
        assert entity.unique_id == unique_id
        assert entity._attr_name == name
        assert entity._attr_native_unit_of_measurement == "%"
        assert entity._attr_state_class == "measurement"  # recorded: mean, min, max
        assert not hasattr(entity, "_attr_device_class")  # Home Assistant has none for it
        assert entity.device_info["identifiers"] == {("effy", "entry1")}

    def test_the_entity_id_cannot_be_renamed_by_home_assistant(self) -> None:
        entity = _entity()
        entity.entity_id = "sensor.something_else"
        assert entity.entity_id == "sensor.effy_charge_efficiency"

    def test_it_is_unavailable_not_unknown_until_the_first_measurement(self) -> None:
        entity = _entity()
        assert entity.available is False and entity.native_value is None
        entity._machine = _calc.RunState()  # restored from nothing
        assert entity.available is False and entity.native_value is None
        assert entity.extra_state_attributes is None

    def test_it_stays_unavailable_without_a_standby_value(self) -> None:
        for coordinator in (
            _FakeCoordinator(standby_estimate=None),
            _FakeCoordinator(),
        ):
            entity = _entity("charging", coordinator)
            entity._machine = _machine()
            assert entity.available is (coordinator.standby_state.estimate is not None)
        no_state = _FakeCoordinator()
        no_state.standby_state = None  # the standby sensor has not restored yet
        entity = _entity("charging", no_state)
        entity._machine = _machine()
        assert entity.available is False

    def test_the_state_is_the_estimate_in_percent_rounded_to_a_tenth(self) -> None:
        charge, discharge = _entity("charging"), _entity("discharging")
        charge._machine = discharge._machine = _machine()
        assert charge.available and discharge.available
        assert charge.native_value == 95.1
        assert discharge.native_value == 88.0

    def test_a_value_above_100_is_shown_as_it_is(self) -> None:
        entity = _entity()
        entity._machine = _machine(charge=1.0344)
        assert entity.native_value == 103.4

    def test_each_sensor_reads_its_own_estimate(self) -> None:
        discharge = _entity("discharging")
        discharge._machine = _machine(charge=None)  # no charge measurement yet
        assert discharge.available and discharge.native_value == 88.0
        charge = _entity("charging")
        charge._machine = _machine(charge=None)
        assert charge.available is False

    def test_the_attributes_show_provenance_on_the_scale_of_the_state(self) -> None:
        entity = _entity()
        entity._machine = _machine()
        assert entity.extra_state_attributes == {
            "last_counted_value": 94.3,
            "last_counted_end": "2026-01-11T03:00:00+00:00",
            "counted_kwh": 12.346,
            "counted_runs": 4,
            "last_processed_slot": "2026-01-11T05:00:00+00:00",
        }
        other = _entity("discharging")
        other._machine = _machine()
        assert other.extra_state_attributes["counted_runs"] == 7

    def test_the_whole_machine_with_its_exact_sums_is_what_survives_a_restart(self) -> None:
        entity = _entity()
        before = entity.extra_restore_state_data
        assert before is None
        entity._machine = _machine()
        after = entity.extra_restore_state_data
        assert after is not None
        stored = after.as_dict()
        assert stored == _calc.run_restore_data(entity._machine)
        assert stored["charging"]["num"] == pytest.approx(9.512)  # exact, not the rounded 95.1
        assert stored["discharging"]["counted_runs"] == 7  # the other sensor's part too
        assert stored["last_slot"] == "2026-01-11T05:00:00+00:00"


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_added_without_restored_data_starts_the_coordinator_empty(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity("charging", coordinator)
        await entity.async_added_to_hass()
        assert coordinator.restore_calls == [None]
        assert entity.available is False
        assert len(coordinator.subscribers) == 1 and len(coordinator.standby_subscribers) == 1

    @pytest.mark.asyncio
    async def test_added_with_restored_data_shows_the_estimate_at_once(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity("charging", coordinator)
        dump = _calc.run_restore_data(_machine())
        entity.restored = dump
        await entity.async_added_to_hass()
        assert coordinator.restore_calls == [dump]
        assert entity.available is True and entity.native_value == 95.1
        # a second restart before any job keeps the whole machine
        assert entity.extra_restore_state_data.as_dict() == dump

    @pytest.mark.asyncio
    async def test_unusable_restored_data_starts_empty(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity("charging", coordinator)
        entity.restored = {"charging": "garbage"}
        await entity.async_added_to_hass()
        assert entity.available is False and entity.native_value is None

    @pytest.mark.asyncio
    async def test_it_publishes_itself_in_the_configuration(self) -> None:
        for kind, series in (
            ("charging", "charge_efficiency"),
            ("discharging", "discharge_efficiency"),
        ):
            coordinator = _FakeCoordinator()
            entity = _entity(kind, coordinator)
            await entity.async_added_to_hass()
            assert coordinator.published == [
                {
                    "entity_id": entity.entity_id,
                    "series": series,
                    "source_entity_id": None,
                    "direction": "output",
                    "role": "bms",
                    "aggregations": ["mean", "min", "max"],
                }
            ]
            await entity.async_will_remove_from_hass()
            assert coordinator.unpublished == 1

    @pytest.mark.asyncio
    async def test_a_new_state_from_the_coordinator_is_published(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity("charging", coordinator)
        await entity.async_added_to_hass()
        coordinator.subscribers[0](_machine())
        assert entity.writes == 1
        assert entity.available is True and entity.native_value == 95.1

    @pytest.mark.asyncio
    async def test_the_first_standby_value_makes_it_available(self) -> None:
        coordinator = _FakeCoordinator(standby_estimate=None)
        entity = _entity("charging", coordinator)
        await entity.async_added_to_hass()
        coordinator.subscribers[0](_machine())
        assert entity.available is False  # a measurement, but no standby value yet
        coordinator.standby_state = _calc.DrainState(estimate=18.0)
        coordinator.standby_subscribers[0](coordinator.standby_state)
        assert entity.writes == 2 and entity.available is True

    @pytest.mark.asyncio
    async def test_removing_the_entity_unsubscribes_everything_once(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity("charging", coordinator)
        await entity.async_added_to_hass()
        await entity.async_will_remove_from_hass()
        await entity.async_will_remove_from_hass()
        assert (coordinator.unsubscribed, coordinator.standby_unsubscribed) == (1, 1)
        assert coordinator.unpublished == 1


class TestBatteryAndRoundtripSensors:
    def test_battery_sensor_is_a_measurement_and_does_not_wait_for_standby(self) -> None:
        coordinator = _FakeCoordinator(standby_estimate=None)
        entity = _sensor.EffyBatteryEfficiencySensor(object(), _Entry(), coordinator)
        assert entity.entity_id == "sensor.effy_battery_efficiency"
        assert entity.unique_id == "entry1_battery_efficiency"
        assert entity._attr_translation_key == "battery_efficiency"
        assert entity._attr_native_unit_of_measurement == "%"
        assert entity._attr_state_class == "measurement"
        assert not hasattr(entity, "_attr_device_class")
        assert entity.available is False and entity.native_value is None
        entity._machine = _valley()
        assert entity.available and entity.native_value == 90.0
        assert entity.extra_state_attributes == {
            "last_counted_value": 92.3,
            "last_counted_end": "2026-01-11T03:00:00+00:00",
            "counted_kwh": 42.346,
            "counted_runs": 6,
            "last_processed_slot": "2026-01-11T05:00:00+00:00",
            "last_valley_span_begin": "2026-01-11T01:00:00+00:00",
            "last_valley_span_end": "2026-01-11T03:00:00+00:00",
        }

    def test_roundtrip_is_the_product_of_the_three_published_percent_values(self) -> None:
        coordinator = _FakeCoordinator()
        coordinator.efficiency_state = _machine()
        coordinator.battery_state = _valley()
        entity = _sensor.EffyRoundtripEfficiencySensor(object(), _Entry(), coordinator)
        assert entity.entity_id == "sensor.effy_roundtrip_efficiency"
        assert entity.unique_id == "entry1_roundtrip_efficiency"
        assert entity._attr_translation_key == "roundtrip_efficiency"
        assert entity.available and entity.native_value == 75.3

    def test_roundtrip_is_unavailable_when_any_component_is_unavailable(self) -> None:
        coordinator = _FakeCoordinator(standby_estimate=None)
        coordinator.efficiency_state = _machine()
        coordinator.battery_state = _valley()
        entity = _sensor.EffyRoundtripEfficiencySensor(object(), _Entry(), coordinator)
        assert entity.available is False and entity.native_value is None
        coordinator.standby_state = _calc.DrainState(estimate=20.0)
        entity._standby = coordinator.standby_state
        entity._battery_machine = _valley(None)
        assert entity.available is False and entity.native_value is None

    @pytest.mark.asyncio
    async def test_battery_sensor_restores_the_valley_machine_and_publishes_it(self) -> None:
        coordinator = _FakeCoordinator(standby_estimate=None)
        entity = _sensor.EffyBatteryEfficiencySensor(object(), _Entry(), coordinator)
        dump = _calc.valley_restore_data(_valley(), coordinator.battery_hysteresis)
        entity.restored = dump
        await entity.async_added_to_hass()
        assert coordinator.battery_restore_calls == [dump]
        assert entity.available and entity.native_value == 90.0
        assert coordinator.published[0]["series"] == "battery_efficiency"
        assert coordinator.published[0]["aggregations"] == ["mean", "min", "max"]
        assert entity.extra_restore_state_data.as_dict() == dump
        await entity.async_will_remove_from_hass()
        assert coordinator.battery_unsubscribed == 1 and coordinator.unpublished == 1

    @pytest.mark.asyncio
    async def test_roundtrip_restores_both_machines_and_publishes_itself(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _sensor.EffyRoundtripEfficiencySensor(object(), _Entry(), coordinator)
        data = {
            "efficiency": _calc.run_restore_data(_machine()),
            "battery": _calc.valley_restore_data(_valley(), coordinator.battery_hysteresis),
        }
        entity.restored = data
        await entity.async_added_to_hass()
        assert coordinator.restore_calls == [data["efficiency"]]
        assert coordinator.battery_restore_calls == [data["battery"]]
        assert entity.available and entity.native_value == 75.3
        assert coordinator.published[0]["series"] == "roundtrip_efficiency"
        assert entity.extra_restore_state_data.as_dict() == data
        assert len(coordinator.subscribers) == 1
        assert len(coordinator.battery_subscribers) == 1
        assert len(coordinator.standby_subscribers) == 1
        await entity.async_will_remove_from_hass()
        assert coordinator.unpublished == 1

    @pytest.mark.parametrize(
        ("sensor_class", "entity_id"),
        [
            ("EffyBatteryEfficiencySensor", "sensor.effy_battery_efficiency"),
            ("EffyRoundtripEfficiencySensor", "sensor.effy_roundtrip_efficiency"),
        ],
    )
    def test_the_entity_id_is_fixed_and_the_device_is_effy(
        self, sensor_class: str, entity_id: str
    ) -> None:
        entity = getattr(_sensor, sensor_class)(object(), _Entry(), _FakeCoordinator())
        entity.entity_id = "sensor.something_else"
        assert entity.entity_id == entity_id
        assert entity.device_info["identifiers"] == {("effy", "entry1")}

    def test_battery_without_an_estimate_has_no_attributes_and_nothing_to_restore(self) -> None:
        entity = _sensor.EffyBatteryEfficiencySensor(object(), _Entry(), _FakeCoordinator())
        assert entity.extra_restore_state_data is None  # no machine restored yet
        entity._machine = _valley(None)
        assert entity.extra_state_attributes is None

    def test_roundtrip_attributes_follow_the_battery_spans(self) -> None:
        coordinator = _FakeCoordinator()
        coordinator.efficiency_state = _machine()
        coordinator.battery_state = _valley()
        entity = _sensor.EffyRoundtripEfficiencySensor(object(), _Entry(), coordinator)
        assert entity.extra_state_attributes == {
            "last_counted_value": 75.3,
            "last_counted_end": "2026-01-11T03:00:00+00:00",
            "counted_kwh": 42.346,
            "counted_runs": 6,
            "last_processed_slot": "2026-01-11T05:00:00+00:00",
        }
        entity._battery_machine = _valley(None)
        assert entity.extra_state_attributes is None

    def test_roundtrip_has_nothing_to_restore_until_both_machines_exist(self) -> None:
        coordinator = _FakeCoordinator()
        coordinator.efficiency_state = _machine()
        entity = _sensor.EffyRoundtripEfficiencySensor(object(), _Entry(), coordinator)
        assert entity.extra_restore_state_data is None  # no battery machine yet

    @pytest.mark.asyncio
    async def test_a_new_battery_state_is_published(self) -> None:
        coordinator = _FakeCoordinator(standby_estimate=None)
        entity = _sensor.EffyBatteryEfficiencySensor(object(), _Entry(), coordinator)
        await entity.async_added_to_hass()
        coordinator.battery_subscribers[0](_valley())
        assert entity.writes == 1 and entity.native_value == 90.0

    @pytest.mark.asyncio
    async def test_removing_the_battery_sensor_twice_is_harmless(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _sensor.EffyBatteryEfficiencySensor(object(), _Entry(), coordinator)
        await entity.async_will_remove_from_hass()  # never added
        await entity.async_added_to_hass()
        await entity.async_will_remove_from_hass()
        await entity.async_will_remove_from_hass()
        assert (coordinator.battery_unsubscribed, coordinator.unpublished) == (1, 1)

    @pytest.mark.asyncio
    async def test_every_component_change_updates_the_roundtrip_sensor(self) -> None:
        coordinator = _FakeCoordinator(standby_estimate=None)
        entity = _sensor.EffyRoundtripEfficiencySensor(object(), _Entry(), coordinator)
        await entity.async_added_to_hass()
        coordinator.subscribers[0](_machine())
        coordinator.battery_subscribers[0](_valley())
        assert entity.writes == 2 and entity.available is False  # standby still missing
        coordinator.standby_subscribers[0](_calc.DrainState(estimate=18.0))
        assert entity.writes == 3 and entity.native_value == 75.3

    @pytest.mark.asyncio
    async def test_removing_the_roundtrip_sensor_twice_is_harmless(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _sensor.EffyRoundtripEfficiencySensor(object(), _Entry(), coordinator)
        await entity.async_added_to_hass()
        await entity.async_will_remove_from_hass()
        await entity.async_will_remove_from_hass()
        assert coordinator.unsubscribed == 1 and coordinator.battery_unsubscribed == 1
        assert coordinator.standby_unsubscribed == 1 and coordinator.unpublished == 1


def _created(coordinator: _FakeCoordinator) -> list[str]:
    entities = _sensor._efficiency_entities(object(), _Entry(), coordinator)
    return [e.entity_id for e in entities]


def _battery_created(coordinator: _FakeCoordinator) -> list[str]:
    entities = _sensor._battery_efficiency_entities(object(), _Entry(), coordinator)
    return [e.entity_id for e in entities]


class TestBatteryEntityExistence:
    def test_battery_requires_its_four_configured_inputs_and_roundtrip_all_three(self) -> None:
        _registry[0] = _Registry({})
        assert _battery_created(_FakeCoordinator()) == [
            "sensor.effy_battery_efficiency",
            "sensor.effy_roundtrip_efficiency",
        ]
        _registry[0] = _Registry({})
        partial = _FakeCoordinator(grid_import=None)
        assert _battery_created(partial) == ["sensor.effy_battery_efficiency"]
        _registry[0] = _Registry({})
        assert _battery_created(_FakeCoordinator(battery_enabled=False)) == []

    def test_sensors_are_removed_when_their_required_inputs_disappear(self) -> None:
        _registry[0] = _Registry(
            {
                ("sensor", "effy", "entry1_battery_efficiency"): "sensor.effy_battery_efficiency",
                ("sensor", "effy", "entry1_roundtrip_efficiency"): (
                    "sensor.effy_roundtrip_efficiency"
                ),
            }
        )
        assert _battery_created(_FakeCoordinator(battery_enabled=False)) == []
        assert sorted(_registry[0].removed) == [
            "sensor.effy_battery_efficiency",
            "sensor.effy_roundtrip_efficiency",
        ]


class TestExistsOnlyWithItsPair:
    def test_both_pairs_create_both_sensors(self) -> None:
        _registry[0] = _Registry({})
        assert _created(_FakeCoordinator()) == [
            "sensor.effy_charge_efficiency",
            "sensor.effy_discharge_efficiency",
        ]
        assert _registry[0].removed == []

    def test_grid_import_with_charge_is_the_charge_pair(self) -> None:
        _registry[0] = _Registry({})
        only = _FakeCoordinator(discharge=None, grid_export=None)
        assert _created(only) == ["sensor.effy_charge_efficiency"]

    def test_discharge_with_grid_export_is_the_discharge_pair(self) -> None:
        _registry[0] = _Registry({})
        only = _FakeCoordinator(grid_import=None, charge=None)
        assert _created(only) == ["sensor.effy_discharge_efficiency"]

    def test_a_half_pair_creates_nothing(self) -> None:
        _registry[0] = _Registry({})
        halves = _FakeCoordinator(charge=None, grid_export=None)  # grid import and discharge
        assert _created(halves) == []
        crossed = _FakeCoordinator(grid_import=None, grid_export=None)  # discharge and charge
        assert _created(crossed) == []

    def test_a_sensor_whose_pair_is_gone_is_removed_from_the_registry(self) -> None:
        _registry[0] = _Registry(
            {
                ("sensor", "effy", "entry1_charge_efficiency"): "sensor.effy_charge_efficiency",
                (
                    "sensor",
                    "effy",
                    "entry1_discharge_efficiency",
                ): "sensor.effy_discharge_efficiency",
            }
        )
        only_charge = _FakeCoordinator(discharge=None)
        assert _created(only_charge) == ["sensor.effy_charge_efficiency"]
        assert _registry[0].removed == ["sensor.effy_discharge_efficiency"]
        _registry[0].removed.clear()
        assert _created(_FakeCoordinator(grid_import=None, discharge=None)) == []
        assert sorted(_registry[0].removed) == [
            "sensor.effy_charge_efficiency",
            "sensor.effy_discharge_efficiency",
        ]

    def test_nothing_registered_and_nothing_configured_changes_nothing(self) -> None:
        _registry[0] = _Registry({})
        assert _created(_FakeCoordinator(grid_import=None, discharge=None)) == []
        assert _registry[0].removed == []
