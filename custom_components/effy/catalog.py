"""The Effy sensors as other integrations see them (ADR-021).

Pure: imports only ``const``, no Home Assistant, so the contract is testable
without mocks. Every Effy sensor describes itself with one dict (the keys are
the published ones, in the order the ADR shows them); the recalculated-from
sensor publishes the registered descriptors, sorted by ``entity_id``, beside
``schema_version`` and ``max_history_days``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .const import EFFY_AGGREGATIONS, PUBLISHED_SCHEMA_VERSION, STANDBY_AGGREGATIONS

SERIES_EFFECTIVE = "effective"
SERIES_DERIVED_POWER = "derived_power"
SERIES_SMOOTHED = "smoothed"
SERIES_STANDBY_POWER = "standby_power"

DIRECTION_INPUT = "input"
DIRECTION_OUTPUT = "output"

ROLE_PV = "pv"
ROLE_BMS = "bms"


def describe_source_series(
    entity_id: str,
    series: str,
    source_entity_id: str,
    *,
    pv_input_ids: Sequence[str],
    bms_input_ids: Sequence[str],
    output_ids: Sequence[str],
) -> dict[str, Any]:
    """Describe an Effy sensor that is a series of one configured source sensor.

    Direction and role follow the source: a PV input is ``input``/``pv``, a BMS
    input ``input``/``bms``, an output ``output``/``bms`` (every output is a BMS
    reading, ADR-021). A source that is not configured is a programming error:
    the entities are only ever built from the same options.
    """
    if source_entity_id in pv_input_ids:
        direction, role = DIRECTION_INPUT, ROLE_PV
    elif source_entity_id in bms_input_ids:
        direction, role = DIRECTION_INPUT, ROLE_BMS
    elif source_entity_id in output_ids:
        direction, role = DIRECTION_OUTPUT, ROLE_BMS
    else:
        raise ValueError(f"{source_entity_id} is not a configured input or output sensor")
    return {
        "entity_id": entity_id,
        "series": series,
        "source_entity_id": source_entity_id,
        "direction": direction,
        "role": role,
        "aggregations": list(EFFY_AGGREGATIONS),
    }


def describe_standby(entity_id: str) -> dict[str, Any]:
    """Describe the standby power sensor: an output with no single source."""
    return {
        "entity_id": entity_id,
        "series": SERIES_STANDBY_POWER,
        "source_entity_id": None,
        "direction": DIRECTION_OUTPUT,
        "role": ROLE_BMS,
        "aggregations": list(STANDBY_AGGREGATIONS),
    }


def base_attributes(max_history_days: int) -> dict[str, Any]:
    """The two attributes that are always present, ``sensors`` not yet known."""
    return {
        "schema_version": PUBLISHED_SCHEMA_VERSION,
        "max_history_days": int(max_history_days),
    }


def published_attributes(
    descriptors: Iterable[Mapping[str, Any]], max_history_days: int
) -> dict[str, Any]:
    """All published attributes: the scalars and the sorted ``sensors`` list."""
    attrs = base_attributes(max_history_days)
    attrs["sensors"] = sorted((dict(d) for d in descriptors), key=lambda d: str(d["entity_id"]))
    return attrs
