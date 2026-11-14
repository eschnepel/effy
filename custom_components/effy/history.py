"""
History recalculation for Effy.

Uses a mix of the Home Assistant statistics API and raw state history to:
1. Get a 5-minute-resolution reading for every configured sensor —
   ``statistics_during_period``'s ``mean`` for MEASUREMENT/TOTAL-as-power
   sensors (gap-interpolated for *input* sensors, see point 4 below), or a
   trapezoidal-rule redistribution of raw state history for
   TOTAL_INCREASING/TOTAL-as-energy sensors (ADR-012,
   ``calculation.trapezoidal_slot_contributions`` — replaces ADR-009's
   neighbor-steal smoothing).
2. Apply the same loss-distribution algorithm as the live sensor
   (``distribute_loss`` from calculation.py — see ADR-001) — using the
   gap-interpolated series from point 4 for power-family inputs, not the
   raw gappy one.
3. Write back corrected statistics for all effy_* output sensors,
   **overwriting** any existing rows for the same slots (ADR-004): the
   post-waterfall "effective" value (input sensors only, as before), and,
   new in ADR-012, the raw pre-waterfall "derived power" for every
   energy-family sensor (inputs and outputs) as a separate `effy_*_power`
   statistic.
4. Bridge short (<= ``calculation.INTERPOLATION_MAX_GAP_SLOTS``) runs of
   missing slots in a power-family *input* sensor's ``mean`` via linear
   interpolation (``calculation.interpolate_slot_gaps``) before step 2
   runs, and separately expose that same interpolated series as its own
   `effy_*_smoothed` statistic — output sensors and energy-family sensors
   are unaffected (out of scope for this feature).
5. Write an explicit 0, not nothing, for a genuinely idle energy-family
   stretch — online-but-unchanging (e.g. an empty battery's 0 discharge,
   several zero-import days) or offline-then-recovered with a net-zero
   delta alike (ADR-013). The trapezoidal cap itself
   (``calculation.TRAPEZOID_MAX_MINUTES``) is 120 minutes, not 15
   (ADR-014 — 15 minutes was too tight for real low-resolution meters,
   producing a visibly oscillating derived-power curve). RECENT_RECALC_WINDOW
   (the slot-timer-driven "recent" recalc's own look-back) is deliberately
   small and, since ADR-014, deliberately decoupled from that cap — it
   controls how many slots get *rewritten* each cycle, not how far back
   raw history is read to correctly anchor them. A staged lookback
   (``_fetch_last_valid_state_before``, 30 min → 1 day → the full
   configured history range) finds the real pre-outage baseline on the
   rare occasions a cycle's own small window starts mid-outage, regardless
   of how far back that baseline is — staged specifically to bound cost
   when this fires for many sensors at once (e.g. right after a Home
   Assistant restart).

State-class handling (ADR-003)
-------------------------------
TOTAL_INCREASING  → energy-family; per-slot value from the trapezoidal
                    rule over raw state history (ADR-012), not the
                    statistics API's ``change`` field anymore.
TOTAL / MEASUREMENT → power-family; request ``mean`` from the statistics
                    API, gap-interpolated for input sensors (point 4
                    above), otherwise unchanged.

All sources are written back with both ``mean`` and ``state`` — the
latter is the same per-slot value (there is exactly one reading per
5-minute slot, so "last/only reading in the interval" and "mean of the
interval" coincide at short-term resolution). Filling ``state`` is
required for consumers that read the raw per-period value directly
instead of ``mean`` — e.g. the ``apexcharts-card`` frontend card renders
gaps for statistics rows where ``state`` is null, even when ``mean`` is
populated (see ADR-003 amendment below).

Units (ADR-002): statistic values are read and written in the sensor's
original unit (W, kW, Wh, kWh); normalization to W happens exactly once,
inside ``distribute_loss``.

----------------------------------------------------------------------------
WARNING – INTERNAL RECORDER API USAGE (ADR-003 5-minute requirement)
----------------------------------------------------------------------------
ADR-003 requires Effy to provide genuine 5-minute statistics in the
recorder for its `effy_*` sensors. Home Assistant's public, stable API
(`async_add_external_statistics` / `async_import_statistics`) can ONLY
write hourly long-term statistics – there is no public API to retroactively
write 5-minute short-term statistics (the `statistics_short_term` table).
Short-term statistics are normally produced exclusively by the recorder's
own periodic compiler, which always runs against "now", never against a
caller-supplied past timestamp, deriving them from real `states` rows for
that exact 5-minute window – not from arbitrary historical values supplied
by an integration.

To honour ADR-003 anyway, this module calls the recorder instance's own
``async_import_statistics(metadata, stats, table)`` method – a PUBLIC
``@callback`` on the ``Recorder`` class itself (see
``homeassistant/components/recorder/core.py``) – with
``table=StatisticsShortTerm`` instead of the hardcoded ``table=Statistics``
that the documented wrappers (`async_add_external_statistics` /
`async_import_statistics` module-level function) force. This method:

  - is called directly from the event loop (it is a ``@callback``, not a
    blocking function – calling it from a worker thread, e.g. via
    ``recorder.async_add_executor_job``, raises
    ``RuntimeError: Detected unsafe call not in recorder thread``, since
    `StatisticsMetaManager` asserts it is only ever touched from the
    recorder's own thread),
  - schedules an internal ``ImportStatisticsTask`` on the recorder's task
    queue, which the recorder thread processes asynchronously,
  - resolves/creates statistic metadata and performs a genuine
    update-or-insert per `(metadata_id, start)`, so re-running
    recalculation overwrites existing rows cleanly (ADR-004),
  - when writing to `StatisticsShortTerm`, also refreshes the recorder's
    internal `ShortTermStatisticsRunCache` so the live periodic compiler
    stays consistent with what we just wrote.

Because scheduling is fire-and-forget, this module calls
``await instance.async_block_till_done()`` after queuing all import tasks,
to ensure every task has actually been processed by the recorder thread
before returning a row count to the caller.

This still relies on ``table=StatisticsShortTerm`` being accepted by
``async_import_statistics`` / `ImportStatisticsTask`, which is an
implementation detail of internal recorder plumbing, not a documented,
versioned contract:

  - the ``table`` parameter and the existence of ``StatisticsShortTerm`` as
    an importable model are NOT guaranteed to keep their name, shape, or
    behaviour across HA core releases — a core update can silently break
    this module, requiring an update,
  - the public, documented wrappers (`async_add_external_statistics`,
    `async_import_statistics` at module level) deliberately hardcode
    ``table=Statistics`` and validate `source != DOMAIN`/`"recorder"` to
    prevent exactly this kind of direct short-term-table write; this
    module intentionally calls the lower-level, less-guarded method
    instead.

Two recorder tables are populated for every recalculated slot:

  - `statistics_short_term` (5-minute, ADR-003 requirement) – HA purges
    this table after the recorder's actually configured `purge_keep_days`
    (10 days by default, but read dynamically from `instance.keep_days`
    at runtime since users commonly change this setting), so only slots
    within that retention window are written here. Writing further back
    would be pointless: HA's own purge task deletes those rows again
    within hours of the next purge cycle regardless of what we write.
  - `statistics` (hourly, long-term, persists forever) – populated for the
    *entire* `max_history_days` window by averaging the 5-minute effective
    values per clock-hour, so historical data survives beyond the 10-day
    short-term retention window (e.g. for the Energy dashboard and
    long-range history graphs).
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from itertools import chain
from operator import itemgetter
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.components.recorder import get_instance as get_recorder
from homeassistant.components.recorder.db_schema import Statistics, StatisticsShortTerm
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

try:
    # StatisticMeanType / unit_class were introduced in newer HA core
    # releases (≈2025.10+) and are required there; older cores (this was
    # originally developed/tested against 2025.1.4) don't have this enum
    # and derive everything from has_mean/has_sum instead. Import
    # defensively so the same history.py works across both API shapes.
    from homeassistant.components.recorder.models import StatisticMeanType

    _HAS_STATISTIC_MEAN_TYPE = True
except ImportError:
    StatisticMeanType = None
    _HAS_STATISTIC_MEAN_TYPE = False

from .sensor_utils import (
    RECORDER_SETTLE,
    SLOT_MINUTES,
    effective_unit_for,
    is_energy_family,
    settled_before,
    to_power_equivalent,
)
from .calculation import (
    DRAIN_LOOKBACK,
    EFF_VALIDATION_DELAY,
    BatSlot,
    EffRoles,
    EffSlot,
    IntegralRow,
    IntegralStep,
    RunState,
    ValleyState,
    DrainBreak,
    DrainPeak,
    DrainSeen,
    DrainState,
    SensorReading,
    TRAPEZOID_MAX_MINUTES,
    _parse_energy_state,
    capacity_by_slot,
    combined_integral_rows,
    convert_unit,
    drain_activity_start,
    drain_feed,
    drop_open_slot,
    distribute_loss,
    effective_in_original_unit,
    energy_drain_events,
    integral_rows,
    interpolate_slot_gaps,
    pv_delta,
    slot_energy_steps,
    steps_from_running_totals,
    power_drain_events,
    pv_share,
    recent_rewrite_start,
    recent_trigger_from,
    run_feed,
    run_learn_resolution,
    split_eps_readings,
    trapezoidal_jump_windows,
    trapezoidal_slot_contributions,
    valley_feed,
    weighted_share,
)
from .const import (
    CONF_MAX_HISTORY_DAYS,
    DEFAULT_MAX_HISTORY_DAYS,
    PV_DELTA_INTEGRAL_TODAY_SUFFIX,
    PV_DELTA_TODAY_ENTITY_ID,
    PV_DELTA_TOTAL_ENTITY_ID,
    PV_SHARE_ENTITY_ID,
    PV_SUM_ENTITY_ID,
)
from .cache import Cache
from .debug_export import ExportOutcome, ExportRecord
from .effy_exports import EFFY_STANDBY_POWER_ENTITY_ID
from .recorder_storage import RecorderStorage
from .storage import IntegralStat, Storage
from .inputs import (
    all_input_ids,
    battery_hysteresis,
    bms_input_ids,
    capacity_id,
    charge_id,
    discharge_id,
    eps_id,
    grid_export_id,
    grid_import_id,
    pv_input_ids,
    series_only_ids,
    soc_id,
)
from .inputs import output_ids as configured_output_ids

_LOGGER = logging.getLogger(__name__)

# What a recalculation returns: (short-term rows written, recalculated_from, touched entity
# IDs, last values, the PV deltas). The PV deltas (ADR-027 decision 2) map the entity ID of
# each PV input to ``(delta, unit)`` with the unit "Wh" (effective unit W) or "kWh" (kW);
# ``None`` if the stored rows could not be read: no delta, which is not 0.
_PvDeltas = dict[str, tuple[float, str]]
_RecalcResult = tuple[
    int, datetime | None, set[str], dict[str, tuple[float, str]], _PvDeltas | None
]

# Type alias for a single statistics row returned by the recorder
StatRow = dict[str, Any]

_T = TypeVar("_T")

# Wall clock of the recalculation timing. A module attribute, read at every use,
# so tests can replace it with a fake that advances only where they say.
_clock: Callable[[], float] = time.perf_counter

# CPU seconds the whole process has used (all threads), for the detail block of the
# record (TASK-0010 patch 1). Read at every use, like ``_clock``.
_cpu_clock: Callable[[], float] = time.process_time

# Version of the detail block; it is also how a pasted record proves which build made it.
_RECORD_VERSION = 2


class _Figure:
    """Accumulated figures of one named step of the detail block.

    For a recorder job ``items`` is the rows it returned and ``run`` the seconds
    inside the executor (``wall`` minus ``run`` is the wait for it); for a stage
    ``items`` is what went in and ``out`` what came out.
    """

    __slots__ = ("calls", "items", "out", "run", "wall")

    def __init__(self, items: int = 0) -> None:
        self.calls = 0
        self.wall = 0.0
        self.run = 0.0
        self.items = items
        self.out = 0


class _WriteFigure:
    """One statistics write: its rows, preparing it, awaiting the recorder."""

    __slots__ = ("build", "hourly", "short", "wait")

    def __init__(self) -> None:
        self.short = 0
        self.hourly = 0
        self.build = 0.0
        self.wait = 0.0


def _rows_of(result: Any) -> int:
    """Rows a recorder job returned. A diagnostic: an odd result counts as 0 or 1."""
    try:
        if result is None:
            return 0
        if isinstance(result, dict):
            return sum(
                len(rows) if isinstance(rows, (list, set, frozenset)) else 1
                for rows in result.values()
            )
        if isinstance(result, list):
            return len(result)
    except TypeError:
        return 0
    return 1


def _environment_summary(hass: Any, history_days: int) -> str:
    """First line of the detail block: what produced the numbers. Never raises."""
    try:
        try:
            from homeassistant.const import __version__ as ha_version  # noqa: PLC0415
        except Exception:
            ha_version = "unknown"
        try:
            instance: Any = get_recorder(hass)
        except Exception:
            instance = None
        dialect = getattr(instance, "dialect_name", None)
        dialect = getattr(dialect, "value", dialect)
        keep_days = getattr(instance, "keep_days", None)
        return (
            f"detail v{_RECORD_VERSION}: HA {ha_version},"
            f" Python {platform.python_version()} {platform.machine() or 'unknown'},"
            f" {os.cpu_count() or 'unknown'} CPUs,"
            f" recorder {dialect or 'unknown'},"
            f" keep_days {'unknown' if keep_days is None else keep_days},"
            f" history days {history_days:g}"
        )
    except Exception:
        return f"detail v{_RECORD_VERSION}: environment unknown, history days {history_days:g}"


class _PhaseTimings:
    """Wall-clock accounting of one recalculation (TASK-0010, ADR-022 Decision 1).

    Three phases (GLOSSARY: Recalculation phase), and the rest:

    * ``fetch``: awaiting a recorder executor job (unit lookup, raw states,
      offline-anchor lookbacks, power-family statistics) — database reads;
    * ``compute``: Effy's own synchronous work on the event loop — jump windows,
      the trapezoid, interpolation, the slot loop, and the scans the fetch
      helper does between its jobs;
    * ``write``: ``_write_recorder_statistics`` — database writes;
    * ``other``: everything else in the call (total minus the phases).

    A "current phase" stopwatch: ``enter(name)`` books the time since the last
    switch to the phase that was current, so the phases and ``other`` always add
    up to the total and the instrumented code needs only one-line calls.

    ``parse_probe`` (history recalculation only) adds a separate measurement of
    what parsing costs: ONE extra pass over every raw state held after the last
    fetch, once converting the state strings (``_parse_energy_state``), once
    converting the timestamps to epoch seconds (what an array-based compute path
    would have to do; today's code does datetime arithmetic instead). It is
    booked as its own ``probe`` phase, never as ``compute``, so it does not
    disturb the compute figure. The code parses each state in
    ``trapezoidal_slot_contributions`` and, when a reading cache is passed (the
    coordinator always does), once more in the fetch helper — the probe is the
    cost of one such pass.

    ``detail_on`` (history recalculation only; TASK-0010 patch 1) adds a detail
    block to the record: every recorder job with its time in the executor and
    its wait for it, the compute stages, the three statistics writes (preparing
    apart from awaiting the recorder), CPU seconds next to wall seconds, the
    longest stretch without an ``await`` and the environment (GLOSSARY:
    Recalculation detail). The stages are carved out of ``compute``, never added
    to it. Without ``detail_on`` none of it costs anything: ``wrap`` hands the
    function back unchanged and ``recorder_job`` hands the job over unwrapped.

    A disabled instance (``_NO_TIMINGS``) does nothing and keeps no state; it is
    the default for every caller that does not ask for timing.
    """

    def __init__(
        self, kind: str, level: int, *, parse_probe: bool = False, enabled: bool = True
    ) -> None:
        self.kind = kind
        self.level = level
        self.enabled = enabled
        self.parse_probe = parse_probe and enabled
        self.seconds: dict[str, float] = {"fetch": 0.0, "compute": 0.0, "write": 0.0, "probe": 0.0}
        self.queries = 0
        self.raw_states = 0
        self.slots = 0
        self.rows = 0
        self.parse_values = 0.0
        self.parse_timestamps = 0.0
        self.cpu: dict[str, float] = {"fetch": 0.0, "compute": 0.0, "write": 0.0, "probe": 0.0}
        self.unchanged_rows: int | None = None
        self.hourly_left: int | None = None
        self.cache_reads: tuple[int, int] | None = None
        self.export_note: str | None = None
        self.stored_checks: dict[str, str] = {}
        self.detail = False
        self.environment = ""
        self.energy_sources = 0
        self.power_sources = 0
        self.jobs: dict[tuple[str, str], _Figure] = {}
        self.stages: dict[tuple[str, str], _Figure] = {}
        self.parts: dict[str, _Figure] = {}
        self.writes: dict[str, _WriteFigure] = {}
        self.longest_block = 0.0
        self._write_label = "write"
        self._phase: str | None = None
        self._started = self._since = self._resumed = _clock() if enabled else 0.0
        self._cpu_since = _cpu_clock() if enabled else 0.0
        self._ended: float | None = None

    def enter(self, phase: str | None) -> None:
        """Switch the current phase; ``None`` means no phase (time counts as other)."""
        if not self.enabled:
            return
        now, cpu_now = _clock(), _cpu_clock()
        if self._phase is not None:
            self.seconds[self._phase] += now - self._since
            self.cpu[self._phase] += cpu_now - self._cpu_since
        self._phase, self._since, self._cpu_since = phase, now, cpu_now

    def detail_on(self, environment: str) -> None:
        """Switch the detail block on; ``environment`` becomes its first line."""
        if self.enabled:
            self.detail = True
            self.environment = environment

    def note_unchanged(self, rows: int) -> None:
        """Short-term rows found already up to date (ADR-024); shown on the first line."""
        if self.enabled:
            self.unchanged_rows = rows

    def note_cache(self, hits: int, misses: int) -> None:
        """Reads served from the cache and reads that went to the recorder (TASK-0036)."""
        if self.enabled:
            self.cache_reads = (hits, misses)

    def note_export(self, seconds: float, outcome: ExportOutcome | None, error: str = "") -> None:
        """The debug export of this run (TASK-0037): where it went, how large, how long."""
        if not self.enabled:
            return
        if outcome is None:
            self.export_note = f", debug export failed after {seconds:.2f} s ({error})"
            return
        omitted = f", left out for size: {', '.join(outcome.omitted)}" if outcome.omitted else ""
        self.export_note = (
            f", debug export {outcome.size / 1024:.0f} KiB in {seconds:.2f} s"
            f" -> {outcome.path}{omitted}"
        )

    def note_hourly_left(self, rows: int) -> None:
        """Hourly rows left to the slot job's rewrite (TASK-0035); shown on the first line."""
        if self.enabled:
            self.hourly_left = rows

    def note_stored_checks(self, checks: dict[str, str]) -> None:
        """What each stored series looked like against the new rows (detail block only)."""
        if self.detail:
            self.stored_checks.update(checks)

    def note_sources(self, energy: int, power: int) -> None:
        if self.enabled:
            self.energy_sources, self.power_sources = energy, power

    def before_await(self) -> None:
        """The coroutine is about to give the event loop back: close the stretch it held it."""
        if self.enabled:
            self.longest_block = max(self.longest_block, _clock() - self._resumed)

    def after_await(self) -> None:
        if self.enabled:
            self._resumed = _clock()

    async def recorder_job(
        self,
        recorder: Any,
        func: Callable[..., _T],
        *args: Any,
        label: str = "",
        source: str = "",
    ) -> _T:
        """Await ``recorder.async_add_executor_job(func, *args)`` as a ``fetch`` query.

        With the detail on, ``func`` runs inside a twin that notes how long it ran in
        the executor thread, so the wait for the executor is wall minus run.
        """
        if not self.enabled:
            plain: _T = await recorder.async_add_executor_job(func, *args)
            return plain
        self.queries += 1
        previous = self._phase
        self.enter("fetch")
        began = self._since
        ran: list[float] = []

        def timed(*job_args: Any) -> _T:
            job_began = _clock()
            try:
                return func(*job_args)
            finally:
                ran.append(_clock() - job_began)

        self.before_await()
        try:
            result: _T = await recorder.async_add_executor_job(
                timed if self.detail else func, *args
            )
        finally:
            self.after_await()
            self.enter(previous)
        if self.detail:
            figure = self.jobs.setdefault((label or "recorder job", source), _Figure())
            figure.calls += 1
            figure.wall += _clock() - began
            figure.run += ran[-1] if ran else 0.0
            figure.items += _rows_of(result)
        return result

    @contextmanager
    def stage(self, name: str, source: str = "", *, items: int = 0) -> Iterator[_Figure]:
        """Time a block as one stage of ``compute``; the block may set ``.out`` on the figure."""
        figure = _Figure(items)
        if not self.detail:
            yield figure
            return
        began = _clock()
        try:
            yield figure
        finally:
            total = self.stages.setdefault((name, source), _Figure())
            total.calls += 1
            total.wall += _clock() - began
            total.items += figure.items
            total.out += figure.out

    def wrap(self, name: str, func: Callable[..., _T]) -> Callable[..., _T]:
        """``func`` itself, or with the detail on a twin adding its calls and time to ``name``."""
        if not self.detail:
            return func
        figure = self.parts.setdefault(name, _Figure())

        def timed(*args: Any, **kwargs: Any) -> _T:
            began = _clock()
            try:
                return func(*args, **kwargs)
            finally:
                figure.calls += 1
                figure.wall += _clock() - began

        return timed

    @contextmanager
    def write_call(self, label: str) -> Iterator[None]:
        """Name one statistics write and let the real writer report to this stopwatch."""
        if not self.detail:
            yield
            return
        self._write_label = label
        token = _active_timings.set(self)
        try:
            yield
        finally:
            _active_timings.reset(token)

    def note_write(self, short: int, hourly: int, build: float, wait: float) -> None:
        if not self.detail:
            return
        figure = self.writes.setdefault(self._write_label, _WriteFigure())
        figure.short += short
        figure.hourly += hourly
        figure.build += build
        figure.wait += wait

    def note_raw_states(self, energy_raw: dict[str, list[tuple[datetime, str]]]) -> None:
        if self.enabled:
            self.raw_states = sum(len(raw) for raw in energy_raw.values())

    def probe_parse(self, energy_raw: dict[str, list[tuple[datetime, str]]]) -> None:
        """One extra parse pass over all held raw states (see the class docstring)."""
        if not self.parse_probe:
            return
        previous = self._phase
        self.enter("probe")
        entries = chain.from_iterable(energy_raw.values())
        began = _clock()
        deque(map(_parse_energy_state, map(itemgetter(1), entries)), maxlen=0)
        parsed = _clock()
        self.parse_values = parsed - began
        entries = chain.from_iterable(energy_raw.values())
        try:
            deque(map(datetime.timestamp, map(itemgetter(0), entries)), maxlen=0)
        except (OverflowError, OSError, ValueError):
            # A diagnostic must never break the recalculation it measures.
            _LOGGER.debug("Effy: timestamp pass of the parse probe skipped", exc_info=True)
        else:
            self.parse_timestamps = _clock() - parsed
        self.enter(previous)

    def total(self) -> float:
        end = self._ended if self._ended is not None else _clock()
        return end - self._started

    def other(self) -> float:
        return max(0.0, self.total() - sum(self.seconds.values()))

    def finish(self, rows: int) -> None:
        """Close the record and log it (one line, meant to be pasted into a report)."""
        if not self.enabled:
            return
        self.enter(None)
        self._ended = _clock()
        self.longest_block = max(self.longest_block, self._ended - self._resumed)
        self.rows = rows
        if not _LOGGER.isEnabledFor(self.level):
            return
        sec = self.seconds
        parts = [
            f"Effy recalculation timing ({self.kind}): total {self.total():.2f} s"
            f" = fetch {sec['fetch']:.2f} s + compute {sec['compute']:.2f} s"
            f" + write {sec['write']:.2f} s"
        ]
        if self.parse_probe:
            parts.append(f" + parse probe {sec['probe']:.2f} s")
        parts.append(
            f" + other {self.other():.2f} s; recorder queries {self.queries},"
            f" raw states {self.raw_states}, slots {self.slots}, rows written {self.rows}"
        )
        if self.unchanged_rows is not None:
            parts.append(f", rows unchanged {self.unchanged_rows}")
        if self.hourly_left is not None:
            parts.append(f", hourly rows left for the slot job {self.hourly_left}")
        if self.cache_reads is not None:
            hits, misses = self.cache_reads
            parts.append(f", reads from the cache {hits}, from the recorder {misses}")
        if self.export_note is not None:
            parts.append(self.export_note)
        if self.parse_probe:
            parts.append(
                "; parse probe (one extra pass over all raw states, not part of compute):"
                f" values {self.parse_values:.2f} s, timestamps {self.parse_timestamps:.2f} s"
            )
        if self.detail:
            parts.extend(f"\n  {line}" for line in self._detail_lines())
        _LOGGER.log(self.level, "%s", "".join(parts))

    def _detail_lines(self) -> list[str]:
        """The detail block: headers flush left, their items indented two spaces."""
        sec, cpu = self.seconds, self.cpu
        lines = [
            f"{self.environment}, energy sources {self.energy_sources},"
            f" power sources {self.power_sources}",
            "cpu / wall (cpu is process-wide, it includes anything else Home Assistant did"
            " meanwhile): "
            + ", ".join(
                f"{p} {cpu[p]:.2f} / {sec[p]:.2f} s" for p in ("fetch", "compute", "write")
            ),
            f"longest stretch without awaiting (event loop held): {self.longest_block:.2f} s",
            "fetch jobs (wall = run in the executor + wait for the executor):",
        ]
        for (label, source), job in self.jobs.items():
            lines.append(
                f"  {f'{label} {source}'.strip()}: calls {job.calls}, wall {job.wall:.2f} s"
                f" = run {job.run:.2f} s + wait {max(0.0, job.wall - job.run):.2f} s,"
                f" rows {job.items}"
            )
        if self.stored_checks:
            lines.append(
                "stored rows check (mean, min, max and last_reset are compared; state is not"
                " returned by Home Assistant for these series):"
            )
            lines.extend(f"  {sid}: {text}" for sid, text in self.stored_checks.items())
        lines.append("compute stages (carved out of compute):")
        unit = {"trapezoid": "states", "cache pass": "states", "interpolation": "slots"}
        for (name, source), stage in self.stages.items():
            if name == "slot loop":
                continue
            out = f" -> {stage.out} slots" if stage.out else ""
            lines.append(
                f"  {f'{name} {source}'.strip()}: {stage.wall:.2f} s,"
                f" {stage.items} {unit.get(name, 'items')}{out}"
            )
        loop = self.stages.get(("slot loop", ""))
        if loop is not None:

            def part(name: str) -> float:
                return self.parts[name].wall if name in self.parts else 0.0

            rest = max(
                0.0, loop.wall - sum(part(n) for n in ("readings", "waterfall", "conversion"))
            )
            lines.append(
                f"  slot loop: {loop.wall:.2f} s for {loop.items} slots"
                f" = readings {part('readings'):.2f} s + waterfall {part('waterfall'):.2f} s"
                f" + conversion {part('conversion'):.2f} s + rest {rest:.2f} s"
            )
        lines.append(
            "write calls (build = preparing and scheduling the imports on the event loop,"
            " recorder = awaiting the recorder):"
        )
        for name, write in self.writes.items():
            lines.append(
                f"  {name}: {write.short} short-term + {write.hourly} hourly rows,"
                f" build {write.build:.2f} s, recorder {write.wait:.2f} s"
            )
        return lines


_NO_TIMINGS = _PhaseTimings("", logging.NOTSET, enabled=False)

# The stopwatch the real ``_write_recorder_statistics`` reports its build and wait
# times to. Set only by ``_PhaseTimings.write_call``, so the writer needs no new
# parameter (the tests' fake writer keeps the old signature).
_active_timings: ContextVar[_PhaseTimings] = ContextVar("effy_active_timings", default=_NO_TIMINGS)

# The debug export's record of the run in progress (TASK-0037): the writer tells it what it
# schedules, so the writer's signature stays as it is. ``None`` outside an exported run.
_active_export: ContextVar[ExportRecord | None] = ContextVar("effy_active_export", default=None)

# What the coordinator hands to a history run that is to be exported: it receives the
# record and either writes the file and says where, or returns ``None`` when it writes the
# file later (the coordinator does, after the rebuilds).
ExportHook = Callable[[ExportRecord], Awaitable["ExportOutcome | None"]]

# Fallback only used if the recorder instance somehow has no usable
# keep_days value (should not normally happen) – matches HA's own default.
_FALLBACK_SHORT_TERM_RETENTION_DAYS = 10

# Small, fixed extra lookback before any requested [start, end) range, so a
# transition whose *end* falls right at the start of the range still has
# its *start* visible to trapezoidal_slot_contributions — without this,
# the first few slots in any range could silently under-count. Tied to
# TRAPEZOID_MAX_MINUTES (ADR-014 raised that to 120 minutes) since that's
# exactly how far back a normal, non-offline transition's own window can
# now legitimately reach — this margin is what lets that transition's
# *start* stay visible even when it falls before whatever range a caller
# below is recomputing. This is NOT what makes offline-gap detection work
# across an arbitrarily long outage — that's _fetch_last_valid_state_before's
# job (ADR-013/014) — offline gaps are handled separately, regardless of
# this margin's size. A single bounded per-entity range read (not the
# unbounded, descending search _fetch_last_valid_state_before does) --
# ~2 hours of raw history for one entity is a cheap, indexed query, not
# the kind of cost that caused ADR-014's bootstrap-timeout incident. Not
# verified against a real HA instance — see the module WARNING docstring's
# general caveat on this file's internal-API-adjacent code.
_RAW_HISTORY_BOUNDARY_MARGIN = timedelta(minutes=TRAPEZOID_MAX_MINUTES + 5)

# How far back the slot-timer-driven "recent" recalculation (ADR-011,
# ADR-012) looks for candidate slots to rewrite, every time it runs
# (~every 5 minutes). Deliberately small and, since ADR-014, deliberately
# NOT tied to TRAPEZOID_MAX_MINUTES (unlike _RAW_HISTORY_BOUNDARY_MARGIN
# above) — this constant controls how many slots get rewritten (a write-
# volume cost) every single cycle, whereas the boundary margin only
# controls how much *extra raw history is read* to correctly anchor that
# smaller write. Widening this to match the 120-minute cap would mean
# rewriting up to 2 hours of statistics every ~5 minutes for every sensor
# — reintroducing the exact cost (and the "recalculated_from always shows
# the window size" symptom) ADR-013 shrank this away from in the first
# place, just at a different size. This window is only the *base* range of an
# idle cycle, though: since the ADR-014 Amendment (2026-10-05) a jump that
# arrives inside this window extends the rewrite range back to where that
# jump's own distribution window starts (RECENT_REWRITE_MAX_LOOKBACK
# below), so a jump that took longer than this window to arrive is no
# longer left half-written (stale 0s from the "no new reading yet"
# synthetic continuation) until the next full history recalc — idle cycles
# stay this cheap, and only a cycle that actually saw a jump pays for the
# wider rewrite. See ADR-011/012/013 for why this window exists at all,
# and ADR-014 (+ Amendment) for why it doesn't grow with the cap. A
# genuinely long-neglected sensor (e.g. HA itself was down for days) is
# still corrected eventually by the next full history recalc, whose own
# window is much larger (max_history_days) — same fallback as before.
RECENT_RECALC_WINDOW = timedelta(minutes=20)

# Hard bound on how far back a jump-triggered extension of the slot-timer
# rewrite range may reach (ADR-014 Amendment 2026-10-05, item 4). Normal
# jumps reach at most TRAPEZOID_MAX_MINUTES (+ RECENT_RECALC_WINDOW) back;
# only an offline-recovery jump (uncapped window, starts at the last
# pre-outage reading) can reach further, and one day is enough to cover an
# overnight outage or an HA restart without letting a single cycle rewrite
# days of statistics. An older gap is left to the next full history recalc,
# as before.
RECENT_REWRITE_MAX_LOOKBACK = timedelta(days=1)


def recent_trigger_since(previous_run: datetime | None, now: datetime) -> datetime:
    """The ``trigger_since`` argument for ``async_recalculate_recent``
    (ADR-014 Amendment 2026-10-06): how far back a counter jump / offline
    recovery may have arrived without any cycle having applied it yet.

    ``previous_run`` is the ``now`` of the last cycle that *completed*
    (kept in memory by EffyCoordinator). Every trigger up to that moment was
    seen by a cycle, so only later ones are still outstanding — in normal
    5-minute operation this is inside RECENT_RECALC_WINDOW and changes
    nothing; after a pause (HA restart, suspended host, failed cycles) it
    reaches back across the pause. On the first cycle of a session there is
    no previous run, and nothing is known about what happened while HA was
    down, so a one-off catch-up window of RECENT_REWRITE_MAX_LOOKBACK is
    assumed.
    """
    if previous_run is None:
        return now - RECENT_REWRITE_MAX_LOOKBACK
    return previous_run


# Staged lookback distances for _fetch_last_valid_state_before, tried in
# order, cheapest/narrowest first (ADR-014) — only escalating to the next
# stage when the previous one turned up no valid reading at all. Replaces
# a single jump straight to the full configured history range, which
# caused a Home Assistant bootstrap timeout: right after a restart, many
# energy-family sensors can plausibly all have an invalid first entry in
# their (small) RECENT_RECALC_WINDOW fetch at once (their owning
# integrations haven't reconnected yet), each independently triggering
# this lookback — an unbounded, descending, days-long search for every one
# of them at once is exactly the kind of cost that produced the timeout.
# Round 1 (30 minutes) resolves a brief blip. Round 2 (1 day) resolves the
# next most common case (overnight outage, HA restart) far more cheaply
# than a full-history query. The full configured max_history_days is
# tried last, only if both of the above come up completely empty — the
# rare case of a sensor offline for more than a day.
_OFFLINE_ANCHOR_LOOKBACK_STAGES: tuple[timedelta, ...] = (
    timedelta(minutes=30),
    timedelta(days=1),
)

# Row cap per _fetch_last_valid_state_before query — bounds worst-case
# scan/memory cost even within a single stage's own time range (e.g. a
# very chatty sensor with hundreds of invalid entries before a valid one).
_OFFLINE_ANCHOR_QUERY_LIMIT = 200


def _get_short_term_retention_days(hass: HomeAssistant) -> int:
    """Return the recorder's actually configured short-term retention.

    Home Assistant's recorder purges `statistics_short_term` globally
    based on a single `purge_keep_days` setting (`recorder.keep_days` on
    the running instance) — this is NOT configurable per entity/sensor,
    only for the recorder as a whole. The default is 10 days, but users
    commonly change it (e.g. via `configuration.yaml: recorder:
    purge_keep_days: N`), so the actual configured value is read at
    runtime instead of hardcoding HA's default.
    """
    instance = get_recorder(hass)
    keep_days = getattr(instance, "keep_days", None)
    if isinstance(keep_days, int) and keep_days > 0:
        return keep_days
    _LOGGER.warning(
        "Effy: could not read recorder.keep_days, falling back to %d days",
        _FALLBACK_SHORT_TERM_RETENTION_DAYS,
    )
    return _FALLBACK_SHORT_TERM_RETENTION_DAYS


def _build_statistic_metadata(
    statistic_id: str, unit: str, *, has_sum: bool = False
) -> StatisticMetaData:
    """Build StatisticMetaData compatible with both old and new HA core APIs.

    HA core ≈2025.10+ requires (or strongly deprecates not specifying)
    `unit_class` and `mean_type` on the metadata passed to
    `async_import_statistics`/`async_add_external_statistics`, replacing
    the older `has_mean`/`has_sum` boolean flags. Older cores (this
    integration was originally developed/tested against 2025.1.4) only
    understand `has_mean`/`has_sum` and have no `unit_class` concept at
    all. Sending the new fields to an old core that doesn't expect them
    has not been observed to cause problems (extra TypedDict keys are
    ignored), so we always include both the old flags (for older cores)
    and, when available, the new fields (required by newer cores) —
    this matches the dict actually seen in a real recorder error log,
    where the core had already derived `mean_type` from `has_mean` but
    still required `unit_class` to be present explicitly.

    `unit_class` is set to ``None``: Effy intentionally never asks the
    recorder to perform unit conversion (ADR-002 normalizes units inside
    `distribute_loss` itself, before writing), so there is no compatible
    unit converter class to point to here.
    """
    metadata: StatisticMetaData = {
        "has_mean": not has_sum,
        "has_sum": has_sum,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }
    if _HAS_STATISTIC_MEAN_TYPE:
        # A running total has no mean. Its unit class is ``energy`` (the PV delta integrals,
        # Wh or kWh), the one the recorder gives the same series when it compiles the live
        # sensor later, so the two writers agree on the metadata (ADR-027 decision 5).
        metadata["mean_type"] = StatisticMeanType.NONE if has_sum else StatisticMeanType.ARITHMETIC
        metadata["unit_class"] = "energy" if has_sum else None
    return metadata


def _get_state_class(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    sc: str | None = state.attributes.get("state_class")
    return sc


def _get_unit(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    if state is None:
        return "W"
    unit: str = state.attributes.get("unit_of_measurement", "W")
    return unit


def _get_statistics_units(hass: HomeAssistant, entity_ids: list[str]) -> dict[str, str]:
    """Return the unit HA's compiled statistics actually store for each entity.

    ``statistics_during_period`` (called with ``units=None`` in
    ``_fetch_statistics``) returns ``change``/``mean`` values in whatever
    unit is recorded in ``statistics_meta`` for that statistic_id — which is
    **not guaranteed to equal** the live entity's current
    ``unit_of_measurement`` (``_get_unit``). Home Assistant normalizes
    statistics units independently of the live entity for certain device
    classes precisely so historical data stays consistent even if a
    sensor's reported unit changes later (see HA developer docs: "For
    certain device classes, the unit of the statistics is normalized").
    This is a separate, sync-only lookup, so callers must run it via
    ``recorder.async_add_executor_job``, like ``_fetch_statistics``.

    Falls back to an empty dict (caller uses the live unit instead) if the
    metadata API is unavailable or its shape has changed — this is
    read-only defensive fallback, not a hard requirement, matching the
    version-compat approach already used for ``StatisticMeanType``
    (ADR-003).
    """
    from homeassistant.components.recorder.statistics import get_metadata  # noqa: PLC0415

    try:
        metadata = get_metadata(hass, statistic_ids=set(entity_ids))
    except Exception:
        _LOGGER.exception(
            "Effy: could not read statistics metadata; falling back to live "
            "sensor units for change/mean interpretation"
        )
        return {}

    units: dict[str, str] = {}
    for eid, meta_entry in metadata.items():
        try:
            _metadata_id, meta = meta_entry
            unit = meta.get("unit_of_measurement")
        except (TypeError, ValueError, AttributeError):
            continue
        if unit:
            units[eid] = unit
    return units


def _stat_field_for(state_class: str | None, unit: str) -> str:
    """Return the statistics field to read for a given state class (ADR-003).

    Delegates the energy-vs-power classification to
    ``sensor_utils.is_energy_family`` (ADR-012) so this can't silently
    diverge from sensor.py's derived-power entity setup, which needs the
    same classification.
    """
    return "change" if is_energy_family(state_class, unit) else "mean"


def _readings_for_slot(
    slot: datetime,
    slot_duration_minutes: int,
    entity_ids: list[str],
    indexed: dict[str, dict[datetime, StatRow]],
    state_classes: dict[str, str | None],
    units: dict[str, str],
) -> list[SensorReading]:
    """Build SensorReadings for one time slot from pre-indexed statistics.

    TOTAL_INCREASING / TOTAL-as-energy sensors supply a ``change`` value in
    Wh/kWh — the energy accumulated during the slot.  MEASUREMENT / TOTAL-as-
    power sensors supply ``mean`` in W/kW (already average instantaneous power).

    To make all sensors comparable inside ``distribute_loss``, Wh/kWh deltas
    are converted to W-equivalent average power by dividing by the slot
    duration in hours:

        W_equiv = Wh_delta / (slot_minutes / 60)

    The slot duration is passed explicitly so this function does not hard-code
    the 5-minute assumption — it is consistent with the live path which uses
    the actual elapsed time between reset_ts and updated_ts.

    ``original_unit`` is updated from Wh → W (kWh → kW) so that
    ``effective_in_original_unit`` converts the W result back to W, which is
    the natural output unit for an interval-average sensor value (ADR-008).
    """
    readings: list[SensorReading] = []
    for eid in entity_ids:
        slot_row: StatRow | None = indexed.get(eid, {}).get(slot)
        if slot_row is None:
            continue
        unit = units[eid]
        field = _stat_field_for(state_classes.get(eid), unit)
        raw_val: float | None = slot_row.get(field)
        if raw_val is None:
            continue
        value, effective_unit = to_power_equivalent(raw_val, unit, slot_duration_minutes)
        readings.append(SensorReading(entity_id=eid, raw_value=value, original_unit=effective_unit))
    return readings


def _fetch_raw_energy_states(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, str]]:
    """Fetch raw state history for one TOTAL_INCREASING/energy-family entity.

    Uses ``state_changes_during_period`` — a public, stable recorder API
    (unlike the internal-table statistics writes documented in the module
    WARNING above); it's the same function HA's own History panel is built
    on. Runs synchronously; callers must invoke this via
    ``recorder.async_add_executor_job``, exactly like ``_fetch_statistics``.

    ``significant_changes_only=False``: every single state write matters
    for the trapezoidal algorithm (ADR-012) — a filtered/deduplicated
    series would hide exactly the small, frequent ticks it needs.
    ``no_attributes=True``: pure performance win here, only ``.state`` and
    ``.last_changed`` are used below.

    Returns (timestamp, raw_state_string) pairs in chronological order,
    including "unavailable"/"unknown" entries —
    ``trapezoidal_slot_contributions`` depends on seeing those to detect
    offline gaps (see its docstring in calculation.py).
    """
    from homeassistant.components.recorder.history import (  # noqa: PLC0415
        state_changes_during_period,
    )

    history_by_entity = state_changes_during_period(
        hass,
        start,
        end,
        entity_id,
        no_attributes=True,
    )
    states = history_by_entity.get(entity_id, [])
    return [(s.last_changed, s.state) for s in states]


def _fetch_last_valid_state_before(
    hass: HomeAssistant,
    entity_id: str,
    before: datetime,
    max_history_days: int,
) -> tuple[datetime, str] | None:
    """Find the most recent *numeric* raw state for entity_id strictly
    before ``before``, escalating through progressively wider (and more
    expensive) lookback stages (ADR-013, staged in ADR-014).

    Only needed when the regular per-cycle raw-history fetch
    (_fetch_raw_energy_states, using include_start_time_state=True) both
    (a) starts mid-outage — its very first entry is already invalid
    (unavailable/unknown/non-numeric) — *and* (b) that same fetch also
    contains an actual recovery: at least one valid reading somewhere in
    it, meaning the sensor came back online within this specific window.
    A merely slow-ticking *online* sensor never reaches this function at
    all: include_start_time_state=True already reliably returns that
    sensor's true last known value as the first entry, however long ago
    it was, with no need for any of this. And a sensor that stays
    *entirely* invalid for the whole window (condition (a) without (b) —
    e.g. an empty battery all night, if its integration reports the
    discharge sensor as unavailable rather than 0) also doesn't reach this
    function: without a recovery, an anchor found here wouldn't be used
    for anything anyway (trapezoidal_slot_contributions only forms a
    transition once a valid reading actually follows the invalid stretch),
    so the caller skips searching until a real reading arrives — meaning
    this runs at most once per outage, right when it ends, not repeatedly
    on every cycle for as long as it lasts.

    Tries _OFFLINE_ANCHOR_LOOKBACK_STAGES in order (30 minutes, then 1
    day), stopping as soon as a stage's query turns up a valid numeric
    reading. Only if every staged attempt comes up completely empty does
    this fall back to searching the entire configured max_history_days —
    the rare, most expensive case (a sensor offline for more than a day),
    not the common one. This staging is what keeps the cost of this
    lookback bounded even when it fires for many sensors at once — e.g.
    right after a Home Assistant restart, before various source
    integrations have reconnected, when it's entirely plausible for many
    energy-family sensors to all have an invalid first entry
    simultaneously. Before this staging existed, every one of them
    independently ran an unbounded, descending, days-long query at once —
    this is what produced a real Home Assistant bootstrap timeout
    (`Setup timed out for bootstrap waiting on {<Task ...
    EffyCoordinator._async_recalculate_recent_and_report()> ...}`),
    which is the incident this staging fixes.

    Runs synchronously; callers must invoke this via
    ``recorder.async_add_executor_job``, exactly like ``_fetch_raw_energy_states``.

    Returns (timestamp, raw_state_string) of the found reading, or None if
    no numeric reading exists at all within max_history_days (a genuinely
    brand-new entity, or one that's been offline longer than that) — in
    either case, the transition into the window is simply left without a
    computed contribution, same as any other "fewer than 2 valid readings"
    case.
    """
    from homeassistant.components.recorder.history import (  # noqa: PLC0415
        state_changes_during_period,
    )

    stages = (*_OFFLINE_ANCHOR_LOOKBACK_STAGES, timedelta(days=max_history_days))
    already_searched: timedelta | None = None
    for stage in stages:
        if already_searched is not None and stage <= already_searched:
            # max_history_days is shorter than a fixed stage already tried
            # (an unusually small configured retention) — nothing wider to
            # gain from repeating the same search.
            continue
        search_start = before - stage
        history_by_entity = state_changes_during_period(
            hass,
            search_start,
            before,
            entity_id,
            no_attributes=True,
            descending=True,
            include_start_time_state=False,
            limit=_OFFLINE_ANCHOR_QUERY_LIMIT,
        )
        for state in history_by_entity.get(entity_id, []):
            if _parse_energy_state(state.state) is not None:
                return (state.last_changed, state.state)
        already_searched = stage
    return None


def _effy_entity_id(source_entity_id: str) -> str:
    """Return the effy_* entity_id for a given input sensor (mirrors sensor.py)."""
    slug = slugify(source_entity_id.split(".")[-1])
    return f"sensor.effy_{slug}"


def _effy_power_entity_id(source_entity_id: str) -> str:
    """Return the effy_*_power entity_id for an energy-family sensor (ADR-012).

    Mirrors _effy_entity_id / sensor.py's slug derivation, with a "_power"
    suffix — this is the raw, pre-loss-distribution trapezoidal power
    derived from a TOTAL_INCREASING/energy source, distinct from the
    "effective" (post-waterfall) sensor _effy_entity_id names.
    """
    slug = slugify(source_entity_id.split(".")[-1])
    return f"sensor.effy_{slug}_power"


def _effy_smoothed_entity_id(source_entity_id: str) -> str:
    """Return the effy_*_smoothed entity_id for a power-family INPUT sensor.

    Mirrors _effy_entity_id / _effy_power_entity_id's slug derivation, with
    a "_smoothed" suffix — the gap-interpolated (interpolate_slot_gaps)
    series for a MEASUREMENT/TOTAL-as-power *input* sensor.
    """
    slug = slugify(source_entity_id.split(".")[-1])
    return f"sensor.effy_{slug}_smoothed"


# This module, for ``RecorderStorage``: it looks the read functions up here at call time,
# so the tests that replace them by name (``monkeypatch.setattr(history, ...)``) still work.
_SELF = sys.modules[__name__]


def create_storage(
    hass: HomeAssistant, *, clock: Callable[[], datetime], warn: Callable[[str], None] | None = None
) -> Cache:
    """The storage the coordinator hands to every run: a cache over the recorder (ADR-025)."""
    return Cache(RecorderStorage(hass, _SELF), clock=clock, settle=RECORDER_SETTLE, warn=warn)


def _cache_counters(storage: Storage) -> tuple[int, int] | None:
    """``(hits, misses)`` of a storage that counts them (the cache), else ``None``."""
    counters = getattr(storage, "counters", None)
    if counters is None:
        return None
    reads = counters()
    return int(reads["hits"]), int(reads["misses"])


def _note_cache_reads(
    phases: _PhaseTimings, storage: Storage, before: tuple[int, int] | None
) -> None:
    after = _cache_counters(storage)
    if before is not None and after is not None:
        phases.note_cache(after[0] - before[0], after[1] - before[1])


def _storage_or_default(
    hass: HomeAssistant, storage: Storage | None, phases: _PhaseTimings
) -> Storage:
    """The storage a run reads through: the one it was given, else the recorder's own.

    A caller that gives none (the tests of single functions) gets the behaviour the
    functions had before the storage interface: every read goes to the recorder.
    """
    return storage if storage is not None else RecorderStorage(hass, _SELF, timings=phases)


async def _write_and_forget(
    hass: HomeAssistant,
    storage: Storage,
    series: dict[str, dict[str, Any]],
    **options: Any,
) -> int:
    """Write the series, then tell the storage what is stale (ADR-025 decision 11).

    The standby and efficiency readers read back what Effy wrote, so whatever a cache
    holds for these statistics is out of date the moment the writer returns.
    """
    written = await _write_recorder_statistics(hass, series, **options)
    slots = [ts for info in series.values() for ts, _value in info["slot_values"]]
    if slots:
        storage.written(list(series), min(slots), max(slots) + timedelta(minutes=SLOT_MINUTES))
    return written


async def _fetch_energy_raw_with_anchor(
    hass: HomeAssistant,
    recorder: Any,
    eid: str,
    raw_history_start: datetime,
    end: datetime,
    max_history_days: int,
    energy_reading_cache: dict[str, tuple[datetime, str]] | None,
    phases: _PhaseTimings = _NO_TIMINGS,
    storage: Storage | None = None,
) -> list[tuple[datetime, str]]:
    """Raw state history for one energy-family sensor over
    [raw_history_start, end], with the offline-anchor lookback applied and
    the ADR-015 last-valid-reading cache kept warm.

    Extracted from _compute_effective_slots so the slot-timer path's
    first (base-window) fetch and its jump-extended second fetch share
    exactly one implementation of the anchor logic (ADR-014 Amendment
    2026-10-05) — an offline-recovery jump's pre-outage reading must be
    visible in *both*.
    """
    storage = _storage_or_default(hass, storage, phases)
    raw_states: list[tuple[datetime, str]] = await storage.raw_states(eid, raw_history_start, end)
    # RECENT_RECALC_WINDOW is deliberately small (ADR-013) — if the
    # sensor was already offline when this window's own fetch starts,
    # the window contains no valid baseline to redistribute the
    # eventual return-to-online jump against. Detected cheaply: the
    # chronologically-first fetched entry is itself invalid.
    #
    # Only worth searching for an anchor if this window also contains
    # an actual *recovery* — at least one valid reading somewhere in
    # it. If the sensor is invalid for the *entire* window (still
    # offline throughout, e.g. a battery empty all night with an
    # integration that reports its discharge sensor as
    # unavailable/unknown rather than 0), an anchor wouldn't be used
    # for anything anyway: trapezoidal_slot_contributions only forms a
    # transition once a valid reading follows the invalid stretch, and
    # only adds the synthetic now-continuation when the sensor is
    # *currently* valid (ADR-013) — neither applies here, so finding
    # the anchor would be wasted work, repeated on every single cycle
    # for as long as the outage lasts (amends ADR-014 Decision 3).
    # Skipping it here means: no search at all while nothing has
    # changed, and exactly one search on the cycle a real reading
    # finally arrives.
    first_is_invalid = raw_states and _parse_energy_state(raw_states[0][1]) is None
    has_recovery_in_window = any(_parse_energy_state(state) is not None for _, state in raw_states)
    if first_is_invalid and has_recovery_in_window:
        # Check the volatile last-known-valid-reading cache (ADR-015)
        # before touching the recorder at all — if some earlier cycle
        # this session already saw this entity in a valid state, that
        # cached (timestamp, state) *is* the answer
        # _fetch_last_valid_state_before would otherwise have to query
        # the recorder for, at zero cost. Only genuinely falls back to
        # the recorder for a sensor whose outage predates this
        # coordinator's own runtime (e.g. right after a Home Assistant
        # restart, before any cycle has had a chance to observe it).
        cached = (energy_reading_cache or {}).get(eid)
        anchor = (
            cached
            if cached is not None and cached[0] < raw_history_start
            else await storage.last_valid_state_before(eid, raw_history_start, max_history_days)
        )
        if anchor is not None:
            raw_states = [anchor, *raw_states]

    # Keep the cache warm regardless of whether it was used above:
    # remember the most recent *valid* reading seen this cycle (which
    # may be the just-fetched anchor itself, if raw_states was
    # entirely invalid otherwise), so a *future* outage for this same
    # entity can skip the recorder query entirely too.
    if energy_reading_cache is not None:
        latest_valid: tuple[datetime, str] | None = None
        with phases.stage("cache pass", eid, items=len(raw_states)):
            for ts, state in raw_states:
                if _parse_energy_state(state) is not None:
                    latest_valid = (ts, state)
        if latest_valid is not None:
            cached = energy_reading_cache.get(eid)
            if cached is None or latest_valid[0] > cached[0]:
                energy_reading_cache[eid] = latest_valid
    return raw_states


def _additional_series(
    extra_ids: list[str],
    energy_ids: list[str],
    indexed: dict[str, dict[datetime, StatRow]],
    state_classes: dict[str, str | None],
    units: dict[str, str],
    smoothed_series: dict[str, dict[datetime, float]],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """The series of the additional sensors (ADR-028): ``(derived power, smoothed)``.

    An energy-family sensor gets the derived power series an energy-family input gets: the
    trapezoidal redistribution already in ``indexed`` (with the explicit zeros of an idle
    counter, ADR-013) turned into a power the way ``_readings_for_slot`` does for the waterfall.
    A power-family sensor gets the gap-interpolated series in ``smoothed_series``. Neither is
    handed to the waterfall: the caller keeps these sensors out of its slot loop, so no
    effective, loss or share value depends on them.
    """
    energy = set(energy_ids)
    power_results: dict[str, list[tuple[datetime, float]]] = {
        eid: [] for eid in extra_ids if eid in energy
    }
    for slot in sorted({slot for eid in power_results for slot in indexed.get(eid, {})}):
        for reading in _readings_for_slot(
            slot, SLOT_MINUTES, list(power_results), indexed, state_classes, units
        ):
            power_results[reading.entity_id].append((slot, reading.raw_value))
    per_sensor_power: dict[str, dict[str, Any]] = {
        _effy_power_entity_id(eid): {
            "unit": effective_unit_for(units[eid]),
            "slot_values": slot_values,
        }
        for eid, slot_values in power_results.items()
        if slot_values
    }
    per_sensor_smoothed: dict[str, dict[str, Any]] = {}
    for eid in extra_ids:
        slot_values = sorted(
            (ts, val) for ts, val in smoothed_series.get(eid, {}).items() if start <= ts < end
        )
        if slot_values:
            per_sensor_smoothed[_effy_smoothed_entity_id(eid)] = {
                "unit": units[eid],
                "slot_values": slot_values,
            }
    return per_sensor_power, per_sensor_smoothed


async def _compute_effective_slots(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    start: datetime,
    end: datetime,
    energy_reading_cache: dict[str, tuple[datetime, str]] | None = None,
    extend_for_jumps: bool = False,
    trigger_since: datetime | None = None,
    phases: _PhaseTimings = _NO_TIMINGS,
    storage: Storage | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Core computation shared by async_recalculate_history and async_recalculate_recent.

    ``extend_for_jumps`` is the slot-timer path's switch (ADR-014 Amendment
    2026-10-05): ``[start, end)`` is then only the *base* window
    (``end - start`` == RECENT_RECALC_WINDOW), and if any energy-family
    sensor saw a real counter jump inside it, the range is moved back —
    for *all* sensors and all three series at once, never per sensor,
    because distribute_loss needs every sensor's reading for a slot — to
    where that jump's own distribution window starts
    (``calculation.recent_rewrite_start``, bounded by
    RECENT_REWRITE_MAX_LOOKBACK). Without this a jump spread over more than
    RECENT_RECALC_WINDOW would be only partly written, leaving stale 0s
    behind and losing energy. Raw history is then fetched a second time
    from the new, earlier start — only on such cycles; an idle cycle makes
    exactly the one fetch per sensor it always did. Full history recalc
    leaves this False and is unchanged. ``trigger_since`` (only meaningful
    with ``extend_for_jumps``; ADR-014 Amendment 2026-10-06) widens the
    window in which a jump counts as a trigger back to the last completed
    cycle (``calculation.recent_trigger_from``); the *first* raw fetch then
    has to reach back that far too, or a jump older than the base window
    would not even be visible. Without it (the default) nothing changes.

    Resolves units (preferring statistics metadata over the live entity's
    current unit — see _get_statistics_units), builds each entity's
    per-slot series for [start, end), and runs distribute_loss per slot
    exactly as the live path does per event (ADR-008).

    Energy-family (TOTAL_INCREASING / TOTAL-as-energy) sensors' per-slot
    values come from trapezoidal_slot_contributions (ADR-012) applied to
    raw state history — not statistics_during_period's ``change`` field
    (that field is still fetched, unchanged, for MEASUREMENT/TOTAL-as-power
    sensors' ``mean``). Raw history is fetched starting
    _RAW_HISTORY_BOUNDARY_MARGIN before ``start`` so a jump whose
    distribution window begins before ``start`` is still correctly
    accounted for in the first slots of the requested range; any resulting
    contribution for a slot before ``start`` is then discarded (it belongs
    to a range some *other* call is responsible for). ``now=end`` is
    passed through to trapezoidal_slot_contributions so a sensor that
    simply hasn't reported a new value yet still gets explicit 0 entries
    for the slots since its last reading (ADR-013), and if the fetched raw
    history itself starts mid-outage (its first entry is already invalid),
    a staged lookback (_fetch_last_valid_state_before, 30 min → 1 day →
    the full configured max_history_days, ADR-014) finds the real
    pre-outage baseline regardless of how far back it is, without every
    such sensor paying the cost of an unbounded search.

    Power-family (MEASUREMENT / TOTAL-as-power) *input* sensors' compiled
    ``mean`` occasionally has short gaps (a slot the recorder never
    compiled a reading for). Gaps of up to interpolate_slot_gaps's
    ``INTERPOLATION_MAX_GAP_SLOTS`` are bridged via linear interpolation
    and merged straight into this same per-slot series *before*
    distribute_loss runs — the waterfall sees the smoothed values, not the
    gappy raw ones, so a short blip in one input no longer misattributes
    loss for that slot. Output sensors and energy-family sensors are
    untouched (out of scope for this feature; energy-family sensors don't
    have this problem in the same way — see trapezoidal_slot_contributions
    instead).

    Returns (per_sensor, per_sensor_power, per_sensor_smoothed):
      - per_sensor: effy_entity_id → {"unit", "slot_values"} — the
        post-waterfall "effective" value, input sensors only (unchanged
        concept from before ADR-012). Already reflects the interpolated
        input series described above, since that's merged in before this
        is computed.
      - per_sensor_power: effy_*_power entity_id → {"unit", "slot_values"}
        — the raw, pre-waterfall trapezoidal power for every energy-family
        sensor, inputs *and* outputs (new in ADR-012) — this is exactly
        the value already computed for distribute_loss's input, so no
        extra computation is needed, just capturing it before the
        waterfall step consumes it.
      - per_sensor_smoothed: effy_*_smoothed entity_id →
        {"unit", "slot_values"} — the gap-interpolated series for every
        power-family *input* sensor that had at least one reading in
        range; a plain copy of what was merged into distribute_loss's own
        input above, exposed as its own statistic so the smoothing is
        visible, not just an invisible internal correction.
    All three are ready for _write_recorder_statistics. Nothing is written here.

    The PV combined series (ADR-027 decision 1, TASK-0045a) is one more member of
    ``per_sensor``, under the statistic ID ``const.PV_SUM_ENTITY_ID``: per slot the
    addition of the effective values of all PV inputs, in W whatever their units are.
    A PV input without a value in a slot adds nothing, a slot in which no PV input has
    a value has no row, and neither the EPS sensor nor a BMS input is in it. It is
    computed in the same slot loop as the effective series, so the slot job, the
    recent recalculation and the button write it the same way, and a full
    recalculation and the recent recalculation give identical rows.

    The additional sensors (ADR-028) and the household meter sensors (ADR-029), together
    ``series_only_ids``, are fetched, converted and smoothed with the inputs, and their derived
    power or smoothed series is added to the second and third result. A power-family one is
    smoothed as measured, a negative reading is not clamped (the signed household import of
    ADR-029 decision 2 relies on it). They are never in the slot union or the slot loop below,
    so the effective rows are the same with and without them; the rewrite range of the slot job
    stays global, so a jump of such a counter extends it for every series, like any other jump.

    ``phases`` (TASK-0010) books the time of this call to the fetch and compute
    phases; the default does nothing.
    """
    phases.enter("compute")
    input_ids: list[str] = all_input_ids(entry_options)
    output_ids: list[str] = configured_output_ids(entry_options)
    all_ids: list[str] = input_ids + output_ids
    # the additional sensors (ADR-028) and the household meter sensors (ADR-029): they only get a
    # series, they are in no formula
    extra_ids: list[str] = series_only_ids(entry_options)
    # every sensor Effy reads; the extra ones only get a series
    read_ids: list[str] = all_ids + extra_ids
    max_history_days: int = entry_options.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS)

    storage = _storage_or_default(hass, storage, phases)

    # Gather per-sensor metadata (event loop – states are in memory)
    live_units: dict[str, str] = {eid: storage.unit(eid) for eid in read_ids}
    state_classes: dict[str, str | None] = {eid: storage.state_class(eid) for eid in read_ids}

    recorder = get_recorder(hass)

    # The unit `change`/`mean` values from statistics_during_period are
    # actually expressed in can differ from the live entity's current unit
    # (see _get_statistics_units) — read it from statistics_meta instead of
    # assuming it matches the live state, to avoid interpreting e.g. an
    # already-Wh value as if it were kWh (or vice versa), which would throw
    # off every downstream Wh/kWh → W conversion by a further factor of 1000.
    stats_units: dict[str, str] = await storage.statistics_units(read_ids)
    units: dict[str, str] = {}
    for eid in read_ids:
        stat_unit = stats_units.get(eid)
        live_unit = live_units[eid]
        if stat_unit and stat_unit != live_unit:
            _LOGGER.warning(
                "Effy: %s's compiled statistics are stored in '%s' but the live "
                "entity currently reports '%s'; using '%s' (the statistics unit) "
                "to interpret change/mean values, since that is what those "
                "values are actually expressed in",
                eid,
                stat_unit,
                live_unit,
                stat_unit,
            )
        units[eid] = stat_unit or live_unit

    energy_ids = [
        eid for eid in read_ids if _stat_field_for(state_classes.get(eid), units[eid]) == "change"
    ]
    power_ids = [eid for eid in read_ids if eid not in energy_ids]
    phases.note_sources(len(energy_ids), len(power_ids))

    indexed: dict[str, dict[datetime, StatRow]] = {}

    # ---- Energy-family sensors: trapezoidal-redistributed raw history (ADR-012, ADR-013) ----
    # Raw history is fetched first (before the power-family statistics) so
    # that, on the slot-timer path, a jump-extended start (ADR-014
    # Amendment) is known before anything is fetched or filtered by `start`.
    raw_history_start = start - _RAW_HISTORY_BOUNDARY_MARGIN
    if extend_for_jumps and trigger_since is not None:
        # Catch-up (ADR-014 Amendment 2026-10-06): jumps since `trigger_since`
        # can predate the base window, so the first fetch must see them. A
        # no-op for the normal cadence (trigger_from == start there).
        trigger_from = recent_trigger_from(
            end, end - start, RECENT_REWRITE_MAX_LOOKBACK, trigger_since
        )
        raw_history_start = min(start, trigger_from) - _RAW_HISTORY_BOUNDARY_MARGIN
    energy_raw: dict[str, list[tuple[datetime, str]]] = {}
    for eid in energy_ids:
        energy_raw[eid] = await _fetch_energy_raw_with_anchor(
            hass,
            recorder,
            eid,
            raw_history_start,
            end,
            max_history_days,
            energy_reading_cache,
            phases,
            storage,
        )

    if extend_for_jumps and energy_ids:
        jump_windows = [w for raw in energy_raw.values() for w in trapezoidal_jump_windows(raw)]
        extended_start = recent_rewrite_start(
            jump_windows,
            end,
            end - start,
            RECENT_REWRITE_MAX_LOOKBACK,
            SLOT_MINUTES,
            trigger_since=trigger_since,
        )
        if extended_start < start:
            _LOGGER.debug(
                "Effy recent recalc: counter jump(s) in the last %s extend the rewrite range "
                "from %s back to %s",
                end - start,
                start,
                extended_start,
            )
            start = extended_start
            raw_history_start = start - _RAW_HISTORY_BOUNDARY_MARGIN
            for eid in energy_ids:
                energy_raw[eid] = await _fetch_energy_raw_with_anchor(
                    hass,
                    recorder,
                    eid,
                    raw_history_start,
                    end,
                    max_history_days,
                    energy_reading_cache,
                    phases,
                    storage,
                )

    phases.note_raw_states(energy_raw)
    for eid in energy_ids:
        with phases.stage("trapezoid", eid, items=len(energy_raw[eid])) as trapezoid:
            contributions = trapezoidal_slot_contributions(
                energy_raw[eid], slot_minutes=SLOT_MINUTES, now=end
            )
            trapezoid.out = len(contributions)
        indexed[eid] = {
            slot: {"start": slot, "change": value}
            for slot, value in contributions.items()
            if start <= slot < end
        }
    phases.probe_parse(energy_raw)

    # ---- Power-family sensors: unchanged, statistics `mean` per slot ----
    # Fetched from the (possibly jump-extended) `start`, so every sensor
    # covers the same range.
    if power_ids:
        raw_stats: dict[str, list[StatRow]] = await storage.statistics(power_ids, start, end)
        for eid, rows in raw_stats.items():
            indexed[eid] = {row["start"]: row for row in rows}

    # ---- Smoothed power-family INPUT sensors: gap interpolation ----
    # Bridges short (<= INTERPOLATION_MAX_GAP_SLOTS) runs of missing slots
    # in a power-family input sensor's `mean` and merges the interpolated
    # values straight into `indexed[eid]` — using setdefault so an actual
    # recorder row for a slot is never overwritten, only genuinely missing
    # slots get a synthetic one. This runs *before* `slots`/distribute_loss
    # below, so the waterfall calculation itself sees the smoothed series.
    # Output sensors are deliberately left untouched (out of scope). The additional sensors are
    # smoothed like inputs (ADR-028) but, never being in the slot union below, no waterfall slot
    # comes of their filled values. The one exception is the
    # EPS sensor (ADR-026 decision 3): it is a member of ``input_ids``, so its *signed* series
    # is interpolated here, before the slot loop splits it into an EPS input and an EPS output,
    # and both halves see the same filled value.
    smoothed_series: dict[str, dict[datetime, float]] = {}
    for eid in input_ids + extra_ids:
        if eid not in power_ids:
            continue
        entity_rows = indexed.setdefault(eid, {})
        raw_means = {
            ts: row["mean"] for ts, row in entity_rows.items() if row.get("mean") is not None
        }
        if not raw_means:
            continue
        with phases.stage("interpolation", eid, items=len(raw_means)) as interpolation:
            filled = interpolate_slot_gaps(raw_means, slot_minutes=SLOT_MINUTES)
            interpolation.out = len(filled)
        smoothed_series[eid] = filled
        for ts, value in filled.items():
            entity_rows.setdefault(ts, {"start": ts, "mean": value})

    # Union of all slot timestamps actually present, sorted. The additional sensors are left
    # out: the waterfall runs for the slots of the inputs and outputs only (ADR-028 decision 2).
    slot_set: set[datetime] = set()
    for eid, entity_rows in indexed.items():
        if eid not in extra_ids:
            slot_set.update(entity_rows.keys())
    slots: list[datetime] = sorted(slot_set)
    phases.slots = len(slots)

    extra_power, extra_smoothed = _additional_series(
        extra_ids, energy_ids, indexed, state_classes, units, smoothed_series, start, end
    )

    if not slots:
        phases.enter(None)
        return {}, extra_power, extra_smoothed

    # results: effective (post-waterfall) values per slot, input sensors only
    results: dict[str, list[tuple[datetime, float]]] = {eid: [] for eid in input_ids}
    # power_results: raw (pre-waterfall) derived power per slot, energy-family only
    power_results: dict[str, list[tuple[datetime, float]]] = {
        eid: [] for eid in energy_ids if eid in all_ids
    }
    # pv_sum_results: the PV combined series (ADR-027 decision 1): per slot the effective
    # values of all PV inputs added up, in W (``effective_values_w`` is W for every input)
    pv_ids: set[str] = set(pv_input_ids(entry_options))
    pv_sum_results: list[tuple[datetime, float]] = []
    # pv_share_results: the PV share (ADR-027 amendment 2026-11-09): per slot 100 x (the effective
    # PV + the effective EPS input) / (all effective inputs), and the denominator of the slot,
    # the weight of the hourly row. It exists for an entry with a PV input or an EPS sensor.
    pv_share_results: list[tuple[datetime, float]] = []
    pv_share_weights: dict[datetime, float] = {}

    readings_for_slot = phases.wrap("readings", _readings_for_slot)
    waterfall = phases.wrap("waterfall", distribute_loss)
    to_original_unit = phases.wrap("conversion", effective_in_original_unit)
    eps = eps_id(entry_options)
    with phases.stage("slot loop", items=len(slots)):
        for slot in slots:
            input_readings = readings_for_slot(
                slot, SLOT_MINUTES, input_ids, indexed, state_classes, units
            )
            output_readings = readings_for_slot(
                slot, SLOT_MINUTES, output_ids, indexed, state_classes, units
            )
            if eps is not None:
                # ADR-026: the signed EPS value becomes an input half (in the waterfall's
                # pool) and an output half; total_loss = max(0, in + eps_in - out - eps_out)
                input_readings, output_readings = split_eps_readings(
                    eps, input_readings, output_readings
                )

            for reading in input_readings + output_readings:
                if reading.entity_id in power_results:
                    power_results[reading.entity_id].append((slot, reading.raw_value))

            if not input_readings:
                continue

            distribution = waterfall(input_readings, output_readings)

            for reading in input_readings:
                eff = to_original_unit(reading.entity_id, distribution, reading.original_unit)
                results[reading.entity_id].append((slot, eff))

            pv_values = [
                distribution.effective_values_w[reading.entity_id]
                for reading in input_readings
                if reading.entity_id in pv_ids
            ]
            if pv_values:
                pv_sum_results.append((slot, sum(pv_values)))

            if pv_ids or eps is not None:
                # the effective values are in W for every input (ADR-008); the EPS input half is
                # the EPS sensor's entry in ``input_readings`` (``split_eps_readings``)
                pv_side = sum(
                    distribution.effective_values_w[reading.entity_id]
                    for reading in input_readings
                    if reading.entity_id in pv_ids or reading.entity_id == eps
                )
                all_inputs = sum(
                    distribution.effective_values_w[reading.entity_id] for reading in input_readings
                )
                pv_share_results.append((slot, pv_share(pv_side, all_inputs)))
                pv_share_weights[slot] = all_inputs

    per_sensor: dict[str, dict[str, Any]] = {}
    for eid in input_ids:
        if not results[eid]:
            continue
        per_sensor[_effy_entity_id(eid)] = {
            # results[eid] holds effective_in_original_unit() output, which
            # for an energy-family source is already a W/kW-equivalent power
            # reading (to_power_equivalent converts before distribute_loss
            # ever runs — ADR-008), never a genuine Wh/kWh energy figure.
            # Writing units[eid] (the source's raw unit) here would label
            # those W/kW numbers as Wh/kWh — a category error, not just a
            # scale error. See the bug this fixed.
            "unit": effective_unit_for(units[eid]),
            "slot_values": results[eid],
        }

    if pv_sum_results:
        per_sensor[PV_SUM_ENTITY_ID] = {"unit": "W", "slot_values": pv_sum_results}
    if pv_share_results:
        per_sensor[PV_SHARE_ENTITY_ID] = {
            "unit": "%",
            "slot_values": pv_share_results,
            "weights": pv_share_weights,
            # the effective series it is computed from: an hour of a history run is unchanged
            # only if their 5-minute rows are too (``_unchanged_weighted_hours``)
            "sources": [_effy_entity_id(eid) for eid in input_ids],
        }

    per_sensor_power: dict[str, dict[str, Any]] = {}
    for eid in power_results:
        if not power_results[eid]:
            continue
        per_sensor_power[_effy_power_entity_id(eid)] = {
            "unit": effective_unit_for(units[eid]),
            "slot_values": power_results[eid],
        }

    per_sensor_smoothed: dict[str, dict[str, Any]] = {}
    for eid, filled in smoothed_series.items():
        if eid in extra_ids:
            continue
        slot_values = sorted((ts, val) for ts, val in filled.items() if start <= ts < end)
        if not slot_values:
            continue
        per_sensor_smoothed[_effy_smoothed_entity_id(eid)] = {
            # Power-family sensors are already in W/kW — no to_power_equivalent
            # conversion applies here (that's Wh/kWh → W only), so the raw
            # units[eid] is the correct unit to write, unlike per_sensor above.
            "unit": units[eid],
            "slot_values": slot_values,
        }

    per_sensor_power.update(extra_power)
    per_sensor_smoothed.update(extra_smoothed)

    phases.enter(None)
    return per_sensor, per_sensor_power, per_sensor_smoothed


def _drop_open_slot(
    series: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Apply ``calculation.drop_open_slot`` to every entity of one per-sensor
    series (ADR-016 Amendment 2026-10-16). Returns a new dict; an entity that
    has nothing left afterwards is left out, so it is neither written nor
    reported as touched."""
    result: dict[str, dict[str, Any]] = {}
    for statistic_id, info in series.items():
        slot_values = drop_open_slot(info["slot_values"], now, SLOT_MINUTES)
        if slot_values:
            result[statistic_id] = {**info, "slot_values": slot_values}
    return result


def _last_slot_values(
    *series: dict[str, dict[str, Any]],
) -> dict[str, tuple[float, str]]:
    """Extract each entity's most-recent-slot ``(value, unit)`` across one
    or more per-sensor series (ADR-016).

    Each series maps a statistic_id to ``{"unit", "slot_values"}``, with
    ``slot_values`` a ``(timestamp, value)`` list in ascending slot order
    (guaranteed by ``_compute_effective_slots`` — see the comment on
    ``_write_recorder_statistics``'s hourly-aggregation loop for the same
    assumption already relied on elsewhere). The last entry is therefore
    the latest slot written *this run* for that entity — not necessarily
    "now" itself (a sensor that came back online mid-window may have its
    latest write short of ``now``), but the most recent one this
    recalculation actually produced, which is the best available stand-in
    for "the current value" (ADR-016).

    Statistic ids across per_sensor/per_sensor_power/per_sensor_smoothed
    never collide (they carry different suffixes — ``_effy_entity_id`` vs
    ``_effy_power_entity_id`` vs ``_effy_smoothed_entity_id``), so merging
    all given series into one dict is safe.
    """
    last_values: dict[str, tuple[float, str]] = {}
    for one_series in series:
        for statistic_id, info in one_series.items():
            slot_values: list[tuple[datetime, float]] = info["slot_values"]
            if not slot_values:
                continue
            _last_slot, last_value = slot_values[-1]
            last_values[statistic_id] = (last_value, info["unit"])
    return last_values


async def async_recalculate_history(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    energy_reading_cache: dict[str, tuple[datetime, str]] | None = None,
    storage: Storage | None = None,
    export: ExportHook | None = None,
    delta_skip: frozenset[str] = frozenset(),
) -> _RecalcResult:
    """
    Recalculate and overwrite effy statistics for up to max_history_days.

    Existing statistics for the same statistic_id + timestamp are
    overwritten, not appended to — see ADR-004 for why this is intentional
    (stale rows from a previous sensor configuration must not survive a
    recalculation).

    Energy-family sensors' per-slot values are computed via the
    trapezoidal rule (ADR-012, calculation.trapezoidal_slot_contributions)
    applied to raw state history, replacing ADR-009's neighbor-steal
    smoothing. A genuinely idle stretch (the counter simply didn't move,
    online or not) now gets explicit 0 entries rather than none at all
    (ADR-013) — this is where that shows up most: a full recalc walks the
    entire max_history_days window, so any long-idle period within it (an
    empty battery's 0 discharge, several zero-import days, …) gets fully
    zero-filled here, not just the last 15 minutes of it. Also writes the
    raw, pre-loss-distribution derived-power statistic (effy_*_power) for
    every energy-family sensor, input and output alike (ADR-012) — new in
    addition to the existing "effective" (post-waterfall, input-only)
    statistic. Power-family *input* sensors additionally get
    gap-interpolated values (calculation.interpolate_slot_gaps) merged
    into distribute_loss's own input and written out as a third
    effy_*_smoothed statistic.

    Writes both short-term (5-minute) and long-term (hourly) statistics for
    all three series — see ADR-011 for why the hourly aggregate specifically
    requires a full multi-slot range like this one, and must not be
    written from the slot-timer-driven recent recalculation.

    Returns (short_term_rows_written, recalculated_from, touched_entity_ids,
    last_values, deltas); ``deltas`` is the PV delta of every PV input (see
    ``_pv_deltas``), or ``None`` if the stored rows could not be read.
    A full recalc recomputes every slot in [start, end) but, since TASK-0030
    (ADR-024), writes only the rows that are new or differ from what is
    stored. recalculated_from is the earliest slot whose row was written (a
    changed hourly row counts from the first slot of its hour), or None if
    nothing was — ADR-012 Amendment 2026-10-25, and the "recalculated from"
    sensor. If the stored rows cannot be read, every row is written (warning).
    touched_entity_ids is every effy_* entity_id that was computed this run,
    across all three series, changed row or not — callers (button.py)
    push it, together with last_values, through
    EffyCoordinator.notify_updated() so those entities' dashboard cards
    see a state_changed event and refetch. last_values maps each touched
    entity_id to the ``(value, unit)`` of the *last* (most recent) slot
    written for it this run (``_last_slot_values``, ADR-016) — pushed as
    that entity's live state so a dashboard shows a real number instead
    of "unknown" between recalculations, even though the live path itself
    stays disabled; see ADR-016 (the open slot is never written, so this is
    the last completed slot's value). See the module WARNING docstring above
    for how (and why) this writes to internal recorder tables instead of
    using the public statistics import API.

    ``energy_reading_cache``, if given, is EffyCoordinator's volatile
    last-known-valid-reading cache (ADR-015) — passed straight through to
    _compute_effective_slots so an offline-anchor lookback that would
    otherwise hit the recorder can often be answered from memory instead.
    Safe to omit (defaults to None, meaning always query the recorder
    when a lookback is actually needed) — callers without access to a
    coordinator (e.g. tests) don't need to provide one.

    ``export``, if given, is the debug export (TASK-0037): once the run has written, it
    is called with a record of what the cache does not hold (what the run took for "now",
    the reading cache it started with, the rows the writer scheduled) and writes the
    file. Its time and its outcome are part of the timing record; its failure is logged
    and never changes the result of the run.
    """
    phases = _PhaseTimings("history", logging.INFO, parse_probe=True)
    storage = _storage_or_default(hass, storage, phases)
    storage.set_timing(phases)  # the recorder reads of this run, and only this run, are timed
    record = ExportRecord() if export is not None else None
    token = _active_export.set(record)
    try:
        return await _recalculate_history(
            hass, entry_options, energy_reading_cache, storage, phases, export, record, delta_skip
        )
    finally:
        _active_export.reset(token)
        storage.set_timing(_NO_TIMINGS)


async def _run_export(export: ExportHook, record: ExportRecord, phases: _PhaseTimings) -> None:
    """Call the export hook; whatever it does, the recalculation's result is unchanged."""
    began = _clock()
    phases.enter(None)
    try:
        outcome = await export(record)
    except Exception as err:  # never fails the recalculation (ADR-000 §8)
        _LOGGER.warning(
            "Effy: the debug export failed (%s: %s); the recalculation is not affected",
            type(err).__name__,
            err,
        )
        phases.note_export(_clock() - began, None, f"{type(err).__name__}: {err}")
        return
    if outcome is not None:
        phases.note_export(_clock() - began, outcome)


async def _recalculate_history(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    energy_reading_cache: dict[str, tuple[datetime, str]] | None,
    storage: Storage,
    phases: _PhaseTimings,
    export: ExportHook | None = None,
    record: ExportRecord | None = None,
    delta_skip: frozenset[str] = frozenset(),
) -> _RecalcResult:
    max_days: int = entry_options.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS)
    phases.detail_on(_environment_summary(hass, max_days))
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=max_days)
    if record is not None:
        record.ran_at, record.window_start = end, start
        record.note_reading_cache(energy_reading_cache)
    cache_before = _cache_counters(storage)
    short_term_retention_days = storage.retention_days()
    short_term_cutoff = end - timedelta(days=short_term_retention_days)

    per_sensor, per_sensor_power, per_sensor_smoothed = await _compute_effective_slots(
        hass,
        entry_options,
        start,
        end,
        energy_reading_cache=energy_reading_cache,
        phases=phases,
        storage=storage,
    )
    # No statistic for the open slot (ADR-016 Amendment 2026-10-16): the
    # recorder inserts that row itself; the live push below is then the last
    # completed slot's value, not a ~0 sliver.
    per_sensor = _drop_open_slot(per_sensor, end)
    per_sensor_power = _drop_open_slot(per_sensor_power, end)
    per_sensor_smoothed = _drop_open_slot(per_sensor_smoothed, end)
    if not per_sensor and not per_sensor_power and not per_sensor_smoothed:
        _LOGGER.warning("Effy history recalc: no statistics found in the requested period.")
        phases.finish(0)
        return 0, None, set(), {}, {}

    # Nothing Effy inserts first (TASK-0035, ADR-020 Amendment): the rows of a slot the
    # recorder has not surely compiled, and the hourly row of an hour it has not surely
    # rolled up, are left to the slot job, which updates them after the recorder's
    # compile. Every slot is still computed, so the live push below is unchanged.
    settled = settled_before(end)
    all_series = (per_sensor, per_sensor_power, per_sensor_smoothed)
    for series in all_series:
        for info in series.values():
            info["settled_before"] = settled
    hourly_left = _count_hourly_left(all_series, settled)

    # Read what is stored and mark the rows that are already right (ADR-024); the
    # writer skips them. The reads are recorder jobs, so they count as fetch.
    stored_values_read = await _mark_unchanged(
        hass,
        all_series,
        short_term_cutoff=short_term_cutoff,
        include_long_term=True,
        phases=phases,
        storage=storage,
        delta_series=_pv_delta_series(entry_options, delta_skip),
    )

    short_term_written = 0
    phases.enter("write")
    if per_sensor:
        with phases.write_call("effective"):
            short_term_written += await _write_and_forget(
                hass,
                storage,
                per_sensor,
                short_term_cutoff=short_term_cutoff,
                include_long_term=True,
            )
    if per_sensor_power:
        with phases.write_call("derived power"):
            short_term_written += await _write_and_forget(
                hass,
                storage,
                per_sensor_power,
                short_term_cutoff=short_term_cutoff,
                include_long_term=True,
            )
    if per_sensor_smoothed:
        with phases.write_call("smoothed"):
            short_term_written += await _write_and_forget(
                hass,
                storage,
                per_sensor_smoothed,
                short_term_cutoff=short_term_cutoff,
                include_long_term=True,
            )
    phases.enter(None)

    touched_entity_ids: set[str] = (
        set(per_sensor) | set(per_sensor_power) | set(per_sensor_smoothed)
    )
    last_values = _last_slot_values(per_sensor, per_sensor_power, per_sensor_smoothed)

    changes = [
        _earliest_change(series, short_term_cutoff=short_term_cutoff, include_long_term=True)
        for series in (per_sensor, per_sensor_power, per_sensor_smoothed)
    ]
    earliest = min((ts for ts in changes if ts is not None), default=None)
    unchanged = _count_unchanged((per_sensor, per_sensor_power, per_sensor_smoothed))

    _LOGGER.debug(
        "Effy: history recalculation wrote %d short-term slots (%d already up to date) "
        "across %d effective + %d derived-power + %d smoothed sensors, earliest change %s",
        short_term_written,
        unchanged,
        len(per_sensor),
        len(per_sensor_power),
        len(per_sensor_smoothed),
        earliest,
    )
    phases.note_unchanged(unchanged)
    phases.note_hourly_left(hourly_left)
    _note_cache_reads(phases, storage, cache_before)
    if export is not None and record is not None:
        record.result = {
            "rows_written": short_term_written,
            "recalculated_from": earliest.astimezone(timezone.utc).isoformat()
            if earliest is not None
            else None,
        }
        await _run_export(export, record, phases)
    phases.finish(short_term_written)
    deltas = _pv_deltas(
        per_sensor,
        entry_options,
        delta_skip,
        short_term_cutoff=short_term_cutoff,
        values_read=stored_values_read,
    )
    return short_term_written, earliest, touched_entity_ids, last_values, deltas


async def async_recalculate_recent(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    now: datetime,
    energy_reading_cache: dict[str, tuple[datetime, str]] | None = None,
    trigger_since: datetime | None = None,
    storage: Storage | None = None,
    delta_skip: frozenset[str] = frozenset(),
) -> _RecalcResult:
    """
    Recalculate and write effy statistics for whatever recent slots need it.

    Intended to be called shortly after a slot closes (EffyCoordinator's
    slot timer, ADR-011). Unlike the original fixed single-slot design,
    the exact range recomputed is dynamic (ADR-012, ADR-014 Amendment
    2026-10-05): an idle cycle rewrites every slot within
    RECENT_RECALC_WINDOW of ``now`` for *any* sensor, but a trapezoidal-
    redistributed energy jump can touch many more slots than that — up to
    24 for a normal (120-minute-capped, ADR-014) jump, more for an
    offline-recovery jump — so when a real counter jump arrived inside
    RECENT_RECALC_WINDOW, the range is extended back (for all sensors and
    all three series at once) to where that jump's own distribution window
    starts, bounded by RECENT_REWRITE_MAX_LOOKBACK. That keeps the
    slot-timer output equal to what a full history recalc would write —
    including conserving the jump's energy — instead of leaving the older
    part of the jump's window at stale 0s until the next full recalc.
    RECENT_RECALC_WINDOW itself stays small and decoupled from
    TRAPEZOID_MAX_MINUTES (just enough to cover a missed timer tick or
    two — see its own comment for why it doesn't grow with the cap), so
    idle cycles cost what they always did. A sensor that comes back online
    from a genuinely long offline gap is handled via
    _compute_effective_slots' staged _fetch_last_valid_state_before
    lookback (30 min → 1 day → the full configured max_history_days,
    ADR-014) plus the extension above (up to RECENT_REWRITE_MAX_LOOKBACK);
    a gap older than that, or HA itself being down for that long, is still
    corrected by the next full history recalc.

    Deliberately writes ONLY the short-term (5-minute) statistic, never the
    long-term (hourly) one, for the "effective", "derived power", and
    "smoothed" series alike — see ADR-011 Decision 2 (unchanged by
    ADR-012 or the gap-interpolation feature).

    Returns (short_term_rows_written, recalculated_from, touched_entity_ids,
    last_values, deltas); ``deltas`` as in ``async_recalculate_history``.
    recalculated_from is the earliest slot whose stored row this run wrote
    across all three series, or None if nothing was written (every row
    already equalled what is stored, TASK-0031, ADR-024) — see ADR-012
    Amendment 2026-10-25 for how this feeds the "recalculated from" sensor.
    A counter jump extends the range that is recomputed (up to ~2 hours back
    for a normal jump, further for an offline-recovery jump, bounded by
    RECENT_REWRITE_MAX_LOOKBACK, ADR-014 Amendment), but only the rows that
    differ are written, and only they move this value.
    touched_entity_ids is every effy_* entity_id that was computed this run,
    changed row or not, so every live state is pushed (ADR-016) — the caller
    (EffyCoordinator's slot timer) pushes it, together with last_values,
    through notify_updated() so those entities' dashboard cards see a
    state_changed event and refetch. last_values maps each touched
    entity_id to the ``(value, unit)`` of the last (most recent) slot
    written for it this run (``_last_slot_values``) — pushed as that
    entity's live state instead of "unknown" (ADR-016, revisiting ADR-011
    Decision 4: still no backdating into the closed slot itself, just an
    ordinary *live* state update timestamped "now", additive to the
    statistics write, exactly as ADR-011 anticipated).

    ``trigger_since`` (ADR-014 Amendment 2026-10-06) is how far back a jump
    may have arrived without any cycle having applied it yet — see
    ``recent_trigger_since``, which EffyCoordinator uses to derive it from
    the end time of the last completed cycle (one-off catch-up window of
    RECENT_REWRITE_MAX_LOOKBACK on the first cycle of a session). It widens
    the window in which a jump triggers the extension, so a jump that
    arrived while the timer was not running (HA restart, suspended host)
    is no longer left half-written. ``None`` (the default) keeps the
    pre-amendment trigger window of exactly RECENT_RECALC_WINDOW.

    The slot that contains ``now`` is still open — only seconds of data
    stand behind it. **No statistic is written for it** in any series
    (``calculation.drop_open_slot``, ADR-016 Amendment 2026-10-16, TASK-0007a;
    supersedes the provisional value of the 2026-10-06 amendment): the
    recorder inserts that row itself when the slot ends, and Effy updates it
    in the next cycle. ADR-016's live push (the last slot written) therefore
    carries the last *completed* slot's value, as the provisional value did.
    The full history recalc does the same.

    ``energy_reading_cache``, if given, is EffyCoordinator's volatile
    last-known-valid-reading cache (ADR-015) — passed straight through to
    _compute_effective_slots so a sensor whose last valid reading was
    already seen in some earlier cycle this session can have its
    offline-anchor lookback (when one is even needed — see
    _compute_effective_slots' docstring) answered from memory, without a
    single recorder query. Safe to omit (defaults to None); the slot timer
    always provides one in practice.
    """
    phases = _PhaseTimings("recent", logging.DEBUG)
    storage = _storage_or_default(hass, storage, phases)
    storage.set_timing(phases)
    try:
        return await _recalculate_recent(
            hass,
            entry_options,
            now,
            energy_reading_cache,
            trigger_since,
            storage,
            phases,
            delta_skip,
        )
    finally:
        storage.set_timing(_NO_TIMINGS)


async def _recalculate_recent(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    now: datetime,
    energy_reading_cache: dict[str, tuple[datetime, str]] | None,
    trigger_since: datetime | None,
    storage: Storage,
    phases: _PhaseTimings,
    delta_skip: frozenset[str] = frozenset(),
) -> _RecalcResult:
    start = now - RECENT_RECALC_WINDOW
    per_sensor, per_sensor_power, per_sensor_smoothed = await _compute_effective_slots(
        hass,
        entry_options,
        start,
        now,
        energy_reading_cache=energy_reading_cache,
        extend_for_jumps=True,
        trigger_since=trigger_since,
        phases=phases,
        storage=storage,
    )
    # No statistic for the open slot (ADR-016 Amendment 2026-10-16): the
    # recorder inserts that row itself, Effy only updates it afterwards.
    per_sensor = _drop_open_slot(per_sensor, now)
    per_sensor_power = _drop_open_slot(per_sensor_power, now)
    per_sensor_smoothed = _drop_open_slot(per_sensor_smoothed, now)
    if not per_sensor and not per_sensor_power and not per_sensor_smoothed:
        _LOGGER.debug(
            "Effy recent recalc: nothing to recompute in the %s before %s",
            RECENT_RECALC_WINDOW,
            now,
        )
        phases.finish(0)
        return 0, None, set(), {}, {}

    # Read what is stored and mark the rows that are already right (ADR-024); the
    # writer skips them. ``touched``/``last_values`` below stay "every entity computed":
    # the live states of ADR-016 go out for all of them, changed row or not.
    all_series = (per_sensor, per_sensor_power, per_sensor_smoothed)
    stored_values_read = await _mark_unchanged(
        hass,
        all_series,
        short_term_cutoff=None,
        include_long_term=False,
        phases=phases,
        storage=storage,
        delta_series=_pv_delta_series(entry_options, delta_skip),
    )

    written = 0
    for series in all_series:
        if not series:
            continue
        phases.enter("write")
        written += await _write_and_forget(hass, storage, series, include_long_term=False)
        phases.enter(None)
    earliest = min(
        (
            ts
            for ts in (
                _earliest_change(series, short_term_cutoff=None, include_long_term=False)
                for series in all_series
            )
            if ts is not None
        ),
        default=None,
    )
    unchanged = _count_unchanged(all_series)

    touched_entity_ids: set[str] = (
        set(per_sensor) | set(per_sensor_power) | set(per_sensor_smoothed)
    )
    last_values = _last_slot_values(per_sensor, per_sensor_power, per_sensor_smoothed)

    _LOGGER.debug(
        "Effy: recent recalculation wrote %d short-term rows (%d already up to date) across "
        "%d effective + %d derived-power + %d smoothed sensors, earliest changed slot %s",
        written,
        unchanged,
        len(per_sensor),
        len(per_sensor_power),
        len(per_sensor_smoothed),
        earliest,
    )
    phases.note_unchanged(unchanged)
    phases.finish(written)
    deltas = _pv_deltas(
        per_sensor,
        entry_options,
        delta_skip,
        short_term_cutoff=None,
        values_read=stored_values_read,
    )
    return written, earliest, touched_entity_ids, last_values, deltas


async def async_rewrite_hourly(
    hass: HomeAssistant,
    units: dict[str, str],
    hour_start: datetime,
    storage: Storage | None = None,
    entry_options: dict[str, Any] | None = None,
) -> int:
    """Rewrite one hour's long-term statistic from its 12 short-term rows (TASK-0007a).

    The recorder rolls an hour up in the same compile that inserts the hour's
    last slot, before the 5-minute event fires, so that slot enters the roll-up
    with the recorder's native value and Effy's update of it comes too late
    (ADR-011 Amendment 2026-10-16). The slot job calls this once, after its
    recent recalculation (so the just-updated last slot is included), when the
    last completed slot is the last of its hour.

    ``units`` maps every statistic_id to rewrite to its unit (the recent
    recalculation's ``last_values`` carry both). Reads the short-term ``mean``
    rows in ``[hour_start, hour_start + 1 hour)`` in one call and writes the
    hourly rows with the same writer, and so the same mean/state semantics, as
    the full history recalculation (``include_short_term=False``). A statistic
    without any row in the hour is skipped. Returns the number of statistics
    rewritten.

    The PV share (``PV_SHARE_ENTITY_ID``, TASK-0046) is the exception: its hourly row is
    energy-weighted and the stored 5-minute shares cannot give the weights back, so its hour
    is recomputed over the whole hour with ``_compute_effective_slots``, the code of the full
    recalculation, from ``entry_options``. Without them the share is left out (its hour is
    corrected by the next history recalculation) rather than written as the mean of its slots.

    Exceptions propagate; the caller logs them and does not retry — the next history
    recalculation corrects the hour.
    """
    statistic_ids = sorted(sid for sid in units if sid != PV_SHARE_ENTITY_ID)
    share_wanted = PV_SHARE_ENTITY_ID in units and entry_options is not None
    if not statistic_ids and not share_wanted:
        return 0
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    hour_end = hour_start + timedelta(hours=1)
    rows: dict[str, list[StatRow]] = (
        await storage.statistics(statistic_ids, hour_start, hour_end) if statistic_ids else {}
    )
    series: dict[str, dict[str, Any]] = {}
    for statistic_id in statistic_ids:
        slot_values = sorted(
            (row["start"], float(row["mean"]))
            for row in rows.get(statistic_id, [])
            if row.get("mean") is not None
        )
        if slot_values:
            series[statistic_id] = {"unit": units[statistic_id], "slot_values": slot_values}
    if share_wanted and entry_options is not None:
        per_sensor, _power, _smoothed = await _compute_effective_slots(
            hass, entry_options, hour_start, hour_end, storage=storage
        )
        if PV_SHARE_ENTITY_ID in per_sensor:
            series[PV_SHARE_ENTITY_ID] = per_sensor[PV_SHARE_ENTITY_ID]
    if not series:
        return 0
    # An hourly row that is already what the writer would write is not written again.
    await _mark_unchanged(
        hass,
        (series,),
        short_term_cutoff=None,
        include_long_term=True,
        include_short_term=False,
        phases=_NO_TIMINGS,
        storage=storage,
    )
    await _write_and_forget(hass, storage, series, include_long_term=True, include_short_term=False)
    return len(series)


@dataclass(frozen=True)
class DeltaBase:
    """Where a PV delta integral's live sensor continues (ADR-027 decision 5).

    The ``state``, the ``last_reset`` and the running total of the last row the derivation
    wrote; ``0`` and no ``last_reset`` when there was nothing to derive.
    """

    state: float
    last_reset: datetime | None
    total: float


def _pv_delta_integral_id(entity_id: str) -> str:
    """The statistic ID of one PV input's *today* integral (ADR-027 decision 3)."""
    return _effy_entity_id(entity_id) + PV_DELTA_INTEGRAL_TODAY_SUFFIX


def _local_tz(hass: HomeAssistant) -> tzinfo:
    """Home Assistant's time zone; UTC, with a warning, if it is not known."""
    name = getattr(getattr(hass, "config", None), "time_zone", None)
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        _LOGGER.warning("Effy: unknown time zone %r; the PV delta integrals use UTC days", name)
        return timezone.utc


def _fetch_integral_source(
    hass: HomeAssistant, statistic_id: str, start: datetime, end: datetime
) -> tuple[dict[datetime, float], dict[datetime, float]]:
    """Blocking, run in the executor: the stored 5-minute and hourly ``mean`` of one series."""
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        statistics_during_period,
    )

    found: list[dict[datetime, float]] = []
    for period in ("5minute", "hour"):
        result = statistics_during_period(
            hass,
            start,
            end,
            statistic_ids=[statistic_id],
            period=period,
            units=None,
            types={"mean"},
        )
        rows: dict[datetime, float] = {}
        for row in result.get(statistic_id, []):
            ts = row["start"]
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=timezone.utc)
            if row.get("mean") is not None:
                rows[ts] = float(row["mean"])
        found.append(rows)
    return found[0], found[1]


