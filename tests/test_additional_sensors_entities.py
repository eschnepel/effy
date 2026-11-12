"""
The Effy sensors of the additional sensors and what they publish (TASK-0047, CAP-14, ADR-028).

An additional sensor has a derived power sensor (energy family) or a smoothed sensor (power
family), built by the code that builds them for an input, and no effective sensor. Their
unique IDs carry a marker no slug can hold, so the registry cleanup of a sensor taken out of the
list cannot touch the entity of an input. ``sensor.py`` is the real module loaded by
``tests/test_standby_sensor.py`` with Home Assistant stubbed.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_published_sensors import _Coordinator
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

GRID = "sensor.house_grid_import"
HOUSE = "sensor.house_power"
PV = "sensor.pv_energy"
OUT = "sensor.export_power"
EXTRAS = (GRID, HOUSE)


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> _Tracker:
    stub = _Tracker()
    monkeypatch.setattr(_sensor, "async_track_state_change_event", stub)
    return stub


@pytest.fixture(autouse=True)
def _empty_registry() -> None:
    _registry[0] = _Registry({})


def _platform(states: dict[str, Any], extras: tuple[str, ...] = EXTRAS) -> _Platform:
    entry = _SetupEntry([PV], [OUT])
    entry.options["additional_sensors"] = list(extras)
    platform = _Platform(entry, states)
    platform.coordinator.additional_ids = list(extras)
    return platform


def _extras_of(entities: list[Any]) -> list[tuple[str, str | None]]:
    return [(kind, source) for kind, source in _kinds(entities) if source in EXTRAS]


class TestSetup:
    async def test_each_additional_sensor_gets_the_one_series_its_family_calls_for(
        self, tracker: _Tracker
    ) -> None:
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), GRID: _state(ENERGY), HOUSE: _state(POWER)}
        )
        await platform.setup()

        assert _extras_of(platform.added[0]) == [
            ("EffyDerivedPowerSensor", GRID),
            ("EffySmoothedSensor", HOUSE),
        ]
        assert tracker.calls == []  # nothing is waiting

    async def test_no_effective_sensor_is_built_for_them(self, tracker: _Tracker) -> None:
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), GRID: _state(ENERGY), HOUSE: _state(POWER)}
        )
        await platform.setup()
        effective = [s for k, s in _kinds(platform.added[0]) if k == "EffySensor"]
        assert effective == [PV]

    async def test_an_entry_without_the_list_builds_what_it_built_before(
        self, tracker: _Tracker
    ) -> None:
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER)}, extras=())
        await platform.setup()
        assert _extras_of(platform.added[0]) == []
        assert ("EffySensor", PV) in _kinds(platform.added[0])

    async def test_a_sensor_without_a_state_is_waited_for_and_built_when_it_appears(
        self, tracker: _Tracker
    ) -> None:
        """ADR-013 decision 1: the one-shot listener of the inputs serves the additional
        sensors too, and the late entity is an additional one all the same."""
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER), HOUSE: _state(POWER)})
        await platform.setup()
        assert _extras_of(platform.added[0]) == [("EffySmoothedSensor", HOUSE)]
        ((_hass, waiting_for, callback),) = tracker.calls
        assert waiting_for == [GRID]

        platform.states[GRID] = _state(ENERGY)  # the state that makes the event
        callback(_event(GRID, platform.states[GRID]))

        assert [(k, s) for k, s in _kinds(platform.added[1])] == [("EffyDerivedPowerSensor", GRID)]
        assert platform.added[1][0].unique_id == "entry1_additional-house_grid_import_power"
        assert tracker.unsubscribed == 1  # the last one waiting: the listener ends itself

    async def test_a_late_power_sensor_gets_a_smoothed_sensor(self, tracker: _Tracker) -> None:
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), GRID: _state(ENERGY)}, extras=(GRID, HOUSE)
        )
        await platform.setup()
        ((_hass, _waiting, callback),) = tracker.calls
        platform.states[HOUSE] = _state(POWER)
        callback(_event(HOUSE, platform.states[HOUSE]))
        assert platform.added[1][0].unique_id == "entry1_additional-house_power_smoothed"

    async def test_the_entities_of_the_inputs_keep_their_unique_ids(
        self, tracker: _Tracker
    ) -> None:
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), GRID: _state(ENERGY), HOUSE: _state(POWER)}
        )
        await platform.setup()
        ids = {e.unique_id for e in platform.added[0] if hasattr(e, "_source_entity_id")}
        assert {"entry1_pv_energy", "entry1_pv_energy_power"} <= ids
        marked = {unique_id for unique_id in ids if "additional" in unique_id}
        assert marked == {
            "entry1_additional-house_grid_import_power",
            "entry1_additional-house_power_smoothed",
        }


class TestRegistryCleanup:
    _KNOWN = {
        ("sensor", "effy", "entry1_additional-house_grid_import_power"): "sensor.effy_hgi_power",
        ("sensor", "effy", "entry1_additional-old_one_power"): "sensor.effy_old_one_power",
        ("sensor", "effy", "entry1_additional-old_two_smoothed"): "sensor.effy_old_two_smoothed",
        ("sensor", "effy", "entry1_pv_energy_power"): "sensor.effy_pv_energy_power",
        ("sensor", "effy", "entry1_old_input_power"): "sensor.effy_old_input_power",
        ("sensor", "effy", "other_entry_additional-old_one_power"): "sensor.effy_other",
    }

    async def test_the_entities_of_a_sensor_taken_out_of_the_list_are_removed(
        self, tracker: _Tracker
    ) -> None:
        _registry[0] = _Registry(dict(self._KNOWN))
        platform = _platform(
            {PV: _state(ENERGY), OUT: _state(POWER), GRID: _state(ENERGY)}, extras=(GRID,)
        )
        await platform.setup()
        assert sorted(_registry[0].removed) == [
            "sensor.effy_old_one_power",
            "sensor.effy_old_two_smoothed",
        ]

    async def test_with_no_list_every_additional_entity_goes_and_inputs_stay(
        self, tracker: _Tracker
    ) -> None:
        _registry[0] = _Registry(dict(self._KNOWN))
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER)}, extras=())
        await platform.setup()
        assert sorted(_registry[0].removed) == [
            "sensor.effy_hgi_power",
            "sensor.effy_old_one_power",
            "sensor.effy_old_two_smoothed",
        ]

    async def test_a_sensor_still_listed_that_has_no_state_yet_keeps_its_entity(
        self, tracker: _Tracker
    ) -> None:
        _registry[0] = _Registry(dict(self._KNOWN))
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER)}, extras=(GRID,))
        await platform.setup()
        assert "sensor.effy_hgi_power" not in _registry[0].removed

    async def test_nothing_registered_nothing_removed(self, tracker: _Tracker) -> None:
        platform = _platform({PV: _state(ENERGY), OUT: _state(POWER)})
        await platform.setup()
        assert _registry[0].removed == []


class TestIdentityAndDescriptor:
    def _make(self, cls: str, source: str, coordinator: _Coordinator, *, additional: bool) -> Any:
        return getattr(_sensor, cls)(object(), _Entry(), coordinator, source, additional=additional)

    @pytest.mark.parametrize(
        ("cls", "source", "entity_id", "unique_id", "series"),
        [
            (
                "EffyDerivedPowerSensor",
                GRID,
                "sensor.effy_house_grid_import_power",
                "entry1_additional-house_grid_import_power",
                "derived_power",
            ),
            (
                "EffySmoothedSensor",
                HOUSE,
                "sensor.effy_house_power_smoothed",
                "entry1_additional-house_power_smoothed",
                "smoothed",
            ),
        ],
    )
    async def test_an_additional_series_sensor_publishes_role_other_and_everything_unknown(
        self, cls: str, source: str, entity_id: str, unique_id: str, series: str
    ) -> None:
        coordinator = _Coordinator()
        coordinator.additional_ids = [source]
        entity = self._make(cls, source, coordinator, additional=True)
        assert entity.entity_id == entity_id and entity.unique_id == unique_id

        await entity.async_added_to_hass()

        assert coordinator.registry[entity_id] == {
            "entity_id": entity_id,
            "series": series,
            "source_entity_id": source,
            "direction": "unknown",
            "role": "other",
            "function": "unknown",  # the key is present, with the value 'unknown'
            "aggregations": ["mean", "state"],
        }

    async def test_the_entity_of_an_input_is_not_an_additional_one(self) -> None:
        coordinator = _Coordinator()
        entity = self._make(
            "EffyDerivedPowerSensor", "sensor.pv_south", coordinator, additional=False
        )
        assert entity.unique_id == "entry1_pv_south_power"
        await entity.async_added_to_hass()
        assert coordinator.registry["sensor.effy_pv_south_power"]["role"] == "pv"

    def test_the_additional_flag_is_optional_and_off_by_default(self) -> None:
        entity = _sensor.EffySmoothedSensor(object(), _Entry(), _Coordinator(), "sensor.pv_south")
        assert entity.unique_id == "entry1_pv_south_smoothed"


class TestCatalog:
    def _describe(self, source: str, additional: list[str], series: str = "smoothed") -> Any:
        return _sensor.describe_source_series(
            f"sensor.effy_{source.split('.')[-1]}",
            series,
            source,
            pv_input_ids=["sensor.pv_south"],
            grid_import_id=None,
            discharge_id="sensor.bms_discharge",
            charge_id="sensor.bms_charge",
            grid_export_id=None,
            additional_ids=additional,
        )

    def test_an_additional_sensor_is_other_unknown_unknown(self) -> None:
        described = self._describe(GRID, [GRID], "derived_power")
        assert (described["role"], described["direction"], described["function"]) == (
            "other",
            "unknown",
            "unknown",
        )
        assert described["source_entity_id"] == GRID

    def test_a_role_wins_over_the_additional_list(self) -> None:
        described = self._describe("sensor.pv_south", ["sensor.pv_south"])
        assert (described["role"], described["direction"]) == ("pv", "input")

    def test_a_sensor_that_is_neither_still_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError):
            self._describe("sensor.stranger", [GRID])

    def test_the_default_is_no_additional_sensor(self) -> None:
        with pytest.raises(ValueError):
            _sensor.describe_source_series(
                "sensor.effy_x",
                "smoothed",
                GRID,
                pv_input_ids=[],
                grid_import_id=None,
                discharge_id=None,
                charge_id=None,
                grid_export_id=None,
            )


class TestTranslations:
    @pytest.mark.parametrize("lang", ["en", "de"])
    def test_the_picker_and_its_error_are_translated_in_both_flows(self, lang: str) -> None:
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
        data = json.loads((path / "translations" / f"{lang}.json").read_text("utf-8"))
        for part, step in (("config", "user"), ("options", "init")):
            assert data[part]["step"][step]["data"]["additional_sensors"]
            assert data[part]["error"]["additional_unit_not_supported"]
            assert data[part]["error"]["sensor_in_two_roles"]  # the existing role message
