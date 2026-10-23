"""Input sensors by role (PV / BMS) and the silent migration of the old key.

ADR-019 splits Effy's one mixed list of input sensors into PV inputs and BMS
inputs. The calculation does not change: the waterfall (ADR-001) sees one mixed
pool, so every consumer that only needs "all inputs" uses ``all_input_ids``;
the two roles are exposed separately for the standby power sensor (ADR-018),
whose drain source can only be a BMS input.

Pure functions over the entry's options mapping; only ``const`` is imported, so
the module is testable without Home Assistant.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import CONF_BMS_INPUT_SENSORS, CONF_PV_INPUT_SENSORS

# The single input list of every entry created before ADR-019. Read only by
# the migration below; nothing else may use it.
LEGACY_CONF_INPUT_SENSORS = "input_sensors"


def pv_input_ids(options: Mapping[str, Any]) -> list[str]:
    """Source entity IDs of the PV inputs (a copy; empty when unset)."""
    return list(options.get(CONF_PV_INPUT_SENSORS, []))


def bms_input_ids(options: Mapping[str, Any]) -> list[str]:
    """Source entity IDs of the BMS inputs (a copy; empty when unset)."""
    return list(options.get(CONF_BMS_INPUT_SENSORS, []))


def all_input_ids(options: Mapping[str, Any]) -> list[str]:
    """Every input, PV first: the mixed pool the waterfall works on (ADR-019).

    The result of the calculation does not depend on this order (equal values
    get equal shares, ADR-001); it only fixes the order entities are created in.
    """
    return pv_input_ids(options) + bms_input_ids(options)


def legacy_inputs_were_moved(options: Mapping[str, Any]) -> bool:
    """True when ``migrate_legacy_input_options`` moves the old list into the PV list.

    False when there is no old key, or when a new key is already present (the
    new keys win and the old key is only dropped).
    """
    return (
        LEGACY_CONF_INPUT_SENSORS in options
        and CONF_PV_INPUT_SENSORS not in options
        and CONF_BMS_INPUT_SENSORS not in options
    )


def migrate_legacy_input_options(options: Mapping[str, Any]) -> dict[str, Any] | None:
    """The options of an entry with the old ``input_sensors`` key, migrated (ADR-019 D4).

    Returns ``None`` when the entry has no old key (nothing to write). Otherwise
    a new dict without the old key: if neither new key is present, the old list
    becomes ``pv_input_sensors`` and ``bms_input_sensors`` is empty — the human
    reassigns the BMS sensors in the options flow; if either new key is already
    present, the new keys win and the old list is dropped unread. The given
    mapping is never mutated.
    """
    if LEGACY_CONF_INPUT_SENSORS not in options:
        return None
    migrated = {key: value for key, value in options.items() if key != LEGACY_CONF_INPUT_SENSORS}
    if legacy_inputs_were_moved(options):
        migrated[CONF_PV_INPUT_SENSORS] = list(options[LEGACY_CONF_INPUT_SENSORS])
        migrated[CONF_BMS_INPUT_SENSORS] = []
    return migrated