def _fetch_integral_stored(
    hass: HomeAssistant, statistic_id: str, start: datetime, end: datetime
) -> tuple[dict[datetime, IntegralStat], dict[datetime, IntegralStat]]:
    """Blocking, run in the executor: the stored 5-minute and hourly rows of an integral.

    ``state``, ``sum`` and ``last_reset`` of every row that has a ``state`` and a ``sum``
    (ADR-027 decisions 5 and 9); a row without either is left out.
    """
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        statistics_during_period,
    )

    def moment(value: Any) -> datetime | None:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return value if isinstance(value, datetime) else None

    found: list[dict[datetime, IntegralStat]] = []
    for period in ("5minute", "hour"):
        result = statistics_during_period(
            hass,
            start,
            end,
            statistic_ids=[statistic_id],
            period=period,
            units=None,
            types={"state", "sum", "last_reset"},
        )
        rows: dict[datetime, IntegralStat] = {}
        for row in result.get(statistic_id, []):
            ts = moment(row["start"])
            if ts is None or row.get("sum") is None or row.get("state") is None:
                continue
            rows[ts] = IntegralStat(
                float(row["state"]), float(row["sum"]), moment(row.get("last_reset"))
            )
        found.append(rows)
    return found[0], found[1]


def _integral_statistics(
    rows: list[IntegralRow], settled: datetime
) -> tuple[list[StatisticData], list[StatisticData]]:
    """The 5-minute and the hourly rows to write for one integral (ADR-027 decision 5).

    5-minute rows only for slots that start before ``settled`` and only where a 5-minute row
    exists; hourly rows only for closed hours (``hour + 1 h <= settled``), taking the values of
    the last row of the hour, like the recorder does. The settled rule of ADR-024: a slot or an
    hour the recorder has not surely compiled is never written, so the recorder never inserts a
    row first that Effy then overwrites.
    """

    def stat(row: IntegralRow, start: datetime) -> StatisticData:
        data: StatisticData = {"start": start, "state": row.state, "sum": row.total}
        if row.last_reset is not None:
            data["last_reset"] = row.last_reset
        return data

    short = [stat(row, row.start) for row in rows if row.short and row.start < settled]
    last_of_hour: dict[datetime, IntegralRow] = {}
    for row in rows:
        hour = row.start.replace(minute=0, second=0, microsecond=0)
        if hour + timedelta(hours=1) <= settled:
            last_of_hour[hour] = row
    hourly = [stat(row, hour) for hour, row in last_of_hour.items()]
    return short, hourly


