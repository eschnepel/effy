"""The debug export of a history recalculation (TASK-0037, ADR-025 Amendment 2026-11-03).

Three things are shown here. The file: what it holds, what it refuses, how it is bounded,
written and pruned. The recording: with the real history code and the real writer, the
file's cache section is exactly what a clean cache holds after the run, its rows are the
rows the writer scheduled, and its time and its failure are part of the timing record. The
round trip: a run replayed from the file alone (``tests/replay.py``, a storage that raises
on every read) writes the same rows.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.replay import assert_same, debug_export, replay, written_rows
from tests.support import FakeClock, FakeStorage, RaisingStorage, UnrecordedRead
from tests.test_history_cache import _Logging, _cache_over, _recorder_storage
from tests.test_history_phase_timing import NOW, OUT, PV, PW, _opts, _world
from tests.test_history_recent_equivalence import (
    _energy_world,
    _FrozenDatetime,
    _history,
    _install,
    _SensorStateClass,
    _ts,
    _World,
)
from tests.test_history_write_only_changed import (
    REAL_UNITS,
    REAL_WRITE,
    StoredRecorder,
    _install_ha_api,
)

UTC = timezone.utc
ENTRY = "01HZXYENTRYID"


# ---------------------------------------------------------------------------
# a small record, for the tests of the file itself
# ---------------------------------------------------------------------------


def _record(**kwargs: Any) -> Any:
    record = debug_export.ExportRecord()
    record.ran_at = kwargs.get("ran_at", datetime(2026, 11, 3, 12, 0, 7, 123456, tzinfo=UTC))
    record.window_start = record.ran_at - timedelta(days=28)
    record.note_reading_cache({"sensor.pv": (record.ran_at - timedelta(days=2), "100.5")})
    record.note_written(
        "sensor.effy_pv",
        "W",
        "5minute",
        [
            {
                "start": record.ran_at.replace(minute=0, second=0, microsecond=0),
                "mean": 1.5,
                "state": 1.5,
            }
        ],
    )
    record.result = {"rows_written": 1, "recalculated_from": None}
    return record


def _snapshot() -> dict[str, Any]:
    return {"version": 1, "raw": {"sensor.pv": {"lo": "x", "events": [["t", "1.0"]]}}, "stats": {}}


def _encode(**kwargs: Any) -> tuple[bytes, list[str]]:
    encoded: tuple[bytes, list[str]] = debug_export.encode(
        kwargs.pop("record", None) or _record(),
        config=kwargs.pop("config", {"pv_input_sensors": ["sensor.pv"]}),
        cache=kwargs.pop("cache", _snapshot()),
        meta=kwargs.pop("meta", {"effy_version": "1.0.0", "ha_version": "2026.10.4"}),
        **kwargs,
    )
    return encoded


class TestTheFile:
    def test_it_is_indented_plain_json_with_every_section_and_a_format_version(self) -> None:
        payload, omitted = _encode()
        assert payload[:2] != b"\x1f\x8b" and omitted == []
        text = payload.decode("utf-8")
        assert text.startswith('{\n  "format_version": 1,\n  "meta": {\n    "')
        assert text.endswith("\n}\n")
        document = json.loads(text)
        assert document["format_version"] == debug_export.SCHEMA_VERSION == 1
        assert list(document) == [
            "format_version",
            "meta",
            "config",
            "inputs",
            "result",
            "cache",
            "written",
        ]
        assert document["meta"]["effy_version"] == "1.0.0"
        assert document["meta"]["omitted"] == []
        assert set(document["meta"]["section_bytes"]) == {
            "config",
            "inputs",
            "result",
            "cache",
            "written",
        }

    def test_loads_gives_back_what_was_put_in(self) -> None:
        document = debug_export.loads(_encode()[0])
        assert document["config"] == {"pv_input_sensors": ["sensor.pv"]}
        assert document["cache"] == _snapshot()
        assert document["result"] == {"rows_written": 1, "recalculated_from": None}
        assert document["inputs"]["energy_reading_cache"] == {
            "sensor.pv": ["2026-11-01T12:00:07.123456+00:00", "100.5"]
        }

    def test_datetimes_are_utc_with_their_offset_and_keep_their_microseconds(self) -> None:
        paris = timezone(timedelta(hours=2))
        record = _record(ran_at=datetime(2026, 11, 3, 14, 0, 7, 123456, tzinfo=paris))
        document = debug_export.loads(_encode(record=record)[0])
        assert document["inputs"]["ran_at"] == "2026-11-03T12:00:07.123456+00:00"
        assert datetime.fromisoformat(document["inputs"]["ran_at"]) == record.ran_at
        assert document["written"][0]["rows"][0]["start"] == "2026-11-03T12:00:00+00:00"

    def test_a_gzip_file_of_an_older_build_is_read_too(self) -> None:
        raw = gzip.compress(_encode()[0])
        assert debug_export.loads(raw)["config"] == {"pv_input_sensors": ["sensor.pv"]}

    def test_a_run_that_did_not_note_its_time_cannot_be_exported(self) -> None:
        with pytest.raises(debug_export.ExportError, match="did not note"):
            _encode(record=debug_export.ExportRecord())


class TestTheFileIsRefusedWhenItCannotBeReplayed:
    def _tampered(self, **changes: Any) -> bytes:
        document = json.loads(_encode()[0])
        for key, value in changes.items():
            if value is None:
                del document[key]
            else:
                document[key] = value
        return json.dumps(document).encode()

    def test_another_format_version_is_refused_with_both_versions_named(self) -> None:
        with pytest.raises(debug_export.ExportError, match=r"format version 2.*reads 1"):
            debug_export.loads(self._tampered(format_version=2))

    def test_a_file_without_a_version_is_refused(self) -> None:
        with pytest.raises(debug_export.ExportError, match="format version None"):
            debug_export.loads(self._tampered(format_version=None))

    def test_a_missing_section_is_named(self) -> None:
        for section in ("config", "inputs", "cache", "meta"):
            with pytest.raises(debug_export.ExportError, match=section):
                debug_export.loads(self._tampered(**{section: None}))

    def test_damaged_files_are_refused_not_half_read(self) -> None:
        payload = _encode()[0]
        for damaged in (
            payload[: len(payload) // 2],  # cut short
            payload[:20] + b"\x00" * 8 + payload[28:],  # a block of garbage
            b"not json at all",
            b"",
            gzip.compress(b"[1, 2, 3]"),  # json, but not an object
            gzip.compress(b'{"format_version": 1, "meta": '),  # cut-off json
            gzip.compress(b"\xff\xfe\xfd"),  # not utf-8
        ):
            with pytest.raises(debug_export.ExportError, match="damaged"):
                debug_export.loads(damaged)


class TestTheSizeBound:
    def test_a_section_over_the_bound_is_left_out_and_said_so_never_cut(self) -> None:
        full = debug_export.loads(_encode()[0])["meta"]["section_bytes"]
        small = sum(full[name] for name in ("config", "inputs", "result"))
        payload, omitted = _encode(max_bytes=small + full["cache"] + 1)
        document = debug_export.loads(payload)
        assert omitted == ["written"] and document["meta"]["omitted"] == ["written"]
        assert "written" not in document and document["cache"] == _snapshot()
        assert document["meta"]["section_bytes"] == full  # what it would have been
        assert document["meta"]["max_bytes"] == small + full["cache"] + 1

    def test_the_cache_goes_first_and_then_the_file_cannot_be_replayed(self) -> None:
        payload, omitted = _encode(max_bytes=10)
        assert omitted == ["cache", "written"]
        with pytest.raises(debug_export.ExportError, match=r"no 'cache' section.*left out.*cache"):
            debug_export.loads(payload)

    def test_the_small_sections_are_always_in(self) -> None:
        raw = json.loads(_encode(max_bytes=10)[0])
        assert {"config", "inputs", "result", "meta"} <= set(raw)
        assert raw["meta"]["omitted"] == ["cache", "written"]


class TestWhereTheFilesGo:
    def test_the_name_is_the_utc_time_of_the_run_and_the_entry(self) -> None:
        moment = datetime(2026, 11, 3, 14, 5, 9, tzinfo=timezone(timedelta(hours=2)))
        assert debug_export.file_name(ENTRY, moment) == "effy-export-20261103T120509Z-01HZXYEN.json"
        assert debug_export.file_name("a-b_c!", moment).endswith("-abc.json")
        assert debug_export.file_name("---", moment).endswith("-entry.json")

    def test_the_oldest_past_the_limit_go_and_nothing_else_is_ever_named(self) -> None:
        mine = [f"effy-export-2026110{d}T120000Z-01HZXYEN.json" for d in range(1, 8)]
        other_entry = ["effy-export-20261101T120000Z-OTHER123.json"]
        foreign = [
            "notes.txt",
            "effy-export-garbage.json",
            "effy-export-20261101T120000Z-01HZXYEN.json.tmp",
            "x-effy-export-20261101T120000Z-01HZXYEN.json",
            "effy-export-20261101T120000Z-01HZXYEN.json.bak",
        ]
        doomed = debug_export.files_to_remove([*foreign, *other_entry, *reversed(mine)], ENTRY, 3)
        assert doomed == mine[:4]
        assert debug_export.files_to_remove(mine, ENTRY, 10) == []
        assert debug_export.files_to_remove(mine, ENTRY, 0) == mine

    def test_a_directory_with_other_files_keeps_them_and_the_newest_exports(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "notes.txt").write_text("keep me")
        (tmp_path / "effy-export-20260101T000000Z-OTHER123.json").write_bytes(b"x")
        for day in (1, 2, 3, 4):
            record = _record(ran_at=datetime(2026, 11, day, 12, 0, tzinfo=UTC))
            debug_export.export_to_file(
                tmp_path, ENTRY, record, config={}, cache=_snapshot(), meta={}, keep=2
            )
        names = sorted(os.listdir(tmp_path))
        assert names == [
            "effy-export-20260101T000000Z-OTHER123.json",
            "effy-export-20261103T120000Z-01HZXYEN.json",
            "effy-export-20261104T120000Z-01HZXYEN.json",
            "notes.txt",
        ]
        assert (tmp_path / "notes.txt").read_text() == "keep me"

    def test_the_directory_is_created_and_the_outcome_says_where_and_how_large(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "effy_debug"
        outcome = debug_export.export_to_file(
            target, ENTRY, _record(), config={}, cache=_snapshot(), meta={}
        )
        path = Path(outcome.path)
        assert path.parent == target and path.exists()
        assert outcome.size == path.stat().st_size > 0
        assert outcome.omitted == ()
        assert debug_export.loads(path.read_bytes())["cache"] == _snapshot()

    def test_a_write_that_fails_leaves_no_file_not_even_a_half_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*_args: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(debug_export.os, "replace", broken)
        with pytest.raises(OSError, match="disk full"):
            debug_export.export_to_file(
                tmp_path, ENTRY, _record(), config={}, cache=_snapshot(), meta={}
            )
        assert os.listdir(tmp_path) == []

    def test_a_file_that_cannot_be_removed_is_a_warning_not_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        old = tmp_path / "effy-export-20260101T000000Z-01HZXYEN.json"
        old.write_bytes(b"x")
        original = Path.unlink

        def unlink(self: Path, *args: Any, **kwargs: Any) -> None:
            if self == old:
                raise PermissionError("in use")
            original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink)
        with caplog.at_level(logging.WARNING):
            outcome = debug_export.export_to_file(
                tmp_path, ENTRY, _record(), config={}, cache=_snapshot(), meta={}, keep=1
            )
        assert Path(outcome.path).exists()
        assert any("could not remove" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# the real history code and the real writer
# ---------------------------------------------------------------------------


def _rig(monkeypatch: pytest.MonkeyPatch, world: _World) -> StoredRecorder:
    """The in-memory world, the real writer and read, and a stateful recorder."""
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
    return recorder


def _day(day: int, hour: int, minute: int = 0) -> datetime:
    return _ts(hour, minute, day=day)


def _offline_world() -> _World:
    """An energy counter that was offline when the window opened and came back (an anchor,
    the strings ``unavailable`` and ``unknown``), and a power sensor with a gap."""
    readings = [(_day(5, 6), "100.0"), (_day(5, 7), "unavailable"), (_day(5, 14), "101.0")]
    start = _day(5, 15)
    readings += [(start + timedelta(hours=i), f"{101 + 0.25 * (i + 1):.2f}") for i in range(116)]
    out = [(_day(5, 8) + timedelta(hours=2 * i), f"{50 + 0.3 * i:.2f}") for i in range(60)]
    out[30] = (out[30][0], "unknown")
    world = _energy_world(**{PV: readings, OUT: out})
    first = _day(5, 12)
    world.power_means[PW] = {
        first + timedelta(minutes=5 * i): 300.0 + (i % 17)
        for i in range(1430)
        if i not in (700, 701, 702)
    }
    world.units[PW] = "W"
    world.state_classes[PW] = _SensorStateClass.MEASUREMENT
    return world


class _Exported:
    def __init__(self) -> None:
        self.document: dict[str, Any] = {}
        self.outcome: Any = None
        self.snapshot: dict[str, Any] = {}
        self.result: Any = None
        self.recorder: StoredRecorder = StoredRecorder()
        self.cache: Any = None
        self.inner: Any = None


async def _exported_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    world: _World | None = None,
    *,
    reading_cache: dict[str, tuple[datetime, str]] | None = None,
    prepare: Any = None,
) -> _Exported:
    """The button's history recalculation, from a clean cache, with the export switched on."""
    done = _Exported()
    done.recorder = _rig(monkeypatch, world or _world())
    if prepare is not None:
        await prepare(done.recorder)
    done.inner = _Logging(_recorder_storage())
    done.cache = _cache_over(done.inner)
    done.cache.clear()
    options = _opts()

    async def hook(record: Any) -> Any:
        done.snapshot = done.cache.snapshot()
        done.outcome = debug_export.export_to_file(
            tmp_path, ENTRY, record, config=options, cache=done.snapshot, meta={"effy_version": "t"}
        )
        return done.outcome

    _FrozenDatetime.frozen = NOW
    done.result = await _history.async_recalculate_history(
        object(), options, energy_reading_cache=reading_cache, storage=done.cache, export=hook
    )
    done.document = debug_export.loads(Path(done.outcome.path).read_bytes())
    return done


