"""
Unit tests for the Effy config flow's output-sensor device_class validation.

Covers:
  - _outputs_missing_device_class

Uses the same importlib-by-file-path + HA-module-stubbing pattern as
test_coordinator_slot.py (ADR-000 §6). Neither homeassistant nor voluptuous
need to be installed to run these tests.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from effy.config_flow import _outputs_missing_device_class as _outputs_missing_device_class

# ---------------------------------------------------------------------------
# 1. HA / voluptuous stubs – registered before config_flow.py is loaded
# ---------------------------------------------------------------------------


def _stub(name: str, **attrs: Any) -> ModuleType:
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _FlowHandlerBase:
    """Minimal stand-in for HA's FlowHandler/ConfigFlow/OptionsFlow.

    Supports being subclassed with a `domain=` kwarg and records what a step
    returns: ``async_show_form`` / ``async_create_entry`` return their keyword
    arguments as a plain dict, so tests can inspect the schema, the errors and
    the created data (TASK-0008).
    """

    hass: Any = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__()

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}


_MISSING = object()


class _OptionsFlowWithReload(_FlowHandlerBase):
    """Stand-in for HA's OptionsFlowWithReload (reloads the entry when options change)."""

    automatic_reload = True


class _Marker:
    """voluptuous Required/Optional stand-in: remembers key, default, description."""

    required = False

    def __init__(self, schema: str, default: Any = _MISSING, description: Any = None) -> None:
        self.schema = schema
        self.default = default
        self.description = description


class _Required(_Marker):
    required = True


class _Optional(_Marker):
    required = False


class _Schema:
    def __init__(self, schema: dict[Any, Any]) -> None:
        self.schema = schema


_stub("voluptuous", Schema=_Schema, Required=_Required, Optional=_Optional)
_stub(
    "homeassistant.config_entries",
    ConfigFlow=_FlowHandlerBase,
    OptionsFlow=_FlowHandlerBase,
    OptionsFlowWithReload=_OptionsFlowWithReload,
    ConfigEntry=object,
    FlowResult=object,
)
_stub("homeassistant")
_stub("homeassistant.core", callback=lambda f: f)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.selector", selector=lambda cfg: cfg)

# ---------------------------------------------------------------------------
# 2. Load config_flow.py by file path
# ---------------------------------------------------------------------------

_base = Path(__file__).resolve().parent.parent / "custom_components" / "effy"

_effy_pkg = ModuleType("effy")
_effy_pkg.__path__ = [str(_base)]
_effy_pkg.__package__ = "effy"
sys.modules["effy"] = _effy_pkg


def _load(reg_name: str, filename: str) -> ModuleType:
    path = _base / filename
    spec = importlib.util.spec_from_file_location(reg_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "effy"
    sys.modules[reg_name] = mod
    spec.loader.exec_module(mod)
    return mod


_unused_const = _load("effy.const", "const.py")
_config_flow = _load("effy.config_flow", "config_flow.py")

_outputs_missing_device_class = _config_flow._outputs_missing_device_class
_const = sys.modules["effy.const"]
PV = _const.CONF_PV_INPUT_SENSORS
GRID_IMPORT = _const.CONF_GRID_IMPORT_SENSOR
DISCHARGE = _const.CONF_DISCHARGE_SENSOR
CHARGE = _const.CONF_CHARGE_SENSOR
GRID_EXPORT = _const.CONF_GRID_EXPORT_SENSOR
COUNTERS = (GRID_IMPORT, DISCHARGE, CHARGE, GRID_EXPORT)
SOC = _const.CONF_SOC_SENSOR
CAPACITY = _const.CONF_CAPACITY_ENTITY
HYSTERESIS = _const.CONF_VALLEY_HYSTERESIS


# ---------------------------------------------------------------------------
# 3. Test doubles
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, device_class: str | None, **attributes: Any) -> None:
        self.attributes: dict[str, Any] = {"device_class": device_class} if device_class else {}
        self.attributes.update(attributes)


