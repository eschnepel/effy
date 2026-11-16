"""The Effy sensors as other integrations see them (ADR-021 as reworked by ADR-029).

Pure: imports only ``effy_exports`` (the contract's symbols), no Home Assistant, so the
contract is testable without mocks. Every Effy sensor describes itself with one
``EffyDescriptor``: the attributes a reader picks by (``series``, ``quantity``, ``scope``,
``span``, ``role``, ``direction``, ``function``, ``aggregations``) and, for the grouping
only, the sensor's ``entity_id`` and the configured ``source_entity_id`` it depends on. The
recalculated-from sensor publishes the registered descriptors as **groups** (one per
distinct combination of the attributes, with the sorted entity IDs of its sensors) and as
the map ``sources`` (a source sensor, the Effy sensors that depend on it), beside
``schema_version`` and ``max_history_days``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypedDict

from .effy_exports import (
    EFFY_AGGREGATIONS,
    EFFY_ATTR_MAX_HISTORY_DAYS,
    EFFY_ATTR_SCHEMA_VERSION,
    EFFY_ATTR_SENSORS,
    EFFY_ATTR_SOURCES,
    EFFY_DIRECTION_BOTH,
    EFFY_DIRECTION_INPUT,
    EFFY_DIRECTION_OUTPUT,
    EFFY_DIRECTION_UNKNOWN,
    EFFY_EFFICIENCY_AGGREGATIONS,
    EFFY_ENERGY_AGGREGATIONS,
    EFFY_FUNCTION_CHARGE,
    EFFY_FUNCTION_CONSUMPTION,
    EFFY_FUNCTION_DISCHARGE,
    EFFY_FUNCTION_EFFICIENCY,
    EFFY_FUNCTION_EPS,
    EFFY_FUNCTION_GRID_EXPORT,
    EFFY_FUNCTION_GRID_IMPORT,
    EFFY_FUNCTION_HOUSEHOLD_EXPORT,
    EFFY_FUNCTION_HOUSEHOLD_IMPORT,
    EFFY_FUNCTION_PV,
    EFFY_FUNCTION_SHARE,
    EFFY_FUNCTION_STANDBY,
    EFFY_FUNCTION_UNKNOWN,
    EFFY_KEY_AGGREGATIONS,
    EFFY_KEY_DIRECTION,
    EFFY_KEY_FUNCTION,
    EFFY_KEY_QUANTITY,
    EFFY_KEY_ROLE,
    EFFY_KEY_SCOPE,
    EFFY_KEY_SENSORS,
    EFFY_KEY_SERIES,
    EFFY_KEY_SPAN,
    EFFY_PUBLISHED_SCHEMA_VERSION,
    EFFY_QUANTITY_EFFICIENCY,
    EFFY_QUANTITY_ENERGY,
    EFFY_QUANTITY_POWER,
    EFFY_QUANTITY_SHARE,
    EFFY_ROLE_BMS,
    EFFY_ROLE_EPS,
    EFFY_ROLE_HOUSEHOLD,
    EFFY_ROLE_OTHER,
    EFFY_ROLE_PV,
    EFFY_SCOPE_COMBINED,
    EFFY_SCOPE_SOURCE,
    EFFY_SCOPE_SYSTEM,
    EFFY_SERIES_CONSUMPTION,
    EFFY_SERIES_EFFECTIVE,
    EFFY_SERIES_PV,
    EFFY_SERIES_SMOOTHED,
    EFFY_SERIES_STANDBY,
    EFFY_SPAN_SLOT,
    EFFY_SPAN_TODAY,
    EFFY_SPAN_TOTAL,
    EFFY_STANDBY_AGGREGATIONS,
    EffyDirection,
    EffyFunction,
    EffyPublishedGroup,
    EffyQuantity,
    EffyRole,
    EffyScope,
    EffySeries,
    EffySpan,
)

# The keys that make two sensors one group, in the order groups are sorted by.
_GROUP_KEYS = (
    EFFY_KEY_SERIES,
    EFFY_KEY_QUANTITY,
    EFFY_KEY_SCOPE,
    EFFY_KEY_SPAN,
    EFFY_KEY_ROLE,
    EFFY_KEY_DIRECTION,
    EFFY_KEY_FUNCTION,
    EFFY_KEY_AGGREGATIONS,
)


class EffyDescriptor(TypedDict):
    """What an Effy sensor registers: the group attributes plus its place in the lists.

    Internal to Effy (the contract file has no such shape): ``entity_id`` and
    ``source_entity_id`` are what the published groups and the ``sources`` map are built
    from, and neither is a key of a published group.
    """

    entity_id: str
    # The configured sensor the Effy sensor depends on; ``None`` for a sensor of scope
    # ``combined`` or ``system`` (they are in no ``sources`` list).
    source_entity_id: str | None
    series: EffySeries
    quantity: EffyQuantity
    scope: EffyScope
    span: EffySpan
    role: EffyRole
    direction: EffyDirection
    function: EffyFunction
    aggregations: list[str]


def _descriptor(
    entity_id: str,
    *,
    source_entity_id: str | None,
    series: EffySeries,
    quantity: EffyQuantity,
    scope: EffyScope,
    span: EffySpan,
    role: EffyRole,
    direction: EffyDirection,
    function: EffyFunction,
    aggregations: Sequence[str],
) -> EffyDescriptor:
    return {
        "entity_id": entity_id,
        "source_entity_id": source_entity_id,
        "series": series,
        "quantity": quantity,
        "scope": scope,
        "span": span,
        "role": role,
        "direction": direction,
        "function": function,
        "aggregations": list(aggregations),
    }


def describe_source_series(
    entity_id: str,
    series: EffySeries,
    source_entity_id: str,
    *,
    pv_input_ids: Sequence[str],
    grid_import_id: str | None,
    discharge_id: str | None,
    charge_id: str | None,
    grid_export_id: str | None,
    eps_id: str | None = None,
    additional_ids: Sequence[str] = (),
    household_import_id: str | None = None,
    household_export_id: str | None = None,
    household_import_signed: bool = False,
) -> EffyDescriptor:
    """Describe an Effy sensor that is a series of one configured source sensor.

    Quantity ``power``, scope ``source``, span ``slot``. Direction, role and function
    follow the source's role: a PV input is ``input``/``pv`` with function ``pv``; the grid
    import and discharge counters are ``input``/``bms``, the charge and grid export counters
    ``output``/``bms`` (every output but the EPS output is a BMS reading, ADR-021), each with
    its own function (ADR-023 decision 11). The EPS sensor (ADR-026) has role and function
    ``eps``; its ``effective`` series, the effective value of the input half, is an
    ``input``, every other series of it (the ``smoothed`` one) is the signed value and has
    direction ``both``. A household meter (ADR-029) has role ``household``: the import meter
    is an ``input`` with function ``household_import``, signed (direction ``both``) exactly
    when ``household_import_signed`` and the series is the smoothed one; the export meter is
    an ``output`` with function ``household_export``. An additional sensor (ADR-028) is role
    ``other``, direction ``unknown`` and function ``unknown``: Effy does not know what it
    stands for. A source that is not configured is a programming error: the entities are
    only ever built from the same options.
    """
    direction: EffyDirection
    role: EffyRole
    function: EffyFunction
    if source_entity_id in pv_input_ids:
        direction, role, function = EFFY_DIRECTION_INPUT, EFFY_ROLE_PV, EFFY_FUNCTION_PV
    elif source_entity_id == grid_import_id:
        direction, role, function = EFFY_DIRECTION_INPUT, EFFY_ROLE_BMS, EFFY_FUNCTION_GRID_IMPORT
    elif source_entity_id == discharge_id:
        direction, role, function = EFFY_DIRECTION_INPUT, EFFY_ROLE_BMS, EFFY_FUNCTION_DISCHARGE
    elif source_entity_id == charge_id:
        direction, role, function = EFFY_DIRECTION_OUTPUT, EFFY_ROLE_BMS, EFFY_FUNCTION_CHARGE
    elif source_entity_id == grid_export_id:
        direction, role, function = EFFY_DIRECTION_OUTPUT, EFFY_ROLE_BMS, EFFY_FUNCTION_GRID_EXPORT
    elif source_entity_id == eps_id:
        direction = EFFY_DIRECTION_INPUT if series == EFFY_SERIES_EFFECTIVE else EFFY_DIRECTION_BOTH
        role, function = EFFY_ROLE_EPS, EFFY_FUNCTION_EPS
    elif source_entity_id == household_import_id:
        signed = household_import_signed and series == EFFY_SERIES_SMOOTHED
        direction = EFFY_DIRECTION_BOTH if signed else EFFY_DIRECTION_INPUT
        role, function = EFFY_ROLE_HOUSEHOLD, EFFY_FUNCTION_HOUSEHOLD_IMPORT
    elif source_entity_id == household_export_id:
        direction, role = EFFY_DIRECTION_OUTPUT, EFFY_ROLE_HOUSEHOLD
        function = EFFY_FUNCTION_HOUSEHOLD_EXPORT
    elif source_entity_id in additional_ids:
        direction, role, function = EFFY_DIRECTION_UNKNOWN, EFFY_ROLE_OTHER, EFFY_FUNCTION_UNKNOWN
    else:
        raise ValueError(f"{source_entity_id} is not a configured sensor")
    return _descriptor(
        entity_id,
        source_entity_id=source_entity_id,
        series=series,
        quantity=EFFY_QUANTITY_POWER,
        scope=EFFY_SCOPE_SOURCE,
        span=EFFY_SPAN_SLOT,
        role=role,
        direction=direction,
        function=function,
        aggregations=EFFY_AGGREGATIONS,
    )


def describe_standby(entity_id: str) -> EffyDescriptor:
    """Describe the standby power sensor: an output of the system, with no single source."""
    return _descriptor(
        entity_id,
        source_entity_id=None,
        series=EFFY_SERIES_STANDBY,
        quantity=EFFY_QUANTITY_POWER,
        scope=EFFY_SCOPE_SYSTEM,
        span=EFFY_SPAN_SLOT,
        role=EFFY_ROLE_BMS,
        direction=EFFY_DIRECTION_OUTPUT,
        function=EFFY_FUNCTION_STANDBY,
        aggregations=EFFY_STANDBY_AGGREGATIONS,
    )


def describe_efficiency(entity_id: str, series: EffySeries) -> EffyDescriptor:
    """Describe an efficiency sensor: an output of the system, with no single source (ADR-023).

    ``series`` is ``charge``, ``discharge``, ``battery`` or ``roundtrip``.
    """
    return _descriptor(
        entity_id,
        source_entity_id=None,
        series=series,
        quantity=EFFY_QUANTITY_EFFICIENCY,
        scope=EFFY_SCOPE_SYSTEM,
        span=EFFY_SPAN_SLOT,
        role=EFFY_ROLE_BMS,
        direction=EFFY_DIRECTION_OUTPUT,
        function=EFFY_FUNCTION_EFFICIENCY,
        aggregations=EFFY_EFFICIENCY_AGGREGATIONS,
    )


def describe_pv_sum(entity_id: str) -> EffyDescriptor:
    """Describe Effective PV Sum now: the effective values of all PV inputs added up."""
    return _descriptor(
        entity_id,
        source_entity_id=None,
        series=EFFY_SERIES_EFFECTIVE,
        quantity=EFFY_QUANTITY_POWER,
        scope=EFFY_SCOPE_COMBINED,
        span=EFFY_SPAN_SLOT,
        role=EFFY_ROLE_PV,
        direction=EFFY_DIRECTION_INPUT,
        function=EFFY_FUNCTION_PV,
        aggregations=EFFY_AGGREGATIONS,
    )


def describe_pv_energy(
    entity_id: str, span: EffySpan, source_entity_id: str | None = None
) -> EffyDescriptor:
    """Describe a PV energy sensor (ADR-027).

    With ``source_entity_id`` it is the energy of one PV input (scope ``source``, span
    ``today``, listed under that input in ``sources``); without it the energy of all PV
    (scope ``combined``, span ``today`` or ``total``).
    """
    per_input = source_entity_id is not None
    if per_input and span != EFFY_SPAN_TODAY:
        raise ValueError("a PV input has a today energy sensor only")
    if span not in (EFFY_SPAN_TODAY, EFFY_SPAN_TOTAL):
        raise ValueError(f"a PV energy sensor has span today or total, not {span}")
    return _descriptor(
        entity_id,
        source_entity_id=source_entity_id,
        series=EFFY_SERIES_EFFECTIVE,
        quantity=EFFY_QUANTITY_ENERGY,
        scope=EFFY_SCOPE_SOURCE if per_input else EFFY_SCOPE_COMBINED,
        span=span,
        role=EFFY_ROLE_PV,
        direction=EFFY_DIRECTION_INPUT,
        function=EFFY_FUNCTION_PV,
        aggregations=EFFY_ENERGY_AGGREGATIONS,
    )


def describe_pv_share(entity_id: str) -> EffyDescriptor:
    """Describe PV share now: a share of the whole system's effective inputs (ADR-027)."""
    return _descriptor(
        entity_id,
        source_entity_id=None,
        series=EFFY_SERIES_PV,
        quantity=EFFY_QUANTITY_SHARE,
        scope=EFFY_SCOPE_SYSTEM,
        span=EFFY_SPAN_SLOT,
        role=EFFY_ROLE_PV,
        direction=EFFY_DIRECTION_INPUT,
        function=EFFY_FUNCTION_SHARE,
        aggregations=EFFY_AGGREGATIONS,
    )


