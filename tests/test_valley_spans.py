"""TASK-0024 — zero-mock tests of the valley and span machine (ADR-023 decision 6).

``calculation.py`` only (ADR-000 §6, no mocking): the valley zigzag on hand-made SoC
series (with the numbers worked out in the comments), the span accounting with the
middle-of-boundary-slot convention, every reason to drop a span, Open decision 1
(an estimate above 100 % is published as it is), the age of a slot, the batch feed,
the restore at every cut point of a series, and a simulator of a battery of known
efficiency (wobbles, a stack upgrade, missing statistics, counters that tick in 10 Wh,
a SoC that is a whole number of points) to see that the estimate finds it and that the
boundary convention has no bias.
"""

from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

_calc_path = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
)
_spec = importlib.util.spec_from_file_location("effy_calculation_valley_spans", _calc_path)
assert _spec is not None and _spec.loader is not None
_calculation: Any = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation_valley_spans"] = _calculation
_spec.loader.exec_module(_calculation)

BatSlot = _calculation.BatSlot
ValleyState = _calculation.ValleyState
valley_step = _calculation.valley_step
valley_feed = _calculation.valley_feed
valley_restore_data = _calculation.valley_restore_data
valley_state_from_restore = _calculation.valley_state_from_restore
DecayedRatio = _calculation.DecayedRatio
decay_add = _calculation.decay_add
VALLEY_BAND: float = _calculation.VALLEY_BAND
CAPACITY_TOLERANCE: float = _calculation.CAPACITY_TOLERANCE
EFF_VALIDATION_DELAY: timedelta = _calculation.EFF_VALIDATION_DELAY
DROP_MISSING_ENERGY: str = _calculation.SPAN_DROP_MISSING_ENERGY
DROP_CAPACITY_UNAVAILABLE: str = _calculation.SPAN_DROP_CAPACITY_UNAVAILABLE
DROP_CAPACITY_CHANGED: str = _calculation.SPAN_DROP_CAPACITY_CHANGED
DROP_NO_NET_INPUT: str = _calculation.SPAN_DROP_NO_NET_INPUT

UTC = timezone.utc
SLOT = timedelta(minutes=5)
T0 = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
LATE = T0 + timedelta(days=900)  # "now" far enough that every slot has settled
H = 30.0
CAP = 1.0  # kWh in the hand-made series: one point of SoC is 0.01 kWh, small against the energies


def when(i: int) -> datetime:
    return T0 + SLOT * i


def charge_of(i: int) -> float:
    return 1.0 + 0.01 * i


def discharge_of(i: int) -> float:
    return 0.5 + 0.02 * i


def bat(i: int, soc: float | None, **kw: Any) -> Any:
    """Slot ``i`` with unique energies (so that a test can tell which slots a sum took),
    unless the test sets them."""
    return BatSlot(
        when(i),
        soc,
        kw.pop("charge", charge_of(i)),
        kw.pop("discharge", discharge_of(i)),
        kw.pop("capacity", CAP),
    )


def series(socs: Sequence[float | None], start: int = 0, **kw: Any) -> list[Any]:
    return [bat(start + k, soc, **kw) for k, soc in enumerate(socs)]


def feed(slots: list[Any], state: Any = None, now: datetime = LATE, hysteresis: float = H) -> Any:
    return valley_feed(state if state is not None else ValleyState(), slots, now, hysteresis)


def step_by_step(slots: list[Any], hysteresis: float = H) -> Any:
    state = ValleyState()
    for slot in slots:
        state = valley_step(state, slot, hysteresis)
    return state


# The first worked series. L = 10 (index 2); the band is up to 20; the rise to 40 comes at
# index 7, and the last slot in the band before it is index 5 (18). The peak is 80 (index 9).
# After it the running minima are 70, 50, 30, 15, 10; the lowest point is L₂ = 10 and the first
# slot at or below L₂ + 10 = 20 after the peak is index 13 (15). The next valley is real at
# index 18 (45 >= 10 + 30). So: span from index 5 to index 13.
S1 = [20, 15, 10, 10, 12, 18, 25, 40, 60, 80, 70, 50, 30, 15, 10, 10, 14, 30, 45]
S1_BEGIN, S1_END = 5, 13

# S1 goes on: the second valley ends at index 16 (14, the last slot at or below 20 before the
# rise at index 18), the peak is 85, the third valley begins at index 23 (12) and is real at 27.
S4 = [*S1, 70, 85, 60, 30, 12, 10, 10, 20, 45]
S4_BEGIN2, S4_END2 = 16, 23


def span_sums(first: int, last: int, socs: Sequence[float], cap: float = CAP) -> tuple[float, ...]:
    """Σ charge, Σ discharge and Δstored of a span from slot ``first`` to slot ``last``:
    half of each boundary slot, all of those between (the convention of ADR-023)."""
    ch = 0.5 * charge_of(first) + 0.5 * charge_of(last)
    dis = 0.5 * discharge_of(first) + 0.5 * discharge_of(last)
    for i in range(first + 1, last):
        ch += charge_of(i)
        dis += discharge_of(i)
    delta = (socs[last] * cap - socs[first] * cap) / 100
    return ch, dis, delta


