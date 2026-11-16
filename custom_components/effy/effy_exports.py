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
  ``EFFY_PUBLISHED_SCHEMA_VERSION`` (ADR-021 decision 1); an additive key or an additive
  value does not. Version 2 (ADR-021 Amendment 2026-11-13, ADR-029) is the typed,
  grouped contract: it broke version 1, which nobody read yet.
  A reader therefore ignores keys it does not know, and compares the published
  ``schema_version`` with the value in its own copy of this file: a larger
  published value means the copy is stale.

What is published: ``sensor.effy_recalculated_from`` carries ``schema_version``,
``max_history_days``, ``sensors`` and ``sources``. ``sensors`` is a list of **groups**,
one per distinct combination of ``series``, ``quantity``, ``scope``, ``span``, ``role``,
``direction``, ``function`` and ``aggregations``; a group lists, as ``sensors``, the
entity IDs of the Effy sensors that have exactly these attributes. A reader picks a group
by its attributes and takes the entity IDs. ``sources`` maps each configured source
sensor to the Effy sensors that depend on it. After a reboot the sensor is restored with
its previous attributes plus ``restored_as_of`` and ``restored_at`` until the next
recalculation.
"""

from typing import Final, Literal, NotRequired, TypedDict

# --- Version ------------------------------------------------------------------

# Raised only for a change that breaks existing keys or meanings (ADR-021).
EFFY_PUBLISHED_SCHEMA_VERSION: Final = 2

# --- Entities with a fixed entity ID --------------------------------------------

# The sensor whose state is "a recalculation finished" and whose attributes
# publish the configuration. Its fixed entity ID is the entry point of a reader.
EFFY_RECALCULATED_FROM_ENTITY_ID: Final = "sensor.effy_recalculated_from"
# The sensors below are listed in ``sensors`` as well; a reader finds them there by their
# attributes and needs these IDs only to name one directly.
# The standby power estimate (ADR-018).
EFFY_STANDBY_POWER_ENTITY_ID: Final = "sensor.effy_standby_power"
# The charge, discharge, battery and roundtrip efficiency estimates (ADR-023).
EFFY_CHARGE_EFFICIENCY_ENTITY_ID: Final = "sensor.effy_charge_efficiency"
EFFY_DISCHARGE_EFFICIENCY_ENTITY_ID: Final = "sensor.effy_discharge_efficiency"
EFFY_BATTERY_EFFICIENCY_ENTITY_ID: Final = "sensor.effy_battery_efficiency"
EFFY_ROUNDTRIP_EFFICIENCY_ENTITY_ID: Final = "sensor.effy_roundtrip_efficiency"
# The effective values of all PV inputs added up, per slot (ADR-027).
EFFY_PV_SUM_ENTITY_ID: Final = "sensor.effy_effective_pv_sum_now"
# The energy of all PV inputs, since local midnight and in total (ADR-027).
EFFY_PV_ENERGY_TODAY_ENTITY_ID: Final = "sensor.effy_pv_deltasum_integral_today"
EFFY_PV_ENERGY_TOTAL_ENTITY_ID: Final = "sensor.effy_pv_deltasum_integral_total"
# The share of the effective inputs that PV and the EPS input make up (ADR-027).
EFFY_PV_SHARE_ENTITY_ID: Final = "sensor.effy_pv_share_now"
# The household's consumption, per slot and as energy since midnight and in total (ADR-029).
EFFY_HOUSEHOLD_CONSUMPTION_ENTITY_ID: Final = "sensor.effy_household_consumption_now"
EFFY_HOUSEHOLD_CONSUMPTION_TODAY_ENTITY_ID: Final = "sensor.effy_household_consumption_today"
EFFY_HOUSEHOLD_CONSUMPTION_TOTAL_ENTITY_ID: Final = "sensor.effy_household_consumption_total"

# --- Attributes of the recalculated-from sensor ---------------------------------

EFFY_ATTR_SCHEMA_VERSION: Final = "schema_version"
EFFY_ATTR_MAX_HISTORY_DAYS: Final = "max_history_days"
EFFY_ATTR_SENSORS: Final = "sensors"
# Source sensor -> the Effy sensors that depend on it (ADR-029 decision 7).
EFFY_ATTR_SOURCES: Final = "sources"
# Present only while the state is a restored one (until the next recalculation).
EFFY_ATTR_RESTORED_AS_OF: Final = "restored_as_of"
EFFY_ATTR_RESTORED_AT: Final = "restored_at"

# --- Keys of one group of ``sensors`` -------------------------------------------

# In the order a group is sorted by (``sensors`` last: the entity IDs are the value).
EFFY_KEY_SERIES: Final = "series"
EFFY_KEY_QUANTITY: Final = "quantity"
EFFY_KEY_SCOPE: Final = "scope"
EFFY_KEY_SPAN: Final = "span"
EFFY_KEY_ROLE: Final = "role"
EFFY_KEY_DIRECTION: Final = "direction"
EFFY_KEY_FUNCTION: Final = "function"
EFFY_KEY_AGGREGATIONS: Final = "aggregations"
EFFY_KEY_SENSORS: Final = "sensors"

# --- Values ---------------------------------------------------------------------

# ``series``: which stage or estimate of the data an Effy sensor stands for; the value
# never repeats the quantity (ADR-029 decision 8). ``effective``: the value after the loss
# is distributed, and the energy booked from it; ``derived``: the power derived from an
# energy counter; ``smoothed``: a power sensor with short gaps bridged; ``standby``,
# ``charge``, ``discharge``, ``battery``, ``roundtrip``: the estimates of the battery system;
# ``pv``: the PV share; ``consumption``: the household's consumption.
EFFY_SERIES_EFFECTIVE: Final = "effective"
EFFY_SERIES_DERIVED: Final = "derived"
EFFY_SERIES_SMOOTHED: Final = "smoothed"
EFFY_SERIES_STANDBY: Final = "standby"
EFFY_SERIES_CHARGE: Final = "charge"
EFFY_SERIES_DISCHARGE: Final = "discharge"
EFFY_SERIES_BATTERY: Final = "battery"
EFFY_SERIES_ROUNDTRIP: Final = "roundtrip"
EFFY_SERIES_PV: Final = "pv"
EFFY_SERIES_CONSUMPTION: Final = "consumption"

# ``quantity``: what the data of the sensor measures.
EFFY_QUANTITY_POWER: Final = "power"
EFFY_QUANTITY_ENERGY: Final = "energy"
EFFY_QUANTITY_EFFICIENCY: Final = "efficiency"
EFFY_QUANTITY_SHARE: Final = "share"

# ``scope``: how much of the system the sensor covers. ``source``: one configured sensor
# (a PV input, a counter, ...); ``combined``: the addition over several sources of one kind
# (all PV inputs); ``system``: the system as a whole. A sensor of scope ``source`` is in the
# ``sources`` attribute under its source; the other two are in no list.
EFFY_SCOPE_SOURCE: Final = "source"
EFFY_SCOPE_COMBINED: Final = "combined"
EFFY_SCOPE_SYSTEM: Final = "system"

# ``span``: the time one value of the sensor covers. ``slot``: one slot; ``today``: since
# local midnight; ``total``: since the running total began.
EFFY_SPAN_SLOT: Final = "slot"
EFFY_SPAN_TODAY: Final = "today"
EFFY_SPAN_TOTAL: Final = "total"

# ``direction``: the direction of the flow the sensor stands for. ``both`` is a *signed*
# series: the smoothed series of the EPS sensor (ADR-026), positive while energy leaves the
# system through the EPS/UPS port and negative while it enters, and the smoothed series of
# a household import meter that has no household export sensor (ADR-029 decision 2),
# positive while energy comes from the grid and negative while it goes to it. A reader takes
# the sign as the direction. ``unknown`` is the series of an additional sensor
# (ADR-028): Effy does not know which way its energy flows, and a reader that picks by direction
# never selects it.
EFFY_DIRECTION_INPUT: Final = "input"
EFFY_DIRECTION_OUTPUT: Final = "output"
EFFY_DIRECTION_BOTH: Final = "both"
EFFY_DIRECTION_UNKNOWN: Final = "unknown"

# ``role``: PV inputs apart from the battery system (ADR-019); every output except the EPS
# output is ``bms``. ``eps`` is the EPS/UPS sensor of the inverter (ADR-026): neither a PV
# string nor a BMS counter. ``other`` is an additional sensor (ADR-028): it has the series of an
# input, but takes part in no loss, effective value, standby, efficiency or battery calculation.
# ``household`` is a household meter sensor and everything computed from it (ADR-029).
EFFY_ROLE_PV: Final = "pv"
EFFY_ROLE_BMS: Final = "bms"
EFFY_ROLE_EPS: Final = "eps"
EFFY_ROLE_OTHER: Final = "other"
EFFY_ROLE_HOUSEHOLD: Final = "household"

# ``function``: present in every group (ADR-029 decision 9). Which counter of the battery
# system a source series stands for (ADR-023): ``grid_import``, ``discharge``, ``charge``,
# ``grid_export``; pick a BMS counter by this, not by guessing. ``eps`` marks the two series of
# the EPS/UPS sensor (ADR-026): its ``effective`` series is the effective value of its input
# half (never negative), its ``smoothed`` series the signed value. ``pv`` marks a PV input
# and the sums and energy sensors made from the PV inputs. ``unknown`` marks the series of an
# additional sensor (ADR-028): its function is not known to Effy, and a reader that picks a
# counter by function never selects it. ``standby``, ``efficiency`` and ``share`` mark the
# sensors that are estimates of the system and no counter's series. ``household_import`` and
# ``household_export`` mark the household meter sensors, ``consumption`` the household
# consumption sensors (ADR-029).
EFFY_FUNCTION_GRID_IMPORT: Final = "grid_import"
EFFY_FUNCTION_DISCHARGE: Final = "discharge"
EFFY_FUNCTION_CHARGE: Final = "charge"
EFFY_FUNCTION_GRID_EXPORT: Final = "grid_export"
EFFY_FUNCTION_EPS: Final = "eps"
EFFY_FUNCTION_UNKNOWN: Final = "unknown"
EFFY_FUNCTION_PV: Final = "pv"
EFFY_FUNCTION_STANDBY: Final = "standby"
EFFY_FUNCTION_EFFICIENCY: Final = "efficiency"
EFFY_FUNCTION_SHARE: Final = "share"
EFFY_FUNCTION_HOUSEHOLD_IMPORT: Final = "household_import"
EFFY_FUNCTION_HOUSEHOLD_EXPORT: Final = "household_export"
EFFY_FUNCTION_CONSUMPTION: Final = "consumption"

# ``aggregations``: the ``types`` a reader can pass to ``statistics_during_period``
# for the sensor's short-term statistics, with non-null values.
EFFY_AGGREGATION_MEAN: Final = "mean"
EFFY_AGGREGATION_MIN: Final = "min"
EFFY_AGGREGATION_MAX: Final = "max"
EFFY_AGGREGATION_STATE: Final = "state"
# The energy sensors (ADR-027) are recorded by Home Assistant from a ``total`` sensor.
EFFY_AGGREGATION_SUM: Final = "sum"
EFFY_AGGREGATION_CHANGE: Final = "change"

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

# Every ``energy`` sensor: Home Assistant compiles its statistics from the states of a
# ``total`` sensor (ADR-027 decision 4), so the sum and the change are there.
EFFY_ENERGY_AGGREGATIONS: Final = (
    EFFY_AGGREGATION_STATE,
    EFFY_AGGREGATION_SUM,
    EFFY_AGGREGATION_CHANGE,
)

EffySeries = Literal[
    "effective",
    "derived",
    "smoothed",
    "standby",
    "charge",
    "discharge",
    "battery",
    "roundtrip",
    "pv",
    "consumption",
]
EffyQuantity = Literal["power", "energy", "efficiency", "share"]
EffyScope = Literal["source", "combined", "system"]
EffySpan = Literal["slot", "today", "total"]
EffyDirection = Literal["input", "output", "both", "unknown"]
EffyRole = Literal["pv", "bms", "eps", "other", "household"]
EffyFunction = Literal[
    "grid_import",
    "discharge",
    "charge",
    "grid_export",
    "eps",
    "unknown",
    "pv",
    "standby",
    "efficiency",
    "share",
    "household_import",
    "household_export",
    "consumption",
]

# --- Shapes ---------------------------------------------------------------------


class EffyPublishedGroup(TypedDict):
    """One group of ``sensors``: the Effy sensors that have exactly these attributes."""

    series: EffySeries
    quantity: EffyQuantity
    scope: EffyScope
    span: EffySpan
    role: EffyRole
    direction: EffyDirection
    function: EffyFunction
    aggregations: list[str]
    # The sorted entity IDs of the sensors of the group.
    sensors: list[str]


class EffyPublishedAttributes(TypedDict):
    """The attributes of ``sensor.effy_recalculated_from``.

    ``sensors`` and ``sources`` are absent until the first recalculation closes after a
    start; the ``restored_*`` markers only while the state was restored. After the upgrade
    to version 2 a restored attribute set can still hold the version 1 shapes until then.
    """

    schema_version: int
    max_history_days: int
    # Groups sorted by the attributes in the order of the keys above.
    sensors: NotRequired[list[EffyPublishedGroup]]
    # Source sensor -> sorted entity IDs of the Effy sensors that depend on it. A source
    # without an Effy sensor has no key; sensors of scope ``combined`` and ``system`` are in
    # no list.
    sources: NotRequired[dict[str, list[str]]]
    restored_as_of: NotRequired[str]
    restored_at: NotRequired[str]
