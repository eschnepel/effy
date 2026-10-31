"""
No Effy write inserts a row the recorder has yet to insert (TASK-0035, ADR-020 Amendment).

The recorder compiles a 5-minute period at its boundary + 10 s with a plain insert and,
for the period starting at minute 55, rolls the hour up with a plain insert too. A row
Effy inserted first makes that insert fail with ``UNIQUE constraint failed``, and the
whole period is rolled back for every statistic of every integration. The fakes of the
other history tests accept an insert for a row that exists, which is why none of them
saw it: the recorder here (``tests.support.StrictRecorderMixin``) queues the imports in
one FIFO with the compile tasks, inserts like Home Assistant 2026.10.4 and records a
failed period in ``failures``.

Every test drives the REAL history code and the REAL writer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.support import StrictRecorderMixin
from tests.test_history_phase_timing import _opts, _world
from tests.test_history_recent_equivalence import _FrozenDatetime, _history, _install, _ts
from tests.test_history_write_only_changed import (
    REAL_UNITS,
    REAL_WRITE,
    StoredRecorder,
    _install_ha_api,
)

REAL_FETCH = _history._fetch_statistics  # before any test replaces it with the world's
SLOT = timedelta(minutes=5)
EFFY = [
    "sensor.effy_out_power",
    "sensor.effy_pv",
    "sensor.effy_pv_power",
    "sensor.effy_pv_w",
    "sensor.effy_pv_w_smoothed",
]
# The series for which Effy has a value in every slot of the world's last hour (the world
# has no power-family data after 11:45, so the others keep the recorder's native row there).
FULL = ["sensor.effy_out_power", "sensor.effy_pv", "sensor.effy_pv_power"]
SOURCES = ["sensor.pv", "sensor.out", "sensor.pv_w"]
OTHER = "sensor.house_energy"  # an entity of another integration, recorded natively
NATIVE = 99.0  # what the recorder compiles for a slot; an Effy row never has this value


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return _ts(hour, minute, second, day=10)


class StrictRecorder(StrictRecorderMixin, StoredRecorder):
    pass


def _rig(
    monkeypatch: pytest.MonkeyPatch, *, effy_recorded: bool = True
) -> tuple[StrictRecorder, dict[str, str]]:
    """The real writer and read over a recorder that rejects a duplicate insert."""
    world = _world()
    _install(monkeypatch, world)
    recorder = StrictRecorder()
    recorder.strict_setup((EFFY if effy_recorded else []) + SOURCES + [OTHER])
    _install_ha_api(monkeypatch, recorder)
    monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
    monkeypatch.setattr(_history, "_write_recorder_statistics", REAL_WRITE)
    sources = dict(world.units)
    monkeypatch.setattr(
        _history,
        "_get_statistics_units",
        lambda hass, ids: {**sources, **REAL_UNITS(hass, list(ids))},
    )
    return recorder, sources


async def _press(when: datetime) -> tuple[int, datetime | None, set[str], dict[str, Any]]:
    """The history button, pressed at ``when`` (the writes are queued, then drained)."""
    _FrozenDatetime.frozen = when
    result: tuple[
        int, datetime | None, set[str], dict[str, Any]
    ] = await _history.async_recalculate_history(object(), _opts())
    return result


def _tick(recorder: StrictRecorder, slot: datetime) -> None:
    """The recorder's tick at the end of ``slot`` + 10 s: compile it, in queue order."""
    recorder.queue_compile(slot)
    recorder.run_queue()


def _compiled_through(recorder: StrictRecorder, last: datetime) -> None:
    """The recorder has been running since 10:00 and compiled every period up to ``last``."""
    recorder.compile_through(last, first=_at(10, 0))


def _tick_through(recorder: StrictRecorder, first: datetime, last: datetime) -> None:
    slot = first
    while slot <= last:
        _tick(recorder, slot)
        slot += SLOT


