"""
The household meter sensors in the options and in the formula signature (TASK-0048, CAP-15,
ADR-029).

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
_PKG = "effy_household_options_pkg"
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

HI = _const.CONF_HOUSEHOLD_IMPORT_SENSOR
HE = _const.CONF_HOUSEHOLD_EXPORT_SENSOR
ADDITIONAL = _const.CONF_ADDITIONAL_SENSORS
IMPORT = "sensor.house_import"
EXPORT = "sensor.house_export"

ROLE_SENSORS = [
    "sensor.pv_a",
    "sensor.bms_grid_in",
    "sensor.discharge",
    "sensor.charge",
    "sensor.grid_out",
    "sensor.eps",
    "sensor.soc",
    "sensor.capacity",
]


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
    def test_the_option_keys_the_units_and_the_unique_id_marker(self) -> None:
        assert HI == "household_import_sensor"
        assert HE == "household_export_sensor"
        assert _const.HOUSEHOLD_UNITS == ("W", "kW", "Wh", "kWh")
        assert _const.HOUSEHOLD_UNIQUE_ID_MARKER == "household-"

    def test_the_marker_cannot_be_part_of_a_slug_and_differs_from_the_additional_one(self) -> None:
        assert "-" in _const.HOUSEHOLD_UNIQUE_ID_MARKER
        assert _const.HOUSEHOLD_UNIQUE_ID_MARKER != _const.ADDITIONAL_UNIQUE_ID_MARKER
        # neither is a prefix of the other, so one cleanup never matches the other's entities
        assert not _const.HOUSEHOLD_UNIQUE_ID_MARKER.startswith(_const.ADDITIONAL_UNIQUE_ID_MARKER)
        assert not _const.ADDITIONAL_UNIQUE_ID_MARKER.startswith(_const.HOUSEHOLD_UNIQUE_ID_MARKER)


class TestTheHouseholdSensors:
    def test_unset_is_none_and_an_empty_list(self) -> None:
        options = _options()
        assert _inputs.household_import_id(options) is None
        assert _inputs.household_export_id(options) is None
        assert _inputs.household_ids(options) == []

    def test_an_import_alone_and_an_import_with_an_export(self) -> None:
        only = _options(**{HI: IMPORT})
        assert _inputs.household_import_id(only) == IMPORT
        assert _inputs.household_export_id(only) is None
        assert _inputs.household_ids(only) == [IMPORT]
        both = _options(**{HI: IMPORT, HE: EXPORT})
        assert _inputs.household_ids(both) == [IMPORT, EXPORT]  # the import first

    def test_empty_values_are_unset(self) -> None:
        options = _options(**{HI: "", HE: None})
        assert _inputs.household_ids(options) == []

    def test_an_export_without_an_import_is_no_household_sensor(self) -> None:
        """ADR-029 decision 1: without a household import there is no household sensor."""
        options = _options(**{HE: EXPORT})
        assert _inputs.household_export_id(options) is None
        assert _inputs.household_ids(options) == []

    def test_the_same_sensor_as_import_and_export_counts_once_as_the_import(self) -> None:
        options = _options(**{HI: IMPORT, HE: IMPORT})
        assert _inputs.household_ids(options) == [IMPORT]

    @pytest.mark.parametrize("role_sensor", ROLE_SENSORS)
    def test_a_sensor_that_has_a_role_is_no_household_import(self, role_sensor: str) -> None:
        """The options flow refuses it; a hand-edited entry must not give two Effy series one
        statistic ID (ADR-019 amendment 2026-11-13)."""
        options = _options(**{HI: role_sensor, HE: EXPORT})
        assert _inputs.household_import_id(options) is None
        # no import, so the export is none either
        assert _inputs.household_ids(options) == []

    @pytest.mark.parametrize("role_sensor", ROLE_SENSORS)
    def test_a_sensor_that_has_a_role_is_no_household_export(self, role_sensor: str) -> None:
        options = _options(**{HI: IMPORT, HE: role_sensor})
        assert _inputs.household_export_id(options) is None
        assert _inputs.household_ids(options) == [IMPORT]

    def test_the_household_sensors_are_in_no_other_role_list(self) -> None:
        options = _options(**{HI: IMPORT, HE: EXPORT})
        for ids in (
            _inputs.all_input_ids(options),
            _inputs.output_ids(options),
            _inputs.pv_input_ids(options),
            _inputs.bms_input_ids(options),
        ):
            assert IMPORT not in ids and EXPORT not in ids


class TestTheSignedImport:
    """ADR-029 decision 2: only an import with no export sensor is read with its sign."""

    def test_an_import_alone_is_signed(self) -> None:
        assert _inputs.household_import_is_signed(_options(**{HI: IMPORT})) is True

    def test_an_import_with_an_export_is_not_signed(self) -> None:
        assert _inputs.household_import_is_signed(_options(**{HI: IMPORT, HE: EXPORT})) is False

    def test_without_a_household_import_nothing_is_signed(self) -> None:
        assert _inputs.household_import_is_signed(_options()) is False
        assert _inputs.household_import_is_signed(_options(**{HE: EXPORT})) is False

    def test_an_export_that_a_hand_edit_made_invalid_does_not_unsign_the_import(self) -> None:
        options = _options(**{HI: IMPORT, HE: "sensor.pv_a"})
        assert _inputs.household_import_is_signed(options) is True


class TestSeriesOnlyIds:
    def test_the_additional_sensors_first_then_the_household_sensors(self) -> None:
        options = _options(**{ADDITIONAL: ["sensor.extra"], HI: IMPORT, HE: EXPORT})
        assert _inputs.series_only_ids(options) == ["sensor.extra", IMPORT, EXPORT]

    def test_unset_is_empty(self) -> None:
        assert _inputs.series_only_ids(_options()) == []

    def test_a_sensor_that_is_additional_and_household_is_a_household_sensor(self) -> None:
        """A hand-edited entry: the household role wins, ``inputs`` drops it from the additional
        list at read time (the Gate 1 point of TASK-0048, as ADR-028's amendment does for any
        role), so no sensor has two entities."""
        options = _options(**{ADDITIONAL: [IMPORT, "sensor.extra", EXPORT], HI: IMPORT, HE: EXPORT})
        assert _inputs.additional_ids(options) == ["sensor.extra"]
        assert _inputs.household_ids(options) == [IMPORT, EXPORT]
        assert _inputs.series_only_ids(options) == ["sensor.extra", IMPORT, EXPORT]

    def test_an_invalid_household_sensor_leaves_the_additional_list_alone(self) -> None:
        """An export with no import is no household sensor, so it may stay additional."""
        options = _options(**{ADDITIONAL: [EXPORT], HE: EXPORT})
        assert _inputs.additional_ids(options) == [EXPORT]
        assert _inputs.series_only_ids(options) == [EXPORT]


class TestTheSensorsAreInTheFormulaSignature:
    def _sig(self, **changes: Any) -> dict[str, Any]:
        signature: dict[str, Any] = _inputs.formula_signature(_options(**changes))
        return signature

    def test_the_signature_holds_both_sensors_under_their_option_keys(self) -> None:
        signature = self._sig(**{HI: IMPORT, HE: EXPORT})
        assert signature[HI] == IMPORT and signature[HE] == EXPORT

    def test_unset_sensors_are_none(self) -> None:
        signature = self._sig()
        assert signature[HI] is None and signature[HE] is None

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            ({}, {HI: IMPORT}),  # the import added
            ({HI: IMPORT}, {HI: "sensor.other_import"}),  # the import swapped
            ({HI: IMPORT}, {HI: IMPORT, HE: EXPORT}),  # the export added
            ({HI: IMPORT, HE: EXPORT}, {HI: IMPORT, HE: "sensor.other"}),  # the export swapped
        ],
    )
    def test_adding_or_swapping_one_changes_it(
        self, before: dict[str, str], after: dict[str, str]
    ) -> None:
        assert self._sig(**before) != self._sig(**after)

    def test_removing_the_export_or_both_changes_it(self) -> None:
        both = self._sig(**{HI: IMPORT, HE: EXPORT})
        assert self._sig(**{HI: IMPORT}) != both
        assert self._sig() != both

    def test_an_export_without_an_import_does_not_change_it(self) -> None:
        """It is no household sensor (read-time rule), so nothing in the formula changes."""
        assert self._sig(**{HE: EXPORT}) == self._sig()

    def test_a_role_sensor_as_household_does_not_change_it(self) -> None:
        assert self._sig(**{HI: "sensor.pv_a"}) == self._sig()

    def test_a_household_sensor_in_the_additional_list_leaves_the_additional_key(self) -> None:
        signature = self._sig(**{ADDITIONAL: [IMPORT, "sensor.extra"], HI: IMPORT})
        assert signature[ADDITIONAL] == ["sensor.extra"] and signature[HI] == IMPORT

    def test_a_signature_stored_before_this_task_differs_so_it_recalculates_once(self) -> None:
        old = self._sig()
        del old[HI], old[HE]
        assert old != self._sig()

    def test_it_survives_the_json_store_of_the_entry_data(self) -> None:
        signature = self._sig(**{HI: IMPORT, HE: EXPORT})
        assert json.loads(json.dumps(signature)) == signature

    def test_the_pv_reader_and_the_other_keys_are_not_affected(self) -> None:
        signature = self._sig(**{HI: IMPORT})
        assert _inputs.signature_pv_inputs(signature) == ["sensor.pv_a"]
        assert signature[ADDITIONAL] == []
