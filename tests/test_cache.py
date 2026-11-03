"""The cache is a ``Storage`` that can stand for any other ``Storage`` (TASK-0036, ADR-025).

``cache.py`` has no Home Assistant dependency, so it is loaded by file path (ADR-000 §6)
and tested without mocks: the inner storage is a real implementation of the recorder's
rule over lists (``tests.support.FakeStorage``) that commits late, as the recorder does.

The central test is model-based: for random worlds, random reads and a clock that
moves, ``Cache(S)`` returns what ``S`` returns at that moment. The others pin what the
model cannot say: that only what is missing is read, what a snapshot holds, that a
state or slot the recorder has not committed is not stored as an answer, and that a
cache loaded from a snapshot over a storage that raises replays a recording and
refuses a read it does not contain.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.support import FakeClock, FakeStorage, RaisingStorage, UnrecordedRead

_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "cache.py"
_spec = importlib.util.spec_from_file_location("effy_cache", _PATH)
assert _spec is not None and _spec.loader is not None
cache_mod = importlib.util.module_from_spec(_spec)
sys.modules["effy_cache"] = cache_mod
_spec.loader.exec_module(cache_mod)
Cache = cache_mod.Cache

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
QUARTER = timedelta(milliseconds=250)  # event times never coincide with a whole second
SETTLE = timedelta(seconds=30)
SEAM = timedelta(milliseconds=500)
FIVE = timedelta(minutes=5)
ENTITY = "sensor.counter"
CAP = "sensor.capacity"
STAT_IDS = ["sensor.a", "sensor.b"]


def _make(now: datetime | None = None, **options: Any) -> tuple[Any, FakeStorage, FakeClock]:
    clock = FakeClock(now or T0 + timedelta(hours=3))
    storage = FakeStorage(clock)
    cache = Cache(storage, clock=clock, settle=SETTLE, **options)
    return cache, storage, clock


def _sec(base: datetime, seconds: float) -> datetime:
    return base + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# The model: Cache(S) == S, for random worlds and a moving clock
# ---------------------------------------------------------------------------


def _random_states(
    rng: random.Random, storage: FakeStorage, entity: str, lo: datetime, hi: datetime
) -> None:
    for _ in range(rng.randint(0, 30)):
        ts = _sec(lo, rng.randint(0, int((hi - lo).total_seconds()))) + QUARTER
        state = (
            rng.choice(["unavailable", "unknown"])
            if rng.random() < 0.15
            else f"{rng.uniform(0, 100):.2f}"
        )
        storage.add_state(entity, ts, state, delay=rng.choice([0, 0, 2, 12, 25]))


class TestRawStatesEqualTheStorage:
    @pytest.mark.parametrize("seed", range(150))
    @pytest.mark.asyncio
    async def test_random_reads_with_a_moving_clock(self, seed: int) -> None:
        rng = random.Random(seed)
        cache, storage, clock = _make()
        _random_states(rng, storage, ENTITY, T0, clock.now - timedelta(minutes=5))
        for _ in range(35):
            if rng.random() < 0.4:
                before = clock.now
                clock.now += timedelta(seconds=rng.choice([0, 3, 20, 45, 120, 400]))
                _random_states(rng, storage, ENTITY, before, clock.now)
            span = int((clock.now - T0).total_seconds())
            start = _sec(T0, rng.randint(-3600, span - 1))
            end = _sec(start, rng.randint(1, max(1, int((clock.now - start).total_seconds()))))
            end = min(end, clock.now)
            got = await cache.raw_states(ENTITY, start, end)
            assert got == storage.direct_raw(ENTITY, start, end), (seed, start, end)

    @pytest.mark.asyncio
    async def test_the_first_read_asks_for_exactly_that_span_and_a_seam(self) -> None:
        cache, storage, clock = _make()
        storage.add_state(ENTITY, _sec(T0, 100) + QUARTER, "1.00")
        start, end = _sec(T0, 60), _sec(T0, 600)
        await cache.raw_states(ENTITY, start, end)
        assert storage.calls == [("raw_states", ENTITY, start - SEAM, end)]

    @pytest.mark.asyncio
    async def test_a_read_inside_what_is_known_asks_nothing(self) -> None:
        cache, storage, clock = _make()
        for i in range(10):
            storage.add_state(ENTITY, _sec(T0, 60 * i) + QUARTER, f"{i}.5")
        await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 3000))
        storage.calls.clear()
        again = await cache.raw_states(ENTITY, _sec(T0, 120), _sec(T0, 2000))
        assert storage.calls == []
        assert again == storage.direct_raw(ENTITY, _sec(T0, 120), _sec(T0, 2000))

    @pytest.mark.asyncio
    async def test_extending_the_tail_asks_only_for_the_tail(self) -> None:
        cache, storage, clock = _make()
        for i in range(30):
            storage.add_state(ENTITY, _sec(T0, 60 * i) + QUARTER, f"{i}.5")
        await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 1200))
        storage.calls.clear()
        got = await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 1500))
        assert storage.calls == [("raw_states", ENTITY, _sec(T0, 1200) - SEAM, _sec(T0, 1500))]
        assert got == storage.direct_raw(ENTITY, _sec(T0, 0), _sec(T0, 1500))

    @pytest.mark.asyncio
    async def test_extending_the_head_asks_only_for_the_head(self) -> None:
        cache, storage, clock = _make()
        for i in range(30):
            storage.add_state(ENTITY, _sec(T0, 60 * i) + QUARTER, f"{i}.5")
        await cache.raw_states(ENTITY, _sec(T0, 600), _sec(T0, 1200))
        storage.calls.clear()
        got = await cache.raw_states(ENTITY, _sec(T0, 120), _sec(T0, 1200))
        assert storage.calls == [("raw_states", ENTITY, _sec(T0, 120) - SEAM, _sec(T0, 600))]
        assert got == storage.direct_raw(ENTITY, _sec(T0, 120), _sec(T0, 1200))

    @pytest.mark.asyncio
    async def test_a_state_exactly_where_the_known_span_ends_is_not_lost(self) -> None:
        cache, storage, clock = _make()
        edge = _sec(T0, 600)
        storage.add_state(ENTITY, _sec(T0, 100) + QUARTER, "1.00")
        await cache.raw_states(ENTITY, _sec(T0, 0), edge)  # known: states before ``edge``
        storage.add_state(ENTITY, edge, "2.00")  # written exactly on the seam
        storage.add_state(ENTITY, _sec(T0, 700) + QUARTER, "3.00")
        got = await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 1000))
        assert got == storage.direct_raw(ENTITY, _sec(T0, 0), _sec(T0, 1000))
        assert (edge, "2.00") in got

    @pytest.mark.asyncio
    async def test_a_state_exactly_where_the_known_span_begins_is_not_lost(self) -> None:
        cache, storage, clock = _make()
        edge = _sec(T0, 600)
        storage.add_state(ENTITY, _sec(T0, 100) + QUARTER, "1.00")
        storage.add_state(ENTITY, edge, "2.00")
        storage.add_state(ENTITY, _sec(T0, 900) + QUARTER, "3.00")
        await cache.raw_states(ENTITY, edge + timedelta(seconds=60), _sec(T0, 1200))
        got = await cache.raw_states(ENTITY, _sec(T0, 300), _sec(T0, 1200))
        assert got == storage.direct_raw(ENTITY, _sec(T0, 300), _sec(T0, 1200))

    @pytest.mark.asyncio
    async def test_a_state_the_recorder_has_not_committed_is_read_again(self) -> None:
        cache, storage, clock = _make()
        end = clock.now
        storage.add_state(ENTITY, end - timedelta(seconds=40) + QUARTER, "1.00")
        # committed 12 s late: invisible to the first read, which ends at ``end``
        storage.add_state(ENTITY, end - timedelta(seconds=10) + QUARTER, "2.00", delay=12)
        first = await cache.raw_states(ENTITY, end - timedelta(hours=1), end)
        assert [s for _, s in first][-1:] == ["1.00"]
        clock.now += timedelta(seconds=20)  # now committed
        second = await cache.raw_states(ENTITY, end - timedelta(hours=1), end)
        assert second == storage.direct_raw(ENTITY, end - timedelta(hours=1), end)
        assert any(s == "2.00" for _, s in second)

    @pytest.mark.asyncio
    async def test_a_read_starting_inside_the_seam_widens_the_span(self) -> None:
        """A state on the very first instant the cache knows must not be trusted away."""
        cache, storage, clock = _make()
        lo = _sec(T0, 600) - SEAM  # where the first read leaves the cache's knowledge
        storage.add_state(ENTITY, _sec(T0, 100) + QUARTER, "1.00")
        storage.add_state(ENTITY, lo, "2.00")  # exactly there: invisible to the first read
        await cache.raw_states(ENTITY, _sec(T0, 600), _sec(T0, 1200))
        inside = lo + timedelta(milliseconds=250)
        got = await cache.raw_states(ENTITY, inside, _sec(T0, 1200))
        assert got == storage.direct_raw(ENTITY, inside, _sec(T0, 1200))
        assert got[0] == (inside, "2.00")

    @pytest.mark.asyncio
    async def test_a_clear_during_a_read_is_not_undone_by_it(self) -> None:
        cache, storage, clock = _make()
        for i in range(10):
            storage.add_state(ENTITY, _sec(T0, 100 * i) + QUARTER, f"{i}.5")
        await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 900))
        storage.add_state(ENTITY, _sec(T0, 500) + timedelta(milliseconds=500), "9.99")
        storage.yield_in_reads = True
        task = asyncio.ensure_future(cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 1200)))
        await asyncio.sleep(0)  # the read is waiting for the storage
        cache.clear()
        got = await task
        assert got == storage.direct_raw(ENTITY, _sec(T0, 0), _sec(T0, 1200))
        assert (_sec(T0, 500) + timedelta(milliseconds=500), "9.99") in got
        assert any(
            row[1] == "9.99" for row in cache.snapshot()["raw"][ENTITY]["events"]
        ), "the cleared cache was refilled from a stale span"

    @pytest.mark.asyncio
    async def test_no_start_state_when_the_recorder_holds_nothing_that_old(self) -> None:
        cache, storage, clock = _make()
        storage.add_state(ENTITY, _sec(T0, 500) + QUARTER, "1.00")
        got = await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 900))
        assert got == [(_sec(T0, 500) + QUARTER, "1.00")]
        again = await cache.raw_states(ENTITY, _sec(T0, 100), _sec(T0, 900))
        assert again == storage.direct_raw(ENTITY, _sec(T0, 100), _sec(T0, 900))

    @pytest.mark.asyncio
    async def test_over_the_cap_an_entity_is_read_directly_and_not_stored(self) -> None:
        warnings: list[str] = []
        cache, storage, clock = _make(max_raw_entries=5, warn=warnings.append)
        for i in range(20):
            storage.add_state(ENTITY, _sec(T0, 60 * i) + QUARTER, f"{i}.5")
        got = await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 1300))
        assert got == storage.direct_raw(ENTITY, _sec(T0, 0), _sec(T0, 1300))
        assert cache.snapshot()["raw"] == {}
        await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 1300))
        assert len(storage.calls) == 2  # read again, as there is nothing to serve it from
        assert len(warnings) == 1  # said once


class _JitteredStart:
    """A storage whose start state comes back a microsecond off the start asked for.

    The real one builds it from a float timestamp; the cache must not depend on the
    round trip being exact.
    """

    def __init__(self, inner: FakeStorage) -> None:
        self._inner = inner

    async def raw_states(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, str]]:
        rows = await self._inner.raw_states(entity_id, start, end)
        if rows and rows[0][0] == start:
            rows[0] = (start + timedelta(microseconds=1), rows[0][1])
        return rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TestTheStartStateTolerance:
    @pytest.mark.parametrize("seed", range(30))
    @pytest.mark.asyncio
    async def test_a_start_state_a_microsecond_off_is_still_the_start_state(
        self, seed: int
    ) -> None:
        rng = random.Random(7000 + seed)
        cache, storage, clock = _make()
        cache._inner = _JitteredStart(storage)
        _random_states(rng, storage, ENTITY, T0, clock.now - timedelta(minutes=5))
        for _ in range(20):
            clock.now += timedelta(seconds=rng.choice([0, 20, 90]))
            start = _sec(T0, rng.randint(-600, 9000))
            end = min(_sec(start, rng.randint(1, 3000)), clock.now)
            if end <= start:
                continue
            assert await cache.raw_states(ENTITY, start, end) == storage.direct_raw(
                ENTITY, start, end
            )


class TestCapacityStatesEqualTheStorage:
    @pytest.mark.parametrize("seed", range(40))
    @pytest.mark.asyncio
    async def test_random_reads_with_a_moving_clock(self, seed: int) -> None:
        rng = random.Random(1000 + seed)
        cache, storage, clock = _make()
        for _ in range(rng.randint(0, 12)):
            ts = _sec(T0, rng.randint(0, 9000)) + QUARTER
            storage.add_capacity(
                CAP,
                ts,
                rng.choice(["5.1", "5.2", "unavailable"]),
                rng.choice(["kWh", "Wh", None]),
                delay=rng.choice([0, 12]),
            )
        for _ in range(20):
            clock.now += timedelta(seconds=rng.choice([0, 20, 90]))
            start = _sec(T0, rng.randint(-600, 9000))
            end = min(_sec(start, rng.randint(1, 4000)), clock.now)
            if end <= start:
                continue
            assert await cache.capacity_states(CAP, start, end) == storage.direct_capacity(
                CAP, start, end
            )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _fill_rows(
    rng: random.Random, storage: FakeStorage, lo: datetime, hi: datetime, gaps: float
) -> None:
    slot = lo
    while slot + FIVE <= hi:
        for sid in STAT_IDS:
            if rng.random() > gaps:
                storage.add_row(sid, slot, rng.uniform(0, 500), row_delay=rng.choice([0, 0, 5, 15]))
        slot += FIVE


class TestStatisticsEqualTheStorage:
    @pytest.mark.parametrize("seed", range(100))
    @pytest.mark.asyncio
    async def test_random_reads_with_a_moving_clock(self, seed: int) -> None:
        rng = random.Random(5000 + seed)
        cache, storage, clock = _make()
        filled = T0
        for _ in range(30):
            if rng.random() < 0.4:
                clock.now += timedelta(seconds=rng.choice([0, 7, 25, 60, 330, 900]))
            _fill_rows(rng, storage, filled, clock.now, gaps=0.2)
            filled = max(filled, T0 + (clock.now - T0) // FIVE * FIVE)
            ids = rng.choice([STAT_IDS, STAT_IDS[:1], STAT_IDS[1:]])
            start = T0 + FIVE * rng.randint(-3, 30)
            if rng.random() < 0.3:
                start += timedelta(seconds=rng.randint(1, 299))
            end = min(start + FIVE * rng.randint(1, 20), clock.now)
            if end <= start:
                continue
            got = await cache.statistics(ids, start, end)
            assert got == storage.direct_statistics(ids, start, end), (seed, ids, start, end)

    @pytest.mark.asyncio
    async def test_the_slot_the_recorder_has_not_compiled_is_asked_again(self) -> None:
        cache, storage, clock = _make(now=T0 + timedelta(hours=1, seconds=12))
        for i in range(11):
            storage.add_row("sensor.a", T0 + FIVE * i, float(i))
        # slot 11 (55 to 60 min) ended 12 s ago: compiled 10 s after its end, committed late
        storage.add_row("sensor.a", T0 + FIVE * 11, 11.0, row_delay=20)
        end = T0 + timedelta(hours=1)
        first = await cache.statistics(["sensor.a"], T0, end)
        assert len(first["sensor.a"]) == 11
        clock.now += timedelta(seconds=40)
        second = await cache.statistics(["sensor.a"], T0, end)
        assert (
            len(second["sensor.a"]) == 12
        )  # the row is there now, and was not remembered as absent

    @pytest.mark.asyncio
    async def test_a_gap_that_is_old_is_a_settled_answer(self) -> None:
        cache, storage, clock = _make()
        for i in range(20):
            if i != 8:
                storage.add_row("sensor.a", T0 + FIVE * i, float(i))
        await cache.statistics(["sensor.a"], T0, T0 + FIVE * 20)
        storage.calls.clear()
        await cache.statistics(["sensor.a"], T0 + FIVE * 5, T0 + FIVE * 15)
        assert storage.calls == []

    @pytest.mark.asyncio
    async def test_an_id_with_no_row_is_absent_as_in_the_storage(self) -> None:
        cache, storage, clock = _make()
        storage.add_row("sensor.a", T0, 1.0)
        got = await cache.statistics(["sensor.a", "sensor.b"], T0, T0 + FIVE)
        assert list(got) == ["sensor.a"]

    @pytest.mark.asyncio
    async def test_ids_missing_the_same_span_share_one_read(self) -> None:
        cache, storage, clock = _make()
        for i in range(6):
            storage.add_row("sensor.a", T0 + FIVE * i, float(i))
            storage.add_row("sensor.b", T0 + FIVE * i, float(i))
        await cache.statistics(["sensor.a", "sensor.b"], T0, T0 + FIVE * 6)
        assert len(storage.calls) == 1

    @pytest.mark.asyncio
    async def test_a_caller_that_changes_a_row_does_not_change_the_cache(self) -> None:
        cache, storage, clock = _make()
        storage.add_row("sensor.a", T0, 1.0)
        got = await cache.statistics(["sensor.a"], T0, T0 + FIVE)
        got["sensor.a"][0]["mean"] = 999.0
        got["sensor.a"][0]["start"] = "changed"
        again = await cache.statistics(["sensor.a"], T0, T0 + FIVE)
        assert again == storage.direct_statistics(["sensor.a"], T0, T0 + FIVE)

    @pytest.mark.asyncio
    async def test_what_effy_wrote_is_forgotten_and_read_again(self) -> None:
        """The standby and efficiency readers read back what Effy wrote (ADR-025 decision 11)."""
        cache, storage, clock = _make()
        for i in range(6):
            storage.add_row("sensor.effy_x", T0 + FIVE * i, float(i))
        await cache.statistics(["sensor.effy_x"], T0, T0 + FIVE * 6)
        storage.add_row("sensor.effy_x", T0 + FIVE * 2, 777.0)  # Effy rewrote slot 2
        cache.written(["sensor.effy_x"], T0, T0 + FIVE * 6)
        again = await cache.statistics(["sensor.effy_x"], T0, T0 + FIVE * 6)
        assert again == storage.direct_statistics(["sensor.effy_x"], T0, T0 + FIVE * 6)
        assert again["sensor.effy_x"][2]["mean"] == 777.0
        assert storage.written_calls == [(("sensor.effy_x",), T0, T0 + FIVE * 6)]

    @pytest.mark.asyncio
    async def test_a_write_to_one_id_leaves_the_others_alone(self) -> None:
        cache, storage, clock = _make()
        for i in range(6):
            storage.add_row("sensor.a", T0 + FIVE * i, float(i))
            storage.add_row("sensor.effy_x", T0 + FIVE * i, float(i))
        await cache.statistics(["sensor.a", "sensor.effy_x"], T0, T0 + FIVE * 6)
        storage.calls.clear()
        cache.written(["sensor.effy_x"], T0, T0 + FIVE * 6)
        await cache.statistics(["sensor.a"], T0, T0 + FIVE * 6)
        assert storage.calls == []


# ---------------------------------------------------------------------------
# Anchors and the observed kinds
# ---------------------------------------------------------------------------


class TestAnchorsAndObserved:
    @pytest.mark.asyncio
    async def test_an_anchor_answer_is_kept_including_none(self) -> None:
        cache, storage, clock = _make()
        storage.add_state(ENTITY, _sec(T0, 100) + QUARTER, "7.00")
        found = await cache.last_valid_state_before(ENTITY, _sec(T0, 500), 3)
        nothing = await cache.last_valid_state_before(ENTITY, _sec(T0, 50), 3)
        assert found == (_sec(T0, 100) + QUARTER, "7.00") and nothing is None
        storage.calls.clear()
        assert await cache.last_valid_state_before(ENTITY, _sec(T0, 500), 3) == found
        assert await cache.last_valid_state_before(ENTITY, _sec(T0, 50), 3) is None
        assert storage.calls == []

    @pytest.mark.asyncio
    async def test_stored_rows_are_read_fresh_every_time_and_recorded(self) -> None:
        cache, storage, clock = _make()
        short = [(T0, 1.0), (T0 + FIVE, 2.0)]
        first = await cache.unchanged_rows("sensor.effy_x", "kW", short, {})
        storage.unchanged_result = {"short": frozenset({T0}), "hourly": frozenset()}
        second = await cache.unchanged_rows("sensor.effy_x", "kW", short, {})
        assert first["short"] == frozenset() and second["short"] == frozenset({T0})
        assert [c[0] for c in storage.calls] == ["unchanged_rows", "unchanged_rows"]
        assert cache.snapshot()["observed"]["unchanged_rows"]

    @pytest.mark.asyncio
    async def test_the_statistics_units_are_read_fresh_and_recorded(self) -> None:
        cache, storage, clock = _make()
        storage.units["sensor.a"] = "kWh"
        assert await cache.statistics_units(["sensor.a"]) == {"sensor.a": "kWh"}
        storage.units["sensor.a"] = "Wh"
        assert await cache.statistics_units(["sensor.a"]) == {"sensor.a": "Wh"}
        assert len(storage.calls) == 2

    def test_unit_state_class_retention_and_timing_pass_through_and_are_recorded(self) -> None:
        cache, storage, clock = _make()
        storage.live_units["sensor.a"] = "kW"
        storage.state_classes["sensor.a"] = "measurement"
        assert cache.unit("sensor.a") == "kW"
        assert cache.state_class("sensor.a") == "measurement"
        assert cache.retention_days() == 10
        marker = object()
        cache.set_timing(marker)
        assert storage.timing is marker
        observed = cache.snapshot()["observed"]
        assert observed["units"] == {"sensor.a": "kW"}
        assert observed["state_classes"] == {"sensor.a": "measurement"}
        assert observed["retention_days"] == 10


# ---------------------------------------------------------------------------
# Snapshot, clear, replay
# ---------------------------------------------------------------------------


async def _recording(cache: Any, storage: FakeStorage) -> list[Any]:
    """A sequence of reads of every kind; returns their answers."""
    for i in range(40):
        storage.add_state(ENTITY, _sec(T0, 90 * i) + QUARTER, f"{i}.25")
        storage.add_capacity(CAP, _sec(T0, 400 * i) + QUARTER, "5.0", "kWh")
    for i in range(30):
        for sid in STAT_IDS:
            storage.add_row(sid, T0 + FIVE * i, float(i))
    storage.units["sensor.a"] = "kW"
    answers: list[Any] = []
    answers.append(await cache.raw_states(ENTITY, _sec(T0, 600), _sec(T0, 2400)))
    answers.append(await cache.raw_states(ENTITY, _sec(T0, 300), _sec(T0, 3000)))
    answers.append(await cache.statistics(STAT_IDS, T0 + FIVE * 2, T0 + FIVE * 12))
    answers.append(await cache.statistics(STAT_IDS[:1], T0 + FIVE, T0 + FIVE * 20))
    answers.append(await cache.capacity_states(CAP, _sec(T0, 200), _sec(T0, 3000)))
    answers.append(await cache.last_valid_state_before(ENTITY, _sec(T0, 600), 3))
    answers.append(await cache.statistics_units(["sensor.a"]))
    answers.append(await cache.unchanged_rows("sensor.effy_x", "kW", [(T0, 1.0)], {T0: (1.0, 1.0)}))
    return answers


class TestSnapshotAndReplay:
    @pytest.mark.asyncio
    async def test_after_a_clear_the_snapshot_holds_exactly_what_was_asked(self) -> None:
        cache, storage, clock = _make()
        storage.add_state(ENTITY, _sec(T0, 100) + QUARTER, "1.00")
        await cache.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 5))  # junk, then clear
        cache.clear()
        assert cache.snapshot()["raw"] == {} and cache.snapshot()["stats"] == {}
        await cache.raw_states(ENTITY, _sec(T0, 600), _sec(T0, 2400))
        for i in range(10):
            storage.add_row("sensor.a", T0 + FIVE * i, float(i))
        await cache.statistics(["sensor.a"], T0 + FIVE * 2, T0 + FIVE * 6)
        snap = cache.snapshot()
        assert list(snap["raw"]) == [ENTITY]
        assert snap["raw"][ENTITY]["lo"] == (_sec(T0, 600) - SEAM).isoformat()
        assert snap["raw"][ENTITY]["obtained"] == _sec(T0, 2400).isoformat()
        assert list(snap["stats"]) == ["sensor.a"]
        assert snap["stats"]["sensor.a"]["lo"] == (T0 + FIVE * 2).isoformat()
        assert snap["stats"]["sensor.a"]["obtained"] == (T0 + FIVE * 6).isoformat()
        assert [r["start"] for r in snap["stats"]["sensor.a"]["rows"]] == [
            (T0 + FIVE * i).isoformat() for i in range(2, 6)
        ]

    @pytest.mark.asyncio
    async def test_a_snapshot_is_plain_data_that_survives_json(self) -> None:
        cache, storage, clock = _make()
        await _recording(cache, storage)
        snap = cache.snapshot()
        assert snap["version"] == 1
        assert json.loads(json.dumps(snap)) == snap

    @pytest.mark.asyncio
    async def test_a_cache_loaded_from_a_snapshot_replays_the_recording(self) -> None:
        cache, storage, clock = _make()
        recorded = await _recording(cache, storage)
        data = json.loads(json.dumps(cache.snapshot()))

        replay_clock = FakeClock(T0)  # a different clock: nothing is fetched, nothing is settled
        replay = Cache.from_snapshot(data, RaisingStorage(), clock=replay_clock, settle=SETTLE)
        replayed = await _recording_reads_only(replay)
        assert replayed == recorded

    @pytest.mark.asyncio
    async def test_a_replay_refuses_a_read_the_recording_does_not_contain(self) -> None:
        cache, storage, clock = _make()
        await _recording(cache, storage)
        replay = Cache.from_snapshot(
            json.loads(json.dumps(cache.snapshot())),
            RaisingStorage(),
            clock=FakeClock(T0),
            settle=SETTLE,
        )
        with pytest.raises(UnrecordedRead):
            await replay.raw_states(ENTITY, _sec(T0, 0), _sec(T0, 100))  # older than recorded
        with pytest.raises(UnrecordedRead):
            await replay.raw_states(ENTITY, _sec(T0, 600), _sec(T0, 9000))  # newer
        with pytest.raises(UnrecordedRead):
            await replay.raw_states("sensor.other", _sec(T0, 600), _sec(T0, 900))
        with pytest.raises(UnrecordedRead):
            await replay.statistics(["sensor.b"], T0, T0 + FIVE)
        with pytest.raises(UnrecordedRead):
            await replay.statistics_units(["sensor.zzz"])

    @pytest.mark.asyncio
    async def test_from_snapshot_gives_an_equal_snapshot(self) -> None:
        cache, storage, clock = _make()
        await _recording(cache, storage)
        data = cache.snapshot()
        again = Cache.from_snapshot(data, RaisingStorage(), clock=FakeClock(T0), settle=SETTLE)
        assert again.snapshot() == data

    @pytest.mark.asyncio
    async def test_a_snapshot_of_the_wrong_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="version"):
            Cache.from_snapshot(
                {"version": 99}, RaisingStorage(), clock=FakeClock(T0), settle=SETTLE
            )


async def _recording_reads_only(cache: Any) -> list[Any]:
    answers: list[Any] = []
    answers.append(await cache.raw_states(ENTITY, _sec(T0, 600), _sec(T0, 2400)))
    answers.append(await cache.raw_states(ENTITY, _sec(T0, 300), _sec(T0, 3000)))
    answers.append(await cache.statistics(STAT_IDS, T0 + FIVE * 2, T0 + FIVE * 12))
    answers.append(await cache.statistics(STAT_IDS[:1], T0 + FIVE, T0 + FIVE * 20))
    answers.append(await cache.capacity_states(CAP, _sec(T0, 200), _sec(T0, 3000)))
    answers.append(await cache.last_valid_state_before(ENTITY, _sec(T0, 600), 3))
    answers.append(await cache.statistics_units(["sensor.a"]))
    answers.append(await cache.unchanged_rows("sensor.effy_x", "kW", [(T0, 1.0)], {T0: (1.0, 1.0)}))
    return answers


# ---------------------------------------------------------------------------
# Concurrency (the probe reads outside the recorder-job lock) and purity
# ---------------------------------------------------------------------------


class TestConcurrentReads:
    @pytest.mark.parametrize("seed", range(30))
    @pytest.mark.asyncio
    async def test_overlapping_reads_at_the_same_time_are_each_right(self, seed: int) -> None:
        rng = random.Random(9000 + seed)
        cache, storage, clock = _make()
        storage.yield_in_reads = True
        _random_states(rng, storage, ENTITY, T0, clock.now - timedelta(minutes=5))
        for i in range(30):
            storage.add_row("sensor.a", T0 + FIVE * i, float(i))
        spans = []
        for _ in range(4):
            s = _sec(T0, rng.randint(0, 9000))
            spans.append((s, min(_sec(s, rng.randint(60, 4000)), clock.now)))
        raw = await asyncio.gather(*(cache.raw_states(ENTITY, s, e) for s, e in spans))
        stats = await asyncio.gather(
            *(
                cache.statistics(["sensor.a"], T0 + FIVE * (k % 5), T0 + FIVE * (10 + k))
                for k in range(4)
            )
        )
        for (s, e), got in zip(spans, raw, strict=True):
            assert got == storage.direct_raw(ENTITY, s, e)
        for k, got in enumerate(stats):
            assert got == storage.direct_statistics(
                ["sensor.a"], T0 + FIVE * (k % 5), T0 + FIVE * (10 + k)
            )


class TestPurity:
    def test_the_modules_import_only_the_standard_library_at_run_time(self) -> None:
        """No Home Assistant, no other Effy module (a type-only import is fine)."""
        import ast

        for name in ("cache.py", "storage.py"):
            tree = ast.parse((_PATH.parent / name).read_text(encoding="utf-8"))
            for node in tree.body:  # module level; ``if TYPE_CHECKING`` is an ``If``, skipped
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    assert node.level == 0, (name, "relative import at run time")
                    modules = [node.module or ""]
                for module in modules:
                    assert module.split(".")[0] in sys.stdlib_module_names, (name, module)
