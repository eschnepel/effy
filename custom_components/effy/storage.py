"""The interface to everything Effy reads from the recorder (ADR-025).

Pure: no Home Assistant import, so ``cache.py`` and the tests load it by file path
(ADR-000 §6). ``history.py`` takes a ``Storage`` and cannot tell which one it is:

- ``RecorderStorage`` (``recorder_storage.py``) asks the recorder, in its executor;
- ``Cache`` (``cache.py``) answers from memory what it can and asks the storage it
  wraps for the rest;
- the tests have a storage that applies the recorder's rule to a list of rows, and
  a replay is a ``Cache`` loaded from a snapshot over a storage that raises.

Each method has the signature and the result of the function of ``history.py`` it
replaces, so that a ``Cache`` can be substituted for any ``Storage``. The writer is
not part of it (ADR-025 open point 10).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

# One row of ``statistics_during_period``: ``start`` (a datetime), ``mean``,
# ``change`` (the two types Effy asks for).
StatRow = dict[str, Any]

# One state: ``(time, state string)``.
RawState = tuple[datetime, str]

# One state of the capacity entity: ``(time, state string, unit or None)``.
CapacityState = tuple[datetime, str, str | None]


class Storage(Protocol):
    """Reads of source data and of Effy's own stored rows."""

    async def raw_states(self, entity_id: str, start: datetime, end: datetime) -> list[RawState]:
        """State changes of ``entity_id`` as ``state_changes_during_period`` returns them.

        The latest state strictly before ``start`` first, stamped ``start`` (if the
        recorder holds one that old), then every change with ``start < time < end``.
        """
        ...

    async def last_valid_state_before(
        self, entity_id: str, before: datetime, max_history_days: int
    ) -> RawState | None:
        """The most recent numeric state strictly before ``before`` (ADR-013, ADR-014)."""
        ...

    async def statistics(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[StatRow]]:
        """5-minute ``mean`` and ``change`` rows with ``start <= row start < end``.

        An id with no row in the range is absent from the result.
        """
        ...

    async def capacity_states(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[CapacityState]:
        """Like ``raw_states`` for the battery capacity entity, with each state's unit."""
        ...

    async def statistics_units(self, entity_ids: list[str]) -> dict[str, str]:
        """The unit the recorder's statistics are stored in, per id (metadata)."""
        ...

    async def stored_means(
        self, statistic_id: str, start: datetime, end: datetime
    ) -> tuple[dict[datetime, float], dict[datetime, float]]:
        """The stored 5-minute and hourly ``mean`` of one series, ``start <= row start < end``.

        Effy's own rows: **observed, never reused**. Read once, by the delta derivation
        (ADR-027 decision 5), which is no part of a recorded history run; ``({}, {})`` for a
        series with no rows.
        """
        ...

    async def unchanged_rows(
        self,
        statistic_id: str,
        unit: str,
        short: list[tuple[datetime, float]],
        hourly: dict[datetime, tuple[float, float]],
        notes: dict[str, str] | None = None,
        values: bool = False,
    ) -> dict[str, Any]:
        """Which of the rows to write are already stored as they are (ADR-024).

        Effy's own rows: **observed, never reused**: every call reads the recorder.
        The result has the unchanged starts, ``"short"`` and ``"hourly"``. With ``values``
        (only for the PV inputs' effective series, ADR-024 Amendment 2026-11-09) it also
        has ``"stored"``, the stored 5-minute value of each row to write, in ``unit``; a
        storage that cannot say leaves it out, and the run then has no PV delta.
        """
        ...

    def written(self, statistic_ids: list[str], start: datetime, end: datetime) -> None:
        """Effy has written rows of ``statistic_ids`` in ``[start, end)``.

        Whatever a storage holds for these ids is stale. Called after every write,
        because the standby and efficiency readers read back what Effy wrote.
        """
        ...

    def unit(self, entity_id: str) -> str:
        """The live unit of the entity (the state machine, not the recorder)."""
        ...

    def state_class(self, entity_id: str) -> str | None:
        """The live state class of the entity (the state machine)."""
        ...

    def retention_days(self) -> int:
        """The recorder's short-term retention in days."""
        ...

    def set_timing(self, timings: Any) -> None:
        """The timing record of the run that holds the recorder-job lock (ADR-022)."""
        ...
