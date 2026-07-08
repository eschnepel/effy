"""Utility helpers for reading HA sensor states."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .calculation import SensorReading

_LOGGER = logging.getLogger(__name__)

# Width of one statistics slot in minutes — must match HA recorder and ADR-007.
SLOT_MINUTES = 5


def effective_unit_for(unit: str) -> str:
    """Return the unit an *effective* (post-loss-distribution) reading is in.

    ``distribute_loss`` always operates on, and ``effective_values_w``
    always holds, plain Watts — for MEASUREMENT/TOTAL-as-power sensors
    (W/kW) that's already the source unit; for TOTAL_INCREASING/TOTAL-as-
    energy sensors (Wh/kWh), ``to_power_equivalent`` converts the raw
    energy delta into a W-equivalent *before* it ever reaches
    ``distribute_loss`` (see ADR-008). Either way, the result handed back
    by ``effective_in_original_unit`` is always a power reading, never an
    energy one — so any caller that reports an "effective" value for an
    entity (the live EffySensor, or the history-recalc write path) must use
    *this* unit, not the source entity's own raw unit_of_measurement. Using
    the raw Wh/kWh unit instead is a category error, not just a scale
    error: ``_from_w`` only knows how to strip a kilo- prefix (÷1000), it
    has no notion of "per hour" and cannot turn a Watts figure into a
    genuine Wh/kWh one — see the bug this fixed.
    """
    if unit == "Wh":
        return "W"
    if unit == "kWh":
        return "kW"
    return unit


def to_power_equivalent(
    value: float,
    unit: str,
    slot_minutes: int = SLOT_MINUTES,
) -> tuple[float, str]:
    """Convert an energy-delta reading to its W-equivalent average power (ADR-008).

    TOTAL_INCREASING / TOTAL-as-energy sensors supply energy increments in Wh
    or kWh (``change`` from the statistics API, or the slot-aligned live delta
    from EffyCoordinator._delta_reading).  MEASUREMENT/TOTAL-as-power sensors
    supply instantaneous power in W or kW.  To make both comparable inside
    ``distribute_loss``, energy deltas are divided by the slot duration in hours:

        W_equiv = Wh_delta / (slot_minutes / 60)
                = Wh_delta × (60 / slot_minutes)
                = Wh_delta × 12   (for 5-minute slots)

    ``original_unit`` is changed from Wh → W (or kWh → kW), via
    ``effective_unit_for``, so that ``effective_in_original_unit`` inside
    ``distribute_loss`` converts the result back to W/kW — the natural
    output unit for an average-power value over the interval.

    Non-energy units (W, kW) are returned unchanged.

    ``slot_minutes`` defaults to SLOT_MINUTES (5) but can be overridden; the
    live path may eventually pass the actual elapsed time within the slot.
    """
    if unit in ("Wh", "kWh"):
        return value * (60.0 / slot_minutes), effective_unit_for(unit)
    return value, unit


def get_sensor_meta(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """Return relevant metadata for a sensor entity."""
    state = hass.states.get(entity_id)
    if state is None:
        return {}
    attrs = state.attributes
    return {
        "unit": attrs.get("unit_of_measurement", "W"),
        "state_class": attrs.get("state_class"),
        "friendly_name": attrs.get("friendly_name", entity_id),
    }


def get_current_value(
    hass: HomeAssistant,
    entity_id: str,
) -> SensorReading | None:
    """Read the current state of a sensor and return a SensorReading.

    The raw value is stored as-is in the original unit (W, kW, Wh, kWh).
    For TOTAL_INCREASING sensors this function returns the absolute counter
    value. The caller (EffyCoordinator) is responsible for computing the
    slot-aligned delta and calling ``to_power_equivalent`` before passing
    the reading on to ``distribute_loss``.
    """
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown", ""):
        return None

    try:
        raw = float(state.state)
    except ValueError:
        _LOGGER.warning("Effy: cannot parse state '%s' for %s", state.state, entity_id)
        return None

    unit: str = state.attributes.get("unit_of_measurement", "W")
    return SensorReading(entity_id=entity_id, raw_value=raw, original_unit=unit)