def describe_household_consumption(
    entity_id: str, quantity: EffyQuantity, span: EffySpan
) -> EffyDescriptor:
    """Describe a household consumption sensor (ADR-029): ``power`` per slot, or ``energy``
    since midnight (``today``) or in total (``total``). An output of the system."""
    if quantity == EFFY_QUANTITY_POWER:
        if span != EFFY_SPAN_SLOT:
            raise ValueError("the household consumption power has span slot")
        aggregations: Sequence[str] = EFFY_AGGREGATIONS
    elif quantity == EFFY_QUANTITY_ENERGY:
        if span not in (EFFY_SPAN_TODAY, EFFY_SPAN_TOTAL):
            raise ValueError("the household consumption energy has span today or total")
        aggregations = EFFY_ENERGY_AGGREGATIONS
    else:
        raise ValueError(f"the household consumption is power or energy, not {quantity}")
    return _descriptor(
        entity_id,
        source_entity_id=None,
        series=EFFY_SERIES_CONSUMPTION,
        quantity=quantity,
        scope=EFFY_SCOPE_SYSTEM,
        span=span,
        role=EFFY_ROLE_HOUSEHOLD,
        direction=EFFY_DIRECTION_OUTPUT,
        function=EFFY_FUNCTION_CONSUMPTION,
        aggregations=aggregations,
    )


