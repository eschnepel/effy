"""Effy's published contract as symbols (ADR-021).

**Copy this file verbatim** into another project to read what Effy publishes
without depending on Effy: no import, no package requirement, no version pin.
Effy itself imports the very same file, so the symbols below are the ones Effy
publishes with; there is no second copy inside Effy that could differ.

Rules for this file (checked by ``tests/test_effy_exports.py``):

- Standard library only (``typing``), and no import from the Effy package. It must
  also work as a top-level module of any project, so it never uses a relative
  import. Python 3.11 or newer (``NotRequired``).
- Every symbol is prefixed ``EFFY_`` (constants) or ``Effy`` (types, as ADR-000 §5
  names Effy's classes), so a copy cannot collide with a project's own names.
- No ``from __future__ import annotations`` (the one exception to ADR-000 §4): a
  ``TypedDict`` reads ``NotRequired`` at runtime, and stringified annotations hide it
  from ``__required_keys__``.
- Symbols only: constants and types, no behaviour, no Home Assistant.
- A change that breaks an existing key or its meaning raises
  ``EFFY_PUBLISHED_SCHEMA_VERSION`` (ADR-021 decision 1); an additive key does not.
  A reader therefore ignores keys it does not know, and compares the published
  ``schema_version`` with the value in its own copy of this file: a larger
  published value means the copy is stale.

What is published: ``sensor.effy_recalculated_from`` carries ``schema_version``,
``max_history_days`` and ``sensors``, one dict per Effy sensor that exists. After
a reboot the sensor is restored with its previous attributes plus
``restored_as_of`` and ``restored_at`` until the next recalculation.
"""

from typing import Final, Literal, NotRequired, TypedDict

# --- Version ------------------------------------------------------------------

# Raised only for a change that breaks existing keys or meanings (ADR-021).
EFFY_PUBLISHED_SCHEMA_VERSION: Final = 1

# --- Entities with a fixed entity ID --------------------------------------------

# The sensor whose state is "a recalculation finished" and whose attributes
# publish the configuration. Its fixed entity ID is the entry point of a reader.
EFFY_RECALCULATED_FROM_ENTITY_ID: Final = "sensor.effy_recalculated_from"
# The standby power estimate (ADR-018); also listed in ``sensors``.
EFFY_STANDBY_POWER_ENTITY_ID: Final = "sensor.effy_standby_power"
# The charge and discharge efficiency estimates (ADR-023); also listed in ``sensors``.
EFFY_CHARGE_EFFICIENCY_ENTITY_ID: Final = "sensor.effy_charge_efficiency"
EFFY_DISCHARGE_EFFICIENCY_ENTITY_ID: Final = "sensor.effy_discharge_efficiency"

# --- Attributes of the recalculated-from sensor ---------------------------------

EFFY_ATTR_SCHEMA_VERSION: Final = "schema_version"
EFFY_ATTR_MAX_HISTORY_DAYS: Final = "max_history_days"
EFFY_ATTR_SENSORS: Final = "sensors"
# Present only while the state is a restored one (until the next recalculation).
EFFY_ATTR_RESTORED_AS_OF: Final = "restored_as_of"
EFFY_ATTR_RESTORED_AT: Final = "restored_at"

# --- Keys of one entry of ``sensors`` -------------------------------------------

EFFY_KEY_ENTITY_ID: Final = "entity_id"
EFFY_KEY_SERIES: Final = "series"
EFFY_KEY_SOURCE_ENTITY_ID: Final = "source_entity_id"
EFFY_KEY_DIRECTION: Final = "direction"
EFFY_KEY_ROLE: Final = "role"
EFFY_KEY_FUNCTION: Final = "function"
EFFY_KEY_AGGREGATIONS: Final = "aggregations"

# --- Values ---------------------------------------------------------------------

# ``series``: what an Effy sensor stands for.
EFFY_SERIES_EFFECTIVE: Final = "effective"
EFFY_SERIES_DERIVED_POWER: Final = "derived_power"
EFFY_SERIES_SMOOTHED: Final = "smoothed"
EFFY_SERIES_STANDBY_POWER: Final = "standby_power"
EFFY_SERIES_CHARGE_EFFICIENCY: Final = "charge_efficiency"
EFFY_SERIES_DISCHARGE_EFFICIENCY: Final = "discharge_efficiency"

