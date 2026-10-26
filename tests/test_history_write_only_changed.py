"""
The history recalculation writes only rows that differ (TASK-0030, ADR-024).

98 % of a history recalculation was the recorder upserting rows at about 1 ms
each, and most rows equal what is stored. The recalculation now reads the stored
rows first and hands the recorder only the new or different ones, and
``recalculated_from`` is the earliest slot actually written (ADR-012 Amendment
2026-10-25).

The harness here is stateful, unlike the fake writer of
``test_history_recent_equivalence``: the REAL writer and the REAL read run against
a recorder that keeps rows per table and, like Home Assistant's import, replaces
every column of a row on an upsert. A fake of Home Assistant's
``statistics_during_period`` and ``get_metadata`` answers from that store, so the
arguments the production code passes are checked too.
"""

from __future__ import annotations

import logging
import math
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any

import pytest

from tests.test_history_phase_timing import NOW, OUT, PV, PW, _opts, _world
from tests.test_history_recent_equivalence import (
    _FakeRecorder,
    _FrozenDatetime,
    _history,
    _install,
)

REAL_WRITE = _history._write_recorder_statistics
REAL_UNITS = _history._get_statistics_units
COLUMNS = ("mean", "state", "min", "max", "sum", "last_reset")
SLOT = timedelta(minutes=5)