class TestTheRecordingOfARun:
    @pytest.mark.asyncio
    async def test_the_file_has_every_section_and_what_the_run_took_for_now(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        done = await _exported_run(monkeypatch, tmp_path)
        document = done.document
        assert document["inputs"]["ran_at"] == NOW.isoformat()
        assert document["inputs"]["window_start"] == (NOW - timedelta(days=5)).isoformat()
        assert document["config"] == _opts()
        assert document["result"]["rows_written"] == done.result[0] > 0
        assert document["meta"]["omitted"] == []
        assert document["written"], "the rows the writer scheduled"

    @pytest.mark.asyncio
    async def test_the_cache_section_is_exactly_the_snapshot_of_a_clean_cache_after_the_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        done = await _exported_run(monkeypatch, tmp_path)
        assert done.document["cache"] == json.loads(json.dumps(done.snapshot))
        # and it holds what the run asked the recorder for, entity by entity, nothing more
        raw = [c for c in done.inner.calls if c[0] == "raw_states"]
        stats = [c for c in done.inner.calls if c[0] == "statistics"]
        assert set(done.document["cache"]["raw"]) == {c[1] for c in raw} == {PV, OUT}
        assert set(done.document["cache"]["stats"]) == {sid for c in stats for sid in c[1]} == {PW}
        observed = done.document["cache"]["observed"]
        assert set(observed["units"]) == {PV, OUT, PW}
        assert observed["unchanged_rows"], "the stored rows that decided what was written"
        assert observed["retention_days"] == 10

    @pytest.mark.asyncio
    async def test_the_written_section_is_what_the_writer_scheduled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        done = await _exported_run(monkeypatch, tmp_path)
        stored = {
            key: {"mean": row["mean"], "state": row["state"]}
            for key, row in done.recorder.rows.items()
        }
        assert written_rows(done.document) == stored
        tables = {imported["table"] for imported in done.document["written"]}
        assert tables == {"5minute", "hour"}

    @pytest.mark.asyncio
    async def test_a_run_without_the_hook_records_nothing_and_leaves_no_record_behind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _rig(monkeypatch, _world())
        await _history.async_recalculate_history(object(), _opts())
        assert recorder.rows
        assert _history._active_export.get() is None

    @pytest.mark.asyncio
    async def test_the_run_is_the_same_with_and_without_the_export(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        exported = await _exported_run(monkeypatch, tmp_path)
        plain = _rig(monkeypatch, _world())
        result = await _history.async_recalculate_history(
            object(), _opts(), storage=_cache_over(_recorder_storage())
        )
        assert result == exported.result and plain.rows == exported.recorder.rows

    @pytest.mark.asyncio
    async def test_the_timing_record_says_where_how_large_and_how_long(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO):
            done = await _exported_run(monkeypatch, tmp_path)
        lines = [r.getMessage() for r in caplog.records if "timing (history)" in r.getMessage()]
        assert len(lines) == 1, "a part of the timing record, not a second line"
        assert f"debug export {done.outcome.size / 1024:.0f} KiB in " in lines[0]
        assert f" s -> {done.outcome.path}" in lines[0]
        assert "left out" not in lines[0]

    @pytest.mark.asyncio
    async def test_a_section_left_out_for_size_is_said_in_the_log_and_in_the_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorder = _rig(monkeypatch, _world())
        cache = _cache_over(_recorder_storage())
        cache.clear()

        async def hook(record: Any) -> Any:
            return debug_export.export_to_file(
                tmp_path,
                ENTRY,
                record,
                config={},
                cache=cache.snapshot(),
                meta={},
                max_bytes=2_000,
            )

        with caplog.at_level(logging.INFO):
            await _history.async_recalculate_history(object(), _opts(), storage=cache, export=hook)
        assert recorder.rows
        [path] = list(tmp_path.iterdir())
        raw = json.loads(path.read_bytes())
        assert raw["meta"]["omitted"], raw["meta"]
        line = next(r.getMessage() for r in caplog.records if "timing (history)" in r.getMessage())
        assert f"left out for size: {', '.join(raw['meta']['omitted'])}" in line


class TestAFailingExportNeverFailsTheRecalculation:
    @pytest.mark.asyncio
    async def test_the_result_the_rows_and_the_cache_are_unchanged_and_it_is_logged_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        reference = _rig(monkeypatch, _world())
        expected = await _history.async_recalculate_history(
            object(), _opts(), storage=_cache_over(_recorder_storage())
        )
        recorder = _rig(monkeypatch, _world())
        cache = _cache_over(_recorder_storage())

        async def broken(_record: Any) -> Any:
            raise OSError("disk full")

        with caplog.at_level(logging.INFO):
            result = await _history.async_recalculate_history(
                object(), _opts(), storage=cache, export=broken
            )
        assert result == expected and recorder.rows == reference.rows
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "disk full" in warnings[0].getMessage()
        line = next(r.getMessage() for r in caplog.records if "timing (history)" in r.getMessage())
        assert "debug export failed" in line and "disk full" in line
        assert _history._active_export.get() is None
        # the cache is as usable as after any run: it still holds what the run read
        assert set(cache.snapshot()["raw"]) == {PV, OUT}
        again = await _history.async_recalculate_history(object(), _opts(), storage=cache)
        assert again[2] == expected[2]

    @pytest.mark.asyncio
    async def test_a_hook_that_is_cancelled_is_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _rig(monkeypatch, _world())

        async def cancelled(_record: Any) -> Any:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await _history.async_recalculate_history(
                object(), _opts(), storage=_cache_over(_recorder_storage()), export=cancelled
            )
        assert _history._active_export.get() is None


class TestTheRoundTrip:
    """A run replayed from its file alone writes what the original wrote."""

    @pytest.mark.asyncio
    async def test_an_energy_and_a_power_sensor_with_a_gap(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        done = await _exported_run(monkeypatch, tmp_path)
        replayed = await replay(done.document, monkeypatch)
        assert_same(replayed)
        assert replayed.result[2:] == done.result[2:]  # the same touched entities and values

    @pytest.mark.asyncio
    async def test_an_offline_gap_with_an_anchor_and_unavailable_and_unknown_states(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        done = await _exported_run(monkeypatch, tmp_path, _offline_world())
        cache = done.document["cache"]
        assert [a["entity"] for a in cache["anchors"]] == [PV] and cache["anchors"][0]["found"]
        states = set()
        for series in cache["raw"].values():
            states |= {state for _t, state in series["events"]}
            states |= {series["base"][0]} if series["base"] else set()
        assert {"unavailable", "unknown"} <= states
        replayed = await replay(done.document, monkeypatch)
        assert_same(replayed)
        assert len(replayed.expected) > 1000

    @pytest.mark.asyncio
    async def test_the_reading_cache_the_run_started_with_is_part_of_the_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With the anchor in the coordinator's reading cache the recorder is not asked for
        it: the file must hold that cache, or the replay would ask and fail."""
        readings = {PV: (_day(5, 6), "100.0")}
        done = await _exported_run(
            monkeypatch, tmp_path, _offline_world(), reading_cache=dict(readings)
        )
        assert done.document["cache"]["anchors"] == []
        assert done.document["inputs"]["energy_reading_cache"][PV] == [
            _day(5, 6).isoformat(),
            "100.0",
        ]
        assert_same(await replay(done.document, monkeypatch))
        without = json.loads(json.dumps(done.document))
        without["inputs"]["energy_reading_cache"] = {}
        with pytest.raises(UnrecordedRead):
            await replay(without, monkeypatch)

    @pytest.mark.asyncio
    async def test_the_stored_rows_decide_what_is_written_and_the_file_has_them(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def first_press_then_lose_some_rows(recorder: StoredRecorder) -> None:
            await _history.async_recalculate_history(object(), _opts())
            lost = [
                k
                for k in recorder.rows
                if k[0] == "5minute" and k[1] == PV.replace("sensor.", "sensor.effy_")
            ]
            for key in lost[::9]:
                del recorder.rows[key]

        done = await _exported_run(monkeypatch, tmp_path, prepare=first_press_then_lose_some_rows)
        everything = await _exported_run(monkeypatch, tmp_path / "all")
        assert 0 < len(written_rows(done.document)) < len(written_rows(everything.document))
        replayed = await replay(done.document, monkeypatch)
        assert_same(replayed)
        assert replayed.recorder.rows.keys() == written_rows(done.document).keys()

    @pytest.mark.asyncio
    async def test_a_file_that_went_through_the_disk_replays_the_same(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        done = await _exported_run(monkeypatch, tmp_path)
        from_disk = debug_export.loads(Path(done.outcome.path).read_bytes())
        assert from_disk == done.document
        assert_same(await replay(from_disk, monkeypatch))

    @pytest.mark.asyncio
    async def test_a_read_the_file_does_not_contain_fails_instead_of_being_answered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        done = await _exported_run(monkeypatch, tmp_path)
        damaged = json.loads(json.dumps(done.document))
        damaged["cache"]["raw"].pop(PV)
        with pytest.raises(UnrecordedRead):
            await replay(damaged, monkeypatch)

    @pytest.mark.asyncio
    async def test_the_replay_notices_a_changed_row(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        done = await _exported_run(monkeypatch, tmp_path)
        changed = json.loads(json.dumps(done.document))
        changed["written"][0]["rows"][0]["mean"] += 1.0
        with pytest.raises(AssertionError):
            assert_same(await replay(changed, monkeypatch))

    def test_a_tz_aware_row_key_does_not_depend_on_the_tzinfo_object(self) -> None:
        paris = timezone(timedelta(hours=2))
        instant = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
        as_utc = _history.Cache._rows_key("s", "W", [(instant, 1.5)], {instant: (1.5, 1.5)})
        as_paris = _history.Cache._rows_key(
            "s", "W", [(instant.astimezone(paris), 1.5)], {instant.astimezone(paris): (1.5, 1.5)}
        )
        assert as_utc == as_paris

    @pytest.mark.asyncio
    async def test_the_capacity_entity_survives_the_file(self) -> None:
        clock = FakeClock(NOW)
        fake = FakeStorage(clock)
        fake.add_capacity("number.cap", NOW - timedelta(days=3), "10.0", "kWh")
        fake.add_capacity("number.cap", NOW - timedelta(days=1), "9.5", "kWh")
        fake.add_capacity("number.cap", NOW - timedelta(hours=2), "unavailable", None)
        cache = _history.Cache(fake, clock=clock, settle=timedelta(seconds=30))
        start = NOW - timedelta(days=2)
        recorded = await cache.capacity_states("number.cap", start, NOW)
        assert [state for _t, state, _u in recorded] == ["10.0", "9.5", "unavailable"]
        payload, _ = debug_export.encode(_record(), config={}, cache=cache.snapshot(), meta={})
        document = debug_export.loads(payload)
        replay_cache = _history.Cache.from_snapshot(
            document["cache"], RaisingStorage(), clock=clock, settle=timedelta(seconds=30)
        )
        assert await replay_cache.capacity_states("number.cap", start, NOW) == recorded


class TestTheSlotJobsProbeDoesNotChangeWhatTheFileHolds:
    """The probe reads outside the recorder-job lock, so it can fill the cache between the
    clear and the dump. It reads two slots of the power sources the history run reads over
    the whole window anyway, and a cache merges both into one span: the snapshot is the same."""

    @pytest.mark.asyncio
    async def test_a_probe_after_the_clear_and_before_the_run_leaves_the_snapshot_as_it_is(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        clean = await _exported_run(monkeypatch, tmp_path / "clean")

        _rig(monkeypatch, _world())
        cache = _cache_over(_recorder_storage())
        cache.clear()
        assert await _history.async_probe_last_slot_ready(object(), _opts(), NOW, storage=cache)
        assert cache.snapshot()["stats"], "the probe did read through the cache"
        taken: dict[str, Any] = {}

        async def hook(record: Any) -> Any:
            taken["snapshot"] = cache.snapshot()
            return debug_export.export_to_file(
                tmp_path / "probed", ENTRY, record, config=_opts(), cache=taken["snapshot"], meta={}
            )

        await _history.async_recalculate_history(object(), _opts(), storage=cache, export=hook)
        assert taken["snapshot"] == clean.snapshot
