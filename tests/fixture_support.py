"""Fixture support for the debug-export replay (TASK-0037), after shady's
``csv_fixture_support.py``: no test, no Home Assistant, only the generic parts.

A fixture is an anonymized debug export (``utils/import_debug_export.py``) stored gzip-compressed
under ``tests/fixtures/debug_export/``. ``tests/test_debug_export_fixtures.py`` replays it with
the real code and compares what comes out with what the fixture stored; this module holds
the comparison and the file handling, so the runner stays short.

Non-test-prefixed on purpose, so pytest never collects it.
"""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path
from typing import Any

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "debug_export"
FIXTURE_DIRS = (FIXTURES_ROOT / "curated", FIXTURES_ROOT / "synthetic")
GENERATED_DIR = FIXTURES_ROOT / "_generated"
EXPECTED = ("PASS", "FAIL")


def fixture_files() -> list[Path]:
    """Every ``*.json.gz`` of the fixture folders; no folder or no file is no case."""
    return sorted(p for d in FIXTURE_DIRS if d.is_dir() for p in d.glob("*.json.gz"))


def expected_outcome(document: dict[str, Any], source: str) -> str:
    """``PASS`` (the default) or ``FAIL``: a fixture that must be rejected, to test the test."""
    expected = str(document.get("meta", {}).get("fixture", {}).get("expected", "PASS")).upper()
    if expected not in EXPECTED:
        raise ValueError(f"{source}: fixture 'expected' must be PASS or FAIL, got {expected!r}")
    return expected


def compare_values(
    stored: Any, regenerated: Any, rel_tol: float = 1e-9, path: str = ""
) -> list[str]:
    """Human-readable differences between two JSON values, empty if none.

    Numbers compare with a relative tolerance, everything else exactly; nothing is special-cased
    by name, so a new field in a summary needs no change here.
    """
    here = path or "<root>"
    if isinstance(stored, dict) and isinstance(regenerated, dict):
        problems: list[str] = []
        for key in sorted(set(stored) | set(regenerated)):
            if key not in stored:
                problems.append(f"{path}/{key}: present in the regenerated result only")
            elif key not in regenerated:
                problems.append(f"{path}/{key}: present in the fixture only")
            else:
                problems += compare_values(stored[key], regenerated[key], rel_tol, f"{path}/{key}")
        return problems
    if isinstance(stored, list) and isinstance(regenerated, list):
        if len(stored) != len(regenerated):
            return [f"{here}: length differs ({len(stored)} != {len(regenerated)})"]
        return [
            problem
            for i, (a, b) in enumerate(zip(stored, regenerated, strict=True))
            for problem in compare_values(a, b, rel_tol, f"{path}[{i}]")
        ]
    numbers = (int, float)
    if isinstance(stored, bool) or isinstance(regenerated, bool):
        return (
            []
            if type(stored) is type(regenerated) and stored == regenerated
            else [f"{here}: {stored!r} != {regenerated!r}"]
        )
    if isinstance(stored, numbers) and isinstance(regenerated, numbers):
        if math.isclose(stored, regenerated, rel_tol=rel_tol, abs_tol=1e-12):
            return []
        return [f"{here}: {stored!r} != {regenerated!r}"]
    return [] if stored == regenerated else [f"{here}: {stored!r} != {regenerated!r}"]


def with_rebuilds(document: dict[str, Any], rebuilds: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``document`` whose recorded rebuild results are ``rebuilds``."""
    copy: dict[str, Any] = json.loads(json.dumps(document))
    copy["result"]["rebuilds"] = rebuilds
    return copy


def dumps(document: dict[str, Any]) -> bytes:
    """The bytes a fixture is stored as (the same encoding as ``utils/import_debug_export.py``)."""
    text = json.dumps(document, separators=(",", ":"), ensure_ascii=True)
    return gzip.compress(text.encode("ascii"), compresslevel=9, mtime=0)