def _effy_rows(recorder: StrictRecorder, table: str, start: datetime) -> list[str]:
    return [sid for sid in EFFY if (table, sid, start) in recorder.rows]


def _no_failure(recorder: StrictRecorder) -> None:
    assert recorder.failures == [], recorder.failures


class TestTheHistoryRecalculationInsertsNothingTheRecorderHasYetToInsert:
    @pytest.mark.asyncio
    async def test_pressed_in_the_open_hour_the_roll_up_of_that_hour_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 25))
        await _press(_at(11, 32))
        # The open hour is left to the slot job: Effy wrote no hourly row of it.
        assert _effy_rows(recorder, "hour", _at(11, 0)) == []
        _tick_through(recorder, _at(11, 30), _at(11, 55))
        _no_failure(recorder)
        assert _at(11, 55) in recorder.runs
        # The roll-up reached every entity, other integrations' included.
        assert ("hour", OTHER, _at(11, 0)) in recorder.rows
        assert ("5minute", OTHER, _at(11, 55)) in recorder.rows

    @pytest.mark.asyncio
    async def test_pressed_in_the_last_second_before_the_roll_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 50))
        await _press(_at(12, 0, 9))  # the hour is over, its last compile is 1 s away
        assert _effy_rows(recorder, "hour", _at(11, 0)) == []
        assert _effy_rows(recorder, "5minute", _at(11, 55)) == []
        _tick(recorder, _at(11, 55))
        _no_failure(recorder)
        assert ("hour", OTHER, _at(11, 0)) in recorder.rows

    @pytest.mark.asyncio
    async def test_pressed_just_after_a_slot_ended_the_slot_is_left_to_the_recorder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 20))
        await _press(_at(11, 30, 3))  # slot 11:25 is over; its compile is 7 s away
        assert _effy_rows(recorder, "5minute", _at(11, 25)) == []
        _tick(recorder, _at(11, 25))
        _no_failure(recorder)
        assert ("5minute", OTHER, _at(11, 25)) in recorder.rows

    @pytest.mark.asyncio
    async def test_pressed_in_the_seconds_after_the_roll_up_nothing_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))  # compiled at 12:00:10
        await _press(_at(12, 0, 12))
        _no_failure(recorder)
        assert all(("hour", sid, _at(11, 0)) in recorder.rows for sid in EFFY)  # the roll-up's
        # Effy cannot know the roll-up ran, so the hour is still the slot job's rewrite.
        assert all(recorder.rows[("hour", sid, _at(11, 0))]["mean"] == NATIVE for sid in EFFY)

    @pytest.mark.asyncio
    async def test_pressed_once_the_hour_is_surely_rolled_up_the_hour_is_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        await _press(_at(12, 1))
        _no_failure(recorder)
        for sid in FULL:
            row = recorder.rows[("hour", sid, _at(11, 0))]
            slots = [recorder.rows[("5minute", sid, _at(11, m))]["mean"] for m in range(0, 60, 5)]
            assert row["mean"] == pytest.approx(sum(slots) / 12), sid
            assert row["state"] == slots[-1], sid
            assert row["min"] is None and row["max"] is None, sid  # the recorder's columns, cleared
            assert slots[-1] != NATIVE, sid  # Effy's value replaced the native one

    @pytest.mark.asyncio
    async def test_the_margin_is_thirty_seconds_after_the_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two fresh recorders, the roll-up done in both: 29 s leaves the hour, 31 s writes it."""
        early, _ = _rig(monkeypatch)
        _compiled_through(early, _at(11, 55))
        await _press(_at(12, 0, 29))
        assert all(early.rows[("hour", sid, _at(11, 0))]["mean"] == NATIVE for sid in EFFY)
        assert all(early.rows[("5minute", sid, _at(11, 55))]["mean"] == NATIVE for sid in FULL)
        late, _ = _rig(monkeypatch)
        _compiled_through(late, _at(11, 55))
        await _press(_at(12, 0, 31))
        assert all(late.rows[("hour", sid, _at(11, 0))]["mean"] != NATIVE for sid in EFFY)
        assert all(late.rows[("5minute", sid, _at(11, 55))]["mean"] != NATIVE for sid in FULL)
        _no_failure(early)
        _no_failure(late)

    @pytest.mark.asyncio
    async def test_after_a_restart_the_catch_up_runs_before_the_history_recalculation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Down from 11:33 to 12:02:33; the catch-up task was queued first, at start."""
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 30))
        recorder.queue_catch_up([_at(11, m) for m in range(35, 60, 5)])
        await _press(_at(12, 2, 40))  # the catch-up is still queued behind nothing
        _no_failure(recorder)
        assert _at(11, 55) in recorder.runs
        for sid in EFFY:
            row = recorder.rows[("hour", sid, _at(11, 0))]
            assert row["min"] is None and row["max"] is None, sid  # Effy updated the roll-up
            assert row["mean"] != NATIVE, sid

    @pytest.mark.asyncio
    async def test_the_hour_that_closed_during_the_outage_is_written_after_the_catch_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 30))
        recorder.queue_catch_up([_at(11, m) for m in range(35, 60, 5)])
        recorder.run_queue()  # the catch-up is over before the button is pressed
        await _press(_at(12, 6))
        _no_failure(recorder)
        assert all(recorder.rows[("hour", sid, _at(11, 0))]["mean"] != NATIVE for sid in EFFY)