class TestThePureModule:
    def test_calculation_py_imports_no_home_assistant_module(self) -> None:
        source = _calc_path.read_text()
        assert "homeassistant" not in source


class TestTheValley:
    def test_the_first_span_runs_from_the_end_of_one_valley_to_the_beginning_of_the_next(
        self,
    ) -> None:
        state = feed(series(S1))
        assert state.estimate.counted_spans == 1
        assert state.estimate.last_span_begin == when(S1_BEGIN)
        assert state.estimate.last_span_end == when(S1_END)

    def test_the_valley_is_real_only_when_the_next_one_has_risen(self) -> None:
        # nothing counts at index 17 (30 < 10 + 30), the span is open from index 5
        state = feed(series(S1[:18]))
        assert state.estimate.counted_spans == 0
        assert state.span.start == when(S1_BEGIN)
        assert feed(series(S1)).estimate.counted_spans == 1

    def test_a_valley_is_real_when_the_soc_has_risen_exactly_by_the_hysteresis(self) -> None:
        assert feed(series([10, 40])).span.start == when(0)
        assert feed(series([10, 39])).span is None

    def test_a_valley_ends_at_the_last_slot_at_or_below_its_lowest_point_plus_10(self) -> None:
        assert feed(series([10, 20, 40])).span.start == when(1)  # 20 is still in the band
        assert feed(series([10, 21, 40])).span.start == when(0)  # 21 is not

    def test_the_second_valley_ends_at_the_last_slot_at_or_below_its_lowest_point_plus_10(
        self,
    ) -> None:
        # the second valley of S1 with its last slot at 20 (= 10 + 10, in the band) or at 21
        in_band = feed(series([*S1[:16], 20, 45]))
        out_of_band = feed(series([*S1[:16], 21, 45]))
        assert in_band.span.start == when(16)
        assert out_of_band.span.start == when(15)

    def test_the_band_belongs_to_the_final_lowest_point(self) -> None:
        # the band is L + 10 for the lowest point found by the rise: 8 + 10 = 18, so the
        # 25 does not count and the valley ends at the 8 (index 2) although 25 is below 10 + 30
        assert feed(series([18, 25, 8, 40])).span.start == when(2)

    def test_a_dip_smaller_than_the_hysteresis_widens_the_valley(self) -> None:
        # 10, a rise to 25 (15 < 30), a fall back: one valley that ends at index 4, not 1
        s2 = [10, 10, 25, 12, 10, 50, 80, 50, 12, 10, 50]
        state = feed(series(s2))
        assert state.estimate.counted_spans == 1
        assert state.estimate.last_span_begin == when(4)
        assert state.estimate.last_span_end == when(8)

    def test_the_peak_needs_a_fall_of_the_hysteresis_to_make_the_next_valley(self) -> None:
        # 80 down to 50 (30) and up to 80 again: a valley of exactly the hysteresis is real
        exact = feed(series([10, 10, 40, 80, 50, 80]))
        assert exact.estimate.counted_spans == 1
        assert exact.estimate.last_span_end == when(4)
        # 80 down to 51 (29) and up again: a wobble of the peak, no valley
        assert feed(series([10, 10, 40, 80, 51, 80])).estimate.counted_spans == 0

    def test_a_wobble_at_the_peak_moves_the_peak_not_the_boundaries(self) -> None:
        wobbly = feed(series([10, 10, 40, 80, 55, 75, 85, 12, 50]))
        plain = feed(series([10, 10, 40, 80, 85, 85, 85, 12, 50]))
        for state in (wobbly, plain):
            assert state.estimate.counted_spans == 1
            assert state.estimate.last_span_begin == when(1)
            assert state.estimate.last_span_end == when(7)

    def test_after_a_double_top_the_valley_begins_after_the_latest_of_them(self) -> None:
        # 80, a dip to 60 (20, a wobble), 80 again, then 50 (30 below the peak) and up to 80: the
        # 60 lies between the two tops, not after the peak, so the valley begins at the 50
        out = feed(series([10, 10, 40, 80, 60, 80, 50, 80]))
        assert out.estimate.counted_spans == 1
        assert out.estimate.last_span_end == when(6)

    def test_the_next_valley_begins_at_the_first_slot_in_the_band_of_its_lowest_point(
        self,
    ) -> None:
        # after the peak: 30, 19, 8, 8: the lowest point is 8, the band is up to 18, so the
        # 19 is not in it and the valley begins at the 8 (index 6)
        out = feed(series([5, 5, 40, 80, 30, 19, 8, 8, 40]))
        assert out.estimate.last_span_end == when(6)
        # with an 18 instead of the 19 the band includes it: the valley begins at index 5
        assert feed(series([5, 5, 40, 80, 30, 18, 8, 8, 40])).estimate.last_span_end == when(5)

    def test_a_fall_that_rises_again_before_the_lowest_point_is_found_is_one_valley(
        self,
    ) -> None:
        # 80, 30, 12, 25 (a rise of 13), 9, 40: the lowest point is 9 (band 19), so 12 is in the
        # band and is where the valley begins; the 25 in between widened it
        out = feed(series([5, 5, 40, 80, 30, 12, 25, 9, 40]))
        assert out.estimate.last_span_end == when(5)

    def test_three_valleys_make_two_spans_and_each_valley_ends_one_and_begins_the_next(
        self,
    ) -> None:
        state = feed(series(S4))
        assert state.estimate.counted_spans == 2
        assert state.estimate.last_span_begin == when(S4_BEGIN2)
        assert state.estimate.last_span_end == when(S4_END2)

    def test_one_valley_alone_makes_no_span(self) -> None:
        assert feed(series([10, 10, 50, 80, 50, 30])).estimate.counted_spans == 0
        assert feed(series([10, 10, 10, 10])).estimate.counted_spans == 0

    def test_a_slot_at_or_before_the_last_processed_one_is_ignored(self) -> None:
        state = feed(series(S1))
        assert valley_step(state, bat(S1_END, 99), H) is state
        assert valley_step(state, bat(0, 99), H) is state

    def test_the_hysteresis_must_exceed_the_band(self) -> None:
        # a hysteresis of 10 or less could not tell a real valley from its own band
        for bad in (10, 9.99, 0):
            with pytest.raises(ValueError):
                valley_step(ValleyState(), bat(0, 10), bad)
        valley_step(ValleyState(), bat(0, 10), 10.01)

    def test_the_band_is_ten_points(self) -> None:
        assert VALLEY_BAND == 10
        assert CAPACITY_TOLERANCE == 0.01


