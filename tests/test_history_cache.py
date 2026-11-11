"""The history code reads through one ``Storage``, and the cache is one (TASK-0036, ADR-025).

``tests/test_cache.py`` shows that a ``Cache`` stands for any storage. These tests show
it with the real history code: the same rows are written with the cache and without it;
the second slot job reads only what the first did not; after a clean history
recalculation the cache holds exactly what that run asked the recorder for; what Effy
wrote is forgotten; a failing read leaves nothing half-filled; the timing record says
what was served from memory; and no read of the recorder can go around the interface.

They use the real writer and the strict recorder of TASK-0035 (``tests.support``).
"""

from __future__ import annotations

import ast
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.test_history_phase_timing import _opts
from tests.test_history_recent_equivalence import _FrozenDatetime, _history
from tests.test_history_recorder_rollup import EFFY, _at, _compiled_through, _rig

SEAM = timedelta(milliseconds=500)
SETTLE = timedelta(seconds=30)


class _Logging:
    """A ``Storage`` that logs what reaches it and passes everything on."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[tuple[Any, ...]] = []
        self.written_calls: list[tuple[Any, ...]] = []
        self.fail_on: str | None = None  # an entity id whose raw read raises

    async def raw_states(self, entity_id: str, start: datetime, end: datetime) -> Any:
        self.calls.append(("raw_states", entity_id, start, end))
        if entity_id == self.fail_on:
            raise RuntimeError("the recorder is gone")
        return await self.inner.raw_states(entity_id, start, end)

    async def last_valid_state_before(self, entity_id: str, before: datetime, days: int) -> Any:
        self.calls.append(("last_valid_state_before", entity_id, before, days))
        return await self.inner.last_valid_state_before(entity_id, before, days)

    async def statistics(self, ids: list[str], start: datetime, end: datetime) -> Any:
        self.calls.append(("statistics", tuple(ids), start, end))
        return await self.inner.statistics(ids, start, end)

    async def capacity_states(self, entity_id: str, start: datetime, end: datetime) -> Any:
        self.calls.append(("capacity_states", entity_id, start, end))
        return await self.inner.capacity_states(entity_id, start, end)

    async def statistics_units(self, ids: list[str]) -> Any:
        self.calls.append(("statistics_units", tuple(ids)))
        return await self.inner.statistics_units(ids)

    async def unchanged_rows(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("unchanged_rows", args[0]))
        return await self.inner.unchanged_rows(*args, **kwargs)

    def written(self, ids: list[str], start: datetime, end: datetime) -> None:
        self.written_calls.append((tuple(ids), start, end))
        self.inner.written(ids, start, end)

    def unit(self, entity_id: str) -> str:
        return str(self.inner.unit(entity_id))

    def state_class(self, entity_id: str) -> str | None:
        result: str | None = self.inner.state_class(entity_id)
        return result

    def retention_days(self) -> int:
        return int(self.inner.retention_days())

    def set_timing(self, timings: Any) -> None:
        self.inner.set_timing(timings)


def _recorder_storage() -> Any:
    return _history.RecorderStorage(object(), _history)


def _cache_over(inner: Any) -> Any:
    return _history.Cache(
        inner, clock=lambda: _FrozenDatetime.frozen, settle=SETTLE, max_raw_entries=200_000
    )


async def _scenario(monkeypatch: pytest.MonkeyPatch, with_cache: bool) -> tuple[Any, list[Any]]:
    """A history run and three slot jobs; what was stored and what each returned."""
    recorder, _ = _rig(monkeypatch)
    _compiled_through(recorder, _at(11, 55))
    storage = _cache_over(_recorder_storage()) if with_cache else None
    results: list[Any] = []
    _FrozenDatetime.frozen = _at(12, 1)
    results.append(await _history.async_recalculate_history(object(), _opts(), storage=storage))
    for minute in (5, 10, 15):
        now = _at(12, minute, 11)
        _FrozenDatetime.frozen = now
        recorder.queue_compile(_at(12, minute - 5))
        recorder.run_queue()
        results.append(
            await _history.async_recalculate_recent(object(), _opts(), now, storage=storage)
        )
    return recorder, results


class TestTheSameRowsWithAndWithoutTheCache:
    @pytest.mark.asyncio
    async def test_a_history_run_and_three_slot_jobs_write_identical_statistics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plain, plain_results = await _scenario(monkeypatch, with_cache=False)
        cached, cached_results = await _scenario(monkeypatch, with_cache=True)
        assert plain.rows, "the scenario writes something"
        assert cached.rows == plain.rows
        assert cached_results == plain_results
        assert cached.failures == plain.failures == []


class TestTheSecondSlotJobReadsOnlyWhatTheFirstDidNot:
    @pytest.mark.asyncio
    async def test_the_raw_reads_start_where_the_cache_left_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        asked: list[tuple[str, datetime, datetime]] = []
        world_read = _history._fetch_raw_energy_states

        def spy(hass: Any, entity_id: str, start: datetime, end: datetime) -> Any:
            asked.append((entity_id, start, end))
            return world_read(hass, entity_id, start, end)

        monkeypatch.setattr(_history, "_fetch_raw_energy_states", spy)
        cache = _cache_over(_recorder_storage())
        _FrozenDatetime.frozen = _at(12, 1)
        await _history.async_recalculate_history(object(), _opts(), storage=cache)
        asked.clear()
        now = _at(12, 6, 11)
        _FrozenDatetime.frozen = now
        recorder.queue_compile(_at(12, 0))
        recorder.run_queue()
        await _history.async_recalculate_recent(object(), _opts(), now, storage=cache)
        assert asked, "the newest states are still read"
        window_start = now - _history.RECENT_RECALC_WINDOW
        for _entity, start, _end in asked:
            assert start > window_start + timedelta(minutes=5), asked  # not the whole window again
            assert start >= _at(12, 0), asked  # the tail from the settled bound of the last run

    @pytest.mark.asyncio
    async def test_without_the_cache_the_whole_window_is_read_each_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        asked: list[datetime] = []
        world_read = _history._fetch_raw_energy_states

        def spy(hass: Any, entity_id: str, start: datetime, end: datetime) -> Any:
            asked.append(start)
            return world_read(hass, entity_id, start, end)

        monkeypatch.setattr(_history, "_fetch_raw_energy_states", spy)
        now = _at(12, 6, 11)
        _FrozenDatetime.frozen = now
        await _history.async_recalculate_recent(object(), _opts(), now)
        assert min(asked) <= now - _history.RECENT_RECALC_WINDOW


class TestACleanCacheHoldsExactlyWhatARecalculationRead:
    @pytest.mark.asyncio
    async def test_the_snapshot_is_the_set_of_reads_the_recorder_saw(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        logging_inner = _Logging(_recorder_storage())
        cache = _cache_over(logging_inner)
        cache.clear()
        _FrozenDatetime.frozen = _at(12, 1)
        await _history.async_recalculate_history(object(), _opts(), storage=cache)
        snapshot = cache.snapshot()

        raw = [c for c in logging_inner.calls if c[0] == "raw_states"]
        stats = [c for c in logging_inner.calls if c[0] == "statistics"]
        assert raw and stats
        assert set(snapshot["raw"]) == {c[1] for c in raw}
        for entity, held in snapshot["raw"].items():
            asked = [c for c in raw if c[1] == entity]
            assert len(asked) == 1, "one read per entity in a history run"
            assert held["lo"] == asked[0][2].isoformat()
            assert held["obtained"] == asked[0][3].isoformat()
        assert set(snapshot["stats"]) == {sid for c in stats for sid in c[1]}
        for sid, held in snapshot["stats"].items():
            asked = [c for c in stats if sid in c[1]]
            assert held["lo"] == min(c[2] for c in asked).isoformat()
            assert held["obtained"] == max(c[3] for c in asked).isoformat()
        assert snapshot["capacity"] == {} and snapshot["anchors"] == []
        # the observed kinds are the ones this run asked for, no others
        assert set(snapshot["observed"]["units"]) == set(snapshot["observed"]["state_classes"])
        assert snapshot["observed"]["unchanged_rows"], "the stored rows were read and recorded"

    @pytest.mark.asyncio
    async def test_nothing_the_run_read_is_missing_from_the_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replayed from the snapshot alone, the run reads exactly the same things."""
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        first = _cache_over(_Logging(_recorder_storage()))
        _FrozenDatetime.frozen = _at(12, 1)
        recorded = await _history.async_recalculate_history(object(), _opts(), storage=first)

        from tests.support import RaisingStorage

        replay = _history.Cache.from_snapshot(
            first.snapshot(), RaisingStorage(), clock=lambda: _FrozenDatetime.frozen, settle=SETTLE
        )
        recorder2, _ = _rig(monkeypatch)
        _compiled_through(recorder2, _at(11, 55))
        replayed = await _history.async_recalculate_history(object(), _opts(), storage=replay)
        assert replayed == recorded
        assert recorder2.rows == recorder.rows


