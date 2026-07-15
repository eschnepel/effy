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
   delta alike (ADR-013). RECENT_RECALC_WINDOW (the slot-timer-driven
   "recent" recalc's own look-back) is deliberately small for this reason
   — just enough to cover the trapezoidal cap, not sized to also contain
   a whole multi-hour offline gap the way it originally was — and a
   single targeted lookback (``_fetch_last_valid_state_before``) finds the
   real pre-outage baseline on the rare occasions the window itself starts
   mid-outage, regardless of how far back that baseline is.

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
    _parse_energy_state,
    distribute_loss,
    effective_in_original_unit,
    interpolate_slot_gaps,
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
# its *start* visible to trapezoidal_slot_contributions — without this,
# the first few slots in any range could silently under-count. This is
# NOT what makes offline-gap detection work across an arbitrarily long
# outage — that's _fetch_last_valid_state_before's job (ADR-013) — this
# margin only exists to avoid an ordinary boundary artifact at the edge
# of whatever range a caller below chooses to recompute. Not verified
# against a real HA instance — see the module WARNING docstring's general
# caveat on this file's internal-API-adjacent code.
_RAW_HISTORY_BOUNDARY_MARGIN = timedelta(minutes=TRAPEZOID_MAX_MINUTES + 5)

# How far back the slot-timer-driven "recent" recalculation (ADR-011,
# ADR-012) looks for candidate slots to rewrite, every time it runs
# (~every 5 minutes). Deliberately small (ADR-013) — just enough to
# comfortably cover the trapezoidal cap (TRAPEZOID_MAX_MINUTES) plus a
# missed timer tick or two, NOT sized to also cover a multi-hour offline
# gap the way it originally was. That's no longer this window's job:
# _fetch_last_valid_state_before performs a single targeted lookback
# (only when the window's own oldest raw state turns out to be invalid,
# i.e. the window happens to start mid-outage) to find the real
# pre-outage baseline, however far back that is — so an offline gap of
# any length is handled correctly without this window needing to be wide
# enough to contain the whole thing. See ADR-013 for the full reasoning
# (this replaces the original 4-hour value, which existed for that
# now-obsolete reason and made every recalculation's own "touched" range
# — and therefore the recalculated-from sensor — look like it always
# spanned about 4 hours, regardless of how much had actually changed). A
# genuinely long-neglected sensor (e.g. HA itself was down for days) is
# still corrected eventually by the next full history recalc, whose own
# window is much larger (max_history_days) — same fallback as before.
RECENT_RECALC_WINDOW = timedelta(minutes=TRAPEZOID_MAX_MINUTES + 5)

# How far back _fetch_last_valid_state_before is willing to search for a
# pre-outage baseline reading, when RECENT_RECALC_WINDOW's own oldest raw
# state turns out to already be invalid. Deliberately generous (matches
# the full history recalc's own reach) since this lookback is a rare,
# single targeted query — not a per-cycle cost — so there's little reason
# to bound it any tighter than "as far back as a full recalc would look
# anyway".
_OFFLINE_ANCHOR_SEARCH_DAYS = DEFAULT_MAX_HISTORY_DAYS


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
    )
    states = history_by_entity.get(entity_id, [])
    return [(s.last_changed, s.state) for s in states]


def _fetch_last_valid_state_before(
    hass: HomeAssistant,
    entity_id: str,
    before: datetime,
    search_days: int = _OFFLINE_ANCHOR_SEARCH_DAYS,
) -> tuple[datetime, str] | None:
    """Find the most recent *numeric* raw state for entity_id strictly
    before ``before`` (ADR-013).

    Only needed when RECENT_RECALC_WINDOW's own fetch (_fetch_raw_energy_states,
    starting at raw_history_start) happens to *start* mid-outage — i.e. its
    very first entry is already invalid (unavailable/unknown/non-numeric).
    In that case there is no valid reading anywhere in the small window to
    anchor a trapezoidal redistribution against once the sensor comes back
    online, even though the real outage may have started long before the
    window (RECENT_RECALC_WINDOW is now deliberately small, see its own
    docstring — it no longer tries to be wide enough to contain a whole
    offline gap itself). This is a single, targeted, descending query for
    one entity — not a per-cycle cost, only run when that specific pattern
    is detected — so it can afford to search much further back than the
    window itself, all the way to search_days.

    Runs synchronously; callers must invoke this via
    ``recorder.async_add_executor_job``, exactly like ``_fetch_raw_energy_states``.

    Returns (timestamp, raw_state_string) of the found reading, or None if
    no numeric reading exists at all within the search range (a genuinely
    brand-new entity, or one that's been offline longer than search_days —
    in either case, there's nothing to anchor a redistribution to, so the
    transition into the window is simply left without a computed
    contribution, same as any other "fewer than 2 valid readings" case).
    """
    from homeassistant.components.recorder.history import (  # noqa: PLC0415
        state_changes_during_period,
    )

    search_start = before - timedelta(days=search_days)
    history_by_entity = state_changes_during_period(
        hass,
        search_start,
        before,
        entity_id,
        no_attributes=True,
        descending=True,
        include_start_time_state=False,
    )
    for state in history_by_entity.get(entity_id, []):
        if _parse_energy_state(state.state) is not None:
            return (state.last_changed, state.state)
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


