"""Input sensors by role (PV / BMS) and the silent migration of the old key.

ADR-019 splits Effy's one mixed list of input sensors into PV inputs and BMS
inputs; ADR-023 decision 1 replaces the BMS list and the output list by one
optional sensor per BMS counter (grid import, discharge, charge, grid export).
The calculation does not change: the waterfall (ADR-001) sees one mixed pool of
inputs and the outputs, so every consumer that only needs "all inputs" uses
``all_input_ids``; the roles are exposed separately for the standby power sensor
(ADR-018) and the efficiency sensors (ADR-023).

Pure functions over the entry's options mapping; only ``const`` is imported, so
the module is testable without Home Assistant.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_CHARGE_SENSOR,
    CONF_DISCHARGE_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_PV_INPUT_SENSORS,
)

# The single input list of every entry created before ADR-019. Read only by
# the migration below; nothing else may use it.
LEGACY_CONF_INPUT_SENSORS = "input_sensors"


def pv_input_ids(options: Mapping[str, Any]) -> list[str]:
    """Source entity IDs of the PV inputs (a copy; empty when unset)."""
    return list(options.get(CONF_PV_INPUT_SENSORS, []))


def _role_id(options: Mapping[str, Any], key: str) -> str | None:
    """The entity ID assigned to one role, or ``None`` when unset (or emptied)."""
    value = options.get(key)
    return str(value) if value else None


def grid_import_id(options: Mapping[str, Any]) -> str | None:
    """The grid import counter (a BMS input), or ``None``."""
    return _role_id(options, CONF_GRID_IMPORT_SENSOR)


def discharge_id(options: Mapping[str, Any]) -> str | None:
    """The discharge counter (a BMS input), or ``None``."""
    return _role_id(options, CONF_DISCHARGE_SENSOR)


def charge_id(options: Mapping[str, Any]) -> str | None:
    """The charge counter (a BMS output), or ``None``."""
    return _role_id(options, CONF_CHARGE_SENSOR)


def grid_export_id(options: Mapping[str, Any]) -> str | None:
    """The grid export counter (a BMS output), or ``None``."""
    return _role_id(options, CONF_GRID_EXPORT_SENSOR)


def bms_input_ids(options: Mapping[str, Any]) -> list[str]:
    """Source entity IDs of the BMS inputs: grid import first, then discharge (set ones only)."""
    return [i for i in (grid_import_id(options), discharge_id(options)) if i]


def output_ids(options: Mapping[str, Any]) -> list[str]:
    """Source entity IDs of the BMS outputs: charge first, then grid export (set ones only)."""
    return [i for i in (charge_id(options), grid_export_id(options)) if i]


def all_input_ids(options: Mapping[str, Any]) -> list[str]:
    """Every input, PV first: the mixed pool the waterfall works on (ADR-019).

    The result of the calculation does not depend on this order (equal values
    get equal shares, ADR-001); it only fixes the order entities are created in.
    """
    return pv_input_ids(options) + bms_input_ids(options)


def legacy_inputs_were_moved(options: Mapping[str, Any]) -> bool:
    """True when ``migrate_legacy_input_options`` moves the old list into the PV list.

    False when there is no old key, or when a new key (the PV list or one of the
    four counters) is already present (the new keys win and the old key is only
    dropped). The ADR-019 keys ``bms_input_sensors`` and ``output_sensors`` are not
    new keys: they are ignored (ADR-023 decision 1).
    """
    return LEGACY_CONF_INPUT_SENSORS in options and not any(
        key in options
        for key in (
            CONF_PV_INPUT_SENSORS,
            CONF_GRID_IMPORT_SENSOR,
            CONF_DISCHARGE_SENSOR,
            CONF_CHARGE_SENSOR,
            CONF_GRID_EXPORT_SENSOR,
        )
    )


def migrate_legacy_input_options(options: Mapping[str, Any]) -> dict[str, Any] | None:
    """The options of an entry with the old ``input_sensors`` key, migrated (ADR-019 D4).

    Returns ``None`` when the entry has no old key (nothing to write). Otherwise
    a new dict without the old key: if no new key is present, the old list
    becomes ``pv_input_sensors`` — the human assigns the four BMS counters in the
    options flow, and until then the entry refuses to set up (ADR-023 decision 1);
    if a new key is already present, the new keys win and the old list is dropped
    unread. The given mapping is never mutated.
    """
    if LEGACY_CONF_INPUT_SENSORS not in options:
        return None
    migrated = {key: value for key, value in options.items() if key != LEGACY_CONF_INPUT_SENSORS}
    if legacy_inputs_were_moved(options):
        migrated[CONF_PV_INPUT_SENSORS] = list(options[LEGACY_CONF_INPUT_SENSORS])
    return migrated