async def async_derive_pv_delta_integrals(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    *,
    now: datetime | None = None,
    storage: Storage | None = None,
) -> dict[str, DeltaBase]:
    """Derive, once, the history of the PV delta integrals (ADR-027 decision 5).

    For every PV input whose *today* integral has no statistics, and for the combined *today*
    and *total* integrals if they have none, the history is derived from the stored effective
    rows (``calculation.slot_energy_steps``, ``integral_rows``, ``combined_integral_rows``) and
    written in both tables as running-total rows (metadata ``has_sum``, no ``has_mean``). A
    series that already has statistics is skipped and writes nothing, so a second run does
    nothing. The combined integrals add up *all* PV inputs, those that were skipped too.

    Returns the delta base of every series it derived, keyed by its statistic ID; the last
    written row, or zero if there was nothing to derive (nothing is then written). A series that
    was skipped has no entry. Nothing calls this yet: the button never does (ADR-027
    decision 4), the implicit full recalculation will (TASK-0045e).
    """
    pv_inputs = list(pv_input_ids(entry_options))
    if not pv_inputs:
        return {}
    now = now if now is not None else datetime.now(tz=timezone.utc)
    settled = settled_before(now)
    tz = _local_tz(hass)
    instance = get_recorder(hass)
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)

    per_input_ids = {entity_id: _pv_delta_integral_id(entity_id) for entity_id in pv_inputs}
    combined_ids = (PV_DELTA_TODAY_ENTITY_ID, PV_DELTA_TOTAL_ENTITY_ID)
    targets = [*per_input_ids.values(), *combined_ids]
    existing = await storage.statistics_units(targets)
    missing = [statistic_id for statistic_id in targets if statistic_id not in existing]
    if not missing:
        return {}

    effective_ids = {entity_id: _effy_entity_id(entity_id) for entity_id in pv_inputs}
    effective_units = await storage.statistics_units(list(effective_ids.values()))
    max_days: int = entry_options.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS)
    start = now - timedelta(days=max_days)
    steps: dict[str, list[IntegralStep]] = {}
    units: dict[str, str] = {}
    for entity_id, effective_id in effective_ids.items():
        five, hourly = await storage.stored_means(effective_id, start, now)
        steps[entity_id] = slot_energy_steps(five, hourly, SLOT_MINUTES)
        # the effective series is in W (energy in Wh) or kW (energy in kWh)
        units[entity_id] = "kWh" if effective_units.get(effective_id) == "kW" else "Wh"

    series: dict[str, tuple[str, list[IntegralRow]]] = {}
    for entity_id, statistic_id in per_input_ids.items():
        if statistic_id in missing:
            rows = integral_rows(steps[entity_id], tz, today=True)
            series[statistic_id] = (units[entity_id], rows)
    if any(statistic_id in missing for statistic_id in combined_ids):
        today_rows, total_rows = combined_integral_rows(
            [(steps[entity_id], units[entity_id]) for entity_id in pv_inputs], tz
        )
        for statistic_id, rows in zip(combined_ids, (today_rows, total_rows), strict=True):
            if statistic_id in missing:
                series[statistic_id] = ("kWh", rows)

    bases: dict[str, DeltaBase] = {}
    for statistic_id, (unit, rows) in series.items():
        short_stats, hourly_stats = _integral_statistics(rows, settled)
        metadata = _build_statistic_metadata(statistic_id, unit, has_sum=True)
        if short_stats:
            instance.async_import_statistics(metadata, short_stats, StatisticsShortTerm)
        if hourly_stats:
            instance.async_import_statistics(metadata, hourly_stats, Statistics)
        last = (short_stats or hourly_stats or [None])[-1]
        bases[statistic_id] = (
            DeltaBase(last["state"], last.get("last_reset"), last["sum"])
            if last is not None
            else DeltaBase(0.0, None, 0.0)
        )
    # the imports above only queued the work: the rows are in when this returns
    await instance.async_block_till_done()
    return bases