class TestTheMissingSoc:
    def test_a_missing_soc_slot_resets_the_detection(self) -> None:
        socs: list[float | None] = list(S1)
        socs[10] = None  # in the descent from the peak
        state = feed(series(socs))
        assert state.estimate.counted_spans == 0
        # what follows the missing slot is a new beginning: its first valley is found again (from
        # the 10 at index 14, ending at index 16) and a span is open from there, but the span
        # of the cycle that was cut is not measured
        assert state.span is not None and state.span.start == when(16)

    def test_a_missing_slot_is_a_gap_and_resets_the_detection_too(self) -> None:
        slots = series(S1)
        del slots[10]
        state = feed(slots)
        assert state.estimate.counted_spans == 0

    def test_a_span_never_contains_a_missing_slot_but_the_detection_starts_again(self) -> None:
        # the cycle with its last slot missing, and the same cycle again: only the second counts
        slots = series(S1[:-1]) + [bat(len(S1) - 1, None)] + series(S1, start=len(S1))
        state = feed(slots)
        assert state.estimate.counted_spans == 1
        assert state.estimate.last_span_begin == when(len(S1) + S1_BEGIN)

    def test_a_reset_keeps_what_was_measured(self) -> None:
        counted = feed(series(S1))
        after = feed([bat(len(S1), None)], counted)
        assert after.estimate == counted.estimate
        assert after.span is None and after.low is None and after.records == ()

    def test_the_slot_after_a_reset_starts_a_valley_of_its_own(self) -> None:
        after = feed([bat(0, 50), bat(1, None), bat(2, 12), bat(3, 41)])
        # 12 is the lowest after the reset (the 50 before it is forgotten); 41 is no rise of 30
        assert after.span is None and after.low == 12
        assert feed([bat(0, 50), bat(1, None), bat(2, 12), bat(3, 42)]).span is not None


