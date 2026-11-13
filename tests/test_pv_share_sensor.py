"""
The sensor "PV share now" (TASK-0046, CAP-13, ADR-027 amendment 2026-11-09).

The series is written by ``history.py`` (see ``test_pv_share_series``); the entity gives it a
place to attach to and a live state. ``sensor.py`` is the real module loaded by
``test_standby_sensor`` with Home Assistant stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_published_sensors import _Coordinator, _Entry, _last
from tests.test_sensor_setup import ENERGY, POWER, _Platform, _SetupEntry, _state
from tests.test_standby_sensor import _Registry, _registry, _sensor

SHARE = "sensor.effy_pv_share_now"
UNIQUE = "entry1_pv_share_now"


class _ShareCoordinator(_Coordinator):
    def __init__(self, pv: list[str] | None = None, eps: str | None = None) -> None:
        super().__init__()
        self.pv_input_ids = ["sensor.pv_a_w"] if pv is None else pv
        self.eps_id = eps


def _share(coordinator: _Coordinator | None = None) -> _sensor.EffyPvShareSensor:
    return _sensor.EffyPvShareSensor(object(), _Entry(), coordinator or _ShareCoordinator())


class TestTheSensorsIdentity:
    def test_name_ids_class_and_unit(self) -> None:
        entity = _share()
        assert entity.entity_id == SHARE
        assert entity.unique_id == UNIQUE
        assert entity._attr_translation_key == "pv_share_now"
        assert entity._attr_native_unit_of_measurement == "%"
        assert entity._attr_state_class == "measurement"
        assert getattr(entity, "_attr_device_class", None) is None  # a percentage of nothing
        assert entity._attr_has_entity_name is True
        assert entity.device_info["name"] == "Effy"

    def test_the_entity_id_is_fixed_and_cannot_be_renamed_by_the_registry(self) -> None:
        entity = _share()
        entity.entity_id = "sensor.something_else"
        assert entity.entity_id == SHARE

    def test_the_constants(self) -> None:
        assert _sensor.PV_SHARE_ENTITY_ID == SHARE
        assert _sensor.pv_share_unique_id(_Entry()) == UNIQUE

    def test_the_translations_name_it_in_english_and_german(self) -> None:
        base = Path(__file__).resolve().parents[1] / "custom_components" / "effy" / "translations"
        en = json.loads((base / "en.json").read_text(encoding="utf-8"))
        de = json.loads((base / "de.json").read_text(encoding="utf-8"))
        assert en["entity"]["sensor"]["pv_share_now"]["name"] == "PV share now"
        assert de["entity"]["sensor"]["pv_share_now"]["name"]
        assert de["entity"]["sensor"]["pv_share_now"]["name"] != "PV share now"


class TestExistence:
    def _created(self, coordinator: _Coordinator) -> list[str]:
        entities = _sensor._pv_share_entities(object(), _Entry(), coordinator)
        return [e.entity_id for e in entities]

    def test_it_exists_with_a_pv_input(self) -> None:
        _registry[0] = _Registry({})
        assert self._created(_ShareCoordinator(["sensor.a"])) == [SHARE]
        assert _registry[0].removed == []

    def test_it_exists_with_only_an_eps_sensor(self) -> None:
        _registry[0] = _Registry({})
        assert self._created(_ShareCoordinator([], eps="sensor.eps")) == [SHARE]

    def test_without_pv_and_without_eps_it_does_not_and_the_registered_entity_goes(self) -> None:
        _registry[0] = _Registry({("sensor", "effy", UNIQUE): SHARE})
        assert self._created(_ShareCoordinator([], eps=None)) == []
        assert _registry[0].removed == [SHARE]  # the statistics stay: only the entity is removed

    def test_nothing_is_removed_that_was_never_registered(self) -> None:
        _registry[0] = _Registry({})
        assert self._created(_ShareCoordinator([])) == []
        assert _registry[0].removed == []

    async def test_the_platform_adds_it_with_the_other_entities(self) -> None:
        _registry[0] = _Registry({})
        entry = _SetupEntry(["sensor.pv_energy", "sensor.pv_power"], [])
        platform = _Platform(
            entry, {"sensor.pv_energy": _state(ENERGY), "sensor.pv_power": _state(POWER)}
        )
        await platform.setup()
        names = [type(e).__name__ for batch in platform.added for e in batch]
        assert names.count("EffyPvShareSensor") == 1

    async def test_the_platform_adds_none_without_pv_and_eps(self) -> None:
        _registry[0] = _Registry({})
        platform = _Platform(_SetupEntry([], []), {})
        platform.coordinator.pv_input_ids = []
        platform.coordinator.eps_id = None
        await platform.setup()
        names = [type(e).__name__ for batch in platform.added for e in batch]
        assert "EffyPvShareSensor" not in names


class TestLiveStateAndRestore:
    async def test_the_last_slots_share_is_pushed_in_percent(self) -> None:
        coordinator = _ShareCoordinator()
        entity = _share(coordinator)
        entity.async_write_ha_state = lambda: None
        await entity.async_added_to_hass()
        coordinator.updates[SHARE](41.66666, "%")
        assert entity._attr_native_value == 41.667
        assert entity._attr_native_unit_of_measurement == "%"

    async def test_a_missing_value_is_unknown_not_a_number(self) -> None:
        coordinator = _ShareCoordinator()
        entity = _share(coordinator)
        entity.async_write_ha_state = lambda: None
        await entity.async_added_to_hass()
        coordinator.updates[SHARE](None, None)
        assert entity._attr_native_value is None

    async def test_it_restores_its_last_state_like_the_other_effective_sensors(self) -> None:
        entity = _share()
        entity.last_state = _last("37.5", {"unit_of_measurement": "%"})
        await entity.async_added_to_hass()
        assert entity._attr_native_value == 37.5

    @pytest.mark.parametrize("state", ["unknown", "unavailable", "not-a-number"])
    async def test_a_last_state_that_is_not_a_number_restores_nothing(self, state: str) -> None:
        entity = _share()
        entity.last_state = _last(state, {"unit_of_measurement": "%"})
        await entity.async_added_to_hass()
        assert entity._attr_native_value is None

    async def test_it_is_not_published_yet_and_unsubscribes_on_removal(self) -> None:
        coordinator = _ShareCoordinator()
        entity = _share(coordinator)
        await entity.async_added_to_hass()
        assert coordinator.registry == {}  # TASK-0045f publishes it, not this task
        assert SHARE in coordinator.updates
        await entity.async_will_remove_from_hass()
