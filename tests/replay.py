"""Replay a debug export offline (TASK-0037, ADR-025 Amendment 2026-11-03).

An export (``custom_components/effy/debug_export.py``) holds Effy's configuration, the
instant the history recalculation took for "now", and everything it read. This runs the
REAL ``async_recalculate_history`` and the REAL writer over it, with no recorder world:
the cache is rebuilt from the file over a storage that raises on every read, so a read the
file does not contain fails (``UnrecordedRead``) instead of being answered from somewhere.

Used by ``tests/test_debug_export.py``; whoever has an export file can use it the same way:

    document = debug_export.loads(Path("effy-export-....json.gz").read_bytes())
    replayed = await replay(document, monkeypatch)
    assert_same(replayed)
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.support import RaisingStorage
from tests.test_history_recent_equivalence import _FrozenDatetime, _history
from tests.test_history_write_only_changed import REAL_WRITE, StoredRecorder, _install_ha_api

_BASE = Path(__file__).resolve().parent.parent / "custom_components" / "effy"


def _load_pure(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"effy_replay_{name}", _BASE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


debug_export = _load_pure("debug_export")

Key = tuple[str, str, datetime]


@dataclass
class Replayed:
    result: tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]
    recorder: StoredRecorder
    expected: dict[Key, dict[str, float]]
    actual: dict[Key, dict[str, float]]
    recorded_result: dict[str, Any]


def written_rows(document: dict[str, Any]) -> dict[Key, dict[str, float]]:
    """The rows the recorded run scheduled, keyed like the stored recorder's rows."""
    rows: dict[Key, dict[str, float]] = {}
    for imported in document["written"]:
        for row in imported["rows"]:
            key = (
                imported["table"],
                imported["statistic_id"],
                datetime.fromisoformat(row["start"]),
            )
            rows[key] = {"mean": row["mean"], "state": row["state"]}
    return rows


async def replay(document: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Replayed:
    """Run the history recalculation again from ``document`` alone."""
    inputs = document["inputs"]
    ran_at = datetime.fromisoformat(inputs["ran_at"])
    _FrozenDatetime.frozen = ran_at
    recorder = StoredRecorder()
    _install_ha_api(monkeypatch, recorder)
    monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
    monkeypatch.setattr(_history, "_write_recorder_statistics", REAL_WRITE)
    monkeypatch.setattr(_history, "datetime", _FrozenDatetime)
    cache = _history.Cache.from_snapshot(
        document["cache"],
        RaisingStorage(),
        clock=lambda: ran_at,
        settle=_history.RECORDER_SETTLE,
    )
    readings = {
        entity: (datetime.fromisoformat(ts), state)
        for entity, (ts, state) in inputs["energy_reading_cache"].items()
    }
    result = await _history.async_recalculate_history(
        object(), document["config"], energy_reading_cache=readings, storage=cache
    )
    actual = {
        key: {"mean": row["mean"], "state": row["state"]} for key, row in recorder.rows.items()
    }
    return Replayed(
        result=result,
        recorder=recorder,
        expected=written_rows(document),
        actual=actual,
        recorded_result=document["result"],
    )


def assert_same(replayed: Replayed) -> None:
    """The same keys exactly and the values within ADR-022's tolerance; the same result."""
    assert set(replayed.actual) == set(replayed.expected)
    assert replayed.expected, "the recorded run wrote something"
    for key, row in replayed.expected.items():
        assert replayed.actual[key]["mean"] == pytest.approx(row["mean"], rel=1e-9, abs=1e-9), key
        assert replayed.actual[key]["state"] == pytest.approx(row["state"], rel=1e-9, abs=1e-9), key
    written, earliest, _touched, _last = replayed.result
    assert written == replayed.recorded_result["rows_written"]
    recorded_from = replayed.recorded_result["recalculated_from"]
    assert (earliest.isoformat() if earliest is not None else None) == recorded_from
