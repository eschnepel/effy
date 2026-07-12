"""
History recalculation for Effy.

Uses a mix of the Home Assistant statistics API and raw state history to:
1. Get a 5-minute-resolution reading for every configured sensor —
   ``statistics_during_period``'s ``mean`` for MEASUREMENT/TOTAL-as-power
   sensors (unchanged), or a trapezoidal-rule redistribution of raw state
   history for TOTAL_INCREASING/TOTAL-as-energy sensors (ADR-012,
   ``calculation.trapezoidal_slot_contributions`` — replaces ADR-009's
   neighbor-steal smoothing).
2. Apply the same loss-distribution algorithm as the live sensor
   (``distribute_loss`` from calculation.py — see ADR-001).
3. Write back corrected statistics for all effy_* output sensors,
   **overwriting** any existing rows for the same slots (ADR-004): the
   post-waterfall "effective" value (input sensors only, as before), and,
   new in ADR-012, the raw pre-waterfall "derived power" for every
   energy-family sensor (inputs and outputs) as a separate `effy_*_power`
   statistic.

State-class handling (ADR-003)
-------------------------------
TOTAL_INCREASING  → energy-family; per-slot value from the trapezoidal
                    rule over raw state history (ADR-012), not the
                    statistics API's ``change`` field anymore.
TOTAL / MEASUREMENT → power-family; request ``mean`` from the statistics
                    API, unchanged.

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
from datetime import datetime, timedelta, timezone
from typing import Any

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

from .sensor_utils import SLOT_MINUTES, effective_unit_for, is_energy_family, to_power_equivalent
from .calculation import (
    SensorReading,
    TRAPEZOID_MAX_MINUTES,
    distribute_loss,
    effective_in_original_unit,
    trapezoidal_slot_contributions,
)
from .const import (
    CONF_INPUT_SENSORS,
    CONF_MAX_HISTORY_DAYS,
    CONF_OUTPUT_SENSORS,
    DEFAULT_MAX_HISTORY_DAYS,
)

_LOGGER = logging.getLogger(__name__)

# Type alias for a single statistics row returned by the recorder
StatRow = dict[str, Any]

# Fallback only used if the recorder instance somehow has no usable
# keep_days value (should not normally happen) – matches HA's own default.
_FALLBACK_SHORT_TERM_RETENTION_DAYS = 10

# Small, fixed extra lookback before any requested [start, end) range, so a
# transition whose *end* falls right at the start of the range still has
# its *start* (and any preceding offline gap immediately before it)
# visible to trapezoidal_slot_contributions — without this, the first few
# slots in any range could silently under-count. Deliberately much smaller
# than, and independent of, how large a candidate window each caller below
# chooses to recompute (max_history_days vs. a few hours) — it only exists
# to avoid a boundary artifact, not to control how far back an offline gap
# can be correctly detected (that's bounded by how far back raw history is
# fetched overall, i.e. by `start` itself). Not verified against a real HA
# instance — see the module WARNING docstring's general caveat on this
# file's internal-API-adjacent code.
_RAW_HISTORY_BOUNDARY_MARGIN = timedelta(minutes=TRAPEZOID_MAX_MINUTES + 5)

# How far back the slot-timer-driven "recent" recalculation (ADR-011,
# ADR-012) looks for candidate slots to rewrite, every time it runs
# (~every 5 minutes). Bounds the query cost of that frequent call; a real
# offline gap longer than this is still corrected eventually by the next
# full history recalc, whose own window is much larger (max_history_days).
RECENT_RECALC_WINDOW = timedelta(hours=4)


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


def _build_statistic_metadata(statistic_id: str, unit: str) -> StatisticMetaData:
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
        "has_mean": True,
        "has_sum": False,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }
    if _HAS_STATISTIC_MEAN_TYPE:
        metadata["mean_type"] = StatisticMeanType.ARITHMETIC
        metadata["unit_class"] = None
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
        significant_changes_only=False,
    )
    states = history_by_entity.get(entity_id, [])
    return [(s.last_changed, s.state) for s in states]


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


async def _compute_effective_slots(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Core computation shared by async_recalculate_history and async_recalculate_recent.

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
    to a range some *other* call is responsible for).

    Returns (per_sensor, per_sensor_power):
      - per_sensor: effy_entity_id → {"unit", "slot_values"} — the
        post-waterfall "effective" value, input sensors only (unchanged
        concept from before ADR-012).
      - per_sensor_power: effy_*_power entity_id → {"unit", "slot_values"}
        — the raw, pre-waterfall trapezoidal power for every energy-family
        sensor, inputs *and* outputs (new in ADR-012) — this is exactly
        the value already computed for distribute_loss's input, so no
        extra computation is needed, just capturing it before the
        waterfall step consumes it.
    Both are ready for _write_recorder_statistics. Nothing is written here.
    """
    input_ids: list[str] = entry_options.get(CONF_INPUT_SENSORS, [])
    output_ids: list[str] = entry_options.get(CONF_OUTPUT_SENSORS, [])
    all_ids: list[str] = input_ids + output_ids

    # Gather per-sensor metadata (event loop – states are in memory)
    live_units: dict[str, str] = {eid: _get_unit(hass, eid) for eid in all_ids}
    state_classes: dict[str, str | None] = {eid: _get_state_class(hass, eid) for eid in all_ids}

    recorder = get_recorder(hass)

    # The unit `change`/`mean` values from statistics_during_period are
    # actually expressed in can differ from the live entity's current unit
    # (see _get_statistics_units) — read it from statistics_meta instead of
    # assuming it matches the live state, to avoid interpreting e.g. an
    # already-Wh value as if it were kWh (or vice versa), which would throw
    # off every downstream Wh/kWh → W conversion by a further factor of 1000.
    stats_units: dict[str, str] = await recorder.async_add_executor_job(
        _get_statistics_units, hass, all_ids
    )
    units: dict[str, str] = {}
    for eid in all_ids:
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
        eid for eid in all_ids if _stat_field_for(state_classes.get(eid), units[eid]) == "change"
    ]
    power_ids = [eid for eid in all_ids if eid not in energy_ids]

    indexed: dict[str, dict[datetime, StatRow]] = {}

    # ---- Power-family sensors: unchanged, statistics `mean` per slot ----
    if power_ids:
        raw_stats: dict[str, list[StatRow]] = await recorder.async_add_executor_job(
            _fetch_statistics, hass, power_ids, start, end
        )
        for eid, rows in raw_stats.items():
            indexed[eid] = {row["start"]: row for row in rows}

    # ---- Energy-family sensors: trapezoidal-redistributed raw history (ADR-012) ----
    raw_history_start = start - _RAW_HISTORY_BOUNDARY_MARGIN
    for eid in energy_ids:
        raw_states = await recorder.async_add_executor_job(
            _fetch_raw_energy_states, hass, eid, raw_history_start, end
        )
        contributions = trapezoidal_slot_contributions(raw_states, slot_minutes=SLOT_MINUTES)
        indexed[eid] = {
            slot: {"start": slot, "change": value}
            for slot, value in contributions.items()
            if start <= slot < end
        }

    # Union of all slot timestamps actually present, sorted
    slot_set: set[datetime] = set()
    for entity_rows in indexed.values():
        slot_set.update(entity_rows.keys())
    slots: list[datetime] = sorted(slot_set)

    if not slots:
        return {}, {}

    # results: effective (post-waterfall) values per slot, input sensors only
    results: dict[str, list[tuple[datetime, float]]] = {eid: [] for eid in input_ids}
    # power_results: raw (pre-waterfall) derived power per slot, energy-family only
    power_results: dict[str, list[tuple[datetime, float]]] = {eid: [] for eid in energy_ids}

    for slot in slots:
        input_readings = _readings_for_slot(
            slot, SLOT_MINUTES, input_ids, indexed, state_classes, units
        )
        output_readings = _readings_for_slot(
            slot, SLOT_MINUTES, output_ids, indexed, state_classes, units
        )

        for reading in input_readings + output_readings:
            if reading.entity_id in power_results:
                power_results[reading.entity_id].append((slot, reading.raw_value))

        if not input_readings:
            continue

        distribution = distribute_loss(input_readings, output_readings)

        for reading in input_readings:
            eff = effective_in_original_unit(reading.entity_id, distribution, reading.original_unit)
            results[reading.entity_id].append((slot, eff))

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

    per_sensor_power: dict[str, dict[str, Any]] = {}
    for eid in energy_ids:
        if not power_results[eid]:
            continue
        per_sensor_power[_effy_power_entity_id(eid)] = {
            "unit": effective_unit_for(units[eid]),
            "slot_values": power_results[eid],
        }

    return per_sensor, per_sensor_power