class TestTheSpanAccounting:
    def test_the_sums_take_half_of_each_boundary_slot_and_all_those_between(self) -> None:
        state = feed(series(S1))
        ch, dis, delta = span_sums(S1_BEGIN, S1_END, S1)
        x = ch - delta
        assert state.estimate.counted_kwh == pytest.approx(x)
        assert state.estimate.last_value == pytest.approx(dis / x)
        assert state.estimate.value == pytest.approx(dis / x)
        assert state.estimate.ratio.num == pytest.approx(dis)
        assert state.estimate.ratio.den == pytest.approx(x)

    def test_delta_stored_is_the_soc_times_the_capacity_read_at_each_boundary(self) -> None:
        # 100.9 at the end of the span against 100 at its start: within 1 %
        state = feed(_with(S1, capacity=_caps(S1, {S1_END: 100.9})))
        ch, dis, _ = span_sums(S1_BEGIN, S1_END, S1)
        delta = (S1[S1_END] * 100.9 - S1[S1_BEGIN] * 100.0) / 100
        assert state.estimate.counted_kwh == pytest.approx(ch - delta)
        assert state.estimate.last_value == pytest.approx(dis / (ch - delta))

    def test_the_battery_efficiency_is_discharge_over_charge_less_the_change_in_stored(
        self,
    ) -> None:
        # 9 kWh in, 6 kWh out, the same SoC at both ends: nothing is stored and 6 / 9 comes
        # back (the boundary slots carry no energy, so only the ones between count)
        socs = [10, 10, 40, 40, 80, 40, 10, 10, 40]
        slots = [bat(i, s, charge=0.0, discharge=0.0) for i, s in enumerate(socs)]
        slots[3] = bat(3, 40, charge=7.0, discharge=0.0)
        slots[4] = bat(4, 80, charge=2.0, discharge=0.0)
        slots[5] = bat(5, 40, charge=0.0, discharge=6.0)
        state = feed(slots)
        # span: index 1 (10) to index 6 (10): charge 7 + 2 = 9, discharge 6, delta 0
        assert state.estimate.last_value == pytest.approx(6 / 9)

    def test_the_weight_of_a_span_is_its_charge_less_the_change_in_stored(self) -> None:
        state = feed(series(S1))
        ch, _, delta = span_sums(S1_BEGIN, S1_END, S1)
        assert state.estimate.counted_kwh == pytest.approx(ch - delta)

    def test_the_second_span_fades_the_first_by_its_weight(self) -> None:
        state = feed(series(S4))
        ch1, dis1, d1 = span_sums(S1_BEGIN, S1_END, S4)
        ch2, dis2, d2 = span_sums(S4_BEGIN2, S4_END2, S4)
        x1, x2 = ch1 - d1, ch2 - d2
        ratio = decay_add(decay_add(DecayedRatio(), x1, dis1, x1), x2, dis2, x2)
        assert state.estimate.ratio.num == pytest.approx(ratio.num)
        assert state.estimate.ratio.den == pytest.approx(ratio.den)
        assert state.estimate.counted_kwh == pytest.approx(x1 + x2)
        assert state.estimate.counted_spans == 2
        assert state.estimate.last_value == pytest.approx(dis2 / x2)

    def test_an_estimate_above_one_is_published_as_it_is(self) -> None:
        # ADR-023 Open decision 1, option A (the human, 2026-10-29): not capped, not discarded
        slots = [bat(i, s, discharge=discharge_of(i) * 10) for i, s in enumerate(S1)]
        state = feed(slots)
        ch, _, delta = span_sums(S1_BEGIN, S1_END, S1)
        dis = sum(
            (0.5 if i in (S1_BEGIN, S1_END) else 1.0) * discharge_of(i) * 10
            for i in range(S1_BEGIN, S1_END + 1)
        )
        assert dis / (ch - delta) > 1.2  # the case is a real one
        assert state.estimate.value == pytest.approx(dis / (ch - delta))
        assert state.estimate.value > 1.2
        assert state.estimate.counted_spans == 1
        assert state.dropped_spans == 0

    def test_nothing_is_measured_before_the_first_span(self) -> None:
        state = ValleyState()
        assert state.estimate.value is None
        assert state.estimate.counted_spans == 0
        assert state.estimate.counted_kwh == 0.0
        assert state.estimate.last_value is None
        assert state.estimate.last_span_begin is None and state.estimate.last_span_end is None
        assert state.dropped_spans == 0 and state.last_drop is None


def _with(socs: Sequence[float], **changes: dict[int, Any]) -> list[Any]:
    """S-series slots with some slots' fields replaced: ``capacity={9: 11.0}``."""
    slots = []
    for i, soc in enumerate(socs):
        slots.append(
            bat(i, soc, **{key: values[i] for key, values in changes.items() if i in values})
        )
    return slots


def _caps(socs: Sequence[float], changes: dict[int, Any], base: float = 100.0) -> dict[int, Any]:
    """A capacity for every slot (``base``, in which 1 % is exactly 1.0) with some changed."""
    return {**{i: base for i in range(len(socs))}, **changes}


