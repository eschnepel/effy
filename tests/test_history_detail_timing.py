"""
Sub-process timing of the history recalculation (TASK-0010 patch 1, ADR-022 D1).

TASK-0010 times three phases. The first run on the Raspberry Pi (228 s) showed
that this is not enough to act on, so the history recalculation's record now
carries a *detail block*: the recorder jobs one by one (with the time spent in
the executor apart from the time queued for it), the compute stages, the three
statistics writes (preparing apart from awaiting the recorder), CPU seconds
against wall seconds, the longest stretch without an ``await``, and the
environment that produced the numbers.

Same method as ``test_history_phase_timing``: the real history.py against the
in-memory recorder world, with a fake wall clock and a fake CPU clock that
advance only where a test says so, so every figure is exact. Nothing here is
allowed to change what is calculated or written; that is the last class.
"""

from __future__ import annotations

import copy
import logging
import re
import sys
from collections.abc import Callable
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.test_history_phase_timing import (
    DIST_S,
    JOB_S,
    NOW,
    OUT,
    PARSE_S,
    PV,
    PW,
    REAL_TRAPEZOID,
    TRAP_S,
    _Clock,
    _figure,
    _opts,
    _rig,
    _timing_records,
    _world,
)
from tests.test_history_recent_equivalence import (
    _FakeRecorder,
    _FrozenDatetime,
    _history,
    _install,
)

REAL_WRITE = _history._write_recorder_statistics  # captured before any test patches it

RUN_S = 0.75  # time inside the executor for one raw-state fetch
INTERP_S = 1.5
READINGS_S = 0.125  # per _readings_for_slot call
CONVERT_S = 0.25  # per effective_in_original_unit call
IMPORT_S = 0.5  # per async_import_statistics call
BLOCK_S = 4.0  # per async_block_till_done


def _detail(msg: str) -> list[str]:
    """The lines after the TASK-0010 line, stripped."""
    return [line.strip() for line in msg.splitlines()[1:]]


def _line(msg: str, prefix: str) -> str:
    found = [line for line in _detail(msg) if line.startswith(prefix)]
    assert len(found) == 1, f"{prefix!r}: {found!r} in {msg!r}"
    return found[0]


def _seconds(text: str, label: str) -> float:
    match = re.search(rf"{re.escape(label)} (\d+(?:\.\d+)?) s", text)
    assert match, f"{label!r} not in {text!r}"
    return float(match.group(1))


async def _detail_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, **kwargs: Any
) -> tuple[str, _Clock, Any, Any]:
    """Rig the clock, run one history recalculation, return its single record."""
    clock, world = _Clock(), _world()
    counts = _rig(monkeypatch, world, clock)
    _FrozenDatetime.frozen = NOW
    with caplog.at_level(logging.INFO, logger=_history.__name__):
        await _history.async_recalculate_history(object(), _opts(), **kwargs)
    records = _timing_records(caplog, "history")
    assert len(records) == 1
    return records[0].getMessage(), clock, counts, world


