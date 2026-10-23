"""
Phase timing of recalculations (TASK-0010, ADR-022 Decision 1; CAP-6).

A history recalculation logs one INFO record with the wall time of its fetch,
compute and write phases, the sizes behind them, and — measured separately,
by one extra pass that is *not* part of ``compute`` — what parsing the raw
states costs. A recent recalculation logs the same at DEBUG, without the
parse probe.

The real history.py runs against the in-memory recorder world of
``test_history_recent_equivalence``. The clock is a fake that advances only at
the points the tests instrument, so every figure in the record is exact and
any time nobody accounted for must show up as ``other``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_history_recent_equivalence import (
    _energy_world,
    _FakeRecorder,
    _FrozenDatetime,
    _history,
    _install,
    _options,
    _run_timer_cycles,
    _SensorStateClass,
    _ts,
    _World,
)

PV = "sensor.pv"  # energy-family input
OUT = "sensor.out"  # energy-family output
PW = "sensor.pv_w"  # power-family input (W)

JOB_S = 2.0  # every recorder executor job
TRAP_S = 7.0  # every trapezoidal_slot_contributions call
DIST_S = 0.5  # every distribute_loss call
WRITE_S = 3.0  # every statistics write
PARSE_S = 0.5  # every _parse_energy_state call
EPOCH_S = 0.25  # every datetime -> epoch conversion

REAL_TRAPEZOID = _history.trapezoidal_slot_contributions
REAL_DISTRIBUTE = _history.distribute_loss
REAL_PARSE = _history._parse_energy_state
NOW = _ts(12, 0, day=10)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _readings(first_value: float, step: float) -> list[tuple[datetime, str]]:
    start = _ts(10, 0, day=10)
    return [(start + timedelta(minutes=10 * i), f"{first_value + step * i:.2f}") for i in range(12)]


def _world() -> _World:
    """Two energy-family sensors and one power-family input with a 2-slot gap."""
    world = _energy_world(**{PV: _readings(100.0, 0.5), OUT: _readings(50.0, 0.3)})
    slot = timedelta(minutes=5)
    first = _ts(10, 0, day=10)
    world.power_means[PW] = {first + slot * i: 300.0 + 10 * i for i in range(22) if i not in (7, 8)}
    world.units[PW] = "W"
    world.state_classes[PW] = _SensorStateClass.MEASUREMENT
    return world


def _opts() -> dict[str, Any]:
    return _options([PV, PW], [OUT])


class _Counts:
    def __init__(self) -> None:
        self.distribute = 0
        self.parse = 0


def _rig(monkeypatch: pytest.MonkeyPatch, world: _World, clock: _Clock) -> _Counts:
    """Install the recorder world, then make every instrumented point cost time."""
    _install(monkeypatch, world)
    counts = _Counts()

    class TimedRecorder(_FakeRecorder):
        async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
            clock.advance(JOB_S)
            return fn(*args)

    real_write = _history._write_recorder_statistics

    async def timed_write(*args: Any, **kwargs: Any) -> int:
        clock.advance(WRITE_S)
        written: int = await real_write(*args, **kwargs)
        return written

    def timed_trapezoid(*args: Any, **kwargs: Any) -> Any:
        clock.advance(TRAP_S)
        return REAL_TRAPEZOID(*args, **kwargs)

    def timed_distribute(*args: Any, **kwargs: Any) -> Any:
        counts.distribute += 1
        clock.advance(DIST_S)
        return REAL_DISTRIBUTE(*args, **kwargs)

    def timed_parse(state: str) -> float | None:
        counts.parse += 1
        clock.advance(PARSE_S)
        parsed: float | None = REAL_PARSE(state)
        return parsed

    def timed_epoch(_self: datetime) -> float:
        clock.advance(EPOCH_S)
        return 0.0

    monkeypatch.setattr(_history, "_clock", clock)
    monkeypatch.setattr(_history, "get_recorder", lambda _hass: TimedRecorder())
    monkeypatch.setattr(_history, "_write_recorder_statistics", timed_write)
    monkeypatch.setattr(_history, "trapezoidal_slot_contributions", timed_trapezoid)
    monkeypatch.setattr(_history, "distribute_loss", timed_distribute)
    monkeypatch.setattr(_history, "_parse_energy_state", timed_parse)
    monkeypatch.setattr(_FrozenDatetime, "timestamp", timed_epoch)
    return counts


async def _history_recalc(
    world: _World, options: dict[str, Any] | None = None
) -> tuple[int, Any, Any, Any]:
    _FrozenDatetime.frozen = NOW
    result: tuple[int, Any, Any, Any] = await _history.async_recalculate_history(
        object(), options or _opts()
    )
    return result


def _timing_records(caplog: pytest.LogCaptureFixture, kind: str) -> list[logging.LogRecord]:
    marker = f"recalculation timing ({kind})"
    return [r for r in caplog.records if marker in r.getMessage()]


def _figure(message: str, label: str) -> float:
    match = re.search(rf"{re.escape(label)} (\d+(?:\.\d+)?)", message)
    assert match, f"{label!r} not in {message!r}"
    return float(match.group(1))


def _expected_slots(world: _World) -> int:
    start, end = NOW - timedelta(days=5), NOW
    slots: set[datetime] = set()
    for eid in (PV, OUT):
        contributions = REAL_TRAPEZOID(world.energy[eid], slot_minutes=5, now=end)
        slots |= {s for s in contributions if start <= s < end}
    slot = timedelta(minutes=5)
    means = {s: v for s, v in world.power_means[PW].items() if start <= s and s + slot <= end}
    slots |= set(means) | set(_history.interpolate_slot_gaps(means, slot_minutes=5))
    return len(slots)


class TestPhaseTimings:
    """The stopwatch itself."""

    def test_time_is_attributed_to_the_current_phase_and_the_rest_is_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(_history, "_clock", clock)
        timings = _history._PhaseTimings("history", logging.INFO)
        clock.advance(1.0)  # before any phase: other
        timings.enter("compute")
        clock.advance(4.0)
        timings.enter("write")
        clock.advance(2.0)
        timings.enter(None)
        clock.advance(0.5)  # after a phase: other
        assert timings.seconds["compute"] == pytest.approx(4.0)
        assert timings.seconds["write"] == pytest.approx(2.0)
        assert timings.seconds["fetch"] == 0.0
        assert timings.total() == pytest.approx(7.5)
        assert timings.other() == pytest.approx(1.5)

    async def test_a_recorder_job_counts_as_fetch_and_hands_the_phase_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(_history, "_clock", clock)

        class Recorder:
            async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
                clock.advance(3.0)
                return fn(*args)

        timings = _history._PhaseTimings("history", logging.INFO)
        timings.enter("compute")
        clock.advance(1.0)
        result = await timings.recorder_job(Recorder(), lambda a, b: a + b, 2, 3)
        clock.advance(1.0)
        timings.enter(None)
        assert result == 5
        assert timings.queries == 1
        assert timings.seconds["fetch"] == pytest.approx(3.0)
        assert timings.seconds["compute"] == pytest.approx(2.0)

    def test_a_disabled_stopwatch_records_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _Clock()
        monkeypatch.setattr(_history, "_clock", clock)
        timings = _history._PhaseTimings("history", logging.INFO, enabled=False)
        timings.enter("compute")
        clock.advance(5.0)
        timings.enter(None)
        assert timings.seconds == {"fetch": 0.0, "compute": 0.0, "write": 0.0, "probe": 0.0}
        assert timings.queries == 0


class TestHistoryRecalculationRecord:
    async def test_one_info_record_names_the_phases_and_sizes(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        counts = _rig(monkeypatch, world, clock)
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            written, _from, _touched, _last = await _history_recalc(world)

        records = _timing_records(caplog, "history")
        assert len(records) == 1
        assert records[0].levelno == logging.INFO
        msg = records[0].getMessage()

        jobs = 1 + 2 + 1  # statistics units, one raw fetch per energy sensor, power statistics
        compute = TRAP_S * 2 + DIST_S * counts.distribute
        write = WRITE_S * len(world.write_calls)
        assert _figure(msg, "fetch") == pytest.approx(JOB_S * jobs)
        assert _figure(msg, "compute") == pytest.approx(
            compute + PARSE_S * (counts.parse - 24)  # see the probe test: 24 states, one pass
        )
        assert _figure(msg, "write") == pytest.approx(write)
        assert _figure(msg, "recorder queries") == jobs
        assert _figure(msg, "raw states") == 24
        assert _figure(msg, "slots") == _expected_slots(world)
        assert _figure(msg, "rows written") == written
        assert written > 0

    async def test_the_phases_add_up_to_the_total_and_unaccounted_time_is_other(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        _rig(monkeypatch, world, clock)
        real_drop = _history._drop_open_slot

        def slow_drop(series: Any, when: datetime) -> Any:  # runs outside every phase
            clock.advance(1.5)
            return real_drop(series, when)

        monkeypatch.setattr(_history, "_drop_open_slot", slow_drop)
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history_recalc(world)
        msg = _timing_records(caplog, "history")[0].getMessage()

        assert _figure(msg, "other") == pytest.approx(1.5 * 3)
        parts = [_figure(msg, "= fetch")]
        others = ("compute", "write", "parse probe", "other")
        parts += [_figure(msg, f"+ {name}") for name in others]
        assert sum(parts) == pytest.approx(_figure(msg, "total"))

    async def test_an_anchor_lookback_is_a_query_too(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        # first fetched entry invalid, a valid one later -> offline-anchor lookback
        world.energy[PV] = [(_ts(10, 0, day=10), "unavailable"), *world.energy[PV][1:]]
        _rig(monkeypatch, world, clock)
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history_recalc(world)
        msg = _timing_records(caplog, "history")[0].getMessage()
        assert _figure(msg, "recorder queries") == 1 + 2 + 1 + 1
        assert world.anchor_calls == [PV]

    async def test_a_recalculation_that_finds_nothing_still_logs_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = _Clock()
        world = _energy_world(**{PV: [], OUT: []})
        _rig(monkeypatch, world, clock)
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            written, _from, _touched, _last = await _history_recalc(world, _options([PV], [OUT]))
        records = _timing_records(caplog, "history")
        assert written == 0
        assert len(records) == 1
        assert _figure(records[0].getMessage(), "rows written") == 0


class TestParseProbe:
    async def test_parsing_is_measured_separately_and_is_not_part_of_compute(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        counts = _rig(monkeypatch, world, clock)
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history_recalc(world)
        msg = _timing_records(caplog, "history")[0].getMessage()

        states = 24  # raw states over both energy-family sensors
        assert "parse probe (one extra pass" in msg
        assert _figure(msg, "values") == pytest.approx(PARSE_S * states)
        assert _figure(msg, "timestamps") == pytest.approx(EPOCH_S * states)
        assert _figure(msg, "+ parse probe") == pytest.approx((PARSE_S + EPOCH_S) * states)
        # every _parse_energy_state call that is not the probe's is ordinary compute
        outside_probe = counts.parse - states
        assert outside_probe >= 0
        assert _figure(msg, "compute") == pytest.approx(
            TRAP_S * 2 + DIST_S * counts.distribute + PARSE_S * outside_probe
        )

    async def test_a_failing_probe_never_breaks_the_recalculation(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        _rig(monkeypatch, world, clock)

        def broken_epoch(_self: datetime) -> float:
            raise OverflowError("timestamp out of range")

        monkeypatch.setattr(_FrozenDatetime, "timestamp", broken_epoch)
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            written, _from, _touched, _last = await _history_recalc(world)
        records = _timing_records(caplog, "history")
        assert written > 0
        assert len(records) == 1
        msg = records[0].getMessage()
        assert _figure(msg, "values") == pytest.approx(PARSE_S * 24)  # measured before it failed
        assert _figure(msg, "timestamps") == 0.0

    async def test_the_probe_does_not_change_what_is_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with_probe, without_probe = _world(), _world()
        _install(monkeypatch, with_probe)
        _FrozenDatetime.frozen = NOW
        await _history.async_recalculate_history(object(), _opts())

        monkeypatch.undo()
        _install(monkeypatch, without_probe)
        monkeypatch.setattr(_history, "_PhaseTimings", _no_probe_timings())
        await _history.async_recalculate_history(object(), _opts())

        assert with_probe.write_calls == without_probe.write_calls
        assert with_probe.store == without_probe.store


def _no_probe_timings() -> Any:
    real = _history._PhaseTimings

    def factory(kind: str, level: int, *, parse_probe: bool = False, enabled: bool = True) -> Any:
        return real(kind, level, parse_probe=False, enabled=enabled)

    return factory


class TestRecentRecalculation:
    async def test_it_logs_at_debug_only_and_without_the_probe(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        _rig(monkeypatch, world, clock)
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _run_timer_cycles(world, _opts(), _ts(11, 0, day=10), _ts(11, 0, day=10))
        assert _timing_records(caplog, "recent") == []

        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger=_history.__name__):
            await _run_timer_cycles(world, _opts(), _ts(11, 5, day=10), _ts(11, 5, day=10))
        records = _timing_records(caplog, "recent")
        assert len(records) == 1
        assert records[0].levelno == logging.DEBUG
        assert "parse probe" not in records[0].getMessage()
        assert _figure(records[0].getMessage(), "recorder queries") >= 3


class TestJumpExtendedRefetch:
    async def test_the_extended_refetch_counts_as_a_second_raw_query_and_as_fetch(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        readings = [(_ts(7, 30), "5.00"), (_ts(10, 50), "5.01")]

        # Idle: no jump inside RECENT_RECALC_WINDOW -> units + one raw read per sensor.
        clock, idle = _Clock(), _energy_world(**{PV: readings})
        _rig(monkeypatch, idle, clock)
        with caplog.at_level(logging.DEBUG, logger=_history.__name__):
            await _history.async_recalculate_recent(object(), _options([PV]), _ts(12, 0, 5))
        idle_msg = _timing_records(caplog, "recent")[0].getMessage()
        assert _figure(idle_msg, "recorder queries") == 2
        assert _figure(idle_msg, "fetch") == pytest.approx(2 * JOB_S)

        # Jump at 10:50: the range is extended, the raw history is read a second
        # time, and the power-family statistics are read once from the new start.
        caplog.clear()
        clock, jumpy = _Clock(), _energy_world(**{PV: readings})
        jumpy.units[PW] = "W"
        jumpy.state_classes[PW] = _SensorStateClass.MEASUREMENT
        jumpy.power_means[PW] = {_ts(8, 0) + timedelta(minutes=5 * i): 50.0 for i in range(60)}
        _rig(monkeypatch, jumpy, clock)
        with caplog.at_level(logging.DEBUG, logger=_history.__name__):
            await _history.async_recalculate_recent(object(), _options([PV, PW]), _ts(10, 55, 5))
        jump_msg = _timing_records(caplog, "recent")[0].getMessage()
        assert len(jumpy.raw_calls) == 2  # base fetch + extended refetch
        assert _figure(jump_msg, "recorder queries") == 1 + 2 + 1
        assert _figure(jump_msg, "fetch") == pytest.approx(4 * JOB_S)


class TestNoBehaviourChange:
    async def test_compute_without_a_stopwatch_equals_compute_with_one(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        world = _world()
        _install(monkeypatch, world)
        start, end = NOW - timedelta(days=5), NOW

        with caplog.at_level(logging.DEBUG, logger=_history.__name__):
            plain = await _history._compute_effective_slots(object(), _opts(), start, end)
        assert _timing_records(caplog, "history") == []
        assert _history._NO_TIMINGS.queries == 0
        assert _history._NO_TIMINGS.seconds["compute"] == 0.0

        timed = await _history._compute_effective_slots(
            object(), _opts(), start, end, phases=_history._PhaseTimings("history", logging.INFO)
        )
        assert timed == plain
