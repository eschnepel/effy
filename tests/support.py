"""Shared test support: entry options built by role (ADR-023 decision 1).

The BMS counters are one sensor per role since ADR-023. Many older tests describe
their system as lists (PV inputs, BMS inputs, outputs); ``role_options`` maps those
lists onto the roles: the first BMS input is the grid import counter, the second the
discharge counter; the first output is the charge counter, the second the grid export
counter. More than two of either has no meaning any more and is refused loudly, so a
test cannot silently lose a sensor.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

PV_INPUT_SENSORS = "pv_input_sensors"
GRID_IMPORT_SENSOR = "grid_import_sensor"
DISCHARGE_SENSOR = "discharge_sensor"
CHARGE_SENSOR = "charge_sensor"
GRID_EXPORT_SENSOR = "grid_export_sensor"


def role_options(
    pv: Sequence[str] = (),
    bms: Sequence[str] = (),
    outputs: Sequence[str] = (),
    **extra: Any,
) -> dict[str, Any]:
    """Entry options with the given sensors assigned by role; unset roles are absent."""
    if len(bms) > 2 or len(outputs) > 2:
        raise ValueError("a system has at most two BMS inputs and two outputs (ADR-023)")
    options: dict[str, Any] = {PV_INPUT_SENSORS: list(pv), **extra}
    for key, entity in zip((GRID_IMPORT_SENSOR, DISCHARGE_SENSOR), bms, strict=False):
        options[key] = entity
    for key, entity in zip((CHARGE_SENSOR, GRID_EXPORT_SENSOR), outputs, strict=False):
        options[key] = entity
    return options
