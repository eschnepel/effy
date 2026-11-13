"""Realistic branches of ``cache.py`` the other cache tests leave out.

Loaded by file path (ADR-000 §6) over the real-rule fake storage of ``tests.support``.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.support import FakeClock, FakeStorage

_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "cache.py"
_spec = importlib.util.spec_from_file_location("effy_cache_branches", _PATH)
assert _spec is not None and _spec.loader is not None
cache_mod: Any = importlib.util.module_from_spec(_spec)
sys.modules["effy_cache_branches"] = cache_mod
_spec.loader.exec_module(cache_mod)

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
FIVE = timedelta(minutes=5)
SID = "sensor.a"


class _EndInclusiveStorage(FakeStorage):
    """A storage whose ``end`` is inclusive: it also returns the row starting at ``end``."""

    async def statistics(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        self.calls.append(("statistics", tuple(entity_ids), start, end))
        return self.direct_statistics(entity_ids, start, end + timedelta(microseconds=1))


class TestATailReadReplacesWhatLiesAtItsStart:
    @pytest.mark.asyncio
    async def test_a_row_held_at_the_seam_is_replaced_not_duplicated(self) -> None:
        clock = FakeClock(T0 + timedelta(hours=3))
        storage = _EndInclusiveStorage(clock)
        for i in range(12):
            storage.add_row(SID, T0 + FIVE * i, float(i))
        cache = cache_mod.Cache(storage, clock=clock, settle=timedelta(seconds=30))

        first = await cache.statistics([SID], T0, T0 + FIVE * 6)
        # the first read held the row at the end, which the read itself excludes
        assert [row["start"] for row in first[SID]] == [T0 + FIVE * i for i in range(6)]

        storage.add_row(SID, T0 + FIVE * 6, 66.0)  # recompiled with another value
        second = await cache.statistics([SID], T0, T0 + FIVE * 9)

        starts = [row["start"] for row in second[SID]]
        assert starts == [T0 + FIVE * i for i in range(9)]
        assert second[SID][6]["mean"] == 66.0
        assert storage.calls[-1] == ("statistics", (SID,), T0 + FIVE * 6, T0 + FIVE * 9)
