"""Shared test support: entry options built by role (ADR-023 decision 1).

The BMS counters are one sensor per role since ADR-023. Many older tests describe
their system as lists (PV inputs, BMS inputs, outputs); ``role_options`` maps those
lists onto the roles: the first BMS input is the grid import counter, the second the
discharge counter; the first output is the charge counter, the second the grid export
counter. More than two of either has no meaning any more and is refused loudly, so a
test cannot silently lose a sensor.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

PV_INPUT_SENSORS = "pv_input_sensors"
GRID_IMPORT_SENSOR = "grid_import_sensor"
DISCHARGE_SENSOR = "discharge_sensor"
CHARGE_SENSOR = "charge_sensor"
GRID_EXPORT_SENSOR = "grid_export_sensor"


def role_options(
    pv: Sequence[str] = (),
    bms: Sequence[str] = (),
    outputs: Sequence[str] = (),
    **extra: Any,
) -> dict[str, Any]:
    """Entry options with the given sensors assigned by role; unset roles are absent."""
    if len(bms) > 2 or len(outputs) > 2:
        raise ValueError("a system has at most two BMS inputs and two outputs (ADR-023)")
    options: dict[str, Any] = {PV_INPUT_SENSORS: list(pv), **extra}
    for key, entity in zip((GRID_IMPORT_SENSOR, DISCHARGE_SENSOR), bms, strict=False):
        options[key] = entity
    for key, entity in zip((CHARGE_SENSOR, GRID_EXPORT_SENSOR), outputs, strict=False):
        options[key] = entity
    return options


# ---------------------------------------------------------------------------
# A recorder that behaves like Home Assistant's where Effy's writes meet it (TASK-0035)
# ---------------------------------------------------------------------------

_FIVE_MINUTES = timedelta(minutes=5)
_HOUR = timedelta(hours=1)


class StrictRecorderMixin:
    """Home Assistant 2026.10.4's recorder as far as Effy's writes meet it (TASK-0035).

    Mixed in front of a stored recorder with a ``rows`` dict of
    ``(table, statistic_id, start) -> columns`` (``table`` is ``"5minute"`` or ``"hour"``)
    whose ``async_import_statistics`` upserts into it and replaces every column. What it
    adds, each point read from ``homeassistant/components/recorder`` of 2026.10.4:

    - **One FIFO queue.** ``async_import_statistics`` only queues the import (a
      ``RecorderTask`` on a ``SimpleQueue``); ``async_block_till_done`` runs the queue in
      order, and so does ``run_queue``. A compile task queued before an import runs
      before it.
    - **The compile inserts with a plain ``session.add``.** For a period it inserts one
      5-minute row for every natively recorded statistic that has a value in it
      (``native_value``); when the period starts at minute 55 it then rolls the hour up
      from the 5-minute rows of every statistic with any (Effy's rows included), again
      by plain insert. A row that exists already makes the flush fail with a UNIQUE
      violation, and the **whole period is rolled back**: no 5-minute row, no roll-up of
      any statistic, no run marker. The failure is recorded in ``failures`` (Home
      Assistant logs ``Blocked attempt to insert duplicated statistic rows``).
    - **The run table.** A compiled period is in ``runs``; compiling it twice does
      nothing.
    - **The catch-up is one task over many periods** and stops at the first failure
      (``compile_missing_statistics``); its last period fires the 5-minute event.
    """

    queue: list[tuple[Any, ...]]
    runs: set[datetime]
    failures: list[tuple[datetime, str, str, datetime]]
    events: list[datetime]
    native_ids: tuple[str, ...]
    native_value: Callable[[str, datetime], float | None]

    def strict_setup(
        self,
        native_ids: Iterable[str],
        native_value: Callable[[str, datetime], float | None] = lambda _sid, _slot: 99.0,
    ) -> None:
        """Say which statistics the recorder compiles natively, and with which value."""
        self.queue = []
        self.runs = set()
        self.failures = []
        self.events = []
        self.native_ids = tuple(native_ids)
        self.native_value = native_value

    # --- Effy's side: imports are queued, not applied -------------------------------
    def async_import_statistics(self, metadata: Any, stats: Any, table: Any) -> None:
        self.queue.append(("import", metadata, list(stats), table))

    async def async_block_till_done(self) -> None:
        self.run_queue()

    # --- the recorder's side ----------------------------------------------------------
    def queue_compile(self, slot: datetime, *, fire_event: bool = True) -> None:
        """The periodic tick (boundary + 10 s) queues the compile of the period ``slot``."""
        self.queue.append(("compile", slot, fire_event))

    def queue_catch_up(self, slots: list[datetime]) -> None:
        """The task queued once at recorder start: every missed period, in one call."""
        self.queue.append(("catch_up", list(slots)))

    def run_queue(self) -> None:
        """Run every queued task, in the order they were queued."""
        while self.queue:
            task = self.queue.pop(0)
            if task[0] == "import":
                super().async_import_statistics(task[1], task[2], task[3])  # type: ignore[misc]
            elif task[0] == "compile":
                self._compile(task[1], fire_event=task[2])
            else:
                slots = task[1]
                for index, slot in enumerate(slots):
                    if not self._compile(slot, fire_event=index == len(slots) - 1):
                        break

    def _short(self, statistic_id: str, hour: datetime) -> list[tuple[datetime, dict[str, Any]]]:
        return sorted(
            (ts, row)
            for (table, sid, ts), row in self.rows.items()  # type: ignore[attr-defined]
            if table == "5minute" and sid == statistic_id and hour <= ts < hour + _HOUR
        )

    def _compile(self, slot: datetime, *, fire_event: bool) -> bool:
        """Compile one period; ``False`` if it failed (and was rolled back)."""
        if slot in self.runs:
            return True
        rows: dict[tuple[str, str, datetime], dict[str, Any]] = self.rows  # type: ignore[attr-defined]
        staged: dict[tuple[str, str, datetime], dict[str, Any]] = {}
        for sid in self.native_ids:
            value = self.native_value(sid, slot)
            if value is None:
                continue
            key = ("5minute", sid, slot)
            if key in rows:
                self.failures.append((slot, "5minute", sid, slot))
                return False
            staged[key] = {
                "mean": value,
                "state": value,
                "min": value,
                "max": value,
                "sum": None,
                "last_reset": None,
            }
        if slot.minute == 55:
            hour = slot.replace(minute=0)
            sids = {
                sid
                for (table, sid, ts) in list(rows) + list(staged)
                if table == "5minute" and hour <= ts < hour + _HOUR
            }
            for sid in sorted(sids):
                together = {
                    ts: row
                    for ts, row in self._short(sid, hour)
                    if ("5minute", sid, ts) not in staged
                }
                together.update(
                    {
                        ts: row
                        for (table, s, ts), row in staged.items()
                        if s == sid and table == "5minute"
                    }
                )
                ordered = [together[ts] for ts in sorted(together)]
                means = [row["mean"] for row in ordered if row["mean"] is not None]
                mins = [row["min"] for row in ordered if row["min"] is not None]
                maxs = [row["max"] for row in ordered if row["max"] is not None]
                key = ("hour", sid, hour)
                if key in rows:
                    self.failures.append((slot, "hour", sid, hour))
                    return False
                staged[key] = {
                    "mean": sum(means) / len(means) if means else None,
                    "state": ordered[-1]["state"],
                    "min": min(mins) if mins else None,
                    "max": max(maxs) if maxs else None,
                    "sum": None,
                    "last_reset": None,
                }
        rows.update(staged)
        self.runs.add(slot)
        if fire_event:
            self.events.append(slot)
        return True

    # --- assertions -------------------------------------------------------------------
    def compile_through(self, last: datetime, *, first: datetime) -> None:
        """Compile every period from ``first`` to ``last`` (inclusive), as the ticks did."""
        slot = first
        while slot <= last:
            self.queue_compile(slot, fire_event=False)
            slot += _FIVE_MINUTES
        self.run_queue()


# ---------------------------------------------------------------------------
# Storage doubles (TASK-0036, ADR-025)
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402


class FakeClock:
    """A settable clock; the storage and the cache under test read the same one."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class UnrecordedRead(Exception):
    """A read the recording does not contain (the replay's inner storage raises it)."""


class RaisingStorage:
    """A ``Storage`` whose every read raises: the inner storage of a replay."""

    async def raw_states(self, *args: Any) -> Any:
        raise UnrecordedRead(("raw_states", *args))

    async def last_valid_state_before(self, *args: Any) -> Any:
        raise UnrecordedRead(("last_valid_state_before", *args))

    async def statistics(self, *args: Any) -> Any:
        raise UnrecordedRead(("statistics", *args))

    async def capacity_states(self, *args: Any) -> Any:
        raise UnrecordedRead(("capacity_states", *args))

    async def statistics_units(self, *args: Any) -> Any:
        raise UnrecordedRead(("statistics_units", *args))

    async def stored_means(self, *args: Any) -> Any:
        raise UnrecordedRead(("stored_means", *args))

    async def unchanged_rows(self, *args: Any, **kwargs: Any) -> Any:
        raise UnrecordedRead(("unchanged_rows", *args))

    def written(self, *args: Any) -> None:
        return None

    def unit(self, *args: Any) -> Any:
        raise UnrecordedRead(("unit", *args))

    def state_class(self, *args: Any) -> Any:
        raise UnrecordedRead(("state_class", *args))

    def retention_days(self) -> Any:
        raise UnrecordedRead(("retention_days",))

    def set_timing(self, timings: Any) -> None:
        return None


class FakeStorage:
    """The recorder's rule, applied to lists (``state_changes_during_period``, 2026.10.4).

    A raw read returns the latest state strictly before ``start`` stamped ``start`` (if
    there is one), then the changes with ``start < time < end``; a statistics read the
    rows with ``start <= row start < end``. Everything is **committed late**: a state is
    visible ``delay`` seconds after its time, a 5-minute row 10 s plus ``row_delay``
    after the slot ended, so a read at the wrong moment misses the newest, as the real
    recorder does. ``calls`` logs every read that reaches it; ``direct_*`` do not.
    """

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.events: dict[str, list[tuple[datetime, str, datetime]]] = {}
        self.capacity: dict[str, list[tuple[datetime, str, str | None, datetime]]] = {}
        self.rows: dict[str, dict[datetime, tuple[dict[str, Any], datetime]]] = {}
        self.units: dict[str, str] = {}
        self.live_units: dict[str, str] = {}
        self.state_classes: dict[str, str | None] = {}
        self.retention = 10
        self.calls: list[tuple[Any, ...]] = []
        self.written_calls: list[tuple[Any, ...]] = []
        self.timing: Any = None
        self.yield_in_reads = False
        self.unchanged_result: dict[str, frozenset[datetime]] = {
            "short": frozenset(),
            "hourly": frozenset(),
        }

    # --- filling -----------------------------------------------------------------
    def add_state(self, entity_id: str, ts: datetime, state: str, delay: float = 0.0) -> None:
        self.events.setdefault(entity_id, []).append((ts, state, ts + timedelta(seconds=delay)))
        self.events[entity_id].sort()

    def add_capacity(
        self, entity_id: str, ts: datetime, state: str, unit: str | None, delay: float = 0.0
    ) -> None:
        self.capacity.setdefault(entity_id, []).append(
            (ts, state, unit, ts + timedelta(seconds=delay))
        )
        self.capacity[entity_id].sort(key=lambda r: r[0])

    def add_row(
        self, statistic_id: str, start: datetime, mean: float, row_delay: float = 0.0
    ) -> None:
        row = {"start": start, "mean": mean, "change": mean / 10}
        visible = start + _FIVE_MINUTES + timedelta(seconds=10 + row_delay)
        self.rows.setdefault(statistic_id, {})[start] = (row, visible)

    # --- the recorder's rule -----------------------------------------------------------
    def direct_raw(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, str]]:
        now = self.clock.now
        visible = [(t, s) for t, s, v in self.events.get(entity_id, []) if v <= now]
        before = [(t, s) for t, s in visible if t < start]
        result = [(start, before[-1][1])] if before else []
        result.extend((t, s) for t, s in visible if start < t < end)
        return result

    def direct_capacity(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, str, str | None]]:
        now = self.clock.now
        visible = [(t, s, u) for t, s, u, v in self.capacity.get(entity_id, []) if v <= now]
        before = [(t, s, u) for t, s, u in visible if t < start]
        result = [(start, before[-1][1], before[-1][2])] if before else []
        result.extend((t, s, u) for t, s, u in visible if start < t < end)
        return result

    def direct_statistics(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        now = self.clock.now
        result: dict[str, list[dict[str, Any]]] = {}
        for sid in entity_ids:
            rows = [
                dict(row)
                for t, (row, visible) in sorted(self.rows.get(sid, {}).items())
                if start <= t < end and visible <= now
            ]
            if rows:
                result[sid] = rows
        return result

    # --- Storage -------------------------------------------------------------------------
    async def _maybe_yield(self) -> None:
        if self.yield_in_reads:
            await asyncio.sleep(0)

    async def raw_states(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, str]]:
        self.calls.append(("raw_states", entity_id, start, end))
        await self._maybe_yield()
        return self.direct_raw(entity_id, start, end)

    async def last_valid_state_before(
        self, entity_id: str, before: datetime, max_history_days: int
    ) -> tuple[datetime, str] | None:
        self.calls.append(("last_valid_state_before", entity_id, before, max_history_days))
        await self._maybe_yield()
        floor = before - timedelta(days=max_history_days)
        for t, s, v in reversed(self.events.get(entity_id, [])):
            if floor <= t < before and v <= self.clock.now:
                try:
                    float(s)
                except ValueError:
                    continue
                return (t, s)
        return None

    async def statistics(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        self.calls.append(("statistics", tuple(entity_ids), start, end))
        await self._maybe_yield()
        return self.direct_statistics(entity_ids, start, end)

    async def capacity_states(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, str, str | None]]:
        self.calls.append(("capacity_states", entity_id, start, end))
        await self._maybe_yield()
        return self.direct_capacity(entity_id, start, end)

    async def statistics_units(self, entity_ids: list[str]) -> dict[str, str]:
        self.calls.append(("statistics_units", tuple(entity_ids)))
        await self._maybe_yield()
        return {eid: self.units[eid] for eid in entity_ids if eid in self.units}

    async def stored_means(
        self, statistic_id: str, start: datetime, end: datetime
    ) -> tuple[dict[datetime, float], dict[datetime, float]]:
        self.calls.append(("stored_means", statistic_id))
        await self._maybe_yield()
        return {}, {}

    async def unchanged_rows(
        self,
        statistic_id: str,
        unit: str,
        short: list[tuple[datetime, float]],
        hourly: dict[datetime, tuple[float, float]],
        notes: dict[str, str] | None = None,
    ) -> dict[str, frozenset[datetime]]:
        self.calls.append(("unchanged_rows", statistic_id, unit, len(short), len(hourly)))
        await self._maybe_yield()
        return dict(self.unchanged_result)

    def written(self, statistic_ids: list[str], start: datetime, end: datetime) -> None:
        self.written_calls.append((tuple(statistic_ids), start, end))

    def unit(self, entity_id: str) -> str:
        self.calls.append(("unit", entity_id))
        return self.live_units.get(entity_id, "W")

    def state_class(self, entity_id: str) -> str | None:
        self.calls.append(("state_class", entity_id))
        return self.state_classes.get(entity_id)

    def retention_days(self) -> int:
        self.calls.append(("retention_days",))
        return self.retention

    def set_timing(self, timings: Any) -> None:
        self.timing = timings
