"""
Unit tests for the input-sensor role helpers and the silent migration of the
old ``input_sensors`` key (TASK-0008, ADR-019).

``inputs.py`` imports only ``const``, so no Home Assistant stubs are needed.
Uses the same importlib-by-file-path pattern as the other test modules
(ADR-000 §6).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_base = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_pkg = ModuleType("effy_inputs_pkg")
_pkg.__path__ = [str(_base)]
_pkg.__package__ = "effy_inputs_pkg"
sys.modules["effy_inputs_pkg"] = _pkg


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"effy_inputs_pkg.{name}", _base / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "effy_inputs_pkg"
    sys.modules[f"effy_inputs_pkg.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_const = _load("const")
_inputs = _load("inputs")

PV = _const.CONF_PV_INPUT_SENSORS
GRID_IMPORT = _const.CONF_GRID_IMPORT_SENSOR
DISCHARGE = _const.CONF_DISCHARGE_SENSOR
CHARGE = _const.CONF_CHARGE_SENSOR
GRID_EXPORT = _const.CONF_GRID_EXPORT_SENSOR
SOC = _const.CONF_SOC_SENSOR
CAPACITY = _const.CONF_CAPACITY_ENTITY
HYSTERESIS = _const.CONF_VALLEY_HYSTERESIS
LEGACY = _inputs.LEGACY_CONF_INPUT_SENSORS
# the ADR-019 keys: read nowhere any more (ADR-023 decision 1), only used to show they are ignored
OLD_BMS = "bms_input_sensors"
OLD_OUT = "output_sensors"


class TestKeys:
    def test_key_names_are_fixed_by_adr_023(self) -> None:
        assert PV == "pv_input_sensors"
        assert GRID_IMPORT == "grid_import_sensor"
        assert DISCHARGE == "discharge_sensor"
        assert CHARGE == "charge_sensor"
        assert GRID_EXPORT == "grid_export_sensor"
        assert LEGACY == "input_sensors"

    def test_the_battery_option_keys_are_fixed_by_adr_023(self) -> None:
        assert SOC == "soc_sensor"
        assert CAPACITY == "capacity_entity"
        assert HYSTERESIS == "valley_hysteresis"

    def test_the_hysteresis_default_and_minimum_are_fixed_by_adr_023(self) -> None:
        # default 30, minimum 15 so that it stays above the 10-point valley band (decision 1)
        assert _const.DEFAULT_VALLEY_HYSTERESIS == 30
        assert _const.MIN_VALLEY_HYSTERESIS == 15
        assert _const.MIN_VALLEY_HYSTERESIS > 10

    def test_the_capacity_units_are_wh_and_kwh_only(self) -> None:
        assert set(_const.CAPACITY_UNITS) == {"Wh", "kWh"}

    def test_the_old_constants_are_gone(self) -> None:
        assert not hasattr(_const, "CONF_INPUT_SENSORS")
        assert not hasattr(_const, "CONF_BMS_INPUT_SENSORS")
        assert not hasattr(_const, "CONF_OUTPUT_SENSORS")


ALL_FOUR = {
    GRID_IMPORT: "sensor.grid_in",
    DISCHARGE: "sensor.discharge",
    CHARGE: "sensor.charge",
    GRID_EXPORT: "sensor.grid_out",
}


class TestRoleHelpers:
    def test_one_accessor_per_role_returns_its_entity_or_none(self) -> None:
        assert _inputs.grid_import_id(ALL_FOUR) == "sensor.grid_in"
        assert _inputs.discharge_id(ALL_FOUR) == "sensor.discharge"
        assert _inputs.charge_id(ALL_FOUR) == "sensor.charge"
        assert _inputs.grid_export_id(ALL_FOUR) == "sensor.grid_out"
        for accessor in (
            _inputs.grid_import_id,
            _inputs.discharge_id,
            _inputs.charge_id,
            _inputs.grid_export_id,
        ):
            assert accessor({}) is None
            assert accessor({GRID_IMPORT: None, DISCHARGE: "", CHARGE: None}) is None

    def test_bms_inputs_are_grid_import_then_discharge_and_outputs_charge_then_export(
        self,
    ) -> None:
        options = {**ALL_FOUR, PV: ["sensor.pv"]}
        assert _inputs.bms_input_ids(options) == ["sensor.grid_in", "sensor.discharge"]
        assert _inputs.output_ids(options) == ["sensor.charge", "sensor.grid_out"]
        assert _inputs.all_input_ids(options) == ["sensor.pv", "sensor.grid_in", "sensor.discharge"]

    def test_unset_roles_are_skipped(self) -> None:
        only = {DISCHARGE: "sensor.discharge", GRID_EXPORT: "sensor.grid_out"}
        assert _inputs.bms_input_ids(only) == ["sensor.discharge"]
        assert _inputs.output_ids(only) == ["sensor.grid_out"]
        assert _inputs.bms_input_ids({PV: ["sensor.pv"]}) == []
        assert _inputs.output_ids({}) == []

    def test_the_old_lists_are_ignored(self) -> None:
        old = {PV: ["sensor.pv"], OLD_BMS: ["sensor.bms"], OLD_OUT: ["sensor.out"]}
        assert _inputs.bms_input_ids(old) == []
        assert _inputs.output_ids(old) == []
        assert _inputs.all_input_ids(old) == ["sensor.pv"]

    def test_missing_pv_key_means_an_empty_list(self) -> None:
        assert _inputs.pv_input_ids({}) == []

    def test_all_inputs_is_the_mixed_pool_pv_first(self) -> None:
        options = {
            PV: ["sensor.pv_a", "sensor.pv_b"],
            GRID_IMPORT: "sensor.g",
            DISCHARGE: "sensor.d",
        }
        assert _inputs.all_input_ids(options) == [
            "sensor.pv_a",
            "sensor.pv_b",
            "sensor.g",
            "sensor.d",
        ]

    def test_a_pv_only_and_a_bms_only_system_are_possible(self) -> None:
        assert _inputs.all_input_ids({PV: ["sensor.pv"]}) == ["sensor.pv"]
        assert _inputs.all_input_ids({DISCHARGE: "sensor.bms"}) == ["sensor.bms"]

    def test_the_list_helpers_return_copies(self) -> None:
        options: dict[str, Any] = {PV: ["sensor.pv"], **ALL_FOUR}
        _inputs.pv_input_ids(options).append("sensor.other")
        _inputs.bms_input_ids(options).append("sensor.other")
        _inputs.output_ids(options).append("sensor.other")
        assert options[PV] == ["sensor.pv"]
        assert _inputs.bms_input_ids(options) == ["sensor.grid_in", "sensor.discharge"]
        assert _inputs.output_ids(options) == ["sensor.charge", "sensor.grid_out"]


class TestBatteryOptionAccessors:
    """TASK-0023: the SoC sensor, the capacity entity and the valley hysteresis (ADR-023 D1)."""

    def test_the_ids_are_returned_and_an_unset_or_emptied_picker_is_none(self) -> None:
        options = {SOC: "sensor.soc", CAPACITY: "number.capacity"}
        assert _inputs.soc_id(options) == "sensor.soc"
        assert _inputs.capacity_id(options) == "number.capacity"
        for accessor in (_inputs.soc_id, _inputs.capacity_id):
            assert accessor({}) is None
            assert accessor({SOC: None, CAPACITY: ""}) is None

    def test_the_hysteresis_is_the_default_when_unset(self) -> None:
        assert _inputs.valley_hysteresis({}) == 30
        assert _inputs.valley_hysteresis({HYSTERESIS: None}) == 30
        assert _inputs.valley_hysteresis({}) == _const.DEFAULT_VALLEY_HYSTERESIS

    def test_a_set_hysteresis_is_returned_as_a_number(self) -> None:
        assert _inputs.valley_hysteresis({HYSTERESIS: 45}) == 45
        assert _inputs.valley_hysteresis({HYSTERESIS: 15.0}) == 15
        assert isinstance(_inputs.valley_hysteresis({HYSTERESIS: 45}), float)

    def test_soc_and_capacity_are_neither_inputs_nor_outputs(self) -> None:
        # the waterfall's pools do not change (ADR-023 decision 12)
        plain = {PV: ["sensor.pv"], **ALL_FOUR}
        with_battery = {**plain, SOC: "sensor.soc", CAPACITY: "number.capacity", HYSTERESIS: 40}
        assert _inputs.all_input_ids(with_battery) == _inputs.all_input_ids(plain)
        assert _inputs.bms_input_ids(with_battery) == _inputs.bms_input_ids(plain)
        assert _inputs.output_ids(with_battery) == _inputs.output_ids(plain)
        assert _inputs.pv_input_ids(with_battery) == ["sensor.pv"]

    def test_an_entry_without_the_options_has_none_of_them(self) -> None:
        # "an entry without them: nothing changes" — the battery efficiency simply has no inputs
        options = {PV: ["sensor.pv"], **ALL_FOUR}
        assert _inputs.soc_id(options) is None
        assert _inputs.capacity_id(options) is None


class TestMigrateLegacyInputOptions:
    def test_returns_none_when_there_is_no_old_key(self) -> None:
        assert _inputs.migrate_legacy_input_options({PV: ["sensor.pv"], CHARGE: "sensor.c"}) is None
        assert _inputs.migrate_legacy_input_options({}) is None

    def test_the_old_list_becomes_the_pv_list_and_nothing_else_is_invented(self) -> None:
        old = {
            LEGACY: ["sensor.pv", "sensor.bms_bat"],
            OLD_OUT: ["sensor.export"],
            "max_history_days": 7,
        }
        new = _inputs.migrate_legacy_input_options(old)
        assert new == {
            PV: ["sensor.pv", "sensor.bms_bat"],
            OLD_OUT: ["sensor.export"],  # ignored, not migrated: the human assigns the counters
            "max_history_days": 7,
        }
        assert LEGACY not in new

    def test_the_given_options_are_not_mutated(self) -> None:
        old = {LEGACY: ["sensor.pv"], OLD_OUT: ["sensor.export"]}
        _inputs.migrate_legacy_input_options(old)
        assert old == {LEGACY: ["sensor.pv"], OLD_OUT: ["sensor.export"]}

    def test_new_keys_win_and_the_old_key_is_dropped_unread(self) -> None:
        old = {LEGACY: ["sensor.old"], PV: ["sensor.pv"], CHARGE: "sensor.c"}
        new = _inputs.migrate_legacy_input_options(old)
        assert new == {PV: ["sensor.pv"], CHARGE: "sensor.c"}

    def test_any_new_key_is_enough_to_win(self) -> None:
        for key, value in [(PV, ["sensor.p"]), (GRID_IMPORT, "x"), (DISCHARGE, "x"),
                           (CHARGE, "x"), (GRID_EXPORT, "x")]:  # fmt: skip
            result = _inputs.migrate_legacy_input_options({LEGACY: ["sensor.old"], key: value})
            assert result == {key: value}  # nothing is invented for the other roles

    def test_the_adr_019_keys_are_not_new_keys(self) -> None:
        # an old bms list next to the legacy key is garbage, not a decision: the legacy list moves
        result = _inputs.migrate_legacy_input_options(
            {LEGACY: ["sensor.old"], OLD_BMS: ["sensor.b"]}
        )
        assert result == {PV: ["sensor.old"], OLD_BMS: ["sensor.b"]}

    def test_the_migration_reports_whether_inputs_were_moved(self) -> None:
        moved = _inputs.legacy_inputs_were_moved({LEGACY: ["sensor.old"]})
        dropped = _inputs.legacy_inputs_were_moved({LEGACY: ["sensor.old"], PV: ["sensor.pv"]})
        assert moved is True
        assert dropped is False
