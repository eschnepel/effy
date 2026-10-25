"""
The sensor platform's setup and the identity of its entities (TASK-0028, slice 6).

``sensor.py`` is the real module loaded by ``test_standby_sensor`` (Home Assistant
stubbed only while it loads). Here ``async_setup_entry`` runs against a fake hass
whose states say what each source sensor is, and the late-binding listener
(ADR-013 Decision 1) is driven by hand with the events Home Assistant would fire.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_published_sensors import _Coordinator, _last
from tests.test_standby_sensor import _sensor

PV_E = "sensor.pv_energy"  # PV input, energy family
PV_W = "sensor.pv_power"  # PV input, power family
OUT_E = "sensor.export_energy"  # output, energy family
OUT_W = "sensor.export_power"  # output, power family
SOURCE = "sensor.pv_south"  # the PV input the fake coordinator is configured with

ENERGY = {"state_class": "total_increasing", "unit_of_measurement": "kWh"}
POWER = {"state_class": "measurement", "unit_of_measurement": "W"}


def _state(attributes: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(state="1.0", attributes=attributes)


class _SetupCoordinator(_Coordinator):
    def __init__(self) -> None:
        super().__init__()
        self.standby_enabled = False
        self.refreshes = 0

    def force_refresh(self) -> None:
        self.refreshes += 1


class _SetupEntry:
    entry_id = "entry1"

    def __init__(self, pv: list[str], out: list[str]) -> None:
        self.options: dict[str, Any] = {
            "pv_input_sensors": pv,
            "bms_input_sensors": [],
            "output_sensors": out,
        }
        self.unloads: list[Any] = []

    def async_on_unload(self, callback: Any) -> None:
        self.unloads.append(callback)


class _Platform:
    """hass, the coordinator behind it, and what the platform added."""

    def __init__(self, entry: _SetupEntry, states: dict[str, Any]) -> None:
        self.entry = entry
        self.states = states
        self.coordinator = _SetupCoordinator()
        self.hass = SimpleNamespace(
            data={_sensor.DOMAIN: {entry.entry_id: self.coordinator}},
            states=SimpleNamespace(get=states.get),
        )
        self.added: list[list[Any]] = []
        self.add_kwargs: list[dict[str, Any]] = []

    def add_entities(self, entities: list[Any], **kwargs: Any) -> None:
        self.added.append(list(entities))
        self.add_kwargs.append(kwargs)

    async def setup(self) -> None:
        await _sensor.async_setup_entry(self.hass, self.entry, self.add_entities)


def _kinds(entities: list[Any]) -> list[tuple[str, str | None]]:
    return [(type(e).__name__, getattr(e, "_source_entity_id", None)) for e in entities]


class _Tracker:
    """Stands in for ``async_track_state_change_event``."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, list[str], Any]] = []
        self.unsubscribed = 0

    def __call__(self, hass: Any, entity_ids: list[str], callback: Any) -> Any:
        self.calls.append((hass, list(entity_ids), callback))

        def unsub() -> None:
            self.unsubscribed += 1

        return unsub


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> _Tracker:
    stub = _Tracker()
    monkeypatch.setattr(_sensor, "async_track_state_change_event", stub)
    return stub


def _event(entity_id: str, new_state: Any) -> Any:
    return SimpleNamespace(data={"entity_id": entity_id, "new_state": new_state})


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