class _FakeStates:
    def __init__(self, states: dict[str, _FakeState]) -> None:
        self._states = states

    def get(self, entity_id: str) -> _FakeState | None:
        return self._states.get(entity_id)


class _FakeHass:
    def __init__(self, states: dict[str, _FakeState]) -> None:
        self.states = _FakeStates(states)


def _hass_with_outputs() -> _FakeHass:
    return _FakeHass(
        {
            "sensor.export": _FakeState("energy"),
            "sensor.charge": _FakeState("energy"),
            "sensor.bare": _FakeState(None),
            # TASK-0023: the battery options
            "sensor.soc": _FakeState("battery", state_class="measurement", unit_of_measurement="%"),
            "sensor.soc_total": _FakeState("battery", state_class="total_increasing"),
            "sensor.soc_no_class": _FakeState("battery", unit_of_measurement="%"),
            "sensor.cap_kwh": _FakeState("energy_storage", unit_of_measurement="kWh"),
            "sensor.cap_wh": _FakeState("energy_storage", unit_of_measurement="Wh"),
            "number.cap": _FakeState(None, unit_of_measurement="kWh"),
            "input_number.cap": _FakeState(None, unit_of_measurement="Wh"),
            "sensor.cap_mwh": _FakeState("energy_storage", unit_of_measurement="MWh"),
            "sensor.cap_percent": _FakeState(None, unit_of_measurement="%"),
            "number.cap_no_unit": _FakeState(None),
        }
    )


def _keys(form: dict[str, Any]) -> dict[str, _Marker]:
    return {marker.schema: marker for marker in form["data_schema"].schema}


# ---------------------------------------------------------------------------
# 4. Tests
# ---------------------------------------------------------------------------


class TestOutputsMissingDeviceClass:
    def test_all_have_device_class_returns_false(self) -> None:
        hass = _FakeHass(
            {
                "sensor.grid_export": _FakeState("energy"),
                "sensor.battery_export": _FakeState("power"),
            }
        )
        assert (
            _outputs_missing_device_class(hass, ["sensor.grid_export", "sensor.battery_export"])
            is False
        )

    def test_one_missing_device_class_returns_true(self) -> None:
        hass = _FakeHass(
            {
                "sensor.grid_export": _FakeState("energy"),
                "sensor.battery_export": _FakeState(None),
            }
        )
        assert (
            _outputs_missing_device_class(hass, ["sensor.grid_export", "sensor.battery_export"])
            is True
        )

    def test_missing_entity_state_counts_as_missing(self) -> None:
        """An entity_id with no known state (e.g. unavailable) can't be
        confirmed to have a device_class, so it's treated as missing.
        """
        hass = _FakeHass({})
        assert _outputs_missing_device_class(hass, ["sensor.does_not_exist"]) is True

    def test_empty_output_list_returns_false(self) -> None:
        hass = _FakeHass({})
        assert _outputs_missing_device_class(hass, []) is False

    def test_empty_string_device_class_counts_as_missing(self) -> None:
        """An explicit empty-string device_class is falsy and must not pass."""
        hass = _FakeHass({"sensor.x": _FakeState("")})
        assert _outputs_missing_device_class(hass, ["sensor.x"]) is True


# ---------------------------------------------------------------------------
# TASK-0008 / ADR-019: input sensors split by role
# ---------------------------------------------------------------------------


