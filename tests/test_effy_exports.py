"""Tests for ``effy_exports.py``, the file other projects copy (ADR-021 Amendment 2026-10-23).

The file is loaded by path, never through the package, because that is how a
downstream project meets it: as a plain module of its own namespace.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
import typing
from pathlib import Path
from types import ModuleType

import pytest

_BASE = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_SOURCE = _BASE / "effy_exports.py"


def _load_as_top_level(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(name, None)
    return mod


exports = _load_as_top_level(_SOURCE, "effy_exports_under_test")

# Every symbol of the contract and its value. A change here is a change of the
# published contract: a breaking one (a key or a meaning that existed) raises
# EFFY_PUBLISHED_SCHEMA_VERSION in the same commit (ADR-021 decision 1).
PINNED: dict[str, object] = {
    "EFFY_PUBLISHED_SCHEMA_VERSION": 2,
    "EFFY_RECALCULATED_FROM_ENTITY_ID": "sensor.effy_recalculated_from",
    "EFFY_STANDBY_POWER_ENTITY_ID": "sensor.effy_standby_power",
    "EFFY_CHARGE_EFFICIENCY_ENTITY_ID": "sensor.effy_charge_efficiency",
    "EFFY_DISCHARGE_EFFICIENCY_ENTITY_ID": "sensor.effy_discharge_efficiency",
    "EFFY_BATTERY_EFFICIENCY_ENTITY_ID": "sensor.effy_battery_efficiency",
    "EFFY_ROUNDTRIP_EFFICIENCY_ENTITY_ID": "sensor.effy_roundtrip_efficiency",
    "EFFY_PV_SUM_ENTITY_ID": "sensor.effy_effective_pv_sum_now",
    "EFFY_PV_ENERGY_TODAY_ENTITY_ID": "sensor.effy_pv_deltasum_integral_today",
    "EFFY_PV_ENERGY_TOTAL_ENTITY_ID": "sensor.effy_pv_deltasum_integral_total",
    "EFFY_PV_SHARE_ENTITY_ID": "sensor.effy_pv_share_now",
    "EFFY_HOUSEHOLD_CONSUMPTION_ENTITY_ID": "sensor.effy_household_consumption_now",
    "EFFY_HOUSEHOLD_CONSUMPTION_TODAY_ENTITY_ID": "sensor.effy_household_consumption_today",
    "EFFY_HOUSEHOLD_CONSUMPTION_TOTAL_ENTITY_ID": "sensor.effy_household_consumption_total",
    "EFFY_ATTR_SCHEMA_VERSION": "schema_version",
    "EFFY_ATTR_MAX_HISTORY_DAYS": "max_history_days",
    "EFFY_ATTR_SENSORS": "sensors",
    "EFFY_ATTR_SOURCES": "sources",
    "EFFY_ATTR_RESTORED_AS_OF": "restored_as_of",
    "EFFY_ATTR_RESTORED_AT": "restored_at",
    "EFFY_KEY_SERIES": "series",
    "EFFY_KEY_QUANTITY": "quantity",
    "EFFY_KEY_SCOPE": "scope",
    "EFFY_KEY_SPAN": "span",
    "EFFY_KEY_ROLE": "role",
    "EFFY_KEY_DIRECTION": "direction",
    "EFFY_KEY_FUNCTION": "function",
    "EFFY_KEY_AGGREGATIONS": "aggregations",
    "EFFY_KEY_SENSORS": "sensors",
    "EFFY_SERIES_EFFECTIVE": "effective",
    "EFFY_SERIES_DERIVED": "derived",
    "EFFY_SERIES_SMOOTHED": "smoothed",
    "EFFY_SERIES_STANDBY": "standby",
    "EFFY_SERIES_CHARGE": "charge",
    "EFFY_SERIES_DISCHARGE": "discharge",
    "EFFY_SERIES_BATTERY": "battery",
    "EFFY_SERIES_ROUNDTRIP": "roundtrip",
    "EFFY_SERIES_PV": "pv",
    "EFFY_SERIES_CONSUMPTION": "consumption",
    "EFFY_QUANTITY_POWER": "power",
    "EFFY_QUANTITY_ENERGY": "energy",
    "EFFY_QUANTITY_EFFICIENCY": "efficiency",
    "EFFY_QUANTITY_SHARE": "share",
    "EFFY_SCOPE_SOURCE": "source",
    "EFFY_SCOPE_COMBINED": "combined",
    "EFFY_SCOPE_SYSTEM": "system",
    "EFFY_SPAN_SLOT": "slot",
    "EFFY_SPAN_TODAY": "today",
    "EFFY_SPAN_TOTAL": "total",
    "EFFY_DIRECTION_INPUT": "input",
    "EFFY_DIRECTION_OUTPUT": "output",
    "EFFY_DIRECTION_BOTH": "both",
    "EFFY_DIRECTION_UNKNOWN": "unknown",
    "EFFY_ROLE_PV": "pv",
    "EFFY_ROLE_BMS": "bms",
    "EFFY_ROLE_EPS": "eps",
    "EFFY_ROLE_OTHER": "other",
    "EFFY_ROLE_HOUSEHOLD": "household",
    "EFFY_FUNCTION_GRID_IMPORT": "grid_import",
    "EFFY_FUNCTION_DISCHARGE": "discharge",
    "EFFY_FUNCTION_CHARGE": "charge",
    "EFFY_FUNCTION_GRID_EXPORT": "grid_export",
    "EFFY_FUNCTION_EPS": "eps",
    "EFFY_FUNCTION_UNKNOWN": "unknown",
    "EFFY_FUNCTION_PV": "pv",
    "EFFY_FUNCTION_STANDBY": "standby",
    "EFFY_FUNCTION_EFFICIENCY": "efficiency",
    "EFFY_FUNCTION_SHARE": "share",
    "EFFY_FUNCTION_HOUSEHOLD_IMPORT": "household_import",
    "EFFY_FUNCTION_HOUSEHOLD_EXPORT": "household_export",
    "EFFY_FUNCTION_CONSUMPTION": "consumption",
    "EFFY_AGGREGATION_MEAN": "mean",
    "EFFY_AGGREGATION_MIN": "min",
    "EFFY_AGGREGATION_MAX": "max",
    "EFFY_AGGREGATION_STATE": "state",
    "EFFY_AGGREGATION_SUM": "sum",
    "EFFY_AGGREGATION_CHANGE": "change",
    "EFFY_AGGREGATIONS": ("mean", "state"),
    "EFFY_STANDBY_AGGREGATIONS": ("mean", "min", "max"),
    "EFFY_EFFICIENCY_AGGREGATIONS": ("mean", "min", "max"),
    "EFFY_ENERGY_AGGREGATIONS": ("state", "sum", "change"),
}


def _constants(module: ModuleType) -> dict[str, object]:
    return {
        name: value
        for name, value in vars(module).items()
        if name.isupper() and not name.startswith("_")
    }


class TestTheFileIsCopyable:
    def test_it_imports_only_the_standard_library(self) -> None:
        tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, "a relative import cannot be copied"
                assert node.module is not None
                imported.add(node.module.split(".")[0])
        assert imported <= {"__future__", "typing"}

    def test_it_loads_as_a_top_level_module_under_any_name(self, tmp_path: Path) -> None:
        copy = tmp_path / "chargy_effy_exports.py"
        shutil.copyfile(_SOURCE, copy)
        mod = _load_as_top_level(copy, "chargy_effy_exports")
        assert _constants(mod) == _constants(exports)

    def test_it_defines_symbols_only(self) -> None:
        tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
        kinds = {type(node) for node in tree.body}
        # docstring (Expr), imports, constants and type aliases (AnnAssign, Assign),
        # and the TypedDict shapes (ClassDef): no function, no behaviour.
        assert kinds <= {
            ast.Expr,
            ast.ImportFrom,
            ast.AnnAssign,
            ast.Assign,
            ast.ClassDef,
        }

    def test_every_symbol_carries_the_effy_prefix(self) -> None:
        tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
        defined: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.append(node.target.id)
            elif isinstance(node, ast.Assign):
                defined.extend(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.ClassDef):
                defined.append(node.name)
        assert defined, "the file defines nothing"
        for name in defined:
            assert name.startswith(("EFFY_", "Effy")), name


class TestTheContractIsPinned:
    def test_every_symbol_has_its_published_value(self) -> None:
        assert _constants(exports) == PINNED

    def test_the_aggregation_tuples_use_the_aggregation_constants_only(self) -> None:
        names = {
            exports.EFFY_AGGREGATION_MEAN,
            exports.EFFY_AGGREGATION_MIN,
            exports.EFFY_AGGREGATION_MAX,
            exports.EFFY_AGGREGATION_STATE,
            exports.EFFY_AGGREGATION_SUM,
            exports.EFFY_AGGREGATION_CHANGE,
        }
        assert set(exports.EFFY_AGGREGATIONS) <= names
        assert set(exports.EFFY_STANDBY_AGGREGATIONS) <= names
        assert set(exports.EFFY_EFFICIENCY_AGGREGATIONS) <= names
        assert set(exports.EFFY_ENERGY_AGGREGATIONS) <= names

    def test_the_schema_version_is_a_positive_integer(self) -> None:
        version = exports.EFFY_PUBLISHED_SCHEMA_VERSION
        assert isinstance(version, int) and not isinstance(version, bool)
        assert version >= 1


class TestTheTypesMatchTheConstants:
    def test_a_published_group_has_exactly_the_key_constants(self) -> None:
        keys = {
            exports.EFFY_KEY_SERIES,
            exports.EFFY_KEY_QUANTITY,
            exports.EFFY_KEY_SCOPE,
            exports.EFFY_KEY_SPAN,
            exports.EFFY_KEY_ROLE,
            exports.EFFY_KEY_DIRECTION,
            exports.EFFY_KEY_FUNCTION,
            exports.EFFY_KEY_AGGREGATIONS,
            exports.EFFY_KEY_SENSORS,
        }
        assert set(exports.EffyPublishedGroup.__annotations__) == keys
        # every key is present in every group (ADR-029 decision 9): a reader never meets a
        # missing one
        assert exports.EffyPublishedGroup.__required_keys__ == keys
        # the keys are in the order the groups are sorted by, the entity IDs last
        assert list(exports.EffyPublishedGroup.__annotations__) == [
            "series",
            "quantity",
            "scope",
            "span",
            "role",
            "direction",
            "function",
            "aggregations",
            "sensors",
        ]

    def test_the_published_attributes_have_exactly_the_attribute_constants(self) -> None:
        attrs = {
            exports.EFFY_ATTR_SCHEMA_VERSION,
            exports.EFFY_ATTR_MAX_HISTORY_DAYS,
            exports.EFFY_ATTR_SENSORS,
            exports.EFFY_ATTR_SOURCES,
            exports.EFFY_ATTR_RESTORED_AS_OF,
            exports.EFFY_ATTR_RESTORED_AT,
        }
        assert set(exports.EffyPublishedAttributes.__annotations__) == attrs
        # Always present; the others come and go (ADR-021 decision 6).
        assert exports.EffyPublishedAttributes.__required_keys__ == {
            exports.EFFY_ATTR_SCHEMA_VERSION,
            exports.EFFY_ATTR_MAX_HISTORY_DAYS,
        }

    def test_the_old_per_sensor_shape_is_gone(self) -> None:
        # version 2 dropped the flat entry: its keys ``entity_id`` and ``source_entity_id``
        # are no group keys, the sources are a map of their own
        assert not hasattr(exports, "EffyPublishedSensor")
        assert not hasattr(exports, "EFFY_KEY_ENTITY_ID")
        assert not hasattr(exports, "EFFY_KEY_SOURCE_ENTITY_ID")

    def test_the_literal_types_list_exactly_the_value_constants(self) -> None:
        assert set(typing.get_args(exports.EffySeries)) == {
            exports.EFFY_SERIES_EFFECTIVE,
            exports.EFFY_SERIES_DERIVED,
            exports.EFFY_SERIES_SMOOTHED,
            exports.EFFY_SERIES_STANDBY,
            exports.EFFY_SERIES_CHARGE,
            exports.EFFY_SERIES_DISCHARGE,
            exports.EFFY_SERIES_BATTERY,
            exports.EFFY_SERIES_ROUNDTRIP,
            exports.EFFY_SERIES_PV,
            exports.EFFY_SERIES_CONSUMPTION,
        }
        assert set(typing.get_args(exports.EffyQuantity)) == {
            exports.EFFY_QUANTITY_POWER,
            exports.EFFY_QUANTITY_ENERGY,
            exports.EFFY_QUANTITY_EFFICIENCY,
            exports.EFFY_QUANTITY_SHARE,
        }
        assert set(typing.get_args(exports.EffyScope)) == {
            exports.EFFY_SCOPE_SOURCE,
            exports.EFFY_SCOPE_COMBINED,
            exports.EFFY_SCOPE_SYSTEM,
        }
        assert set(typing.get_args(exports.EffySpan)) == {
            exports.EFFY_SPAN_SLOT,
            exports.EFFY_SPAN_TODAY,
            exports.EFFY_SPAN_TOTAL,
        }
        assert set(typing.get_args(exports.EffyDirection)) == {
            exports.EFFY_DIRECTION_INPUT,
            exports.EFFY_DIRECTION_OUTPUT,
            exports.EFFY_DIRECTION_BOTH,
            exports.EFFY_DIRECTION_UNKNOWN,
        }
        assert set(typing.get_args(exports.EffyRole)) == {
            exports.EFFY_ROLE_PV,
            exports.EFFY_ROLE_BMS,
            exports.EFFY_ROLE_EPS,
            exports.EFFY_ROLE_OTHER,
            exports.EFFY_ROLE_HOUSEHOLD,
        }
        assert set(typing.get_args(exports.EffyFunction)) == {
            exports.EFFY_FUNCTION_GRID_IMPORT,
            exports.EFFY_FUNCTION_DISCHARGE,
            exports.EFFY_FUNCTION_CHARGE,
            exports.EFFY_FUNCTION_GRID_EXPORT,
            exports.EFFY_FUNCTION_EPS,
            exports.EFFY_FUNCTION_UNKNOWN,
            exports.EFFY_FUNCTION_PV,
            exports.EFFY_FUNCTION_STANDBY,
            exports.EFFY_FUNCTION_EFFICIENCY,
            exports.EFFY_FUNCTION_SHARE,
            exports.EFFY_FUNCTION_HOUSEHOLD_IMPORT,
            exports.EFFY_FUNCTION_HOUSEHOLD_EXPORT,
            exports.EFFY_FUNCTION_CONSUMPTION,
        }

    def test_the_schema_version_is_two_because_version_one_was_broken(self) -> None:
        # ADR-021 Amendment 2026-11-13: the flat list became groups, series values were
        # renamed and two keys went: a breaking change, version 2
        assert exports.EFFY_PUBLISHED_SCHEMA_VERSION == 2

    def test_the_values_of_earlier_versions_keep_their_meaning(self) -> None:
        # ADR-021 Amendment 2026-11-06: a reader that does not know eps or both ignores them
        assert exports.EFFY_ROLE_PV == "pv" and exports.EFFY_ROLE_BMS == "bms"
        assert exports.EFFY_DIRECTION_INPUT == "input" and exports.EFFY_DIRECTION_OUTPUT == "output"
        assert exports.EFFY_ROLE_EPS == exports.EFFY_FUNCTION_EPS == "eps"
        assert exports.EFFY_DIRECTION_BOTH == "both"

    def test_no_contract_value_names_the_quantity_twice_or_a_mechanism(self) -> None:
        # ADR-029 decision 8: the series says which stage, not which quantity; the words
        # delta, integral and sum are no value of the contract (an entity ID is no value)
        for name, value in _constants(exports).items():
            if name.endswith("_ENTITY_ID") or not isinstance(value, str):
                continue
            assert not value.endswith(("_power", "_efficiency")), name
            assert not any(word in value for word in ("delta", "integral", "deltasum")), name


class TestEffyHasNoSecondCopy:
    """Effy imports the contract from the file; it never spells it out again."""

    @pytest.mark.parametrize(
        "literal",
        [
            "sensor.effy_recalculated_from",
            "sensor.effy_standby_power",
            "sensor.effy_charge_efficiency",
            "sensor.effy_discharge_efficiency",
            "sensor.effy_battery_efficiency",
            "sensor.effy_roundtrip_efficiency",
            "sensor.effy_effective_pv_sum_now",
            "sensor.effy_pv_deltasum_integral_today",
            "sensor.effy_pv_deltasum_integral_total",
            "sensor.effy_pv_share_now",
            "sensor.effy_household_consumption_now",
            "sensor.effy_household_consumption_today",
            "sensor.effy_household_consumption_total",
            "schema_version",
            "restored_as_of",
            "restored_at",
            "grid_import",
            "discharge",
            "charge",
            "grid_export",
            "eps",
            "both",
            "household_import",
            "household_export",
            "combined",
        ],
    )
    def test_the_literal_is_spelled_out_only_in_effy_exports(self, literal: str) -> None:
        quoted = (f'"{literal}"', f"'{literal}'")
        offenders = [
            path.name
            for path in sorted(_BASE.glob("*.py"))
            if path != _SOURCE and any(q in path.read_text(encoding="utf-8") for q in quoted)
        ]
        assert offenders == []
