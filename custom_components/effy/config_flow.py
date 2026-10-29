"""Config flow for Effy integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CAPACITY_UNITS,
    CONF_CAPACITY_ENTITY,
    CONF_CHARGE_SENSOR,
    CONF_DISCHARGE_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_MAX_HISTORY_DAYS,
    CONF_PV_INPUT_SENSORS,
    CONF_SOC_SENSOR,
    CONF_VALLEY_HYSTERESIS,
    DEFAULT_MAX_HISTORY_DAYS,
    DEFAULT_VALLEY_HYSTERESIS,
    DOMAIN,
    MIN_VALLEY_HYSTERESIS,
)
from .inputs import valley_hysteresis

# The four BMS counters, one optional picker each (ADR-023 decision 1), in the
# order they are validated. The two outputs must have a device class (power or
# energy): see ADR-002/ADR-008, unit normalisation and cross-unit comparability
# rely on knowing whether a sensor represents power or energy, and device_class
# is the authoritative signal for that, not just the raw unit string.
_COUNTER_KEYS = (
    CONF_GRID_IMPORT_SENSOR,
    CONF_DISCHARGE_SENSOR,
    CONF_CHARGE_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
)
_OUTPUT_KEYS = (CONF_CHARGE_SENSOR, CONF_GRID_EXPORT_SENSOR)
# The battery efficiency's two entities (ADR-023 decision 1): set together or not at all. Like
# the counters they are optional single pickers, and no entity may have two roles.
_BATTERY_KEYS = (CONF_SOC_SENSOR, CONF_CAPACITY_ENTITY)
# A capacity can be a sensor, but also a number or an input_number: it can follow the battery's
# ageing. A typed number is not offered.
_CAPACITY_DOMAINS = ["sensor", "number", "input_number"]


def _picker(
    key: str,
    suggested: str | None,
    *,
    device_class: bool,
    domains: list[str] | None = None,
) -> tuple[Any, Any]:
    """One optional single-entity picker, with a *suggested* (never a default) value.

    A default would bring a cleared picker back (the frontend omits an emptied
    optional field), and every role must be clearable.
    """
    config: dict[str, Any] = {"domain": domains or ["sensor"]}
    if device_class:
        # Restricting the picker is the first line of defense;
        # _outputs_missing_device_class below re-checks at submit time, since an
        # already-configured sensor's device_class can change or be removed later.
        config["device_class"] = ["power", "energy"]
    description = {"suggested_value": suggested} if suggested else None
    return vol.Optional(key, description=description), selector.selector({"entity": config})


def _build_schema(
    pv_input_sensors: list[str] | None = None,
    grid_import_sensor: str | None = None,
    discharge_sensor: str | None = None,
    charge_sensor: str | None = None,
    grid_export_sensor: str | None = None,
    soc_sensor: str | None = None,
    capacity_entity: str | None = None,
    valley_hysteresis: float = DEFAULT_VALLEY_HYSTERESIS,
    max_history_days: int = DEFAULT_MAX_HISTORY_DAYS,
) -> vol.Schema:
    # The PV list is an optional key with a *suggested* value: a PV-less system
    # must be able to clear it. "At least one input in total" and "at least one
    # output" are checked in _validate_user_input.
    schema: dict[Any, Any] = {
        vol.Optional(
            CONF_PV_INPUT_SENSORS,
            description={"suggested_value": pv_input_sensors or []},
        ): selector.selector({"entity": {"multiple": True, "domain": ["sensor"]}}),
    }
    for key, value, device_class in (
        (CONF_GRID_IMPORT_SENSOR, grid_import_sensor, False),
        (CONF_DISCHARGE_SENSOR, discharge_sensor, False),
        (CONF_CHARGE_SENSOR, charge_sensor, True),
        (CONF_GRID_EXPORT_SENSOR, grid_export_sensor, True),
    ):
        marker, picker = _picker(key, value, device_class=device_class)
        schema[marker] = picker
    marker, picker = _picker(CONF_SOC_SENSOR, soc_sensor, device_class=False)
    schema[marker] = picker
    marker, picker = _picker(
        CONF_CAPACITY_ENTITY, capacity_entity, device_class=False, domains=_CAPACITY_DOMAINS
    )
    schema[marker] = picker
    # The hysteresis has a real default (30): an emptied number box is not a "no value" the way
    # an emptied picker is. The selector holds the minimum of 15 (ADR-023 decision 1).
    schema[vol.Optional(CONF_VALLEY_HYSTERESIS, default=valley_hysteresis)] = selector.selector(
        {
            "number": {
                "min": MIN_VALLEY_HYSTERESIS,
                "max": 100,
                "step": 1,
                "mode": "box",
                "unit_of_measurement": "%",
            }
        }
    )
    schema[vol.Optional(CONF_MAX_HISTORY_DAYS, default=max_history_days)] = selector.selector(
        {
            "number": {
                "min": 1,
                "max": 365,
                "step": 1,
                "mode": "box",
                "unit_of_measurement": "days",
            }
        }
    )
    return vol.Schema(schema)


def _outputs_missing_device_class(hass: Any, output_ids: list[str]) -> bool:
    """True if any selected output sensor currently lacks a device_class.

    The entity selector already filters candidates by device_class, but
    that only guards new selections made through the picker. An
    already-configured output sensor can lose its device_class later (the
    underlying integration is reconfigured, the entity is replaced, etc.),
    so this is re-checked at submit time on every save, not just enforced
    once at selection time.
    """
    for entity_id in output_ids:
        state = hass.states.get(entity_id)
        if state is None or not state.attributes.get("device_class"):
            return True
    return False


def _entity_attribute(hass: Any, entity_id: str, attribute: str) -> Any:
    """One attribute of an entity's current state, or ``None`` (no state, or no such attribute)."""
    state = hass.states.get(entity_id)
    return None if state is None else state.attributes.get(attribute)