class TestSetupEntry:
    async def test_each_source_gets_the_entities_its_state_calls_for(
        self, tracker: _Tracker
    ) -> None:
        """ADR-012: an energy-family source (input or output) gets a derived-power
        sensor; a power-family *input* a smoothed one; a power-family output
        neither. Every input has its effective sensor, and the recalculated-from
        sensor is always there."""
        entry = _SetupEntry([PV_E, PV_W], [OUT_E, OUT_W])
        platform = _Platform(
            entry,
            {
                PV_E: _state(ENERGY),
                PV_W: _state(POWER),
                OUT_E: _state(ENERGY),
                OUT_W: _state(POWER),
            },
        )

        await platform.setup()

        assert len(platform.added) == 1 and platform.add_kwargs == [{"update_before_add": False}]
        assert _kinds(platform.added[0]) == [
            ("EffySensor", PV_E),
            ("EffySensor", PV_W),
            ("EffyDerivedPowerSensor", PV_E),
            ("EffySmoothedSensor", PV_W),
            ("EffyDerivedPowerSensor", OUT_E),
            ("EffyRecalculatedFromSensor", None),
        ]
        assert tracker.calls == [] and entry.unloads == []  # nothing is waiting
        assert platform.coordinator.refreshes == 1  # one recalculation on first load

    async def test_a_source_without_a_state_is_waited_for_not_dropped(
        self, tracker: _Tracker
    ) -> None:
        """ADR-013 Decision 1: at startup the source's own integration may not have
        reported. Its effective sensor exists at once; its derived entity is
        decided when the first state arrives."""
        entry = _SetupEntry([PV_E, PV_W], [OUT_W])
        platform = _Platform(entry, {PV_W: _state(POWER)})  # PV_E and OUT_W have no state yet

        await platform.setup()

        assert _kinds(platform.added[0]) == [
            ("EffySensor", PV_E),
            ("EffySensor", PV_W),
            ("EffySmoothedSensor", PV_W),
            ("EffyRecalculatedFromSensor", None),
        ]
        ((hass, waiting_for, callback),) = tracker.calls
        assert hass is platform.hass
        assert waiting_for == [PV_E, OUT_W]
        assert entry.unloads != [] and len(entry.unloads) == 1  # the listener ends with the entry
        assert platform.coordinator.refreshes == 1
        assert callable(callback)


# ---------------------------------------------------------------------------
# the late-binding listener
# ---------------------------------------------------------------------------


class TestLateBinding:
    async def _waiting(self, tracker: _Tracker) -> tuple[_Platform, Any]:
        """Three sources without a state: a power input, a power output, an energy output."""
        entry = _SetupEntry([PV_W], [OUT_W, OUT_E])
        platform = _Platform(entry, {})
        await platform.setup()
        platform.added.clear()
        return platform, tracker.calls[0][2]

    async def test_events_that_resolve_nothing_are_ignored(self, tracker: _Tracker) -> None:
        platform, on_event = await self._waiting(tracker)
        platform.states[PV_W] = _state(POWER)

        on_event(_event(PV_W, None))  # went unavailable → still no state
        on_event(_event("sensor.unrelated", _state(POWER)))  # nobody asked for it

        assert platform.added == []
        assert tracker.unsubscribed == 0

    async def test_the_first_state_decides_the_derived_entity(self, tracker: _Tracker) -> None:
        platform, on_event = await self._waiting(tracker)

        platform.states[OUT_W] = _state(POWER)
        on_event(_event(OUT_W, platform.states[OUT_W]))
        assert platform.added == []  # a power-family output gets no derived entity
        assert tracker.unsubscribed == 0  # two sources still wait

        platform.states[PV_W] = _state(POWER)
        on_event(_event(PV_W, platform.states[PV_W]))
        assert [_kinds(batch) for batch in platform.added] == [[("EffySmoothedSensor", PV_W)]]
        assert tracker.unsubscribed == 0  # one still waits

        platform.states[OUT_E] = _state(ENERGY)
        on_event(_event(OUT_E, platform.states[OUT_E]))
        assert [_kinds(batch) for batch in platform.added] == [
            [("EffySmoothedSensor", PV_W)],
            [("EffyDerivedPowerSensor", OUT_E)],
        ]
        assert tracker.unsubscribed == 1  # all resolved: the listener removes itself

    async def test_a_resolved_source_is_not_resolved_twice(self, tracker: _Tracker) -> None:
        platform, on_event = await self._waiting(tracker)
        platform.states[PV_W] = _state(POWER)

        on_event(_event(PV_W, platform.states[PV_W]))
        on_event(_event(PV_W, platform.states[PV_W]))  # a later state change of the same source

        assert [_kinds(batch) for batch in platform.added] == [[("EffySmoothedSensor", PV_W)]]


