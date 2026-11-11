"""The recorder-backed ``Storage`` (ADR-025, TASK-0036).

Everything Effy reads from the recorder is one of the methods below. Each runs the
function of ``history.py`` of the same kind in the recorder's executor, through the
timing record's ``recorder_job`` (ADR-022), with the label the detail log has always
used for that kind of read.

The functions are looked up on ``reads`` (the ``history`` module, which passes
itself) **at the moment of the call**, not imported here: ``history`` imports this
module, and the tests of the history code replace those functions by name
(``monkeypatch.setattr(history, "_fetch_statistics", ...)``). Moving their bodies
here is a later step (TASK-0036 findings).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .storage import CapacityState, RawState, StatRow


class RecorderStorage:
    """Reads from the recorder; the storage underneath a ``Cache``."""

    def __init__(self, hass: HomeAssistant, reads: Any, *, timings: Any = None) -> None:
        self._hass = hass
        self._reads = reads
        self._timings = timings

    def set_timing(self, timings: Any) -> None:
        """The timing record of the run that holds the recorder-job lock (ADR-022)."""
        self._timings = timings

    async def _job(self, function: str, *args: Any, label: str = "", source: str = "") -> Any:
        reads = self._reads
        timings = self._timings if self._timings is not None else reads._NO_TIMINGS
        recorder = reads.get_recorder(self._hass)
        return await timings.recorder_job(
            recorder,
            getattr(reads, function),
            self._hass,
            *args,
            label=label,
            source=source,
        )

    async def raw_states(self, entity_id: str, start: datetime, end: datetime) -> list[RawState]:
        result: list[RawState] = await self._job(
            "_fetch_raw_energy_states", entity_id, start, end, label="raw states", source=entity_id
        )
        return result

    async def last_valid_state_before(
        self, entity_id: str, before: datetime, max_history_days: int
    ) -> RawState | None:
        result: RawState | None = await self._job(
            "_fetch_last_valid_state_before",
            entity_id,
            before,
            max_history_days,
            label="anchor lookback",
            source=entity_id,
        )
        return result

    async def statistics(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[StatRow]]:
        result: dict[str, list[StatRow]] = await self._job(
            "_fetch_statistics", list(entity_ids), start, end, label="power statistics"
        )
        return result

    async def capacity_states(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[CapacityState]:
        result: list[CapacityState] = await self._job(
            "_fetch_capacity_states",
            entity_id,
            start,
            end,
            label="capacity states",
            source=entity_id,
        )
        return result

    async def stored_means(
        self, statistic_id: str, start: datetime, end: datetime
    ) -> tuple[dict[datetime, float], dict[datetime, float]]:
        result: tuple[dict[datetime, float], dict[datetime, float]] = await self._job(
            "_fetch_integral_source",
            statistic_id,
            start,
            end,
            label="integral source",
            source=statistic_id,
        )
        return result

    async def statistics_units(self, entity_ids: list[str]) -> dict[str, str]:
        result: dict[str, str] = await self._job(
            "_get_statistics_units", list(entity_ids), label="units"
        )
        return result

    async def unchanged_rows(
        self,
        statistic_id: str,
        unit: str,
        short: list[tuple[datetime, float]],
        hourly: dict[datetime, tuple[float, float]],
        notes: dict[str, str] | None = None,
        values: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self._job(
            "_unchanged_rows",
            statistic_id,
            unit,
            short,
            hourly,
            notes,
            values,
            label="stored rows",
            source=statistic_id,
        )
        return result

    def written(self, statistic_ids: list[str], start: datetime, end: datetime) -> None:
        """Nothing is kept here, so nothing is stale."""
        return None

    def unit(self, entity_id: str) -> str:
        unit: str = self._reads._get_unit(self._hass, entity_id)
        return unit

    def state_class(self, entity_id: str) -> str | None:
        state_class: str | None = self._reads._get_state_class(self._hass, entity_id)
        return state_class

    def retention_days(self) -> int:
        days: int = self._reads._get_short_term_retention_days(self._hass)
        return days
