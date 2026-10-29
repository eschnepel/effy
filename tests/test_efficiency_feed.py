"""TASK-0020 — the efficiency machine fed from the recorder (ADR-023 decisions 3, 9).

Runs the *real* ``history.async_recalculate_recent``, ``async_update_standby`` and
``async_update_efficiency`` cycle by cycle against the faked recorder of
``test_history_recent_equivalence`` (raw states, statistics and writes in memory), on a
simulated battery system with known efficiencies. The machine reads the statistics Effy
itself wrote plus the recorded standby statistic, so these tests prove the chain: raw
counters → derived power → kWh → pure runs → estimates, a restart in the middle of a run,
the catch-up, and that the feed only reads.
"""

from __future__ import annotations

import copy
import inspect
import json
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_history_recent_equivalence import (
    _energy_world,
    _history,
    _install,
    _options,
    _World,
)
from tests.test_standby import T0

SLOT = timedelta(minutes=5)
GI, DIS, CH, GE = "sensor.grid_in", "sensor.discharge", "sensor.charge", "sensor.grid_out"
STANDBY_ID = "sensor.effy_standby_power"
STANDBY_W = 20
ETA_C, ETA_D = 0.95, 0.90

_calc = _history.run_feed.__globals__
RunState = _calc["RunState"]
run_state_from_restore = _calc["run_state_from_restore"]
run_restore_data = _calc["run_restore_data"]
DrainState = _history.DrainState


POLL_SECONDS = 30  # a real integration reports every few seconds to a minute, not on every Wh


def _counter(
    segments: list[tuple[datetime, datetime, int]], start: datetime, end: datetime
) -> list[tuple[datetime, str]]:
    """Raw states of a kWh counter that ticks in 1 Wh (three decimals) and is *polled*
    every ``POLL_SECONDS``: a state exists only when a poll sees a new value, as with a
    real integration (at 3 kW a bare 1 Wh counter would change every 1.2 s, 60 times
    denser than anything Home Assistant records, and the simulation would spend its time
    in the redistribution of those readings). The segments' powers add up. A coarser
    *resolution* would tick so rarely at the standby power that its next tick is spread
    back over a whole charge, and an idle counter would then look active in every slot
    (the glossary's activity has no tolerance, ADR-023 decision 3). Integer arithmetic:
    the times of the polls are exact."""
    total = int((end - start).total_seconds())
    watts = [0] * total
    for seg_start, seg_end, w in segments:
        first = max(int((seg_start - start).total_seconds()), 0)
        last = min(int((seg_end - start).total_seconds()), total)
        for i in range(first, last):
            watts[i] += w
    states = [(start, "12.300")]
    energy_ws, reported, quantum = 0, 0, 3600  # 1 Wh in watt-seconds
    for i in range(total):
        energy_ws += watts[i]
        if (i + 1) % POLL_SECONDS == 0 and energy_ws // quantum > reported:
            reported = energy_ws // quantum
            states.append((start + timedelta(seconds=i + 1), f"{12.3 + reported / 1000:.3f}"))
    return states


def _hours(a: float, b: float) -> tuple[datetime, datetime]:
    return T0 + timedelta(hours=a), T0 + timedelta(hours=b)


# (from hour, to hour, power in W): charging takes the power from the grid, discharging
# gives it from the battery; the standby power is part of both counters that feed it.
# The runs are hours long on purpose: a counter that does not tick is redistributed back
# over up to 120 min when it ticks next (ADR-012), so the idle counters of a short run
# all look active in it and no slot of it is pure; a long run keeps pure slots.
CHARGES = [(0.5, 3.5, 3000)]
DISCHARGES = [(4.0, 7.0, 2500)]


