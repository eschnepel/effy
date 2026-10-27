"""TASK-0019 — zero-mock tests of the charge/discharge efficiency machine (ADR-023).

``calculation.py`` only (ADR-000 §6, no mocking): the classification of a slot, the
decayed ratio, the run rules (first and last slot of a run do not count), the batch
feed, the restore round trip, and a simulator of a battery system with known
efficiencies and cumulative counters that tick in 10 Wh, to see that the estimate
finds them.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

_calc_path = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
)
_spec = importlib.util.spec_from_file_location("effy_calculation_efficiency_runs", _calc_path)
assert _spec is not None and _spec.loader is not None
_calculation: Any = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation_efficiency_runs"] = _calculation
_spec.loader.exec_module(_calculation)

EffRoles = _calculation.EffRoles
EffSlot = _calculation.EffSlot
RunState = _calculation.RunState
DecayedRatio = _calculation.DecayedRatio
decay_add = _calculation.decay_add
ratio_value = _calculation.ratio_value
classify_slot = _calculation.classify_slot
run_step = _calculation.run_step
run_feed = _calculation.run_feed
run_restore_data = _calculation.run_restore_data
run_state_from_restore = _calculation.run_state_from_restore
EFF_DECAY_PER_KWH: float = _calculation.EFF_DECAY_PER_KWH
EFF_VALIDATION_DELAY: timedelta = _calculation.EFF_VALIDATION_DELAY

UTC = timezone.utc
SLOT = timedelta(minutes=5)
T0 = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
LATE = T0 + timedelta(days=400)  # "now" far enough that every slot has settled

GI, DIS, CH, GE, PV = "sensor.gi", "sensor.dis", "sensor.ch", "sensor.ge", "sensor.pv"
ROLES = EffRoles(grid_import=GI, discharge=DIS, charge=CH, grid_export=GE)
STANDBY = 0.002  # kWh per slot (24 W)


def when(i: int) -> datetime:
    return T0 + SLOT * i


def charge_slot(i: int, grid_in: float = 0.30, out: float = 0.27, **kw: Any) -> Any:
    return EffSlot(
        when(i),
        kw.pop("active", frozenset({GI, CH})),
        grid_import_kwh=grid_in,
        charge_kwh=out,
        standby_kwh=kw.pop("standby", STANDBY),
    )


def discharge_slot(i: int, discharge: float = 0.30, out: float = 0.27, **kw: Any) -> Any:
    return EffSlot(
        when(i),
        kw.pop("active", frozenset({DIS, GE})),
        discharge_kwh=discharge,
        grid_export_kwh=out,
        standby_kwh=kw.pop("standby", STANDBY),
    )


def idle_slot(i: int) -> Any:
    return EffSlot(when(i), frozenset(), standby_kwh=STANDBY)


def feed(slots: list[Any], state: Any = None, now: datetime = LATE) -> Any:
    return run_feed(state or RunState(), slots, ROLES, now)


class TestThePureModule:
    def test_calculation_py_imports_no_home_assistant_module(self) -> None:
        source = _calc_path.read_text(encoding="utf-8")
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert imports and not any("homeassistant" in line for line in imports)


class TestClassification:
    def test_charge_is_exactly_grid_import_and_charge(self) -> None:
        assert classify_slot({GI, CH}, ROLES) == "charging"
        assert classify_slot([CH, GI], ROLES) == "charging"

    def test_discharge_is_exactly_discharge_and_grid_export(self) -> None:
        assert classify_slot({DIS, GE}, ROLES) == "discharging"

    @pytest.mark.parametrize(
        "active",
        [
            set(),
            {GI},
            {CH},
            {GI, CH, PV},  # PV flows too
            {GI, CH, GE},  # grid export flows too
            {GI, CH, DIS},
            {GI, GE},  # a mix of both processes
            {DIS, CH},
            {DIS},
            {PV},
            {GI, CH, GE, DIS},
        ],
    )
    def test_anything_else_is_not_pure(self, active: set[str]) -> None:
        assert classify_slot(active, ROLES) is None

    def test_a_role_that_is_not_set_makes_its_process_impossible(self) -> None:
        no_charge = EffRoles(grid_import=GI, discharge=DIS, charge=None, grid_export=GE)
        assert classify_slot({GI}, no_charge) is None
        assert classify_slot({DIS, GE}, no_charge) == "discharging"
        assert classify_slot({GI, CH}, EffRoles()) is None


class TestDecayedRatio:
    def test_nothing_measured_has_no_value(self) -> None:
        assert ratio_value(DecayedRatio()) is None

    def test_the_first_measurement_seeds_the_ratio_without_a_cap(self) -> None:
        first = decay_add(DecayedRatio(), 0.30, out=0.27, inp=0.28)
        assert (first.num, first.den) == (0.27, 0.28)
        assert ratio_value(first) == pytest.approx(0.27 / 0.28)
        # a large measurement is not capped: one of 40 kWh dominates what is there
        big = decay_add(first, 40.0, out=36.0, inp=39.9)
        f = EFF_DECAY_PER_KWH**40.0
        assert big.num == pytest.approx(f * 0.27 + 36.0)
        assert big.den == pytest.approx(f * 0.28 + 39.9)

    def test_the_decay_is_five_percent_per_kwh_of_gross_input(self) -> None:
        one = decay_add(DecayedRatio(1.0, 2.0), 1.0, out=0.0, inp=0.0)
        assert (one.num, one.den) == pytest.approx((0.95, 1.9))
        two = decay_add(DecayedRatio(1.0, 2.0), 2.0, out=0.0, inp=0.0)
        assert (two.num, two.den) == pytest.approx((0.9025, 1.805))
        assert EFF_DECAY_PER_KWH == 0.95

    def test_in_a_steady_state_one_kwh_weighs_five_percent(self) -> None:
        ratio = DecayedRatio()
        for _ in range(500):
            ratio = decay_add(ratio, 1.0, out=0.9, inp=1.0)
        assert ratio_value(ratio) == pytest.approx(0.9)
        assert 1.0 / ratio.den == pytest.approx(1 - 0.95)

    def test_in_a_steady_state_three_kwh_weigh_14_point_3_percent(self) -> None:
        ratio = DecayedRatio()
        for _ in range(500):
            ratio = decay_add(ratio, 3.0, out=2.7, inp=3.0)
        assert 3.0 / ratio.den == pytest.approx(1 - 0.95**3)
        assert 3.0 / ratio.den == pytest.approx(0.143, abs=0.0005)

    def test_the_new_measurement_pulls_the_estimate_by_its_weight(self) -> None:
        ratio = DecayedRatio()
        for _ in range(500):
            ratio = decay_add(ratio, 1.0, out=0.9, inp=1.0)
        after = decay_add(ratio, 1.0, out=0.5, inp=1.0)
        # 5 % weight: 0.95 * 0.9 + 0.05 * 0.5 = 0.88
        assert ratio_value(after) == pytest.approx(0.88)


class TestRuns:
    def test_only_the_slots_with_two_pure_neighbours_count(self) -> None:
        for length, counted in [(1, 0), (2, 0), (3, 1), (4, 2), (10, 8)]:
            slots = [charge_slot(i, grid_in=1.0 + 0.1 * i) for i in range(length)]
            state = feed([idle_slot(-1), *slots, idle_slot(length)])
            expected = sum(1.0 + 0.1 * i for i in range(1, length - 1))
            assert state.charge.counted_kwh == pytest.approx(expected), length
            assert (state.charge.counted_runs, state.discharge.counted_runs) == (
                1 if counted else 0,
                0,
            )

    def test_an_impure_neighbour_is_not_a_pure_neighbour(self) -> None:
        pv_slot = charge_slot(3, active=frozenset({GI, CH, PV}))
        slots = [charge_slot(i, grid_in=1.0 + i) for i in (0, 1, 2)] + [pv_slot]
        slots += [charge_slot(i, grid_in=1.0 + i) for i in (4, 5, 6)]
        state = feed(slots)
        assert state.charge.counted_kwh == pytest.approx((1.0 + 1) + (1.0 + 5))  # slots 1 and 5

    def test_the_other_kind_next_to_a_run_ends_it(self) -> None:
        slots = [charge_slot(0), charge_slot(1), discharge_slot(2), discharge_slot(3)]
        state = feed(slots)
        assert state.charge.counted_kwh == 0 and state.discharge.counted_kwh == 0

    def test_a_missing_slot_or_a_gap_ends_the_run(self) -> None:
        slots = [charge_slot(i, grid_in=1.0 + i) for i in (0, 1, 2, 4, 5, 6)]  # 3 is missing
        state = feed(slots)
        # slot 2 must not count with 4 as its neighbour: only 1 and 5 count
        assert state.charge.counted_kwh == pytest.approx((1.0 + 1) + (1.0 + 5))
        assert state.charge.counted_runs == 2

    def test_a_run_counts_once_however_many_slots_it_contributes(self) -> None:
        state = feed([charge_slot(i) for i in range(12)])
        assert state.charge.counted_runs == 1
        state = feed([idle_slot(12), *[charge_slot(i) for i in range(13, 20)]], state)
        assert state.charge.counted_runs == 2

    def test_charge_and_discharge_are_independent_estimates(self) -> None:
        slots = [charge_slot(i, 0.30, 0.27) for i in range(5)]
        slots += [idle_slot(5)]
        slots += [discharge_slot(i, 0.40, 0.34) for i in range(6, 11)]
        state = feed(slots)
        assert state.charge.value == pytest.approx(0.27 / (0.30 - STANDBY))
        assert state.discharge.value == pytest.approx(0.34 / (0.40 - STANDBY))
        assert state.charge.counted_runs == state.discharge.counted_runs == 1

    def test_a_slot_without_a_standby_statistic_is_not_counted_but_keeps_the_run(self) -> None:
        slots = [charge_slot(i, grid_in=1.0 + i) for i in range(5)]
        slots[2] = charge_slot(2, grid_in=3.0, standby=None)
        state = feed(slots)
        assert state.charge.counted_kwh == pytest.approx((1.0 + 1) + (1.0 + 3))  # 1 and 3, not 2

    def test_a_slot_whose_input_is_not_larger_than_its_standby_is_not_counted(self) -> None:
        slots = [charge_slot(i) for i in range(5)]
        slots[2] = charge_slot(2, grid_in=STANDBY, out=0.0)
        slots[3] = charge_slot(3, grid_in=STANDBY / 2, out=0.0)
        state = feed(slots)
        assert state.charge.counted_kwh == pytest.approx(0.30)  # slot 1 only

    def test_the_gross_input_is_the_weight_and_the_net_input_the_denominator(self) -> None:
        state = feed([charge_slot(0), charge_slot(1, 0.30, 0.27), charge_slot(2)])
        expected = decay_add(DecayedRatio(), 0.30, out=0.27, inp=0.30 - STANDBY)
        assert state.charge.ratio == expected

    def test_each_measurement_fades_what_was_there_by_its_gross_input(self) -> None:
        # a standby energy of 0.1 kWh in slots of 0.5 kWh makes gross (0.5) and net (0.4)
        # differ: the second counted slot fades the first by 0.95 ** 0.5, not 0.95 ** 0.4
        slots = [charge_slot(i, 0.5, 0.35 + 0.01 * i, standby=0.1) for i in range(4)]
        state = feed(slots)
        first = decay_add(DecayedRatio(), 0.5, out=0.36, inp=0.4)
        second = decay_add(first, 0.5, out=0.37, inp=0.4)
        assert state.charge.ratio == second
        assert (first.num * 0.95**0.5 + 0.37) == pytest.approx(second.num)

    def test_an_estimate_above_one_is_published_as_it_is(self) -> None:
        # no cap (ADR-023 decision 6): out is larger than the net input, e.g. the
        # standby power is overestimated; the value shows it instead of hiding it
        state = feed([charge_slot(i, 0.30, 0.31) for i in range(5)])
        assert state.charge.value == pytest.approx(0.31 / (0.30 - STANDBY))
        assert state.charge.value is not None and state.charge.value > 1.0
        assert state.charge.last_value is not None and state.charge.last_value > 1.0

    def test_the_attributes_describe_the_last_counted_slot(self) -> None:
        slots = [charge_slot(i, 0.30, 0.20 + 0.01 * i) for i in range(6)]
        state = feed(slots)
        assert state.charge.last_value == pytest.approx(0.24 / (0.30 - STANDBY))  # slot 4
        assert state.charge.last_end == when(4) + SLOT
        assert state.charge.counted_kwh == pytest.approx(4 * 0.30)  # slots 1..4
        assert state.discharge.last_value is None and state.discharge.last_end is None
        assert state.last_slot == when(5)


class TestAge:
    def test_a_slot_enters_only_when_it_is_125_minutes_old(self) -> None:
        assert EFF_VALIDATION_DELAY == timedelta(minutes=125)
        slots = [charge_slot(i) for i in range(5)]
        # slot 2 is counted when slot 3 enters: slot 3 must be 125 minutes old
        too_early = when(3) + EFF_VALIDATION_DELAY - timedelta(seconds=1)
        assert feed(slots, now=too_early).charge.counted_kwh == pytest.approx(0.30)  # slot 1 only
        enough = when(3) + EFF_VALIDATION_DELAY
        assert feed(slots, now=enough).charge.counted_kwh == pytest.approx(0.60)

    def test_a_too_young_slot_waits_and_enters_at_a_later_call(self) -> None:
        slots = [charge_slot(i) for i in range(8)]
        state = feed(slots, now=when(4) + EFF_VALIDATION_DELAY)
        assert state.last_slot == when(4)
        state = feed(slots, state, now=when(7) + EFF_VALIDATION_DELAY)
        assert state.last_slot == when(7)
        assert state == feed(slots)


class TestFeed:
    def _world(self) -> list[Any]:
        slots: list[Any] = []
        for i in range(40):
            if i % 13 < 6:
                slots.append(charge_slot(i, 0.30, 0.27))
            elif i % 13 < 10:
                slots.append(discharge_slot(i, 0.35, 0.31))
            elif i % 13 == 11:
                slots.append(charge_slot(i, 0.30, 0.27, active=frozenset({GI, CH, PV})))
            elif i % 13 != 10:
                slots.append(idle_slot(i))
        return slots

    def test_slot_by_slot_equals_one_batch(self) -> None:
        slots = self._world()
        batch = feed(slots)
        step = RunState()
        for slot in slots:
            step = run_step(step, slot, ROLES)
        assert step == batch
        chunks = RunState()
        for i in range(0, len(slots), 7):
            chunks = feed(slots[i : i + 7], chunks)
        assert chunks == batch

    def test_a_replay_counts_nothing_twice(self) -> None:
        slots = self._world()
        once = feed(slots)
        assert feed(slots, once) == once
        assert feed(slots[:20], once) == once

    def test_the_input_order_does_not_matter(self) -> None:
        slots = self._world()
        shuffled = list(slots)
        random.Random(3).shuffle(shuffled)
        assert feed(shuffled) == feed(slots)

    def test_the_states_are_values_the_input_is_never_changed(self) -> None:
        slots = self._world()
        before = list(slots)
        start = RunState()
        feed(slots, start)
        assert start == RunState() and slots == before


class TestRestore:
    def _state(self, count: int = 9) -> Any:
        slots = [charge_slot(i, 0.30, 0.27) for i in range(count)]
        slots += [
            idle_slot(count),
            *[discharge_slot(i, 0.4, 0.34) for i in range(count + 1, count + 6)],
        ]
        return feed(slots)

    def test_the_state_survives_json(self) -> None:
        state = self._state()
        data = json.loads(json.dumps(run_restore_data(state)))
        assert run_state_from_restore(data) == state

    def test_an_empty_state_round_trips(self) -> None:
        data = json.loads(json.dumps(run_restore_data(RunState())))
        # nothing was processed: there is no last slot, which restore does not accept
        assert run_state_from_restore(data) is None

    def test_a_restore_in_the_middle_of_a_run_continues_as_if_nothing_happened(self) -> None:
        slots = [charge_slot(i, 1.0 + 0.1 * i, 0.9) for i in range(30)]
        uninterrupted = feed(slots)
        for cut in (1, 2, 3, 11, 29):
            half = feed(slots[:cut])
            restored = run_state_from_restore(json.loads(json.dumps(run_restore_data(half))))
            assert restored == half
            assert feed(slots, restored) == uninterrupted, cut

    def test_a_restart_with_an_older_state_gives_the_same_result(self) -> None:
        slots = [charge_slot(i, 1.0 + 0.1 * i, 0.9) for i in range(30)]
        older = run_state_from_restore(run_restore_data(feed(slots[:10])))
        assert feed(slots, older) == feed(slots)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.pop("charging"),
            lambda d: d.pop("last_slot"),
            lambda d: d.update(last_slot="not a time"),
            lambda d: d["charging"].update(num=-1.0),
            lambda d: d["charging"].update(den="x"),
            lambda d: d["discharging"].update(counted_runs=-2),
            lambda d: d["discharging"].update(counted_runs=1.5),
            lambda d: d["charging"].update(counted_kwh=float("nan")),
            lambda d: d.update(run_counted="yes"),
            lambda d: d["prev1"].update(kind="sideways"),
            lambda d: d["prev1"].update(energy_in=None),
            lambda d: d["prev1"].update(standby_kwh="lots"),
            lambda d: d.update(prev1=None),  # prev2 without prev1
            lambda d: d.update(prev2=7),
        ],
    )
    def test_unusable_data_is_refused(self, mutate: Any) -> None:
        data = json.loads(json.dumps(run_restore_data(self._state())))
        mutate(data)
        assert run_state_from_restore(data) is None

    def test_a_state_with_no_records_yet_is_accepted(self) -> None:
        state = RunState(last_slot=when(0))
        assert run_state_from_restore(run_restore_data(state)) == state


class _System:
    """A battery system with known efficiencies. Charge: of the grid import, the BMS
    keeps its standby energy and the battery receives η_c of the rest. Discharge: the
    battery gives ``d`` (the standby energy is part of it) and η_d of the rest reaches
    the grid. The counters are cumulative and tick in 10 Wh, as real ones do."""

    def __init__(self, eta_c: float, eta_d: float, seed: int = 11) -> None:
        self.eta_c, self.eta_d = eta_c, eta_d
        self.rng = random.Random(seed)
        self.cum = {"gi": 0.0, "dis": 0.0, "ch": 0.0, "ge": 0.0}
        self.shown = dict(self.cum)

    def _tick(self, key: str, energy: float) -> float:
        self.cum[key] += energy
        now = round(self.cum[key] / 0.01) * 0.01
        step = now - self.shown[key]
        self.shown[key] = now
        return step

    def slot(self, i: int, kind: str | None) -> Any:
        if kind == "charge":
            g = self.rng.uniform(0.15, 0.40)
            gi = self._tick("gi", g)
            ch = self._tick("ch", (g - STANDBY) * self.eta_c)
            return EffSlot(
                when(i), frozenset({GI, CH}), grid_import_kwh=gi, charge_kwh=ch, standby_kwh=STANDBY
            )
        if kind == "discharge":
            d = self.rng.uniform(0.15, 0.40)
            dis = self._tick("dis", d)
            ge = self._tick("ge", (d - STANDBY) * self.eta_d)
            return EffSlot(
                when(i), frozenset({DIS, GE}), discharge_kwh=dis, grid_export_kwh=ge,
                standby_kwh=STANDBY,
            )  # fmt: skip
        if kind == "mixed":  # the PV also charges: not pure, whatever the counters show
            g = self.rng.uniform(0.05, 0.2)
            return EffSlot(
                when(i), frozenset({GI, CH, PV}), grid_import_kwh=self._tick("gi", g),
                charge_kwh=self._tick("ch", g * 3), standby_kwh=STANDBY,
            )  # fmt: skip
        return EffSlot(when(i), frozenset(), standby_kwh=STANDBY)

    def world(self, days: int, start: int = 0) -> list[Any]:
        """Runs of 1 to 30 slots of one kind between idle stretches, with a few mixed
        slots and a few missing slots, as a real battery behaves."""
        slots: list[Any] = []
        i = start
        end = start + days * 288
        while i < end:
            kind = self.rng.choice(["charge", "charge", "discharge", "discharge", "mixed", None])
            for _ in range(
                self.rng.randint(1, 30)
                if kind in ("charge", "discharge")
                else self.rng.randint(1, 6)
            ):
                if self.rng.random() < 0.01:
                    i += 1  # a missing slot: a gap
                    continue
                slots.append(self.slot(i, kind))
                i += 1
        return slots


class TestTheSimulatedBattery:
    def test_the_estimates_find_the_true_efficiencies_and_stay(self) -> None:
        system = _System(eta_c=0.95, eta_d=0.93)
        slots = system.world(days=90)
        state = RunState()
        seen_c: list[float] = []
        seen_d: list[float] = []
        for slot in slots:
            state = run_step(state, slot, ROLES)
            if state.charge.counted_kwh >= 60 and state.charge.value is not None:
                seen_c.append(state.charge.value)
            if state.discharge.counted_kwh >= 60 and state.discharge.value is not None:
                seen_d.append(state.discharge.value)
        assert len(seen_c) > 1000 and len(seen_d) > 1000  # plenty of data after 60 kWh each
        # within half a percentage point after 60 kWh, and it stays there
        assert max(abs(v - 0.95) for v in seen_c) < 0.005
        assert max(abs(v - 0.93) for v in seen_d) < 0.005
        assert state.charge.counted_runs > 50 and state.discharge.counted_runs > 50

    def test_the_estimate_follows_a_change_of_efficiency(self) -> None:
        system = _System(eta_c=0.95, eta_d=0.93, seed=5)
        first = system.world(days=40)
        system.eta_c, system.eta_d = 0.90, 0.88  # the battery ages
        second = system.world(days=40, start=(first[-1].start - T0) // SLOT + 2)
        state = feed(first)
        assert state.charge.value == pytest.approx(0.95, abs=0.005)
        before = state.charge.counted_kwh
        state = feed(second, state)
        assert state.charge.counted_kwh > before + 60
        assert state.charge.value == pytest.approx(0.90, abs=0.005)
        assert state.discharge.value == pytest.approx(0.88, abs=0.005)

    def test_the_mixed_slots_would_have_spoiled_an_estimate_that_took_them(self) -> None:
        system = _System(eta_c=0.95, eta_d=0.93, seed=2)
        slots = system.world(days=30)
        impure = [s for s in slots if PV in s.active]
        assert impure  # the world has them
        # the machine ignores them: its estimate is on the true value although the mixed
        # slots charge at three times the grid import (a ratio of 3)
        assert feed(slots).charge.value == pytest.approx(0.95, abs=0.005)
