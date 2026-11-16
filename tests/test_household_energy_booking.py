"""
Booking the delta of the household consumption series into its energy sensors (TASK-0050, CAP-15,
ADR-029 decision 5, ADR-027 decisions 2 and 3).

Part one is the pure ``DeltaIntegrals`` (no Home Assistant, no fake): the *today* and *total*
integrals of a series take the delta of that series alone, and the PV integrals are untouched by
it and it by them. Part two runs the real ``history.py``: the delta of the consumption series of
a run is ``(new - stored) * 5/60`` Wh, as the PV delta of an input's effective series is.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_delta_integrals import (
    BERLIN,
    COMBINED_TODAY,
    COMBINED_TOTAL,
    MIDNIGHT,
    NOW,
    PV_A,
    TODAY_A,
    YESTERDAY,
    _di,
)
from tests.test_household_consumption import (
    MIXED,
    PV,
    SYS_IMPORT,
    _mixed_world,
    _options_for,
    _Rig,
)
from tests.test_history_recent_equivalence import _const, _history, _ts
from tests.test_household_consumption import CONSUMPTION as CONSUMPTION_ID

TODAY = _const.HOUSEHOLD_CONSUMPTION_TODAY_ENTITY_ID
TOTAL = _const.HOUSEHOLD_CONSUMPTION_TOTAL_ENTITY_ID
SERIES = CONSUMPTION_ID


def _integrals(*, base: bool = True, pv: bool = False) -> Any:
    """Live integrals: the two of the household consumption, optionally with a PV input."""
    integrals = _di.DeltaIntegrals()
    for entity_id, kind in ((TODAY, "today"), (TOTAL, "total")):
        integrals.restore(
            entity_id=entity_id,
            kind=kind,
            source=None,
            series=SERIES,
            unit="kWh",
            value=10.0 if base else None,
            last_reset=MIDNIGHT if (base and kind == "today") else None,
            now=NOW,
            tz=BERLIN,
        )
    if pv:
        integrals.restore(
            entity_id=TODAY_A,
            kind="today",
            source=PV_A,
            unit="Wh",
            value=500.0,
            last_reset=MIDNIGHT,
            now=NOW,
            tz=BERLIN,
        )
        for entity_id, kind in ((COMBINED_TODAY, "today"), (COMBINED_TOTAL, "total")):
            integrals.restore(
                entity_id=entity_id,
                kind=kind,
                source=None,
                unit="kWh",
                value=2.0,
                last_reset=MIDNIGHT if kind == "today" else None,
                now=NOW,
                tz=BERLIN,
            )
    return integrals


def _state(integrals: Any, entity_id: str) -> float | None:
    state: float | None = integrals.get(entity_id).state
    return state


class TestBookingASeriesDelta:
    def test_a_delta_in_wh_is_booked_into_today_and_total_in_kwh(self) -> None:
        integrals = _integrals()
        changed = integrals.book({SERIES: (250.0, "Wh")})
        assert {i.entity_id for i in changed} == {TODAY, TOTAL}
        assert _state(integrals, TODAY) == pytest.approx(10.25)
        assert _state(integrals, TOTAL) == pytest.approx(10.25)

    def test_a_delta_in_kwh_is_booked_as_it_is(self) -> None:
        integrals = _integrals()
        integrals.book({SERIES: (0.5, "kWh")})
        assert _state(integrals, TODAY) == pytest.approx(10.5)

    def test_a_correction_may_take_energy_away_today_may_go_below_zero(self) -> None:
        integrals = _integrals()
        integrals.book({SERIES: (-12000.0, "Wh")})  # a correction of -12 kWh
        assert _state(integrals, TODAY) == pytest.approx(-2.0)  # below 0, no clamp
        assert _state(integrals, TOTAL) == pytest.approx(-2.0)

    def test_a_delta_of_zero_changes_nothing_but_is_not_an_error(self) -> None:
        integrals = _integrals()
        integrals.book({SERIES: (0.0, "Wh")})
        assert (_state(integrals, TODAY), _state(integrals, TOTAL)) == (10.0, 10.0)

    def test_no_delta_books_nothing(self) -> None:
        integrals = _integrals()
        assert integrals.book({}) == [] and integrals.book(None) == []

    def test_an_unavailable_integral_books_nothing_the_other_still_does(self) -> None:
        integrals = _integrals()
        integrals.invalidate([TOTAL])
        changed = integrals.book({SERIES: (100.0, "Wh")})
        assert [i.entity_id for i in changed] == [TODAY]
        assert _state(integrals, TOTAL) is None
        assert _state(integrals, TODAY) == pytest.approx(10.1)

    def test_with_no_base_at_all_nothing_is_booked(self) -> None:
        integrals = _integrals(base=False)
        assert integrals.book({SERIES: (100.0, "Wh")}) == []
        assert _state(integrals, TODAY) is None and _state(integrals, TOTAL) is None

    def test_a_key_that_belongs_to_no_integral_books_nothing(self) -> None:
        integrals = _integrals()
        assert integrals.book({"sensor.nobody": (100.0, "Wh")}) == []
        assert (_state(integrals, TODAY), _state(integrals, TOTAL)) == (10.0, 10.0)


class TestTheTwoUsersDoNotMix:
    def test_a_series_delta_does_not_touch_the_pv_integrals(self) -> None:
        integrals = _integrals(pv=True)
        integrals.book({SERIES: (1000.0, "Wh")})
        assert _state(integrals, TODAY_A) == 500.0
        assert (_state(integrals, COMBINED_TODAY), _state(integrals, COMBINED_TOTAL)) == (2.0, 2.0)
        assert _state(integrals, TODAY) == pytest.approx(11.0)

    def test_a_pv_delta_does_not_touch_the_consumption_integrals(self) -> None:
        integrals = _integrals(pv=True)
        integrals.book({PV_A: (100.0, "Wh")})
        assert _state(integrals, TODAY_A) == pytest.approx(600.0)
        assert _state(integrals, COMBINED_TODAY) == pytest.approx(2.1)
        assert (_state(integrals, TODAY), _state(integrals, TOTAL)) == (10.0, 10.0)

    def test_one_run_books_both_as_its_own(self) -> None:
        integrals = _integrals(pv=True)
        changed = integrals.book({PV_A: (100.0, "Wh"), SERIES: (400.0, "Wh")})
        assert {i.entity_id for i in changed} == {
            TODAY_A,
            COMBINED_TODAY,
            COMBINED_TOTAL,
            TODAY,
            TOTAL,
        }
        assert _state(integrals, TODAY_A) == pytest.approx(600.0)
        assert _state(integrals, COMBINED_TODAY) == pytest.approx(2.1)  # the PV delta alone
        assert _state(integrals, TODAY) == pytest.approx(10.4)

    def test_a_series_key_equal_to_a_pv_source_is_the_pv_input_first(self) -> None:
        """Keys are separate by construction (a PV source is a source sensor, a series key an
        Effy statistic ID); should they ever coincide the PV input is booked, never both."""
        integrals = _integrals(pv=True)
        integrals.book({SERIES: (1000.0, "Wh")})
        integrals.restore(
            entity_id="sensor.effy_clash_delta_integral_today",
            kind="today",
            source=SERIES,
            unit="Wh",
            value=0.0,
            last_reset=MIDNIGHT,
            now=NOW,
            tz=BERLIN,
        )
        integrals.book({SERIES: (100.0, "Wh")})
        assert _state(integrals, "sensor.effy_clash_delta_integral_today") == pytest.approx(100.0)
        assert _state(integrals, TODAY) == pytest.approx(11.0)  # not booked a second time


class TestRestoreAndTheMidnight:
    def test_a_today_state_of_an_earlier_day_restarts_at_zero_the_total_keeps_its_state(
        self,
    ) -> None:
        integrals = _di.DeltaIntegrals()
        today = integrals.restore(
            entity_id=TODAY,
            kind="today",
            source=None,
            series=SERIES,
            unit="kWh",
            value=7.0,
            last_reset=YESTERDAY,
            now=NOW,
            tz=BERLIN,
        )
        total = integrals.restore(
            entity_id=TOTAL,
            kind="total",
            source=None,
            series=SERIES,
            unit="kWh",
            value=123.0,
            last_reset=None,
            now=NOW,
            tz=BERLIN,
        )
        assert (today.state, today.last_reset) == (0.0, MIDNIGHT)
        assert (total.state, total.last_reset) == (123.0, None)

    def test_nothing_restored_means_unavailable(self) -> None:
        integrals = _integrals(base=False)
        assert not integrals.get(TODAY).available and not integrals.get(TOTAL).available

    def test_at_midnight_today_goes_to_zero_and_the_total_does_not(self) -> None:
        integrals = _integrals()
        next_midnight = MIDNIGHT + timedelta(days=1)
        changed = integrals.reset_today(next_midnight)
        assert [i.entity_id for i in changed] == [TODAY]
        assert (integrals.get(TODAY).state, integrals.get(TODAY).last_reset) == (0.0, next_midnight)
        assert _state(integrals, TOTAL) == 10.0

    def test_an_unavailable_today_is_not_reset_into_availability(self) -> None:
        integrals = _integrals(base=False)
        assert integrals.reset_today(MIDNIGHT) == []
        assert _state(integrals, TODAY) is None

    def test_restoring_an_entity_again_replaces_it_and_never_books_twice(self) -> None:
        integrals = _integrals()
        integrals.restore(
            entity_id=TODAY,
            kind="today",
            source=None,
            series=SERIES,
            unit="kWh",
            value=1.0,
            last_reset=MIDNIGHT,
            now=NOW,
            tz=BERLIN,
        )
        changed = integrals.book({SERIES: (1000.0, "Wh")})
        assert [i.entity_id for i in changed].count(TODAY) == 1
        assert _state(integrals, TODAY) == pytest.approx(2.0)


class TestTheBaseAndTheSkipSet:
    def test_a_series_has_a_base_while_one_of_its_integrals_is_available(self) -> None:
        integrals = _integrals(base=False)
        assert not integrals.has_base(SERIES)
        integrals.hand_over({TOTAL: _Base(3.0, None)}, NOW, BERLIN)
        assert integrals.has_base(SERIES)

    def test_a_series_without_a_base_is_in_the_skip_set_one_with_a_base_is_not(self) -> None:
        integrals = _integrals(base=False)
        assert integrals.skip_sources([SERIES, "sensor.pv_a"]) == {SERIES, "sensor.pv_a"}
        integrals.hand_over({TODAY: _Base(1.0, MIDNIGHT), TOTAL: _Base(1.0, None)}, NOW, BERLIN)
        assert integrals.skip_sources([SERIES]) == frozenset()

    def test_a_series_that_is_not_registered_has_no_base(self) -> None:
        assert not _di.DeltaIntegrals().has_base(SERIES)

    def test_the_handed_over_base_becomes_the_state_exactly(self) -> None:
        integrals = _integrals(base=False)
        changed = integrals.hand_over(
            {TODAY: _Base(0.92, MIDNIGHT), TOTAL: _Base(0.98, None)},
            NOW,
            BERLIN,
            units={TODAY: "kWh", TOTAL: "kWh"},
        )
        assert {i.entity_id for i in changed} == {TODAY, TOTAL}
        assert (_state(integrals, TODAY), _state(integrals, TOTAL)) == (0.92, 0.98)
        assert integrals.get(TODAY).available

    def test_invalidating_both_makes_the_series_skip_until_a_new_base(self) -> None:
        """The derivation again (Q27 = A): unavailable meanwhile, nothing booked, then the base."""
        integrals = _integrals()
        integrals.invalidate([TODAY, TOTAL])
        assert integrals.skip_sources([SERIES]) == {SERIES}
        assert integrals.book({SERIES: (100.0, "Wh")}) == []
        integrals.hand_over({TODAY: _Base(5.0, MIDNIGHT), TOTAL: _Base(50.0, None)}, NOW, BERLIN)
        assert integrals.skip_sources([SERIES]) == frozenset()
        integrals.book({SERIES: (1000.0, "Wh")})
        assert (_state(integrals, TODAY), _state(integrals, TOTAL)) == (6.0, 51.0)

    def test_a_base_that_arrives_before_its_sensor_waits_for_it(self) -> None:
        integrals = _di.DeltaIntegrals()
        integrals.hand_over({TODAY: _Base(0.5, MIDNIGHT)}, NOW, BERLIN)
        restored = integrals.restore(
            entity_id=TODAY,
            kind="today",
            source=None,
            series=SERIES,
            unit="kWh",
            value=None,
            last_reset=None,
            now=NOW,
            tz=BERLIN,
        )
        assert restored.state == 0.5


class _Base:
    def __init__(self, state: float, last_reset: datetime | None) -> None:
        self.state = state
        self.last_reset = last_reset


# ---------------------------------------------------------------------------
# Part two: the delta of the consumption series in a run
# ---------------------------------------------------------------------------


def _written_wh(rig: _Rig) -> float:
    """The energy of every 5-minute row of the consumption series now stored, in Wh."""
    means: list[float] = [row["mean"] for row in rig.recorder.short(CONSUMPTION_ID).values()]
    return sum(means) * 5 / 60


class TestTheDeltaOfARun:
    async def test_the_first_run_books_the_energy_of_every_row_it_wrote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch, _mixed_world())
        deltas = (await rig.run())[4]
        delta, unit = deltas[CONSUMPTION_ID]
        assert unit == "Wh"
        assert delta == pytest.approx(_written_wh(rig))  # no stored row: the new value alone
        assert delta > 0.0

    async def test_a_second_run_with_nothing_changed_has_a_delta_of_exactly_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch, _mixed_world())
        await rig.run()
        deltas = (await rig.run())[4]
        assert deltas[CONSUMPTION_ID] == (0.0, "Wh")  # an unchanged row adds exactly 0

    async def test_a_corrected_slot_is_the_new_minus_the_stored_value_times_5_over_60(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _mixed_world()
        rig = _Rig(monkeypatch, world)
        await rig.run()
        slot = _ts(11, 30)
        before = rig.recorder.short(CONSUMPTION_ID)[slot]["mean"]
        # the system's grid import is 150 W higher in one slot: the household used 150 W less
        world.power_means[SYS_IMPORT] = {s: (250.0 if s == slot else 100.0) for s in _slots64()}
        deltas = (await rig.run())[4]
        after = rig.recorder.short(CONSUMPTION_ID)[slot]["mean"]
        assert after == pytest.approx(before - 150.0)
        delta, unit = deltas[CONSUMPTION_ID]
        assert unit == "Wh"
        assert delta == pytest.approx(-150.0 * 5 / 60)  # -12.5 Wh: a negative delta is kept

    async def test_the_delta_of_a_series_is_not_the_delta_of_the_pv_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _mixed_world()
        rig = _Rig(monkeypatch, world)
        await rig.run()
        world.power_means[SYS_IMPORT] = {
            s: 250.0 if s == _ts(11, 30) else 100.0 for s in _slots64()
        }
        deltas = (await rig.run())[4]
        assert deltas[PV][0] == 0.0  # the PV input's effective series did not change
        assert deltas[CONSUMPTION_ID][0] != 0.0

    async def test_a_series_in_the_skip_set_is_neither_computed_nor_booked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch, _mixed_world())
        result = await _history.async_recalculate_history(
            object(), MIXED, delta_skip=frozenset({CONSUMPTION_ID})
        )
        deltas = result[4]
        assert CONSUMPTION_ID not in deltas and PV in deltas  # only the consumption is skipped
        assert rig.recorder.short(CONSUMPTION_ID)  # its power rows are written all the same

    async def test_without_a_household_import_there_is_no_consumption_delta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch, _mixed_world())
        result = await _history.async_recalculate_history(object(), _options_for(eps=True))
        assert CONSUMPTION_ID not in result[4]
        assert not rig.recorder.short(CONSUMPTION_ID)

    async def test_the_pv_deltas_are_what_they_were_with_or_without_household_sensors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CAP-13 pin: the numbers of a PV entry do not depend on the household sensors."""
        first = _Rig(monkeypatch, _mixed_world())
        with_household = (await _history.async_recalculate_history(object(), MIXED))[4]
        stored_pv = sum(r["mean"] for r in first.recorder.short(f"sensor.effy_{PV[7:]}").values())
        second = _Rig(monkeypatch, _mixed_world())
        without = (await _history.async_recalculate_history(object(), _options_for(eps=True)))[4]
        assert second.recorder is not first.recorder
        assert with_household[PV] == without[PV]
        assert with_household[PV] == (pytest.approx(stored_pv * 5 / 60), "Wh")

    async def test_the_recent_recalculation_has_the_series_delta_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _mixed_world()
        rig = _Rig(monkeypatch, world)
        await rig.run()
        now = _ts(13, 5, 5)
        # the first recent run writes the slot completed since the full run: new, no stored
        # value, so its whole energy is the delta (one slot of 490 W)
        first = (await _history.async_recalculate_recent(object(), MIXED, now))[4]
        assert first[CONSUMPTION_ID] == (pytest.approx(490.0 * 5 / 60), "Wh")
        # a second one over the same slots finds everything as it wrote it
        unchanged = (await _history.async_recalculate_recent(object(), MIXED, now))[4]
        assert unchanged[CONSUMPTION_ID] == (0.0, "Wh")
        slot = _ts(12, 50)
        before = rig.recorder.short(CONSUMPTION_ID)[slot]["mean"]
        world.power_means[SYS_IMPORT] = {s: 250.0 if s == slot else 100.0 for s in _slots64()}
        corrected = (await _history.async_recalculate_recent(object(), MIXED, now))[4]
        assert rig.recorder.short(CONSUMPTION_ID)[slot]["mean"] == pytest.approx(before - 150.0)
        assert corrected[CONSUMPTION_ID][0] == pytest.approx(-150.0 * 5 / 60)
        assert corrected[CONSUMPTION_ID][1] == "Wh"

    async def test_a_run_that_writes_nothing_books_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch, _mixed_world())
        await rig.run()
        written, _earliest, _touched, _last, deltas = await rig.run()
        assert written == 0
        assert all(delta == 0.0 for delta, _unit in deltas.values())


def _slots64() -> list[datetime]:
    return [_ts(9, 0) + timedelta(minutes=5 * i) for i in range(64)]