def _segments() -> dict[str, list[tuple[datetime, datetime, int]]]:
    segments: dict[str, list[tuple[datetime, datetime, int]]] = {GI: [], DIS: [], CH: [], GE: []}
    for a, b, watts in CHARGES:
        start, end = _hours(a, b)
        segments[GI].append((start, end, watts))
        segments[CH].append((start, end, round((watts - STANDBY_W) * ETA_C)))
    for a, b, watts in DISCHARGES:
        start, end = _hours(a, b)
        segments[DIS].append((start, end, watts))
        segments[GE].append((start, end, round((watts - STANDBY_W) * ETA_D)))
    # the battery feeds the standby power whenever the grid does not (ADR-018 A1)
    cursor = T0
    for a, b, _w in sorted(CHARGES):
        start, end = _hours(a, b)
        if start > cursor:
            segments[DIS].append((cursor, start, STANDBY_W))
        cursor = end
    segments[DIS].append((cursor, T0 + timedelta(hours=10), STANDBY_W))
    return segments


def _world(hours: int = 10) -> _World:
    end = T0 + timedelta(hours=hours)
    segments = _segments()
    world = _energy_world(**{eid: _counter(segs, T0, end) for eid, segs in segments.items()})
    standby: dict[datetime, float] = {}
    slot = T0
    while slot < end:
        standby[slot] = float(STANDBY_W)
        slot += SLOT
    world.store[STANDBY_ID] = standby
    return world


def _opts() -> dict[str, Any]:
    return _options([], [CH, GE], [GI, DIS])