# ``direction``: the direction of the flow the sensor stands for.
EFFY_DIRECTION_INPUT: Final = "input"
EFFY_DIRECTION_OUTPUT: Final = "output"

# ``role``: PV inputs apart from the battery system (ADR-019); every output is ``bms``.
EFFY_ROLE_PV: Final = "pv"
EFFY_ROLE_BMS: Final = "bms"

# ``function``: which counter of the battery system a source series stands for (ADR-023,
# ADR-021 Amendment 2026-10-23). Only the entries of a source series (``effective``,
# ``derived_power``, ``smoothed``) have the key; it is ``None`` for a PV input. The
# standby power entry has none. Pick a BMS counter by this, not by guessing.
EFFY_FUNCTION_GRID_IMPORT: Final = "grid_import"
EFFY_FUNCTION_DISCHARGE: Final = "discharge"
EFFY_FUNCTION_CHARGE: Final = "charge"
EFFY_FUNCTION_GRID_EXPORT: Final = "grid_export"

# ``aggregations``: the ``types`` a reader can pass to ``statistics_during_period``
# for the sensor's short-term statistics, with non-null values.
EFFY_AGGREGATION_MEAN: Final = "mean"
EFFY_AGGREGATION_MIN: Final = "min"
EFFY_AGGREGATION_MAX: Final = "max"
EFFY_AGGREGATION_STATE: Final = "state"

# The series Effy writes carry exactly ``start``, ``mean`` and ``state`` and never
# min/max/sum (ADR-003).
EFFY_AGGREGATIONS: Final = (EFFY_AGGREGATION_MEAN, EFFY_AGGREGATION_STATE)
# The standby power sensor is not written by Effy's statistics writer; its
# short-term statistics are the recorder's own, compiled from a measurement
# sensor (ADR-018, ADR-020).
EFFY_STANDBY_AGGREGATIONS: Final = (
    EFFY_AGGREGATION_MEAN,
    EFFY_AGGREGATION_MIN,
    EFFY_AGGREGATION_MAX,
)

# The efficiency sensors are like the standby sensor in this: not written by Effy's
# statistics writer, the recorder compiles their statistics from a measurement sensor
# (ADR-023 decision 2, ADR-020).
EFFY_EFFICIENCY_AGGREGATIONS: Final = (
    EFFY_AGGREGATION_MEAN,
    EFFY_AGGREGATION_MIN,
    EFFY_AGGREGATION_MAX,
)

EffySeries = Literal[
    "effective",
    "derived_power",
    "smoothed",
    "standby_power",
    "charge_efficiency",
    "discharge_efficiency",
]
EffyDirection = Literal["input", "output"]
EffyRole = Literal["pv", "bms"]
EffyFunction = Literal["grid_import", "discharge", "charge", "grid_export"]

# --- Shapes ---------------------------------------------------------------------


class EffyPublishedSensor(TypedDict):
    """One entry of ``sensors``: the metadata of one Effy sensor."""

    entity_id: str
    series: EffySeries
    # The configured source sensor; ``None`` for the standby power and efficiency
    # sensors, which have no single source.
    source_entity_id: str | None
    direction: EffyDirection
    role: EffyRole
    # A source series only: the function of its source, ``None`` for a PV input; absent
    # on the standby entry and on an entry published before ``function`` existed.
    function: NotRequired[EffyFunction | None]
    aggregations: list[str]


class EffyPublishedAttributes(TypedDict):
    """The attributes of ``sensor.effy_recalculated_from``.

    ``sensors`` is absent until the first recalculation closes after an upgrade; the
    ``restored_*`` markers only while the state was restored.
    """

    schema_version: int
    max_history_days: int
    sensors: NotRequired[list[EffyPublishedSensor]]
    restored_as_of: NotRequired[str]
    restored_at: NotRequired[str]
