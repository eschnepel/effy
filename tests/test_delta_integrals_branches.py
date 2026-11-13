"""The registry listing of ``DeltaIntegrals`` (``all()``), on top of ``test_delta_integrals``."""

from __future__ import annotations

from tests.test_delta_integrals import (
    COMBINED_TODAY,
    COMBINED_TOTAL,
    TODAY_A,
    TODAY_B,
    _all,
    _di,
    _restore,
)


class TestAll:
    def test_an_empty_registry_lists_nothing(self) -> None:
        assert _di.DeltaIntegrals().all() == []

    def test_it_lists_every_registered_sensor(self) -> None:
        integrals = _all({TODAY_A: 1.0, COMBINED_TOTAL: 5.0})

        listed = integrals.all()

        assert {item.entity_id for item in listed} == {
            TODAY_A,
            TODAY_B,
            COMBINED_TODAY,
            COMBINED_TOTAL,
        }
        assert all(item is integrals.get(item.entity_id) for item in listed)

    def test_a_sensor_restored_again_is_listed_once_with_its_new_state(self) -> None:
        integrals = _all({TODAY_A: 1.0})

        _restore(integrals, TODAY_A, "today", "sensor.pv_a", 2.5, unit="Wh")

        listed = [item for item in integrals.all() if item.entity_id == TODAY_A]
        assert len(listed) == 1
        assert listed[0].state == 2.5
        assert len(integrals.all()) == 4

    def test_the_list_is_a_copy(self) -> None:
        integrals = _all()

        integrals.all().clear()

        assert len(integrals.all()) == 4
