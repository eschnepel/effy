"""TASK-0009 — the Effy sensors' published descriptors, their restore, and the
recalculated-from sensor's attributes (ADR-021).

The real ``sensor.py`` is loaded by ``test_standby_sensor`` under a private package
name with Home Assistant stubbed only while it loads; this file reuses that loaded
module and a small fake coordinator that keeps the registry's semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_standby_sensor import _sensor

UTC = timezone.utc
PV, BMS, OUT = "sensor.pv_south", "sensor.bms_discharge", "sensor.bms_charge"
RESTORED_AT = datetime(2026, 10, 18, 9, 0, 5, tzinfo=UTC)


class _Entry:
    entry_id = "entry1"

    def __init__(self, max_history_days: int | None = 28) -> None:
        self.options: dict[str, Any] = {"output_sensors": [OUT]}
        if max_history_days is not None:
            self.options["max_history_days"] = max_history_days


class _Coordinator:
    """Keeps what the entities use: the registry, the channels, the two role lists."""

    pv_input_ids = [PV]
    bms_input_ids = [BMS]

    def __init__(self) -> None:
        self.recalculated_from: datetime | None = None
        self.registry: dict[str, dict[str, Any]] = {}
        self.updates: dict[str, Callable[..., None]] = {}
        self.closing: list[Callable[[datetime], None]] = []

    def subscribe(self, _entity_id: str, _cb: Callable[..., None]) -> Callable[[], None]:
        return lambda: None

    def subscribe_updates(self, entity_id: str, cb: Callable[..., None]) -> Callable[[], None]:
        self.updates[entity_id] = cb
        return lambda: None

    def subscribe_recalculated_from(self, cb: Callable[[datetime], None]) -> Callable[[], None]:
        self.closing.append(cb)
        return lambda: self.closing.remove(cb)

    def register_published(self, descriptor: dict[str, Any]) -> Callable[[], None]:
        stored = dict(descriptor)
        self.registry[stored["entity_id"]] = stored

        def unregister() -> None:
            if self.registry.get(stored["entity_id"]) is stored:
                del self.registry[stored["entity_id"]]

        return unregister

    def published_descriptors(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self.registry.values()]

    def close(self, ts: datetime) -> None:
        for cb in list(self.closing):
            cb(ts)


def _last(state: str, attributes: dict[str, Any] | None = None, **kw: Any) -> Any:
    return SimpleNamespace(
        state=state,
        attributes=attributes or {},
        last_updated=kw.get("last_updated", datetime(2026, 10, 18, 8, 55, tzinfo=UTC)),
    )


def _series(cls: str, source: str, coordinator: _Coordinator | None = None) -> Any:
    return getattr(_sensor, cls)(object(), _Entry(), coordinator or _Coordinator(), source)


def _recalculated(coordinator: _Coordinator | None = None, entry: _Entry | None = None) -> Any:
    return _sensor.EffyRecalculatedFromSensor(
        object(), entry or _Entry(), coordinator or _Coordinator()
    )


@pytest.fixture(autouse=True)
def _fixed_restore_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_sensor, "_utcnow", lambda: RESTORED_AT)


SERIES = [
    ("EffySensor", PV, "sensor.effy_pv_south", "effective", "input", "pv"),
    ("EffySensor", BMS, "sensor.effy_bms_discharge", "effective", "input", "bms"),
    ("EffyDerivedPowerSensor", PV, "sensor.effy_pv_south_power", "derived_power", "input", "pv"),
    (
        "EffyDerivedPowerSensor",
        OUT,
        "sensor.effy_bms_charge_power",
        "derived_power",
        "output",
        "bms",
    ),
    ("EffySmoothedSensor", BMS, "sensor.effy_bms_discharge_smoothed", "smoothed", "input", "bms"),
]


class TestRegistration:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("cls", "source", "entity_id", "series", "direction", "role"), SERIES)
    async def test_each_series_sensor_registers_its_descriptor_when_added(
        self, cls: str, source: str, entity_id: str, series: str, direction: str, role: str
    ) -> None:
        coordinator = _Coordinator()
        entity = _series(cls, source, coordinator)
        await entity.async_added_to_hass()
        assert coordinator.registry == {
            entity_id: {
                "entity_id": entity_id,
                "series": series,
                "source_entity_id": source,
                "direction": direction,
                "role": role,
                "aggregations": ["mean", "state"],
            }
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("cls", "source", "entity_id", "series", "direction", "role"), SERIES)
    async def test_removing_the_sensor_unregisters_it(
        self, cls: str, source: str, entity_id: str, series: str, direction: str, role: str
    ) -> None:
        coordinator = _Coordinator()
        entity = _series(cls, source, coordinator)
        await entity.async_added_to_hass()
        await entity.async_will_remove_from_hass()
        assert coordinator.registry == {}

    @pytest.mark.asyncio
    async def test_the_standby_sensor_registers_as_an_output_with_the_recorders_aggregations(
        self,
    ) -> None:
        coordinator = _Coordinator()
        entity = _sensor.EffyStandbyPowerSensor(
            object(), _Entry(), _StandbyCoordinator(coordinator)
        )
        await entity.async_added_to_hass()
        assert coordinator.registry["sensor.effy_standby_power"] == {
            "entity_id": "sensor.effy_standby_power",
            "series": "standby_power",
            "source_entity_id": None,
            "direction": "output",
            "role": "bms",
            "aggregations": ["mean", "min", "max"],
        }
        await entity.async_will_remove_from_hass()
        assert coordinator.registry == {}


class _StandbyCoordinator:
    """The standby sensor's own coordinator surface, backed by the shared fake."""

    def __init__(self, inner: _Coordinator) -> None:
        self._inner = inner
        self.standby_enabled = True

    def restore_standby(self, _data: Any) -> Any:
        return _sensor.DrainState()

    def subscribe_standby(self, _cb: Callable[..., None]) -> Callable[[], None]:
        return lambda: None

    def register_published(self, descriptor: dict[str, Any]) -> Callable[[], None]:
        return self._inner.register_published(descriptor)