# Older than any statistic Effy holds: the start of a read of a whole running-total series.
_SERIES_START = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _last_integral_row(
    five: Mapping[datetime, IntegralStat], hourly: Mapping[datetime, IntegralStat]
) -> IntegralStat | None:
    """The stored row of a series that ends last: a 5-minute row ends 5 minutes, an hourly one
    1 hour after its start; the 5-minute row wins a tie (it is the finer reading)."""
    best: tuple[datetime, int, IntegralStat] | None = None
    for start, row in five.items():
        candidate = (start + timedelta(minutes=SLOT_MINUTES), 1, row)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    for start, row in hourly.items():
        candidate = (start + timedelta(hours=1), 0, row)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best is not None else None


async def async_read_delta_bases(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    *,
    now: datetime | None = None,
    storage: Storage | None = None,
) -> tuple[dict[str, DeltaBase], dict[str, str]]:
    """The base of every PV delta integral that already has statistics (ADR-027 decision 6).

    A PV input that is removed and added again still has its statistics: nothing is derived for
    it and its base is its last stored row (``state``, ``last_reset``, running total). The same
    holds for a combined integral that exists. Returns the bases keyed by statistic ID, with the
    unit each series is stored in; a series without statistics, or without rows, is absent.
    Reads only; writes nothing.
    """
    pv_inputs = list(pv_input_ids(entry_options))
    if not pv_inputs:
        return {}, {}
    now = now if now is not None else datetime.now(tz=timezone.utc)
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    statistic_ids = [
        *(_pv_delta_integral_id(entity_id) for entity_id in pv_inputs),
        PV_DELTA_TODAY_ENTITY_ID,
        PV_DELTA_TOTAL_ENTITY_ID,
    ]
    stored_units = await storage.statistics_units(statistic_ids)
    bases: dict[str, DeltaBase] = {}
    units: dict[str, str] = {}
    for statistic_id in statistic_ids:
        if statistic_id not in stored_units:
            continue
        five, hourly = await storage.stored_integral(
            statistic_id, _SERIES_START, now + timedelta(days=1)
        )
        last = _last_integral_row(five, hourly)
        if last is not None:
            bases[statistic_id] = DeltaBase(last.state, last.last_reset, last.total)
            units[statistic_id] = stored_units[statistic_id]
    return bases, units