class TestDroppedSpans:
    def _dropped(self, state: Any, reason: str) -> None:
        assert state.estimate.counted_spans == 0
        assert state.estimate.value is None
        assert state.dropped_spans == 1
        assert state.last_drop == reason

    def test_a_capacity_that_changes_by_more_than_one_percent_inside_the_span_is_dropped(
        self,
    ) -> None:
        # 101.01 against 100: more than 1 %, in a slot in the middle (the boundaries agree)
        self._dropped(feed(_with(S1, capacity=_caps(S1, {9: 101.01}))), DROP_CAPACITY_CHANGED)

    def test_a_capacity_that_changes_by_exactly_one_percent_does_not_drop_the_span(self) -> None:
        state = feed(_with(S1, capacity=_caps(S1, {9: 101.0, 8: 99.0})))
        assert state.estimate.counted_spans == 1 and state.dropped_spans == 0

    def test_a_capacity_change_at_a_boundary_slot_drops_the_span_too(self) -> None:
        self._dropped(feed(_with(S1, capacity=_caps(S1, {S1_END: 110.0}))), DROP_CAPACITY_CHANGED)
        self._dropped(
            feed(_with(S1, capacity=_caps(S1, {S1_BEGIN + 1: 90.0}))), DROP_CAPACITY_CHANGED
        )

    def test_an_unavailable_capacity_inside_the_span_is_dropped(self) -> None:
        for where in (S1_BEGIN, 8, S1_END):
            self._dropped(feed(_with(S1, capacity={where: None})), DROP_CAPACITY_UNAVAILABLE)

    def test_a_capacity_that_is_not_positive_is_unavailable(self) -> None:
        for bad in (0.0, -5.0):
            self._dropped(feed(_with(S1, capacity={8: bad})), DROP_CAPACITY_UNAVAILABLE)

    def test_a_missing_charge_or_discharge_statistic_inside_the_span_is_dropped(self) -> None:
        for where in (S1_BEGIN, 8, S1_END):
            self._dropped(feed(_with(S1, charge={where: None})), DROP_MISSING_ENERGY)
            self._dropped(feed(_with(S1, discharge={where: None})), DROP_MISSING_ENERGY)

    def test_a_problem_outside_the_span_does_not_drop_it(self) -> None:
        # before the end of the first valley and after the beginning of the second
        for where in (1, 3, S1_BEGIN - 1, S1_END + 1, 15):
            for field in ("charge", "discharge", "capacity"):
                state = feed(_with(S1, **{field: {where: None}}))
                assert state.estimate.counted_spans == 1, (field, where)
                assert state.dropped_spans == 0

    def test_a_span_whose_charge_less_the_change_in_stored_is_exactly_zero_is_dropped(
        self,
    ) -> None:
        # nothing charged and the same SoC at both ends: 0 is not above 0
        socs = [5, 5, 40, 80, 30, 5, 5, 40]
        slots = [bat(i, s, charge=0.0, discharge=0.1) for i, s in enumerate(socs)]
        self._dropped(feed(slots), DROP_NO_NET_INPUT)

    def test_a_span_whose_charge_less_the_change_in_stored_is_not_above_zero_is_dropped(
        self,
    ) -> None:
        # the battery ends 9 points (9 kWh) fuller than it began and nothing was charged
        socs = [5, 5, 40, 80, 30, 14, 4, 4, 40]
        slots = [bat(i, s, charge=0.0, discharge=0.1) for i, s in enumerate(socs)]
        state = feed(slots)
        self._dropped(state, DROP_NO_NET_INPUT)

    def test_a_dropped_span_resets_nothing_and_the_next_span_counts(self) -> None:
        # S4: the first span is fine, the second has a missing statistic inside it
        counted_first = feed(series(S4[: S1_END + 6]))  # up to the second valley's rise
        slots = _with(S4, charge={20: None})
        state = feed(slots)
        assert state.estimate.counted_spans == 1
        assert state.dropped_spans == 1
        assert state.last_drop == DROP_MISSING_ENERGY
        assert state.estimate.ratio == counted_first.estimate.ratio  # untouched
        # and a span that follows the dropped one counts on its own
        more = [*S4, 70, 90, 60, 30, 12, 10, 10, 20, 50]
        again = feed(_with(more, charge={20: None}))
        assert again.estimate.counted_spans == 2 and again.dropped_spans == 1

    def test_the_drops_are_counted(self) -> None:
        two = feed(_with(S4, charge={8: None, 20: None}))
        assert two.dropped_spans == 2 and two.estimate.counted_spans == 0


class TestAge:
    def test_a_slot_enters_only_when_it_is_125_minutes_old(self) -> None:
        slots = series(S1)
        rise = when(len(S1) - 1)
        assert EFF_VALIDATION_DELAY == timedelta(minutes=125)
        late = feed(slots, now=rise + timedelta(minutes=125))
        early = feed(slots, now=rise + timedelta(minutes=124))
        assert late.estimate.counted_spans == 1
        assert early.estimate.counted_spans == 0
        assert early.last_slot == when(len(S1) - 2)

    def test_the_span_closes_only_when_the_next_valley_is_real_and_its_beginning_is_old(
        self,
    ) -> None:
        # the rise comes after the beginning, so a slot that has entered brings an old
        # beginning with it: at no time is a span closed on a beginning younger than 125 min
        slots = series(S1)
        for minutes in range(0, 400, 5):
            now = when(0) + timedelta(minutes=minutes)
            state = feed(slots, now=now)
            if state.estimate.counted_spans:
                assert state.estimate.last_span_end <= now - EFF_VALIDATION_DELAY

    def test_a_too_young_slot_waits_and_enters_at_a_later_call(self) -> None:
        slots = series(S1)
        first = feed(slots, now=when(10) + timedelta(minutes=127))
        assert first.last_slot == when(10)
        assert feed(slots, first).estimate.counted_spans == 1