class TestTheStoredMeansForTheDeltaDerivation:
    """ADR-027 decision 5: Effy's own rows are observed, never reused."""

    @pytest.mark.asyncio
    async def test_the_cache_asks_the_storage_every_time_and_records_nothing(self) -> None:
        class _Means:
            def __init__(self) -> None:
                self.calls = 0

            async def stored_means(self, *args: Any) -> Any:
                self.calls += 1
                return {_at(10, 0): float(self.calls)}, {}

        inner = _Means()
        cache = _cache_over(inner)
        first = await cache.stored_means("sensor.effy_x", _at(9, 0), _at(11, 0))
        second = await cache.stored_means("sensor.effy_x", _at(9, 0), _at(11, 0))
        assert (first[0][_at(10, 0)], second[0][_at(10, 0)]) == (1.0, 2.0)  # not reused
        assert inner.calls == 2
        assert "stored_means" not in str(cache.snapshot()["observed"])


class TestTheStoredValuesOfThePvInputsInTheSnapshot:
    """ADR-024 Amendment 2026-11-09 (TASK-0045b): the PV delta's stored values are observed."""

    @staticmethod
    async def _recorded(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        cache = _cache_over(_Logging(_recorder_storage()))
        _FrozenDatetime.frozen = _at(12, 1)
        recorded = await _history.async_recalculate_history(object(), _opts(), storage=cache)
        return cache.snapshot(), recorded

    @pytest.mark.asyncio
    async def test_only_the_effective_series_of_the_pv_inputs_record_their_stored_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        snapshot, recorded = await self._recorded(monkeypatch)
        entries = snapshot["observed"]["unchanged_rows"]
        with_values = {k: v for k, v in entries.items() if k.endswith("|values")}
        assert {v["statistic_id"] for v in with_values.values()} == {
            "sensor.effy_pv",
            "sensor.effy_pv_w",
        }  # the PV inputs of the world (_opts), not the output, not the derived series
        assert all("stored" in v for v in with_values.values())
        assert not any("stored" in v for k, v in entries.items() if k not in with_values)
        assert recorded[4] is not None

    @pytest.mark.asyncio
    async def test_an_entry_without_stored_values_replays_as_no_delta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import copy

        from tests.support import RaisingStorage

        snapshot, recorded = await self._recorded(monkeypatch)
        older = copy.deepcopy(snapshot)
        for key, entry in older["observed"]["unchanged_rows"].items():
            if key.endswith("|values"):
                del entry["stored"]  # a snapshot of a version that did not record them
        replay = _history.Cache.from_snapshot(
            older, RaisingStorage(), clock=lambda: _FrozenDatetime.frozen, settle=SETTLE
        )
        recorder2, _ = _rig(monkeypatch)
        _compiled_through(recorder2, _at(11, 55))
        replayed = await _history.async_recalculate_history(object(), _opts(), storage=replay)
        assert replayed[4] is None  # no delta: not 0
        assert replayed[:4] == recorded[:4]  # everything else is what was recorded


class TestWhatEffyWroteIsForgotten:
    @pytest.mark.asyncio
    async def test_the_storage_is_told_once_the_writer_has_the_rows_in_the_recorder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        spy = _Logging(_recorder_storage())
        applied_at_call: list[int] = []
        original = spy.written

        def written(ids: list[str], start: datetime, end: datetime) -> None:
            applied_at_call.append(len(recorder.imports))
            original(ids, start, end)

        spy.written = written  # type: ignore[method-assign]
        _FrozenDatetime.frozen = _at(12, 1)
        await _history.async_recalculate_history(object(), _opts(), storage=spy)
        told = {sid for ids, _s, _e in spy.written_calls for sid in ids}
        assert set(EFFY) <= told
        # the writer drains the recorder's queue before it returns: the rows are in
        assert applied_at_call and all(n > 0 for n in applied_at_call)

    @pytest.mark.asyncio
    async def test_the_slot_job_tells_it_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(12, 0))
        spy = _Logging(_recorder_storage())
        now = _at(12, 6, 11)
        _FrozenDatetime.frozen = now
        await _history.async_recalculate_recent(object(), _opts(), now, storage=spy)
        assert spy.written_calls


