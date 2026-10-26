"""
The slot job and the hour rewrite write only rows that differ (TASK-0031, ADR-024).

Same harness as ``test_history_write_only_changed``: the real writer and the real
read against a stateful recorder. Here the unit under test is the 5-minute path:
``async_recalculate_recent`` and ``async_rewrite_hourly``. What must not change is
every entity's live state (ADR-016): ``touched`` and ``last_values`` stay "every
entity computed", even when a cycle writes nothing.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest

from tests.test_history_phase_timing import NOW, PV, _opts
from tests.test_history_recent_equivalence import _history
from tests.test_history_write_only_changed import (
    SLOT,
    StoredRecorder,
    _rig,
    _snapshot,
)


async def _cycle(now: Any = NOW, **kwargs: Any) -> Any:
    return await _history.async_recalculate_recent(object(), _opts(), now, **kwargs)


def _short_ids(recorder: StoredRecorder) -> list[str]:
    return sorted({s for (t, s, _ts) in recorder.rows if t == "5minute"})


class TestTheSlotJob:
    async def test_a_cycle_whose_rows_equal_the_stored_ones_writes_nothing_and_reports_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        first = await _cycle()
        assert first[0] > 0  # the first cycle wrote its rows
        stored, imports = _snapshot(recorder), len(recorder.imports)

        written, earliest, touched, last_values = await _cycle()

        assert (written, earliest) == (0, None)  # the coordinator pushes nothing for None
        assert len(recorder.imports) == imports
        assert _snapshot(recorder) == stored
        # ... but every live state still goes out (ADR-016): nothing was dropped
        assert touched == first[2] and touched
        assert last_values == first[3] and set(last_values) == touched

    async def test_a_native_row_that_the_recorder_inserted_is_rewritten_and_is_the_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _cycle()
        sid = _short_ids(recorder)[0]
        slot = sorted(recorder.short(sid))[-1]  # the last completed slot
        recorder.rows[("5minute", sid, slot)].update(
            min=0.0, max=5.0
        )  # as the recorder compiles it

        written, earliest, _touched, _last = await _cycle()

        assert (written, earliest) == (1, slot)
        assert recorder.rows[("5minute", sid, slot)]["min"] is None

    async def test_a_row_the_recorder_has_not_committed_yet_counts_as_a_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _cycle()
        sid = _short_ids(recorder)[0]
        slot = sorted(recorder.short(sid))[-1]
        del recorder.rows[("5minute", sid, slot)]  # not readable (yet)

        written, earliest, _touched, _last = await _cycle()

        assert (written, earliest) == (1, slot)  # an extra write, never a missed one
        assert ("5minute", sid, slot) in recorder.rows

    async def test_the_earliest_change_is_the_earliest_row_that_differs_not_the_window_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world, recorder = _rig(monkeypatch)
        await _cycle()
        before = _snapshot(recorder)
        window_start = min(ts for (t, _s, ts) in recorder.rows if t == "5minute")

        ts, state = world.energy[PV][-1]
        world.energy[PV][-1] = (ts, f"{float(state) + 0.5:.3f}")
        written, earliest, _touched, _last = await _cycle()

        changed = {k for k, row in recorder.rows.items() if before.get(k) != row}
        assert changed and written == len(changed)
        assert earliest == min(ts_ for (_t, _s, ts_) in changed)
        assert earliest is not None and earliest > window_start

    async def test_an_unreadable_store_writes_everything_with_one_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        first = await _cycle()
        recorder.read_error = RuntimeError("database is locked")

        with caplog.at_level(logging.WARNING, logger=_history.__name__):
            written, earliest, _touched, _last = await _cycle()

        assert written == first[0] and earliest == first[1]  # exactly as the first cycle
        assert len([r for r in caplog.records if "stored statistics" in r.getMessage()]) == 1

    async def test_the_debug_line_and_the_record_say_how_much_was_up_to_date(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _world_, _recorder = _rig(monkeypatch)
        first = await _cycle()
        with caplog.at_level(logging.DEBUG, logger=_history.__name__):
            await _cycle()
        messages = [r.getMessage() for r in caplog.records]

        assert any(
            "earliest changed slot None" in m and "wrote 0 short-term" in m for m in messages
        )
        assert any(
            "recalculation timing (recent)" in m and f"rows unchanged {first[0]}" in m
            for m in messages
        )


class TestTheHourRewrite:
    HOUR = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)

    def _store_hour(self, recorder: StoredRecorder, sid: str, values: list[float]) -> None:
        recorder.units[sid] = "kW"
        for i, value in enumerate(values):
            recorder.rows[("5minute", sid, self.HOUR + SLOT * i)] = {
                "mean": value, "state": value, "min": None, "max": None, "sum": None,
                "last_reset": None,
            }  # fmt: skip

    def _hourly(self, sid: str) -> dict[str, Any]:
        return {
            "mean": 5.5,
            "state": 11.0,
            "min": None,
            "max": None,
            "sum": None,
            "last_reset": None,
        }

    async def test_an_hourly_row_that_is_already_right_is_not_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        sid = "sensor.effy_x"
        self._store_hour(recorder, sid, [float(i) for i in range(12)])
        recorder.rows[("hour", sid, self.HOUR)] = self._hourly(sid)
        monkeypatch.setattr(_history, "_fetch_statistics", recorder_fetch(recorder))
        imports = len(recorder.imports)

        rewritten = await _history.async_rewrite_hourly(object(), {sid: "kW"}, self.HOUR)

        assert rewritten == 1  # one statistic examined and returned, as before
        assert len(recorder.imports) == imports  # but nothing was imported

    async def test_a_wrong_hourly_row_is_rewritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _world_, recorder = _rig(monkeypatch)
        sid = "sensor.effy_x"
        self._store_hour(recorder, sid, [float(i) for i in range(12)])
        native = self._hourly(sid) | {"mean": 4.0, "min": 0.0, "max": 11.0}  # compiled natively
        recorder.rows[("hour", sid, self.HOUR)] = native
        monkeypatch.setattr(_history, "_fetch_statistics", recorder_fetch(recorder))

        await _history.async_rewrite_hourly(object(), {sid: "kW"}, self.HOUR)

        assert recorder.rows[("hour", sid, self.HOUR)] == self._hourly(sid)
        assert recorder.imports[-1] == ("hour", sid, 1)

    async def test_the_hour_rewrite_reads_only_the_hourly_side(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        sid = "sensor.effy_x"
        self._store_hour(recorder, sid, [float(i) for i in range(12)])
        recorder.rows[("hour", sid, self.HOUR)] = self._hourly(sid)
        monkeypatch.setattr(_history, "_fetch_statistics", recorder_fetch(recorder))
        recorder.reads.clear()

        await _history.async_rewrite_hourly(object(), {sid: "kW"}, self.HOUR)

        assert {period for period, *_ in recorder.reads} == {"hour"}


def recorder_fetch(recorder: StoredRecorder) -> Any:
    """``_fetch_statistics`` over the stored 5-minute rows (what the hour rewrite reads).

    Reads the store directly, so the hourly-side check sees only the new code's reads.
    """

    def fetch(_hass: Any, ids: list[str], start: Any, end: Any) -> Any:
        return {
            sid: [
                {"start": ts, "mean": row["mean"]}
                for (table, s, ts), row in sorted(recorder.rows.items(), key=lambda kv: kv[0][2])
                if table == "5minute" and s == sid and start <= ts < end
            ]
            for sid in ids
        }

    return fetch


class TestTheEndStateIsUnchanged:
    async def test_cycle_by_cycle_the_stored_rows_equal_those_of_writing_every_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-004's end state: after every cycle the store is what it always was."""
        nows = [NOW + SLOT * i for i in range(5)]
        _world_a, changed_only = _rig(monkeypatch)
        a = [await _cycle(now) for now in nows]
        a_states = [_snapshot(changed_only)]

        _world_b, write_all = _rig(monkeypatch)
        write_all.read_error = RuntimeError("every row is written, as before")
        b = [await _cycle(now) for now in nows]

        assert _snapshot(changed_only) == _snapshot(write_all)
        for (a_written, a_earliest, a_touched, a_last), (
            b_written,
            b_earliest,
            b_touched,
            b_last,
        ) in zip(a, b, strict=True):
            assert a_touched == b_touched and a_last == b_last  # live states: identical
            assert a_written <= b_written  # never more rows than writing everything
            assert a_earliest is None or (b_earliest is not None and a_earliest >= b_earliest)
        assert a_states  # the comparison above ran on a populated store
        assert sum(w for w, *_ in a) < sum(w for w, *_ in b)  # and it really wrote less