class TestTheFeed:
    def _world(self) -> list[Any]:
        socs: list[float | None] = [*S4, None, *S4, 50, 20, 10, 10, 45]
        slots = []
        for i, soc in enumerate(socs):
            slots.append(bat(i, soc))
        return slots

    def test_slot_by_slot_equals_one_batch(self) -> None:
        slots = self._world()
        assert feed(slots) == step_by_step(slots)

    def test_a_replay_counts_nothing_twice(self) -> None:
        slots = self._world()
        once = feed(slots)
        assert feed(slots, once) == once
        assert feed(slots[: len(slots) // 2], once) == once

    def test_the_input_order_does_not_matter(self) -> None:
        slots = self._world()
        shuffled = list(slots)
        random.Random(4).shuffle(shuffled)
        assert feed(shuffled) == feed(slots)

    def test_the_states_are_values_the_input_is_never_changed(self) -> None:
        slots = self._world()
        before = feed(slots[:20])
        snapshot = valley_restore_data(before, H)
        feed(slots, before)
        assert valley_restore_data(before, H) == snapshot


class TestRestore:
    def _world(self) -> list[Any]:
        # a counted span, a dropped span (a capacity out of range), a missing soc, a gap,
        # a missing statistic, then more cycles: every part of the state is in some cut
        socs: list[float | None] = [
            *S4,
            *[70, 90, 60, 30, 14, 10, 10, 18, 50, 85, 40, 10, 10, 40],
            *[80, 40, 10, 10, 45, 85, 30, 10, 10, 45, 85, 40, 10, 10, 45],
            *[80, 55, 75, 90, 60, 30, 12, 10, 45],  # a wobble at the peak, then a higher one
        ]
        slots = [bat(i, s) for i, s in enumerate(socs)]
        slots[8] = bat(8, socs[8], capacity=120.0)
        slots[40] = bat(40, None)
        del slots[50]
        return slots

    def test_the_state_survives_json(self) -> None:
        state = feed(self._world())
        data = json.loads(json.dumps(valley_restore_data(state, H)))
        assert valley_state_from_restore(data, H) == state

    def test_a_state_that_has_processed_nothing_has_nothing_to_continue_from(self) -> None:
        data = json.loads(json.dumps(valley_restore_data(ValleyState(), H)))
        assert valley_state_from_restore(data, H) is None  # starting from nothing is the same

    def test_a_restore_at_every_cut_continues_as_if_nothing_had_happened(self) -> None:
        slots = self._world()
        whole = feed(slots)
        assert whole.estimate.counted_spans >= 2 and whole.dropped_spans >= 1
        cuts_with_records = 0
        for cut in range(len(slots) + 1):
            first = feed(slots[:cut])
            cuts_with_records += bool(first.records)
            data = json.loads(json.dumps(valley_restore_data(first, H)))
            restored = valley_state_from_restore(data, H)
            if cut == 0:  # nothing processed, nothing to continue from: the same as a start
                assert restored is None and first == ValleyState()
                restored = ValleyState()
            assert restored == first, cut
            assert feed(slots[cut:], restored) == whole, cut
        assert cuts_with_records > 5  # the cuts include the middle of a descent

    def test_a_restart_with_an_older_dump_gives_the_same_result(self) -> None:
        slots = self._world()
        old = feed(slots[:25])
        restored = valley_state_from_restore(json.loads(json.dumps(valley_restore_data(old, H))), H)
        assert feed(slots, restored) == feed(slots)

    def test_a_dump_made_with_another_hysteresis_is_refused(self) -> None:
        data = valley_restore_data(feed(self._world()[:20]), H)
        assert valley_state_from_restore(data, 45) is None
        assert valley_state_from_restore(data, H) is not None

    def _mid_descent(self) -> dict[str, Any]:
        state = feed(series(S1[:16]))
        assert state.span is not None and state.records and state.band is not None
        data: dict[str, Any] = json.loads(json.dumps(valley_restore_data(state, H)))
        assert valley_state_from_restore(data, H) == state
        return data

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.clear(),
            lambda d: d.update(hysteresis="30"),
            lambda d: d.update(hysteresis=True),
            lambda d: d.update(estimate=None),
            lambda d: d["estimate"].update(num=float("nan")),
            lambda d: d["estimate"].update(den=-1.0),
            lambda d: d["estimate"].update(counted_kwh=True),
            lambda d: d["estimate"].update(counted_spans=-1),
            lambda d: d["estimate"].update(counted_spans=1.5),
            lambda d: d["estimate"].update(last_span_begin="yesterday"),
            lambda d: d["estimate"].update(last_span_end="2026-03-01T00:00:00"),
            lambda d: d.update(last_slot=None),
            lambda d: d.update(last_slot="nonsense"),
            lambda d: d.update(dropped_spans=-1),
            lambda d: d.update(dropped_spans=False),
            lambda d: d.update(last_drop="because"),
            lambda d: d.update(last_drop=3),
            lambda d: d.update(peak=None),  # a span without a peak
            lambda d: d.update(peak="high"),
            lambda d: d.update(span=None),  # a peak and records without a span
            lambda d: d.update(span=[1, 2]),
            lambda d: d["span"].update(soc="full"),
            lambda d: d["span"].update(capacity=-3.0),
            lambda d: d["span"].update(charge_kwh=float("inf")),
            lambda d: d["span"].update(problem="oops"),
            lambda d: d["span"].update(start="x"),
            lambda d: d.update(records=None),
            lambda d: d.update(records=[None]),
            lambda d: d["records"][0].update(soc=None),
            lambda d: d["records"][0].update(start=3),
            lambda d: d["records"][0].update(discharge_kwh="3"),
            lambda d: d.update(low=None),  # records without a lowest point
            lambda d: d.update(low=99.0),  # a lowest point that is not the last record's
            lambda d: d.update(band=None),  # records without a band
            lambda d: d.update(records=list(reversed(d["records"]))),  # not decreasing
            lambda d: d["records"][0].update(soc=10.0),  # as low as the last: not decreasing
            lambda d: d["records"][0].update(soc=5.0),  # lower than the last one
        ],
    )
    def test_unusable_data_is_refused(self, mutate: Callable[[dict[str, Any]], Any]) -> None:
        data = self._mid_descent()
        mutate(data)
        assert valley_state_from_restore(data, H) is None

    def test_a_valley_candidate_without_a_span_needs_its_lowest_point_and_its_band(self) -> None:
        state = feed(series([20, 15, 12, 14]))
        assert state.span is None and state.low == 12 and state.band is not None
        data = json.loads(json.dumps(valley_restore_data(state, H)))
        assert valley_state_from_restore(data, H) == state
        for key in ("low", "band"):
            broken = dict(data, **{key: None})
            assert valley_state_from_restore(broken, H) is None
        assert valley_state_from_restore(dict(data, peak=50.0), H) is None
        assert valley_state_from_restore(dict(data, records=[]), H) is not None

    def test_a_span_without_records_has_neither_a_lowest_point_nor_a_band(self) -> None:
        state = feed(series(S1[:9]))  # just risen, the peak is still rising
        assert state.span is not None and state.records == ()
        data = json.loads(json.dumps(valley_restore_data(state, H)))
        assert valley_state_from_restore(data, H) == state
        assert valley_state_from_restore(dict(data, low=10.0), H) is None
        assert valley_state_from_restore(dict(data, band=data["span"]), H) is None