class TestRecordShape:
    async def test_the_detail_follows_the_unchanged_first_line_in_the_same_record(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, _clock, _counts, _world_ = await _detail_message(monkeypatch, caplog)
        lines = msg.splitlines()
        assert lines[0].startswith("Effy recalculation timing (history): total ")
        assert "= fetch " in lines[0] and "rows written" in lines[0]
        assert len(lines) > 5  # the detail block
        assert all(line.startswith("  ") for line in lines[1:])

    async def test_a_recent_recalculation_stays_a_one_line_debug_record(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        _rig(monkeypatch, world, clock)
        _FrozenDatetime.frozen = NOW
        with caplog.at_level(logging.DEBUG, logger=_history.__name__):
            await _history.async_recalculate_recent(object(), _opts(), NOW)
        records = _timing_records(caplog, "recent")
        assert len(records) == 1
        assert records[0].levelno == logging.DEBUG
        assert "\n" not in records[0].getMessage()
        assert "detail v2" not in records[0].getMessage()


class TestEnvironment:
    async def test_it_names_the_build_the_sources_and_the_machine(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        home = ModuleType("homeassistant")
        const = ModuleType("homeassistant.const")
        setattr(const, "__version__", "2026.3.4")
        monkeypatch.setitem(sys.modules, "homeassistant", home)
        monkeypatch.setitem(sys.modules, "homeassistant.const", const)

        class Recorder(_FakeRecorder):
            keep_days = 60
            dialect_name = SimpleNamespace(value="sqlite")

        clock, world = _Clock(), _world()
        _rig(monkeypatch, world, clock)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: Recorder())
        _FrozenDatetime.frozen = NOW
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history.async_recalculate_history(object(), _opts())
        text = _line(_timing_records(caplog, "history")[0].getMessage(), "detail v2")

        assert "HA 2026.3.4" in text
        assert re.search(r"Python \d+\.\d+\.\d+ \S+", text)
        assert re.search(r"\d+ CPUs", text)
        assert "recorder sqlite" in text
        assert "keep_days 60" in text
        assert "history days 5" in text
        assert "energy sources 2" in text  # PV, OUT
        assert "power sources 1" in text  # PW

    async def test_what_cannot_be_read_is_unknown_and_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(_hass: Any) -> Any:
            raise RuntimeError("no recorder")

        monkeypatch.setattr(_history, "get_recorder", broken)
        monkeypatch.delitem(sys.modules, "homeassistant.const", raising=False)
        text = _history._environment_summary(object(), 28)
        assert "unknown" in text
        assert "history days 28" in text

    async def test_even_an_unexpected_failure_leaves_a_line_and_raises_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken() -> str:
            raise OSError("uname failed")

        monkeypatch.setattr(_history.platform, "machine", broken)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: _FakeRecorder())
        text = _history._environment_summary(object(), 7)
        assert text == "detail v2: environment unknown, history days 7"

    async def test_a_recorder_without_the_attributes_is_unknown_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: _FakeRecorder())
        text = _history._environment_summary(object(), 5)
        assert "recorder unknown" in text
        assert "keep_days unknown" in text