async def async_recalculate_history(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
) -> tuple[int, datetime | None]:
    """
    Recalculate and overwrite effy statistics for up to max_history_days.

    Existing statistics for the same statistic_id + timestamp are
    overwritten, not appended to — see ADR-004 for why this is intentional
    (stale rows from a previous sensor configuration must not survive a
    recalculation).

    Energy-family sensors' per-slot values are computed via the
    trapezoidal rule (ADR-012, calculation.trapezoidal_slot_contributions)
    applied to raw state history, replacing ADR-009's neighbor-steal
    smoothing. Also writes the raw, pre-loss-distribution derived-power
    statistic (effy_*_power) for every energy-family sensor, input and
    output alike (ADR-012) — new in addition to the existing "effective"
    (post-waterfall, input-only) statistic.

    Writes both short-term (5-minute) and long-term (hourly) statistics for
    both series — see ADR-011 for why the hourly aggregate specifically
    requires a full multi-slot range like this one, and must not be
    written from the slot-timer-driven recent recalculation.

    Returns (short_term_rows_written, recalculated_from). A full recalc
    unconditionally recomputes every slot in [start, end), so
    recalculated_from is always exactly ``start`` — see
    async_recalculate_recent for the dynamic-range case, and ADR-012 for
    why this value matters (the "recalculated from" sensor). See the
    module WARNING docstring above for how (and why) this writes to
    internal recorder tables instead of using the public statistics import
    API.
    """
    max_days: int = entry_options.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS)
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=max_days)
    short_term_retention_days = _get_short_term_retention_days(hass)
    short_term_cutoff = end - timedelta(days=short_term_retention_days)

    per_sensor, per_sensor_power = await _compute_effective_slots(hass, entry_options, start, end)
    if not per_sensor and not per_sensor_power:
        _LOGGER.warning("Effy history recalc: no statistics found in the requested period.")
        return 0, None

    short_term_written = 0
    if per_sensor:
        short_term_written += await _write_recorder_statistics(
            hass, per_sensor, short_term_cutoff=short_term_cutoff, include_long_term=True
        )
    if per_sensor_power:
        short_term_written += await _write_recorder_statistics(
            hass, per_sensor_power, short_term_cutoff=short_term_cutoff, include_long_term=True
        )

    _LOGGER.debug(
        "Effy: history recalculation wrote %d short-term slots across %d effective + "
        "%d derived-power sensors",
        short_term_written,
        len(per_sensor),
        len(per_sensor_power),
    )
    return short_term_written, start


