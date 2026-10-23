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
BMS = _const.CONF_BMS_INPUT_SENSORS
OUT = _const.CONF_OUTPUT_SENSORS
LEGACY = _inputs.LEGACY_CONF_INPUT_SENSORS


class TestKeys:
    def test_key_names_are_fixed_by_adr_019(self) -> None:
        assert PV == "pv_input_sensors"
        assert BMS == "bms_input_sensors"
        assert LEGACY == "input_sensors"

    def test_the_public_old_constant_is_gone(self) -> None:
        assert not hasattr(_const, "CONF_INPUT_SENSORS")


class TestRoleHelpers:
    def test_each_role_is_its_own_list(self) -> None:
        options = {PV: ["sensor.pv_a", "sensor.pv_b"], BMS: ["sensor.bms_bat"]}
        assert _inputs.pv_input_ids(options) == ["sensor.pv_a", "sensor.pv_b"]
        assert _inputs.bms_input_ids(options) == ["sensor.bms_bat"]

    def test_missing_keys_mean_empty_lists(self) -> None:
        assert _inputs.pv_input_ids({}) == []
        assert _inputs.bms_input_ids({PV: ["sensor.pv"]}) == []

    def test_all_inputs_is_the_mixed_pool_pv_first(self) -> None:
        options = {PV: ["sensor.pv_a", "sensor.pv_b"], BMS: ["sensor.bms_bat", "sensor.bms_grid"]}
        assert _inputs.all_input_ids(options) == [
            "sensor.pv_a",
            "sensor.pv_b",
            "sensor.bms_bat",
            "sensor.bms_grid",
        ]

    def test_a_pv_only_and_a_bms_only_system_are_possible(self) -> None:
        assert _inputs.all_input_ids({PV: ["sensor.pv"]}) == ["sensor.pv"]
        assert _inputs.all_input_ids({BMS: ["sensor.bms"]}) == ["sensor.bms"]

    def test_the_helpers_return_copies(self) -> None:
        options: dict[str, Any] = {PV: ["sensor.pv"]}
        _inputs.pv_input_ids(options).append("sensor.other")
        assert options[PV] == ["sensor.pv"]


class TestMigrateLegacyInputOptions:
    def test_returns_none_when_there_is_no_old_key(self) -> None:
        assert _inputs.migrate_legacy_input_options({PV: ["sensor.pv"], BMS: []}) is None
        assert _inputs.migrate_legacy_input_options({}) is None

    def test_the_old_list_becomes_the_pv_list_and_the_bms_list_is_empty(self) -> None:
        old = {
            LEGACY: ["sensor.pv", "sensor.bms_bat"],
            OUT: ["sensor.export"],
            "max_history_days": 7,
        }
        new = _inputs.migrate_legacy_input_options(old)
        assert new == {
            PV: ["sensor.pv", "sensor.bms_bat"],
            BMS: [],
            OUT: ["sensor.export"],
            "max_history_days": 7,
        }
        assert LEGACY not in new

    def test_the_given_options_are_not_mutated(self) -> None:
        old = {LEGACY: ["sensor.pv"], OUT: ["sensor.export"]}
        _inputs.migrate_legacy_input_options(old)
        assert old == {LEGACY: ["sensor.pv"], OUT: ["sensor.export"]}

    def test_new_keys_win_and_the_old_key_is_dropped_unread(self) -> None:
        old = {LEGACY: ["sensor.old"], PV: ["sensor.pv"], BMS: ["sensor.bms"], OUT: ["sensor.o"]}
        new = _inputs.migrate_legacy_input_options(old)
        assert new == {PV: ["sensor.pv"], BMS: ["sensor.bms"], OUT: ["sensor.o"]}

    def test_either_new_key_is_enough_to_win(self) -> None:
        only_bms = _inputs.migrate_legacy_input_options({LEGACY: ["sensor.old"], BMS: ["sensor.b"]})
        assert only_bms == {BMS: ["sensor.b"]}  # nothing is invented for the other role
        only_pv = _inputs.migrate_legacy_input_options({LEGACY: ["sensor.old"], PV: ["sensor.p"]})
        assert only_pv == {PV: ["sensor.p"]}

    def test_the_migration_reports_whether_inputs_were_moved(self) -> None:
        moved = _inputs.legacy_inputs_were_moved({LEGACY: ["sensor.old"]})
        dropped = _inputs.legacy_inputs_were_moved({LEGACY: ["sensor.old"], PV: ["sensor.pv"]})
        assert moved is True
        assert dropped is False
