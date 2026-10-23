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
BMS = _const.CONF_BMS_INPUT_SENSORS
OUT = _const.CONF_OUTPUT_SENSORS


# ---------------------------------------------------------------------------
# 3. Test doubles
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, device_class: str | None) -> None:
        self.attributes = {"device_class": device_class} if device_class else {}


class _FakeStates:
    def __init__(self, states: dict[str, _FakeState]) -> None:
        self._states = states

    def get(self, entity_id: str) -> _FakeState | None:
        return self._states.get(entity_id)


class _FakeHass:
    def __init__(self, states: dict[str, _FakeState]) -> None:
        self.states = _FakeStates(states)


def _hass_with_outputs() -> _FakeHass:
    return _FakeHass({"sensor.export": _FakeState("energy")})


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


class TestSchemaHasTwoInputLists:
    def _form(self, flow_step: str) -> dict[str, Any]:
        flow = _config_flow.EffyConfigFlow()
        flow.hass = _hass_with_outputs()
        if flow_step == "user":
            return asyncio.run(flow.async_step_user())
        entry = type("Entry", (), {"options": {PV: ["sensor.pv"], BMS: ["sensor.bms"], OUT: []}})()
        options_flow = _config_flow.EffyOptionsFlow(entry)
        options_flow.hass = flow.hass
        return asyncio.run(options_flow.async_step_init())

    def test_both_flows_show_pv_and_bms_multi_selects(self) -> None:
        for step in ("user", "init"):
            keys = _keys(self._form(step))
            assert PV in keys and BMS in keys
            for key in (PV, BMS):
                assert keys[key].schema == key
        # the selectors themselves are multi-select entity pickers
        schema = self._form("user")["data_schema"].schema
        for marker, selector_cfg in schema.items():
            if marker.schema in (PV, BMS):
                assert selector_cfg["entity"]["multiple"] is True

    def test_the_old_key_is_no_longer_in_either_form(self) -> None:
        for step in ("user", "init"):
            assert "input_sensors" not in _keys(self._form(step))

    def test_neither_list_is_individually_required(self) -> None:
        keys = _keys(self._form("user"))
        assert keys[PV].required is False  # a BMS-only system is possible
        assert keys[BMS].required is False  # no BMS input is required (ADR-019)

    def test_the_options_flow_prefills_both_lists_as_suggestions(self) -> None:
        keys = _keys(self._form("init"))
        assert keys[PV].description == {"suggested_value": ["sensor.pv"]}
        assert keys[BMS].description == {"suggested_value": ["sensor.bms"]}

    def test_a_migrated_entry_shows_the_pv_list_filled_and_the_bms_list_empty(self) -> None:
        entry = type("Entry", (), {"options": {PV: ["sensor.a", "sensor.b"], BMS: [], OUT: []}})()
        flow = _config_flow.EffyOptionsFlow(entry)
        flow.hass = _hass_with_outputs()
        keys = _keys(asyncio.run(flow.async_step_init()))
        assert keys[PV].description == {"suggested_value": ["sensor.a", "sensor.b"]}
        assert keys[BMS].description == {"suggested_value": []}


class TestInputValidation:
    def _submit(self, user_input: dict[str, Any]) -> dict[str, Any]:
        flow = _config_flow.EffyConfigFlow()
        flow.hass = _hass_with_outputs()
        return asyncio.run(flow.async_step_user(user_input))

    def test_no_input_in_either_list_is_at_least_one_input(self) -> None:
        result = self._submit({PV: [], BMS: [], OUT: ["sensor.export"]})
        assert result["type"] == "form"
        assert result["errors"] == {PV: "at_least_one_input"}

    def test_both_keys_missing_is_at_least_one_input(self) -> None:
        result = self._submit({OUT: ["sensor.export"]})
        assert result["errors"] == {PV: "at_least_one_input"}

    def test_a_sensor_in_both_lists_is_its_own_error(self) -> None:
        result = self._submit(
            {PV: ["sensor.x", "sensor.pv"], BMS: ["sensor.x"], OUT: ["sensor.export"]}
        )
        assert result["type"] == "form"
        assert result["errors"] == {BMS: "sensor_in_both_input_lists"}

    def test_pv_only_is_accepted(self) -> None:
        result = self._submit({PV: ["sensor.pv"], BMS: [], OUT: ["sensor.export"]})
        assert result["type"] == "create_entry"
        assert result["options"][PV] == ["sensor.pv"]

    def test_pv_only_with_the_bms_key_missing_is_accepted(self) -> None:
        result = self._submit({PV: ["sensor.pv"], OUT: ["sensor.export"]})
        assert result["type"] == "create_entry"

    def test_bms_only_is_accepted(self) -> None:
        result = self._submit({BMS: ["sensor.bms"], OUT: ["sensor.export"]})
        assert result["type"] == "create_entry"

    def test_a_split_into_both_lists_is_accepted(self) -> None:
        result = self._submit({PV: ["sensor.pv"], BMS: ["sensor.bms"], OUT: ["sensor.export"]})
        assert result["type"] == "create_entry"

    def test_the_options_flow_validates_the_same_way(self) -> None:
        entry = type("Entry", (), {"options": {PV: ["sensor.pv"], OUT: []}})()
        flow = _config_flow.EffyOptionsFlow(entry)
        flow.hass = _hass_with_outputs()
        both = asyncio.run(
            flow.async_step_init({PV: ["sensor.x"], BMS: ["sensor.x"], OUT: ["sensor.export"]})
        )
        assert both["errors"] == {BMS: "sensor_in_both_input_lists"}
        empty = asyncio.run(flow.async_step_init({PV: [], BMS: [], OUT: ["sensor.export"]}))
        assert empty["errors"] == {PV: "at_least_one_input"}
        split = asyncio.run(
            flow.async_step_init({PV: ["sensor.pv"], BMS: ["sensor.bms"], OUT: ["sensor.export"]})
        )
        assert split["type"] == "create_entry"

    def test_the_existing_output_checks_still_apply(self) -> None:
        none = self._submit({PV: ["sensor.pv"], BMS: [], OUT: []})
        assert none["errors"] == {OUT: "at_least_one_output"}
        no_class = _config_flow.EffyConfigFlow()
        no_class.hass = _FakeHass({"sensor.export": _FakeState(None)})
        result = asyncio.run(
            no_class.async_step_user({PV: ["sensor.pv"], BMS: [], OUT: ["sensor.export"]})
        )
        assert result["errors"] == {OUT: "output_needs_device_class"}

    def test_the_config_flow_version_is_not_bumped(self) -> None:
        assert _config_flow.EffyConfigFlow.VERSION == 1


class TestTranslations:
    def _load(self, lang: str) -> dict[str, Any]:
        import json

        path = _base / "translations" / f"{lang}.json"
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def test_en_and_de_carry_the_new_fields_and_error_in_both_flows(self) -> None:
        for lang in ("en", "de"):
            data = self._load(lang)
            for section, step in (("config", "user"), ("options", "init")):
                fields = data[section]["step"][step]["data"]
                assert PV in fields and BMS in fields
                assert "input_sensors" not in fields
                assert fields[PV] and fields[BMS]
                errors = data[section]["error"]
                assert errors["sensor_in_both_input_lists"]
                assert errors["at_least_one_input"]
