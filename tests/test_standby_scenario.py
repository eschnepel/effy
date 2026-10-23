"""TASK-0005 Stage 1 — zero-mock scenarios for the standby-power investigation.

Two purposes, both pure (``calculation.py`` only, ADR-000 §6, no mocking):

1. ``TestStandbyOnlySlotToday`` — characterises how the *existing* loss model
   treats the battery system's standby power (Findings Q2). These tests pin
   today's behaviour; they do not change it. If ADR-001/005 ever change, they
   are expected to be revisited.
2. ``TestDrainMachinePrototype`` — a throwaway prototype of the state machine
   in the Draft ADR-018 (peaks and their heights, quiet test, same-height rule,
   validation delay, 5 % weight), to show it is feasible with Effy's inputs and outputs and to
   size what each protection buys. ``_DrainMachine``, the event adapters
   and the replay driver are NOT production code; Stage 2 re-implements them in
   ``calculation.py`` once a human has confirmed ADR-018 (they are deliberately
   defined here, not imported).

Simulation convention: constant powers, perfect conversion (η = 1) for the
consumption part so the arithmetic stays checkable by hand; the quantity under
test is the *standby* power, which is the only loss in these worlds.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    # Static-only import (ADR-000 §6) — runtime loads the module by file path.
    from effy.calculation import SensorReading as SensorReading

_calc_path = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
)
_spec = importlib.util.spec_from_file_location("effy_calculation_standby", _calc_path)
assert _spec is not None and _spec.loader is not None
_calculation = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation_standby"] = _calculation
_spec.loader.exec_module(_calculation)

if not TYPE_CHECKING:
    SensorReading = _calculation.SensorReading
distribute_loss = _calculation.distribute_loss
trapezoidal_slot_contributions = _calculation.trapezoidal_slot_contributions
_find_transitions = _calculation._find_transitions

UTC = timezone.utc
SLOT = timedelta(minutes=5)
T0 = datetime(2026, 1, 10, 22, 0, 0, tzinfo=UTC)

RawStates = list[tuple[datetime, str]]
Interval = tuple[datetime, datetime, float]  # (t1, t2, energy_wh)


# --------------------------------------------------------------------------
# helpers shared by both classes
# --------------------------------------------------------------------------


def _kw_reading(eid: str, wh_in_slot: float) -> SensorReading:
    """What history._readings_for_slot hands to distribute_loss for a kWh
    sensor (ADR-008): Wh → kWh, × 12 (5-min slot) → W-equivalent, unit kW."""
    return SensorReading(entity_id=eid, raw_value=wh_in_slot / 1000.0 * 12.0, original_unit="kW")


def _w_reading(eid: str, watts: float) -> SensorReading:
    return SensorReading(entity_id=eid, raw_value=watts, original_unit="W")


def _simulate_counter(
    segments: list[tuple[datetime, datetime, int]],
    start: datetime,
    end: datetime,
    resolution_wh: int = 10,
    base_kwh: float = 12.30,
    jitter_s: int = 0,
) -> RawStates:
    """Raw state history of a kWh counter with ``resolution_wh`` resolution fed
    by piecewise-constant power ``segments`` (start, end, watts; overlapping
    segments add up). A state is recorded the second the quantised value
    changes (+ ``k * 7 % (jitter_s + 1)`` seconds of polling delay, if asked).
    Integer arithmetic only — jump times are exact."""
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


# --------------------------------------------------------------------------
# 1. How today's model treats standby (Findings Q2)
# --------------------------------------------------------------------------


class TestStandbyOnlySlotToday:
    """A slot with standby draw but no other flow: 12 W × 5 min = 1 Wh leaves the
    battery (assumption A1: the discharge counter includes standby energy)."""

    def test_standby_only_slot_is_all_loss_and_effective_is_zero(self) -> None:
        inputs = [
            _kw_reading("bms_discharge", 1.0),
            _kw_reading("bms_grid_in", 0.0),
            _w_reading("pv_south", 0.0),
        ]
        outputs = [_kw_reading("bms_bat_out", 0.0), _kw_reading("bms_grid_out", 0.0)]
        dist = distribute_loss(inputs, outputs)

        # total_loss_w attribute: the whole standby draw
        assert dist.total_loss_w == pytest.approx(12.0)
        # effy_bms_discharge (effective): nothing left
        assert dist.effective_values_w["bms_discharge"] == pytest.approx(0.0)
        # effy_bms_discharge_power (pre-waterfall derived power) is the raw 12 W
        assert inputs[0].raw_value * 1000.0 == pytest.approx(12.0)
        # conservation holds trivially: Σ effective = Σ outputs = 0
        assert sum(dist.effective_values_w.values()) == pytest.approx(0.0)

    def test_standby_is_shared_with_pv_when_pv_is_active(self) -> None:
        # 100 W PV + 12 W standby from the battery, 100 W delivered → loss 12 W,
        # split equally (ADR-001): the standby cannot be attributed to the battery.
        inputs = [_w_reading("pv_south", 100.0), _w_reading("bms_discharge", 12.0)]
        outputs = [_w_reading("bms_grid_out", 100.0)]
        dist = distribute_loss(inputs, outputs)

        assert dist.total_loss_w == pytest.approx(12.0)
        assert dist.shares["pv_south"] == pytest.approx(6.0)
        assert dist.shares["bms_discharge"] == pytest.approx(6.0)
        assert sum(dist.effective_values_w.values()) == pytest.approx(100.0)

    def test_standby_and_conversion_loss_are_indistinguishable_per_slot(self) -> None:
        # Pure standby (12 W, nothing delivered) vs. 100 W delivered from a
        # 112 W discharge (≈ 10.7 % conversion loss): same total_loss_w.
        standby_only = distribute_loss([_w_reading("d", 12.0)], [_w_reading("o", 0.0)])
        conversion = distribute_loss([_w_reading("d", 112.0)], [_w_reading("o", 100.0)])
        assert standby_only.total_loss_w == pytest.approx(conversion.total_loss_w)

    def test_quiet_night_standby_energy_is_fully_inside_the_loss(self) -> None:
        """End to end through the real slot pipeline: 12 W standby, 10 Wh counter
        resolution (a jump every 50 min). Energy that left the battery is
        carried slot by slot into ``total_loss`` — none of it reaches the
        effective discharge series."""
        end = T0 + timedelta(hours=6)
        raw = _simulate_counter([(T0, end, 12)], T0, end)
        last_jump = raw[-1][0]
        contributions: dict[datetime, float] = trapezoidal_slot_contributions(raw, now=end)
        # kWh → Wh
        slot_wh = {slot: kwh * 1000.0 for slot, kwh in contributions.items()}

        total_loss_wh = 0.0
        total_effective_wh = 0.0
        for slot, wh in slot_wh.items():
            if slot + SLOT > last_jump:  # the open tail after the last jump carries 0 Wh
                continue
            dist = distribute_loss([_kw_reading("d", wh)], [_kw_reading("o", 0.0)])
            total_loss_wh += dist.total_loss_w / 12.0
            total_effective_wh += dist.effective_values_w["d"] / 12.0

        discharged_wh = (float(raw[-1][1]) - float(raw[0][1])) * 1000.0
        assert total_effective_wh == pytest.approx(0.0, abs=1e-6)
        # the slot containing last_jump is partially skipped above; everything
        # before it is accounted for within one slot's energy (1 Wh)
        assert discharged_wh - total_loss_wh <= 1.0 + 1e-6
        assert total_loss_wh > 0.0


# --------------------------------------------------------------------------
# 2. Prototype of the drain state machine (NOT production code)
# --------------------------------------------------------------------------

Activity = dict[datetime, frozenset[str]]  # slot start -> sensors with flow in it
EMA_WEIGHT = 0.05
SETTLE = timedelta(minutes=125)  # redistribution cap + 5 min margin


@dataclass(frozen=True)
class _Peak:
    """A peak of a BMS input. ``height`` is its first derivation: for an energy-
    family sensor the energy of the jump (one resolution step at standby, a
    multiple at load), for a power-family sensor the reading. ``power_w`` is its
    power equivalent: the height divided by the time since the previous jump, or
    the reading itself. ``t_prev`` is the time of the previous peak."""

    t: datetime
    source: str
    t_prev: datetime | None
    power_w: float | None
    height: float
    is_energy: bool


@dataclass(frozen=True)
class _Break:
    """Offline, counter reset, or sustained power: nothing spans across it."""

    t: datetime
    source: str


@dataclass(frozen=True)
class _Seen:
    """A non-zero reading of a power-family sensor, seen only to learn its
    resolution (the peaks alone may never show a step)."""

    t: datetime
    source: str
    value: float


@dataclass
class _Interval:
    source: str
    t1: datetime
    t2: datetime
    power_w: float
    start_height: float
    end_height: float
    is_energy: bool


def _slot_of(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 5, second=0, microsecond=0)


class _DrainMachine:
    """Throwaway prototype of the ADR-018 state machine. Events arrive in time
    order; a slot event for slot k arrives when k has completed."""

    def __init__(self, settle: timedelta, single_step: bool) -> None:
        self._resolution: dict[str, float] = {}
        self._last_nonzero: dict[str, float] = {}
        self._settle = settle
        self._single_step = single_step
        self.estimate: float | None = None
        self.samples: list[float] = []
        self.confirmed: list[_Interval] = []  # quiet over their span, delay passed
        self._armed: tuple[str, datetime, float, bool] | None = None
        self._pending: list[_Interval] = []
        self._last_slot: datetime | None = None
        self._last_slot_active: frozenset[str] = frozenset()

    def on_break(self, _event: _Break) -> None:
        self._armed = None
        self._pending = []

    def _learn(self, r: _Peak) -> None:
        """The resolution of an energy-family sensor is the smallest positive peak
        height seen. There is no starting value: until one is learned, nothing
        is validated."""
        if r.is_energy:
            known = self._resolution.get(r.source)
            self._resolution[r.source] = r.height if known is None else min(known, r.height)

    def on_seen(self, seen: _Seen) -> None:
        """The resolution of a power-family sensor is the smallest step between
        two distinct non-zero readings seen (all readings, not only peaks)."""
        before = self._last_nonzero.get(seen.source)
        self._last_nonzero[seen.source] = seen.value
        if before is not None and before != seen.value:
            step = abs(before - seen.value)
            known = self._resolution.get(seen.source)
            self._resolution[seen.source] = step if known is None else min(known, step)

    def on_peak(self, r: _Peak) -> None:
        self._learn(r)
        armed = self._armed
        if armed and armed[0] == r.source and r.t_prev == armed[1] and r.power_w is not None:
            self._pending.append(
                _Interval(r.source, armed[1], r.t, r.power_w, armed[2], r.height, r.is_energy)
            )
            self._armed = (r.source, r.t, r.height, r.is_energy)
            return
        self._armed = None
        if self._last_slot == _slot_of(r.t) - SLOT and not (self._last_slot_active - {r.source}):
            self._armed = (r.source, r.t, r.height, r.is_energy)

    def on_slot(self, slot: datetime, activity: Activity) -> None:
        self._last_slot = slot
        self._last_slot_active = activity.get(slot, frozenset())

        def disturbed(source: str, t1: datetime) -> bool:
            first = _slot_of(t1) - SLOT
            return any(
                first <= s <= slot and bool(active - {source}) for s, active in activity.items()
            )

        if self._armed and disturbed(self._armed[0], self._armed[1]):
            self._armed = None
        self._pending = [p for p in self._pending if not disturbed(p.source, p.t1)]
        ready = [p for p in self._pending if slot >= _slot_of(p.t2) + SLOT + self._settle]
        self._pending = [p for p in self._pending if p not in ready]
        for interval in ready:
            self._confirm(interval)

    def _confirm(self, n: _Interval) -> None:
        """Quiet over its span and old enough. It counts only if its peaks have
        the same height (they differ by less than the learned resolution) and,
        for an energy-family sensor, both are exactly one step."""
        self.confirmed.append(n)
        resolution = self._resolution.get(n.source)
        if resolution is None:
            return  # no resolution learned yet: nothing can be validated
        same = abs(n.start_height - n.end_height) < resolution
        if self._single_step and n.is_energy:
            same = same and n.start_height < 1.5 * resolution and n.end_height < 1.5 * resolution
        if same:
            self.samples.append(n.power_w)
            self.estimate = (
                n.power_w
                if self.estimate is None
                else (1.0 - EMA_WEIGHT) * self.estimate + EMA_WEIGHT * n.power_w
            )


def _energy_events(source: str, raw: RawStates) -> list[_Peak | _Break]:
    """A jump becomes a peak: its height is the energy of the jump, its power
    equivalent that energy divided by the time since the previous jump (only if
    that was a jump)."""
    events: list[_Peak | _Break] = []
    previous_was_jump = False
    for t1, v1, t2, v2, was_offline in _find_transitions(raw):
        delta_wh = (v2 - v1) * 1000.0
        if was_offline or delta_wh < 0.0:
            events.append(_Break(t2, source))
            previous_was_jump = False
        elif delta_wh == 0.0:
            previous_was_jump = False
        else:
            hours = (t2 - t1).total_seconds() / 3600.0
            events.append(
                _Peak(
                    t2,
                    source,
                    t1 if previous_was_jump else None,
                    delta_wh / hours if previous_was_jump else None,
                    delta_wh,
                    True,
                )
            )
            previous_was_jump = True
    return events


def _power_events(source: str, raw: RawStates) -> list[_Peak | _Break | _Seen]:
    """A power-family reading above 0 is a peak if the readings directly before
    and after it are not above 0; two consecutive readings above 0 are no peak
    (sustained power) and break the chain. The power equivalent is the reading."""
    values: list[tuple[datetime, float | None]] = []
    for t, state in raw:
        try:
            values.append((t, float(state)))
        except ValueError:
            values.append((t, None))
    events: list[_Peak | _Break | _Seen] = []
    prev_peak: datetime | None = None
    for i, (t, v) in enumerate(values):
        before = values[i - 1][1] if i > 0 else 0.0
        after = values[i + 1][1] if i + 1 < len(values) else 0.0
        if v is None:
            events.append(_Break(t, source))
            prev_peak = None
        elif v > 0.0:
            events.append(_Seen(t, source, v))
            if (before or 0.0) > 0.0 or (after or 0.0) > 0.0:
                events.append(_Break(t, source))
                prev_peak = None
            else:
                events.append(_Peak(t, source, prev_peak, v, v, False))
                prev_peak = t
    return events


def _activity_at(raws: dict[str, RawStates], tau: datetime) -> Activity:
    """Which sensors show flow in which slot, as the recorder statistics would
    know it at time ``tau`` (a lagging counter has not redistributed yet)."""
    activity: dict[datetime, set[str]] = {}
    for source, raw in raws.items():
        truncated = [(t, v) for t, v in raw if t <= tau]
        if len(truncated) < 2:
            continue
        contributions: dict[datetime, float] = trapezoidal_slot_contributions(truncated, now=tau)
        for slot, value in contributions.items():
            if value > 0.0:
                activity.setdefault(slot, set()).add(source)
    return {slot: frozenset(sources) for slot, sources in activity.items()}


def _run(
    energy: dict[str, RawStates],
    start: datetime,
    end: datetime,
    *,
    bms: frozenset[str] | None = None,
    power: dict[str, RawStates] | None = None,
    settle: timedelta = SETTLE,
    single_step: bool = True,
) -> _DrainMachine:
    """Replay peaks and completed slots through the machine in time order (a
    slot event sorts before a peak at the same instant). Only BMS inputs can be
    drain sources; every sensor counts as activity."""
    events: list[tuple[datetime, int, _Peak | _Break | _Seen | datetime]] = []
    for source, raw in energy.items():
        if bms is None or source in bms:
            events.extend((e.t, 1, e) for e in _energy_events(source, raw))
    for source, raw in (power or {}).items():
        events.extend((e.t, 1, e) for e in _power_events(source, raw))
    slot = _slot_of(start)
    while slot + SLOT <= end:
        events.append((slot + SLOT, 0, slot))
        slot += SLOT
    events.sort(key=lambda item: (item[0], item[1]))
    machine = _DrainMachine(settle, single_step)
    for when, _kind, payload in events:
        if isinstance(payload, _Peak):
            machine.on_peak(payload)
        elif isinstance(payload, _Break):
            machine.on_break(payload)
        elif isinstance(payload, _Seen):
            machine.on_seen(payload)
        else:
            machine.on_slot(payload, _activity_at(energy, when))
    return machine


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 1, 11, hour, minute, second, tzinfo=UTC)


def _night(
    extra: list[tuple[datetime, datetime, int]] | None = None, standby_w: int = 12, hours: int = 8
) -> tuple[RawStates, datetime]:
    """The discharge counter of a quiet night (10 Wh resolution) plus optional
    extra draws, and the end of the night."""
    end = T0 + timedelta(hours=hours)
    return _simulate_counter([(T0, end, standby_w), *(extra or [])], T0, end), end


def _load_world(load_w: int, minutes: int) -> tuple[dict[str, RawStates], datetime]:
    """12 W standby plus ``load_w`` delivered for ``minutes`` from 03:00 (10 Wh in
    total, so the 10 Wh *output* counter jumps once, when the load ends)."""
    load = (T0 + timedelta(hours=5), T0 + timedelta(hours=5, minutes=minutes))
    discharge, end = _night([(*load, load_w)])
    return {
        "discharge": discharge,
        "grid_out": _simulate_counter([(*load, load_w)], T0, end, base_kwh=5.00),
    }, end


class TestDrainMachinePrototype:
    # ---- the estimate itself ------------------------------------------------

    def test_standby_is_the_time_between_two_one_step_peaks(self) -> None:
        discharge, end = _night(hours=3)
        heights = [
            round((float(b[1]) - float(a[1])) * 1000.0) for a, b in zip(discharge, discharge[1:])
        ]
        assert heights == [10, 10, 10]  # every peak is one 10 Wh step
        machine = _run({"discharge": discharge}, T0, end, settle=timedelta(0))
        # 12 W: 10 Wh every 50 min; high standby would just be a shorter delta
        assert machine.samples == [pytest.approx(12.0), pytest.approx(12.0)]
        assert machine.estimate == pytest.approx(12.0)

    def test_a_single_drain_interval_counts(self) -> None:
        discharge, end = _night(hours=2)  # peaks at 22:50 and 23:40
        machine = _run({"discharge": discharge}, T0, end, settle=timedelta(0))
        assert machine.samples == [pytest.approx(12.0)]

    def test_higher_standby_is_a_shorter_time_between_peaks(self) -> None:
        discharge, end = _night(standby_w=24, hours=3)
        machine = _run({"discharge": discharge}, T0, end, settle=timedelta(0))
        assert machine.estimate == pytest.approx(24.0)

    def test_fixed_window_energy_is_quantisation_limited_but_peaks_are_not(self) -> None:
        discharge, end = _night(hours=3)
        naive_w = (len(discharge) - 1) * 10.0 / 3.0  # jumps seen in 3 h × 10 Wh ÷ 3 h
        assert abs(naive_w - 12.0) / 12.0 > 0.15
        assert _run({"discharge": discharge}, T0, end).estimate is None  # delay not yet passed
        estimate = _run({"discharge": discharge}, T0, end, settle=timedelta(0)).estimate
        assert estimate == pytest.approx(12.0)

    @pytest.mark.parametrize("jitter_s", [30, 60])
    def test_polling_delay_needs_no_tolerance_because_heights_stay_exact(
        self, jitter_s: int
    ) -> None:
        end = T0 + timedelta(hours=8)
        discharge = _simulate_counter([(T0, end, 12)], T0, end, jitter_s=jitter_s)
        machine = _run({"discharge": discharge}, T0, end)
        assert len(machine.samples) >= 4  # every interval counts: all peaks are 10 Wh
        assert machine.estimate == pytest.approx(12.0, rel=0.01)

    # ---- the same-height rule -----------------------------------------------

    def test_ten_wh_then_twenty_wh_disqualifies_the_interval(self) -> None:
        discharge: RawStates = [
            (_at(1, 0), "1.00"),
            (_at(2, 0), "1.01"),  # peak of 10 Wh
            (_at(3, 0), "1.03"),  # peak of 20 Wh: two steps at once, i.e. load
        ]
        machine = _run({"discharge": discharge}, _at(1, 0), _at(5, 0), settle=timedelta(0))
        assert len(machine.confirmed) == 1
        assert machine.samples == []

    def test_a_drain_peak_must_be_exactly_one_learned_step(self) -> None:
        discharge: RawStates = [
            (_at(0, 0), "1.00"),
            (_at(0, 30), "1.01"),  # 10 Wh: the smallest step seen, i.e. the resolution
            (_at(2, 0), "1.03"),  # 20 Wh
            (_at(3, 0), "1.05"),  # 20 Wh: the same height, but two steps
        ]
        strict = _run({"discharge": discharge}, _at(0, 0), _at(6, 0), settle=timedelta(0))
        assert strict.samples == []
        free = _run(
            {"discharge": discharge}, _at(0, 0), _at(6, 0), settle=timedelta(0), single_step=False
        )
        assert free.samples == [pytest.approx(20.0)]

    def test_the_resolution_is_learned_from_the_smallest_peak_seen(self) -> None:
        # the first jump is a double step (a reporting gap); one-step peaks follow
        discharge: RawStates = [
            (_at(0, 0), "1.00"),
            (_at(1, 0), "1.02"),  # 20 Wh
            (_at(2, 0), "1.03"),  # 10 Wh: start 20, end 10 differ by one step
            (_at(3, 0), "1.04"),  # 10 Wh, 10 Wh: counts
            (_at(4, 0), "1.05"),
        ]
        machine = _run({"discharge": discharge}, _at(0, 0), _at(7, 0), settle=timedelta(0))
        assert [round(i.power_w) for i in machine.confirmed] == [10, 10, 10]
        assert machine.samples == [pytest.approx(10.0), pytest.approx(10.0)]

    def test_a_lone_60_watt_peak_without_consumption_is_just_a_high_standby(self) -> None:
        # the BMS itself draws 60 W for 10 min from a jump instant at 03:00: the
        # peaks are still one step each, only the time between them is shorter
        burst = (T0 + timedelta(hours=5), T0 + timedelta(hours=5, minutes=10), 48)
        discharge, end = _night([burst])
        gaps = [round((b[0] - a[0]).total_seconds()) for a, b in zip(discharge, discharge[1:])]
        assert 600 in gaps  # 10 Wh in 10 min, i.e. 60 W
        machine = _run({"discharge": discharge}, T0, end)
        assert any(sample == pytest.approx(60.0, rel=0.01) for sample in machine.samples)
        # the 5 % weight bounds its effect: at most 5 % of the 48 W excess
        assert machine.estimate is not None
        assert 12.0 < machine.estimate < 12.0 + 0.05 * 48.0

    def test_a_constant_60_watts_is_a_standby_of_60_watts(self) -> None:
        discharge, end = _night(standby_w=60)
        assert _run({"discharge": discharge}, T0, end).estimate == pytest.approx(60.0, rel=0.01)

    # ---- hidden loads: the quiet test and the validation delay ------------------

    def test_a_fast_load_is_caught_because_validation_rereads_the_span(self) -> None:
        world, end = _load_world(60, 10)  # output counter jumps at the end of the load
        machine = _run(world, T0, end, settle=timedelta(0))
        assert machine.estimate == pytest.approx(12.0)
        assert max(machine.samples) == pytest.approx(12.0)

    def test_a_slow_load_needs_the_validation_delay(self) -> None:
        # 20 W for 30 min: the output counter only jumps at 03:30, after the
        # contaminated interval (12 W + 20 W = 32 W) could be validated
        world, end = _load_world(20, 30)
        bare = _run(world, T0, end, settle=timedelta(0))
        assert any(sample == pytest.approx(32.0) for sample in bare.samples)
        assert bare.estimate == pytest.approx(12.0 + 0.05 * 20.0 * 0.95**2)
        delayed = _run(world, T0, end)
        assert delayed.estimate == pytest.approx(12.0)

    def test_a_load_below_every_counter_resolution_leaks_a_bounded_sample(self) -> None:
        # 5 W for 60 min = 5 Wh: the output counter does not jump for hours
        end = T0 + timedelta(hours=8)
        load = (T0 + timedelta(hours=5), T0 + timedelta(hours=6))
        discharge, _ = _night([(*load, 5)])
        world = {
            "discharge": discharge,
            "grid_out": _simulate_counter([(*load, 5)], T0, end, base_kwh=5.00),
        }
        assert len(world["grid_out"]) == 1  # never jumps
        machine = _run(world, T0, end)
        assert any(sample == pytest.approx(17.0, rel=0.01) for sample in machine.samples)
        assert machine.estimate == pytest.approx(12.0 + 0.05 * 5.0, rel=0.01)

    # ---- which inputs can be the drain source ----------------------------------

    def test_standby_fed_by_the_grid_import_needs_no_configuration(self) -> None:
        end = T0 + timedelta(hours=8)
        world = {
            "discharge": [(T0, "12.30")],
            "grid_in": _simulate_counter([(T0, end, 12)], T0, end, base_kwh=100.00),
        }
        machine = _run(world, T0, end, bms=frozenset({"discharge", "grid_in"}))
        assert machine.estimate == pytest.approx(12.0)

    def test_two_bms_inputs_sharing_the_standby_give_no_estimate(self) -> None:
        end = T0 + timedelta(hours=8)
        world = {
            "discharge": _simulate_counter([(T0, end, 6)], T0, end),
            "grid_in": _simulate_counter([(T0, end, 6)], T0, end, base_kwh=100.00),
        }
        machine = _run(world, T0, end, bms=frozenset({"discharge", "grid_in"}))
        assert machine.samples == []
        assert machine.estimate is None

    def test_a_pv_input_is_never_a_drain_source(self) -> None:
        # only PV moves (12 W, nothing out): loss and drain cannot be told apart
        end = T0 + timedelta(hours=8)
        world = {
            "discharge": [(T0, "12.30")],
            "pv": _simulate_counter([(T0, end, 12)], T0, end, base_kwh=100.00),
        }
        machine = _run(world, T0, end, bms=frozenset({"discharge"}))
        assert machine.estimate is None

    # ---- margins, breaks, adaptation ----------------------------------------

    @pytest.mark.parametrize(
        ("t1", "accepted"),
        [(_at(10, 10), True), (_at(10, 9), False)],
    )
    def test_margin_before_the_first_peak(self, t1: datetime, accepted: bool) -> None:
        # the other sensor is active in slot 10:00 (10:00–10:05)
        other: RawStates = [(_at(10, 0), "5.00"), (_at(10, 5), "5.01")]
        discharge: RawStates = [(_at(9, 0), "1.00"), (t1, "1.01"), (_at(11, 0), "1.02")]
        machine = _run(
            {"discharge": discharge, "other": other},
            _at(9, 0),
            _at(12, 0),
            bms=frozenset({"discharge"}),
            settle=timedelta(0),
        )
        assert (len(machine.confirmed) == 1) is accepted

    @pytest.mark.parametrize("other_slot", [_at(11, 0), _at(11, 5)])
    def test_margin_after_the_last_peak(self, other_slot: datetime) -> None:
        # t2 = 11:02: the slots 11:00 and 11:05 both lie within 5 min after it
        other: RawStates = [(other_slot, "5.00"), (other_slot + SLOT, "5.01")]
        discharge: RawStates = [(_at(9, 0), "1.00"), (_at(10, 10), "1.01"), (_at(11, 2), "1.02")]
        machine = _run(
            {"discharge": discharge, "other": other},
            _at(9, 0),
            _at(12, 0),
            bms=frozenset({"discharge"}),
            settle=timedelta(0),
        )
        assert machine.confirmed == []
        control = _run({"discharge": discharge}, _at(9, 0), _at(12, 0), settle=timedelta(0))
        assert [i.power_w for i in control.confirmed] == [pytest.approx(10.0 / (52.0 / 60.0))]

    def test_offline_gaps_and_counter_resets_break_the_chain(self) -> None:
        discharge: RawStates = [
            (_at(1, 0), "1.00"),
            (_at(1, 50), "1.01"),  # first jump: arms
            (_at(2, 0), "unavailable"),
            (_at(3, 0), "1.02"),  # recovery: not a jump
            (_at(3, 50), "1.03"),  # jump: arms again
            (_at(4, 30), "0.00"),  # counter reset: break
            (_at(5, 20), "0.01"),  # jump: arms again
            (_at(6, 10), "0.02"),  # interval 1
            (_at(7, 0), "0.03"),  # interval 2
        ]
        machine = _run({"discharge": discharge}, _at(1, 0), _at(7, 30), settle=timedelta(0))
        assert machine.samples == [pytest.approx(12.0), pytest.approx(12.0)]

    def test_estimate_follows_a_seasonal_change_with_five_percent_per_sample(self) -> None:
        end = T0 + timedelta(hours=45)
        change = T0 + timedelta(hours=15)
        discharge = _simulate_counter([(T0, change, 12), (change, end, 14)], T0, end)
        machine = _run({"discharge": discharge}, T0, end)
        expected: float | None = None
        for sample in machine.samples:
            expected = sample if expected is None else 0.95 * expected + 0.05 * sample
        assert machine.estimate == pytest.approx(expected)
        assert machine.samples[0] == pytest.approx(12.0)
        assert machine.samples[-1] == pytest.approx(14.0, rel=0.001)
        assert machine.estimate is not None and 13.5 < machine.estimate < 14.0

    # ---- power-family sources --------------------------------------------------

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
        steady = _run({}, T0, end, power={"bms_power": self._power_raw(12.0, 1)})
        assert steady.samples
        shifting = _run({}, T0, end, power={"bms_power": self._power_raw(12.0, 1, 12.5)})
        assert shifting.samples == []  # 12.0 then 12.5: neighbours differ by 0.5 W

    def test_power_peaks_with_no_learned_resolution_validate_nothing(self) -> None:
        # only 0 and 12.0 W are ever read: no step between distinct non-zero
        # readings, hence no resolution and no starting value to fall back on
        end = T0 + timedelta(hours=12)
        raw = self._power_raw(12.0, 1, with_load_readings=False)
        machine = _run({}, T0, end, power={"bms_power": raw})
        assert machine.confirmed  # the intervals exist, but nothing can validate them
        assert machine.samples == []
        assert machine.estimate is None

    def test_two_consecutive_readings_above_zero_are_no_peak(self) -> None:
        end = T0 + timedelta(hours=12)
        machine = _run({}, T0, end, power={"bms_power": self._power_raw(12.0, 2)})
        assert machine.samples == []
        assert machine.estimate is None
