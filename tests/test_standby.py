"""TASK-0005 Stage 2 — zero-mock tests of the standby power machine (ADR-018).

``calculation.py`` only (ADR-000 §6, no mocking): the adapters (readings → peaks),
the transition function (``drain_step``), the batch driver (``drain_feed``) and
the restore round trip. The scenarios are the ones the Stage 1 prototype was
checked with; here they run against the production code.

Simulation convention: constant powers, perfect conversion (η = 1); the quantity
under test is the *standby* power, the only loss in these worlds.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

_calc_path = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
)
_spec = importlib.util.spec_from_file_location("effy_calculation_standby_machine", _calc_path)
assert _spec is not None and _spec.loader is not None
_calculation: Any = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation_standby_machine"] = _calculation
_spec.loader.exec_module(_calculation)

DRAIN_LOOKBACK: timedelta = _calculation.DRAIN_LOOKBACK
DRAIN_VALIDATION_DELAY: timedelta = _calculation.DRAIN_VALIDATION_DELAY
DRAIN_ESTIMATE_WEIGHT: float = _calculation.DRAIN_ESTIMATE_WEIGHT
DrainBreak = _calculation.DrainBreak
DrainPeak = _calculation.DrainPeak
DrainSeen = _calculation.DrainSeen
DrainSlot = _calculation.DrainSlot
DrainState = _calculation.DrainState
drain_activity_start = _calculation.drain_activity_start
drain_feed = _calculation.drain_feed
drain_restore_data = _calculation.drain_restore_data
drain_state_from_restore = _calculation.drain_state_from_restore
drain_step = _calculation.drain_step
energy_drain_events = _calculation.energy_drain_events
power_drain_events = _calculation.power_drain_events
trapezoidal_slot_contributions = _calculation.trapezoidal_slot_contributions

UTC = timezone.utc
SLOT = timedelta(minutes=5)
T0 = datetime(2026, 1, 10, 22, 0, 0, tzinfo=UTC)

RawStates = list[tuple[datetime, str]]
Activity = dict[datetime, frozenset[str]]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _slot_of(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 5, second=0, microsecond=0)


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 1, 11, hour, minute, second, tzinfo=UTC)


def _simulate_counter(
    segments: list[tuple[datetime, datetime, int]],
    start: datetime,
    end: datetime,
    resolution_wh: int = 10,
    base_kwh: float = 12.30,
    jitter_s: int = 0,
) -> RawStates:
    """Raw state history of a kWh counter with ``resolution_wh`` resolution fed by
    piecewise-constant power ``segments`` (overlapping segments add up). Integer
    arithmetic only — jump times are exact."""
    total_s = int((end - start).total_seconds())
    watts = [0] * total_s
    for seg_start, seg_end, w in segments:
        for i in range(
            int((seg_start - start).total_seconds()), int((seg_end - start).total_seconds())
        ):
            if 0 <= i < total_s:
                watts[i] += w
    states: RawStates = [(start, f"{base_kwh:.2f}")]
    energy_ws = 0
    quantum_ws = resolution_wh * 3600
    jumps_seen = 0
    for i in range(total_s):
        energy_ws += watts[i]
        steps = energy_ws // quantum_ws
        if steps > jumps_seen:
            jumps_seen = steps
            delay = (len(states) * 7) % (jitter_s + 1) if jitter_s else 0
            value = base_kwh + steps * resolution_wh / 1000.0
            states.append((start + timedelta(seconds=i + 1 + delay), f"{value:.2f}"))
    return states


def _night(
    extra: list[tuple[datetime, datetime, int]] | None = None, standby_w: int = 12, hours: int = 8
) -> tuple[RawStates, datetime]:
    end = T0 + timedelta(hours=hours)
    return _simulate_counter([(T0, end, standby_w), *(extra or [])], T0, end), end


def _load_world(load_w: int, minutes: int) -> tuple[dict[str, RawStates], datetime]:
    """12 W standby plus ``load_w`` delivered for ``minutes`` from 03:00 (10 Wh in total, so
    the 10 Wh *output* counter jumps once, when the load ends)."""
    load = (T0 + timedelta(hours=5), T0 + timedelta(hours=5, minutes=minutes))
    discharge, end = _night([(*load, load_w)])
    return {
        "discharge": discharge,
        "grid_out": _simulate_counter([(*load, load_w)], T0, end, base_kwh=5.00),
    }, end


def _activity_at(
    raws: dict[str, RawStates], tau: datetime, start: datetime, unknown: frozenset[datetime]
) -> Activity:
    """Which sensors show flow in which slot as the statistics know it at ``tau``
    (a lagging counter has not redistributed yet). Every slot from ``start`` to
    ``tau`` has an entry (possibly empty) except those in ``unknown``."""
    active: dict[datetime, set[str]] = {}
    slot = _slot_of(start)
    while slot < tau:
        if slot not in unknown:
            active[slot] = set()
        slot += SLOT
    for source, raw in raws.items():
        truncated = [(t, v) for t, v in raw if t <= tau]
        if len(truncated) < 2:
            continue
        contributions = trapezoidal_slot_contributions(truncated, now=tau)
        for s, value in contributions.items():
            if value > 0.0 and s in active:
                active[s].add(source)
    return {s: frozenset(sources) for s, sources in active.items()}


class _Run:
    def __init__(self) -> None:
        self.state: Any = DrainState()
        self.samples: list[float] = []
        self.confirmed: list[Any] = []

    @property
    def estimate(self) -> float | None:
        return self.state.estimate  # type: ignore[no-any-return]


def _events(
    energy: dict[str, RawStates],
    bms: frozenset[str] | None,
    power: dict[str, RawStates] | None,
) -> list[Any]:
    events: list[Any] = []
    for source, raw in energy.items():
        if bms is None or source in bms:
            events.extend(energy_drain_events(source, raw, 1000.0))
    for source, raw in (power or {}).items():
        events.extend(power_drain_events(source, raw, 1.0))
    return events


def _run(
    energy: dict[str, RawStates],
    start: datetime,
    end: datetime,
    *,
    bms: frozenset[str] | None = None,
    power: dict[str, RawStates] | None = None,
    delay: timedelta = DRAIN_VALIDATION_DELAY,
    unknown: frozenset[datetime] = frozenset(),
) -> _Run:
    """Replay peaks and completed slots through ``drain_step`` in time order (a slot
    event sorts before a peak at the same instant), the activity as known at each
    slot's completion. Only BMS inputs are drain sources; every sensor in
    ``energy`` counts as activity."""
    timeline: list[tuple[datetime, int, Any]] = [(e.t, 1, e) for e in _events(energy, bms, power)]
    slot = _slot_of(start)
    while slot + SLOT <= end:
        timeline.append((slot + SLOT, 0, DrainSlot(slot)))
        slot += SLOT
    timeline.sort(key=lambda item: (item[0], item[1]))
    run = _Run()
    for when, _kind, event in timeline:
        before = run.state
        activity = (
            _activity_at(energy, when, start, unknown) if isinstance(event, DrainSlot) else {}
        )
        run.state = drain_step(run.state, event, activity, delay)
        if run.state.count > before.count:
            run.samples.append(run.state.last_value)
        if run.state.confirmed > before.confirmed:
            assert run.state.confirmed == before.confirmed + 1
            run.confirmed.append(run.state.last_confirmed)
    return run


# --------------------------------------------------------------------------
# adapters: readings → peaks
# --------------------------------------------------------------------------


class TestEnergyAdapter:
    def test_a_jump_is_a_peak_with_height_and_power_equivalent(self) -> None:
        raw: RawStates = [(_at(1, 0), "1.00"), (_at(1, 50), "1.01"), (_at(2, 40), "1.02")]
        events = energy_drain_events("discharge", raw, 1000.0)
        assert [type(e).__name__ for e in events] == ["DrainPeak", "DrainPeak"]
        first, second = events
        assert (first.t, first.height, first.t_prev, first.power_w) == (
            _at(1, 50),
            pytest.approx(10.0),
            None,
            None,
        )
        assert second.t_prev == _at(1, 50)
        assert second.power_w == pytest.approx(10.0 / (50.0 / 60.0))  # 12 W
        assert second.height == pytest.approx(10.0)
        assert second.is_energy and second.source == "discharge"

    def test_wh_counters_need_no_factor(self) -> None:
        raw: RawStates = [(_at(1, 0), "100"), (_at(1, 50), "110"), (_at(2, 40), "120")]
        events = energy_drain_events("discharge", raw, 1.0)
        assert events[1].power_w == pytest.approx(12.0)

    def test_offline_recovery_and_reset_are_breaks_not_peaks(self) -> None:
        raw: RawStates = [
            (_at(1, 0), "1.00"),
            (_at(1, 50), "1.01"),
            (_at(2, 0), "unavailable"),
            (_at(3, 0), "1.02"),  # recovery: a break, never a peak
            (_at(3, 50), "1.03"),  # peak with no predecessor
            (_at(4, 30), "0.00"),  # reset
        ]
        events = energy_drain_events("discharge", raw, 1000.0)
        assert [(type(e).__name__, e.t) for e in events] == [
            ("DrainPeak", _at(1, 50)),
            ("DrainBreak", _at(3, 0)),
            ("DrainPeak", _at(3, 50)),
            ("DrainBreak", _at(4, 30)),
        ]
        assert events[2].t_prev is None and events[2].power_w is None

    def test_a_standstill_between_jumps_breaks_the_predecessor_link(self) -> None:
        raw: RawStates = [
            (_at(1, 0), "1.00"),
            (_at(1, 50), "1.01"),
            (_at(2, 0), "1.01"),  # an unchanged reading: not a jump
            (_at(2, 40), "1.02"),
        ]
        events = energy_drain_events("discharge", raw, 1000.0)
        assert [e.t_prev for e in events] == [None, None]


class TestPowerAdapter:
    def test_isolated_readings_above_zero_are_peaks(self) -> None:
        raw: RawStates = [
            (_at(1, 0), "0"),
            (_at(1, 50), "12"),
            (_at(1, 51), "0"),
            (_at(2, 40), "12"),
            (_at(2, 41), "0"),
        ]
        events = [e for e in power_drain_events("p", raw, 1.0) if isinstance(e, DrainPeak)]
        assert [(e.t, e.power_w, e.height, e.is_energy) for e in events] == [
            (_at(1, 50), 12.0, 12.0, False),
            (_at(2, 40), 12.0, 12.0, False),
        ]
        assert events[1].t_prev == _at(1, 50)

    def test_two_consecutive_readings_above_zero_are_no_peak(self) -> None:
        raw: RawStates = [(_at(1, 0), "0"), (_at(1, 1), "12"), (_at(1, 2), "13"), (_at(1, 3), "0")]
        events = power_drain_events("p", raw, 1.0)
        assert not [e for e in events if isinstance(e, DrainPeak)]
        assert any(isinstance(e, DrainBreak) for e in events)

    def test_every_reading_above_zero_is_seen_for_the_resolution(self) -> None:
        raw: RawStates = [
            (_at(1, 0), "0"),
            (_at(1, 1), "12"),
            (_at(1, 2), "12.5"),
            (_at(1, 3), "0"),
        ]
        seen = [e for e in power_drain_events("p", raw, 1.0) if isinstance(e, DrainSeen)]
        assert [e.value for e in seen] == [12.0, 12.5]

    def test_offline_is_a_break_and_kilowatts_are_scaled(self) -> None:
        raw: RawStates = [
            (_at(1, 0), "0"),
            (_at(1, 1), "unavailable"),
            (_at(1, 2), "0.012"),
            (_at(1, 3), "0"),
        ]
        events = power_drain_events("p", raw, 1000.0)
        assert isinstance(events[0], DrainBreak)
        peak = [e for e in events if isinstance(e, DrainPeak)]
        assert peak and peak[0].power_w == pytest.approx(12.0)


# --------------------------------------------------------------------------
# the state machine (the Stage 1 scenarios, against production code)
# --------------------------------------------------------------------------


class TestEstimate:
    def test_standby_is_the_time_between_two_one_step_peaks(self) -> None:
        discharge, end = _night(hours=3)
        heights = [
            round((float(b[1]) - float(a[1])) * 1000.0) for a, b in zip(discharge, discharge[1:])
        ]
        assert heights == [10, 10, 10]
        run = _run({"discharge": discharge}, T0, end, delay=timedelta(0))
        assert run.samples == [pytest.approx(12.0), pytest.approx(12.0)]
        assert run.estimate == pytest.approx(12.0)

    def test_a_single_drain_interval_counts(self) -> None:
        discharge, end = _night(hours=2)
        run = _run({"discharge": discharge}, T0, end, delay=timedelta(0))
        assert run.samples == [pytest.approx(12.0)]

    def test_higher_standby_is_a_shorter_time_between_peaks(self) -> None:
        discharge, end = _night(standby_w=24, hours=3)
        run = _run({"discharge": discharge}, T0, end, delay=timedelta(0))
        assert run.estimate == pytest.approx(24.0)

    def test_the_validation_delay_holds_the_first_value_back(self) -> None:
        discharge, end = _night(hours=3)
        assert _run({"discharge": discharge}, T0, end).estimate is None
        assert _run(
            {"discharge": discharge}, T0, end, delay=timedelta(0)
        ).estimate == pytest.approx(12.0)

    @pytest.mark.parametrize("jitter_s", [30, 60])
    def test_polling_delay_needs_no_tolerance_because_heights_stay_exact(
        self, jitter_s: int
    ) -> None:
        end = T0 + timedelta(hours=8)
        discharge = _simulate_counter([(T0, end, 12)], T0, end, jitter_s=jitter_s)
        run = _run({"discharge": discharge}, T0, end)
        assert len(run.samples) >= 4
        assert run.estimate == pytest.approx(12.0, rel=0.01)

    def test_the_first_value_seeds_the_estimate_then_five_percent(self) -> None:
        end = T0 + timedelta(hours=45)
        change = T0 + timedelta(hours=15)
        discharge = _simulate_counter([(T0, change, 12), (change, end, 14)], T0, end)
        run = _run({"discharge": discharge}, T0, end)
        expected: float | None = None
        for sample in run.samples:
            expected = sample if expected is None else 0.95 * expected + 0.05 * sample
        assert run.estimate == pytest.approx(expected)
        assert run.samples[0] == pytest.approx(12.0)
        assert run.state.count == len(run.samples)
        assert run.estimate is not None and 13.5 < run.estimate < 14.0
        assert DRAIN_ESTIMATE_WEIGHT == 0.05

    def test_provenance_of_the_last_counted_value(self) -> None:
        discharge, end = _night(hours=4)
        run = _run({"discharge": discharge}, T0, end)
        assert run.state.last_source == "discharge"
        assert run.state.last_value == pytest.approx(12.0)
        assert run.state.last_end is not None and run.state.last_end <= end


class TestSameHeightRule:
    def test_ten_wh_then_twenty_wh_disqualifies_the_interval(self) -> None:
        discharge: RawStates = [(_at(1, 0), "1.00"), (_at(2, 0), "1.01"), (_at(3, 0), "1.03")]
        run = _run({"discharge": discharge}, _at(1, 0), _at(5, 0), delay=timedelta(0))
        assert len(run.confirmed) == 1
        assert run.samples == []

    def test_a_drain_peak_is_exactly_one_learned_step(self) -> None:
        discharge: RawStates = [
            (_at(0, 0), "1.00"),
            (_at(0, 30), "1.01"),  # 10 Wh: the smallest step seen, the resolution
            (_at(2, 0), "1.03"),  # 20 Wh
            (_at(3, 0), "1.05"),  # 20 Wh: same height, but two steps
        ]
        run = _run({"discharge": discharge}, _at(0, 0), _at(6, 0), delay=timedelta(0))
        assert run.samples == []

    def test_the_resolution_is_learned_from_the_smallest_peak_seen(self) -> None:
        discharge: RawStates = [
            (_at(0, 0), "1.00"),
            (_at(1, 0), "1.02"),  # 20 Wh (a reporting gap)
            (_at(2, 0), "1.03"),  # 10 Wh
            (_at(3, 0), "1.04"),  # 10 Wh, 10 Wh: counts
            (_at(4, 0), "1.05"),
        ]
        run = _run({"discharge": discharge}, _at(0, 0), _at(7, 0), delay=timedelta(0))
        assert [round(i.power_w) for i in run.confirmed] == [10, 10, 10]
        assert run.samples == [pytest.approx(10.0), pytest.approx(10.0)]
        assert run.state.resolution["discharge"] == pytest.approx(10.0)

    def test_a_lone_60_watt_peak_without_consumption_is_just_a_high_standby(self) -> None:
        burst = (T0 + timedelta(hours=5), T0 + timedelta(hours=5, minutes=10), 48)
        discharge, end = _night([burst])
        run = _run({"discharge": discharge}, T0, end)
        assert any(sample == pytest.approx(60.0, rel=0.01) for sample in run.samples)
        assert run.estimate is not None and 12.0 < run.estimate < 12.0 + 0.05 * 48.0

    def test_a_constant_60_watts_is_a_standby_of_60_watts(self) -> None:
        discharge, end = _night(standby_w=60)
        assert _run({"discharge": discharge}, T0, end).estimate == pytest.approx(60.0, rel=0.01)


class TestHiddenLoads:
    def test_a_fast_load_is_caught_because_validation_rereads_the_span(self) -> None:
        world, end = _load_world(60, 10)
        run = _run(world, T0, end, delay=timedelta(0))
        assert run.estimate == pytest.approx(12.0)
        assert max(run.samples) == pytest.approx(12.0)

    def test_a_slow_load_needs_the_validation_delay(self) -> None:
        world, end = _load_world(20, 30)
        bare = _run(world, T0, end, delay=timedelta(0))
        assert any(sample == pytest.approx(32.0) for sample in bare.samples)
        assert bare.estimate == pytest.approx(12.0 + 0.05 * 20.0 * 0.95**2)
        assert _run(world, T0, end).estimate == pytest.approx(12.0)

    def test_a_load_below_every_counter_resolution_leaks_a_bounded_sample(self) -> None:
        end = T0 + timedelta(hours=8)
        load = (T0 + timedelta(hours=5), T0 + timedelta(hours=6))
        discharge, _ = _night([(*load, 5)])
        world = {
            "discharge": discharge,
            "grid_out": _simulate_counter([(*load, 5)], T0, end, base_kwh=5.00),
        }
        assert len(world["grid_out"]) == 1
        run = _run(world, T0, end)
        assert any(sample == pytest.approx(17.0, rel=0.01) for sample in run.samples)
        assert run.estimate == pytest.approx(12.0 + 0.05 * 5.0, rel=0.01)


class TestDrainSources:
    def test_standby_fed_by_the_grid_import_needs_no_configuration(self) -> None:
        end = T0 + timedelta(hours=8)
        world = {
            "discharge": [(T0, "12.30")],
            "grid_in": _simulate_counter([(T0, end, 12)], T0, end, base_kwh=100.00),
        }
        run = _run(world, T0, end, bms=frozenset({"discharge", "grid_in"}))
        assert run.estimate == pytest.approx(12.0)
        assert run.state.last_source == "grid_in"

    def test_two_bms_inputs_sharing_the_standby_give_no_estimate(self) -> None:
        end = T0 + timedelta(hours=8)
        world = {
            "discharge": _simulate_counter([(T0, end, 6)], T0, end),
            "grid_in": _simulate_counter([(T0, end, 6)], T0, end, base_kwh=100.00),
        }
        run = _run(world, T0, end, bms=frozenset({"discharge", "grid_in"}))
        assert run.samples == [] and run.estimate is None

    def test_a_pv_input_is_never_a_drain_source(self) -> None:
        end = T0 + timedelta(hours=8)
        world = {
            "discharge": [(T0, "12.30")],
            "pv": _simulate_counter([(T0, end, 12)], T0, end, base_kwh=100.00),
        }
        run = _run(world, T0, end, bms=frozenset({"discharge"}))
        assert run.estimate is None


class TestMarginsAndBreaks:
    @pytest.mark.parametrize(("t1", "accepted"), [(_at(10, 10), True), (_at(10, 9), False)])
    def test_margin_before_the_first_peak(self, t1: datetime, accepted: bool) -> None:
        other: RawStates = [(_at(10, 0), "5.00"), (_at(10, 5), "5.01")]
        discharge: RawStates = [(_at(9, 0), "1.00"), (t1, "1.01"), (_at(11, 0), "1.02")]
        run = _run(
            {"discharge": discharge, "other": other},
            _at(9, 0),
            _at(12, 0),
            bms=frozenset({"discharge"}),
            delay=timedelta(0),
        )
        assert (len(run.confirmed) == 1) is accepted

    @pytest.mark.parametrize("other_slot", [_at(11, 0), _at(11, 5)])
    def test_margin_after_the_last_peak(self, other_slot: datetime) -> None:
        other: RawStates = [(other_slot, "5.00"), (other_slot + SLOT, "5.01")]
        discharge: RawStates = [(_at(9, 0), "1.00"), (_at(10, 10), "1.01"), (_at(11, 2), "1.02")]
        run = _run(
            {"discharge": discharge, "other": other},
            _at(9, 0),
            _at(12, 0),
            bms=frozenset({"discharge"}),
            delay=timedelta(0),
        )
        assert run.confirmed == []
        control = _run({"discharge": discharge}, _at(9, 0), _at(12, 0), delay=timedelta(0))
        assert [i.power_w for i in control.confirmed] == [pytest.approx(10.0 / (52.0 / 60.0))]

    def test_offline_gaps_and_counter_resets_break_the_chain(self) -> None:
        discharge: RawStates = [
            (_at(1, 0), "1.00"),
            (_at(1, 50), "1.01"),
            (_at(2, 0), "unavailable"),
            (_at(3, 0), "1.02"),
            (_at(3, 50), "1.03"),
            (_at(4, 30), "0.00"),
            (_at(5, 20), "0.01"),
            (_at(6, 10), "0.02"),
            (_at(7, 0), "0.03"),
        ]
        run = _run({"discharge": discharge}, _at(1, 0), _at(7, 30), delay=timedelta(0))
        assert run.samples == [pytest.approx(12.0), pytest.approx(12.0)]

    def test_a_slot_without_statistics_is_unknown_and_disqualifies(self) -> None:
        discharge, end = _night(hours=4)
        everything_known = _run({"discharge": discharge}, T0, end)
        assert everything_known.samples
        # the slot before the second peak's slot has no statistics at all
        peaks = [t for t, _v in discharge[1:]]
        hole = _slot_of(peaks[1]) - SLOT
        holed = _run({"discharge": discharge}, T0, end, unknown=frozenset({hole}))
        assert len(holed.samples) < len(everything_known.samples)

    def test_an_interval_older_than_the_lookback_is_dropped_at_the_next_slot(self) -> None:
        t1 = _at(0, 7)
        state = DrainState()
        state = drain_step(
            state, DrainSlot(_at(0, 0)), {_at(0, 0): frozenset()}, DRAIN_VALIDATION_DELAY
        )
        peak = DrainPeak(t1, "d", None, None, 10.0, True)
        state = drain_step(state, peak, {}, DRAIN_VALIDATION_DELAY)
        assert state.armed is not None
        late = t1 + DRAIN_LOOKBACK + SLOT * 2
        state = drain_step(state, DrainSlot(_slot_of(late)), {}, DRAIN_VALIDATION_DELAY)
        assert state.armed is None


class TestPowerFamily:
    @staticmethod
    def _power_raw(
        peak_w: float,
        readings_per_peak: int,
        second_peak_w: float | None = None,
        *,
        with_load_readings: bool = True,
    ) -> RawStates:
        raw: RawStates = [(T0, "0.0")]
        if with_load_readings:  # two distinct non-zero readings 0.1 W apart: the resolution
            raw += [(T0 + timedelta(minutes=5), "100.0"), (T0 + timedelta(minutes=6), "100.1")]
            raw.append((T0 + timedelta(minutes=7), "0.0"))
        for k in range(1, 15):
            at = T0 + timedelta(minutes=50 * k)
            watts = second_peak_w if (second_peak_w is not None and k % 2 == 0) else peak_w
            raw += [(at + timedelta(minutes=m), f"{watts:.2f}") for m in range(readings_per_peak)]
            raw.append((at + timedelta(minutes=readings_per_peak), "0.0"))
        return raw

    def test_isolated_power_peaks_behave_like_jumps(self) -> None:
        end = T0 + timedelta(hours=12)
        from_power = _run({}, T0, end, power={"bms_power": self._power_raw(12.0, 1)})
        discharge, _ = _night(hours=12)
        from_energy = _run({"discharge": discharge}, T0, end)
        assert from_power.estimate == pytest.approx(12.0)
        assert from_energy.estimate == pytest.approx(12.0)

    def test_power_peaks_must_agree_within_the_sensor_resolution(self) -> None:
        end = T0 + timedelta(hours=12)
        assert _run({}, T0, end, power={"bms_power": self._power_raw(12.0, 1)}).samples
        shifting = _run({}, T0, end, power={"bms_power": self._power_raw(12.0, 1, 12.5)})
        assert shifting.samples == []

    def test_power_peaks_with_no_learned_resolution_validate_nothing(self) -> None:
        end = T0 + timedelta(hours=12)
        raw = self._power_raw(12.0, 1, with_load_readings=False)
        run = _run({}, T0, end, power={"bms_power": raw})
        assert run.confirmed
        assert run.samples == [] and run.estimate is None

    def test_two_consecutive_readings_above_zero_are_no_peak(self) -> None:
        end = T0 + timedelta(hours=12)
        run = _run({}, T0, end, power={"bms_power": self._power_raw(12.0, 2)})
        assert run.samples == [] and run.estimate is None


# --------------------------------------------------------------------------
# batch driver, activity start, restore
# --------------------------------------------------------------------------


def _feed_all(energy: dict[str, RawStates], start: datetime, end: datetime) -> Any:
    """Final statistics for every slot, one ``drain_feed`` call."""
    activity = _activity_at(energy, end, start, frozenset())
    events = _events(energy, None, None)
    slots: list[datetime] = []
    slot = _slot_of(start)
    while slot + SLOT <= end:
        slots.append(slot)
        slot += SLOT
    return drain_feed(DrainState(), events, slots, activity)


class TestFeedAndRestore:
    def test_a_batch_with_final_statistics_equals_the_step_by_step_run(self) -> None:
        discharge, end = _night(hours=8)
        stepwise = _run({"discharge": discharge}, T0, end)
        batch = _feed_all({"discharge": discharge}, T0, end)
        assert batch.estimate == pytest.approx(stepwise.estimate)
        assert batch.count == stepwise.state.count
        assert batch.last_slot == stepwise.state.last_slot

    def test_the_batch_orders_a_slot_before_a_peak_at_the_same_instant(self) -> None:
        peak = DrainPeak(_at(1, 5), "d", None, None, 10.0, True)
        activity: Activity = {_at(1, 0): frozenset(), _at(1, 5): frozenset()}
        state = drain_feed(DrainState(), [peak], [_at(1, 0), _at(1, 5)], activity)
        assert state.armed is not None  # slot 01:00 was processed first: the peak could arm
        # a peak at the end of the last fed slot belongs to the open slot: left out
        late = DrainPeak(_at(1, 10), "d", None, None, 10.0, True)
        activity[_at(1, 10)] = frozenset()
        assert drain_feed(DrainState(), [late], [_at(1, 0), _at(1, 5)], activity).armed is None

    def test_activity_start_is_the_slot_before_the_oldest_armed_or_pending_peak(self) -> None:
        assert drain_activity_start(DrainState()) is None
        discharge, end = _night(hours=2)
        state = _feed_all({"discharge": discharge}, T0, end)
        assert state.armed is not None and state.pending
        oldest = min([p.t1 for p in state.pending] + [state.armed.t])
        assert drain_activity_start(state) == _slot_of(oldest) - SLOT

    def test_restore_data_round_trips(self) -> None:
        discharge, end = _night(hours=8)
        state = _feed_all({"discharge": discharge}, T0, end)
        data = drain_restore_data(state)
        restored = drain_state_from_restore(data)
        assert restored is not None
        assert restored.estimate == state.estimate
        assert restored.count == state.count
        assert restored.last_value == state.last_value
        assert restored.last_end == state.last_end
        assert restored.last_source == state.last_source
        assert dict(restored.resolution) == dict(state.resolution)
        assert restored.suppress_until == state.last_slot
        assert restored.last_slot is None and restored.armed is None and restored.pending == ()

    def test_a_restored_state_that_was_not_fed_yet_survives_a_second_restart(self) -> None:
        discharge, end = _night(hours=8)
        first = drain_state_from_restore(
            drain_restore_data(_feed_all({"discharge": discharge}, T0, end))
        )
        assert first is not None and first.last_slot is None
        again = drain_state_from_restore(drain_restore_data(first))
        assert again is not None
        assert again.suppress_until == first.suppress_until
        assert again.estimate == first.estimate and again.count == first.count

    @pytest.mark.parametrize(
        "data",
        [{}, {"estimate": "x"}, {"estimate": 12.0, "count": 1}, {"last_slot": "not a date"}],
    )
    def test_malformed_restore_data_gives_no_state(self, data: dict[str, Any]) -> None:
        assert drain_state_from_restore(data) is None

    def test_a_state_without_an_estimate_restores_to_nothing(self) -> None:
        assert drain_state_from_restore(drain_restore_data(DrainState())) is None

    @pytest.mark.parametrize("cut_hours", [3, 5, 6, 7])
    def test_replay_after_a_restart_equals_uninterrupted_operation(self, cut_hours: int) -> None:
        discharge, end = _night(hours=10)
        raws = {"discharge": discharge}
        uninterrupted = _feed_all(raws, T0, end)

        cut = T0 + timedelta(hours=cut_hours)
        at_cut = _feed_all({"discharge": [(t, v) for t, v in discharge if t <= cut]}, T0, cut)
        restored = drain_state_from_restore(drain_restore_data(at_cut))
        assert restored is not None or at_cut.estimate is None
        base = restored if restored is not None else DrainState()
        # the replay re-reads the peaks and slots from the start of its window
        activity = _activity_at(raws, end, T0, frozenset())
        slots = [T0 + SLOT * k for k in range(int((end - T0) / SLOT))]
        replayed = drain_feed(base, _events(raws, None, None), slots, activity)

        assert replayed.estimate == pytest.approx(uninterrupted.estimate)
        assert replayed.count == uninterrupted.count
        assert replayed.last_value == pytest.approx(uninterrupted.last_value)
        assert replayed.last_end == uninterrupted.last_end
        assert replayed.armed == uninterrupted.armed
        assert replayed.pending == uninterrupted.pending

    def test_replay_without_a_restored_state_counts_every_interval(self) -> None:
        discharge, end = _night(hours=10)
        replayed = _feed_all({"discharge": discharge}, T0, end)
        assert replayed.suppress_until is None and replayed.count >= 5