async def async_rederive_pv_combined(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    *,
    now: datetime | None = None,
    storage: Storage | None = None,
) -> tuple[dict[str, DeltaBase], dict[str, str]]:
    """Derive the combined integrals again from the per-input statistics (ADR-027 decision 9).

    Q27 = A: when the set of PV inputs changes, the combined *today* and *total* integrals are
    the per-slot addition of the per-input statistics of the configured inputs: the energy of
    an input's slot is the change of its running total (``calculation.steps_from_running_totals``),
    added up in kWh (``combined_integral_rows``). An input that is removed leaves the combined
    history; one that is new comes from its own derivation, which must have run first; one
    without statistics adds nothing.

    A one-time rewrite: the two combined series are cleared (the recorder drops their rows and
    metadata, so that no row of the earlier composition is left and the running total stays
    continuous), then written in both tables like the derivation does (5-minute rows only up to
    the last completed slot, hourly rows only for closed hours). The first row of a series only
    fixes its starting point, so one step at the far end of the history is not counted. The
    clear comes after every read: a read that fails clears nothing.

    Returns the delta base of both combined series (the last written row, or zero if there was
    nothing to write) and their unit; ``({}, {})`` without a PV input, which leaves the
    combined statistics alone.
    """
    pv_inputs = list(pv_input_ids(entry_options))
    if not pv_inputs:
        return {}, {}
    now = now if now is not None else datetime.now(tz=timezone.utc)
    settled = settled_before(now)
    tz = _local_tz(hass)
    instance = get_recorder(hass)
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)

    per_input_ids = [_pv_delta_integral_id(entity_id) for entity_id in pv_inputs]
    stored_units = await storage.statistics_units(per_input_ids)
    per_input: list[tuple[list[IntegralStep], str]] = []
    for statistic_id in per_input_ids:
        if statistic_id not in stored_units:
            continue
        five, hourly = await storage.stored_integral(
            statistic_id, _SERIES_START, now + timedelta(days=1)
        )
        steps = steps_from_running_totals(
            {start: row.total for start, row in five.items()},
            {start: row.total for start, row in hourly.items()},
            SLOT_MINUTES,
        )
        per_input.append((steps, "kWh" if stored_units[statistic_id] == "kWh" else "Wh"))
    today_rows, total_rows = combined_integral_rows(per_input, tz)

    combined_ids = [PV_DELTA_TODAY_ENTITY_ID, PV_DELTA_TOTAL_ENTITY_ID]
    instance.async_clear_statistics(combined_ids)
    bases: dict[str, DeltaBase] = {}
    for statistic_id, rows in zip(combined_ids, (today_rows, total_rows), strict=True):
        short_stats, hourly_stats = _integral_statistics(rows, settled)
        metadata = _build_statistic_metadata(statistic_id, "kWh", has_sum=True)
        if short_stats:
            instance.async_import_statistics(metadata, short_stats, StatisticsShortTerm)
        if hourly_stats:
            instance.async_import_statistics(metadata, hourly_stats, Statistics)
        last = (short_stats or hourly_stats or [None])[-1]
        bases[statistic_id] = (
            DeltaBase(last["state"], last.get("last_reset"), last["sum"])
            if last is not None
            else DeltaBase(0.0, None, 0.0)
        )
    # the clear and the imports only queued the work: the rows are in when this returns
    await instance.async_block_till_done()
    return bases, dict.fromkeys(combined_ids, "kWh")


