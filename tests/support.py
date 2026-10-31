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