def _serve_store(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve what Effy wrote and what the recorder compiled (the standby) to the readers."""

    def fetch(
        _hass: Any, eids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        world.stats_calls.append((start, end))
        return {
            eid: [
                {"start": s, "mean": v}
                for s, v in sorted(world.store.get(eid, {}).items())
                if start <= s and s + SLOT <= end
            ]
            for eid in eids
        }

    monkeypatch.setattr(_history, "_fetch_statistics", fetch)


def _rig(monkeypatch: pytest.MonkeyPatch, world: _World) -> None:
    _install(monkeypatch, world)
    _serve_store(world, monkeypatch)


FIRST = T0 + timedelta(minutes=10, seconds=5)
LAST = T0 + timedelta(hours=9, minutes=10, seconds=5)


async def _cycles(
    world: _World,
    options: dict[str, Any],
    first: datetime,
    last: datetime,
    *,
    efficiency: Any = None,
    standby: Any = None,
    use_efficiency: bool = True,
    history: dict[str, Any] | None = None,
) -> tuple[Any, Any]:
    """Recent recalculation, standby update and efficiency update every 5 minutes at
    HH:MM:05, as the coordinator's slot job does, under one reading cache. Returns the
    efficiency and standby states."""
    cache: dict[str, tuple[datetime, str]] = {}
    previous: datetime | None = None
    efficiency = RunState() if efficiency is None else efficiency
    standby = DrainState() if standby is None else standby
    now = first
    while now <= last:
        await _history.async_recalculate_recent(
            object(),
            options,
            now,
            energy_reading_cache=cache,
            trigger_since=_history.recent_trigger_since(previous, now),
        )
        previous = now
        standby = await _history.async_update_standby(object(), options, standby, now)
        if use_efficiency:
            efficiency = await _history.async_update_efficiency(object(), options, efficiency, now)
            if history is not None:
                history[now.isoformat()] = run_restore_data(efficiency)
        now += SLOT
    return efficiency, standby


_BASELINE: list[Any] = []  # [(efficiency state, standby state, the world after the run)]


async def _baseline(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, _World]:
    """The uninterrupted run over the whole simulation, computed once and handed out as
    a private copy of its world (the run is deterministic, so every test may share it)."""
    if not _BASELINE:
        world = _world()
        _rig(monkeypatch, world)
        efficiency, standby = await _cycles(world, _opts(), FIRST, LAST)
        _BASELINE.append((efficiency, standby, copy.deepcopy(world)))
    efficiency, standby, kept = _BASELINE[0]
    world = copy.deepcopy(kept)
    _rig(monkeypatch, world)
    return efficiency, standby, world


class TestTheSimulationStaysCheap:
    """The simulation runs the real recalculation 109 times per pass, and its cost grows
    with the number of raw readings (the trapezoidal redistribution). At one reading per
    Wh (about 9,000 per counter) one pass took 9.5 s and the file 130 s; polled every
    30 s (a few hundred) it takes 0.2 s. Guard the density, not the clock."""

    def test_the_counters_are_polled_not_ticked_on_every_wh(self) -> None:
        world = _world()
        for entity_id, readings in world.energy.items():
            assert len(readings) < 600, (entity_id, len(readings))
        # and they are still fine enough for the idle counters to tick during a charge
        # run's neighbours: the discharge counter sees its 20 W standby every few minutes
        idle = [t for t, _v in world.energy[DIS] if t < T0 + timedelta(hours=0.4)]
        assert len(idle) >= 5

    def test_a_poll_sees_a_reading_only_when_the_value_changed(self) -> None:
        readings = _counter([(T0, T0 + timedelta(hours=1), 3600)], T0, T0 + timedelta(hours=2))
        values = [v for _t, v in readings]
        assert values == sorted(set(values), key=values.index)  # no duplicate states
        gaps = {
            (b[0] - a[0]).total_seconds() for a, b in zip(readings[1:], readings[2:], strict=False)
        }
        assert gaps <= {float(POLL_SECONDS) * k for k in range(1, 4)}  # a multiple of the poll


class TestTheWholeChain:
    @pytest.mark.asyncio
    async def test_the_estimates_find_the_efficiencies_of_the_battery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state, _standby, _world_ = await _baseline(monkeypatch)
        assert state.charge.counted_runs == 1 and state.discharge.counted_runs == 1
        # only the pure slots count: of 9 kWh of grid import and 7.5 kWh of discharge
        assert 1.0 < state.charge.counted_kwh < 4.0
        assert 5.0 < state.discharge.counted_kwh < 7.5
        assert state.charge.value == pytest.approx(ETA_C, abs=0.02)
        assert state.discharge.value == pytest.approx(ETA_D, abs=0.02)
        # the last job ran at LAST (9 h 10 min 5 s): the newest slot the machine took is the
        # last one that is 125 minutes old by then, 7 h 5 min 5 s after T0
        assert state.last_slot == T0 + timedelta(hours=7, minutes=5)

    @pytest.mark.asyncio
    async def test_the_energies_are_read_in_kwh_and_the_standby_in_kwh_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state, _standby, _world_ = await _baseline(monkeypatch)
        slot = T0 + timedelta(hours=1, minutes=0)  # in the middle of the charge
        energy, standby = await _history.async_read_efficiency_energy(
            object(), _opts(), slot, slot + SLOT
        )
        assert set(energy[slot]) == {GI, DIS, CH, GE}
        assert energy[slot][GI] == pytest.approx(3.0 * 5 / 60, abs=0.02)  # 3 kW for 5 min
        assert energy[slot][CH] == pytest.approx(2.98 * ETA_C * 5 / 60, abs=0.02)
        assert energy[slot][DIS] == pytest.approx(0.0, abs=0.011)
        assert standby[slot] == pytest.approx(STANDBY_W / 1000 * 5 / 60)

    @pytest.mark.asyncio
    async def test_it_only_reads_and_never_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _state, _standby, world = await _baseline(monkeypatch)
        writes, stored = len(world.write_calls), copy.deepcopy(world.store)
        state = RunState()
        for now in (LAST - timedelta(hours=1), LAST):
            state = await _history.async_update_efficiency(object(), _opts(), state, now)
        assert len(world.write_calls) == writes
        assert world.store == stored


class TestRestart:
    @pytest.mark.asyncio
    # at 3.0 h the machine has taken the slots up to 0.9 h (in the pure part of the charge),
    # at 8.0 h those up to 5.9 h (in the middle of the discharge)
    @pytest.mark.parametrize("cut_hours", [3.0, 8.0])
    async def test_a_restart_in_the_middle_equals_uninterrupted_operation(
        self, monkeypatch: pytest.MonkeyPatch, cut_hours: float
    ) -> None:
        whole, _standby, _w = await _baseline(monkeypatch)

        world = _world()
        _rig(monkeypatch, world)
        cut = T0 + timedelta(hours=cut_hours, seconds=5)
        first_half, _ = await _cycles(world, _opts(), FIRST, cut)
        assert first_half.prev1 is not None and first_half.last_slot is not None
        # the sensor stored it as JSON; Home Assistant restarts; the sensor restores it
        restored = run_state_from_restore(json.loads(json.dumps(run_restore_data(first_half))))
        assert restored == first_half
        resumed, _ = await _cycles(
            world, _opts(), cut + SLOT, LAST, efficiency=restored, use_efficiency=True
        )
        assert resumed == whole

    @pytest.mark.asyncio
    async def test_a_restart_before_any_slot_has_settled_starts_from_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whole, _standby, _w = await _baseline(monkeypatch)
        world = _world()
        _rig(monkeypatch, world)
        early, _ = await _cycles(world, _opts(), FIRST, T0 + timedelta(hours=1, seconds=5))
        assert early == RunState()  # nothing is 125 minutes old yet
        # nothing to restore: the coordinator starts fresh, which is the same state
        assert run_state_from_restore(run_restore_data(early)) is None
        resumed, _ = await _cycles(
            world,
            _opts(),
            T0 + timedelta(hours=1, minutes=5, seconds=5),
            LAST,
            efficiency=RunState(),
        )
        assert resumed == whole

    @pytest.mark.asyncio
    async def test_a_restart_with_an_older_state_catches_up_to_the_same_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whole, _standby, _w = await _baseline(monkeypatch)
        older = await _history.async_update_efficiency(
            object(), _opts(), RunState(), T0 + timedelta(hours=6)
        )
        assert older.last_slot is not None and whole.last_slot is not None
        assert older.last_slot < whole.last_slot
        caught_up = await _history.async_update_efficiency(object(), _opts(), older, LAST)
        assert caught_up == whole

    @pytest.mark.asyncio
    async def test_a_first_start_catches_up_everything_within_three_days(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whole, _standby, world = await _baseline(monkeypatch)
        world.stats_calls.clear()
        fresh = await _history.async_update_efficiency(object(), _opts(), RunState(), LAST)
        assert fresh == whole
        earliest = min(start for start, _end in world.stats_calls)
        assert earliest >= LAST - timedelta(minutes=125) - timedelta(days=3) - SLOT

    @pytest.mark.asyncio
    async def test_the_catch_up_is_bounded_to_three_days(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state, _standby, world = await _baseline(monkeypatch)
        world.stats_calls.clear()
        later = LAST + timedelta(days=10)
        await _history.async_update_efficiency(object(), _opts(), RunState(), later)
        floor = later - timedelta(minutes=125) - timedelta(days=3)
        assert all(start >= floor - SLOT for start, _end in world.stats_calls)


class TestWhatIsRead:
    @pytest.mark.asyncio
    async def test_nothing_is_read_without_a_complete_counter_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state, _standby, world = await _baseline(monkeypatch)
        pairless = _options([], [], [])  # discharge and charge: no pair
        pairless["discharge_sensor"], pairless["charge_sensor"] = DIS, CH
        assert not _history.efficiency_enabled(pairless)
        world.stats_calls.clear()
        state = RunState()
        assert await _history.async_update_efficiency(object(), pairless, state, LAST) is state
        assert world.stats_calls == []

    def test_a_pair_is_grid_import_with_charge_or_discharge_with_grid_export(self) -> None:
        assert _history.efficiency_enabled(_options([], [CH, GE], [GI, DIS]))
        assert _history.efficiency_enabled(_options([], [CH], [GI]))  # grid import + charge
        assert _history.efficiency_enabled(_options([], [], [])) is False
        only_export = _options([], [], [])
        only_export["discharge_sensor"] = DIS
        only_export["grid_export_sensor"] = GE
        assert _history.efficiency_enabled(only_export)
        roles = _history.efficiency_roles(_opts())
        assert (roles.grid_import, roles.discharge, roles.charge, roles.grid_export) == (
            GI,
            DIS,
            CH,
            GE,
        )

    @pytest.mark.asyncio
    async def test_a_second_call_without_new_settled_slots_reads_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state, _standby, world = await _baseline(monkeypatch)
        world.stats_calls.clear()
        again = await _history.async_update_efficiency(
            object(), _opts(), state, LAST + timedelta(minutes=1)
        )
        assert again == state and world.stats_calls == []

    @pytest.mark.asyncio
    async def test_a_missing_statistic_reaches_the_machine_as_a_missing_slot_never_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state, _standby, world = await _baseline(monkeypatch)
        gap = T0 + timedelta(hours=1, minutes=0)
        no_standby = T0 + timedelta(hours=1, minutes=10)
        power_ch = _history._effy_power_entity_id(CH)
        del world.store[power_ch][gap]  # a counter without a statistic
        del world.store[STANDBY_ID][no_standby]  # no standby statistic
        seen: list[Any] = []
        real = _history.run_feed

        def spy(state: Any, slots: Any, roles: Any, now: datetime) -> Any:
            seen.extend(slots)
            return real(state, slots, roles, now)

        monkeypatch.setattr(_history, "run_feed", spy)
        await _history.async_update_efficiency(object(), _opts(), RunState(), LAST)
        by_start = {slot.start: slot for slot in seen}
        assert gap not in by_start  # missing, not an empty slot of zeros
        assert by_start[no_standby].standby_kwh is None  # present, but it will not count
        assert by_start[gap - SLOT].standby_kwh == pytest.approx(STANDBY_W / 1000 * 5 / 60)

    @pytest.mark.asyncio
    async def test_a_missing_slot_ends_the_run_in_the_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whole, _standby, world = await _baseline(monkeypatch)
        world.store[_history._effy_power_entity_id(CH)].pop(T0 + timedelta(hours=1))
        broken = await _history.async_update_efficiency(object(), _opts(), RunState(), LAST)
        assert broken.charge.counted_kwh < whole.charge.counted_kwh  # the gap cost slots


class TestNothingElseChanges:
    @pytest.mark.asyncio
    async def test_every_existing_series_and_the_standby_are_identical_with_and_without_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state, standby_with, with_world = await _baseline(monkeypatch)
        world = _world()
        _rig(monkeypatch, world)
        _eff, standby_without = await _cycles(world, _opts(), FIRST, LAST, use_efficiency=False)
        effy_without = {k: v for k, v in world.store.items() if k != STANDBY_ID}
        effy_with = {k: v for k, v in with_world.store.items() if k != STANDBY_ID}
        assert effy_without == effy_with
        assert effy_without  # there were effy_* series to compare
        assert standby_without == standby_with
        assert (len(world.write_calls), world.write_flags) == (
            len(with_world.write_calls),
            with_world.write_flags,
        )


class TestRebuildAfterTheHistoryRecalculation:
    """TASK-0022 (ADR-023 decision 9): reset and replay over the history window."""

    def test_the_window_is_the_smaller_of_the_history_days_and_the_recorder_retention(
        self,
    ) -> None:
        window = _history.efficiency_rebuild_window
        assert window({"max_history_days": 28}, 60) == timedelta(days=28)
        assert window({"max_history_days": 28}, 10) == timedelta(days=10)
        assert window({"max_history_days": 5}, 5) == timedelta(days=5)
        assert window({}, 60) == timedelta(days=28)  # the default of max_history_days
        assert window({}, 10) == timedelta(days=10)

    @pytest.mark.asyncio
    async def test_the_rebuild_equals_a_replay_from_empty_over_the_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whole, _standby, world = await _baseline(monkeypatch)
        rebuilt = await _history.async_rebuild_efficiency(object(), _opts(), LAST)
        assert rebuilt == whole  # incremental operation and a replay from empty agree
        window = _history.efficiency_rebuild_window(
            _opts(), _history._get_short_term_retention_days(None)
        )
        replay = await _history.async_update_efficiency(
            object(), _opts(), RunState(), LAST, window=window
        )
        assert rebuilt == replay

    @pytest.mark.asyncio
    async def test_the_rebuild_replays_from_empty_over_the_history_window_not_the_catch_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # the simulation is shorter than either window, so the result alone cannot tell
        # a 5-day rebuild from a 3-day catch-up: look at what the replay is asked for
        _state, _standby, _world_ = await _baseline(monkeypatch)
        asked: list[tuple[Any, timedelta]] = []
        real = _history.async_update_efficiency

        async def spy(
            hass: Any, options: Any, state: Any, now: datetime, window: timedelta | None = None
        ) -> Any:
            asked.append((state, window))  # type: ignore[arg-type]
            return await real(
                hass, options, state, now, **({} if window is None else {"window": window})
            )

        monkeypatch.setattr(_history, "async_update_efficiency", spy)
        await _history.async_rebuild_efficiency(object(), _opts(), LAST)
        assert asked == [(RunState(), timedelta(days=5))]  # empty, and max_history_days = 5

    @pytest.mark.asyncio
    async def test_the_rebuild_forgets_what_the_machine_had_counted_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # a reconfigured system: the old state describes other counters; it is not an input
        whole, _standby, _world_ = await _baseline(monkeypatch)
        stale = RunState(
            charge=_calc["EffEstimate"](
                ratio=_calc["DecayedRatio"](num=1.0, den=2.0), counted_kwh=500.0, counted_runs=77
            ),
            last_slot=T0 + timedelta(days=30),
        )
        assert stale != whole
        assert await _history.async_rebuild_efficiency(object(), _opts(), LAST) == whole

    @pytest.mark.asyncio
    async def test_the_slot_job_continues_from_the_rebuild_and_agrees_with_a_new_replay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state, _standby, world = await _baseline(monkeypatch)
        earlier = LAST - timedelta(hours=1)
        rebuilt = await _history.async_rebuild_efficiency(object(), _opts(), earlier)
        assert rebuilt.last_slot is not None
        for step in (5, 35, 65):  # the next jobs
            later = earlier + timedelta(minutes=step)
            rebuilt = await _history.async_update_efficiency(object(), _opts(), rebuilt, later)
            assert rebuilt == await _history.async_rebuild_efficiency(object(), _opts(), later)
        # nothing the replay had counted is counted again
        again = await _history.async_update_efficiency(object(), _opts(), rebuilt, later)
        assert again == rebuilt

    @pytest.mark.asyncio
    async def test_slots_without_a_standby_statistic_do_not_count_and_do_not_fail_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whole, _standby, world = await _baseline(monkeypatch)
        for slot in list(world.store[STANDBY_ID]):
            if slot < T0 + timedelta(hours=2):
                del world.store[STANDBY_ID][slot]
        rebuilt = await _history.async_rebuild_efficiency(object(), _opts(), LAST)
        assert rebuilt.charge.counted_kwh < whole.charge.counted_kwh
        assert rebuilt.discharge == whole.discharge  # the discharge was not touched
        for slot in list(world.store[STANDBY_ID]):
            del world.store[STANDBY_ID][slot]
        none = await _history.async_rebuild_efficiency(object(), _opts(), LAST)
        assert none.charge.counted_kwh == 0 and none.discharge.counted_kwh == 0
        assert none.last_slot == whole.last_slot  # it still read every slot

    @pytest.mark.asyncio
    async def test_it_reads_only_inside_the_window_and_never_writes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state, _standby, world = await _baseline(monkeypatch)
        writes = len(world.write_calls)
        world.stats_calls.clear()
        later = LAST + timedelta(days=40)  # far later: the window decides what is read
        await _history.async_rebuild_efficiency(object(), _opts(), later)
        window = _history.efficiency_rebuild_window(
            _opts(), _history._get_short_term_retention_days(None)
        )
        assert window == timedelta(days=5)
        floor = later - timedelta(minutes=125) - window
        assert world.stats_calls and all(start >= floor - SLOT for start, _end in world.stats_calls)
        assert len(world.write_calls) == writes

    def test_the_hook_is_at_the_caller_not_inside_the_history_recalculation(self) -> None:
        source = inspect.getsource(_history.async_recalculate_history)
        assert "efficiency" not in source.lower()
