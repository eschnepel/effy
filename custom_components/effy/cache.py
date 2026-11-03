"""A ``Storage`` that remembers what it has read (ADR-025, TASK-0036).

Pure: no Home Assistant import, no recorder import, no I/O, loaded by file path in
the tests (ADR-000 §6). A ``Cache`` wraps another ``Storage`` (the recorder-backed one,
or a fake) and **is** one: ``history.py`` calls it exactly as it would call the
storage underneath, and gets what that storage would have returned at that moment.

What it keeps, and why each kind is different:

- *Raw states* (and the capacity entity's, with a unit): events at irregular times.
  ``state_changes_during_period(start, end)`` returns the latest state strictly before
  ``start`` stamped ``start``, then the changes with ``start < time < end``, both ends
  exclusive (Home Assistant 2026.10.4). So the cache keeps the **real** events and a
  ``base`` (the state in effect at the start of what it knows), and applies that rule
  to what it holds. A missing head or tail is read with a one-second-or-less seam, so
  that a state written exactly on the join is not lost and none is counted twice.
- *Statistics rows*: one row per 5-minute slot, a validated time span per statistic id.
  A slot the recorder compiles about 10 s after its end is not an answer until it is
  ``settle`` old, and neither is a state until then: the freshest part is read again.
- *Anchors*: the answer of ``last_valid_state_before``, kept as asked, ``None`` too.
- *Observed*: what Effy must **not** reuse (its own stored rows, the statistics units)
  or that comes from the state machine (units, state classes, retention). Every call
  reads the storage underneath; the answer is recorded, so a snapshot is complete.

It fetches exactly what is asked and nothing around it (apart from the seam), so that
after ``clear()`` and one recalculation ``snapshot()`` is what that recalculation read.
All calls run on the event loop; the cache re-checks what it holds after every
``await`` (``async_probe_last_slot_ready`` reads outside the recorder-job lock).

``snapshot()`` is plain data with a schema version; ``from_snapshot(data, inner)``
turns it back into a cache that treats everything recorded as settled. With an inner
storage that raises, that is a replay: a read the recording does not contain fails.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .storage import CapacityState, RawState, StatRow, Storage

SNAPSHOT_VERSION = 1

# How many times one read replans because the cache changed under it (a concurrent
# read of the same entity) before it gives up and reads the storage directly.
_MAX_ROUNDS = 8

# The recorder stamps the start state with the start it was asked for, through a float
# timestamp; a state written within this of the start is taken for it (it would be a state
# written within two microseconds of the instant asked about).
_START_TOLERANCE = timedelta(microseconds=2)

# One event: ``(time, *payload)``; the payload is ``(state,)`` or ``(state, unit)``.
Event = tuple[Any, ...]


@dataclass
class _Events:
    """What is known of one entity's states.

    Every state change with ``lo < time < hi`` is in ``events``; ``base`` is the
    payload of the latest state strictly before ``lo`` (``None`` if there is none).
    ``events`` may hold fresher ones beyond ``hi``: they were returned by the last
    read and are read again by the next. ``obtained`` is where that read ended.
    """

    lo: datetime
    hi: datetime
    obtained: datetime
    base: tuple[Any, ...] | None
    events: list[Event]
    rev: int = 0


@dataclass
class _Rows:
    """What is known of one statistic id: every row with ``lo <= start < hi``."""

    lo: datetime
    hi: datetime
    obtained: datetime
    rows: dict[datetime, StatRow]
    rev: int = 0


@dataclass
class _Observed:
    statistics_units: dict[str, dict[str, str]] = field(default_factory=dict)
    unchanged_rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    state_classes: dict[str, str | None] = field(default_factory=dict)
    retention_days: int | None = None


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Cache:
    """A ``Storage`` over another ``Storage``; see the module docstring."""

    def __init__(
        self,
        inner: Storage,
        *,
        clock: Callable[[], datetime],
        settle: timedelta = timedelta(seconds=30),
        slot: timedelta = timedelta(minutes=5),
        seam: timedelta = timedelta(milliseconds=500),
        max_raw_entries: int = 200_000,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._inner = inner
        self._clock = clock
        self._settle = settle
        self._slot = slot
        self._seam = seam
        self._max_raw_entries = max_raw_entries
        self._warn = warn or (lambda _message: None)
        self._replay = False
        self._raw: dict[str, _Events] = {}
        self._capacity: dict[str, _Events] = {}
        self._stats: dict[str, _Rows] = {}
        self._anchors: dict[tuple[str, datetime, int], RawState | None] = {}
        self._observed = _Observed()
        self._too_big: set[tuple[str, str]] = set()
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------ housekeeping
    def clear(self) -> None:
        """Forget everything (the history button reads fresh, ADR-025 decision 8)."""
        self._raw.clear()
        self._capacity.clear()
        self._stats.clear()
        self._anchors.clear()
        self._observed = _Observed()
        self._too_big.clear()
        self._replay = False

    def counters(self) -> dict[str, int]:
        """Reads served from memory and reads that went to the storage underneath."""
        return {"hits": self._hits, "misses": self._misses}

    def set_timing(self, timings: Any) -> None:
        self._inner.set_timing(timings)

    # ------------------------------------------------------------------ raw states
    async def raw_states(self, entity_id: str, start: datetime, end: datetime) -> list[RawState]:
        async def fetch(a: datetime, b: datetime) -> list[Event]:
            return [tuple(row) for row in await self._inner.raw_states(entity_id, a, b)]

        result = await self._read_events(self._raw, "raw", entity_id, start, end, fetch)
        return [(row[0], row[1]) for row in result]

    async def capacity_states(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[CapacityState]:
        async def fetch(a: datetime, b: datetime) -> list[Event]:
            return [tuple(row) for row in await self._inner.capacity_states(entity_id, a, b)]

        result = await self._read_events(self._capacity, "capacity", entity_id, start, end, fetch)
        return [(row[0], row[1], row[2]) for row in result]

    async def _read_events(
        self,
        store: dict[str, _Events],
        kind: str,
        entity_id: str,
        start: datetime,
        end: datetime,
        fetch: Callable[[datetime, datetime], Any],
    ) -> list[Event]:
        if (kind, entity_id) in self._too_big:
            self._misses += 1
            result: list[Event] = await fetch(start, end)
            return result
        fetched = False
        tail_done = False
        for _round in range(_MAX_ROUNDS):
            series = store.get(entity_id)
            if series is None:
                plan, window = "create", (start - self._seam, end)
            elif start - self._seam < series.lo:
                plan, window = "head", (start - self._seam, series.lo + self._seam)
            elif end > series.hi and not tail_done:
                plan, window = "tail", (series.hi - self._seam, end)
            else:
                break
            rev = series.rev if series is not None else None
            rows: list[Event] = await fetch(*window)
            fetched = True
            current = store.get(entity_id)
            if current is not series or (current is not None and current.rev != rev):
                continue  # another read changed what we hold while we waited: plan again
            merged = self._merge_events(plan, series, rows, window, end)
            if len(merged.events) > self._max_raw_entries:
                store.pop(entity_id, None)
                self._too_big.add((kind, entity_id))
                self._warn(
                    f"{entity_id}: more than {self._max_raw_entries} states in the cache; "
                    "it is read directly from now on"
                )
                self._misses += 1
                return self._compose(merged, start, end)
            merged.rev = (series.rev + 1) if series is not None else 0
            store[entity_id] = merged
            if plan in ("create", "tail"):
                tail_done = True
        else:
            self._misses += 1
            direct: list[Event] = await fetch(start, end)
            return direct
        series = store.get(entity_id)
        if series is None:  # cleared while we waited
            self._misses += 1
            gone: list[Event] = await fetch(start, end)
            return gone
        if fetched:
            self._misses += 1
        else:
            self._hits += 1
        return self._compose(series, start, end)

    def _merge_events(
        self,
        plan: str,
        series: _Events | None,
        rows: list[Event],
        window: tuple[datetime, datetime],
        end: datetime,
    ) -> _Events:
        a = window[0]
        has_start_row = bool(rows) and abs(rows[0][0] - a) <= _START_TOLERANCE
        payload = tuple(rows[0][1:]) if has_start_row else None
        rest = rows[1:] if has_start_row else rows
        settled = self._clock() - self._settle
        if plan == "create":
            return _Events(
                lo=a,
                hi=max(a, min(end, settled)),
                obtained=end,
                base=payload,
                events=list(rest),
            )
        assert series is not None
        if plan == "head":
            # events already known are those after ``series.lo``; the seam brought in the
            # ones at and before it that the earlier read could not see
            fresh = [row for row in rest if row[0] <= series.lo]
            return _Events(
                lo=a,
                hi=series.hi,
                obtained=series.obtained,
                base=payload,
                events=fresh + series.events,
            )
        # tail: what is before ``series.hi`` is final, the rest is replaced
        kept = [row for row in series.events if row[0] < series.hi]
        fresh = [row for row in rest if row[0] >= series.hi]
        return _Events(
            lo=series.lo,
            hi=max(series.hi, min(end, settled)),
            obtained=max(series.obtained, end),
            base=series.base,
            events=kept + fresh,
        )

    @staticmethod
    def _compose(series: _Events, start: datetime, end: datetime) -> list[Event]:
        """What the recorder returns for ``(start, end)``, from the events held."""
        times = [row[0] for row in series.events]
        before = bisect_left(times, start)
        payload = series.events[before - 1][1:] if before > 0 else series.base
        result: list[Event] = []
        if payload is not None:
            result.append((start, *payload))
        for row in series.events[bisect_right(times, start) :]:
            if row[0] >= end:
                break
            result.append(row)
        return result

    # ------------------------------------------------------------------ statistics
    def _settled_bound(self) -> datetime:
        """Start of the first slot that ended less than ``settle`` ago."""
        moment = (self._clock() - self._settle).timestamp()
        width = self._slot.total_seconds()
        return datetime.fromtimestamp(moment - moment % width, tz=timezone.utc)

    async def statistics(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[StatRow]]:
        fetched = False
        tail_done: set[str] = set()
        for _round in range(_MAX_ROUNDS):
            groups: dict[tuple[str, datetime, datetime], list[str]] = {}
            for sid in entity_ids:
                held = self._stats.get(sid)
                if held is None:
                    groups.setdefault(("create", start, end), []).append(sid)
                elif start < held.lo:
                    groups.setdefault(("head", start, held.lo), []).append(sid)
                elif end > held.hi and sid not in tail_done:
                    groups.setdefault(("tail", held.hi, end), []).append(sid)
            if not groups:
                break
            for (plan, a, b), ids in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                revs = {sid: (self._stats[sid].rev if sid in self._stats else None) for sid in ids}
                held_before = {sid: self._stats.get(sid) for sid in ids}
                rows = await self._inner.statistics(ids, a, b)
                fetched = True
                for sid in ids:
                    current = self._stats.get(sid)
                    if current is not held_before[sid] or (
                        current is not None and current.rev != revs[sid]
                    ):
                        continue  # changed while we waited: the next round plans again
                    self._merge_rows(plan, sid, current, rows.get(sid, []), a, b)
                    if plan in ("create", "tail"):
                        tail_done.add(sid)
        else:
            self._misses += 1
            return await self._inner.statistics(entity_ids, start, end)
        if fetched:
            self._misses += 1
        else:
            self._hits += 1
        result: dict[str, list[StatRow]] = {}
        for sid in entity_ids:
            held = self._stats.get(sid)
            if held is None:  # cleared while we waited
                self._misses += 1
                return await self._inner.statistics(entity_ids, start, end)
            rows_in_range = [dict(held.rows[t]) for t in sorted(held.rows) if start <= t < end]
            if rows_in_range:
                result[sid] = rows_in_range
        return result

    def _merge_rows(
        self,
        plan: str,
        sid: str,
        series: _Rows | None,
        fetched: list[StatRow],
        a: datetime,
        b: datetime,
    ) -> None:
        by_start = {row["start"]: row for row in fetched}
        settled = self._settled_bound()
        # a trailing run of absent slots is only an answer once it is ``settle`` old
        last_row_end = max((t + self._slot for t in by_start), default=a)
        hi = max(a, min(b, max(settled, last_row_end)))
        if plan == "create":
            self._stats[sid] = _Rows(lo=a, hi=hi, obtained=b, rows=by_start)
            return
        assert series is not None
        if plan == "head":
            series.rows.update(by_start)
            series.lo = a
        else:
            for t in [t for t in series.rows if t >= a]:
                del series.rows[t]
            series.rows.update(by_start)
            series.hi = max(series.hi, hi)
            series.obtained = max(series.obtained, b)
        series.rev += 1

    # ------------------------------------------------------------------ anchors
    async def last_valid_state_before(
        self, entity_id: str, before: datetime, max_history_days: int
    ) -> RawState | None:
        key = (entity_id, before, max_history_days)
        if key in self._anchors:
            self._hits += 1
            return self._anchors[key]
        answer = await self._inner.last_valid_state_before(entity_id, before, max_history_days)
        self._misses += 1
        self._anchors[key] = answer
        return answer

    # ------------------------------------------------------------------ observed
    async def statistics_units(self, entity_ids: list[str]) -> dict[str, str]:
        key = ",".join(sorted(entity_ids))
        if self._replay and key in self._observed.statistics_units:
            self._hits += 1
            return dict(self._observed.statistics_units[key])
        result = await self._inner.statistics_units(entity_ids)
        self._misses += 1
        self._observed.statistics_units[key] = dict(result)
        return result

    @staticmethod
    def _rows_key(
        statistic_id: str,
        unit: str,
        short: list[tuple[datetime, float]],
        hourly: dict[datetime, tuple[float, float]],
    ) -> str:
        # Instants, not datetimes: the repr of a datetime names its tzinfo object, which a
        # replay from a file does not have to share with the recorded run (TASK-0037).
        digest = hashlib.sha1(  # noqa: S324 - a fingerprint, not a secret
            repr(
                (
                    statistic_id,
                    unit,
                    [(ts.timestamp(), value) for ts, value in short],
                    sorted((ts.timestamp(), value) for ts, value in hourly.items()),
                )
            ).encode()
        ).hexdigest()
        return f"{statistic_id}|{digest}"

    async def unchanged_rows(
        self,
        statistic_id: str,
        unit: str,
        short: list[tuple[datetime, float]],
        hourly: dict[datetime, tuple[float, float]],
        notes: dict[str, str] | None = None,
    ) -> dict[str, frozenset[datetime]]:
        key = self._rows_key(statistic_id, unit, short, hourly)
        if self._replay and key in self._observed.unchanged_rows:
            self._hits += 1
            recorded = self._observed.unchanged_rows[key]
            return {
                name: frozenset(_parse(t) for t in recorded[name]) for name in ("short", "hourly")
            }
        result = await self._inner.unchanged_rows(statistic_id, unit, short, hourly, notes)
        self._misses += 1
        self._observed.unchanged_rows[key] = {
            "statistic_id": statistic_id,
            "short": sorted(_iso(t) for t in result.get("short", frozenset())),
            "hourly": sorted(_iso(t) for t in result.get("hourly", frozenset())),
        }
        return result

    def written(self, statistic_ids: list[str], start: datetime, end: datetime) -> None:
        """Effy wrote these ids: forget them whole (they are read back, ADR-025 decision 11)."""
        for sid in statistic_ids:
            self._stats.pop(sid, None)
        self._inner.written(statistic_ids, start, end)

    def unit(self, entity_id: str) -> str:
        if self._replay and entity_id in self._observed.units:
            return self._observed.units[entity_id]
        value = self._inner.unit(entity_id)
        self._observed.units[entity_id] = value
        return value

    def state_class(self, entity_id: str) -> str | None:
        if self._replay and entity_id in self._observed.state_classes:
            return self._observed.state_classes[entity_id]
        value = self._inner.state_class(entity_id)
        self._observed.state_classes[entity_id] = value
        return value

    def retention_days(self) -> int:
        if self._replay and self._observed.retention_days is not None:
            return self._observed.retention_days
        value = self._inner.retention_days()
        self._observed.retention_days = value
        return value

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> dict[str, Any]:
        """Everything held, as plain data (JSON-able, no live object)."""

        def events(store: dict[str, _Events]) -> dict[str, Any]:
            return {
                entity: {
                    "lo": _iso(s.lo),
                    "obtained": _iso(s.obtained),
                    "base": list(s.base) if s.base is not None else None,
                    "events": [[_iso(row[0]), *row[1:]] for row in s.events],
                }
                for entity, s in sorted(store.items())
            }

        observed = self._observed
        return {
            "version": SNAPSHOT_VERSION,
            "raw": events(self._raw),
            "capacity": events(self._capacity),
            "stats": {
                sid: {
                    "lo": _iso(s.lo),
                    "obtained": _iso(s.obtained),
                    "rows": [
                        {**row, "start": _iso(row["start"])} for _t, row in sorted(s.rows.items())
                    ],
                }
                for sid, s in sorted(self._stats.items())
            },
            "anchors": [
                {
                    "entity": entity,
                    "before": _iso(before),
                    "max_days": days,
                    "found": [_iso(found[0]), found[1]] if found is not None else None,
                }
                for (entity, before, days), found in sorted(
                    self._anchors.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])
                )
            ],
            "observed": {
                "statistics_units": {
                    k: dict(v) for k, v in sorted(observed.statistics_units.items())
                },
                "unchanged_rows": {k: dict(v) for k, v in sorted(observed.unchanged_rows.items())},
                "units": dict(sorted(observed.units.items())),
                "state_classes": dict(sorted(observed.state_classes.items())),
                "retention_days": observed.retention_days,
            },
        }

    @classmethod
    def from_snapshot(
        cls,
        data: dict[str, Any],
        inner: Storage,
        *,
        clock: Callable[[], datetime],
        settle: timedelta = timedelta(seconds=30),
        **options: Any,
    ) -> Cache:
        """A cache that holds ``data`` and treats all of it as settled (a replay)."""
        if data.get("version") != SNAPSHOT_VERSION:
            raise ValueError(
                f"cache snapshot version {data.get('version')!r} is not {SNAPSHOT_VERSION}"
            )
        cache = cls(inner, clock=clock, settle=settle, **options)
        cache._replay = True

        def load_events(target: dict[str, _Events], source: dict[str, Any]) -> None:
            for entity, s in source.items():
                obtained = _parse(s["obtained"])
                target[entity] = _Events(
                    lo=_parse(s["lo"]),
                    hi=obtained,
                    obtained=obtained,
                    base=tuple(s["base"]) if s["base"] is not None else None,
                    events=[(_parse(row[0]), *row[1:]) for row in s["events"]],
                )

        load_events(cache._raw, data["raw"])
        load_events(cache._capacity, data["capacity"])
        for sid, s in data["stats"].items():
            obtained = _parse(s["obtained"])
            cache._stats[sid] = _Rows(
                lo=_parse(s["lo"]),
                hi=obtained,
                obtained=obtained,
                rows={
                    _parse(row["start"]): {**row, "start": _parse(row["start"])}
                    for row in s["rows"]
                },
            )
        for item in data["anchors"]:
            found = item["found"]
            cache._anchors[(item["entity"], _parse(item["before"]), item["max_days"])] = (
                (_parse(found[0]), found[1]) if found is not None else None
            )
        observed = data["observed"]
        cache._observed = _Observed(
            statistics_units={k: dict(v) for k, v in observed["statistics_units"].items()},
            unchanged_rows={k: dict(v) for k, v in observed["unchanged_rows"].items()},
            units=dict(observed["units"]),
            state_classes=dict(observed["state_classes"]),
            retention_days=observed["retention_days"],
        )
        return cache