class TestFetchJobs:
    async def test_every_job_is_listed_with_its_run_its_wait_and_its_rows(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        _rig(monkeypatch, world, clock)
        fetch_raw = _history._fetch_raw_energy_states

        def slow_raw(*args: Any) -> Any:
            clock.advance(RUN_S)  # inside the executor job
            return fetch_raw(*args)

        monkeypatch.setattr(_history, "_fetch_raw_energy_states", slow_raw)
        _FrozenDatetime.frozen = NOW
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history.async_recalculate_history(object(), _opts())
        msg = _timing_records(caplog, "history")[0].getMessage()

        for eid in (PV, OUT):
            job = _line(msg, f"raw states {eid}:")
            assert "calls 1" in job
            assert _seconds(job, "wall") == pytest.approx(JOB_S + RUN_S)
            assert _seconds(job, "run") == pytest.approx(RUN_S)
            assert _seconds(job, "wait") == pytest.approx(JOB_S)
            assert "rows 12" in job
        units = _line(msg, "units:")
        assert "calls 1" in units and "rows 3" in units  # the three configured sensors
        assert _seconds(units, "run") == 0.0 and _seconds(units, "wait") == pytest.approx(JOB_S)
        assert "calls 1" in _line(msg, "power statistics:")

    async def test_the_walls_of_the_jobs_add_up_to_the_fetch_phase(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, _clock_, _counts, _world_ = await _detail_message(monkeypatch, caplog)
        walls = [
            _seconds(line, "wall")
            for line in _detail(msg)
            if line.startswith(("units:", "raw states ", "anchor lookback ", "power statistics:"))
        ]
        assert len(walls) == 4
        assert sum(walls) == pytest.approx(_figure(msg.splitlines()[0], "fetch"))

    async def test_an_anchor_lookback_is_listed_and_counts_one_row_when_found(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        world.energy[PV] = [(world.energy[PV][0][0], "unavailable"), *world.energy[PV][1:]]
        _rig(monkeypatch, world, clock)
        _FrozenDatetime.frozen = NOW
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history.async_recalculate_history(object(), _opts())
        msg = _timing_records(caplog, "history")[0].getMessage()
        anchor = _line(msg, f"anchor lookback {PV}:")
        assert "calls 1" in anchor
        assert "rows 0" in anchor  # nothing valid before the fetched range in this world


class TestRowCount:
    @pytest.mark.parametrize(
        ("result", "rows"),
        [
            ([1, 2, 3], 3),
            ({"a": [1, 2], "b": [3]}, 3),  # statistics: rows per entity
            ({"a": "kWh", "b": "W"}, 2),  # units: one per entity
            (None, 0),  # an anchor lookback that found nothing
            ((object(), "1.0"), 1),  # an anchor lookback that found one
            (42, 1),
        ],
    )
    def test_whatever_a_job_returns_it_has_a_row_count(self, result: Any, rows: int) -> None:
        assert _history._rows_of(result) == rows

    async def test_a_job_with_an_odd_result_never_breaks_the_stopwatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_history, "_clock", _Clock())

        class Recorder:
            async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
                return fn(*args)

        timings = _history._PhaseTimings("history", logging.INFO)
        timings.detail_on("env")
        assert await timings.recorder_job(Recorder(), lambda: None, label="odd") is None
        assert await timings.recorder_job(Recorder(), lambda: 7, label="odd") == 7
        assert timings.queries == 2


class TestComputeStages:
    async def test_the_trapezoid_is_timed_per_source_with_states_in_and_slots_out(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, _clock_, _counts, world = await _detail_message(monkeypatch, caplog)
        for eid in (PV, OUT):
            stage = _line(msg, f"trapezoid {eid}:")
            assert _seconds(stage, "") == pytest.approx(TRAP_S)
            assert "12 states" in stage
            slots = len(REAL_TRAPEZOID(world.energy[eid], slot_minutes=5, now=NOW))
            assert f"-> {slots} slots" in stage

    async def test_interpolation_is_timed_per_power_input(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        _rig(monkeypatch, world, clock)
        real = _history.interpolate_slot_gaps

        def slow(*args: Any, **kwargs: Any) -> Any:
            clock.advance(INTERP_S)
            return real(*args, **kwargs)

        monkeypatch.setattr(_history, "interpolate_slot_gaps", slow)
        _FrozenDatetime.frozen = NOW
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history.async_recalculate_history(object(), _opts())
        stage = _line(_timing_records(caplog, "history")[0].getMessage(), f"interpolation {PW}:")
        assert _seconds(stage, "") == pytest.approx(INTERP_S)
        assert "20 slots" in stage  # 22 slots with a two-slot gap: 20 readings in

    async def test_the_cache_pass_of_the_fetch_helper_is_timed_when_a_cache_is_passed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, _clock_, _counts, _world_ = await _detail_message(
            monkeypatch, caplog, energy_reading_cache={}
        )
        stage = _line(msg, f"cache pass {PV}:")
        assert _seconds(stage, "") == pytest.approx(PARSE_S * 12)  # one parse per state
        assert "12 states" in stage

    async def test_without_a_cache_there_is_no_cache_pass(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, _clock_, _counts, _world_ = await _detail_message(monkeypatch, caplog)
        assert not [line for line in _detail(msg) if line.startswith("cache pass")]

    async def test_the_slot_loop_is_split_into_its_calls_and_the_rest(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world = _Clock(), _world()
        counts = _rig(monkeypatch, world, clock)
        calls = {"readings": 0, "convert": 0}
        readings, convert = _history._readings_for_slot, _history.effective_in_original_unit

        def timed_readings(*args: Any) -> Any:
            calls["readings"] += 1
            clock.advance(READINGS_S)
            return readings(*args)

        def timed_convert(*args: Any) -> Any:
            calls["convert"] += 1
            clock.advance(CONVERT_S)
            return convert(*args)

        monkeypatch.setattr(_history, "_readings_for_slot", timed_readings)
        monkeypatch.setattr(_history, "effective_in_original_unit", timed_convert)
        _FrozenDatetime.frozen = NOW
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history.async_recalculate_history(object(), _opts())
        msg = _timing_records(caplog, "history")[0].getMessage()

        parts = {
            "readings": READINGS_S * calls["readings"],
            "waterfall": DIST_S * counts.distribute,
            "conversion": CONVERT_S * calls["convert"],
        }
        loop = _line(msg, "slot loop:")
        assert _seconds(loop, "readings") == pytest.approx(parts["readings"], abs=0.006)
        assert _seconds(loop, "waterfall") == pytest.approx(parts["waterfall"], abs=0.006)
        assert _seconds(loop, "conversion") == pytest.approx(parts["conversion"], abs=0.006)
        assert _seconds(loop, "rest") == 0.0
        assert _seconds(loop, "slot loop:") == pytest.approx(sum(parts.values()), abs=0.02)
        assert f"for {_figure(msg.splitlines()[0], 'slots'):.0f} slots" in loop

    async def test_the_stages_are_carved_out_of_compute_and_never_added_to_it(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, _clock_, counts, _world_ = await _detail_message(monkeypatch, caplog)
        first = msg.splitlines()[0]
        # TASK-0010's own arithmetic, unchanged: trapezoids + waterfalls + the probe's
        # parse calls beyond its 24 states (see test_history_phase_timing).
        expected = TRAP_S * 2 + DIST_S * counts.distribute + PARSE_S * (counts.parse - 24)
        assert _figure(first, "compute") == pytest.approx(expected)


class _WriteRecorder(_FakeRecorder):
    """A recorder instance for the REAL write function: imports and the final wait cost time."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.imports: list[tuple[str, int]] = []

    async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
        self.clock.advance(JOB_S)
        return fn(*args)

    def async_import_statistics(self, _metadata: Any, stats: Any, table: Any) -> None:
        self.clock.advance(IMPORT_S)
        self.imports.append((table.__name__, len(stats)))

    async def async_block_till_done(self) -> None:
        self.clock.advance(BLOCK_S)


class TestWriteCalls:
    async def _run(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> tuple[str, _WriteRecorder]:
        clock, world = _Clock(), _world()
        _rig(monkeypatch, world, clock)
        recorder = _WriteRecorder(clock)
        monkeypatch.setattr(_history, "_write_recorder_statistics", REAL_WRITE)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
        _FrozenDatetime.frozen = NOW
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history.async_recalculate_history(object(), _opts())
        return _timing_records(caplog, "history")[0].getMessage(), recorder

    async def test_each_of_the_three_writes_has_its_rows_its_build_and_its_wait(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, recorder = await self._run(monkeypatch, caplog)
        # effective: PV and PW; derived power: PV and OUT; smoothed: PW. Two imports each
        # (short-term, hourly) -> 4, 4 and 2 imports in call order.
        segments = [recorder.imports[0:4], recorder.imports[4:8], recorder.imports[8:10]]
        for name, segment in zip(("effective", "derived power", "smoothed"), segments, strict=True):
            call = _line(msg, f"{name}:")
            short = sum(n for table, n in segment if table == "_StatisticsShortTerm")
            hourly = sum(n for table, n in segment if table == "_Statistics")
            assert f"{short} short-term" in call
            assert f"{hourly} hourly rows" in call
            assert _seconds(call, "build") == pytest.approx(IMPORT_S * len(segment))
            assert _seconds(call, "recorder") == pytest.approx(BLOCK_S)
        assert short > 0 and hourly > 0

    async def test_build_and_wait_of_the_three_calls_add_up_to_the_write_phase(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, _recorder = await self._run(monkeypatch, caplog)
        calls = [_line(msg, f"{name}:") for name in ("effective", "derived power", "smoothed")]
        total = sum(_seconds(c, "build") + _seconds(c, "recorder") for c in calls)
        assert total == pytest.approx(_figure(msg.splitlines()[0], "write"))
        assert total == pytest.approx(IMPORT_S * 10 + BLOCK_S * 3)

    async def test_the_real_writer_stops_reporting_once_the_write_is_over(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        await self._run(monkeypatch, caplog)
        assert _history._active_timings.get() is _history._NO_TIMINGS

    async def test_it_stops_reporting_when_the_recorder_fails_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Failing(_WriteRecorder):
            async def async_block_till_done(self) -> None:
                raise RuntimeError("recorder gone")

        monkeypatch.setattr(_history, "_clock", _Clock())
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: Failing(_Clock()))
        timings = _history._PhaseTimings("history", logging.INFO)
        timings.detail_on("env")
        series = {"sensor.effy_x": {"unit": "kW", "slot_values": [(NOW - timedelta(hours=2), 1.0)]}}
        with pytest.raises(RuntimeError, match="recorder gone"):
            with timings.write_call("effective"):
                await REAL_WRITE(object(), series, include_long_term=False)
        assert _history._active_timings.get() is _history._NO_TIMINGS
        assert timings.writes == {}  # a failed write is not reported as a finished one

    async def test_a_write_outside_a_history_recalculation_keeps_its_old_behaviour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _Clock()
        recorder = _WriteRecorder(clock)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
        series = {"sensor.effy_x": {"unit": "kW", "slot_values": [(NOW - timedelta(hours=2), 1.0)]}}
        written = await REAL_WRITE(object(), series, include_long_term=False)
        assert written == 1
        assert recorder.imports == [("_StatisticsShortTerm", 1)]


class TestCpuAgainstWall:
    async def test_cpu_seconds_are_shown_next_to_wall_seconds_per_phase(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, world, cpu = _Clock(), _world(), _Clock()
        _rig(monkeypatch, world, clock)
        monkeypatch.setattr(_history, "_cpu_clock", cpu)
        timed = _history.trapezoidal_slot_contributions

        def busy(*args: Any, **kwargs: Any) -> Any:
            cpu.advance(1.0)  # CPU work only in the trapezoid
            return timed(*args, **kwargs)

        monkeypatch.setattr(_history, "trapezoidal_slot_contributions", busy)
        _FrozenDatetime.frozen = NOW
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _history.async_recalculate_history(object(), _opts())
        msg = _timing_records(caplog, "history")[0].getMessage()
        text = _line(msg, "cpu / wall")

        assert "process-wide" in text
        compute = re.search(r"compute (\d+\.\d+) / (\d+\.\d+) s", text)
        fetch = re.search(r"fetch (\d+\.\d+) / (\d+\.\d+) s", text)
        assert compute and fetch
        assert float(compute.group(1)) == pytest.approx(2.0)  # two sources
        assert float(compute.group(2)) == pytest.approx(_figure(msg.splitlines()[0], "compute"))
        assert float(fetch.group(1)) == 0.0  # waiting costs no CPU in this world
        assert float(fetch.group(2)) == pytest.approx(_figure(msg.splitlines()[0], "fetch"))


class TestLongestStretch:
    async def test_it_is_the_longest_time_between_two_awaits_including_the_ends(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(_history, "_clock", clock)

        class Recorder:
            async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
                clock.advance(3.0)  # waiting, not holding the loop
                return fn(*args)

        timings = _history._PhaseTimings("history", logging.INFO)
        timings.detail_on("env")
        timings.enter("compute")
        clock.advance(1.0)  # stretch 1: from the start to the first await
        await timings.recorder_job(Recorder(), lambda: None)
        clock.advance(4.0)  # stretch 2: between the awaits
        await timings.recorder_job(Recorder(), lambda: None)
        clock.advance(2.0)  # stretch 3: after the last await, up to finish
        timings.finish(rows=0)
        assert timings.longest_block == pytest.approx(4.0)

    async def test_the_last_stretch_counts_when_it_is_the_longest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(_history, "_clock", clock)
        timings = _history._PhaseTimings("history", logging.INFO)
        timings.detail_on("env")
        clock.advance(6.5)
        timings.finish(rows=0)
        assert timings.longest_block == pytest.approx(6.5)

    async def test_it_is_in_the_record(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        msg, _clock_, _counts, _world_ = await _detail_message(monkeypatch, caplog)
        text = _line(msg, "longest stretch")
        assert re.search(r"\d+\.\d+ s", text)


class TestNothingChanges:
    async def test_what_is_written_equals_a_run_without_any_stopwatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with_detail, without = _world(), _world()
        _install(monkeypatch, with_detail)
        _FrozenDatetime.frozen = NOW
        result_a = await _history.async_recalculate_history(
            object(), _opts(), energy_reading_cache={}
        )

        monkeypatch.undo()
        _install(monkeypatch, without)
        monkeypatch.setattr(_history, "_PhaseTimings", lambda *_a, **_k: _history._NO_TIMINGS)
        result_b = await _history.async_recalculate_history(
            object(), _opts(), energy_reading_cache={}
        )

        assert result_a == result_b
        assert with_detail.write_calls == without.write_calls
        assert with_detail.store == without.store

    def test_a_disabled_stopwatch_keeps_no_state_through_any_new_method(self) -> None:
        disabled = _history._NO_TIMINGS
        before = copy.deepcopy(vars(disabled))

        def func() -> int:
            return 1

        disabled.detail_on("env")
        disabled.note_sources(3, 4)
        disabled.note_write(5, 6, 0.5, 0.5)
        disabled.before_await()
        disabled.after_await()
        assert disabled.wrap("x", func) is func  # not even a wrapper
        with disabled.stage("trapezoid", "sensor.x", items=9) as figure:
            figure.out = 4
        with disabled.write_call("effective"):
            pass
        assert vars(disabled) == before
        assert disabled.detail is False

    async def test_a_disabled_stopwatch_hands_the_job_over_unwrapped(self) -> None:
        seen: list[Callable[..., Any]] = []

        class Recorder:
            async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
                seen.append(fn)
                return fn(*args)

        def job() -> int:
            return 5

        assert await _history._NO_TIMINGS.recorder_job(Recorder(), job, label="x") == 5
        assert seen == [job]

    async def test_the_overhead_wrappers_are_absent_without_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_history, "_clock", _Clock())
        timings = _history._PhaseTimings("recent", logging.DEBUG)  # no detail_on
        assert timings.detail is False

        def func() -> int:
            return 1

        assert timings.wrap("x", func) is func