async def _compute_effective_slots(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
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
    to a range some *other* call is responsible for). ``now=end`` is
    passed through to trapezoidal_slot_contributions so a sensor that
    simply hasn't reported a new value yet still gets explicit 0 entries
    for the slots since its last reading (ADR-013), and if the fetched raw
    history itself starts mid-outage (its first entry is already invalid),
    a single targeted lookback (_fetch_last_valid_state_before) finds the
    real pre-outage baseline regardless of how far back it is.

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

    # ---- Energy-family sensors: trapezoidal-redistributed raw history (ADR-012, ADR-013) ----
    raw_history_start = start - _RAW_HISTORY_BOUNDARY_MARGIN
    for eid in energy_ids:
        raw_states = await recorder.async_add_executor_job(
            _fetch_raw_energy_states, hass, eid, raw_history_start, end
        )
        # RECENT_RECALC_WINDOW is deliberately small (ADR-013) — if the
        # sensor was already offline when this window's own fetch starts,
        # the window contains no valid baseline to redistribute the
        # eventual return-to-online jump against. Detected cheaply: the
        # chronologically-first fetched entry is itself invalid. This is a
        # single, targeted, one-off lookback — not run when the window
        # already has a valid starting point (the overwhelmingly common
        # case) — see _fetch_last_valid_state_before's docstring.
        if raw_states and _parse_energy_state(raw_states[0][1]) is None:
            anchor = await recorder.async_add_executor_job(
                _fetch_last_valid_state_before, hass, eid, raw_history_start
            )
            if anchor is not None:
                raw_states = [anchor, *raw_states]
        contributions = trapezoidal_slot_contributions(
            raw_states, slot_minutes=SLOT_MINUTES, now=end
        )
        indexed[eid] = {
            slot: {"start": slot, "change": value}
            for slot, value in contributions.items()
            if start <= slot < end
        }

    # ---- Smoothed power-family INPUT sensors: gap interpolation ----
    # Bridges short (<= INTERPOLATION_MAX_GAP_SLOTS) runs of missing slots
    # in a power-family input sensor's `mean` and merges the interpolated
    # values straight into `indexed[eid]` — using setdefault so an actual
    # recorder row for a slot is never overwritten, only genuinely missing
    # slots get a synthetic one. This runs *before* `slots`/distribute_loss
    # below, so the waterfall calculation itself sees the smoothed series.
    # Output sensors are deliberately left untouched (out of scope).
    smoothed_series: dict[str, dict[datetime, float]] = {}
    for eid in input_ids:
        if eid not in power_ids:
            continue
        entity_rows = indexed.setdefault(eid, {})
        raw_means = {
            ts: row["mean"] for ts, row in entity_rows.items() if row.get("mean") is not None
        }
        if not raw_means:
            continue
        filled = interpolate_slot_gaps(raw_means, slot_minutes=SLOT_MINUTES)
        smoothed_series[eid] = filled
        for ts, value in filled.items():
            entity_rows.setdefault(ts, {"start": ts, "mean": value})

    # Union of all slot timestamps actually present, sorted
    slot_set: set[datetime] = set()
    for entity_rows in indexed.values():
        slot_set.update(entity_rows.keys())
    slots: list[datetime] = sorted(slot_set)

    if not slots:
        return {}, {}, {}

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

    per_sensor_smoothed: dict[str, dict[str, Any]] = {}
    for eid, filled in smoothed_series.items():
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

    return per_sensor, per_sensor_power, per_sensor_smoothed


