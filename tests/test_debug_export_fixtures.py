"""Replay every stored debug-export fixture with the real code (TASK-0037), after shady's
``test_csv_regression_fixtures.py``.

Every ``*.json.gz`` in ``tests/fixtures/debug_export/curated`` (real exports, anonymized by
``utils/import_debug_export.py``) and ``synthetic`` (made by the developers, to test the test
system) becomes one test case; no code change per fixture. Zero fixtures is zero cases.

A case replays what the fixture holds with nothing else to read: the history recalculation
(the rows it wrote and its result) and the standby, efficiency and battery rebuilds, and
compares each with what the fixture stored.

**The optional ``meta.fixture.expected``** is ``PASS`` (the default) or ``FAIL``: a fixture
that must be rejected. The case then passes only if the replay disagrees with it.

**When a ``PASS`` fixture starts failing "correctly"** (the calculation changed on purpose),
the failing case leaves a copy with the regenerated rebuild results at
``_generated/<folder>/<same name>`` (gitignored); copy it over the stale fixture. Only the
rebuild results are regenerated; a difference in the history rows is reported and never
rewritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fixture_support import (
    GENERATED_DIR,
    compare_values,
    dumps,
    expected_outcome,
    fixture_files,
    with_rebuilds,
)
from tests.replay import assert_same, debug_export, replay, replay_rebuilds


async def evaluate_fixture(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, list[str], dict[str, Any], dict[str, Any]]:
    """Replay one fixture: ``(expected, problems, document, regenerated rebuilds)``."""
    document = debug_export.loads(path.read_bytes())
    expected = expected_outcome(document, str(path))
    problems: list[str] = []
    if not document["meta"].get("fixture", {}).get("anonymized"):
        # an anonymized fixture has no unchanged_rows to answer the writer's reads from
        replayed = await replay(document, monkeypatch)
        try:
            assert_same(replayed, require_rows=bool(replayed.expected))
        except AssertionError as err:
            problems.append(f"history recalculation: {err}")
    rebuilds = await replay_rebuilds(document, monkeypatch)
    problems += [
        f"rebuilds{problem}"
        for problem in compare_values(document["result"].get("rebuilds"), rebuilds)
    ]
    return expected, problems, document, rebuilds


async def check_fixture(
    path: Path, monkeypatch: pytest.MonkeyPatch, generated_dir: Path = GENERATED_DIR
) -> None:
    """Pass or fail for one fixture, honouring its ``expected``."""
    expected, problems, document, rebuilds = await evaluate_fixture(path, monkeypatch)
    if expected == "FAIL":
        assert problems, f"{path.name}: marked FAIL, but the replay agrees with it"
        return
    if problems:
        target = generated_dir / path.parent.name / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dumps(with_rebuilds(document, rebuilds)))
        pytest.fail(
            f"{path.name} no longer replays as stored (regenerated rebuilds at {target}):\n  "
            + "\n  ".join(problems[:20])
        )


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
@pytest.mark.asyncio
async def test_the_fixture_replays_as_stored(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await check_fixture(path, monkeypatch)