class TestTheTimingRecord:
    @pytest.mark.asyncio
    async def test_it_says_how_many_reads_the_cache_served(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        cache = _cache_over(_recorder_storage())
        with caplog.at_level(logging.INFO):
            _FrozenDatetime.frozen = _at(12, 1)
            await _history.async_recalculate_history(object(), _opts(), storage=cache)
            _FrozenDatetime.frozen = _at(12, 2)
            await _history.async_recalculate_history(object(), _opts(), storage=cache)
        lines = [r.getMessage() for r in caplog.records if "timing (history)" in r.getMessage()]
        assert len(lines) == 2
        pattern = r"reads from the cache (\d+), from the recorder (\d+)"
        first, second = (re.search(pattern, line) for line in lines)
        assert first and second
        assert int(first.group(2)) > 0  # a clean cache reads from the recorder
        queries = re.search(r"recorder queries (\d+)", lines[0])
        assert queries and int(queries.group(1)) > 0, lines[0]  # and these reads are timed
        assert int(second.group(1)) >= int(first.group(1))  # and the next run gets some from memory

    @pytest.mark.asyncio
    async def test_a_run_without_a_cache_says_nothing_about_one(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        with caplog.at_level(logging.INFO):
            _FrozenDatetime.frozen = _at(12, 1)
            await _history.async_recalculate_history(object(), _opts())
        lines = [r.getMessage() for r in caplog.records if "timing (history)" in r.getMessage()]
        assert lines and "reads from the cache" not in lines[0]


class TestTheTimingScopeEndsWithTheRun:
    """The storage lives as long as the entry; a run's timing record must not outlive it."""

    @pytest.mark.asyncio
    async def test_after_a_history_run_and_after_a_slot_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        underneath = _recorder_storage()
        cache = _cache_over(underneath)
        _FrozenDatetime.frozen = _at(12, 1)
        await _history.async_recalculate_history(object(), _opts(), storage=cache)
        assert underneath._timings is _history._NO_TIMINGS
        now = _at(12, 6, 11)
        _FrozenDatetime.frozen = now
        await _history.async_recalculate_recent(object(), _opts(), now, storage=cache)
        assert underneath._timings is _history._NO_TIMINGS

    @pytest.mark.asyncio
    async def test_after_a_run_that_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        underneath = _recorder_storage()
        failing = _Logging(underneath)
        failing.fail_on = EFFY[0]  # not read; make the first raw read fail instead
        monkeypatch.setattr(
            _history,
            "_fetch_raw_energy_states",
            lambda *args: (_ for _ in ()).throw(RuntimeError("the recorder is gone")),
        )
        cache = _cache_over(underneath)
        _FrozenDatetime.frozen = _at(12, 1)
        with pytest.raises(RuntimeError):
            await _history.async_recalculate_history(object(), _opts(), storage=cache)
        assert underneath._timings is _history._NO_TIMINGS


class TestTheSlotJobsReadsAreTimedThroughTheStorage:
    @pytest.mark.asyncio
    async def test_the_recent_record_counts_the_recorder_queries(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(12, 0))
        cache = _cache_over(_recorder_storage())
        now = _at(12, 6, 11)
        _FrozenDatetime.frozen = now
        with caplog.at_level(logging.DEBUG):
            await _history.async_recalculate_recent(object(), _opts(), now, storage=cache)
        lines = [r.getMessage() for r in caplog.records if "timing (recent)" in r.getMessage()]
        assert lines
        match = re.search(r"recorder queries (\d+)", lines[0])
        assert match and int(match.group(1)) > 0, lines[0]


class TestAFailingRead:
    @pytest.mark.asyncio
    async def test_leaves_no_half_filled_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder, _ = _rig(monkeypatch)
        _compiled_through(recorder, _at(11, 55))
        inner = _Logging(_recorder_storage())
        cache = _cache_over(inner)
        _FrozenDatetime.frozen = _at(12, 1)
        # find which entities a clean run reads, then fail the second of them
        await _history.async_recalculate_history(object(), _opts(), storage=cache)
        entities = [c[1] for c in inner.calls if c[0] == "raw_states"]
        assert len(entities) >= 2
        cache.clear()
        inner.fail_on = entities[1]
        with pytest.raises(RuntimeError):
            await _history.async_recalculate_history(object(), _opts(), storage=cache)
        held = cache.snapshot()["raw"]
        assert entities[1] not in held  # nothing of the entity whose read failed
        # what is held is whole: one finished read per entity, spanning what was asked
        inner.fail_on = None
        for entity, entry in held.items():
            asked = [
                c for c in inner.calls[len(entities) :] if c[0] == "raw_states" and c[1] == entity
            ]
            assert asked and entry["lo"] == asked[0][2].isoformat()
            assert entry["obtained"] == asked[0][3].isoformat()


class TestNoReadGoesAroundTheInterface:
    """A recorder read API is named only inside the readers that ``RecorderStorage`` calls."""

    _RECORDER_APIS = {"state_changes_during_period", "statistics_during_period", "get_metadata"}
    _READERS = {
        "_fetch_raw_energy_states",
        "_fetch_last_valid_state_before",
        "_fetch_statistics",
        "_fetch_capacity_states",
        "_fetch_integral_source",
        "_unchanged_rows",
        "_unchanged_slots",
        "_get_statistics_units",
        "_get_unit",
        "_get_state_class",
        "_get_short_term_retention_days",
    }
    # the readers may call each other
    _INSIDE_READERS = {"_unchanged_rows", "_unchanged_slots", "_get_statistics_units"}

    def _functions(self) -> dict[str, ast.AST]:
        path = Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "history.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }

    def test_a_recorder_read_api_is_named_only_in_the_readers(self) -> None:
        for name, node in self._functions().items():
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
                n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
            }
            imported = {
                a.name for n in ast.walk(node) if isinstance(n, ast.ImportFrom) for a in n.names
            }
            if (used | imported) & self._RECORDER_APIS:
                assert name in self._READERS, f"{name} reads the recorder around the storage"

    def test_a_reader_is_called_only_by_a_reader(self) -> None:
        for name, node in self._functions().items():
            if name in self._READERS:
                continue
            called = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & self._READERS
            assert not called, f"{name} calls {sorted(called)} instead of the storage"

    def test_the_readers_are_called_by_nothing_but_each_other_and_the_storage(self) -> None:
        for name in self._INSIDE_READERS | {"_get_unit"}:
            assert name in self._READERS
        for name, node in self._functions().items():
            if name in self._READERS:
                called = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & (
                    self._READERS - {name}
                )
                assert called <= self._INSIDE_READERS, (name, called)