def _validate_battery_options(hass: Any, user_input: dict[str, Any]) -> dict[str, str]:
    """The checks of the battery efficiency's two entities (ADR-023 decision 1).

    SoC and capacity are set together or not at all (the error sits on the missing one); the
    SoC sensor needs state class ``measurement`` (a SoC is a level, not a counter; an entity
    without a state has none to show); a capacity that reports a unit must report Wh or kWh. A
    capacity without a unit, or without a state yet, cannot be judged here and is accepted.
    """
    soc = user_input.get(CONF_SOC_SENSOR) or None
    capacity = user_input.get(CONF_CAPACITY_ENTITY) or None
    if soc and not capacity:
        return {CONF_CAPACITY_ENTITY: "soc_and_capacity_together"}
    if capacity and not soc:
        return {CONF_SOC_SENSOR: "soc_and_capacity_together"}
    if soc and _entity_attribute(hass, soc, "state_class") != "measurement":
        return {CONF_SOC_SENSOR: "soc_needs_measurement"}
    if capacity:
        unit = _entity_attribute(hass, capacity, "unit_of_measurement")
        if unit is not None and unit not in CAPACITY_UNITS:
            return {CONF_CAPACITY_ENTITY: "capacity_unit_not_energy"}
    return {}


def _validate_user_input(hass: Any, user_input: dict[str, Any]) -> dict[str, str]:
    """Validate one submit of the config or the options flow (ADR-019, ADR-023).

    Returns translatable error keys (resolved via translations/*.json), never
    raw text — ADR-000 §8. The first failing check wins, in this order: at
    least one input in total (the PV list, grid import or discharge); no entity
    in two roles (the PV list, the four counters, the SoC sensor and the capacity
    entity; the error sits on the later field); at least one output (charge or
    grid export); every output has a device class; then the battery options
    (``_validate_battery_options``). Each picker alone is optional.
    """
    pv = list(user_input.get(CONF_PV_INPUT_SENSORS) or [])
    counters = {key: user_input.get(key) or None for key in _COUNTER_KEYS}
    errors: dict[str, str] = {}
    if (
        len(pv) + sum(1 for k in (CONF_GRID_IMPORT_SENSOR, CONF_DISCHARGE_SENSOR) if counters[k])
        < 1
    ):
        errors[CONF_PV_INPUT_SENSORS] = "at_least_one_input"
        return errors
    seen = set(pv)
    if len(seen) != len(pv):
        errors[CONF_PV_INPUT_SENSORS] = "sensor_in_two_roles"
        return errors
    for key in (*_COUNTER_KEYS, *_BATTERY_KEYS):
        entity_id = user_input.get(key) or None
        if entity_id is None:
            continue
        if entity_id in seen:
            errors[key] = "sensor_in_two_roles"
            return errors
        seen.add(entity_id)
    outputs = [(k, entity) for k in _OUTPUT_KEYS if (entity := counters[k])]
    if not outputs:
        errors[CONF_CHARGE_SENSOR] = "at_least_one_output"
        return errors
    for key, entity_id in outputs:
        if _outputs_missing_device_class(hass, [entity_id]):
            errors[key] = "output_needs_device_class"
            return errors
    return _validate_battery_options(hass, user_input)


