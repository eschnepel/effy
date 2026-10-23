"""The Effy sensors as other integrations see them (ADR-021).

Pure: imports only ``effy_exports`` (the contract's symbols), no Home Assistant, so
the contract is testable without mocks. Every Effy sensor describes itself with one
dict (the keys are the published ones, in the order the ADR shows them); the
recalculated-from sensor publishes the registered descriptors, sorted by
``entity_id``, beside ``schema_version`` and ``max_history_days``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .effy_exports import (
    EFFY_AGGREGATIONS,
    EFFY_ATTR_MAX_HISTORY_DAYS,
    EFFY_ATTR_SCHEMA_VERSION,
    EFFY_ATTR_SENSORS,
    EFFY_DIRECTION_INPUT,
    EFFY_DIRECTION_OUTPUT,
    EFFY_KEY_AGGREGATIONS,
    EFFY_KEY_DIRECTION,
    EFFY_KEY_ENTITY_ID,
    EFFY_KEY_ROLE,
    EFFY_KEY_SERIES,
    EFFY_KEY_SOURCE_ENTITY_ID,
    EFFY_PUBLISHED_SCHEMA_VERSION,
    EFFY_ROLE_BMS,
    EFFY_ROLE_PV,
    EFFY_SERIES_STANDBY_POWER,
    EFFY_STANDBY_AGGREGATIONS,
    EffyDirection,
    EffyPublishedSensor,
    EffyRole,
    EffySeries,
)


def describe_source_series(
    entity_id: str,
    series: EffySeries,
    source_entity_id: str,
    *,
    pv_input_ids: Sequence[str],
    bms_input_ids: Sequence[str],
    output_ids: Sequence[str],
) -> EffyPublishedSensor:
    """Describe an Effy sensor that is a series of one configured source sensor.

    Direction and role follow the source: a PV input is ``input``/``pv``, a BMS
    input ``input``/``bms``, an output ``output``/``bms`` (every output is a BMS
    reading, ADR-021). A source that is not configured is a programming error:
    the entities are only ever built from the same options.
    """
    direction: EffyDirection
    role: EffyRole
    if source_entity_id in pv_input_ids:
        direction, role = EFFY_DIRECTION_INPUT, EFFY_ROLE_PV
    elif source_entity_id in bms_input_ids:
        direction, role = EFFY_DIRECTION_INPUT, EFFY_ROLE_BMS
    elif source_entity_id in output_ids:
        direction, role = EFFY_DIRECTION_OUTPUT, EFFY_ROLE_BMS
    else:
        raise ValueError(f"{source_entity_id} is not a configured input or output sensor")
    return {
        EFFY_KEY_ENTITY_ID: entity_id,
        EFFY_KEY_SERIES: series,
        EFFY_KEY_SOURCE_ENTITY_ID: source_entity_id,
        EFFY_KEY_DIRECTION: direction,
        EFFY_KEY_ROLE: role,
        EFFY_KEY_AGGREGATIONS: list(EFFY_AGGREGATIONS),
    }


def describe_standby(entity_id: str) -> EffyPublishedSensor:
    """Describe the standby power sensor: an output with no single source."""
    return {
        EFFY_KEY_ENTITY_ID: entity_id,
        EFFY_KEY_SERIES: EFFY_SERIES_STANDBY_POWER,
        EFFY_KEY_SOURCE_ENTITY_ID: None,
        EFFY_KEY_DIRECTION: EFFY_DIRECTION_OUTPUT,
        EFFY_KEY_ROLE: EFFY_ROLE_BMS,
        EFFY_KEY_AGGREGATIONS: list(EFFY_STANDBY_AGGREGATIONS),
    }


def base_attributes(max_history_days: int) -> dict[str, Any]:
    """The two attributes that are always present, ``sensors`` not yet known."""
    return {
        EFFY_ATTR_SCHEMA_VERSION: EFFY_PUBLISHED_SCHEMA_VERSION,
        EFFY_ATTR_MAX_HISTORY_DAYS: int(max_history_days),
    }


def published_attributes(
    descriptors: Iterable[Mapping[str, Any]], max_history_days: int
) -> dict[str, Any]:
    """All published attributes: the scalars and the sorted ``sensors`` list."""
    attrs = base_attributes(max_history_days)
    attrs[EFFY_ATTR_SENSORS] = sorted(
        (dict(d) for d in descriptors), key=lambda d: str(d[EFFY_KEY_ENTITY_ID])
    )
    return attrs
