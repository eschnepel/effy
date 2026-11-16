"""
The household consumption energy sensors "today" and "total" (TASK-0050, CAP-15, ADR-029
decision 5, ADR-027 decisions 3 and 5).

The same sensor class as the PV delta integrals, built for a series with no inputs under it:
kWh, device class energy, state class ``total``; *today* has a ``last_reset`` at local midnight,
*total* none. They exist while a household import is configured, restore their state like the PV
integrals, take the coordinator's pushes and are not published yet (TASK-0045f). The loader and
the fakes are those of ``test_pv_delta_sensors``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest

from tests.support import GROUP_KEYS
from tests.test_delta_integrals import BERLIN, MIDNIGHT, NOW
from tests.test_history_recent_equivalence import _const
from tests.test_pv_delta_sensors import (
    _TRANSLATIONS,
    _Base,
    _DeltaCoordinator,
    _Entry,
    _hass,
    _last,
)
from tests.test_sensor_setup import POWER, _Platform, _SetupEntry, _state
from tests.test_standby_sensor import _registry, _Registry, _sensor

TODAY_ID = "sensor.effy_household_consumption_today"
TOTAL_ID = "sensor.effy_household_consumption_total"
SERIES = "sensor.effy_household_consumption_now"
PV_SOURCE = "sensor.pv_south"
PV_TODAY = "sensor.effy_pv_south_delta_integral_today"
COMBINED_TODAY = "sensor.effy_pv_deltasum_integral_today"
COMBINED_TOTAL = "sensor.effy_pv_deltasum_integral_total"
IMPORT_ID = "sensor.house_import"


class _HouseholdCoordinator(_DeltaCoordinator):
    """The fake of ``test_pv_delta_sensors`` with the ``series`` a household integral passes."""

    household_import_id: str | None = IMPORT_ID
    household_ids: list[str] = []

    def restore_delta_integral(
        self,
        entity_id: str,
        kind: str,
        source: str | None,
        unit: str,
        value: float | None,
        last_reset: datetime | None,
        series: str | None = None,
    ) -> Any:
        self.restore_calls.append(
            {
                "entity_id": entity_id,
                "kind": kind,
                "source": source,
                "unit": unit,
                "value": value,
                "last_reset": last_reset,
                "series": series,
            }
        )
        return self.integrals.restore(
            entity_id=entity_id,
            kind=kind,
            source=source,
            series=series,
            unit=unit,
            value=value,
            last_reset=last_reset,
            now=NOW,
            tz=BERLIN,
        )


def _household(kind: str, coordinator: _DeltaCoordinator | None = None) -> Any:
    return _sensor.EffyDeltaIntegralSensor(
        _hass(), _Entry(), coordinator or _HouseholdCoordinator(), kind, household=True
    )


def _today(coordinator: _DeltaCoordinator | None = None) -> Any:
    return _household("today", coordinator)


def _total(coordinator: _DeltaCoordinator | None = None) -> Any:
    return _household("total", coordinator)


class TestIdentity:
    def test_the_ids_of_the_two_sensors(self) -> None:
        assert _today().entity_id == TODAY_ID == _const.HOUSEHOLD_CONSUMPTION_TODAY_ENTITY_ID
        assert _total().entity_id == TOTAL_ID == _const.HOUSEHOLD_CONSUMPTION_TOTAL_ENTITY_ID
        assert _today().unique_id == "entry1_household_consumption_today"
        assert _total().unique_id == "entry1_household_consumption_total"

    def test_home_assistant_cannot_rename_them(self) -> None:
        entity = _today()
        entity.entity_id = "sensor.something_else_2"  # Home Assistant assigns one: ignored
        assert entity.entity_id == TODAY_ID

    def test_they_are_not_per_input_sensors(self) -> None:
        for entity in (_today(), _total()):
            assert entity._source_entity_id is None
            assert entity._series == SERIES

    def test_every_one_is_an_energy_total_of_the_one_effy_device(self) -> None:
        for entity in (_today(), _total()):
            assert entity._attr_device_class == "energy"
            assert entity._attr_state_class == "total"
            assert entity.device_info["identifiers"] == {("effy", "entry1")}
            assert entity.device_info["name"] == "Effy"

    def test_the_names_come_from_the_translations(self) -> None:
        assert _today()._attr_translation_key == _const.HOUSEHOLD_CONSUMPTION_TODAY_TRANSLATION_KEY
        assert _total()._attr_translation_key == _const.HOUSEHOLD_CONSUMPTION_TOTAL_TRANSLATION_KEY
        assert _today().translation_placeholders is None or not _today().translation_placeholders

    @pytest.mark.parametrize("language", ["en", "de"])
    def test_both_translations_name_both_sensors_differently(self, language: str) -> None:
        names = json.loads((_TRANSLATIONS / f"{language}.json").read_text("utf-8"))["entity"][
            "sensor"
        ]
        keys = (
            _const.HOUSEHOLD_CONSUMPTION_TODAY_TRANSLATION_KEY,
            _const.HOUSEHOLD_CONSUMPTION_TOTAL_TRANSLATION_KEY,
        )
        for key in keys:
            assert names[key]["name"] and "{" not in names[key]["name"]
        assert len({names[key]["name"] for key in keys}) == 2
        assert names[_const.HOUSEHOLD_CONSUMPTION_TRANSLATION_KEY]["name"] not in {
            names[key]["name"] for key in keys
        }

    def test_the_english_names_are_the_ones_of_the_task(self) -> None:
        names = json.loads((_TRANSLATIONS / "en.json").read_text("utf-8"))["entity"]["sensor"]
        assert names["household_consumption_today"]["name"] == "Household consumption today"
        assert names["household_consumption_total"]["name"] == "Household consumption total"

    def test_the_entity_ids_are_the_statistic_ids_the_derivation_writes(self) -> None:
        """Home Assistant compiles the sensor's statistics under its entity ID: it must be the
        ID of the history ``async_derive_household_integrals`` wrote."""
        assert _today().entity_id == _const.HOUSEHOLD_CONSUMPTION_TODAY_ENTITY_ID
        assert _total().entity_id == _const.HOUSEHOLD_CONSUMPTION_TOTAL_ENTITY_ID

    def test_the_sensors_are_published_as_the_household_consumption_energy(self) -> None:
        """ADR-029: energy of series ``consumption``, scope ``system``, an output with role
        ``household``; no source, so in no ``sources`` list."""
        coordinator = _HouseholdCoordinator()
        for kind in ("today", "total"):
            asyncio.run(_household(kind, coordinator).async_added_to_hass())
        published = {d["entity_id"]: d for d in coordinator.published}
        assert set(published) == {TODAY_ID, TOTAL_ID}
        for entity_id, span in ((TODAY_ID, "today"), (TOTAL_ID, "total")):
            assert [published[entity_id][k] for k in GROUP_KEYS] == [
                "consumption",
                "energy",
                "system",
                span,
                "household",
                "output",
                "consumption",
                ["state", "sum", "change"],
            ]
            assert published[entity_id]["source_entity_id"] is None

    def test_the_pv_class_keeps_its_name_and_is_the_same_mechanism(self) -> None:
        assert issubclass(_sensor.EffyPvDeltaIntegralSensor, _sensor.EffyDeltaIntegralSensor)
        pv = _sensor.EffyPvDeltaIntegralSensor(
            _hass(), _Entry(), _HouseholdCoordinator(), "today", PV_SOURCE
        )
        assert pv.entity_id == PV_TODAY and pv._series is None
        assert type(_today()).__name__ == "EffyDeltaIntegralSensor"


class TestUnit:
    @pytest.mark.asyncio
    async def test_both_are_in_kwh_before_and_after_they_are_added(self) -> None:
        for entity in (_today(), _total()):
            assert entity.native_unit_of_measurement == "kWh"
            await entity.async_added_to_hass()
            assert entity.native_unit_of_measurement == "kWh"

    @pytest.mark.asyncio
    async def test_a_restored_unit_does_not_change_it(self) -> None:
        """There is no source whose unit could differ: the consumption is always in kWh."""
        coordinator = _HouseholdCoordinator()
        entity = _today(coordinator)
        entity.last_state = _last("5.0", unit_of_measurement="Wh", last_reset=MIDNIGHT.isoformat())
        await entity.async_added_to_hass()
        assert coordinator.restore_calls[0]["unit"] == "kWh"
        assert entity.native_unit_of_measurement == "kWh"


class TestStateAndRestore:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("make", [_today, _total])
    async def test_without_a_base_and_nothing_restored_it_is_unavailable(
        self, make: Callable[[], Any]
    ) -> None:
        entity = make()
        assert entity.available is False and entity.native_value is None
        await entity.async_added_to_hass()
        assert entity.available is False and entity.native_value is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["unavailable", "unknown", "not a number", ""])
    async def test_an_unusable_last_state_restores_nothing(self, state: str) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _today(coordinator)
        entity.last_state = _last(state, unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat())
        await entity.async_added_to_hass()
        assert coordinator.restore_calls[0]["value"] is None
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_the_restore_names_the_series_and_passes_what_was_restored(self) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _today(coordinator)
        entity.last_state = _last(
            "12.345678901234", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat()
        )
        await entity.async_added_to_hass()
        assert coordinator.restore_calls == [
            {
                "entity_id": TODAY_ID,
                "kind": "today",
                "source": None,
                "unit": "kWh",
                "value": 12.345678901234,
                "last_reset": MIDNIGHT,
                "series": SERIES,
            }
        ]
        assert entity.available is True and entity.native_value == 12.345678901234
        assert entity.last_reset == MIDNIGHT

    @pytest.mark.asyncio
    async def test_the_total_is_restored_and_has_no_last_reset(self) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _total(coordinator)
        entity.last_state = _last("987.65", unit_of_measurement="kWh", last_reset=None)
        await entity.async_added_to_hass()
        assert coordinator.restore_calls[0]["last_reset"] is None
        assert coordinator.restore_calls[0]["kind"] == "total"
        assert entity.native_value == 987.65 and entity.last_reset is None

    @pytest.mark.asyncio
    async def test_a_restored_state_of_an_earlier_day_is_reset_at_startup(self) -> None:
        entity = _today()
        entity.last_state = _last(
            "5.0", unit_of_measurement="kWh", last_reset="2026-11-09T00:00:00+01:00"
        )
        await entity.async_added_to_hass()
        assert entity.native_value == 0.0 and entity.last_reset == MIDNIGHT

    @pytest.mark.asyncio
    async def test_a_negative_state_is_restored(self) -> None:
        entity = _today()
        entity.last_state = _last(
            "-0.25", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat()
        )
        await entity.async_added_to_hass()
        assert entity.native_value == -0.25

    @pytest.mark.asyncio
    async def test_a_today_integral_has_a_last_reset_and_the_total_never(self) -> None:
        today, total = _today(), _total()
        today.last_state = _last("1.0", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat())
        total.last_state = _last("1.0", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat())
        await today.async_added_to_hass()
        await total.async_added_to_hass()
        assert today.last_reset == MIDNIGHT
        assert total.last_reset is None  # even a restored last_reset is ignored


class TestPushes:
    @pytest.mark.asyncio
    async def test_the_sensor_subscribes_when_added_and_unsubscribes_when_removed(self) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _today(coordinator)
        await entity.async_added_to_hass()
        assert list(coordinator.subscribers) == [TODAY_ID]
        await entity.async_will_remove_from_hass()
        assert coordinator.unsubscribed == [TODAY_ID]
        await entity.async_will_remove_from_hass()  # a second removal does nothing
        assert coordinator.unsubscribed == [TODAY_ID]

    @pytest.mark.asyncio
    async def test_a_booking_is_written_as_a_state_at_full_precision(self) -> None:
        coordinator = _HouseholdCoordinator()
        today, total = _today(coordinator), _total(coordinator)
        today.last_state = _last("1.0", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat())
        total.last_state = _last("10.0", unit_of_measurement="kWh")
        await today.async_added_to_hass()
        await total.async_added_to_hass()
        before = today.writes
        for integral in coordinator.integrals.book({SERIES: (0.123456789012, "kWh")}):
            coordinator.push(integral.entity_id, integral)
        assert today.writes == before + 1  # a state_changed event reaches the recorder
        assert today.native_value == pytest.approx(1.123456789012, abs=1e-15)
        assert total.native_value == pytest.approx(10.123456789012, abs=1e-14)
        assert today.native_value != round(today.native_value, 3)

    @pytest.mark.asyncio
    async def test_the_base_makes_an_unavailable_sensor_available_with_exactly_its_state(
        self,
    ) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _today(coordinator)
        await entity.async_added_to_hass()
        assert entity.available is False
        (changed,) = coordinator.integrals.hand_over(
            {TODAY_ID: _Base(0.0125, MIDNIGHT)}, NOW, BERLIN
        )
        coordinator.push(TODAY_ID, changed)
        assert entity.available is True
        assert entity.native_value == 0.0125 and entity.last_reset == MIDNIGHT

    @pytest.mark.asyncio
    async def test_the_midnight_shows_in_today_and_not_in_the_total(self) -> None:
        coordinator = _HouseholdCoordinator()
        today, total = _today(coordinator), _total(coordinator)
        today.last_state = _last("3.0", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat())
        total.last_state = _last("30.0", unit_of_measurement="kWh")
        await today.async_added_to_hass()
        await total.async_added_to_hass()
        new_midnight = datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN)
        changed = coordinator.integrals.reset_today(new_midnight)
        assert [i.entity_id for i in changed] == [TODAY_ID]
        coordinator.push(TODAY_ID, changed[0])
        assert today.native_value == 0.0 and today.last_reset == new_midnight
        assert total.native_value == 30.0

    @pytest.mark.asyncio
    async def test_a_pv_delta_does_not_reach_the_consumption_sensors(self) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _today(coordinator)
        entity.last_state = _last("1.0", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat())
        await entity.async_added_to_hass()
        coordinator.integrals.restore(
            entity_id=PV_TODAY,
            kind="today",
            source=PV_SOURCE,
            unit="Wh",
            value=0.0,
            last_reset=MIDNIGHT,
            now=NOW,
            tz=BERLIN,
        )
        assert [i.entity_id for i in coordinator.integrals.book({PV_SOURCE: (500.0, "Wh")})] == [
            PV_TODAY
        ]
        assert entity.native_value == 1.0


def _household_entities(platform: _Platform) -> list[Any]:
    return [
        e
        for batch in platform.added
        for e in batch
        if type(e).__name__ == "EffyDeltaIntegralSensor"
    ]


def _pv_entities(platform: _Platform) -> list[Any]:
    return [
        e
        for batch in platform.added
        for e in batch
        if type(e).__name__ == "EffyPvDeltaIntegralSensor"
    ]


class TestSetup:
    @pytest.fixture(autouse=True)
    def _fresh_registry(self) -> Any:
        _registry[0] = _Registry({})
        yield
        _registry[0] = _Registry({})

    async def test_with_a_household_import_both_sensors_exist(self) -> None:
        entry = _SetupEntry([], ["sensor.export"])
        platform = _Platform(entry, {IMPORT_ID: _state(POWER)})
        platform.coordinator.household_import_id = IMPORT_ID
        platform.coordinator.household_ids = [IMPORT_ID]
        await platform.setup()
        sensors = _household_entities(platform)
        assert [(e._kind, e.entity_id) for e in sensors] == [
            ("today", TODAY_ID),
            ("total", TOTAL_ID),
        ]
        assert len({e.unique_id for e in sensors}) == 2

    async def test_they_are_in_the_same_batch_as_the_other_entities(self) -> None:
        entry = _SetupEntry([], ["sensor.export"])
        platform = _Platform(entry, {IMPORT_ID: _state(POWER)})
        platform.coordinator.household_import_id = IMPORT_ID
        platform.coordinator.household_ids = [IMPORT_ID]
        await platform.setup()
        names = [type(e).__name__ for batch in platform.added for e in batch]
        assert "EffyHouseholdConsumptionSensor" in names  # the power sensor of TASK-0049
        assert names.count("EffyDeltaIntegralSensor") == 2

    async def test_without_a_household_import_none_exist_and_they_leave_the_registry(self) -> None:
        entry = _SetupEntry([], ["sensor.export"])
        platform = _Platform(entry, {})
        platform.coordinator.household_import_id = None
        _registry[0] = _Registry(
            {
                ("sensor", "effy", "entry1_household_consumption_today"): TODAY_ID,
                ("sensor", "effy", "entry1_household_consumption_total"): TOTAL_ID,
                ("sensor", "effy", "entry1_household_consumption_now"): SERIES,
                ("sensor", "effy", "entry1_recalculated_from"): "sensor.effy_recalculated_from",
            }
        )
        await platform.setup()
        assert _household_entities(platform) == []
        removed = sorted(_registry[0].removed)
        assert TODAY_ID in removed and TOTAL_ID in removed
        assert "sensor.effy_recalculated_from" not in removed  # not the others

    async def test_nothing_is_removed_that_was_never_registered(self) -> None:
        entry = _SetupEntry([], ["sensor.export"])
        platform = _Platform(entry, {})
        platform.coordinator.household_import_id = None
        await platform.setup()
        assert TODAY_ID not in _registry[0].removed and TOTAL_ID not in _registry[0].removed

    async def test_the_pv_and_the_household_cleanups_never_touch_each_other(self) -> None:
        entry = _SetupEntry([PV_SOURCE], ["sensor.export"])
        platform = _Platform(entry, {PV_SOURCE: _state(POWER), IMPORT_ID: _state(POWER)})
        platform.coordinator.pv_input_ids = [PV_SOURCE]
        platform.coordinator.household_import_id = IMPORT_ID
        platform.coordinator.household_ids = [IMPORT_ID]
        _registry[0] = _Registry(
            {
                ("sensor", "effy", "entry1_pv_south_delta_integral_today"): PV_TODAY,
                ("sensor", "effy", "entry1_pv_north_delta_integral_today"): "sensor.effy_north",
                ("sensor", "effy", "entry1_pv_deltasum_integral_today"): COMBINED_TODAY,
                ("sensor", "effy", "entry1_pv_deltasum_integral_total"): COMBINED_TOTAL,
                ("sensor", "effy", "entry1_household_consumption_today"): TODAY_ID,
                ("sensor", "effy", "entry1_household_consumption_total"): TOTAL_ID,
            }
        )
        await platform.setup()
        assert _registry[0].removed == ["sensor.effy_north"]  # only the PV input that is gone
        assert len(_household_entities(platform)) == 2
        assert len(_pv_entities(platform)) == 3  # the input's own and the two combined

    async def test_pv_inputs_and_a_household_import_give_all_of_the_delta_sensors(self) -> None:
        entry = _SetupEntry([PV_SOURCE], ["sensor.export"])
        platform = _Platform(entry, {PV_SOURCE: _state(POWER), IMPORT_ID: _state(POWER)})
        platform.coordinator.pv_input_ids = [PV_SOURCE]
        platform.coordinator.household_import_id = IMPORT_ID
        platform.coordinator.household_ids = [IMPORT_ID]
        await platform.setup()
        ids = {e.entity_id for e in _household_entities(platform) + _pv_entities(platform)}
        assert ids == {PV_TODAY, COMBINED_TODAY, COMBINED_TOTAL, TODAY_ID, TOTAL_ID}