async def async_probe_last_slot_ready(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    now: datetime,
    storage: Storage | None = None,
) -> bool:
    """Whether the recorder has compiled the last completed slot (TASK-0006).

    The slot job's probe. The recorder fires its 5-minute statistics event from
    *inside* the compile, before the session commits, so right after the event
    the last completed slot's power-family ``mean`` may not be readable yet.
    Returns ``False`` only while some power-family sensor (input or output) is
    demonstrably still being compiled: the slot *before* the last completed one
    has a ``mean`` and the last completed one has none. A sensor with no data
    at all, or that had no value in the slot before, is not pending — so it
    never delays the job. Energy-family sensors are not probed: their values
    come from raw states (ADR-012/013), not from the compiled slot.

    One two-slot ``statistics_during_period`` read for all power-family
    sensors; nothing is read when there are none.
    """
    input_ids: list[str] = all_input_ids(entry_options)
    output_ids: list[str] = configured_output_ids(entry_options)
    all_ids: list[str] = input_ids + output_ids
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    stats_units: dict[str, str] = await storage.statistics_units(all_ids)
    power_ids = [
        eid
        for eid in all_ids
        if _stat_field_for(storage.state_class(eid), stats_units.get(eid) or storage.unit(eid))
        == "mean"
    ]
    if not power_ids:
        return True

    slot_seconds = SLOT_MINUTES * 60
    epoch = now.timestamp()
    last = datetime.fromtimestamp(epoch - (epoch % slot_seconds) - slot_seconds, tz=timezone.utc)
    previous = last - timedelta(seconds=slot_seconds)
    end = last + timedelta(seconds=slot_seconds)
    rows: dict[str, list[StatRow]] = await storage.statistics(power_ids, previous, end)
    for eid in power_ids:
        means = {row["start"]: row.get("mean") for row in rows.get(eid, [])}
        if means.get(previous) is not None and means.get(last) is None:
            return False
    return True


# How far back one standby replay may reach (ADR-018 §5, "the replay cost must be
# bounded"): a restart replays DRAIN_LOOKBACK before the last processed slot, but
# never more than this before the last completed slot. A longer downtime loses
# the intervals that would have been confirmed in the gap, nothing else.
STANDBY_REPLAY_MAX = timedelta(days=3)


async def _classify_sensors(
    hass: HomeAssistant,
    recorder: Any,
    entity_ids: list[str],
    storage: Storage | None = None,
) -> dict[str, tuple[str, bool]]:
    """``entity_id -> (unit, is_energy_family)``, the unit being the one the
    statistics are stored in (``_get_statistics_units``), like
    ``_compute_effective_slots``."""
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    stats_units: dict[str, str] = await storage.statistics_units(entity_ids)
    result: dict[str, tuple[str, bool]] = {}
    for eid in entity_ids:
        unit = stats_units.get(eid) or storage.unit(eid)
        result[eid] = (unit, _stat_field_for(storage.state_class(eid), unit) == "change")
    return result


async def async_read_drain_events(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    armed_since: dict[str, datetime],
    since: datetime,
    until: datetime,
    storage: Storage | None = None,
) -> list[DrainPeak | DrainBreak | DrainSeen]:
    """Peaks, breaks and seen readings of every BMS input in ``[since, until)`` (ADR-018 §1).

    Read from the raw state history, one bounded read per BMS input. A source
    that has an armed turn is read from one minute before the turn's peak, so
    the link between that peak and the next one is intact (the first row of a
    read carries the start time, not the real time of its state); all other
    sources from one minute before ``since``. Events before ``since`` are left out.
    Nothing is read without a BMS input.
    """
    bms_ids = bms_input_ids(entry_options)
    if not bms_ids:
        return []
    recorder = get_recorder(hass)
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    classes = await _classify_sensors(hass, recorder, bms_ids, storage)
    margin = timedelta(minutes=1)
    events: list[DrainPeak | DrainBreak | DrainSeen] = []
    for eid in bms_ids:
        unit, is_energy = classes[eid]
        start = min(armed_since.get(eid, since), since) - margin
        raw: list[tuple[datetime, str]] = await storage.raw_states(eid, start, until)
        if is_energy:
            found = energy_drain_events(eid, raw, 1000.0 if unit == "kWh" else 1.0)
            events.extend(e for e in found if since <= e.t < until)
        else:
            found_power = power_drain_events(eid, raw, 1000.0 if unit == "kW" else 1.0)
            events.extend(e for e in found_power if since <= e.t < until)
    return events


async def async_read_drain_activity(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    start: datetime,
    end: datetime,
    storage: Storage | None = None,
) -> dict[datetime, frozenset[str]]:
    """Which sensors show flow in which slot of ``[start, end)`` (ADR-018 §3).

    Read from the short-term statistics Effy itself writes, so a counter that
    redistributes late is seen as the waterfall sees it: ``effy_*_power`` for an
    energy-family sensor (input or output), ``effy_*_smoothed`` for a
    power-family input, the source's own ``mean`` for a power-family output.
    The result maps a slot to the *source* entity IDs with a value that is not
    zero. A slot in which no sensor has a statistic at all is left out: it is
    unknown, which the machine never treats as quiet. One read for all sensors.
    """
    input_ids = all_input_ids(entry_options)
    output_ids: list[str] = configured_output_ids(entry_options)
    all_ids = input_ids + output_ids
    if not all_ids:
        return {}
    recorder = get_recorder(hass)
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    classes = await _classify_sensors(hass, recorder, all_ids, storage)
    statistic_ids: dict[str, str] = {}  # statistic id -> source entity id
    for eid in all_ids:
        _unit, is_energy = classes[eid]
        if is_energy:
            statistic_ids[_effy_power_entity_id(eid)] = eid
        elif eid in input_ids:
            statistic_ids[_effy_smoothed_entity_id(eid)] = eid
        else:
            statistic_ids[eid] = eid
    rows: dict[str, list[StatRow]] = await storage.statistics(list(statistic_ids), start, end)
    active: dict[datetime, set[str]] = {}
    for statistic_id, stat_rows in rows.items():
        source = statistic_ids.get(statistic_id)
        if source is None:
            continue
        for row in stat_rows:
            slot = row["start"]
            value = row.get("mean")
            if value is None or not start <= slot < end:
                continue
            members = active.setdefault(slot, set())
            if value != 0.0:
                members.add(source)
    return {slot: frozenset(members) for slot, members in active.items()}


async def async_update_standby(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    state: DrainState,
    now: datetime,
    storage: Storage | None = None,
    window: timedelta | None = None,
) -> DrainState:
    """Feed the standby machine every slot that completed since it last ran (ADR-018 §3, §5).

    Call after the recent recalculation, under the same lock: the statistics it
    just wrote are what the machine reads. The slots are those after
    ``state.last_slot`` up to the last completed slot of ``now``. A fresh state
    (no processed slot) is a *replay*: it starts DRAIN_LOOKBACK before the last
    slot the restored state processed (or before ``now`` without one), at most
    STANDBY_REPLAY_MAX back, and feeds the same peaks and slots live operation
    would have seen; ``state.suppress_until`` keeps what the restored estimate
    already holds from counting twice. The open slot is never fed.

    ``window`` is the history button's rebuild (ADR-018 Amendment 2026-11-01): a fresh
    state is fed from ``window`` before the last completed slot, the way
    ``async_rebuild_efficiency`` does, instead of from the restart replay's start, and
    nothing is suppressed because nothing was restored.
    """
    if not bms_input_ids(entry_options):
        return state  # the sensor only exists with a BMS input (ADR-018 D4)
    slot_seconds = SLOT_MINUTES * 60
    slot_width = timedelta(seconds=slot_seconds)
    epoch = now.timestamp()
    last = datetime.fromtimestamp(epoch - (epoch % slot_seconds) - slot_seconds, tz=timezone.utc)
    if state.last_slot is not None:
        first = state.last_slot + slot_width
    else:
        if window is not None:
            first = last - window
        else:
            anchor = state.suppress_until if state.suppress_until is not None else last
            first = max(anchor - DRAIN_LOOKBACK, last - STANDBY_REPLAY_MAX)
        first = datetime.fromtimestamp(
            first.timestamp() - (first.timestamp() % slot_seconds), tz=timezone.utc
        )
    first = max(first, last - (window if window is not None else STANDBY_REPLAY_MAX))
    if first > last:
        return state
    slots: list[datetime] = []
    cursor = first
    while cursor <= last:
        slots.append(cursor)
        cursor += slot_width
    end = last + slot_width
    events = await async_read_drain_events(
        hass,
        entry_options,
        {state.armed.source: state.armed.t} if state.armed is not None else {},
        first,
        end,
        storage=storage,
    )
    needed = drain_activity_start(state)
    activity_start = min(needed, first - slot_width) if needed is not None else first - slot_width
    activity = await async_read_drain_activity(
        hass, entry_options, activity_start, end, storage=storage
    )
    return drain_feed(state, events, slots, activity)