async def async_recalculate_history(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
) -> tuple[int, datetime | None, set[str]]:
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

    Returns (short_term_rows_written, recalculated_from, touched_entity_ids).
    A full recalc unconditionally recomputes every slot in [start, end), so
    recalculated_from is always exactly ``start`` — see
    async_recalculate_recent for the dynamic-range case, and ADR-012 for
    why this value matters (the "recalculated from" sensor).
    touched_entity_ids is every effy_* entity_id that got at least one
    slot written this run, across all three series — callers (button.py)
    push it through EffyCoordinator.notify_updated() so those entities'
    dashboard cards see a state_changed event and refetch, since these
    entities otherwise never get a live push (ADR-011). See the module
    WARNING docstring above for how (and why) this writes to internal
    recorder tables instead of using the public statistics import API.
    """
    max_days: int = entry_options.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS)
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=max_days)
    short_term_retention_days = _get_short_term_retention_days(hass)
    short_term_cutoff = end - timedelta(days=short_term_retention_days)

    per_sensor, per_sensor_power, per_sensor_smoothed = await _compute_effective_slots(
        hass, entry_options, start, end
    )
    if not per_sensor and not per_sensor_power and not per_sensor_smoothed:
        _LOGGER.warning("Effy history recalc: no statistics found in the requested period.")
        return 0, None, set()

    short_term_written = 0
    if per_sensor:
        short_term_written += await _write_recorder_statistics(
            hass, per_sensor, short_term_cutoff=short_term_cutoff, include_long_term=True
        )
    if per_sensor_power:
        short_term_written += await _write_recorder_statistics(
            hass, per_sensor_power, short_term_cutoff=short_term_cutoff, include_long_term=True
        )
    if per_sensor_smoothed:
        short_term_written += await _write_recorder_statistics(
            hass, per_sensor_smoothed, short_term_cutoff=short_term_cutoff, include_long_term=True
        )

    touched_entity_ids: set[str] = (
        set(per_sensor) | set(per_sensor_power) | set(per_sensor_smoothed)
    )

    _LOGGER.debug(
        "Effy: history recalculation wrote %d short-term slots across %d effective + "
        "%d derived-power + %d smoothed sensors",
        short_term_written,
        len(per_sensor),
        len(per_sensor_power),
        len(per_sensor_smoothed),
    )
    return short_term_written, start, touched_entity_ids


async def async_recalculate_recent(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
    now: datetime,
) -> tuple[int, datetime | None, set[str]]:
    """
    Recalculate and write effy statistics for whatever recent slots need it.

    Intended to be called shortly after a slot closes (EffyCoordinator's
    slot timer, ADR-011). Unlike the original fixed single-slot design,
    the exact range recomputed is dynamic within RECENT_RECALC_WINDOW
    (ADR-012): a trapezoidal-redistributed energy jump can touch more than
    one slot — up to 3 for a normal (15-minute-capped) jump — so this
    always recomputes and rewrites every slot touched by *any* sensor
    within RECENT_RECALC_WINDOW of ``now``, not just the single slot that
    most recently closed. RECENT_RECALC_WINDOW is deliberately small
    (ADR-013 — just enough to cover the trapezoidal cap plus a missed
    timer tick or two); a sensor that comes back online from a genuinely
    long offline gap is still handled correctly regardless of the
    window's size, via _compute_effective_slots' single targeted
    _fetch_last_valid_state_before lookback — not by making this window
    itself wide enough to contain the whole gap, the way it originally
    was. A truly long-neglected sensor (further back than
    _OFFLINE_ANCHOR_SEARCH_DAYS, or HA itself down for that long) is still
    corrected eventually by the next full history recalc.

    Deliberately writes ONLY the short-term (5-minute) statistic, never the
    long-term (hourly) one, for the "effective", "derived power", and
    "smoothed" series alike — see ADR-011 Decision 2 (unchanged by
    ADR-012 or the gap-interpolation feature).

    Returns (short_term_rows_written, recalculated_from, touched_entity_ids).
    recalculated_from is the earliest slot timestamp touched by this run
    across all three series, or None if nothing was written this run (e.g.
    the recorder hasn't compiled statistics for the relevant slots yet, or
    no sensor changed) — see ADR-012 for how this feeds the "recalculated
    from" sensor. Thanks to RECENT_RECALC_WINDOW now being small (ADR-013),
    this is normally within a few minutes of ``now``, not always ~4 hours
    back as it was when the window itself was 4 hours wide; it only
    reaches further back than the window when the targeted offline lookback
    above actually fires. touched_entity_ids is every effy_* entity_id
    that got at least one slot written this run — the caller
    (EffyCoordinator's slot timer) pushes it through notify_updated() so
    those entities' dashboard cards see a state_changed event and refetch,
    since these entities otherwise never get a live push (ADR-011).
    """
    start = now - RECENT_RECALC_WINDOW
    per_sensor, per_sensor_power, per_sensor_smoothed = await _compute_effective_slots(
        hass, entry_options, start, now
    )
    if not per_sensor and not per_sensor_power and not per_sensor_smoothed:
        _LOGGER.debug(
            "Effy recent recalc: nothing to recompute in the %s before %s",
            RECENT_RECALC_WINDOW,
            now,
        )
        return 0, None, set()

    written = 0
    earliest: datetime | None = None
    for series in (per_sensor, per_sensor_power, per_sensor_smoothed):
        if not series:
            continue
        written += await _write_recorder_statistics(hass, series, include_long_term=False)
        for info in series.values():
            for slot, _value in info["slot_values"]:
                if earliest is None or slot < earliest:
                    earliest = slot

    touched_entity_ids: set[str] = (
        set(per_sensor) | set(per_sensor_power) | set(per_sensor_smoothed)
    )

    _LOGGER.debug(
        "Effy: recent recalculation wrote %d short-term rows across %d effective + %d "
        "derived-power + %d smoothed sensors, earliest touched slot %s",
        written,
        len(per_sensor),
        len(per_sensor_power),
        len(per_sensor_smoothed),
        earliest,
    )
    return written, earliest, touched_entity_ids


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
