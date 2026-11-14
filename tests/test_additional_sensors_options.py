"""
The additional sensors in the options and in the formula signature (TASK-0047, CAP-14, ADR-028).

``inputs.py`` imports only ``const``: no Home Assistant, no fake.
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
_PKG = "effy_additional_options_pkg"
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

ADDITIONAL = _const.CONF_ADDITIONAL_SENSORS
GRID = "sensor.house_grid_import"


def _options(**changes: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        _const.CONF_PV_INPUT_SENSORS: ["sensor.pv_a"],
        _const.CONF_GRID_IMPORT_SENSOR: "sensor.bms_grid_in",
        _const.CONF_DISCHARGE_SENSOR: "sensor.discharge",
        _const.CONF_CHARGE_SENSOR: "sensor.charge",
        _const.CONF_GRID_EXPORT_SENSOR: "sensor.grid_out",
        _const.CONF_EPS_SENSOR: "sensor.eps",
        _const.CONF_SOC_SENSOR: "sensor.soc",
        _const.CONF_CAPACITY_ENTITY: "sensor.capacity",
    }
    options.update(changes)
    return options


class TestTheConstants:
    def test_the_option_key_and_the_accepted_units(self) -> None:
        assert ADDITIONAL == "additional_sensors"
        assert _const.ADDITIONAL_UNITS == ("W", "kW", "Wh", "kWh")

    def test_the_unique_id_marker_cannot_be_part_of_a_slug(self) -> None:
        # a slug holds letters, digits and underscores; the marker holds a hyphen
        assert "-" in _const.ADDITIONAL_UNIQUE_ID_MARKER


class TestAdditionalIds:
    def test_unset_is_an_empty_list(self) -> None:
        assert _inputs.additional_ids(_options()) == []

    def test_the_configured_list_in_its_order_as_a_copy(self) -> None:
        saved = [GRID, "sensor.house"]
        options = _options(**{ADDITIONAL: saved})
        result = _inputs.additional_ids(options)
        assert result == saved and result is not saved

    def test_a_sensor_listed_twice_counts_once(self) -> None:
        assert _inputs.additional_ids(_options(**{ADDITIONAL: [GRID, "sensor.b", GRID]})) == [
            GRID,
            "sensor.b",
        ]

    def test_empty_entries_are_ignored(self) -> None:
        assert _inputs.additional_ids(_options(**{ADDITIONAL: [GRID, "", None]})) == [GRID]

    @pytest.mark.parametrize(
        "role_sensor",
        [
            "sensor.pv_a",
            "sensor.bms_grid_in",
            "sensor.discharge",
            "sensor.charge",
            "sensor.grid_out",
            "sensor.eps",
            "sensor.soc",
            "sensor.capacity",
        ],
    )
    def test_a_sensor_that_has_a_role_is_dropped_whatever_the_options_say(
        self, role_sensor: str
    ) -> None:
        """The options flow refuses it; a hand-edited entry must not give two Effy entities
        one unique ID (ADR-028 decision 3)."""
        assert _inputs.additional_ids(_options(**{ADDITIONAL: [role_sensor, GRID]})) == [GRID]

    def test_the_additional_sensors_are_in_no_other_role_list(self) -> None:
        options = _options(**{ADDITIONAL: [GRID]})
        assert GRID not in _inputs.all_input_ids(options)
        assert GRID not in _inputs.output_ids(options)
        assert GRID not in _inputs.pv_input_ids(options)
        assert GRID not in _inputs.bms_input_ids(options)


class TestTheListIsPartOfTheFormulaSignature:
    def _sig(self, **changes: Any) -> dict[str, Any]:
        signature: dict[str, Any] = _inputs.formula_signature(_options(**changes))
        return signature

    def test_the_signature_holds_the_sorted_list_under_the_option_key(self) -> None:
        assert self._sig(**{ADDITIONAL: [GRID, "sensor.b"]})[ADDITIONAL] == ["sensor.b", GRID]

    def test_no_list_is_an_empty_list(self) -> None:
        assert self._sig()[ADDITIONAL] == []

    def test_the_order_and_a_duplicate_do_not_matter(self) -> None:
        one = self._sig(**{ADDITIONAL: [GRID, "sensor.b"]})
        assert self._sig(**{ADDITIONAL: ["sensor.b", GRID, GRID]}) == one

    @pytest.mark.parametrize(
        "changed",
        [
            [GRID],  # one removed
            [GRID, "sensor.b", "sensor.c"],  # one added
            [GRID, "sensor.c"],  # one swapped
            [],  # all removed
        ],
    )
    def test_adding_removing_or_swapping_one_changes_it(self, changed: list[str]) -> None:
        base = self._sig(**{ADDITIONAL: [GRID, "sensor.b"]})
        assert self._sig(**{ADDITIONAL: changed}) != base

    def test_the_old_keys_of_the_signature_are_unchanged_and_the_household_keys_added(self) -> None:
        signature = self._sig(**{ADDITIONAL: [GRID]})
        assert set(signature) == {
            _const.CONF_PV_INPUT_SENSORS,
            _const.CONF_GRID_IMPORT_SENSOR,
            _const.CONF_DISCHARGE_SENSOR,
            _const.CONF_CHARGE_SENSOR,
            _const.CONF_GRID_EXPORT_SENSOR,
            _const.CONF_EPS_SENSOR,
            ADDITIONAL,
            _const.CONF_HOUSEHOLD_IMPORT_SENSOR,  # TASK-0048
            _const.CONF_HOUSEHOLD_EXPORT_SENSOR,
        }

    def test_a_signature_stored_before_this_task_differs_so_it_recalculates_once(self) -> None:
        old = self._sig()
        del old[ADDITIONAL]
        assert old != self._sig()

    def test_a_role_sensor_in_the_list_does_not_change_the_signature(self) -> None:
        assert self._sig(**{ADDITIONAL: ["sensor.pv_a"]}) == self._sig()

    def test_it_survives_the_json_store_of_the_entry_data(self) -> None:
        signature = self._sig(**{ADDITIONAL: [GRID]})
        assert json.loads(json.dumps(signature)) == signature

    def test_the_pv_reader_of_a_signature_is_not_affected(self) -> None:
        assert _inputs.signature_pv_inputs(self._sig(**{ADDITIONAL: [GRID]})) == ["sensor.pv_a"]