class StoredRecorder(_FakeRecorder):
    """Rows per table, upserted with Home Assistant's replace-every-column rule."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, datetime], dict[str, Any]] = {}
        self.units: dict[str, str] = {}
        self.imports: list[tuple[str, str, int]] = []  # (table, statistic_id, rows)
        self.reads: list[tuple[str, set[str], datetime, datetime | None]] = []
        self.read_error: Exception | None = None
        self.start_as_float = False

    @staticmethod
    def _table(table: Any) -> str:
        return "5minute" if table is _history.StatisticsShortTerm else "hour"

    async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
        return fn(*args)

    def async_import_statistics(self, metadata: Any, stats: Any, table: Any) -> None:
        sid = metadata["statistic_id"]
        self.units[sid] = metadata["unit_of_measurement"]
        self.imports.append((self._table(table), sid, len(stats)))
        for stat in stats:
            self.rows[(self._table(table), sid, stat["start"])] = {
                column: stat.get(column) for column in COLUMNS
            }

    async def async_block_till_done(self) -> None:
        return None

    # --- Home Assistant's read API over the store ---------------------------------
    def statistics_during_period(
        self,
        _hass: Any,
        start: datetime,
        end: datetime | None,
        statistic_ids: set[str] | None,
        period: str,
        units: Any,
        types: set[str],
    ) -> dict[str, list[dict[str, Any]]]:
        assert units is None  # stored units, no conversion
        self.reads.append((period, set(statistic_ids or ()), start, end))
        if self.read_error is not None:
            raise self.read_error
        result: dict[str, list[dict[str, Any]]] = {}
        for (table, sid, ts), row in sorted(self.rows.items(), key=lambda kv: kv[0][2]):
            if table != period or sid not in (statistic_ids or ()):
                continue
            if ts < start or (end is not None and ts >= end):
                continue
            out: dict[str, Any] = {"start": ts.timestamp() if self.start_as_float else ts}
            out.update({column: row.get(column) for column in types})
            result.setdefault(sid, []).append(out)
        return result

    def get_metadata(
        self, _hass: Any, statistic_ids: set[str] | None = None, **_: Any
    ) -> dict[str, tuple[int, dict[str, Any]]]:
        return {
            sid: (1, {"unit_of_measurement": unit})
            for sid, unit in self.units.items()
            if statistic_ids is None or sid in statistic_ids
        }

    def short(self, sid: str) -> dict[datetime, dict[str, Any]]:
        return {ts: row for (t, s, ts), row in self.rows.items() if t == "5minute" and s == sid}

    def hourly(self, sid: str) -> dict[datetime, dict[str, Any]]:
        return {ts: row for (t, s, ts), row in self.rows.items() if t == "hour" and s == sid}


def _install_ha_api(monkeypatch: pytest.MonkeyPatch, recorder: StoredRecorder) -> None:
    names = [
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.recorder",
        "homeassistant.components.recorder.statistics",
    ]
    modules = {name: ModuleType(name) for name in names}
    stats = modules["homeassistant.components.recorder.statistics"]
    setattr(stats, "statistics_during_period", recorder.statistics_during_period)
    setattr(stats, "get_metadata", recorder.get_metadata)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def _rig(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, StoredRecorder]:
    """The in-memory world, the real writer and read, and a stateful recorder."""
    world = _world()
    _install(monkeypatch, world)
    recorder = StoredRecorder()
    _install_ha_api(monkeypatch, recorder)
    monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
    monkeypatch.setattr(_history, "_write_recorder_statistics", REAL_WRITE)
    sources = dict(world.units)
    monkeypatch.setattr(
        _history,
        "_get_statistics_units",
        lambda hass, ids: {**sources, **REAL_UNITS(hass, list(ids))},
    )
    _FrozenDatetime.frozen = NOW
    return world, recorder


async def _recalculate(**kwargs: Any) -> Any:
    return await _history.async_recalculate_history(object(), _opts(), **kwargs)


def _snapshot(recorder: StoredRecorder) -> dict[Any, dict[str, Any]]:
    return {key: dict(row) for key, row in recorder.rows.items()}


class TestTheRule:
    """``_unchanged_rows`` against a stored row, one condition at a time."""

    SID = "sensor.effy_x"
    T0 = NOW - timedelta(hours=3)

    def _rig(self, monkeypatch: pytest.MonkeyPatch) -> StoredRecorder:
        recorder = StoredRecorder()
        _install_ha_api(monkeypatch, recorder)
        monkeypatch.setattr(_history, "_get_statistics_units", REAL_UNITS)
        recorder.units[self.SID] = "kW"
        return recorder

    def _store(self, recorder: StoredRecorder, ts: datetime, **columns: Any) -> None:
        row = {"mean": 1.5, "state": 1.5, "min": None, "max": None, "sum": None, "last_reset": None}
        recorder.rows[("5minute", self.SID, ts)] = {**row, **columns}

    def _unchanged(self, value: float = 1.5, unit: str = "kW") -> set[datetime]:
        result = _history._unchanged_rows(object(), self.SID, unit, [(self.T0, value)], {})
        return set(result["short"])

    def test_a_row_that_already_is_what_the_writer_would_write_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._store(self._rig(monkeypatch), self.T0)
        assert self._unchanged() == {self.T0}

    @pytest.mark.parametrize("column", ["mean", "state"])
    def test_a_different_mean_or_state_is_a_change(
        self, monkeypatch: pytest.MonkeyPatch, column: str
    ) -> None:
        self._store(self._rig(monkeypatch), self.T0, **{column: 1.6})
        assert self._unchanged() == set()

    @pytest.mark.parametrize(
        ("column", "value"),
        [("min", 1.0), ("max", 2.0), ("sum", 9.0), ("last_reset", NOW)],
    )
    def test_a_native_row_that_still_carries_a_column_the_writer_clears_is_a_change(
        self, monkeypatch: pytest.MonkeyPatch, column: str, value: Any
    ) -> None:
        self._store(self._rig(monkeypatch), self.T0, **{column: value})
        assert self._unchanged() == set()

    def test_no_stored_row_is_a_change(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._rig(monkeypatch)
        assert self._unchanged() == set()

    def test_equality_is_exact_not_approximate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._store(self._rig(monkeypatch), self.T0, mean=math.nextafter(1.5, 2.0))
        assert self._unchanged() == set()

    def test_a_series_whose_stored_unit_differs_is_changed_in_full(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._rig(monkeypatch)
        self._store(recorder, self.T0)
        assert self._unchanged(unit="W") == set()

    def test_a_series_without_metadata_is_changed_in_full(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._rig(monkeypatch)
        self._store(recorder, self.T0)
        recorder.units.clear()
        assert self._unchanged() == set()

    def test_a_start_returned_as_a_unix_timestamp_still_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._rig(monkeypatch)
        recorder.start_as_float = True  # some Home Assistant versions do this
        self._store(recorder, self.T0)
        assert self._unchanged() == {self.T0}

    def test_an_hourly_row_is_compared_with_what_the_writer_derives_from_the_slots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._rig(monkeypatch)
        hour = self.T0.replace(minute=0, second=0, microsecond=0)
        slots = [(hour + SLOT * i, float(i)) for i in range(12)]
        derived = _history._hourly_rows(slots)
        assert derived == {hour: (sum(range(12)) / 12, 11.0)}  # mean of the hour, last value
        recorder.rows[("hour", self.SID, hour)] = {
            "mean": 5.5, "state": 11.0, "min": None, "max": None, "sum": None, "last_reset": None,
        }  # fmt: skip
        result = _history._unchanged_rows(object(), self.SID, "kW", [], derived)
        assert set(result["hourly"]) == {hour}
        recorder.rows[("hour", self.SID, hour)]["state"] = 10.0
        result = _history._unchanged_rows(object(), self.SID, "kW", [], derived)
        assert set(result["hourly"]) == set()


class TestTheWriterHonoursTheMarks:
    async def test_marked_rows_are_not_imported_and_the_count_is_what_was_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = StoredRecorder()
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
        hour = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        slots = [(hour + SLOT * i, float(i)) for i in range(12)]
        series = {
            "sensor.effy_x": {
                "unit": "kW",
                "slot_values": slots,
                "unchanged_short": frozenset(ts for ts, _ in slots[:10]),
                "unchanged_hourly": frozenset(),
            },
            "sensor.effy_y": {
                "unit": "kW",
                "slot_values": slots,
                "unchanged_short": frozenset(ts for ts, _ in slots),
                "unchanged_hourly": frozenset({hour}),
            },
        }
        written = await REAL_WRITE(object(), series, include_long_term=True)
        assert written == 2  # x: the last two slots; y: none
        assert recorder.imports == [
            ("5minute", "sensor.effy_x", 2),
            ("hour", "sensor.effy_x", 1),
        ]

    def test_the_hourly_rows_are_derived_once_for_the_writer_and_the_comparison(self) -> None:
        hour = NOW.replace(minute=0, second=0, microsecond=0)
        rows = _history._hourly_rows([(hour, 2.0), (hour + SLOT, 4.0), (hour + 2 * SLOT, 9.0)])
        assert rows == {hour: (5.0, 9.0)}


class TestTheHistoryRecalculation:
    async def test_a_first_run_writes_exactly_what_it_wrote_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        written, earliest, _touched, _last = await _recalculate()
        with_marks = _snapshot(recorder)

        # the same run with the read made impossible: the fail-safe is the old behaviour
        _world2, baseline = _rig(monkeypatch)
        baseline.read_error = RuntimeError("no read")
        await _recalculate()

        assert with_marks == _snapshot(baseline)
        assert written == sum(len(recorder.short(sid)) for sid in {k[1] for k in recorder.rows})
        assert earliest == min(ts for (t, _s, ts) in recorder.rows if t == "5minute")

    async def test_a_second_run_with_nothing_changed_writes_nothing_and_reports_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        first = await _recalculate()
        imports_after_first = len(recorder.imports)
        stored = _snapshot(recorder)

        written, earliest, touched, last_values = await _recalculate()

        assert len(recorder.imports) == imports_after_first  # not one import task
        assert (written, earliest) == (0, None)
        assert _snapshot(recorder) == stored
        assert touched == first[2] and last_values == first[3]  # live states: every entity

    async def test_one_changed_raw_value_writes_only_what_it_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world, recorder = _rig(monkeypatch)
        await _recalculate()
        before = _snapshot(recorder)

        ts, state = world.energy[PV][8]
        world.energy[PV][8] = (ts, f"{float(state) + 0.5:.3f}")
        imports_before = len(recorder.imports)
        written, earliest, _touched, _last = await _recalculate()

        changed = {k for k, row in recorder.rows.items() if before.get(k) != row}
        changed_short = {ts_ for (t, _s, ts_) in changed if t == "5minute"}
        assert changed_short  # the edit did change something
        assert written == sum(1 for (t, _s, _ts) in changed if t == "5minute")
        rows_imported = sum(n for _t, _s, n in recorder.imports[imports_before:])
        assert rows_imported == len(changed)  # nothing was imported that did not change
        # the earliest change: a short-term slot, or the first slot of a changed hour
        candidates = set(changed_short)
        for t, sid, hour in changed:
            if t == "hour":
                candidates.add(min(s for s in recorder.short(sid) if s >= hour))
        assert earliest == min(candidates)

    async def test_a_native_row_with_min_and_max_is_rewritten_even_if_mean_and_state_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        sid = next(s for s in {k[1] for k in recorder.rows} if recorder.short(s))
        slot = sorted(recorder.short(sid))[3]
        recorder.rows[("5minute", sid, slot)].update(min=0.1, max=9.9)

        written, earliest, _t, _l = await _recalculate()

        assert (written, earliest) == (1, slot)
        assert recorder.rows[("5minute", sid, slot)]["min"] is None  # cleared again

    async def test_a_changed_stored_unit_rewrites_the_whole_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        sid = next(s for s in {k[1] for k in recorder.rows} if recorder.short(s))
        rows = len(recorder.short(sid))
        recorder.units[sid] = "Wh"

        written, _earliest, _t, _l = await _recalculate()

        assert written == rows
        assert recorder.units[sid] != "Wh"  # the import restored the right unit

    async def test_slots_older_than_the_short_term_cutoff_are_neither_compared_nor_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        monkeypatch.setattr(_history, "_get_short_term_retention_days", lambda _hass: 1)
        await _recalculate()
        cutoff = NOW - timedelta(days=1)
        assert all(ts >= cutoff for (t, _s, ts) in recorder.rows if t == "5minute")
        imports = len(recorder.imports)
        written, _earliest, _t, _l = await _recalculate()  # second run: it reads
        assert written == 0 and len(recorder.imports) == imports
        short_reads = [r for r in recorder.reads if r[0] == "5minute"]
        assert short_reads and all(start >= cutoff for _p, _i, start, _e in short_reads)

    async def test_each_series_is_read_on_its_own(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        assert recorder.reads == []  # a first run has no stored metadata: nothing to read
        await _recalculate()
        assert recorder.reads
        assert all(len(ids) == 1 for _p, ids, _s, _e in recorder.reads)
        assert {p for p, *_ in recorder.reads} == {"5minute", "hour"}


class TestTheCutoff:
    """The synthetic world lies wholly inside the short-term cutoff, so these put it
    in the middle of a series."""

    SID = "sensor.effy_x"

    def _series(self) -> tuple[dict[str, dict[str, Any]], list[tuple[datetime, float]]]:
        hour = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
        slots = [(hour + SLOT * i, float(i)) for i in range(24)]  # two whole hours
        return {self.SID: {"unit": "kW", "slot_values": slots}}, slots

    async def test_slots_below_the_cutoff_are_not_compared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = StoredRecorder()
        _install_ha_api(monkeypatch, recorder)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
        monkeypatch.setattr(_history, "_get_statistics_units", REAL_UNITS)
        series, slots = self._series()
        recorder.units[self.SID] = "kW"
        for ts, value in slots:
            recorder.rows[("5minute", self.SID, ts)] = {
                "mean": value, "state": value, "min": None, "max": None, "sum": None,
                "last_reset": None,
            }  # fmt: skip
        cutoff = slots[16][0]

        await _history._mark_unchanged(
            object(),
            (series,),
            short_term_cutoff=cutoff,
            include_long_term=False,
            phases=_history._NO_TIMINGS,
        )

        assert series[self.SID]["unchanged_short"] == {ts for ts, _ in slots[16:]}
        assert [start for _p, _i, start, _e in recorder.reads] == [cutoff]

    def test_the_earliest_change_ignores_slots_the_cutoff_keeps_out(self) -> None:
        series, slots = self._series()
        hours = frozenset(_history._hourly_rows(slots))
        series[self.SID]["unchanged_short"] = frozenset(ts for ts, _ in slots[16:])
        series[self.SID]["unchanged_hourly"] = hours
        cutoff = slots[16][0]
        # slots 0..15 are below the cutoff: not written short-term, so not a change
        assert (
            _history._earliest_change(series, short_term_cutoff=cutoff, include_long_term=True)
            is None
        )

        series[self.SID]["unchanged_short"] = frozenset(ts for ts, _ in slots[17:])
        assert (
            _history._earliest_change(series, short_term_cutoff=cutoff, include_long_term=True)
            == slots[16][0]
        )

    def test_hourly_rows_are_not_a_change_for_a_run_that_does_not_write_them(self) -> None:
        series, slots = self._series()
        series[self.SID]["unchanged_short"] = frozenset(ts for ts, _ in slots)
        assert (
            _history._earliest_change(series, short_term_cutoff=None, include_long_term=False)
            is None
        )  # the slot job writes no hourly rows, so an absent hourly mark is not a change

    def test_a_changed_hourly_row_counts_from_the_first_slot_of_its_hour(self) -> None:
        series, slots = self._series()
        hours = sorted(_history._hourly_rows(slots))
        series[self.SID]["unchanged_short"] = frozenset(ts for ts, _ in slots)
        series[self.SID]["unchanged_hourly"] = frozenset(hours[:1])  # the second hour changed
        assert (
            _history._earliest_change(series, short_term_cutoff=None, include_long_term=True)
            == slots[12][0]
        )


class TestTheFailSafe:
    async def test_an_unreadable_store_means_everything_is_written_and_one_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        stored = _snapshot(recorder)
        imports_before = len(recorder.imports)
        recorder.read_error = RuntimeError("database is locked")

        with caplog.at_level(logging.WARNING, logger=_history.__name__):
            written, earliest, _t, _l = await _recalculate()

        assert written > 0 and earliest is not None  # as before this task: all of it
        assert len(recorder.imports) > imports_before
        assert _snapshot(recorder) == stored  # same values, rewritten
        warnings = [r for r in caplog.records if "stored statistics" in r.getMessage()]
        assert len(warnings) == 1  # one, not one per series

    async def test_a_missing_recorder_module_is_the_same_fail_safe(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _world_, recorder = _rig(monkeypatch)

        def not_importable(*_a: Any, **_k: Any) -> Any:
            raise ModuleNotFoundError("homeassistant.components.recorder.statistics")

        await _recalculate()  # a stored first run, so the reads would happen
        monkeypatch.setattr(_history, "_unchanged_slots", not_importable)
        with caplog.at_level(logging.WARNING, logger=_history.__name__):
            written, _earliest, _t, _l = await _recalculate()
        assert written > 0
        assert recorder.rows  # the run completed and wrote


class TestTheTimingRecord:
    async def test_the_reads_are_jobs_of_the_detail_block_and_the_first_line_counts_unchanged_rows(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _recalculate()
        record = [r for r in caplog.records if "recalculation timing (history)" in r.getMessage()]
        msg = record[-1].getMessage()
        first = msg.splitlines()[0]

        assert "rows written 0, rows unchanged " in first
        unchanged = int(first.split("rows unchanged ")[1].split(";")[0].split(",")[0])
        assert unchanged == sum(len(recorder.short(s)) for s in {k[1] for k in recorder.rows})
        jobs = [line.strip() for line in msg.splitlines() if "stored rows sensor." in line]
        assert len(jobs) == 5  # one per series: 2 effective, 2 derived power, 1 smoothed
        assert all("calls 1" in job for job in jobs)

    async def test_the_old_first_line_figures_are_still_there(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _world_, _recorder = _rig(monkeypatch)
        with caplog.at_level(logging.INFO, logger=_history.__name__):
            await _recalculate()
        first = (
            [r for r in caplog.records if "recalculation timing (history)" in r.getMessage()][0]
            .getMessage()
            .splitlines()[0]
        )
        for label in ("fetch", "compute", "write", "recorder queries", "raw states", "slots"):
            assert label in first
        assert "rows unchanged 0" in first  # a first run: nothing stored yet


def test_the_helpers_the_tests_rely_on_exist() -> None:
    # a typo-proof guard: these names are the contract of TASK-0030
    for name in ("_hourly_rows", "_unchanged_rows", "_mark_unchanged", "_earliest_change"):
        assert hasattr(_history, name), name
    assert {PV, OUT, PW}
