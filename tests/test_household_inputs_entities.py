"""
The Effy sensors of the household meter sensors (TASK-0048, CAP-15, ADR-029).

A household sensor has a derived power sensor (energy family) or a smoothed sensor (power
family), built by the code that builds them for an input and for an additional sensor, and no
effective sensor. Their unique IDs carry the marker ``household-`` (no slug can hold it, and it is
not ``additional-``), so the registry cleanup of a sensor taken out of the options finds exactly
the household entities. They are *not* registered for the published contract here: TASK-0045f
does. ``sensor.py`` is the real module loaded by ``tests/test_standby_sensor.py`` with Home
Assistant stubbed.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_published_sensors import _Coordinator, _last
from tests.test_sensor_setup import (
    ENERGY,
    POWER,
    _event,
    _kinds,
    _Platform,
    _SetupEntry,
    _state,
    _Tracker,
)
from tests.test_standby_sensor import _Entry, _registry, _Registry, _sensor

IMPORT = "sensor.house_import"
EXPORT = "sensor.house_export"
EXTRA = "sensor.house_extra"
PV = "sensor.pv_energy"
OUT = "sensor.export_power"
HOUSEHOLD = (IMPORT, EXPORT)


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> _Tracker:
    stub = _Tracker()
    monkeypatch.setattr(_sensor, "async_track_state_change_event", stub)
    return stub


@pytest.fixture(autouse=True)
def _empty_registry() -> None:
    _registry[0] = _Registry({})


def _platform(
    states: dict[str, Any],
    household: tuple[str, ...] = HOUSEHOLD,
    additional: tuple[str, ...] = (),
) -> _Platform:
    entry = _SetupEntry([PV], [OUT])
    platform = _Platform(entry, states)
    platform.coordinator.household_ids = list(household)
    platform.coordinator.household_import_id = household[0] if household else None
    platform.coordinator.household_export_id = household[1] if len(household) > 1 else None
    platform.coordinator.additional_ids = list(additional)
    return platform


def _own(entities: list[Any], sources: tuple[str, ...] = HOUSEHOLD) -> list[tuple[str, str | None]]:
    return [(kind, source) for kind, source in _kinds(entities) if source in sources]


class TestSetup:
    async def test_each_household_sensor_gets_the_one_series_its_family_calls_for(
        self, tracker: _Tracker
    ) -> None:
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), IMPORT: _state(ENERGY), EXPORT: _state(POWER)}
        )
        await platform.setup()
        assert _own(platform.added[0]) == [
            ("EffyDerivedPowerSensor", IMPORT),
            ("EffySmoothedSensor", EXPORT),
        ]
        assert tracker.calls == []  # nothing is waiting

    async def test_a_power_import_alone_gets_its_smoothed_sensor(self, tracker: _Tracker) -> None:
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), IMPORT: _state(POWER)}, household=(IMPORT,)
        )
        await platform.setup()
        assert _own(platform.added[0]) == [("EffySmoothedSensor", IMPORT)]

    async def test_no_effective_sensor_is_built_for_them(self, tracker: _Tracker) -> None:
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), IMPORT: _state(ENERGY), EXPORT: _state(POWER)}
        )
        await platform.setup()
        effective = [s for k, s in _kinds(platform.added[0]) if k == "EffySensor"]
        assert effective == [PV]

    async def test_an_entry_without_household_sensors_builds_what_it_built_before(
        self, tracker: _Tracker
    ) -> None:
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER)}, household=())
        await platform.setup()
        assert _own(platform.added[0]) == []
        assert ("EffySensor", PV) in _kinds(platform.added[0])

    async def test_a_sensor_without_a_state_is_waited_for_and_built_when_it_appears(
        self, tracker: _Tracker
    ) -> None:
        """ADR-013 decision 1: the one-shot listener of the inputs serves the household sensors
        too, and the late entity is a household one all the same."""
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER), EXPORT: _state(POWER)})
        await platform.setup()
        assert _own(platform.added[0]) == [("EffySmoothedSensor", EXPORT)]
        ((_hass, waiting_for, callback),) = tracker.calls
        assert waiting_for == [IMPORT]

        platform.states[IMPORT] = _state(ENERGY)  # the state that makes the event
        callback(_event(IMPORT, platform.states[IMPORT]))

        assert [(k, s) for k, s in _kinds(platform.added[1])] == [
            ("EffyDerivedPowerSensor", IMPORT)
        ]
        assert platform.added[1][0].unique_id == "entry1_household-house_import_power"
        assert tracker.unsubscribed == 1  # the last one waiting: the listener ends itself

    async def test_a_late_power_sensor_gets_a_smoothed_sensor_with_the_household_marker(
        self, tracker: _Tracker
    ) -> None:
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER), IMPORT: _state(ENERGY)})
        await platform.setup()
        ((_hass, _waiting, callback),) = tracker.calls
        platform.states[EXPORT] = _state(POWER)
        callback(_event(EXPORT, platform.states[EXPORT]))
        assert platform.added[1][0].unique_id == "entry1_household-house_export_smoothed"

    async def test_the_unique_ids_carry_their_own_marker_and_the_others_keep_theirs(
        self, tracker: _Tracker
    ) -> None:
        platform = _platform(
            {
                PV: _state(ENERGY),
                OUT: _state(POWER),
                IMPORT: _state(ENERGY),
                EXPORT: _state(POWER),
                EXTRA: _state(POWER),
            },
            additional=(EXTRA,),
        )
        await platform.setup()
        ids = {e.unique_id for e in platform.added[0] if hasattr(e, "_source_entity_id")}
        assert {"entry1_pv_energy", "entry1_pv_energy_power"} <= ids
        assert {u for u in ids if "household" in u} == {
            "entry1_household-house_import_power",
            "entry1_household-house_export_smoothed",
        }
        assert {u for u in ids if "additional" in u} == {"entry1_additional-house_extra_smoothed"}


class TestRegistryCleanup:
    _KNOWN = {
        ("sensor", "effy", "entry1_household-house_import_power"): "sensor.effy_hi_power",
        ("sensor", "effy", "entry1_household-old_one_power"): "sensor.effy_old_one_power",
        ("sensor", "effy", "entry1_household-old_two_smoothed"): "sensor.effy_old_two_smoothed",
        ("sensor", "effy", "entry1_additional-house_extra_smoothed"): "sensor.effy_extra",
        ("sensor", "effy", "entry1_additional-old_additional_power"): "sensor.effy_old_add",
        ("sensor", "effy", "entry1_pv_energy_power"): "sensor.effy_pv_energy_power",
        ("sensor", "effy", "entry1_old_input_power"): "sensor.effy_old_input_power",
        ("sensor", "effy", "other_entry_household-old_one_power"): "sensor.effy_other",
    }

    async def test_the_entities_of_a_sensor_taken_out_are_removed_and_nothing_else(
        self, tracker: _Tracker
    ) -> None:
        _registry[0] = _Registry(dict(self._KNOWN))
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), IMPORT: _state(ENERGY)},
            household=(IMPORT,),
            additional=(EXTRA,),
        )
        await platform.setup()
        # the household cleanup finds the household entities only; the additional one that is
        # still configured stays, the old additional one goes by ADR-028's own cleanup
        assert sorted(_registry[0].removed) == [
            "sensor.effy_old_add",
            "sensor.effy_old_one_power",
            "sensor.effy_old_two_smoothed",
        ]

    async def test_with_no_household_sensor_every_household_entity_goes_and_others_stay(
        self, tracker: _Tracker
    ) -> None:
        _registry[0] = _Registry(dict(self._KNOWN))
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), EXTRA: _state(POWER)},
            household=(),
            additional=(EXTRA,),
        )
        await platform.setup()
        removed = set(_registry[0].removed)
        assert {
            "sensor.effy_hi_power",
            "sensor.effy_old_one_power",
            "sensor.effy_old_two_smoothed",
        } <= removed
        assert not removed & {
            "sensor.effy_pv_energy_power",
            "sensor.effy_old_input_power",
            "sensor.effy_extra",
            "sensor.effy_other",
        }

    async def test_the_additional_cleanup_never_touches_a_household_entity(
        self, tracker: _Tracker
    ) -> None:
        _registry[0] = _Registry(dict(self._KNOWN))
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), IMPORT: _state(ENERGY)},
            household=(IMPORT,),
            additional=(),
        )
        await platform.setup()
        assert "sensor.effy_hi_power" not in _registry[0].removed

    async def test_a_sensor_still_configured_that_has_no_state_yet_keeps_its_entity(
        self, tracker: _Tracker
    ) -> None:
        _registry[0] = _Registry(dict(self._KNOWN))
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER)}, household=(IMPORT,))
        await platform.setup()
        assert "sensor.effy_hi_power" not in _registry[0].removed

    async def test_nothing_registered_nothing_removed(self, tracker: _Tracker) -> None:
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER), IMPORT: _state(ENERGY)})
        await platform.setup()
        assert _registry[0].removed == []


class TestIdentityAndPublication:
    def _make(self, cls: str, source: str, coordinator: _Coordinator, **flags: bool) -> Any:
        return getattr(_sensor, cls)(object(), _Entry(), coordinator, source, **flags)

    @pytest.mark.parametrize(
        ("cls", "source", "entity_id", "unique_id"),
        [
            (
                "EffyDerivedPowerSensor",
                IMPORT,
                "sensor.effy_house_import_power",
                "entry1_household-house_import_power",
            ),
            (
                "EffySmoothedSensor",
                EXPORT,
                "sensor.effy_house_export_smoothed",
                "entry1_household-house_export_smoothed",
            ),
        ],
    )
    async def test_a_household_entity_has_the_inputs_entity_id_and_is_not_published_here(
        self, cls: str, source: str, entity_id: str, unique_id: str
    ) -> None:
        coordinator = _Coordinator()
        entity = self._make(cls, source, coordinator, household=True)
        assert entity.entity_id == entity_id and entity.unique_id == unique_id

        await entity.async_added_to_hass()

        # not registered with ``register_published``: TASK-0045f publishes the household series
        assert coordinator.registry == {}
        # but it gets the live push of the slot job, like any series
        assert entity_id in coordinator.updates

    @pytest.mark.parametrize("cls", ["EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_removing_the_entity_leaves_nothing_behind(self, cls: str) -> None:
        coordinator = _Coordinator()
        entity = self._make(cls, IMPORT, coordinator, household=True)
        await entity.async_added_to_hass()
        await entity.async_will_remove_from_hass()  # must not raise with nothing registered

    @pytest.mark.parametrize("cls", ["EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_the_live_value_is_the_pushed_slot_value_and_is_restored_after_a_restart(
        self, cls: str
    ) -> None:
        coordinator = _Coordinator()
        entity = self._make(cls, IMPORT, coordinator, household=True)
        entity.last_state = _last("123.4", {"unit_of_measurement": "W"})  # before the restart

        await entity.async_added_to_hass()
        assert entity._attr_native_value == 123.4  # restored, provisional (ADR-016)

        coordinator.updates[entity.entity_id](-45.6789, "W")  # the last completed slot's value
        assert entity._attr_native_value == -45.679  # a negative (signed) value is kept
        assert entity._attr_native_unit_of_measurement == "W"
        assert entity.writes == 1

    @pytest.mark.parametrize("cls", ["EffyDerivedPowerSensor", "EffySmoothedSensor"])
    def test_the_other_roles_unique_ids_are_unchanged(self, cls: str) -> None:
        suffix = "power" if cls == "EffyDerivedPowerSensor" else "smoothed"
        plain = self._make(cls, "sensor.pv_south", _Coordinator())
        additional = self._make(cls, "sensor.pv_south", _Coordinator(), additional=True)
        household = self._make(cls, "sensor.pv_south", _Coordinator(), household=True)
        assert plain.unique_id == f"entry1_pv_south_{suffix}"
        assert additional.unique_id == f"entry1_additional-pv_south_{suffix}"
        assert household.unique_id == f"entry1_household-pv_south_{suffix}"

    @pytest.mark.parametrize("cls", ["EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_an_additional_sensor_is_still_published_as_before(self, cls: str) -> None:
        coordinator = _Coordinator()
        coordinator.additional_ids = [IMPORT]
        entity = self._make(cls, IMPORT, coordinator, additional=True)
        await entity.async_added_to_hass()
        assert coordinator.registry[entity.entity_id]["role"] == "other"