class _StrictTracker(_Tracker):
    """Like Home Assistant's tracker: removing the same listener twice raises
    ``ValueError: list.remove(x): x not in list`` (``helpers/event.py``)."""

    def __call__(self, hass: Any, entity_ids: list[str], callback: Any) -> Any:
        self.calls.append((hass, list(entity_ids), callback))
        registered = [callback]

        def unsub() -> None:
            registered.remove(callback)
            self.unsubscribed += 1

        return unsub


class TestLateBindingUnload:
    """The listener removes itself once every source has a state, and the entry
    removes it again on unload (an options change reloads the entry)."""

    async def _platform(self, monkeypatch: pytest.MonkeyPatch) -> tuple[_Platform, _StrictTracker]:
        tracker = _StrictTracker()
        monkeypatch.setattr(_sensor, "async_track_state_change_event", tracker)
        platform = _Platform(_SetupEntry([PV_W], []), {})
        await platform.setup()
        return platform, tracker

    async def test_unloading_after_the_listener_removed_itself_raises_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        platform, tracker = await self._platform(monkeypatch)
        platform.states[PV_W] = _state(POWER)
        tracker.calls[0][2](
            _event(PV_W, platform.states[PV_W])
        )  # resolves; listener removes itself
        assert tracker.unsubscribed == 1

        for unload in platform.entry.unloads:  # what Home Assistant does on unload
            unload()

        assert tracker.unsubscribed == 1  # the tracker was told once, not twice

    async def test_unloading_while_a_source_still_waits_removes_the_listener_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        platform, tracker = await self._platform(monkeypatch)

        for unload in platform.entry.unloads:
            unload()

        assert tracker.unsubscribed == 1

    async def test_removing_it_twice_in_a_row_is_harmless(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        platform, tracker = await self._platform(monkeypatch)
        for unload in platform.entry.unloads:
            unload()
            unload()
        assert tracker.unsubscribed == 1


# ---------------------------------------------------------------------------
# restoring a value that has no unit
# ---------------------------------------------------------------------------


class TestRestoreWithoutAUnit:
    """ADR-021 §6: a last state that is a number is restored even when it carries
    no unit; the unit then stays unset until the first push."""

    @pytest.mark.parametrize("cls", ["EffySensor", "EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_the_value_is_restored_and_the_unit_stays_unset(self, cls: str) -> None:
        entity = getattr(_sensor, cls)(
            object(), _SetupEntry([SOURCE], []), _SetupCoordinator(), SOURCE
        )
        entity.last_state = _last("12.5")  # no unit_of_measurement

        await entity.async_added_to_hass()

        assert entity._attr_native_value == 12.5
        assert entity._attr_native_unit_of_measurement is None

    async def test_the_effective_sensor_keeps_its_default_unit_and_restored_attributes(
        self,
    ) -> None:
        entity = _sensor.EffySensor(
            object(), _SetupEntry([SOURCE], []), _SetupCoordinator(), SOURCE
        )
        entity.last_state = _last(
            "12.5", {"source_entity": SOURCE, "total_loss_w": 3.0, "loss_share_w": 1.0, "other": 9}
        )

        await entity.async_added_to_hass()

        assert entity._source_unit == "W"
        assert entity._attr_extra_state_attributes == {
            "source_entity": SOURCE,
            "total_loss_w": 3.0,
            "loss_share_w": 1.0,
        }


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

SERIES = [
    ("EffySensor", "entry1_pv_south", "sensor.effy_pv_south", "(effective)"),
    (
        "EffyDerivedPowerSensor",
        "entry1_pv_south_power",
        "sensor.effy_pv_south_power",
        "(derived power)",
    ),
    (
        "EffySmoothedSensor",
        "entry1_pv_south_smoothed",
        "sensor.effy_pv_south_smoothed",
        "(smoothed)",
    ),
]


def _entity(cls: str, states: dict[str, Any]) -> Any:
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    return getattr(_sensor, cls)(hass, _SetupEntry([SOURCE], []), _SetupCoordinator(), SOURCE)


class TestIdentityOfTheSeriesSensors:
    @pytest.mark.parametrize(("cls", "unique_id", "entity_id", "suffix"), SERIES)
    def test_unique_and_entity_id_are_fixed(
        self, cls: str, unique_id: str, entity_id: str, suffix: str
    ) -> None:
        """The entity ID is one Effy publishes (ADR-021 §5); an assignment — Home
        Assistant assigns one when it adds the entity — does not change it."""
        entity = _entity(cls, {})
        assert entity.unique_id == unique_id
        assert entity.entity_id == entity_id
        entity.entity_id = "sensor.something_else_2"
        assert entity.entity_id == entity_id

    @pytest.mark.parametrize(("cls", "unique_id", "entity_id", "suffix"), SERIES)
    def test_the_name_is_the_sources_friendly_name_with_the_series(
        self, cls: str, unique_id: str, entity_id: str, suffix: str
    ) -> None:
        named = _entity(cls, {SOURCE: _state({"friendly_name": "PV South"})})
        assert named.name == f"PV South {suffix}"

    @pytest.mark.parametrize(("cls", "unique_id", "entity_id", "suffix"), SERIES)
    def test_without_a_friendly_name_or_a_state_the_name_uses_the_slug(
        self, cls: str, unique_id: str, entity_id: str, suffix: str
    ) -> None:
        assert _entity(cls, {SOURCE: _state({})}).name == f"pv_south {suffix}"
        assert _entity(cls, {}).name == f"pv_south {suffix}"

    @pytest.mark.parametrize(("cls", "unique_id", "entity_id", "suffix"), SERIES)
    def test_every_series_sensor_belongs_to_the_one_effy_device(
        self, cls: str, unique_id: str, entity_id: str, suffix: str
    ) -> None:
        info = _entity(cls, {}).device_info
        assert info["identifiers"] == {("effy", "entry1")}
        assert info["name"] == "Effy"


class TestIdentityOfTheFixedSensors:
    def _recalculated(self) -> Any:
        return _sensor.EffyRecalculatedFromSensor(
            object(), _SetupEntry([SOURCE], []), _SetupCoordinator()
        )

    def _standby(self) -> Any:
        return _sensor.EffyStandbyPowerSensor(
            object(), _SetupEntry([SOURCE], []), _SetupCoordinator()
        )

    def test_the_recalculated_from_sensor_keeps_its_published_entity_id(self) -> None:
        entity = self._recalculated()
        assert entity.unique_id == "entry1_recalculated_from"
        assert entity.entity_id == "sensor.effy_recalculated_from"  # ADR-021: a fixed entity ID
        entity.entity_id = "sensor.effy_recalculated_from_2"
        assert entity.entity_id == "sensor.effy_recalculated_from"
        assert entity.device_info["identifiers"] == {("effy", "entry1")}

    def test_the_standby_sensor_keeps_its_published_entity_id(self) -> None:
        entity = self._standby()
        entity.entity_id = "sensor.effy_standby_power_2"
        assert entity.entity_id == "sensor.effy_standby_power"


class TestRestorationTime:
    def test_it_is_the_current_time_in_utc(self) -> None:
        before = datetime.now(timezone.utc)
        restored_at = _sensor._utcnow()
        after = datetime.now(timezone.utc)
        assert before <= restored_at <= after
        assert restored_at.utcoffset() == timedelta(0)