class TestTheWholeFixture:
    """The acceptance-criteria fixture: one PV input, one BMS input, one output."""

    @staticmethod
    async def _everything(coordinator: _Coordinator) -> Any:
        for cls, source in (
            ("EffySensor", PV),
            ("EffySensor", BMS),
            ("EffyDerivedPowerSensor", PV),
            ("EffySmoothedSensor", BMS),
            ("EffyDerivedPowerSensor", OUT),
        ):
            await _series(cls, source, coordinator).async_added_to_hass()
        standby = _sensor.EffyStandbyPowerSensor(
            object(), _Entry(), _StandbyCoordinator(coordinator)
        )
        await standby.async_added_to_hass()
        recalculated = _recalculated(coordinator, _Entry(max_history_days=14))
        await recalculated.async_added_to_hass()
        return recalculated

    @pytest.mark.asyncio
    async def test_the_closing_step_lists_exactly_the_six_sensors_and_not_itself(self) -> None:
        coordinator = _Coordinator()
        recalculated = await self._everything(coordinator)
        coordinator.close(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
        attrs = recalculated.extra_state_attributes
        assert (attrs["schema_version"], attrs["max_history_days"]) == (1, 14)
        listed = {s["entity_id"]: s for s in attrs["sensors"]}
        assert sorted(listed) == [
            "sensor.effy_bms_charge_power",
            "sensor.effy_bms_discharge",
            "sensor.effy_bms_discharge_smoothed",
            "sensor.effy_pv_south",
            "sensor.effy_pv_south_power",
            "sensor.effy_standby_power",
        ]
        summary = {
            eid: (s["series"], s["source_entity_id"], s["direction"], s["role"])
            for eid, s in listed.items()
        }
        assert summary == {
            "sensor.effy_pv_south": ("effective", PV, "input", "pv"),
            "sensor.effy_pv_south_power": ("derived_power", PV, "input", "pv"),
            "sensor.effy_bms_discharge": ("effective", BMS, "input", "bms"),
            "sensor.effy_bms_discharge_smoothed": ("smoothed", BMS, "input", "bms"),
            "sensor.effy_bms_charge_power": ("derived_power", OUT, "output", "bms"),
            "sensor.effy_standby_power": ("standby_power", None, "output", "bms"),
        }
        for s in listed.values():
            assert set(s) == {
                "entity_id",
                "series",
                "source_entity_id",
                "direction",
                "role",
                "aggregations",
            }

    def test_without_a_bms_input_there_is_no_standby_entity_to_register(self) -> None:
        coordinator = _StandbyCoordinator(_Coordinator())
        coordinator.standby_enabled = False
        assert _sensor._standby_entities(object(), _Entry(), coordinator) == []
        coordinator.standby_enabled = True
        entities = _sensor._standby_entities(object(), _Entry(), coordinator)
        assert [e.entity_id for e in entities] == ["sensor.effy_standby_power"]


class TestRestoreOfTheSeriesSensors:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("cls", ["EffySensor", "EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_value_and_unit_come_back_as_they_were(self, cls: str) -> None:
        entity = _series(cls, PV)
        entity.last_state = _last("1.234", {"unit_of_measurement": "kW", "friendly_name": "x"})
        await entity.async_added_to_hass()
        assert entity._attr_native_value == 1.234
        assert entity._attr_native_unit_of_measurement == "kW"
        assert entity.writes == 0  # restoring writes no state of its own, nor any statistic

    @pytest.mark.asyncio
    async def test_the_effective_sensor_restores_its_own_attributes_and_nothing_else(self) -> None:
        entity = _series("EffySensor", PV)
        entity.last_state = _last(
            "2.0",
            {
                "unit_of_measurement": "W",
                "source_entity": PV,
                "total_loss_w": 5.0,
                "loss_share_w": 2.0,
                "friendly_name": "framework attribute",
            },
        )
        await entity.async_added_to_hass()
        assert entity._attr_extra_state_attributes == {
            "source_entity": PV,
            "total_loss_w": 5.0,
            "loss_share_w": 2.0,
        }
        assert entity._source_unit == "W"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cls", ["EffySensor", "EffyDerivedPowerSensor", "EffySmoothedSensor"])
    @pytest.mark.parametrize("state", ["unknown", "unavailable", "not a number", ""])
    async def test_a_last_state_that_is_not_a_number_restores_nothing(
        self, cls: str, state: str
    ) -> None:
        entity = _series(cls, PV)
        entity.last_state = _last(state, {"unit_of_measurement": "kW"})
        await entity.async_added_to_hass()
        assert entity._attr_native_value is None
        assert entity._attr_native_unit_of_measurement is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cls", ["EffySensor", "EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_without_a_last_state_the_sensor_starts_unknown(self, cls: str) -> None:
        entity = _series(cls, PV)
        await entity.async_added_to_hass()
        assert entity._attr_native_value is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cls", ["EffySensor", "EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_a_restored_sensor_carries_no_restored_marker(self, cls: str) -> None:
        entity = _series(cls, PV)
        entity.last_state = _last("1.0", {"unit_of_measurement": "W"})
        await entity.async_added_to_hass()
        assert "restored_as_of" not in (getattr(entity, "_attr_extra_state_attributes", None) or {})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cls", ["EffySensor", "EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_the_first_push_replaces_the_restored_value(self, cls: str) -> None:
        coordinator = _Coordinator()
        entity = _series(cls, PV, coordinator)
        entity.last_state = _last("1.0", {"unit_of_measurement": "kW"})
        await entity.async_added_to_hass()
        coordinator.updates[entity.entity_id](7.5, "W")
        assert entity._attr_native_value == 7.5
        assert entity._attr_native_unit_of_measurement == "W"
        assert entity.writes == 1


FIRST = {"entity_id": "sensor.effy_a", "series": "effective"}


class TestRecalculatedFromAttributes:
    def test_before_anything_the_scalars_are_present_and_the_list_is_absent(self) -> None:
        entity = _recalculated()
        assert entity.extra_state_attributes == {"schema_version": 1, "max_history_days": 28}
        assert entity._attr_native_value is None

    def test_the_history_days_default_when_the_option_is_missing(self) -> None:
        entity = _recalculated(entry=_Entry(max_history_days=None))
        assert entity.extra_state_attributes["max_history_days"] == 28

    def test_it_forces_an_update_on_every_write(self) -> None:
        assert _recalculated()._attr_force_update is True

    @pytest.mark.asyncio
    async def test_the_closing_step_publishes_the_registered_sensors_sorted(self) -> None:
        coordinator = _Coordinator()
        entity = _recalculated(coordinator, _Entry(max_history_days=14))
        await entity.async_added_to_hass()
        for source, cls in ((PV, "EffySensor"), (OUT, "EffyDerivedPowerSensor")):
            await _series(cls, source, coordinator).async_added_to_hass()
        ts = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
        coordinator.close(ts)
        attrs = entity.extra_state_attributes
        assert entity._attr_native_value == ts
        assert (attrs["schema_version"], attrs["max_history_days"]) == (1, 14)
        assert [s["entity_id"] for s in attrs["sensors"]] == [
            "sensor.effy_bms_charge_power",
            "sensor.effy_pv_south",
        ]
        assert entity.writes == 1

    @pytest.mark.asyncio
    async def test_a_late_sensor_appears_at_the_next_closing_step_and_not_before(self) -> None:
        coordinator = _Coordinator()
        entity = _recalculated(coordinator)
        await entity.async_added_to_hass()
        coordinator.close(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
        assert entity.extra_state_attributes["sensors"] == []
        late = _series("EffySmoothedSensor", BMS, coordinator)
        await late.async_added_to_hass()  # created after the last closing step
        assert entity.extra_state_attributes["sensors"] == []
        coordinator.close(datetime(2026, 10, 18, 9, 5, tzinfo=UTC))
        assert [s["series"] for s in entity.extra_state_attributes["sensors"]] == ["smoothed"]

    @pytest.mark.asyncio
    async def test_a_removed_sensor_is_gone_at_the_next_closing_step(self) -> None:
        coordinator = _Coordinator()
        entity = _recalculated(coordinator)
        await entity.async_added_to_hass()
        gone = _series("EffySensor", PV, coordinator)
        await gone.async_added_to_hass()
        coordinator.close(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
        assert len(entity.extra_state_attributes["sensors"]) == 1
        await gone.async_will_remove_from_hass()
        coordinator.close(datetime(2026, 10, 18, 9, 5, tzinfo=UTC))
        assert entity.extra_state_attributes["sensors"] == []

    @pytest.mark.asyncio
    async def test_the_sensor_itself_is_never_listed(self) -> None:
        coordinator = _Coordinator()
        entity = _recalculated(coordinator)
        await entity.async_added_to_hass()
        coordinator.close(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
        assert coordinator.registry == {}
        assert entity.extra_state_attributes["sensors"] == []


PREVIOUS = {
    "schema_version": 1,
    "max_history_days": 14,
    "sensors": [
        {"entity_id": "sensor.effy_a", "series": "effective"},
        {"entity_id": "sensor.effy_b", "series": "smoothed"},
    ],
}


class TestRecalculatedFromRestore:
    @pytest.mark.asyncio
    async def test_state_and_all_attributes_come_back_marked_as_restored(self) -> None:
        entity = _recalculated()
        entity.last_state = _last(
            "2026-10-18T08:50:00+00:00",
            dict(PREVIOUS),
            last_updated=datetime(2026, 10, 18, 8, 55, 3, tzinfo=UTC),
        )
        await entity.async_added_to_hass()
        assert entity._attr_native_value == datetime(2026, 10, 18, 8, 50, tzinfo=UTC)
        assert entity.extra_state_attributes == {
            **PREVIOUS,
            "restored_as_of": "2026-10-18T08:55:03+00:00",
            "restored_at": RESTORED_AT.isoformat(),
        }

    @pytest.mark.asyncio
    async def test_a_second_reboot_keeps_the_age_of_the_data(self) -> None:
        entity = _recalculated()
        entity.last_state = _last(
            "2026-10-18T08:50:00+00:00",
            {
                **PREVIOUS,
                "restored_as_of": "2026-10-18T07:00:00+00:00",
                "restored_at": "2026-10-18T08:55:00+00:00",
            },
            last_updated=datetime(2026, 10, 18, 8, 55, tzinfo=UTC),
        )
        await entity.async_added_to_hass()
        attrs = entity.extra_state_attributes
        assert attrs["restored_as_of"] == "2026-10-18T07:00:00+00:00"  # not the first restore
        assert attrs["restored_at"] == RESTORED_AT.isoformat()  # this restore

    @pytest.mark.asyncio
    async def test_the_restored_history_days_are_the_old_ones_until_the_next_closing_step(
        self,
    ) -> None:
        coordinator = _Coordinator()
        entity = _recalculated(coordinator, _Entry(max_history_days=7))  # changed option
        entity.last_state = _last("2026-10-18T08:50:00+00:00", dict(PREVIOUS))
        await entity.async_added_to_hass()
        assert entity.extra_state_attributes["max_history_days"] == 14
        coordinator.close(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
        assert entity.extra_state_attributes["max_history_days"] == 7

    @pytest.mark.asyncio
    async def test_the_next_closing_step_drops_the_markers_and_writes_the_current_list(
        self,
    ) -> None:
        coordinator = _Coordinator()
        entity = _recalculated(coordinator)
        entity.last_state = _last("2026-10-18T08:50:00+00:00", dict(PREVIOUS))
        await entity.async_added_to_hass()
        await _series("EffySensor", PV, coordinator).async_added_to_hass()
        coordinator.close(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
        attrs = entity.extra_state_attributes
        assert "restored_as_of" not in attrs and "restored_at" not in attrs
        assert [s["entity_id"] for s in attrs["sensors"]] == ["sensor.effy_pv_south"]

    @pytest.mark.asyncio
    async def test_a_previous_state_without_attributes_keeps_the_current_scalars_and_no_list(
        self,
    ) -> None:
        entity = _recalculated()  # the first start after the upgrade
        entity.last_state = _last("2026-10-18T08:50:00+00:00", {})
        await entity.async_added_to_hass()
        assert entity._attr_native_value == datetime(2026, 10, 18, 8, 50, tzinfo=UTC)
        attrs = entity.extra_state_attributes
        assert attrs["schema_version"] == 1 and attrs["max_history_days"] == 28
        assert "sensors" not in attrs
        assert "restored_as_of" in attrs and "restored_at" in attrs

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["unknown", "unavailable", "garbage", ""])
    async def test_no_usable_previous_state_restores_nothing(self, state: str) -> None:
        entity = _recalculated()
        entity.last_state = _last(state, dict(PREVIOUS))
        await entity.async_added_to_hass()
        assert entity._attr_native_value is None
        assert entity.extra_state_attributes == {"schema_version": 1, "max_history_days": 28}

    @pytest.mark.asyncio
    async def test_without_a_previous_state_nothing_is_restored(self) -> None:
        entity = _recalculated()
        await entity.async_added_to_hass()
        assert entity._attr_native_value is None
        assert entity.extra_state_attributes == {"schema_version": 1, "max_history_days": 28}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad", ["a string", [1, 2], [{"entity_id": "x"}, "not a dict"], {"entity_id": "x"}]
    )
    async def test_a_malformed_list_is_ignored(self, bad: Any) -> None:
        entity = _recalculated()
        entity.last_state = _last("2026-10-18T08:50:00+00:00", {**PREVIOUS, "sensors": bad})
        await entity.async_added_to_hass()
        assert "sensors" not in entity.extra_state_attributes
        assert entity._attr_native_value == datetime(2026, 10, 18, 8, 50, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_malformed_scalars_fall_back_to_the_current_ones(self) -> None:
        entity = _recalculated(entry=_Entry(max_history_days=21))
        entity.last_state = _last(
            "2026-10-18T08:50:00+00:00", {"schema_version": "1", "max_history_days": True}
        )
        await entity.async_added_to_hass()
        attrs = entity.extra_state_attributes
        assert (attrs["schema_version"], attrs["max_history_days"]) == (1, 21)

    @pytest.mark.asyncio
    async def test_the_restored_list_is_a_copy(self) -> None:
        entity = _recalculated()
        previous = dict(PREVIOUS)
        entity.last_state = _last("2026-10-18T08:50:00+00:00", previous)
        await entity.async_added_to_hass()
        entity.extra_state_attributes["sensors"][0]["series"] = "changed"
        assert previous["sensors"][0]["series"] == "effective"

    @pytest.mark.asyncio
    async def test_a_recalculation_that_finished_before_adding_takes_over_only_the_timestamp(
        self,
    ) -> None:
        coordinator = _Coordinator()
        coordinator.recalculated_from = datetime(2026, 10, 18, 9, 5, tzinfo=UTC)
        entity = _recalculated(coordinator)
        entity.last_state = _last("2026-10-18T08:50:00+00:00", dict(PREVIOUS))
        await entity.async_added_to_hass()
        assert entity._attr_native_value == datetime(2026, 10, 18, 9, 5, tzinfo=UTC)
        assert entity.extra_state_attributes["sensors"] == PREVIOUS["sensors"]
        assert "restored_at" in entity.extra_state_attributes

    @pytest.mark.asyncio
    async def test_removing_the_sensor_unsubscribes_it(self) -> None:
        coordinator = _Coordinator()
        entity = _recalculated(coordinator)
        await entity.async_added_to_hass()
        assert len(coordinator.closing) == 1
        await entity.async_will_remove_from_hass()
        assert coordinator.closing == []
