"""TASK-0005 Stage 2 — the standby machine fed from the recorder (ADR-018 §3, §5, §6).

Runs the *real* ``history.async_recalculate_recent`` and
``history.async_update_standby`` cycle by cycle against the faked recorder of
``test_history_recent_equivalence`` (raw states, statistics and writes in
memory). The machine reads the statistics Effy itself wrote, so these tests prove
the whole chain: counter jumps → peaks, written ``effy_*_power`` statistics →
activity, replay after a restart, bounded reads, and that the machine only reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_history_recent_equivalence import (
    _energy_world,
    _history,
    _install,
    _options,
    _World,
)
from tests.test_standby import T0, _simulate_counter

SLOT = timedelta(minutes=5)
BMS = "sensor.bms_discharge"
PV = "sensor.pv"
OUT = "sensor.grid_out"

# The pure functions live in the private calculation module history.py loaded.
_calc = SimpleNamespace(**_history.drain_feed.__globals__)
DrainState = _history.DrainState


def _world(
    *,
    discharge: list[tuple[datetime, str]],
    pv: list[tuple[datetime, str]] | None = None,
    out: list[tuple[datetime, str]] | None = None,
) -> _World:
    return _energy_world(
        **{
            BMS: discharge,
            PV: pv or [(T0, "100.00")],
            OUT: out or [(T0, "50.00")],
        }
    )


def _opts(*, bms: bool = True) -> dict[str, Any]:
    return _options([PV], [OUT], [BMS] if bms else None)


def _fetch_statistics_from_store(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the statistics Effy wrote (``world.store``) to the activity read."""

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
    _fetch_statistics_from_store(world, monkeypatch)


async def _cycles(
    world: _World,
    options: dict[str, Any],
    first: datetime,
    last: datetime,
    *,
    state: Any = None,
    standby: bool = True,
) -> Any:
    """Recent recalculation then standby update every 5 minutes at HH:MM:05, as
    the coordinator's slot job does, under one reading cache."""
    cache: dict[str, tuple[datetime, str]] = {}
    previous: datetime | None = None
    state = DrainState() if state is None else state
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
        if standby:
            state = await _history.async_update_standby(object(), options, state, now)
        now += SLOT
    return state


FIRST = T0 + timedelta(minutes=10, seconds=5)
LAST = T0 + timedelta(hours=8, seconds=5)


class TestEndToEnd:
    async def test_a_quiet_night_reads_the_standby_power(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=8)
        world = _world(discharge=_simulate_counter([(T0, end, 12)], T0, end))
        _rig(monkeypatch, world)
        state = await _cycles(world, _opts(), FIRST, LAST)
        assert state.estimate == pytest.approx(12.0)
        assert state.count >= 4
        assert state.last_source == BMS

    async def test_a_load_that_a_counter_shows_late_does_not_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=8)
        load = (T0 + timedelta(hours=5), T0 + timedelta(hours=5, minutes=30), 20)
        world = _world(
            discharge=_simulate_counter([(T0, end, 12), load], T0, end),
            out=_simulate_counter([load], T0, end, base_kwh=50.00),
        )
        _rig(monkeypatch, world)
        state = await _cycles(world, _opts(), FIRST, LAST)
        assert state.estimate == pytest.approx(12.0)

    async def test_pv_alone_is_never_a_drain_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        end = T0 + timedelta(hours=8)
        world = _world(
            discharge=[(T0, "12.30")],
            pv=_simulate_counter([(T0, end, 12)], T0, end, base_kwh=100.00),
        )
        _rig(monkeypatch, world)
        state = await _cycles(world, _opts(), FIRST, LAST)
        assert state.estimate is None and state.count == 0

    async def test_without_a_bms_input_nothing_is_read_and_nothing_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=2)
        world = _world(discharge=_simulate_counter([(T0, end, 12)], T0, end))
        _rig(monkeypatch, world)
        before = len(world.raw_calls)
        state = await _history.async_update_standby(
            object(), _opts(bms=False), DrainState(), end + timedelta(seconds=5)
        )
        assert state.estimate is None
        assert len(world.raw_calls) == before
        assert world.stats_calls == []  # nothing is read without a BMS input

    async def test_the_machine_only_reads_every_statistic_is_identical_with_and_without_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=4)
        readings = _simulate_counter([(T0, end, 12)], T0, end)
        stores: list[dict[str, dict[datetime, float]]] = []
        for standby in (False, True):
            world = _world(discharge=list(readings))
            _rig(monkeypatch, world)
            await _cycles(
                world, _opts(), FIRST, T0 + timedelta(hours=4, seconds=5), standby=standby
            )
            stores.append({eid: dict(slots) for eid, slots in world.store.items()})
        assert stores[0] == stores[1]
        assert stores[0]  # something was written at all