class TestOlderHistoryIsStillWritten:
    @pytest.mark.asyncio
    async def test_an_entity_the_recorder_never_compiled_gets_its_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hours from before the exclusion was removed: no native row exists for Effy."""
        recorder, _ = _rig(monkeypatch, effy_recorded=False)
        _compiled_through(recorder, _at(11, 25))
        await _press(_at(11, 32))
        assert _effy_rows(recorder, "hour", _at(10, 0)) == EFFY  # the closed hour 10
        assert _effy_rows(recorder, "hour", _at(11, 0)) == []  # the open hour
        for m in range(0, 60, 5):
            assert _effy_rows(recorder, "5minute", _at(10, m)) == EFFY, m
        assert _effy_rows(recorder, "5minute", _at(11, 25)) == EFFY
        _tick_through(recorder, _at(11, 30), _at(11, 55))
        _no_failure(recorder)

    @pytest.mark.asyncio
    async def test_a_second_press_writes_the_hour_the_first_one_left(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 25))
        await _press(_at(11, 32))
        _tick_through(recorder, _at(11, 30), _at(11, 55))
        await _press(_at(12, 1))
        _no_failure(recorder)
        assert all(recorder.rows[("hour", sid, _at(11, 0))]["mean"] != NATIVE for sid in EFFY)


class TestWhatTheRunReports:
    @pytest.mark.asyncio
    async def test_a_hour_left_to_the_slot_job_is_not_a_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rerun that changes nothing reports nothing, though the open hour has no row."""
        recorder, _ = _rig(monkeypatch, effy_recorded=False)
        _compiled_through(recorder, _at(11, 25))
        _written, first, _touched, _last = await _press(_at(11, 32))
        assert first == _at(10, 0)
        written, second, _touched, _last = await _press(_at(11, 33))
        assert (written, second) == (0, None)

    @pytest.mark.asyncio
    async def test_a_left_hour_does_not_pull_the_report_back_to_the_start_of_the_hour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only slot 11:30 is new at 11:36; the open hour has no row and that is no change."""
        recorder, _ = _rig(monkeypatch, effy_recorded=False)
        _compiled_through(recorder, _at(11, 25))
        await _press(_at(11, 32))
        _tick(recorder, _at(11, 30))
        written, earliest, _touched, _last = await _press(_at(11, 36))
        assert written > 0
        assert earliest == _at(11, 30)

    @pytest.mark.asyncio
    async def test_a_slot_left_to_the_recorder_is_no_change_either(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slot 11:30 ended 3 s ago: it is computed, not written, and not reported."""
        recorder, _ = _rig(monkeypatch, effy_recorded=False)
        _compiled_through(recorder, _at(11, 25))
        await _press(_at(11, 34))
        written, earliest, touched, _last = await _press(_at(11, 35, 3))
        assert (written, earliest) == (0, None)
        assert _effy_rows(recorder, "5minute", _at(11, 30)) == []
        assert touched  # the live states still go out for every computed entity

    @pytest.mark.asyncio
    async def test_the_next_press_reports_the_hour_it_now_writes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch, effy_recorded=False)
        _compiled_through(recorder, _at(11, 25))
        await _press(_at(11, 32))
        _tick_through(recorder, _at(11, 30), _at(11, 55))
        _written, earliest, _touched, _last = await _press(_at(12, 1))
        assert earliest == _at(11, 0)  # the hourly row of the hour it had left

    @pytest.mark.asyncio
    async def test_the_timing_record_counts_the_hourly_rows_left_for_the_slot_job(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 25))
        with caplog.at_level(logging.INFO):
            await _press(_at(11, 32))
            _tick_through(recorder, _at(11, 30), _at(11, 55))
            await _press(_at(12, 1))
        lines = [
            r.getMessage()
            for r in caplog.records
            if "recalculation timing (history)" in r.getMessage()
        ]
        assert len(lines) == 2
        assert f"hourly rows left for the slot job {len(EFFY)}" in lines[0]
        assert "hourly rows left for the slot job 0" in lines[1]

    @pytest.mark.asyncio
    async def test_the_live_state_still_comes_from_the_last_completed_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The slot left to the recorder is still computed: ADR-016's push is unchanged."""
        _recorder, _ = _rig(monkeypatch)
        _compiled_through(_recorder, _at(11, 20))
        _written, _earliest, touched, last_values = await _press(_at(11, 30, 3))
        assert touched
        assert all(sid in last_values for sid in touched)