def base_attributes(max_history_days: int) -> dict[str, Any]:
    """The two attributes that are always present, ``sensors`` and ``sources`` not yet known."""
    return {
        EFFY_ATTR_SCHEMA_VERSION: EFFY_PUBLISHED_SCHEMA_VERSION,
        EFFY_ATTR_MAX_HISTORY_DAYS: int(max_history_days),
    }


def group_sensors(descriptors: Iterable[Mapping[str, Any]]) -> list[EffyPublishedGroup]:
    """The published ``sensors``: one group per distinct combination of attributes.

    Groups are sorted by the attribute values in the order of ``_GROUP_KEYS``, the entity
    IDs of a group sorted. Every group is a fresh dict: nothing of a descriptor is shared.
    """
    members: dict[tuple[Any, ...], list[str]] = {}
    for descriptor in descriptors:
        key = tuple(
            tuple(descriptor[k]) if k == EFFY_KEY_AGGREGATIONS else descriptor[k]
            for k in _GROUP_KEYS
        )
        members.setdefault(key, []).append(str(descriptor["entity_id"]))
    groups: list[EffyPublishedGroup] = []
    for key in sorted(members):
        attributes = dict(zip(_GROUP_KEYS, key, strict=True))
        attributes[EFFY_KEY_AGGREGATIONS] = list(attributes[EFFY_KEY_AGGREGATIONS])
        attributes[EFFY_KEY_SENSORS] = sorted(members[key])
        groups.append(attributes)  # type: ignore[arg-type]
    return groups


def source_map(descriptors: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """The published ``sources``: each configured source sensor, the sorted entity IDs of
    the Effy sensors that depend on it. A sensor with no source (scope ``combined`` or
    ``system``) is in no list, and a source with no sensor has no key."""
    dependents: dict[str, list[str]] = {}
    for descriptor in descriptors:
        source = descriptor.get("source_entity_id")
        if source is not None:
            dependents.setdefault(str(source), []).append(str(descriptor["entity_id"]))
    return {source: sorted(dependents[source]) for source in sorted(dependents)}


def published_attributes(
    descriptors: Iterable[Mapping[str, Any]], max_history_days: int
) -> dict[str, Any]:
    """All published attributes: the scalars, the groups ``sensors`` and the map ``sources``."""
    registered = list(descriptors)
    attrs = base_attributes(max_history_days)
    attrs[EFFY_ATTR_SENSORS] = group_sensors(registered)
    attrs[EFFY_ATTR_SOURCES] = source_map(registered)
    return attrs
