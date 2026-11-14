"""
The formula signature (TASK-0045e, CAP-13, ADR-027 decision 6).

The signature is what decides whether Effy runs the full history recalculation by itself at
setup: the configured PV inputs, grid import, discharge, charge, grid export and EPS sensor.
Everything else in the options (history days, SoC, capacity, valley hysteresis, debug export)
does not change it. ``inputs.py`` imports only ``const``: no Home Assistant, no fake.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_base = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_PKG = "effy_signature_pkg"
_pkg = ModuleType(_PKG)
_pkg.__path__ = [str(_base)]
_pkg.__package__ = _PKG
sys.modules[_PKG] = _pkg


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _base / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_const = _load("const")
_inputs = _load("inputs")

PV_A, PV_B = "sensor.pv_a", "sensor.pv_b"


def _options(**changes: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        _const.CONF_PV_INPUT_SENSORS: [PV_A, PV_B],
        _const.CONF_GRID_IMPORT_SENSOR: "sensor.grid_in",
        _const.CONF_DISCHARGE_SENSOR: "sensor.discharge",
        _const.CONF_CHARGE_SENSOR: "sensor.charge",
        _const.CONF_GRID_EXPORT_SENSOR: "sensor.grid_out",
        _const.CONF_EPS_SENSOR: "sensor.eps",
        _const.CONF_MAX_HISTORY_DAYS: 30,
        _const.CONF_SOC_SENSOR: "sensor.soc",
        _const.CONF_CAPACITY_ENTITY: "sensor.capacity",
        _const.CONF_VALLEY_HYSTERESIS: 30,
        _const.CONF_DEBUG_EXPORT: False,
    }
    options.update(changes)
    return options


def _sig(**changes: Any) -> dict[str, Any]:
    signature: dict[str, Any] = _inputs.formula_signature(_options(**changes))
    return signature


class TestFormulaSignature:
    def test_it_holds_the_roles_the_additional_list_and_the_household_sensors(self) -> None:
        assert _sig() == {
            _const.CONF_PV_INPUT_SENSORS: [PV_A, PV_B],
            _const.CONF_GRID_IMPORT_SENSOR: "sensor.grid_in",
            _const.CONF_DISCHARGE_SENSOR: "sensor.discharge",
            _const.CONF_CHARGE_SENSOR: "sensor.charge",
            _const.CONF_GRID_EXPORT_SENSOR: "sensor.grid_out",
            _const.CONF_EPS_SENSOR: "sensor.eps",
            _const.CONF_ADDITIONAL_SENSORS: [],  # TASK-0047
            _const.CONF_HOUSEHOLD_IMPORT_SENSOR: None,  # TASK-0048
            _const.CONF_HOUSEHOLD_EXPORT_SENSOR: None,
        }

    def test_the_order_of_the_pv_inputs_does_not_matter(self) -> None:
        reversed_ = _sig(**{_const.CONF_PV_INPUT_SENSORS: [PV_B, PV_A]})
        assert reversed_ == _sig()

    def test_a_pv_input_listed_twice_counts_once(self) -> None:
        assert _sig(**{_const.CONF_PV_INPUT_SENSORS: [PV_A, PV_B, PV_A]}) == _sig()

    @pytest.mark.parametrize(
        "change",
        [
            {_const.CONF_PV_INPUT_SENSORS: [PV_A]},
            {_const.CONF_PV_INPUT_SENSORS: [PV_A, PV_B, "sensor.pv_c"]},
            {_const.CONF_PV_INPUT_SENSORS: []},
            {_const.CONF_PV_INPUT_SENSORS: [PV_A, "sensor.pv_c"]},  # one swapped
            {_const.CONF_GRID_IMPORT_SENSOR: "sensor.other"},
            {_const.CONF_GRID_IMPORT_SENSOR: None},
            {_const.CONF_DISCHARGE_SENSOR: "sensor.other"},
            {_const.CONF_CHARGE_SENSOR: "sensor.other"},
            {_const.CONF_GRID_EXPORT_SENSOR: "sensor.other"},
            {_const.CONF_EPS_SENSOR: "sensor.other"},
            {_const.CONF_EPS_SENSOR: None},
        ],
    )
    def test_a_change_of_a_sensor_of_the_formula_changes_the_signature(
        self, change: dict[str, Any]
    ) -> None:
        assert _sig(**change) != _sig()

    @pytest.mark.parametrize(
        "change",
        [
            {_const.CONF_MAX_HISTORY_DAYS: 90},
            {_const.CONF_SOC_SENSOR: "sensor.other_soc"},
            {_const.CONF_SOC_SENSOR: None},
            {_const.CONF_CAPACITY_ENTITY: "sensor.other_capacity"},
            {_const.CONF_VALLEY_HYSTERESIS: 45},
            {_const.CONF_DEBUG_EXPORT: True},
        ],
    )
    def test_the_other_options_do_not_change_it(self, change: dict[str, Any]) -> None:
        assert _sig(**change) == _sig()

    def test_an_unset_role_and_an_emptied_one_are_the_same(self) -> None:
        assert _sig(**{_const.CONF_EPS_SENSOR: ""}) == _sig(**{_const.CONF_EPS_SENSOR: None})
        without = _options()
        del without[_const.CONF_EPS_SENSOR]
        assert _inputs.formula_signature(without) == _sig(**{_const.CONF_EPS_SENSOR: None})

    def test_an_entry_without_a_pv_input_has_a_signature(self) -> None:
        options = _options()
        del options[_const.CONF_PV_INPUT_SENSORS]
        assert _inputs.formula_signature(options)[_const.CONF_PV_INPUT_SENSORS] == []

    def test_it_survives_the_json_store_of_the_entry_data(self) -> None:
        """The entry data is saved as JSON: what comes back must compare equal."""
        signature = _sig()
        assert json.loads(json.dumps(signature)) == signature

    def test_the_options_are_not_changed(self) -> None:
        options = _options()
        before = json.dumps(options, sort_keys=True)
        _inputs.formula_signature(options)
        assert json.dumps(options, sort_keys=True) == before


class TestSignaturePvInputs:
    def test_the_pv_inputs_of_a_stored_signature(self) -> None:
        assert _inputs.signature_pv_inputs(_sig()) == [PV_A, PV_B]

    def test_no_signature_stored_means_unknown_not_empty(self) -> None:
        assert _inputs.signature_pv_inputs(None) is None
        assert _inputs.signature_pv_inputs({}) is None

    def test_a_signature_without_a_pv_list_is_unknown(self) -> None:
        assert _inputs.signature_pv_inputs({"grid_import": "sensor.x"}) is None

    def test_an_entry_that_had_no_pv_input_has_an_empty_list(self) -> None:
        pv = _const.CONF_PV_INPUT_SENSORS
        assert _inputs.signature_pv_inputs({pv: []}) == []

    def test_what_is_not_a_signature_is_unknown(self) -> None:
        assert _inputs.signature_pv_inputs("garbage") is None
        assert _inputs.signature_pv_inputs({_const.CONF_PV_INPUT_SENSORS: "sensor.pv_a"}) is None