def _normalised(user_input: dict[str, Any]) -> dict[str, Any]:
    """The submitted options: the PV list always present (possibly empty), a counter, the SoC
    sensor and the capacity entity only if set; everything else (the hysteresis, the history
    days) as submitted."""
    optional = (*_COUNTER_KEYS, *_BATTERY_KEYS)
    result = {key: value for key, value in user_input.items() if key not in optional}
    result[CONF_PV_INPUT_SENSORS] = list(user_input.get(CONF_PV_INPUT_SENSORS) or [])
    for key in optional:
        if user_input.get(key):
            result[key] = user_input[key]
    return result


class EffyConfigFlow(
    config_entries.ConfigFlow,  # type: ignore[misc]
    domain=DOMAIN,
):
    """Handle a config flow for Effy."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.FlowResult:
        """Handle the initial step.

        Validation failures return translatable error keys (resolved via
        translations/*.json), never raw text — see ADR-000 §8.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_user_input(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title="Effy",
                    data={},
                    options=_normalised(user_input),
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(),
            errors=errors,
        )

    @staticmethod
    @callback  # type: ignore
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EffyOptionsFlow:
        """Return the options flow handler."""
        return EffyOptionsFlow(config_entry)


class EffyOptionsFlow(config_entries.OptionsFlowWithReload):  # type: ignore[misc]
    """Handle options for Effy.

    ``OptionsFlowWithReload`` reloads the entry when the options change. Effy has no
    update listener of its own, which Home Assistant forbids alongside it, and which
    could not reload an entry whose setup failed anyway: Home Assistant removes an
    entry's listeners when its setup fails, and an entry without a charge or a grid
    export counter refuses to set up (ADR-023 decision 1).
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_user_input(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title="", data=_normalised(user_input))

        current = self._config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(
                pv_input_sensors=current.get(CONF_PV_INPUT_SENSORS, []),
                grid_import_sensor=current.get(CONF_GRID_IMPORT_SENSOR),
                discharge_sensor=current.get(CONF_DISCHARGE_SENSOR),
                charge_sensor=current.get(CONF_CHARGE_SENSOR),
                grid_export_sensor=current.get(CONF_GRID_EXPORT_SENSOR),
                soc_sensor=current.get(CONF_SOC_SENSOR),
                capacity_entity=current.get(CONF_CAPACITY_ENTITY),
                valley_hysteresis=valley_hysteresis(current),
                max_history_days=current.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS),
            ),
            errors=errors,
        )
