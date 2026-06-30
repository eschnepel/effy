"""Utility helpers for reading HA sensor states."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .calculation import SensorReading

_LOGGER = logging.getLogger(__name__)


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
    """
    Read the current state of a sensor and return a SensorReading.

    The raw value is stored as-is in the original unit (W, kW, Wh, kWh) and
    deliberately left un-normalized — see ADR-002 for why normalization
    happens exactly once, inside ``distribute_loss``, rather than here.
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
