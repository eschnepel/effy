"""
The PV delta integral sensors (TASK-0045d, CAP-13, ADR-027 decisions 3 to 5).

``sensor.py`` is the real module loaded by ``test_standby_sensor`` (Home Assistant stubbed only
while it loads). The coordinator is a small fake around the real ``DeltaIntegrals``, so what the
sensor shows is what the booking, the reset and the base produce. The setup (which sensors
exist, which are removed from the registry) is tested at the end.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_delta_integrals import BERLIN, MIDNIGHT, NOW, YESTERDAY, _di
from tests.test_history_recent_equivalence import _const, _history
from tests.test_sensor_setup import POWER, _Platform, _SetupEntry, _state
from tests.test_standby_sensor import _registry, _Registry, _sensor

SOURCE = "sensor.pv_south"
OTHER = "sensor.pv_north"
TODAY_ID = "sensor.effy_pv_south_delta_integral_today"
COMBINED_TODAY = "sensor.effy_pv_deltasum_integral_today"
COMBINED_TOTAL = "sensor.effy_pv_deltasum_integral_total"
_TRANSLATIONS = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "translations"
)


@dataclass(frozen=True)
class _Base:
    state: float
    last_reset: datetime | None
    total: float = 0.0


class _Entry:
    entry_id = "entry1"


class _DeltaCoordinator:
    """The part of the coordinator the sensor uses, around the real ``DeltaIntegrals``."""

    pv_input_ids = [SOURCE]

    def __init__(self) -> None:
        self.integrals = _di.DeltaIntegrals()
        self.restore_calls: list[dict[str, Any]] = []
        self.subscribers: dict[str, Callable[[Any], None]] = {}
        self.unsubscribed: list[str] = []
        self.published: list[Any] = []

    def restore_delta_integral(
        self,
        entity_id: str,
        kind: str,
        source: str | None,
        unit: str,
        value: float | None,
        last_reset: datetime | None,
    ) -> Any:
        self.restore_calls.append(
            {
                "entity_id": entity_id,
                "kind": kind,
                "source": source,
                "unit": unit,
                "value": value,
                "last_reset": last_reset,
            }
        )
        return self.integrals.restore(
            entity_id=entity_id,
            kind=kind,
            source=source,
            unit=unit,
            value=value,
            last_reset=last_reset,
            now=NOW,
            tz=BERLIN,
        )

    def subscribe_delta_integral(
        self, entity_id: str, cb: Callable[[Any], None]
    ) -> Callable[[], None]:
        self.subscribers[entity_id] = cb

        def unsubscribe() -> None:
            self.unsubscribed.append(entity_id)

        return unsubscribe

    def register_published(self, descriptor: dict[str, Any]) -> Callable[[], None]:
        self.published.append(descriptor)
        return lambda: None

    def push(self, entity_id: str, integral: Any) -> None:
        self.subscribers[entity_id](integral)


def _hass(states: dict[str, Any] | None = None) -> Any:
    states = states or {}
    return SimpleNamespace(states=SimpleNamespace(get=states.get))


def _sensor_for(
    kind: str = "today",
    source: str | None = SOURCE,
    states: dict[str, Any] | None = None,
    coordinator: _DeltaCoordinator | None = None,
) -> Any:
    return _sensor.EffyPvDeltaIntegralSensor(
        _hass(states), _Entry(), coordinator or _DeltaCoordinator(), kind, source
    )


def _input() -> Any:
    return _sensor_for("today", SOURCE)


def _today() -> Any:
    return _sensor_for("today", None)


def _total() -> Any:
    return _sensor_for("total", None)


def _last(state: str, **attributes: Any) -> Any:
    return SimpleNamespace(state=state, attributes=attributes)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_the_per_input_sensor(self) -> None:
        entity = _input()
        assert entity.entity_id == TODAY_ID
        assert entity.unique_id == "entry1_pv_south_delta_integral_today"
        entity.entity_id = "sensor.something_else_2"  # Home Assistant assigns one: ignored
        assert entity.entity_id == TODAY_ID
        assert entity._source_entity_id == SOURCE

    def test_the_combined_sensors_read_deltasum(self) -> None:
        assert _today().entity_id == COMBINED_TODAY == _const.PV_DELTA_TODAY_ENTITY_ID
        assert _total().entity_id == COMBINED_TOTAL == _const.PV_DELTA_TOTAL_ENTITY_ID
        assert _today().unique_id == "entry1_pv_deltasum_integral_today"
        assert _total().unique_id == "entry1_pv_deltasum_integral_total"
        assert _today()._source_entity_id is None

    def test_the_entity_id_is_the_statistic_id_the_derivation_writes(self) -> None:
        """Home Assistant compiles the sensor's statistics under its entity ID: it must be the
        ID of the history the derivation (TASK-0045c) wrote."""
        for source in (SOURCE, OTHER, "sensor.pv", "sensor.Roof_East"):
            assert _sensor_for("today", source).entity_id == _history._pv_delta_integral_id(source)

    def test_a_source_named_pv_collides_with_nothing(self) -> None:
        """The case that made the human rename the combined IDs (ADR-027 Amendment
        2026-11-10): neither the entity IDs nor the unique IDs are the same."""
        named_pv = _sensor_for("today", "sensor.pv")
        assert named_pv.entity_id == "sensor.effy_pv_delta_integral_today"
        ids = {
            named_pv.entity_id,
            _today().entity_id,
            _total().entity_id,
        }
        uniques = {named_pv.unique_id, _today().unique_id, _total().unique_id}
        assert len(ids) == 3 and len(uniques) == 3

    @pytest.mark.parametrize("make", [_input, _today, _total])
    def test_every_delta_integral_is_an_energy_total_of_the_one_effy_device(
        self, make: Callable[[], Any]
    ) -> None:
        entity = make()
        assert entity._attr_device_class == "energy"
        assert entity._attr_state_class == "total"
        assert entity.device_info["identifiers"] == {("effy", "entry1")}
        assert entity.device_info["name"] == "Effy"

    def test_the_names_come_from_the_translations(self) -> None:
        assert _today()._attr_translation_key == _const.PV_DELTA_TODAY_TRANSLATION_KEY
        assert _total()._attr_translation_key == _const.PV_DELTA_TOTAL_TRANSLATION_KEY
        assert _input()._attr_translation_key == _const.PV_DELTA_INPUT_TRANSLATION_KEY

    def test_the_per_input_name_carries_the_sources_name(self) -> None:
        named = _sensor_for("today", SOURCE, {SOURCE: _state({"friendly_name": "PV South"})})
        assert named.translation_placeholders == {"source_name": "PV South"}

    def test_without_a_friendly_name_or_a_state_the_slug_is_the_name(self) -> None:
        assert _sensor_for("today", SOURCE, {SOURCE: _state({})}).translation_placeholders == {
            "source_name": "pv_south"
        }
        assert _input().translation_placeholders == {"source_name": "pv_south"}

    @pytest.mark.parametrize("language", ["en", "de"])
    def test_both_translations_name_all_three_kinds(self, language: str) -> None:
        names = json.loads((_TRANSLATIONS / f"{language}.json").read_text("utf-8"))["entity"][
            "sensor"
        ]
        assert "{source_name}" in names[_const.PV_DELTA_INPUT_TRANSLATION_KEY]["name"]
        for key in (_const.PV_DELTA_TODAY_TRANSLATION_KEY, _const.PV_DELTA_TOTAL_TRANSLATION_KEY):
            assert names[key]["name"] and "{" not in names[key]["name"]
        assert len({names[key]["name"] for key in names if "delta" in key.lower()}) == 3

    def test_the_english_names_are_the_ones_of_the_task(self) -> None:
        names = json.loads((_TRANSLATIONS / "en.json").read_text("utf-8"))["entity"]["sensor"]
        assert names[_const.PV_DELTA_INPUT_TRANSLATION_KEY]["name"] == (
            "{source_name} delta integral today"
        )
        assert names[_const.PV_DELTA_TODAY_TRANSLATION_KEY]["name"] == "PV delta integral today"
        assert names[_const.PV_DELTA_TOTAL_TRANSLATION_KEY]["name"] == "PV delta integral total"

    def test_the_sensors_are_not_in_the_published_list_yet(self) -> None:
        """TASK-0045f publishes them; until then no descriptor is registered."""
        coordinator = _DeltaCoordinator()
        import asyncio

        for kind, source in (("today", SOURCE), ("today", None), ("total", None)):
            entity = _sensor_for(kind, source, coordinator=coordinator)
            asyncio.run(entity.async_added_to_hass())
        assert coordinator.published == []


# ---------------------------------------------------------------------------
# Unit
# ---------------------------------------------------------------------------


class TestUnit:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("source_unit", "expected"),
        [("W", "Wh"), ("kW", "kWh"), ("Wh", "Wh"), ("kWh", "kWh")],
    )
    async def test_the_unit_follows_the_effective_unit_of_the_source(
        self, source_unit: str, expected: str
    ) -> None:
        """Wh if the effective unit is W, kWh if it is kW (ADR-027 decision 3)."""
        states = {SOURCE: _state({"unit_of_measurement": source_unit})}
        entity = _sensor_for("today", SOURCE, states)
        await entity.async_added_to_hass()
        assert entity.native_unit_of_measurement == expected

    @pytest.mark.asyncio
    async def test_a_restored_unit_wins_over_the_sources(self) -> None:
        states = {SOURCE: _state({"unit_of_measurement": "kW"})}
        entity = _sensor_for("today", SOURCE, states)
        entity.last_state = _last("5.0", unit_of_measurement="Wh", last_reset=MIDNIGHT.isoformat())
        await entity.async_added_to_hass()
        assert entity.native_unit_of_measurement == "Wh"

    @pytest.mark.asyncio
    async def test_an_unusable_restored_unit_is_ignored(self) -> None:
        entity = _sensor_for("today", SOURCE, {SOURCE: _state({"unit_of_measurement": "kW"})})
        entity.last_state = _last("5.0", unit_of_measurement="MW", last_reset=MIDNIGHT.isoformat())
        await entity.async_added_to_hass()
        assert entity.native_unit_of_measurement == "kWh"

    @pytest.mark.asyncio
    async def test_without_any_hint_the_unit_is_wh_never_none(self) -> None:
        """Energy is a device class with units: a missing unit would be an error."""
        entity = _input()
        assert entity.native_unit_of_measurement == "Wh"  # before it was added
        await entity.async_added_to_hass()
        assert entity.native_unit_of_measurement == "Wh"

    @pytest.mark.asyncio
    async def test_the_combined_sensors_are_in_kwh(self) -> None:
        for entity in (_today(), _total()):
            await entity.async_added_to_hass()
            assert entity.native_unit_of_measurement == "kWh"

    @pytest.mark.asyncio
    async def test_the_unit_of_a_handed_over_base_is_the_one_shown(self) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("today", SOURCE, coordinator=coordinator)
        await entity.async_added_to_hass()
        coordinator.integrals.hand_over(
            {TODAY_ID: _Base(1.0, MIDNIGHT)}, NOW, BERLIN, units={TODAY_ID: "kWh"}
        )
        assert entity.native_unit_of_measurement == "kWh"


# ---------------------------------------------------------------------------
# State, availability, restore
# ---------------------------------------------------------------------------


class TestStateAndRestore:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("make", [_input, _today, _total])
    async def test_without_a_base_and_nothing_restored_it_is_unavailable(
        self, make: Callable[[], Any]
    ) -> None:
        entity = make()
        assert entity.available is False and entity.native_value is None  # not added yet
        await entity.async_added_to_hass()
        assert entity.available is False and entity.native_value is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["unavailable", "unknown", "not a number", ""])
    async def test_an_unavailable_or_unknown_last_state_restores_nothing(self, state: str) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("today", SOURCE, coordinator=coordinator)
        entity.last_state = _last(state, unit_of_measurement="Wh", last_reset=MIDNIGHT.isoformat())
        await entity.async_added_to_hass()
        assert coordinator.restore_calls[0]["value"] is None
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_a_last_state_is_restored_like_the_other_effy_sensors(self) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("today", SOURCE, coordinator=coordinator)
        entity.last_state = _last(
            "123.456789012345", unit_of_measurement="Wh", last_reset=MIDNIGHT.isoformat()
        )
        await entity.async_added_to_hass()
        assert coordinator.restore_calls == [
            {
                "entity_id": TODAY_ID,
                "kind": "today",
                "source": SOURCE,
                "unit": "Wh",
                "value": 123.456789012345,
                "last_reset": MIDNIGHT,
            }
        ]
        assert entity.available is True and entity.native_value == 123.456789012345
        assert entity.last_reset == MIDNIGHT

    @pytest.mark.asyncio
    async def test_a_negative_state_is_restored(self) -> None:
        entity = _today()
        entity.last_state = _last(
            "-0.25", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat()
        )
        await entity.async_added_to_hass()
        assert entity.native_value == -0.25

    @pytest.mark.asyncio
    async def test_a_restored_state_of_an_earlier_day_is_reset_at_startup(self) -> None:
        entity = _today()
        entity.last_state = _last(
            "7.5", unit_of_measurement="kWh", last_reset=YESTERDAY.isoformat()
        )
        await entity.async_added_to_hass()
        assert entity.native_value == 0.0
        assert entity.last_reset == MIDNIGHT

    @pytest.mark.asyncio
    async def test_a_last_reset_that_cannot_be_read_counts_as_none(self) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("today", None, coordinator=coordinator)
        entity.last_state = _last("7.5", unit_of_measurement="kWh", last_reset="yesterday-ish")
        await entity.async_added_to_hass()
        assert coordinator.restore_calls[0]["last_reset"] is None
        assert entity.native_value == 0.0 and entity.last_reset == MIDNIGHT

    @pytest.mark.asyncio
    async def test_the_total_is_restored_and_has_no_last_reset(self) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("total", None, coordinator=coordinator)
        entity.last_state = _last("1234.5", unit_of_measurement="kWh")
        await entity.async_added_to_hass()
        assert entity.native_value == 1234.5 and entity.last_reset is None
        assert coordinator.restore_calls[0]["last_reset"] is None

    @pytest.mark.asyncio
    async def test_a_today_integral_has_a_last_reset_and_the_total_never(self) -> None:
        coordinator = _DeltaCoordinator()
        today = _sensor_for("today", None, coordinator=coordinator)
        total = _sensor_for("total", None, coordinator=coordinator)
        await today.async_added_to_hass()
        await total.async_added_to_hass()
        coordinator.integrals.hand_over(
            {
                COMBINED_TODAY: _Base(2.0, MIDNIGHT),
                COMBINED_TOTAL: _Base(50.0, None),
            },
            NOW,
            BERLIN,
        )
        assert today.last_reset == MIDNIGHT and total.last_reset is None


# ---------------------------------------------------------------------------
# The coordinator pushes
# ---------------------------------------------------------------------------


class TestPushes:
    @pytest.mark.asyncio
    async def test_the_sensor_subscribes_when_added_and_unsubscribes_when_removed(self) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("today", SOURCE, coordinator=coordinator)
        await entity.async_added_to_hass()
        assert list(coordinator.subscribers) == [TODAY_ID]
        await entity.async_will_remove_from_hass()
        assert coordinator.unsubscribed == [TODAY_ID]
        await entity.async_will_remove_from_hass()  # a second removal does nothing
        assert coordinator.unsubscribed == [TODAY_ID]

    @pytest.mark.asyncio
    async def test_a_booking_is_written_as_a_state_at_full_precision(self) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("today", SOURCE, coordinator=coordinator)
        entity.last_state = _last("1.0", unit_of_measurement="Wh", last_reset=MIDNIGHT.isoformat())
        await entity.async_added_to_hass()
        before = entity.writes
        changed = coordinator.integrals.book({SOURCE: (0.123456789012, "Wh")})
        coordinator.push(TODAY_ID, changed[0])
        assert entity.writes == before + 1  # a state_changed event reaches the recorder
        assert entity.native_value == pytest.approx(1.123456789012, abs=1e-15)
        assert entity.native_value != round(entity.native_value, 3)

    @pytest.mark.asyncio
    async def test_the_base_makes_an_unavailable_sensor_available_with_exactly_its_state(
        self,
    ) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("today", None, coordinator=coordinator)
        await entity.async_added_to_hass()
        assert entity.available is False
        (changed,) = coordinator.integrals.hand_over(
            {COMBINED_TODAY: _Base(0.0125, MIDNIGHT)}, NOW, BERLIN
        )
        coordinator.push(COMBINED_TODAY, changed)
        assert entity.available is True
        assert entity.native_value == 0.0125 and entity.last_reset == MIDNIGHT

    @pytest.mark.asyncio
    async def test_the_midnight_shows_in_the_sensor(self) -> None:
        coordinator = _DeltaCoordinator()
        entity = _sensor_for("today", None, coordinator=coordinator)
        entity.last_state = _last("3.0", unit_of_measurement="kWh", last_reset=MIDNIGHT.isoformat())
        await entity.async_added_to_hass()
        new_midnight = datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN)
        (changed,) = coordinator.integrals.reset_today(new_midnight)
        coordinator.push(COMBINED_TODAY, changed)
        assert entity.native_value == 0.0 and entity.last_reset == new_midnight


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def _unique(entity: Any) -> str:
    unique_id: str = entity.unique_id
    return unique_id


def _delta_entities(platform: _Platform) -> list[Any]:
    return [e for batch in platform.added for e in batch if "Delta" in type(e).__name__]


class TestSetup:
    @pytest.fixture(autouse=True)
    def _fresh_registry(self) -> Any:
        _registry[0] = _Registry({})
        yield
        _registry[0] = _Registry({})

    async def test_every_pv_input_gets_its_own_and_the_two_combined_sensors_exist(self) -> None:
        entry = _SetupEntry([SOURCE, OTHER], ["sensor.export"])
        platform = _Platform(entry, {SOURCE: _state(POWER), OTHER: _state(POWER)})
        platform.coordinator.pv_input_ids = [SOURCE, OTHER]
        await platform.setup()
        delta = _delta_entities(platform)
        assert [(type(e).__name__, e._source_entity_id, e.entity_id) for e in delta] == [
            ("EffyPvDeltaIntegralSensor", SOURCE, "sensor.effy_pv_south_delta_integral_today"),
            ("EffyPvDeltaIntegralSensor", OTHER, "sensor.effy_pv_north_delta_integral_today"),
            ("EffyPvDeltaIntegralSensor", None, COMBINED_TODAY),
            ("EffyPvDeltaIntegralSensor", None, COMBINED_TOTAL),
        ]
        assert len({_unique(e) for e in delta}) == 4

    async def test_without_a_pv_input_none_exist_and_the_combined_ones_leave_the_registry(
        self,
    ) -> None:
        entry = _SetupEntry([], ["sensor.export"])
        platform = _Platform(entry, {})
        platform.coordinator.pv_input_ids = []
        _registry[0] = _Registry(
            {
                ("sensor", "effy", "entry1_pv_deltasum_integral_today"): COMBINED_TODAY,
                ("sensor", "effy", "entry1_pv_deltasum_integral_total"): COMBINED_TOTAL,
                ("sensor", "effy", "entry1_recalculated_from"): "sensor.effy_recalculated_from",
            }
        )
        await platform.setup()
        assert _delta_entities(platform) == []
        assert sorted(_registry[0].removed) == [COMBINED_TODAY, COMBINED_TOTAL]  # not the others

    async def test_the_per_input_sensor_of_a_removed_input_leaves_with_its_input(self) -> None:
        entry = _SetupEntry([SOURCE], ["sensor.export"])
        platform = _Platform(entry, {SOURCE: _state(POWER)})
        platform.coordinator.pv_input_ids = [SOURCE]
        _registry[0] = _Registry(
            {
                ("sensor", "effy", "entry1_pv_south_delta_integral_today"): TODAY_ID,
                ("sensor", "effy", "entry1_pv_north_delta_integral_today"): (
                    "sensor.effy_pv_north_delta_integral_today"
                ),
                ("sensor", "effy", "entry1_pv_deltasum_integral_today"): COMBINED_TODAY,
                ("sensor", "effy", "entry1_pv_deltasum_integral_total"): COMBINED_TOTAL,
                ("sensor", "effy", "entry1_pv_south_power"): "sensor.effy_pv_south_power",
            }
        )
        await platform.setup()
        assert _registry[0].removed == ["sensor.effy_pv_north_delta_integral_today"]

    async def test_a_source_named_pv_is_not_mistaken_for_a_combined_sensor(self) -> None:
        entry = _SetupEntry(["sensor.pv"], ["sensor.export"])
        platform = _Platform(entry, {"sensor.pv": _state(POWER)})
        platform.coordinator.pv_input_ids = ["sensor.pv"]
        _registry[0] = _Registry(
            {
                ("sensor", "effy", "entry1_pv_delta_integral_today"): (
                    "sensor.effy_pv_delta_integral_today"
                ),
                ("sensor", "effy", "entry1_pv_deltasum_integral_today"): COMBINED_TODAY,
                ("sensor", "effy", "entry1_pv_deltasum_integral_total"): COMBINED_TOTAL,
            }
        )
        await platform.setup()
        assert _registry[0].removed == []  # all three are wanted
        assert len(_delta_entities(platform)) == 3

    async def test_other_platforms_entities_are_never_removed(self) -> None:
        entry = _SetupEntry([], ["sensor.export"])
        platform = _Platform(entry, {})
        platform.coordinator.pv_input_ids = []
        _registry[0] = _Registry(
            {("button", "effy", "entry1_x_delta_integral_today"): "button.effy_x"}
        )
        await platform.setup()
        assert _registry[0].removed == []