# How far back the efficiency feed catches up after a first start or a restart
# (ADR-023 decision 9): the slots after the restored ``last_slot``, at most this many.
EFFICIENCY_CATCHUP_MAX = timedelta(days=3)


def _unseen_settled_slots(
    last_slot: datetime | None, now: datetime, window: timedelta
) -> tuple[datetime, datetime] | None:
    """The first and the last slot a machine has still to take, or ``None`` for none.

    The last is the last slot that is 125 min old at ``now`` (the machines take a slot only
    then); the first is the one after ``last_slot``, and for a state that has none, or one
    that lies more than ``window`` back, the slot ``window`` before the last (ADR-023
    decision 9). Both start on a slot boundary.
    """
    slot_seconds = SLOT_MINUTES * 60
    settled = (now - EFF_VALIDATION_DELAY).timestamp()
    last = datetime.fromtimestamp(settled - (settled % slot_seconds), tz=timezone.utc)
    floor = last - window
    first = last_slot + timedelta(seconds=slot_seconds) if last_slot is not None else floor
    first = max(first, floor)
    first = datetime.fromtimestamp(
        first.timestamp() - (first.timestamp() % slot_seconds), tz=timezone.utc
    )
    return None if first > last else (first, last)


def efficiency_roles(entry_options: dict[str, Any]) -> EffRoles:
    """The four counters of the entry as the machine wants them (ADR-023)."""
    return EffRoles(
        grid_import=grid_import_id(entry_options),
        discharge=discharge_id(entry_options),
        charge=charge_id(entry_options),
        grid_export=grid_export_id(entry_options),
    )


def efficiency_enabled(entry_options: dict[str, Any]) -> bool:
    """The machine runs only with a complete pair: grid import and charge, or
    discharge and grid export (ADR-023 decision 1)."""
    roles = efficiency_roles(entry_options)
    return bool(roles.grid_import and roles.charge) or bool(roles.discharge and roles.grid_export)


def _slot_kwh(power: float, unit: str) -> float:
    """The energy of one slot (kWh) of an average power in ``unit`` (W or kW)."""
    kilowatts = power if unit == "kW" else power / 1000.0
    return kilowatts * SLOT_MINUTES / 60.0


async def async_read_efficiency_energy(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    only: Collection[str] | None = None,
    with_standby: bool = True,
    storage: Storage | None = None,
) -> tuple[dict[datetime, dict[str, float]], dict[datetime, float]]:
    """The energy of each configured counter and the standby energy per slot of
    ``[start, end)``, in kWh (ADR-023 decision 3).

    A counter's energy is its derived power series (``effy_*_power``, before the loss is
    distributed) times 5 min; a power-family input is read from its smoothed series and
    a power-family output from its own ``mean``, as ``async_read_drain_activity`` reads
    them. The standby energy is the recorded ``mean`` of ``sensor.effy_standby_power``
    (W) times 5 min. A statistic that does not exist for a slot is absent from the
    result: unknown is never zero. One read for all of them. ``only`` limits the counters
    (the battery machine takes charge and discharge), ``with_standby=False`` leaves the
    standby statistic unread and the second result empty.
    """
    roles = efficiency_roles(entry_options)
    counters = [
        eid
        for eid in (roles.grid_import, roles.discharge, roles.charge, roles.grid_export)
        if eid and (only is None or eid in only)
    ]
    if not counters:
        return {}, {}
    recorder = get_recorder(hass)
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    classes = await _classify_sensors(hass, recorder, counters, storage)
    inputs = all_input_ids(entry_options)
    source_of: dict[str, str] = {}  # statistic id -> counter entity id
    unit_of: dict[str, str] = {}  # statistic id -> the unit its mean is in
    for eid in counters:
        unit, is_energy = classes[eid]
        if is_energy:
            statistic_id, unit = _effy_power_entity_id(eid), effective_unit_for(unit)
        elif eid in inputs:
            statistic_id = _effy_smoothed_entity_id(eid)
        else:
            statistic_id = eid
        source_of[statistic_id], unit_of[statistic_id] = eid, unit
    standby_ids = [EFFY_STANDBY_POWER_ENTITY_ID] if with_standby else []
    stored: dict[str, str] = await storage.statistics_units(standby_ids) if with_standby else {}
    rows: dict[str, list[StatRow]] = await storage.statistics(
        [*source_of, *standby_ids], start, end
    )
    energy: dict[datetime, dict[str, float]] = {}
    standby: dict[datetime, float] = {}
    standby_unit = stored.get(EFFY_STANDBY_POWER_ENTITY_ID) or "W"
    for statistic_id, stat_rows in rows.items():
        for row in stat_rows:
            slot, value = row["start"], row.get("mean")
            if value is None or not start <= slot < end:
                continue
            if statistic_id == EFFY_STANDBY_POWER_ENTITY_ID:
                standby[slot] = _slot_kwh(value, standby_unit)
            elif statistic_id in source_of:
                energy.setdefault(slot, {})[source_of[statistic_id]] = _slot_kwh(
                    value, unit_of[statistic_id]
                )
    return energy, standby


async def async_read_counter_resolution(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    start: datetime,
    end: datetime,
    storage: Storage | None = None,
) -> dict[str, float]:
    """The smallest jump (kWh) of each energy-family counter of the efficiency machine in
    ``[start, end)`` (ADR-023 Amendment 2026-11-04). A counter that did not jump, or a
    power-family one, is absent: its resolution is not known."""
    roles = efficiency_roles(entry_options)
    counters = [
        eid for eid in (roles.grid_import, roles.discharge, roles.charge, roles.grid_export) if eid
    ]
    if not counters:
        return {}
    recorder = get_recorder(hass)
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    classes = await _classify_sensors(hass, recorder, counters, storage)
    steps: dict[str, float] = {}
    for eid in counters:
        unit, is_energy = classes[eid]
        if not is_energy:
            continue
        raw: list[tuple[datetime, str]] = await storage.raw_states(eid, start, end)
        wh_per_unit = 1000.0 if unit == "kWh" else 1.0
        heights = [
            peak.height
            for peak in energy_drain_events(eid, raw, wh_per_unit)
            if isinstance(peak, DrainPeak)
        ]
        if heights:
            steps[eid] = min(heights) / 1000.0
    return steps


async def async_update_efficiency(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    state: RunState,
    now: datetime,
    window: timedelta = EFFICIENCY_CATCHUP_MAX,
    storage: Storage | None = None,
) -> RunState:
    """Feed the efficiency machine the slots it has not seen (ADR-023 decisions 3, 9).

    Call after the recent recalculation and the standby update, under the same lock: the
    statistics they just wrote are what the machine reads. The machine takes a slot only
    when it is 125 min old, so only the slots up to ``now - 125 min`` are read: the ones
    after ``state.last_slot``, and for a fresh state at most ``window`` back (3 days
    unless a rebuild asks for more). Nothing is read without a complete counter pair or
    without new slots. A slot that lacks a statistic of its activity or of a configured
    counter reaches the machine as a missing slot, never as zero; one that lacks the
    standby statistic reaches it
    without standby energy and does not count.
    """
    if not efficiency_enabled(entry_options):
        return state
    slot_range = _unseen_settled_slots(state.last_slot, now, window)
    if slot_range is None:
        return state
    first, last = slot_range
    end = last + timedelta(minutes=SLOT_MINUTES)
    activity = await async_read_drain_activity(hass, entry_options, first, end, storage=storage)
    energy, standby = await async_read_efficiency_energy(
        hass, entry_options, first, end, storage=storage
    )
    roles = efficiency_roles(entry_options)
    configured = [
        eid for eid in (roles.grid_import, roles.discharge, roles.charge, roles.grid_export) if eid
    ]
    slots: list[EffSlot] = []
    for slot in sorted(activity):
        known = energy.get(slot, {})
        if any(eid not in known for eid in configured):
            continue  # a counter without a statistic: the slot is unknown, not zero
        slots.append(
            EffSlot(
                start=slot,
                active=activity[slot],
                grid_import_kwh=known.get(roles.grid_import or "", 0.0),
                discharge_kwh=known.get(roles.discharge or "", 0.0),
                charge_kwh=known.get(roles.charge or "", 0.0),
                grid_export_kwh=known.get(roles.grid_export or "", 0.0),
                standby_kwh=standby.get(slot),
            )
        )
    state = run_learn_resolution(
        state, await async_read_counter_resolution(hass, entry_options, first, end, storage)
    )
    return run_feed(state, slots, roles, now)


async def async_rebuild_standby(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    now: datetime,
    storage: Storage | None = None,
) -> DrainState:
    """The standby machine as a history recalculation would leave it (ADR-018 Amendment).

    Called by the history button after the statistics were rewritten, with the
    recalculation lock held: a fresh machine replays the whole rebuild window (the
    same ``min(max_history_days, keep_days)`` the efficiency rebuild uses), so what it
    counts is what the rewritten statistics and the recorded counters say now. It is not
    the restart replay: nothing is restored, so nothing is suppressed.
    """
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    window = efficiency_rebuild_window(entry_options, storage.retention_days())
    return await async_update_standby(
        hass, entry_options, DrainState(), now, storage=storage, window=window
    )


def efficiency_rebuild_window(entry_options: dict[str, Any], keep_days: int) -> timedelta:
    """How far back a history rebuild replays: ``min(max_history_days, keep_days)`` days.

    The short-term statistics the machine reads exist only as long as the recorder keeps
    them (ADR-023 decision 9), and the history recalculation only rewrites
    ``max_history_days`` of them.
    """
    configured = int(entry_options.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS))
    return timedelta(days=min(configured, keep_days))


async def async_rebuild_efficiency(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    now: datetime,
    storage: Storage | None = None,
) -> RunState:
    """Replay the efficiency machine from empty over the history window (ADR-023 decision 9).

    Called after a history recalculation (by its caller, not inside it), when the
    statistics it rewrote are what the machine reads: a reconfigured system gets values
    at once instead of after weeks. The result is exactly what a first start with this
    window would catch up; the slot job then continues from its ``last_slot``. Slots
    without a standby statistic do not count and are no failure.
    """
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    window = efficiency_rebuild_window(entry_options, storage.retention_days())
    return await async_update_efficiency(
        hass, entry_options, RunState(), now, window=window, storage=storage
    )


def battery_enabled(entry_options: dict[str, Any]) -> bool:
    """The valley machine runs with the charge and discharge counters, the SoC sensor and the
    capacity entity (ADR-023 decision 2); the standby sensor and the other counters are not
    needed (battery efficiency does not wait for standby)."""
    return bool(
        charge_id(entry_options)
        and discharge_id(entry_options)
        and soc_id(entry_options)
        and capacity_id(entry_options)
    )


def _fetch_capacity_states(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, str, str | None]]:
    """Fetch the raw state history of the battery's capacity entity – run in executor.

    ``(time, state, unit)`` in chronological order, the state in effect at ``start`` first
    (stamped ``start``, as for ``_fetch_raw_energy_states``), including ``unavailable`` and
    ``unknown`` states: a capacity that is not known is not a capacity (ADR-023 decision 6).
    The unit is read from the attributes of the state itself, so a unit that changed is seen
    (the one call asks for the attributes, which ``_fetch_raw_energy_states`` does not).
    """
    from homeassistant.components.recorder.history import (  # noqa: PLC0415
        state_changes_during_period,
    )

    history_by_entity = state_changes_during_period(
        hass, start, end, entity_id, no_attributes=False
    )
    return [
        (s.last_changed, s.state, s.attributes.get("unit_of_measurement"))
        for s in history_by_entity.get(entity_id, [])
    ]


async def async_update_battery(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    state: ValleyState,
    now: datetime,
    window: timedelta = EFFICIENCY_CATCHUP_MAX,
    storage: Storage | None = None,
) -> ValleyState:
    """Feed the valley machine the slots it has not seen (ADR-023 decisions 6, 9).

    Call after the efficiency update, under the same lock: the derived power series it reads
    are what the recent recalculation just wrote. The machine takes a slot only when it is
    125 min old, so only the slots up to ``now - 125 min`` are read: the ones after
    ``state.last_slot``, and for a fresh state, or one that is further back than ``window``,
    at most ``window`` back. A span that was open when the state is further back than that
    is lost (the first slot after the gap is not the one after ``last_slot``, so the machine
    resets its detection), never counted wrongly; what was measured stays. Nothing is read
    without a complete battery configuration or without new slots.

    Every slot of the range reaches the machine, with what the recorder has of it: a SoC
    that is missing is a missing slot (a reset); a charge or discharge statistic that is
    missing is not zero but absent (a span that contains it is dropped); the capacity is
    that of the entity's state in the middle of the slot, in kWh (``capacity_by_slot``).
    """
    if not battery_enabled(entry_options):
        return state
    slot_range = _unseen_settled_slots(state.last_slot, now, window)
    if slot_range is None:
        return state
    first, last = slot_range
    slot_width = timedelta(minutes=SLOT_MINUTES)
    end = last + slot_width
    soc_entity, capacity_entity = soc_id(entry_options), capacity_id(entry_options)
    charge_entity, discharge_entity = charge_id(entry_options), discharge_id(entry_options)
    assert soc_entity and capacity_entity and charge_entity and discharge_entity
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    soc_rows: dict[str, list[StatRow]] = await storage.statistics([soc_entity], first, end)
    energy, _standby = await async_read_efficiency_energy(
        hass,
        entry_options,
        first,
        end,
        only=(charge_entity, discharge_entity),
        with_standby=False,
        storage=storage,
    )
    capacity_states: list[tuple[datetime, str, str | None]] = await storage.capacity_states(
        capacity_entity, first, end
    )
    # A slot the recorder has no row for, or one without a mean, has no SoC: ``None``.
    soc: dict[datetime, float | None] = {
        row["start"]: row["mean"] for row in soc_rows.get(soc_entity, [])
    }
    starts: list[datetime] = []
    cursor = first
    while cursor <= last:
        starts.append(cursor)
        cursor += slot_width
    capacity = capacity_by_slot(capacity_states, starts)
    slots = [
        BatSlot(
            start=slot,
            soc=soc.get(slot),
            charge_kwh=energy.get(slot, {}).get(charge_entity),
            discharge_kwh=energy.get(slot, {}).get(discharge_entity),
            capacity_kwh=capacity[slot],
        )
        for slot in starts
    ]
    return valley_feed(state, slots, now, battery_hysteresis(entry_options))


async def async_rebuild_battery(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    now: datetime,
    storage: Storage | None = None,
) -> ValleyState:
    """Replay the valley machine from empty over the history window (ADR-023 decision 9).

    Called after a history recalculation, like ``async_rebuild_efficiency`` and with the same
    window: ``min(max_history_days, recorder retention)``. The result is what a first start with
    this window would catch up; the slot job then continues from its ``last_slot``.
    """
    storage = _storage_or_default(hass, storage, _NO_TIMINGS)
    window = efficiency_rebuild_window(entry_options, storage.retention_days())
    return await async_update_battery(
        hass, entry_options, ValleyState(), now, window=window, storage=storage
    )


def _fetch_statistics(
    hass: HomeAssistant,
    entity_ids: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[StatRow]]:
    """Blocking call to fetch 5-minute statistics – run in executor."""
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        statistics_during_period,
    )

    result: dict[str, list[StatRow]] = statistics_during_period(
        hass,
        start,
        end,
        statistic_ids=entity_ids,
        period="5minute",
        units=None,
        types={"mean", "change"},
    )

    # Defensive normalization: statistics_during_period is documented to
    # return "start" as a tz-aware datetime, but some HA core versions
    # (observed in the wild) return a float unix timestamp instead.
    # Normalize to tz-aware datetime so downstream code always gets a real
    # datetime object instead of crashing on `.tzinfo`.
    for rows in result.values():
        for row in rows:
            start_val = row.get("start")
            if isinstance(start_val, (int, float)):
                row["start"] = datetime.fromtimestamp(start_val, tz=timezone.utc)

    return result


def _hourly_rows(
    slot_values: list[tuple[datetime, float]],
    weights: Mapping[datetime, float] | None = None,
) -> dict[datetime, tuple[float, float]]:
    """The hourly rows of a series' slots: hour -> (mean, state).

    ``mean`` is the average of every slot in the hour, ``state`` the last one
    (``slot_values`` ascend). The single derivation behind both the writer and the
    comparison with what is stored (TASK-0030), so they cannot drift apart.

    A series with ``weights`` (the PV share, TASK-0046: slot -> the slot's denominator) is
    averaged **energy-weighted**, ``100 x sum(numerators) / sum(denominators)`` of the hour, and
    0 for an hour whose weights add up to nothing; its ``state`` is still the last slot's value.
    """
    hourly: dict[datetime, list[tuple[float, float]]] = {}
    for ts, val in slot_values:
        weight = 1.0 if weights is None else weights.get(ts, 0.0)
        hourly.setdefault(ts.replace(minute=0, second=0, microsecond=0), []).append((val, weight))
    return {
        hour: (
            (sum(v for v, _w in rows) / len(rows))
            if weights is None
            else weighted_share([v for v, _w in rows], [w for _v, w in rows]),
            rows[-1][0],
        )
        for hour, rows in hourly.items()
    }


# Columns of a stored row the writer controls: it writes mean and state and clears the
# rest (an update replaces every column, TASK-0007a), so a row is only "already right"
# if these are empty too. ``state`` and ``sum`` are not compared: Home Assistant's
# ``statistics_during_period`` silently discards both for a series whose metadata has
# ``has_sum`` false, which Effy's always has (verified in the source of 2026.10.4; the
# first Pi record showed ``state stored None`` in every row). The writer's ``state`` is
# the row's own value (5-minute: the mean; hourly: the last slot, which is in the mean),
# so an equal ``mean`` and empty columns are what the writer wrote (ADR-024 Amendment
# 2026-10-26).
_CLEARED_COLUMNS = ("min", "max", "last_reset")
_COMPARED_TYPES = {"mean", *_CLEARED_COLUMNS}


