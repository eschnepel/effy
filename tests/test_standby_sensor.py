"""TASK-0005 Stage 2 — the standby power sensor entity (ADR-018 §5, D4).

``sensor.py`` is loaded for real under a private package name with Home
Assistant stubbed only while it loads (like ``test_history_recent_equivalence``),
so this file neither clobbers nor leaks into the other modules' stubs. The
coordinator is a small fake that uses the real restore function of
``calculation.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_BASE = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_PKG = "effy_sensor_under_test"
UTC = timezone.utc


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
        for n in ("const", "inputs", "calculation", "sensor_utils", "coordinator", "sensor")
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
        for name in ("const", "inputs", "calculation", "sensor_utils", "sensor"):
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
    def __init__(self, enabled: bool = True) -> None:
        self.standby_enabled = enabled
        self.restore_calls: list[Any] = []
        self.subscribers: list[Callable[[Any], None]] = []
        self.unsubscribed = 0

    def restore_standby(self, data: Any) -> Any:
        self.restore_calls.append(data)
        restored = _calc.drain_state_from_restore(data) if data else None
        return restored if restored is not None else _calc.DrainState()

    def subscribe_standby(self, cb: Callable[[Any], None]) -> Callable[[], None]:
        self.subscribers.append(cb)

        def unsubscribe() -> None:
            self.unsubscribed += 1

        return unsubscribe


def _entity(coordinator: _FakeCoordinator | None = None) -> Any:
    return _sensor.EffyStandbyPowerSensor(object(), _Entry(), coordinator or _FakeCoordinator())


def _state(estimate: float | None, count: int = 3) -> Any:
    return _calc.DrainState(
        resolution={"sensor.bms": 10.0},
        estimate=estimate,
        count=count,
        last_value=12.34567,
        last_end=datetime(2026, 1, 11, 3, 0, tzinfo=UTC),
        last_source="sensor.bms",
        last_slot=datetime(2026, 1, 11, 5, 0, tzinfo=UTC),
    )


class TestEntityShape:
    def test_it_is_a_recorded_power_measurement_in_watts_with_a_stable_id(self) -> None:
        entity = _entity()
        assert entity.entity_id == "sensor.effy_standby_power"
        assert entity.unique_id == "entry1_standby_power"
        assert entity._attr_device_class == "power"
        assert entity._attr_state_class == "measurement"
        assert entity._attr_native_unit_of_measurement == "W"
        assert entity.device_info["identifiers"] == {("effy", "entry1")}

    def test_it_is_unavailable_not_unknown_until_the_first_interval_counted(self) -> None:
        entity = _entity()
        assert entity.available is False and entity.native_value is None
        entity._drain = _calc.DrainState()  # restored from nothing
        assert entity.available is False and entity.native_value is None
        assert entity.extra_state_attributes is None

    def test_the_state_is_rounded_to_a_tenth_and_the_attributes_show_provenance(self) -> None:
        entity = _entity()
        entity._drain = _state(12.3456)
        assert entity.available is True
        assert entity.native_value == 12.3
        assert entity.extra_state_attributes == {
            "last_counted_value": 12.346,
            "last_drain_interval_end": "2026-01-11T03:00:00+00:00",
            "drain_source": "sensor.bms",
            "counted_intervals": 3,
            "last_processed_slot": "2026-01-11T05:00:00+00:00",
        }

    def test_the_exact_estimate_is_what_survives_a_restart(self) -> None:
        entity = _entity()
        before = entity.extra_restore_state_data
        assert before is None
        entity._drain = _state(12.3456)
        stored = entity.extra_restore_state_data.as_dict()
        assert stored["estimate"] == 12.3456  # not the rounded 12.3
        assert stored["count"] == 3 and stored["resolution"] == {"sensor.bms": 10.0}


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_added_without_restored_data_starts_the_coordinator_empty(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity(coordinator)
        await entity.async_added_to_hass()
        assert coordinator.restore_calls == [None]
        assert entity.available is False
        assert len(coordinator.subscribers) == 1

    @pytest.mark.asyncio
    async def test_added_with_restored_data_shows_the_estimate_at_once(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity(coordinator)
        entity.restored = {
            "estimate": 12.34,
            "count": 5,
            "last_slot": "2026-01-11T05:00:00+00:00",
            "resolution": {"sensor.bms": 10.0},
        }
        await entity.async_added_to_hass()
        assert coordinator.restore_calls == [entity.restored]
        assert entity.available is True and entity.native_value == 12.3
        assert entity.extra_state_attributes["last_processed_slot"] == "2026-01-11T05:00:00+00:00"
        # a second restart before any job keeps the aggregates
        assert entity.extra_restore_state_data.as_dict()["estimate"] == 12.34

    @pytest.mark.asyncio
    async def test_a_new_state_from_the_coordinator_is_published(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity(coordinator)
        await entity.async_added_to_hass()
        coordinator.subscribers[0](_state(12.0))
        assert entity.writes == 1
        assert entity.available is True and entity.native_value == 12.0

    @pytest.mark.asyncio
    async def test_removing_the_entity_unsubscribes_once(self) -> None:
        coordinator = _FakeCoordinator()
        entity = _entity(coordinator)
        await entity.async_added_to_hass()
        await entity.async_will_remove_from_hass()
        await entity.async_will_remove_from_hass()
        assert coordinator.unsubscribed == 1


class TestExistsOnlyWithABmsInput:
    def test_with_a_bms_input_the_sensor_is_created(self) -> None:
        _registry[0] = _Registry({})
        entities = _sensor._standby_entities(object(), _Entry(), _FakeCoordinator(True))
        assert [type(e).__name__ for e in entities] == ["EffyStandbyPowerSensor"]
        assert _registry[0].removed == []

    def test_without_one_no_sensor_is_created_and_a_stale_registration_is_removed(self) -> None:
        _registry[0] = _Registry(
            {("sensor", "effy", "entry1_standby_power"): "sensor.effy_standby_power"}
        )
        assert _sensor._standby_entities(object(), _Entry(), _FakeCoordinator(False)) == []
        assert _registry[0].removed == ["sensor.effy_standby_power"]

    def test_without_one_and_nothing_registered_nothing_happens(self) -> None:
        _registry[0] = _Registry({})
        assert _sensor._standby_entities(object(), _Entry(), _FakeCoordinator(False)) == []
        assert _registry[0].removed == []