class TestSchemaHasTheFourCounterPickers:
    def _form(self, flow_step: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        flow = _config_flow.EffyConfigFlow()
        flow.hass = _hass_with_outputs()
        if flow_step == "user":
            return asyncio.run(flow.async_step_user())
        entry = type(
            "Entry",
            (),
            {
                "options": options
                if options is not None
                else {PV: ["sensor.pv"], GRID_IMPORT: "sensor.g"}
            },
        )()
        options_flow = _config_flow.EffyOptionsFlow(entry)
        options_flow.hass = flow.hass
        return asyncio.run(options_flow.async_step_init())

    def test_both_flows_show_the_pv_list_and_the_four_single_pickers(self) -> None:
        for step in ("user", "init"):
            keys = _keys(self._form(step))
            assert PV in keys and all(c in keys for c in COUNTERS)
        schema = self._form("user")["data_schema"].schema
        for marker, selector_cfg in schema.items():
            if marker.schema == PV:
                assert selector_cfg["entity"]["multiple"] is True
            if marker.schema in COUNTERS:
                assert "multiple" not in selector_cfg["entity"]  # one sensor per role
                assert selector_cfg["entity"]["domain"] == ["sensor"]

    def test_the_old_keys_are_in_neither_form(self) -> None:
        for step in ("user", "init"):
            keys = _keys(self._form(step))
            for old in ("input_sensors", "bms_input_sensors", "output_sensors"):
                assert old not in keys

    def test_every_picker_is_optional(self) -> None:
        keys = _keys(self._form("user"))
        for key in (PV, *COUNTERS):
            assert keys[key].required is False

    def test_the_outputs_are_restricted_to_a_device_class_and_the_inputs_are_not(self) -> None:
        schema = self._form("user")["data_schema"].schema
        by_key = {marker.schema: cfg for marker, cfg in schema.items()}
        for key in (CHARGE, GRID_EXPORT):
            assert by_key[key]["entity"]["device_class"] == ["power", "energy"]
        for key in (GRID_IMPORT, DISCHARGE):
            assert "device_class" not in by_key[key]["entity"]

    def test_the_options_flow_prefills_every_assigned_role_as_a_suggestion(self) -> None:
        options = {
            PV: ["sensor.pv"],
            GRID_IMPORT: "sensor.g",
            DISCHARGE: "sensor.d",
            CHARGE: "sensor.c",
            GRID_EXPORT: "sensor.e",
        }
        keys = _keys(self._form("init", options))
        assert keys[PV].description == {"suggested_value": ["sensor.pv"]}
        assert keys[GRID_IMPORT].description == {"suggested_value": "sensor.g"}
        assert keys[DISCHARGE].description == {"suggested_value": "sensor.d"}
        assert keys[CHARGE].description == {"suggested_value": "sensor.c"}
        assert keys[GRID_EXPORT].description == {"suggested_value": "sensor.e"}

    def test_an_unassigned_role_has_no_suggestion_and_no_default(self) -> None:
        keys = _keys(self._form("init", {PV: ["sensor.a"]}))  # an entry that still has the old keys
        for key in COUNTERS:
            assert keys[key].description is None
            assert keys[key].default is _MISSING


class TestOptionsFlowHook:
    """The entry point Home Assistant calls to open the options dialog (TASK-0028)."""

    def test_the_hook_returns_the_options_flow_of_the_entry_it_is_given(self) -> None:
        def entry(pv: str) -> Any:
            return type("Entry", (), {"options": {PV: [pv]}})()

        first = _config_flow.EffyConfigFlow.async_get_options_flow(entry("sensor.first"))
        second = _config_flow.EffyConfigFlow.async_get_options_flow(entry("sensor.second"))

        assert isinstance(first, _config_flow.EffyOptionsFlow)
        assert first is not second  # one flow per call, not a shared instance
        for flow, expected in ((first, "sensor.first"), (second, "sensor.second")):
            flow.hass = _hass_with_outputs()
            form = asyncio.run(flow.async_step_init())
            assert _keys(form)[PV].description == {"suggested_value": [expected]}

    def test_the_options_flow_reloads_the_entry_itself(self) -> None:
        # ADR-023 decision 1 / Open decision 3, from the source of Home Assistant 2026.10.4:
        # saving reloads the entry, also one whose setup failed (it has no listeners left);
        # Effy therefore has no update listener (HA forbids both together).
        assert issubclass(_config_flow.EffyOptionsFlow, _OptionsFlowWithReload)
        assert _config_flow.EffyOptionsFlow.automatic_reload is True


class TestInputValidation:
    def _submit(self, user_input: dict[str, Any]) -> dict[str, Any]:
        flow = _config_flow.EffyConfigFlow()
        flow.hass = _hass_with_outputs()
        return asyncio.run(flow.async_step_user(user_input))

    def test_no_input_at_all_is_at_least_one_input(self) -> None:
        result = self._submit({PV: [], CHARGE: "sensor.charge"})
        assert result["type"] == "form"
        assert result["errors"] == {PV: "at_least_one_input"}

    def test_every_key_missing_but_an_output_is_at_least_one_input(self) -> None:
        assert self._submit({GRID_EXPORT: "sensor.export"})["errors"] == {PV: "at_least_one_input"}

    def test_an_output_alone_is_not_an_input(self) -> None:
        both = self._submit({CHARGE: "sensor.charge", GRID_EXPORT: "sensor.export"})
        assert both["errors"] == {PV: "at_least_one_input"}

    def test_no_output_at_all_is_at_least_one_output(self) -> None:
        result = self._submit({PV: ["sensor.pv"], GRID_IMPORT: "sensor.g"})
        assert result["errors"] == {CHARGE: "at_least_one_output"}

    def test_an_emptied_picker_counts_as_unset(self) -> None:
        result = self._submit({PV: ["sensor.pv"], CHARGE: "", GRID_EXPORT: None})
        assert result["errors"] == {CHARGE: "at_least_one_output"}

    def test_a_sensor_in_two_roles_is_an_error_on_the_later_field(self) -> None:
        cases = [
            ({PV: ["sensor.x"], GRID_IMPORT: "sensor.x", CHARGE: "sensor.charge"}, GRID_IMPORT),
            ({PV: ["sensor.pv"], GRID_IMPORT: "sensor.x", DISCHARGE: "sensor.x",
              CHARGE: "sensor.charge"}, DISCHARGE),
            ({PV: ["sensor.pv"], GRID_IMPORT: "sensor.g", CHARGE: "sensor.charge",
              GRID_EXPORT: "sensor.charge"}, GRID_EXPORT),
            ({PV: ["sensor.pv", "sensor.pv"], CHARGE: "sensor.charge"}, PV),
        ]  # fmt: skip
        for user_input, field in cases:
            result = self._submit(user_input)
            assert result["type"] == "form"
            assert result["errors"] == {field: "sensor_in_two_roles"}, user_input

    def test_each_picker_alone_is_optional(self) -> None:
        for inputs in ({PV: ["sensor.pv"]}, {GRID_IMPORT: "sensor.g"}, {DISCHARGE: "sensor.d"}):
            for output in ({CHARGE: "sensor.charge"}, {GRID_EXPORT: "sensor.export"}):
                assert self._submit({**inputs, **output})["type"] == "create_entry", (
                    inputs,
                    output,
                )

    def test_a_charge_or_export_sensor_without_a_device_class_is_rejected(self) -> None:
        assert self._submit({PV: ["sensor.pv"], CHARGE: "sensor.bare"})["errors"] == {
            CHARGE: "output_needs_device_class"
        }
        assert self._submit({PV: ["sensor.pv"], GRID_EXPORT: "sensor.bare"})["errors"] == {
            GRID_EXPORT: "output_needs_device_class"
        }
        mixed = self._submit(
            {PV: ["sensor.pv"], CHARGE: "sensor.charge", GRID_EXPORT: "sensor.bare"}
        )
        assert mixed["errors"] == {GRID_EXPORT: "output_needs_device_class"}

    def test_a_device_class_is_not_required_of_the_inputs(self) -> None:
        result = self._submit(
            {GRID_IMPORT: "sensor.bare", DISCHARGE: "sensor.x", CHARGE: "sensor.charge"}
        )
        assert result["type"] == "create_entry"

    def test_the_saved_options_hold_the_new_keys_and_neither_old_one(self) -> None:
        result = self._submit(
            {
                PV: ["sensor.pv"],
                GRID_IMPORT: "sensor.a",
                DISCHARGE: "sensor.b",
                CHARGE: "sensor.charge",
                GRID_EXPORT: "sensor.export",
            }
        )
        options = result["options"]
        assert options[PV] == ["sensor.pv"]
        assert [options[k] for k in COUNTERS] == [
            "sensor.a",
            "sensor.b",
            "sensor.charge",
            "sensor.export",
        ]
        assert "bms_input_sensors" not in options and "output_sensors" not in options

    def test_unset_pickers_are_not_saved_and_the_pv_list_always_is(self) -> None:
        options = self._submit({GRID_IMPORT: "sensor.g", CHARGE: "sensor.charge", DISCHARGE: ""})[
            "options"
        ]
        assert options[PV] == []
        assert set(options) >= {PV, GRID_IMPORT, CHARGE} and DISCHARGE not in options

    def test_the_options_flow_validates_the_same_way(self) -> None:
        entry = type("Entry", (), {"options": {PV: ["sensor.pv"]}})()
        flow = _config_flow.EffyOptionsFlow(entry)
        flow.hass = _hass_with_outputs()
        both = asyncio.run(
            flow.async_step_init({PV: ["sensor.x"], DISCHARGE: "sensor.x", CHARGE: "sensor.charge"})
        )
        assert both["errors"] == {DISCHARGE: "sensor_in_two_roles"}
        empty = asyncio.run(flow.async_step_init({PV: [], CHARGE: "sensor.charge"}))
        assert empty["errors"] == {PV: "at_least_one_input"}
        none = asyncio.run(flow.async_step_init({PV: ["sensor.pv"]}))
        assert none["errors"] == {CHARGE: "at_least_one_output"}
        saved = asyncio.run(flow.async_step_init({PV: ["sensor.pv"], CHARGE: "sensor.charge"}))
        assert saved["type"] == "create_entry"
        assert saved["data"] == {PV: ["sensor.pv"], CHARGE: "sensor.charge"}

    def test_the_config_flow_version_is_not_bumped(self) -> None:
        assert _config_flow.EffyConfigFlow.VERSION == 1


class TestBatteryOptionsInTheForm:
    """TASK-0023: the SoC sensor, the capacity entity and the valley hysteresis (ADR-023 D1)."""

    def _form(self, step: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        return TestSchemaHasTheFourCounterPickers()._form(step, options)

    def _by_key(self, form: dict[str, Any]) -> dict[str, Any]:
        return {marker.schema: cfg for marker, cfg in form["data_schema"].schema.items()}

    def test_both_flows_offer_the_three_options_and_all_are_optional(self) -> None:
        for step in ("user", "init"):
            keys = _keys(self._form(step))
            for key in (SOC, CAPACITY, HYSTERESIS):
                assert key in keys, (step, key)
                assert keys[key].required is False

    def test_the_soc_picker_is_one_sensor_and_the_capacity_picker_takes_three_domains(self) -> None:
        by_key = self._by_key(self._form("user"))
        assert by_key[SOC]["entity"]["domain"] == ["sensor"]
        assert "multiple" not in by_key[SOC]["entity"]
        assert by_key[CAPACITY]["entity"]["domain"] == ["sensor", "number", "input_number"]
        assert "multiple" not in by_key[CAPACITY]["entity"]

    def test_the_hysteresis_is_a_number_with_minimum_15_and_default_30(self) -> None:
        form = self._form("user")
        number = self._by_key(form)[HYSTERESIS]["number"]
        assert number["min"] == 15
        assert number["max"] == 100
        assert _keys(form)[HYSTERESIS].default == 30

    def test_the_options_flow_defaults_to_the_saved_hysteresis_and_suggests_the_pickers(
        self,
    ) -> None:
        options = {
            GRID_IMPORT: "sensor.g",
            CHARGE: "sensor.c",
            SOC: "sensor.soc",
            CAPACITY: "number.cap",
            HYSTERESIS: 45,
        }
        keys = _keys(self._form("init", options))
        assert keys[SOC].description == {"suggested_value": "sensor.soc"}
        assert keys[CAPACITY].description == {"suggested_value": "number.cap"}
        assert keys[HYSTERESIS].default == 45

    def test_an_entry_without_the_options_gets_no_suggestion_and_the_default_hysteresis(
        self,
    ) -> None:
        keys = _keys(self._form("init", {PV: ["sensor.pv"], CHARGE: "sensor.c"}))
        for key in (SOC, CAPACITY):
            assert keys[key].description is None
            assert keys[key].default is _MISSING  # a default would bring a cleared picker back
        assert keys[HYSTERESIS].default == 30


class TestBatteryOptionsValidation:
    BASE: dict[str, Any] = {GRID_IMPORT: "sensor.g", CHARGE: "sensor.charge"}

    def _submit(self, extra: dict[str, Any], step: str = "user") -> dict[str, Any]:
        user_input = {**self.BASE, **extra}
        if step == "user":
            flow = _config_flow.EffyConfigFlow()
            flow.hass = _hass_with_outputs()
            return asyncio.run(flow.async_step_user(user_input))
        entry = type("Entry", (), {"options": {}})()
        options_flow = _config_flow.EffyOptionsFlow(entry)
        options_flow.hass = _hass_with_outputs()
        return asyncio.run(options_flow.async_step_init(user_input))

    def _errors(self, extra: dict[str, Any]) -> dict[str, str]:
        result = self._submit(extra)
        return dict(result.get("errors", {})) if result["type"] == "form" else {}

    def test_soc_without_capacity_is_rejected_on_the_missing_field(self) -> None:
        assert self._errors({SOC: "sensor.soc"}) == {CAPACITY: "soc_and_capacity_together"}

    def test_capacity_without_soc_is_rejected_on_the_missing_field(self) -> None:
        assert self._errors({CAPACITY: "sensor.cap_kwh"}) == {SOC: "soc_and_capacity_together"}

    def test_an_emptied_picker_counts_as_unset(self) -> None:
        assert self._errors({SOC: "sensor.soc", CAPACITY: ""}) == {
            CAPACITY: "soc_and_capacity_together"
        }
        assert self._errors({SOC: "", CAPACITY: ""}) == {}

    def test_both_together_with_a_valid_pair_are_saved(self) -> None:
        result = self._submit({SOC: "sensor.soc", CAPACITY: "sensor.cap_kwh"})
        assert result["type"] == "create_entry"
        assert result["options"][SOC] == "sensor.soc"
        assert result["options"][CAPACITY] == "sensor.cap_kwh"

    def test_a_soc_sensor_must_have_state_class_measurement(self) -> None:
        for bad in ("sensor.soc_total", "sensor.soc_no_class"):
            errors = self._errors({SOC: bad, CAPACITY: "sensor.cap_kwh"})
            assert errors == {SOC: "soc_needs_measurement"}, bad

    def test_a_soc_sensor_without_a_state_counts_as_without_state_class(self) -> None:
        errors = self._errors({SOC: "sensor.unknown", CAPACITY: "sensor.cap_kwh"})
        assert errors == {SOC: "soc_needs_measurement"}

    def test_the_sensor_number_and_input_number_domains_are_accepted_for_the_capacity(self) -> None:
        for capacity in ("sensor.cap_kwh", "sensor.cap_wh", "number.cap", "input_number.cap"):
            assert self._errors({SOC: "sensor.soc", CAPACITY: capacity}) == {}, capacity

    def test_a_capacity_with_another_unit_is_an_error(self) -> None:
        for capacity in ("sensor.cap_mwh", "sensor.cap_percent"):
            errors = self._errors({SOC: "sensor.soc", CAPACITY: capacity})
            assert errors == {CAPACITY: "capacity_unit_not_energy"}, capacity

    def test_a_capacity_without_a_unit_or_state_is_not_an_error(self) -> None:
        # the task: an error only "when the entity has a unit"; an entity that does not exist
        # yet (or has no unit attribute) cannot be judged here
        for capacity in ("number.cap_no_unit", "number.not_yet_there"):
            assert self._errors({SOC: "sensor.soc", CAPACITY: capacity}) == {}, capacity

    def test_soc_and_capacity_take_part_in_the_one_role_per_sensor_rule(self) -> None:
        # the error sits on the later field, as for the counters
        assert self._errors({SOC: "sensor.g", CAPACITY: "sensor.cap_kwh"}) == {
            SOC: "sensor_in_two_roles"
        }
        assert self._errors({SOC: "sensor.soc", CAPACITY: "sensor.soc"}) == {
            CAPACITY: "sensor_in_two_roles"
        }
        assert self._errors(
            {PV: ["sensor.soc"], SOC: "sensor.soc", CAPACITY: "sensor.cap_kwh"}
        ) == {SOC: "sensor_in_two_roles"}
        assert self._errors({SOC: "sensor.soc", CAPACITY: "sensor.charge"}) == {
            CAPACITY: "sensor_in_two_roles"
        }

    def test_the_first_failing_check_wins(self) -> None:
        # a missing output beats a battery error; the pair check beats the state class check
        no_output = {GRID_IMPORT: "sensor.g", SOC: "sensor.soc_total"}
        flow = _config_flow.EffyConfigFlow()
        flow.hass = _hass_with_outputs()
        assert asyncio.run(flow.async_step_user(no_output))["errors"] == {
            CHARGE: "at_least_one_output"
        }
        assert self._errors({SOC: "sensor.soc_total"}) == {CAPACITY: "soc_and_capacity_together"}

    def test_the_options_flow_validates_the_battery_options_the_same_way(self) -> None:
        bad = self._submit({SOC: "sensor.soc"}, step="init")
        assert bad["errors"] == {CAPACITY: "soc_and_capacity_together"}
        ok = self._submit({SOC: "sensor.soc", CAPACITY: "number.cap", HYSTERESIS: 40}, step="init")
        assert ok["type"] == "create_entry"
        assert ok["data"][HYSTERESIS] == 40

    def test_unset_battery_options_are_not_saved(self) -> None:
        # an entry without them is unchanged: nothing is invented for the battery options
        options = self._submit({SOC: "", CAPACITY: None})["options"]
        assert SOC not in options and CAPACITY not in options and HYSTERESIS not in options

    def test_the_hysteresis_is_saved_as_submitted(self) -> None:
        options = self._submit({HYSTERESIS: 40.0})["options"]
        assert options[HYSTERESIS] == 40.0


class TestTranslations:
    def _load(self, lang: str) -> dict[str, Any]:
        import json

        path = _base / "translations" / f"{lang}.json"
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def test_en_and_de_carry_every_picker_and_every_error_in_both_flows(self) -> None:
        for lang in ("en", "de"):
            data = self._load(lang)
            for section, step in (("config", "user"), ("options", "init")):
                fields = data[section]["step"][step]["data"]
                for key in (PV, *COUNTERS):
                    assert fields[key], (lang, section, key)
                for old in ("input_sensors", "bms_input_sensors", "output_sensors"):
                    assert old not in fields
                assert data[section]["step"][step]["description"], (lang, section)
                errors = data[section]["error"]
                for error in (
                    "at_least_one_input",
                    "sensor_in_two_roles",
                    "at_least_one_output",
                    "output_needs_device_class",
                ):
                    assert errors[error], (lang, section, error)
                assert "sensor_in_both_input_lists" not in errors

    def test_en_and_de_carry_the_battery_options_and_their_errors_in_both_flows(self) -> None:
        for lang in ("en", "de"):
            data = self._load(lang)
            for section, step in (("config", "user"), ("options", "init")):
                fields = data[section]["step"][step]["data"]
                for key in (SOC, CAPACITY, HYSTERESIS):
                    assert fields[key], (lang, section, key)
                errors = data[section]["error"]
                for error in (
                    "soc_and_capacity_together",
                    "soc_needs_measurement",
                    "capacity_unit_not_energy",
                ):
                    assert errors[error], (lang, section, error)