def _unchanged_slots(
    hass: HomeAssistant,
    statistic_id: str,
    period: str,
    expected: dict[datetime, float],
) -> tuple[frozenset[datetime], str, dict[datetime, float]]:
    """Starts of the stored rows that equal ``expected`` (start -> mean) exactly.

    Also returns a one-line diagnosis: how many stored rows were read, how many were
    equal and, for each of the others, which column differed, with the first pair of
    values (the first Pi run found nothing unchanged and could not say why), and the
    stored value (``mean``) of every row whose start is one of ``expected``, equal or
    not, for the PV delta (ADR-027 decision 2). It is the same single read.
    """
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        statistics_during_period,
    )

    first, last = min(expected), max(expected)
    last_end = last + timedelta(minutes=SLOT_MINUTES if period == "5minute" else 60)
    result: dict[str, list[StatRow]] = statistics_during_period(
        hass, first, last_end, {statistic_id}, period, None, _COMPARED_TYPES
    )
    rows = result.get(statistic_id, [])
    same: set[datetime] = set()
    stored: dict[datetime, float] = {}
    why = {"start": 0, "mean": 0, "columns": 0}
    seen = 0
    sample = ""
    for row in rows:
        raw_start = row.get("start")  # a datetime, or a unix timestamp on some versions
        start: Any = (
            datetime.fromtimestamp(raw_start, tz=timezone.utc)
            if isinstance(raw_start, (int, float))
            else raw_start
        )
        want = expected.get(start)
        if want is None:
            why["start"] += 1
            sample = sample or f"start {raw_start!r} is not one of the new rows"
            continue
        seen += 1
        if isinstance(row.get("mean"), (int, float)):
            stored[start] = float(row["mean"])
        reason, detail = "", ""
        if row.get("mean") != want:
            reason, detail = "mean", f"mean stored {row.get('mean')!r}, new {want!r}"
        else:
            for column in _CLEARED_COLUMNS:
                if row.get(column) is not None:
                    reason, detail = "columns", f"{column} stored {row.get(column)!r}"
                    break
        if not reason:
            same.add(start)
            continue
        why[reason] += 1
        sample = sample or f"{detail} at {start.isoformat()}"
    text = (
        f"{period}: read {len(rows)}, equal {len(same)}, missing {len(expected) - seen},"
        f" start unexpected {why['start']}, mean differs {why['mean']},"
        f" columns set {why['columns']}" + (f"; first: {sample}" if sample else "")
    )
    return frozenset(same), text, stored


def _unchanged_rows(
    hass: HomeAssistant,
    statistic_id: str,
    unit: str,
    short: list[tuple[datetime, float]],
    hourly: dict[datetime, tuple[float, float]],
    notes: dict[str, str] | None = None,
    values: bool = False,
) -> dict[str, Any]:
    """Which rows of one series are already exactly what the writer would write.

    Blocking: run in the recorder's executor, one series at a time. A series whose
    stored unit differs from ``unit`` (or has no metadata yet) has nothing unchanged:
    the import is what updates the metadata. ``short`` is the 5-minute rows to write,
    ``hourly`` the hourly ones.

    The result has the unchanged starts, ``"short"`` and ``"hourly"``. With ``values``
    it also has ``"stored"``: the stored 5-minute value of every row to write that is
    stored, converted to ``unit`` by the unit in the metadata (``{}`` with no
    metadata: such a value counts as 0). Only the PV inputs' effective series ask for
    it (ADR-024 Amendment 2026-11-09): their values cross to the event loop, where the
    PV delta is computed; every other series returns only its unchanged starts. It
    is the same read that finds the unchanged rows, never an additional one.
    """
    nothing: frozenset[datetime] = frozenset()
    stored_unit = _get_statistics_units(hass, [statistic_id]).get(statistic_id)
    if stored_unit != unit:
        if notes is not None:
            notes[statistic_id] = (
                "no metadata stored"
                if stored_unit is None
                else f"unit stored {stored_unit!r}, new {unit!r}: written in full"
            )
        result: dict[str, Any] = {"short": nothing, "hourly": nothing}
        if values:
            result["stored"] = {}
            if stored_unit is not None and short:
                _same, _text, stored = _unchanged_slots(hass, statistic_id, "5minute", dict(short))
                result["stored"] = {
                    start: convert_unit(value, stored_unit, unit) for start, value in stored.items()
                }
        return result
    found_short, text_short, stored_short = (
        _unchanged_slots(hass, statistic_id, "5minute", dict(short)) if short else (nothing, "", {})
    )
    found_hourly, text_hourly, _stored_hourly = (
        _unchanged_slots(
            hass, statistic_id, "hour", {h: mean for h, (mean, _state) in hourly.items()}
        )
        if hourly
        else (nothing, "", {})
    )
    if notes is not None:
        notes[statistic_id] = "; ".join(text for text in (text_short, text_hourly) if text)
    result = {"short": found_short, "hourly": found_hourly}
    if values:
        result["stored"] = stored_short
    return result


def _unchanged_hours(
    slot_values: list[tuple[datetime, float]], unchanged_short: frozenset[datetime]
) -> frozenset[datetime]:
    """The hours of a history run in which no change was written.

    The hourly rows of a history run are not read: an hour is rewritten only if a
    change was written in it, i.e. at least one of its slots is not an unchanged
    5-minute row (ADR-024 Amendment 2026-10-26). A slot older than the short-term
    cutoff has no stored 5-minute row to compare, so it counts as not unchanged.
    """
    hour = {ts: ts.replace(minute=0, second=0, microsecond=0) for ts, _val in slot_values}
    touched = {hour[ts] for ts in hour if ts not in unchanged_short}
    return frozenset(set(hour.values()) - touched)


def _unchanged_weighted_hours(
    info: dict[str, Any], all_series: tuple[dict[str, dict[str, Any]], ...]
) -> frozenset[datetime]:
    """The hours of a history run in which a weighted series (the PV share) has no change.

    The hour of the PV share is energy-weighted: it depends on the numerators and the
    denominators of its slots, so "none of its own 5-minute rows changed" is not enough (a slot
    whose share stays 0 while its weight changes changes the hour). It is unchanged only if
    also the 5-minute rows of every effective series it is computed from (``info["sources"]``)
    are unchanged in that hour, which fixes the numerator and the denominator of every slot.
    The exception is an hour in which every slot has the same share (a system without a
    battery or grid input is at 100 % all day): its weighted mean is that value whatever the
    weights are, so only the share's own rows count there. A series that has no row in a slot
    adds nothing to it; a row older than the short-term cutoff, which has no stored 5-minute row
    to compare, counts as changed. No hourly row is read: a history run does not read them
    (ADR-024 Amendment 2026-10-26).
    """

    def hour_of(ts: datetime) -> datetime:
        return ts.replace(minute=0, second=0, microsecond=0)

    unchanged: frozenset[datetime] = info.get("unchanged_short", frozenset())
    values_of: dict[datetime, set[float]] = {}
    for ts, value in info["slot_values"]:
        values_of.setdefault(hour_of(ts), set()).add(value)
    touched = {hour_of(ts) for ts, _value in info["slot_values"] if ts not in unchanged}
    for series in all_series:
        for source_id in info.get("sources", ()):
            source = series.get(source_id)
            if source is None:
                continue
            source_unchanged: frozenset[datetime] = source.get("unchanged_short", frozenset())
            touched.update(
                hour_of(ts)
                for ts, _value in source["slot_values"]
                if ts not in source_unchanged and len(values_of.get(hour_of(ts), ())) != 1
            )
    return frozenset(set(values_of) - touched)


async def _mark_unchanged(
    hass: HomeAssistant,
    all_series: tuple[dict[str, dict[str, Any]], ...],
    *,
    short_term_cutoff: datetime | None,
    include_long_term: bool,
    include_short_term: bool = True,
    phases: _PhaseTimings,
    storage: Storage | None = None,
    delta_series: frozenset[str] = frozenset(),
) -> bool:
    """Add ``unchanged_short`` and ``unchanged_hourly`` to every series entry (in place).

    The series in ``delta_series`` (the effective series of the PV inputs, ADR-027
    decision 2) also get ``stored_short``: the stored 5-minute values of the rows to write,
    for the PV delta. Returns whether those values were read: ``False`` if a read failed or
    a storage did not return them (an older snapshot), in which case the run has no delta.

    ``all_series`` are the series of one recalculation (effective, derived power,
    smoothed): one pass, one series per executor job, one warning at most.

    The writer then skips those rows. If the stored rows cannot be read, no entry is
    marked from then on (everything is written, as before this feature), and one
    warning says so: a failed read never fails a recalculation (ADR-024 Decision 3).
    """
    storage = _storage_or_default(hass, storage, phases)
    notes: dict[str, str] = {}
    values_read = True
    for series in all_series:
        for statistic_id, info in series.items():
            slot_values: list[tuple[datetime, float]] = info["slot_values"]
            short = (
                [
                    (ts, val)
                    for ts, val in slot_values
                    if short_term_cutoff is None or ts >= short_term_cutoff
                ]
                if include_short_term
                else []
            )
            # The hour rewrite (no 5-minute rows to go by) compares the stored hourly
            # rows; a history run derives its hours from its 5-minute result below.
            weights: Mapping[datetime, float] | None = info.get("weights")
            hourly = (
                _hourly_rows(slot_values, weights)
                if include_long_term and not include_short_term
                else {}
            )
            if not short and not hourly:
                continue
            wants_values = statistic_id in delta_series
            try:
                found: dict[str, Any] = (
                    await storage.unchanged_rows(
                        statistic_id, info["unit"], short, hourly, notes, values=True
                    )
                    if wants_values
                    else await storage.unchanged_rows(
                        statistic_id, info["unit"], short, hourly, notes
                    )
                )
            except Exception as err:  # a failed read must never fail the recalculation
                _LOGGER.warning(
                    "Effy: could not read the stored statistics (%s: %s); writing every row",
                    type(err).__name__,
                    err,
                )
                for done_series in all_series:
                    for done in done_series.values():
                        done.pop("unchanged_short", None)
                        done.pop("unchanged_hourly", None)
                        done.pop("stored_short", None)
                return False
            if wants_values:
                if "stored" not in found:  # a storage that cannot say: no delta
                    values_read = False
                else:
                    info["stored_short"] = found["stored"]
            info["unchanged_short"] = found["short"]
            info["unchanged_hourly"] = (
                _unchanged_hours(slot_values, found["short"])
                if include_long_term and include_short_term
                else found["hourly"]
            )
    if include_long_term and include_short_term:
        for series in all_series:
            for info in series.values():
                if "weights" in info:
                    info["unchanged_hourly"] = _unchanged_weighted_hours(info, all_series)
    phases.note_stored_checks(notes)
    return values_read


def _pv_delta_series(entry_options: dict[str, Any], delta_skip: frozenset[str]) -> frozenset[str]:
    """The statistic IDs of the effective series that need their stored values (the PV delta).

    Those of the PV inputs (the EPS sensor and the BMS inputs have no delta) except the
    inputs the caller marks as having no delta base yet (``delta_skip``, source entity IDs).
    """
    return frozenset(
        _effy_entity_id(entity_id)
        for entity_id in pv_input_ids(entry_options)
        if entity_id not in delta_skip
    )


def _pv_deltas(
    per_sensor: dict[str, dict[str, Any]],
    entry_options: dict[str, Any],
    delta_skip: frozenset[str],
    *,
    short_term_cutoff: datetime | None,
    values_read: bool,
) -> _PvDeltas | None:
    """The PV delta of this run per PV input (ADR-027 decision 2, glossary "PV delta").

    The energy the run changed in the stored rows of the input's effective series: the
    added-up ``(new - stored) * 5/60`` over the 5-minute rows it wrote, in Wh if the
    effective unit is W and in kWh if kW. A row counts if it is at or after the short-term
    cutoff and before ``settled_before``: a slot left to the recorder is not written
    (ADR-024), so it is no change. An unchanged row adds 0 and the hourly rows do not
    count. An input without an effective series this run is not reported, and neither is
    one in ``delta_skip``. ``None`` if the stored values could not be read: no delta for
    any input, which is not 0.
    """
    if not values_read:
        return None
    deltas: _PvDeltas = {}
    for entity_id in pv_input_ids(entry_options):
        info = per_sensor.get(_effy_entity_id(entity_id))
        if entity_id in delta_skip or info is None:
            continue
        settled: datetime | None = info.get("settled_before")
        written = [
            (ts, value)
            for ts, value in info["slot_values"]
            if (short_term_cutoff is None or ts >= short_term_cutoff)
            and (settled is None or ts < settled)
        ]
        stored: dict[datetime, float] = info.get("stored_short", {})
        deltas[entity_id] = (
            pv_delta(written, stored, SLOT_MINUTES),
            "Wh" if info["unit"] == "W" else "kWh",
        )
    return deltas


def _count_hourly_left(all_series: tuple[dict[str, dict[str, Any]], ...], settled: datetime) -> int:
    """Hourly rows a history run computes but leaves to the slot job (TASK-0035).

    One per statistic and hour that is not settled: the hour has a slot the recorder has
    not surely compiled, so its roll-up may still be ahead (``settled_before``).
    """
    return sum(
        1
        for series in all_series
        for info in series.values()
        for hour in _hourly_rows(info["slot_values"])
        if hour + timedelta(hours=1) > settled
    )


def _count_unchanged(all_series: tuple[dict[str, dict[str, Any]], ...]) -> int:
    """Short-term rows the stored rows already had (marked by ``_mark_unchanged``)."""
    return sum(
        len(info.get("unchanged_short", ())) for series in all_series for info in series.values()
    )


def _earliest_change(
    series: dict[str, dict[str, Any]],
    *,
    short_term_cutoff: datetime | None,
    include_long_term: bool,
) -> datetime | None:
    """The earliest slot of ``series`` whose stored row is written (ADR-012 Amendment 2026-10-25).

    A short-term slot at or after the cutoff that is not marked unchanged, or, for a
    changed hourly row, the first slot of that hour in the series (only for a run
    that writes hourly rows: ``include_long_term``). ``None`` if nothing is written.
    """
    earliest: datetime | None = None
    for info in series.values():
        slot_values: list[tuple[datetime, float]] = info["slot_values"]
        unchanged_short: frozenset[datetime] = info.get("unchanged_short", frozenset())
        unchanged_hourly: frozenset[datetime] = info.get("unchanged_hourly", frozenset())
        settled: datetime | None = info.get("settled_before")
        seen_hours: set[datetime] = set()
        for ts, _val in slot_values:  # ascending
            hour = ts.replace(minute=0, second=0, microsecond=0)
            written = (
                (short_term_cutoff is None or ts >= short_term_cutoff)
                and ts not in unchanged_short
                and (settled is None or ts < settled)  # a slot left to the recorder is no change
            )
            first_of_hour = hour not in seen_hours
            seen_hours.add(hour)
            hour_written = (
                include_long_term
                and first_of_hour
                and hour not in unchanged_hourly
                and (settled is None or hour + timedelta(hours=1) <= settled)
            )
            if written or hour_written:
                if earliest is None or ts < earliest:
                    earliest = ts
                break
    return earliest


async def _write_recorder_statistics(
    hass: HomeAssistant,
    per_sensor: dict[str, dict[str, Any]],
    short_term_cutoff: datetime | None = None,
    include_long_term: bool = True,
    include_short_term: bool = True,
) -> int:
    """Write 5-min (+ optionally hourly) statistics into the recorder DB.

    *** INTERNAL RECORDER API – see module docstring WARNING above ***

    Runs directly on the EVENT LOOP (not an executor thread). For each
    sensor in ``per_sensor``, calls the recorder instance's public
    ``async_import_statistics(metadata, stats, table)`` @callback once for
    the short-term (5-minute) table and — if ``include_long_term`` — once
    for the long-term (hourly) table. This schedules internal
    ``ImportStatisticsTask`` jobs on the recorder's own task queue, which
    handles metadata resolution/creation and per-timestamp update-or-insert
    itself (ADR-004 overwrite semantics), exactly as it does for HA's
    built-in `async_add_external_statistics` calls – we are simply passing
    a different `table` argument (`StatisticsShortTerm`) than that public
    wrapper allows.

    ``short_term_cutoff``: if given, only slot values at or after this
    timestamp are written short-term (matches the recorder's own purge
    retention — see ``_get_short_term_retention_days``). If ``None``, all
    given slot values are written unfiltered — the single-slot caller
    (``async_recalculate_slot``) always operates on a slot recent enough
    that a cutoff would never exclude it, so it doesn't bother computing one.

    ``include_long_term``: the hourly aggregate's ``mean`` is the average of
    *every* slot_value in that hour (see below) — this is only correct when
    ``per_sensor`` actually contains every slot of the hour, i.e. for a
    multi-slot range like ``async_recalculate_history``'s. A single-slot
    call (``async_recalculate_slot``, ADR-011) must pass
    ``include_long_term=False``: writing it from just one slot would
    overwrite the correct multi-slot hourly mean with that one slot's value,
    silently degrading long-term data every time it runs.

    ``include_short_term=False`` writes only the hourly rows (the slot job's
    hourly rewrite, ``async_rewrite_hourly``, whose slot values were just read
    back from the short-term table); it then returns 0.

    Every row carries exactly ``start``, ``mean`` and ``state``, never
    ``min``/``max``/``sum``: the recorder's update of an existing row replaces
    every column, so an update of a row the recorder inserted natively leaves
    ``min``/``max`` NULL (TASK-0007a).

    Because ``async_import_statistics`` only schedules the work
    (fire-and-forget), this function awaits
    ``instance.async_block_till_done()`` once at the end so every queued
    task has actually been processed by the recorder thread before we
    return a row count to the caller.

    Returns the number of short-term (5-minute) rows scheduled for write.
    """
    phases = _active_timings.get()  # TASK-0010 patch 1; the disabled one outside a history run
    export_record = _active_export.get()  # TASK-0037; ``None`` unless this run is exported
    timed = phases.detail
    began = _clock() if timed else 0.0
    instance = get_recorder(hass)
    short_term_written = 0
    hourly_written = 0

    for statistic_id, info in per_sensor.items():
        unit = info["unit"]
        slot_values: list[tuple[datetime, float]] = info["slot_values"]
        # Rows already stored exactly as they would be written (TASK-0030); absent
        # unless the caller read the stored rows.
        unchanged_short: frozenset[datetime] = info.get("unchanged_short", frozenset())
        unchanged_hourly: frozenset[datetime] = info.get("unchanged_hourly", frozenset())
        # Slots from here on, and the hours that contain one, are left to the recorder
        # (TASK-0035); absent for the slot job's own writes, whose slots it has compiled.
        settled: datetime | None = info.get("settled_before")

        metadata = _build_statistic_metadata(statistic_id, unit)

        # ---- Short-term (5-minute) – ADR-003 requirement ----
        # state == mean here: there is exactly one effective reading per
        # 5-minute slot, so the "last/only value in the interval" that
        # `state` represents is the same number as the interval mean.
        short_term_slots = (
            [(ts, val) for ts, val in slot_values if ts >= short_term_cutoff]
            if short_term_cutoff is not None
            else slot_values
        )
        short_term_stats: list[StatisticData] = [
            {"start": ts, "mean": val, "state": val}
            for ts, val in short_term_slots
            if ts not in unchanged_short and (settled is None or ts < settled)
        ]
        if include_short_term and short_term_stats:
            instance.async_import_statistics(metadata, short_term_stats, StatisticsShortTerm)
            short_term_written += len(short_term_stats)
            if export_record is not None:
                export_record.note_written(statistic_id, unit, "5minute", short_term_stats)

        # ---- Long-term (hourly) – persists beyond the 10-day purge ----
        # `state` is the chronologically last 5-minute value within the
        # hour (the closest analogue to "raw state at end of period"),
        # distinct from `mean`, which averages all slots in that hour.
        # `slot_values` is iterated in ascending slot order (see `slots`
        # above), so the last-appended value per hour is the last one.
        # See the include_long_term docstring above for why this branch
        # must not run for a single-slot call.
        if not include_long_term:
            continue

        long_term_stats: list[StatisticData] = [
            {"start": hour_ts, "mean": mean, "state": state}
            for hour_ts, (mean, state) in _hourly_rows(slot_values, info.get("weights")).items()
            if hour_ts not in unchanged_hourly
            and (settled is None or hour_ts + timedelta(hours=1) <= settled)
        ]
        if long_term_stats:
            instance.async_import_statistics(metadata, long_term_stats, Statistics)
            hourly_written += len(long_term_stats)
            if export_record is not None:
                export_record.note_written(statistic_id, unit, "hour", long_term_stats)

    # All async_import_statistics calls above only *scheduled* tasks on the
    # recorder's queue – wait for the recorder thread to actually process
    # them before reporting a row count back to the caller.
    waited = _clock() if timed else 0.0
    phases.before_await()
    await instance.async_block_till_done()
    phases.after_await()
    if timed:
        phases.note_write(short_term_written, hourly_written, waited - began, _clock() - waited)

    return short_term_written