# ---------------------------------------------------------------------------
# A battery of known efficiency
# ---------------------------------------------------------------------------


class _Battery:
    """A battery with a known efficiency η. Charging puts all of the counted energy in, and
    discharging takes ``d / η`` out of what is stored: so the energy the battery gave back
    over the energy it used up (charge less the change in stored) is η for any stretch of
    time. The SoC is the mean of the slot, rounded to a whole point, as a BMS reports it;
    the counters are cumulative and tick in 10 Wh, as real ones do. A day has a sun (some
    days dull), a load that peaks in the evening, and clouds that make the SoC dip."""

    FLOOR = 0.05

    def __init__(self, eta: float, capacity: float = 10.0, seed: int = 7) -> None:
        self.eta, self.capacity = eta, capacity
        self.rng = random.Random(seed)
        self.stored = 0.12 * capacity
        self.cum = {"ch": 0.0, "dis": 0.0}
        self.shown = dict(self.cum)
        self.index = 0

    def _tick(self, key: str, energy: float) -> float:
        self.cum[key] += energy
        now = round(self.cum[key] / 0.01) * 0.01
        step = now - self.shown[key]
        self.shown[key] = now
        return step

    def upgrade(self, factor: float) -> None:
        """A stack upgrade: the new modules come at the same state of charge."""
        self.capacity *= factor
        self.stored *= factor

    def day(self, missing: float = 0.0, upgrade_at: int | None = None) -> list[Any]:
        rng = self.rng
        sun = rng.choice([0.2, 0.45, 0.7, 1.0, 1.0, 1.0])
        clouds = [(rng.randrange(100, 190), rng.randint(4, 14)) for _ in range(rng.randint(1, 3))]
        slots = []
        for k in range(288):
            if k == upgrade_at:
                self.upgrade(1.5)  # a stack upgrade in the middle of the day: 10 kWh become 15
            hour = k / 12
            pv = sun * 3.0 * math.sin(math.pi * (hour - 7) / 10) if 7 < hour < 17 else 0.0
            load = 0.25 + (1.0 if 17 <= hour < 23 else 0.0) + (0.5 if 6.5 <= hour < 8 else 0.0)
            load += rng.uniform(-0.1, 0.1)
            if any(start <= k < start + length for start, length in clouds):
                pv, load = 0.0, load + 1.4  # a cloud and a kettle: the SoC dips
            net = pv - load
            before = self.stored
            charge = discharge = 0.0
            if net > 0:
                charge = min(net * 5 / 60, self.capacity - self.stored)
            else:
                available = max(0.0, (self.stored - self.FLOOR * self.capacity) * self.eta)
                discharge = min(-net * 5 / 60, available)
            self.stored += charge - discharge / self.eta
            soc = round((before + self.stored) / 2 / self.capacity * 100)
            ch, dis = self._tick("ch", charge), self._tick("dis", discharge)
            capacity: float | None = self.capacity * (1 + rng.uniform(-0.003, 0.003))
            soc_out: float | None = soc
            if rng.random() < missing:
                what = rng.choice(["soc", "ch", "dis", "cap"])
                if what == "soc":
                    soc_out = None
                elif what == "ch":
                    ch = None  # type: ignore[assignment]
                elif what == "dis":
                    dis = None  # type: ignore[assignment]
                else:
                    capacity = None
            slots.append(BatSlot(when(self.index), soc_out, ch, dis, capacity))
            self.index += 1
        return slots