class TestTheSlotJobsHourlyRewriteSucceedsAfterTheRollUp:
    @pytest.mark.asyncio
    async def test_the_rewrite_updates_the_hour_a_press_left(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Press mid-hour, then every slot's job until the hour's rewrite; the hourly row
        equals what a full recalculation at 12:01 writes (the TASK-0004 equivalence,
        extended to the hourly table)."""
        recorder, sources = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 25))
        await _press(_at(11, 32))
        for m in range(30, 60, 5):
            slot = _at(11, m)
            _FrozenDatetime.frozen = slot + SLOT + timedelta(seconds=11)
            _tick(recorder, slot)
            _written, _first, _touched, last_values = await _history.async_recalculate_recent(
                object(), _opts(), _FrozenDatetime.frozen
            )
            if m == 55:
                units = {sid: unit for sid, (_value, unit) in last_values.items()}
                # the rewrite reads the recorder's own 5-minute rows, not the world's sources
                monkeypatch.setattr(_history, "_fetch_statistics", REAL_FETCH)
                await _history.async_rewrite_hourly(object(), units, _at(11, 0))
        _no_failure(recorder)
        rewritten = {sid: dict(recorder.rows[("hour", sid, _at(11, 0))]) for sid in FULL}

        reference, _ = _rig(monkeypatch)
        _compiled_through(reference, _at(11, 55))
        await _press(_at(12, 1))
        for sid in FULL:
            expected = reference.rows[("hour", sid, _at(11, 0))]
            assert rewritten[sid]["mean"] == pytest.approx(expected["mean"]), sid
            assert rewritten[sid]["state"] == pytest.approx(expected["state"]), sid
            assert rewritten[sid]["min"] is None and rewritten[sid]["max"] is None, sid