class TestBoundedReads:
    async def test_a_cycle_reads_only_from_the_armed_peak_not_a_day_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=8)
        world = _world(discharge=_simulate_counter([(T0, end, 12)], T0, end))
        _rig(monkeypatch, world)
        state = await _cycles(world, _opts(), FIRST, T0 + timedelta(hours=6, seconds=5))
        world.raw_calls.clear()
        world.stats_calls.clear()
        now = T0 + timedelta(hours=6, minutes=5, seconds=5)
        await _history.async_recalculate_recent(
            object(), _opts(), now, energy_reading_cache={}, trigger_since=now - SLOT
        )
        world.raw_calls.clear()
        world.stats_calls.clear()
        await _history.async_update_standby(object(), _opts(), state, now)
        bms_reads = [(s, e) for eid, s, e in world.raw_calls if eid == BMS]
        assert len(bms_reads) == 1
        assert now - bms_reads[0][0] < timedelta(hours=3)  # from the armed peak, not 24 h back
        assert len(world.stats_calls) == 1  # one statistics read for all sensors
        assert now - world.stats_calls[0][0] < timedelta(hours=4)


class TestReplay:
    async def test_a_restart_replays_to_the_same_state_as_uninterrupted_operation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=10)
        load = (T0 + timedelta(hours=6), T0 + timedelta(hours=6, minutes=30), 20)
        readings = _simulate_counter([(T0, end, 12), load], T0, end)
        out = _simulate_counter([load], T0, end, base_kwh=50.00)
        last = T0 + timedelta(hours=10, seconds=5)
        cut = T0 + timedelta(hours=7, minutes=20, seconds=5)

        world = _world(discharge=list(readings), out=list(out))
        _rig(monkeypatch, world)
        uninterrupted = await _cycles(world, _opts(), FIRST, last)

        world = _world(discharge=list(readings), out=list(out))
        _rig(monkeypatch, world)
        at_cut = await _cycles(world, _opts(), FIRST, cut)
        assert at_cut.estimate is not None
        restored = _calc.drain_state_from_restore(_calc.drain_restore_data(at_cut))
        assert restored is not None
        # the restart: the machine is gone, the entity brought the aggregates back
        now = cut + SLOT
        cache: dict[str, tuple[datetime, str]] = {}
        previous: datetime | None = None
        state = restored
        first_after = now
        # the slot job never ran during the downtime: catch up at once, then continue
        while now <= last:
            await _history.async_recalculate_recent(
                object(),
                _opts(),
                now,
                energy_reading_cache=cache,
                trigger_since=_history.recent_trigger_since(previous, now),
            )
            previous = now
            if now == first_after or now == last:
                state = await _history.async_update_standby(object(), _opts(), state, now)
            now += SLOT

        assert state.estimate == pytest.approx(uninterrupted.estimate)
        assert state.count == uninterrupted.count
        assert state.last_end == uninterrupted.last_end
        assert state.last_source == uninterrupted.last_source

    async def test_a_replay_never_reaches_further_back_than_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=2)
        world = _world(discharge=_simulate_counter([(T0, end, 12)], T0, end))
        _rig(monkeypatch, world)
        long_ago = DrainState(estimate=12.0, count=3, suppress_until=T0 - timedelta(days=30))
        now = T0 + timedelta(days=10, seconds=5)
        await _history.async_update_standby(object(), _opts(), long_ago, now)
        starts = [s for _eid, s, _e in world.raw_calls]
        assert starts and min(starts) >= now - _history.STANDBY_REPLAY_MAX - timedelta(minutes=10)
        assert min(s for s, _e in world.stats_calls) >= now - _history.STANDBY_REPLAY_MAX - SLOT * 3

    async def test_a_state_that_is_up_to_date_does_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=3)
        world = _world(discharge=_simulate_counter([(T0, end, 12)], T0, end))
        _rig(monkeypatch, world)
        state = await _cycles(world, _opts(), FIRST, T0 + timedelta(hours=3, seconds=5))
        world.raw_calls.clear()
        again = await _history.async_update_standby(
            object(), _opts(), state, T0 + timedelta(hours=3, seconds=40)
        )
        assert again == state
        assert world.raw_calls == []