def _run(
    battery: _Battery,
    days: int,
    state: Any = None,
    missing: float = 0.0,
    upgrade_at: dict[int, int] | None = None,
) -> tuple[Any, list[float]]:
    """Feed ``days`` days slot by slot; the estimate after every counted span.
    ``upgrade_at`` maps a day of this run to the slot of the day with a stack upgrade."""
    state = state if state is not None else ValleyState()
    values: list[float] = []
    for day in range(days):
        for slot in battery.day(missing, (upgrade_at or {}).get(day)):
            counted = state.estimate.counted_spans
            state = valley_step(state, slot, H)
            if state.estimate.counted_spans > counted and state.estimate.counted_kwh >= 40:
                values.append(state.estimate.value)
    return state, values


class TestTheSimulatedBattery:
    def test_the_estimate_finds_the_true_efficiency_and_stays(self) -> None:
        battery = _Battery(eta=0.92)
        state, values = _run(battery, days=120)
        assert state.estimate.counted_spans > 80
        assert state.dropped_spans == 0  # a clean world: every span counts
        assert len(values) > 60
        assert max(abs(v - 0.92) for v in values) < 0.03
        assert abs(sum(values) / len(values) - 0.92) < 0.005  # no bias above half a point

    def test_the_boundary_convention_has_no_bias_over_several_batteries(self) -> None:
        # half a point is the limit of ADR-023 Open decision 2; different seeds and efficiencies
        for eta, seed in [(0.88, 1), (0.95, 2), (0.97, 3), (0.90, 4)]:
            _, values = _run(_Battery(eta=eta, seed=seed), days=100)
            bias = sum(values) / len(values) - eta
            assert abs(bias) < 0.005, (eta, seed, bias)

    def test_the_wobbles_of_the_world_are_there(self) -> None:
        # the clouds make dips inside the days: the SoC falls by 8 or more points from its
        # high of the morning and rises again, mostly by less than the hysteresis (the odd
        # day by more: a real valley at noon), so the estimates above were made with wobbles
        # the detection had to fold into the peaks and the valleys
        battery = _Battery(eta=0.92, seed=5)
        depths = []
        for _ in range(40):
            socs = [slot.soc for slot in battery.day()]
            high, deepest = 0, 0
            for soc in socs[84:205]:  # 07:00 to 17:00
                high = max(high, soc)
                deepest = max(deepest, high - soc)
            depths.append(deepest)
        assert sum(8 <= depth < H for depth in depths) >= 12

    def test_a_stack_upgrade_drops_the_spans_it_is_in_and_nothing_else(self) -> None:
        battery = _Battery(eta=0.92, seed=9)
        state, before = _run(battery, days=60)
        assert state.dropped_spans == 0
        counted_before = state.estimate.counted_spans
        # 10 kWh become 15 kWh at the same state of charge, on the 10th day (a sunny one) at
        # 14:10 (slot 170), in the middle of the span the day makes: that span cannot be measured
        state, after = _run(battery, days=60, state=state, upgrade_at={9: 170})
        assert state.dropped_spans == 1
        assert state.last_drop == DROP_CAPACITY_CHANGED
        assert state.estimate.counted_spans > counted_before + 40
        # the estimate was not reset by it and it did not lose the true efficiency
        assert abs(sum(after) / len(after) - 0.92) < 0.005
        assert max(abs(v - 0.92) for v in after) < 0.03

    def test_the_estimate_follows_a_battery_that_ages(self) -> None:
        battery = _Battery(eta=0.95, seed=11)
        state, _ = _run(battery, days=50)
        assert state.estimate.value == pytest.approx(0.95, abs=0.01)
        battery.eta = 0.88
        state, _ = _run(battery, days=50, state=state)
        assert state.estimate.value == pytest.approx(0.88, abs=0.01)

    def test_missing_statistics_cost_spans_not_accuracy(self) -> None:
        # about one slot in 150 is missing something: a SoC (a reset), a charge or discharge
        # statistic or the capacity (a dropped span)
        battery = _Battery(eta=0.93, seed=13)
        state, values = _run(battery, days=200, missing=1 / 150)
        assert state.dropped_spans > 3
        assert state.estimate.counted_spans > 40
        assert abs(sum(values) / len(values) - 0.93) < 0.005

    def test_the_machine_gives_the_same_state_live_and_replayed(self) -> None:
        battery = _Battery(eta=0.92, seed=21)
        slots = [slot for _ in range(30) for slot in battery.day(missing=1 / 300)]
        live = step_by_step(slots)
        replay = feed(slots)
        assert live == replay
        half = feed(slots[: len(slots) // 2])
        restored = valley_state_from_restore(
            json.loads(json.dumps(valley_restore_data(half, H))), H
        )
        assert feed(slots[len(slots) // 2 :], restored) == live
