"""The branches of ``sensor.py`` the entity tests leave open: the live push and the
distribution of the effective sensor, the removal of an entity that was never added or is
removed twice, and the placeholders of the combined delta integral.

``sensor.py`` is the real module loaded by ``tests/test_standby_sensor.py`` with Home
Assistant stubbed.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_pv_delta_sensors import _input, _today, _total
from tests.test_published_sensors import PV, _Coordinator, _Entry, _recalculated
from tests.test_standby_sensor import _calc, _sensor

OTHER = "sensor.house_power"


class _CountingCoordinator(_Coordinator):
    """The published-sensor fake that also counts what the entities hand back."""

    def __init__(self) -> None:
        super().__init__()
        self.unsubscribed: list[str] = []

    def subscribe(self, entity_id: str, _cb: Callable[..., None]) -> Callable[[], None]:
        return lambda: self.unsubscribed.append(f"distribution:{entity_id}")

    def subscribe_updates(self, entity_id: str, cb: Callable[..., None]) -> Callable[[], None]:
        super().subscribe_updates(entity_id, cb)
        return lambda: self.unsubscribed.append(f"updates:{entity_id}")


def _hass(states: dict[str, Any] | None = None) -> Any:
    states = states or {}
    return SimpleNamespace(states=SimpleNamespace(get=states.get))


def _state(unit: str | None = None) -> Any:
    attributes = {} if unit is None else {"unit_of_measurement": unit}
    return SimpleNamespace(state="1", attributes=attributes)


def _series(
    cls: str, states: dict[str, Any] | None = None, coordinator: _Coordinator | None = None
) -> Any:
    return getattr(_sensor, cls)(_hass(states), _Entry(), coordinator or _Coordinator(), PV)


def _distribution(
    effective_w: dict[str, float], shares: dict[str, float] | None = None, loss_w: float = 0.0
) -> Any:
    return _calc.LossDistribution(
        total_loss_w=loss_w, shares=shares or {}, effective_values_w=effective_w
    )


class TestEffectiveSensorUpdate:
    def test_a_push_without_a_unit_keeps_the_unit_the_sensor_has(self) -> None:
        entity = _series("EffySensor")
        entity._on_updated(1.5, "kW")

        entity._on_updated(2.00049, None)

        assert entity._attr_native_value == 2.0
        assert entity._attr_native_unit_of_measurement == "kW"
        assert entity._source_unit == "kW"
        assert entity.writes == 2

    def test_a_push_with_a_unit_sets_it_as_the_unit_of_later_distributions(self) -> None:
        entity = _series("EffySensor")

        entity._on_updated(1.5, "kW")

        assert entity._attr_native_unit_of_measurement == "kW"
        assert entity._source_unit == "kW"


class TestEffectiveSensorDistribution:
    def test_a_distribution_without_a_value_for_the_source_changes_nothing(self) -> None:
        entity = _series("EffySensor", {PV: _state("kW")})
        entity._on_updated(4.0, "W")

        entity._on_distribution(_distribution({OTHER: 10.0}))

        assert entity._attr_native_value == 4.0
        assert entity._attr_native_unit_of_measurement == "W"
        assert entity._attr_extra_state_attributes == {}
        assert entity.writes == 1  # only the push

    def test_a_power_source_reports_its_effective_value_in_its_own_unit(self) -> None:
        entity = _series("EffySensor", {PV: _state("kW")})

        entity._on_distribution(
            _distribution({PV: 1500.0}, shares={PV: 12.34567}, loss_w=100.98765)
        )

        assert entity._attr_native_value == 1.5
        assert entity._attr_native_unit_of_measurement == "kW"
        assert entity._attr_extra_state_attributes == {
            "source_entity": PV,
            "total_loss_w": 100.988,
            "loss_share_w": 12.346,
        }
        assert entity.writes == 1

    def test_an_energy_source_is_labeled_as_the_power_it_is(self) -> None:
        entity = _series("EffySensor", {PV: _state("kWh")})

        entity._on_distribution(_distribution({PV: 2000.0}))

        assert entity._attr_native_value == 2.0
        assert entity._attr_native_unit_of_measurement == "kW"

    def test_a_source_without_a_unit_is_read_in_watts(self) -> None:
        entity = _series("EffySensor", {PV: _state()})

        entity._on_distribution(_distribution({PV: 321.0}))

        assert entity._attr_native_value == 321.0
        assert entity._attr_native_unit_of_measurement == "W"

    def test_a_source_without_a_state_keeps_the_unit_the_sensor_has(self) -> None:
        entity = _series("EffySensor")
        entity._on_updated(0.0, "kW")

        entity._on_distribution(_distribution({PV: 750.0}))

        assert entity._attr_native_value == 0.75
        assert entity._attr_native_unit_of_measurement == "kW"

    def test_a_source_without_a_share_has_a_share_of_zero(self) -> None:
        entity = _series("EffySensor", {PV: _state("W")})

        entity._on_distribution(_distribution({PV: 50.0}, shares={OTHER: 5.0}, loss_w=5.0))

        assert entity._attr_extra_state_attributes["loss_share_w"] == 0.0
        assert entity._attr_extra_state_attributes["total_loss_w"] == 5.0


class TestLivePushWithoutAUnit:
    @pytest.mark.parametrize("cls", ["EffyDerivedPowerSensor", "EffySmoothedSensor"])
    def test_the_unit_stays_as_it_was(self, cls: str) -> None:
        entity = _series(cls)
        entity._on_updated(10.0, "W")

        entity._on_updated(12.3456, None)

        assert entity._attr_native_value == 12.346
        assert entity._attr_native_unit_of_measurement == "W"
        assert entity.writes == 2

    @pytest.mark.parametrize("cls", ["EffyDerivedPowerSensor", "EffySmoothedSensor"])
    def test_a_push_before_any_unit_leaves_it_unset(self, cls: str) -> None:
        entity = _series(cls)

        entity._on_updated(5.0, None)

        assert entity._attr_native_value == 5.0
        assert entity._attr_native_unit_of_measurement is None


class TestRemovalOfAnEntityNeverAdded:
    """Home Assistant removes an entity whose adding failed or never completed: nothing was
    subscribed, so nothing is handed back."""

    @pytest.mark.parametrize(
        "cls",
        [
            "EffySensor",
            "EffyDerivedPowerSensor",
            "EffySmoothedSensor",
            "EffyPvSumSensor",
            "EffyPvShareSensor",
        ],
    )
    async def test_the_series_sensors_remove_without_a_subscription(self, cls: str) -> None:
        coordinator = _CountingCoordinator()
        if cls in ("EffyPvSumSensor", "EffyPvShareSensor"):
            entity = getattr(_sensor, cls)(object(), _Entry(), coordinator)
        else:
            entity = _series(cls, coordinator=coordinator)

        await entity.async_will_remove_from_hass()

        assert coordinator.unsubscribed == []
        assert coordinator.registry == {}

    async def test_the_recalculated_from_sensor_removes_without_a_subscription(self) -> None:
        coordinator = _CountingCoordinator()
        entity = _recalculated(coordinator)

        await entity.async_will_remove_from_hass()

        assert coordinator.closing == []
        assert entity._unsub_coordinator is None


class TestRemovalTwice:
    @pytest.mark.parametrize("cls", ["EffySensor", "EffyDerivedPowerSensor", "EffySmoothedSensor"])
    async def test_a_second_removal_hands_nothing_back_again(self, cls: str) -> None:
        coordinator = _CountingCoordinator()
        entity = _series(cls, coordinator=coordinator)
        await entity.async_added_to_hass()
        assert coordinator.registry

        await entity.async_will_remove_from_hass()
        handed_back = list(coordinator.unsubscribed)
        await entity.async_will_remove_from_hass()

        assert any(item.startswith("updates:") for item in handed_back)
        assert coordinator.unsubscribed == handed_back
        assert coordinator.registry == {}

    async def test_the_recalculated_from_sensor_unsubscribes_once(self) -> None:
        coordinator = _CountingCoordinator()
        entity = _recalculated(coordinator)
        await entity.async_added_to_hass()
        assert len(coordinator.closing) == 1

        await entity.async_will_remove_from_hass()
        await entity.async_will_remove_from_hass()

        assert coordinator.closing == []


class TestPvSumEntityId:
    def test_an_assigned_entity_id_does_not_replace_the_published_one(self) -> None:
        entity = _sensor.EffyPvSumSensor(object(), _Entry(), _Coordinator())
        published = entity.entity_id

        entity.entity_id = "sensor.something_else_2"  # Home Assistant assigns one: ignored

        assert entity.entity_id == published


class TestDeltaIntegralPlaceholders:
    def test_the_combined_sensors_have_no_source_name(self) -> None:
        assert _today().translation_placeholders == {}
        assert _total().translation_placeholders == {}

    def test_the_input_sensor_names_its_source(self) -> None:
        assert _input().translation_placeholders == {"source_name": "pv_south"}
