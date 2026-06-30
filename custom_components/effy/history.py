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
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.recorder import get_instance as get_recorder
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .calculation import SensorReading, distribute_loss, effective_in_original_unit
from .const import (
    CONF_INPUT_SENSORS,
    CONF_MAX_HISTORY_DAYS,
    CONF_OUTPUT_SENSORS,
    DEFAULT_MAX_HISTORY_DAYS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Type alias for a single statistics row returned by the recorder
StatRow = dict[str, Any]


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

    Returns the number of 5-min slots written.
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
    # dashboard and Energy use-cases. async_add_external_statistics
    # overwrites existing rows for the same statistic_id + timestamp (ADR-004).
    total_written = 0
    for eid in input_ids:
        slug = slugify(eid.split(".")[-1])
        stat_id = f"{DOMAIN}:effy_{slug}"
        unit = units[eid]

        metadata = StatisticMetaData(
            has_mean=True,
            has_sum=False,
            name=f"effy_{slug}",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_of_measurement=unit,
        )

        stat_data: list[StatisticData] = [
            StatisticData(start=ts, mean=val) for ts, val in results[eid]
        ]

        if stat_data:
            async_add_external_statistics(hass, metadata, stat_data)
            total_written += len(stat_data)
            _LOGGER.debug("Effy: wrote %d stat slots for %s", len(stat_data), stat_id)

    return total_written


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
    # Normalize to tz-aware datetime so downstream code (and
    # async_add_external_statistics) always gets a real datetime object
    # instead of crashing on `.tzinfo`.
    for rows in result.values():
        for row in rows:
            start_val = row.get("start")
            if isinstance(start_val, (int, float)):
                row["start"] = datetime.fromtimestamp(start_val, tz=timezone.utc)

    return result
