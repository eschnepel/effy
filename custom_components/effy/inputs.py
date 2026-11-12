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
    CONF_ADDITIONAL_SENSORS,
    CONF_CAPACITY_ENTITY,
    CONF_CHARGE_SENSOR,
    CONF_DISCHARGE_SENSOR,
    CONF_EPS_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_PV_INPUT_SENSORS,
    CONF_SOC_SENSOR,
    CONF_VALLEY_HYSTERESIS,
    DEFAULT_VALLEY_HYSTERESIS,
    MIN_VALLEY_HYSTERESIS,
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


def soc_id(options: Mapping[str, Any]) -> str | None:
    """The state-of-charge sensor of the battery efficiency (ADR-023), or ``None``."""
    return _role_id(options, CONF_SOC_SENSOR)


def capacity_id(options: Mapping[str, Any]) -> str | None:
    """The battery-capacity entity of the battery efficiency (ADR-023), or ``None``."""
    return _role_id(options, CONF_CAPACITY_ENTITY)


def eps_id(options: Mapping[str, Any]) -> str | None:
    """The EPS/UPS sensor (ADR-026), a signed power sensor, or ``None``.

    Neither a PV input nor a BMS counter: it is in none of ``pv_input_ids``,
    ``bms_input_ids`` and ``output_ids``. It is the last member of ``all_input_ids``; the slot
    loop splits its signed value into an EPS input and an EPS output.
    """
    return _role_id(options, CONF_EPS_SENSOR)


def additional_ids(options: Mapping[str, Any]) -> list[str]:
    """The additional sensors (ADR-028), in the saved order, each once; empty when unset.

    A sensor in the list gets the series of an input (a derived power or a smoothed series) and
    is in no formula. A sensor that also has a role (a PV input, a BMS counter, the EPS sensor,
    the state of charge or the capacity) is *not* an additional one: the options flow refuses
    it, and a hand-edited entry must not give two Effy entities one unique ID (decision 3).
    """
    roles = {
        *pv_input_ids(options),
        *bms_input_ids(options),
        *output_ids(options),
        *(i for i in (eps_id(options), soc_id(options), capacity_id(options)) if i),
    }
    result: list[str] = []
    for entity_id in options.get(CONF_ADDITIONAL_SENSORS) or []:
        if entity_id and entity_id not in roles and entity_id not in result:
            result.append(str(entity_id))
    return result


def valley_hysteresis(options: Mapping[str, Any]) -> float:
    """The valley hysteresis in SoC points: the saved number, else the default (30).

    The options flow keeps it at 15 or above (ADR-023 decision 1); this accessor does not
    second-guess a stored number.
    """
    value = options.get(CONF_VALLEY_HYSTERESIS)
    return float(DEFAULT_VALLEY_HYSTERESIS if value is None else value)


def battery_hysteresis(options: Mapping[str, Any]) -> float:
    """The valley hysteresis the battery machine runs with (ADR-023 decision 1).

    The saved number, never below the minimum of 15: the options flow keeps it there, and a
    value edited by hand (or one that is no number) must not make the slot job raise, for the
    machine refuses a hysteresis of 10 or less. ``valley_hysteresis`` stays what is saved.
    """
    try:
        configured = valley_hysteresis(options)
    except (TypeError, ValueError):
        return float(DEFAULT_VALLEY_HYSTERESIS)
    return max(float(MIN_VALLEY_HYSTERESIS), configured)


def bms_input_ids(options: Mapping[str, Any]) -> list[str]:
    """Source entity IDs of the BMS inputs: grid import first, then discharge (set ones only)."""
    return [i for i in (grid_import_id(options), discharge_id(options)) if i]


def output_ids(options: Mapping[str, Any]) -> list[str]:
    """Source entity IDs of the BMS outputs: charge first, then grid export (set ones only)."""
    return [i for i in (charge_id(options), grid_export_id(options)) if i]


def all_input_ids(options: Mapping[str, Any]) -> list[str]:
    """Every input, PV first, the EPS sensor last: the mixed pool the waterfall works on.

    The result of the calculation does not depend on this order (equal values
    get equal shares, ADR-001); it only fixes the order entities are created in. The EPS
    sensor (ADR-026) is a member because it is an input half of the time and must be fetched,
    smoothed and published like any power-family input; it is *not* in ``output_ids``, since
    the slot loop turns its signed value into the two halves (``split_eps_readings``).
    """
    eps = eps_id(options)
    return pv_input_ids(options) + bms_input_ids(options) + ([eps] if eps else [])


def formula_signature(options: Mapping[str, Any]) -> dict[str, Any]:
    """The sensors that make up the formula (ADR-027 decision 6): what a recalculation depends on.

    The PV inputs (a sorted list: their order does not matter), grid import, discharge, charge,
    grid export and the EPS sensor, ``None`` where a role is unset, and the additional sensors
    (ADR-028, a sorted list, empty when there are none); keyed by the option each comes from. A
    change of any of them changes what the history is, so Effy recalculates it by itself at
    setup; a change of anything else (history days, SoC, capacity, valley hysteresis, debug
    export) does not. JSON-safe: it is kept in the entry's data and compared after a round trip
    through the store.
    """
    return {
        CONF_PV_INPUT_SENSORS: sorted(set(pv_input_ids(options))),
        CONF_GRID_IMPORT_SENSOR: grid_import_id(options),
        CONF_DISCHARGE_SENSOR: discharge_id(options),
        CONF_CHARGE_SENSOR: charge_id(options),
        CONF_GRID_EXPORT_SENSOR: grid_export_id(options),
        CONF_EPS_SENSOR: eps_id(options),
        CONF_ADDITIONAL_SENSORS: sorted(additional_ids(options)),
    }


def signature_pv_inputs(signature: Mapping[str, Any] | None) -> list[str] | None:
    """The PV inputs a stored signature was made for; ``None`` when it does not say.

    ``None`` is *unknown*, not *no PV inputs* (which is ``[]``): an entry that never stored a
    signature has none, and so does anything that is not a signature.
    """
    if not isinstance(signature, Mapping):
        return None
    pv = signature.get(CONF_PV_INPUT_SENSORS)
    if not isinstance(pv, list):
        return None
    return [str(entity_id) for entity_id in pv]


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
