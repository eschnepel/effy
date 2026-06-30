"""
History recalculation for Effy.

Uses the Home Assistant statistics API to:
1. Fetch 5-minute statistics for all configured sensors.
2. Apply the same loss-distribution algorithm as the live sensor
   (``distribute_loss`` from calculation.py — see ADR-001).
3. Write back corrected statistics for all effy_* output sensors,
   **overwriting** any existing rows for the same slots (ADR-004).

State-class handling (ADR-003)
-------------------------------
TOTAL_INCREASING  → request ``change`` from the statistics API (HA computes
                    the per-interval delta and handles counter resets).
TOTAL / MEASUREMENT → request ``mean``.

All sources are written back with ``mean`` only, no ``state`` field — the
live cumulative reading needed for ``state`` is not available during
recalculation (ADR-003).

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
caller-supplied past timestamp, and which derives them from real rows in
the `states` table for that exact 5-minute window – not from arbitrary
historical values supplied by an integration.

To honour ADR-003 anyway, this module writes DIRECTLY into the recorder's
internal SQLAlchemy models (`StatisticsShortTerm`, `Statistics`) using the
SAME `statistic_id` the live `sensor.effy_*` entities already use (i.e.
`source="recorder"`, `statistic_id == entity_id` – NOT the external
`domain:object_id` form used by `async_add_external_statistics`). This
deliberately uses *private/internal* recorder internals that:

  - are NOT part of Home Assistant's public, versioned API surface,
  - are NOT guaranteed to keep their signature, table schema, or behaviour
    across HA core releases — a core update can silently break this module,
  - require running on the recorder's own thread/session
    (`instance.get_session()`), because `StatisticsMetaManager` and the
    table managers are explicitly documented in HA core as "not
    thread-safe, must be called from the recorder thread",
  - bypass the unique-constraint-safe upsert helpers HA itself uses
    internally, so this module does its own delete-then-insert to respect
    ADR-004 (overwrite semantics) without violating the
    `(metadata_id, start_ts)` unique index on both tables.

If a future HA core version changes `db_schema.py` (column names, the
`from_stats` classmethod, the short-term table name, or the uniqueness
constraints), this module WILL break and must be updated. This is a
deliberate, documented trade-off: ADR-003 demands 5-minute granularity, and
no public Home Assistant API can deliver that retroactively.

Two recorder tables are populated for every recalculated slot:

  - `statistics_short_term` (5-minute, ADR-003 requirement) – HA purges
    this table after `purge_keep_days` (10 days by default), so only slots
    within that retention window are written here. Writing further back
    would be pointless: HA's own purge task would delete those rows again
    within hours of the next purge cycle.
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
from homeassistant.components.recorder.statistics import get_metadata_with_session
from homeassistant.components.recorder.util import session_scope
from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .calculation import SensorReading, distribute_loss, effective_in_original_unit
from .const import (
    CONF_INPUT_SENSORS,
    CONF_MAX_HISTORY_DAYS,
    CONF_OUTPUT_SENSORS,
    DEFAULT_MAX_HISTORY_DAYS,
)

_LOGGER = logging.getLogger(__name__)

# Type alias for a single statistics row returned by the recorder
StatRow = dict[str, Any]

# HA's default short-term statistics retention (recorder purge_keep_days).
# Slots older than this are skipped for the short-term table since HA would
# purge them again on its own purge cycle anyway; long-term statistics
# still get the full max_history_days window further below.


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


def _stat_field_for(state_class: str | None) -> str:
    """Return the statistics field to read for a given state class (ADR-003)."""
    if state_class == SensorStateClass.TOTAL_INCREASING:
        return "change"
    return "mean"


def _readings_for_slot(
    slot: datetime,
    entity_ids: list[str],
    indexed: dict[str, dict[datetime, StatRow]],
    state_classes: dict[str, str | None],
    units: dict[str, str],
) -> list[SensorReading]:
    """Build SensorReadings for one time slot from pre-indexed statistics.

    The raw statistic value is stored as-is in the original unit; normalization
    to W happens once inside ``distribute_loss`` (ADR-002).
    """
    readings: list[SensorReading] = []
    for eid in entity_ids:
        slot_row: StatRow | None = indexed.get(eid, {}).get(slot)
        if slot_row is None:
            continue
        field = _stat_field_for(state_classes.get(eid))
        raw_val: float | None = slot_row.get(field)
        if raw_val is None:
            continue
        readings.append(SensorReading(entity_id=eid, raw_value=raw_val, original_unit=units[eid]))
    return readings


def _effy_entity_id(source_entity_id: str) -> str:
    """Return the effy_* entity_id for a given input sensor (mirrors sensor.py)."""
    slug = slugify(source_entity_id.split(".")[-1])
    return f"sensor.effy_{slug}"


async def async_recalculate_history(
    hass: HomeAssistant,
    entry_options: dict[str, Any],
) -> int:
    """
    Recalculate and overwrite effy statistics for up to max_history_days.

    Existing statistics for the same statistic_id + timestamp are
    overwritten, not appended to — see ADR-004 for why this is intentional
    (stale rows from a previous sensor configuration must not survive a
    recalculation).

    Returns the number of 5-minute short-term rows written. See the module
    WARNING docstring above for how (and why) this writes to internal
    recorder tables instead of using the public statistics import API.
    """
    input_ids: list[str] = entry_options.get(CONF_INPUT_SENSORS, [])
    output_ids: list[str] = entry_options.get(CONF_OUTPUT_SENSORS, [])
    max_days: int = entry_options.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS)

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=max_days)

    all_ids: list[str] = input_ids + output_ids

    # Gather per-sensor metadata (event loop – states are in memory)
    units: dict[str, str] = {eid: _get_unit(hass, eid) for eid in all_ids}
    state_classes: dict[str, str | None] = {eid: _get_state_class(hass, eid) for eid in all_ids}

    # Fetch statistics – single call requesting both "mean" and "change";
    # each sensor only consumes the field matching its own state class
    # (see _stat_field_for). One call instead of two is simpler (ADR-003).
    recorder = get_recorder(hass)
    raw_stats: dict[str, list[StatRow]] = await recorder.async_add_executor_job(
        _fetch_statistics, hass, all_ids, start, end
    )

    # Build time-indexed lookup: entity_id → {start_dt: row}
    indexed: dict[str, dict[datetime, StatRow]] = {
        eid: {row["start"]: row for row in rows} for eid, rows in raw_stats.items()
    }

    # Union of all slot timestamps, sorted
    slot_set: set[datetime] = set()
    for stat_rows in raw_stats.values():
        for stat_row in stat_rows:
            slot_set.add(stat_row["start"])
    slots: list[datetime] = sorted(slot_set)

    if not slots:
        _LOGGER.warning("Effy history recalc: no statistics found in the requested period.")
        return 0

    # Compute effective values per slot
    # results: entity_id → [(start, effective_value_in_original_unit)]
    results: dict[str, list[tuple[datetime, float]]] = {eid: [] for eid in input_ids}

    for slot in slots:
        input_readings = _readings_for_slot(slot, input_ids, indexed, state_classes, units)
        output_readings = _readings_for_slot(slot, output_ids, indexed, state_classes, units)

        if not input_readings:
            continue

        distribution = distribute_loss(input_readings, output_readings)

        for reading in input_readings:
            eff = effective_in_original_unit(reading.entity_id, distribution, reading.original_unit)
            results[reading.entity_id].append((slot, eff))

    # Write statistics back – only mean, no state (ADR-003).
    # state would require the live cumulative sensor reading which we don't
    # have during history recalculation; mean is sufficient for all HA
    # dashboard and Energy use-cases.
    #
    # Writing happens via _write_recorder_statistics, which uses internal
    # recorder APIs to populate BOTH statistics_short_term (5-min, ADR-003)
    # and statistics (hourly, long-term) – see the module-level WARNING
    # docstring above for why this is necessary and what the risks are.
    per_sensor: dict[str, dict[str, Any]] = {}
    for eid in input_ids:
        if not results[eid]:
            continue
        per_sensor[_effy_entity_id(eid)] = {
            "unit": units[eid],
            "slot_values": results[eid],
        }

    if not per_sensor:
        return 0

    short_term_written: int = await recorder.async_add_executor_job(
        _write_recorder_statistics, hass, per_sensor, start
    )

    _LOGGER.debug(
        "Effy: history recalculation wrote %d short-term slots across %d sensors",
        short_term_written,
        len(per_sensor),
    )
    return short_term_written


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


def _write_recorder_statistics(
    hass: HomeAssistant,
    per_sensor: dict[str, dict[str, Any]],
    short_term_cutoff: datetime,
) -> int:
    """Blocking call: write 5-min + hourly statistics directly into the recorder DB.

    *** INTERNAL RECORDER API – see module docstring WARNING above ***

    Must run on the recorder's executor thread because StatisticsMetaManager
    and the ORM session it uses are explicitly documented in HA core as not
    thread-safe outside of it.

    For each sensor in ``per_sensor``:
      - resolves (or creates) its metadata_id via the recorder's own
        StatisticsMetaManager, using ``source="recorder"`` and
        ``statistic_id == entity_id`` so it lines up with the metadata the
        live sensor already produces through normal HA recorder operation,
      - deletes any existing short-term/long-term rows for the timestamps
        we are about to (re)write (ADR-004 overwrite semantics – the ORM
        does not provide an upsert for these internal tables, and the
        ``(metadata_id, start_ts)`` columns are unique-indexed, so a plain
        insert on a re-run would raise an integrity error),
      - inserts fresh StatisticsShortTerm rows for every 5-minute slot
        within the short-term retention window,
      - inserts fresh Statistics rows for every clock-hour across the full
        recalculation window, using the average of that hour's 5-minute
        effective values.

    Returns the number of short-term (5-minute) rows written.
    """
    instance = get_recorder(hass)
    statistic_ids = set(per_sensor.keys())

    short_term_written = 0

    with session_scope(session=instance.get_session()) as session:
        # Resolve metadata_id for every effy_* entity; create metadata if the
        # sensor has never produced a recorder statistic before (e.g. right
        # after first install, before any live update has run).
        existing_meta = get_metadata_with_session(instance, session, statistic_ids=statistic_ids)

        metadata_ids: dict[str, int] = {}
        for statistic_id, info in per_sensor.items():
            if statistic_id in existing_meta:
                metadata_ids[statistic_id] = existing_meta[statistic_id][0]
                continue

            # No existing metadata (e.g. sensor never updated live yet) –
            # create it now so the recalculation can proceed. mean_type is
            # set the same way the live MEASUREMENT sensor would be tracked.
            new_metadata = StatisticMetaData(
                has_mean=True,
                has_sum=False,
                name=None,
                source="recorder",
                statistic_id=statistic_id,
                unit_of_measurement=info["unit"],
            )
            _, metadata_id = instance.statistics_meta_manager.update_or_add(
                session, new_metadata, existing_meta
            )
            metadata_ids[statistic_id] = metadata_id

        for statistic_id, info in per_sensor.items():
            metadata_id = metadata_ids[statistic_id]
            slot_values: list[tuple[datetime, float]] = info["slot_values"]

            # ---- Short-term (5-minute) – ADR-003 requirement ----
            short_term_slots = [(ts, val) for ts, val in slot_values if ts >= short_term_cutoff]

            if short_term_slots:
                start_ts_list = [ts.timestamp() for ts, _ in short_term_slots]
                session.query(StatisticsShortTerm).filter(
                    StatisticsShortTerm.metadata_id == metadata_id,
                    StatisticsShortTerm.start_ts.in_(start_ts_list),
                ).delete(synchronize_session=False)

                for ts, val in short_term_slots:
                    stat_short: StatisticData = {"start": ts, "mean": val}
                    session.add(StatisticsShortTerm.from_stats(metadata_id, stat_short))
                short_term_written += len(short_term_slots)

            # ---- Long-term (hourly) – persists beyond the 10-day purge ----
            hourly: dict[datetime, list[float]] = {}
            for ts, val in slot_values:
                hour_ts = ts.replace(minute=0, second=0, microsecond=0)
                hourly.setdefault(hour_ts, []).append(val)

            if hourly:
                hour_ts_list = [ts.timestamp() for ts in hourly]
                session.query(Statistics).filter(
                    Statistics.metadata_id == metadata_id,
                    Statistics.start_ts.in_(hour_ts_list),
                ).delete(synchronize_session=False)

                for hour_ts, vals in hourly.items():
                    stat_long: StatisticData = {
                        "start": hour_ts,
                        "mean": sum(vals) / len(vals),
                    }
                    session.add(Statistics.from_stats(metadata_id, stat_long))

    return short_term_written