async def async_recalculate_recent(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    now: datetime,
) -> tuple[int, datetime | None]:
    """
    Recalculate and write effy statistics for whatever recent slots need it.

    Intended to be called shortly after a slot closes (EffyCoordinator's
    slot timer, ADR-011). Unlike the original fixed single-slot design,
    the exact range recomputed is now dynamic (ADR-012): a
    trapezoidal-redistributed energy jump can touch more than one slot —
    up to 3 for a normal (15-minute-capped) jump, or more for an offline
    gap — so this always recomputes and rewrites every slot touched by
    *any* sensor within RECENT_RECALC_WINDOW of ``now``, not just the
    single slot that most recently closed. A real offline gap longer than
    RECENT_RECALC_WINDOW is still corrected eventually by the next full
    history recalc.

    Deliberately writes ONLY the short-term (5-minute) statistic, never the
    long-term (hourly) one, for both the "effective" and "derived power"
    series — see ADR-011 Decision 2 (unchanged by ADR-012).

    Returns (short_term_rows_written, recalculated_from), where
    recalculated_from is the earliest slot timestamp touched by this run
    across both series, or None if nothing was written this run (e.g. the
    recorder hasn't compiled statistics for the relevant slots yet, or no
    sensor changed) — see ADR-012 for how this feeds the "recalculated
    from" sensor.
    """
    start = now - RECENT_RECALC_WINDOW
    per_sensor, per_sensor_power = await _compute_effective_slots(hass, entry_options, start, now)
    if not per_sensor and not per_sensor_power:
        _LOGGER.debug(
            "Effy recent recalc: nothing to recompute in the %s before %s",
            RECENT_RECALC_WINDOW,
            now,
        )
        return 0, None

    written = 0
    earliest: datetime | None = None
    for series in (per_sensor, per_sensor_power):
        if not series:
            continue
        written += await _write_recorder_statistics(hass, series, include_long_term=False)
        for info in series.values():
            for slot, _value in info["slot_values"]:
                if earliest is None or slot < earliest:
                    earliest = slot

    _LOGGER.debug(
        "Effy: recent recalculation wrote %d short-term rows across %d effective + %d "
        "derived-power sensors, earliest touched slot %s",
        written,
        len(per_sensor),
        len(per_sensor_power),
        earliest,
    )
    return written, earliest


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


async def _write_recorder_statistics(
    hass: HomeAssistant,
    per_sensor: dict[str, dict[str, Any]],
    short_term_cutoff: datetime | None = None,
    include_long_term: bool = True,
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

    Because ``async_import_statistics`` only schedules the work
    (fire-and-forget), this function awaits
    ``instance.async_block_till_done()`` once at the end so every queued
    task has actually been processed by the recorder thread before we
    return a row count to the caller.

    Returns the number of short-term (5-minute) rows scheduled for write.
    """
    instance = get_recorder(hass)
    short_term_written = 0

    for statistic_id, info in per_sensor.items():
        unit = info["unit"]
        slot_values: list[tuple[datetime, float]] = info["slot_values"]

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
        if short_term_slots:
            short_term_stats: list[StatisticData] = [
                {"start": ts, "mean": val, "state": val} for ts, val in short_term_slots
            ]
            instance.async_import_statistics(metadata, short_term_stats, StatisticsShortTerm)
            short_term_written += len(short_term_stats)

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

        hourly: dict[datetime, list[float]] = {}
        for ts, val in slot_values:
            hour_ts = ts.replace(minute=0, second=0, microsecond=0)
            hourly.setdefault(hour_ts, []).append(val)

        if hourly:
            long_term_stats: list[StatisticData] = [
                {"start": hour_ts, "mean": sum(vals) / len(vals), "state": vals[-1]}
                for hour_ts, vals in hourly.items()
            ]
            instance.async_import_statistics(metadata, long_term_stats, Statistics)

    # All async_import_statistics calls above only *scheduled* tasks on the
    # recorder's queue – wait for the recorder thread to actually process
    # them before reporting a row count back to the caller.
    await instance.async_block_till_done()

    return short_term_written
