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
    "EFFY_PUBLISHED_SCHEMA_VERSION": 1,
    "EFFY_RECALCULATED_FROM_ENTITY_ID": "sensor.effy_recalculated_from",
    "EFFY_STANDBY_POWER_ENTITY_ID": "sensor.effy_standby_power",
    "EFFY_ATTR_SCHEMA_VERSION": "schema_version",
    "EFFY_ATTR_MAX_HISTORY_DAYS": "max_history_days",
    "EFFY_ATTR_SENSORS": "sensors",
    "EFFY_ATTR_RESTORED_AS_OF": "restored_as_of",
    "EFFY_ATTR_RESTORED_AT": "restored_at",
    "EFFY_KEY_ENTITY_ID": "entity_id",
    "EFFY_KEY_SERIES": "series",
    "EFFY_KEY_SOURCE_ENTITY_ID": "source_entity_id",
    "EFFY_KEY_DIRECTION": "direction",
    "EFFY_KEY_ROLE": "role",
    "EFFY_KEY_AGGREGATIONS": "aggregations",
    "EFFY_SERIES_EFFECTIVE": "effective",
    "EFFY_SERIES_DERIVED_POWER": "derived_power",
    "EFFY_SERIES_SMOOTHED": "smoothed",
    "EFFY_SERIES_STANDBY_POWER": "standby_power",
    "EFFY_DIRECTION_INPUT": "input",
    "EFFY_DIRECTION_OUTPUT": "output",
    "EFFY_ROLE_PV": "pv",
    "EFFY_ROLE_BMS": "bms",
    "EFFY_AGGREGATION_MEAN": "mean",
    "EFFY_AGGREGATION_MIN": "min",
    "EFFY_AGGREGATION_MAX": "max",
    "EFFY_AGGREGATION_STATE": "state",
    "EFFY_AGGREGATIONS": ("mean", "state"),
    "EFFY_STANDBY_AGGREGATIONS": ("mean", "min", "max"),
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
        }
        assert set(exports.EFFY_AGGREGATIONS) <= names
        assert set(exports.EFFY_STANDBY_AGGREGATIONS) <= names

    def test_the_schema_version_is_a_positive_integer(self) -> None:
        version = exports.EFFY_PUBLISHED_SCHEMA_VERSION
        assert isinstance(version, int) and not isinstance(version, bool)
        assert version >= 1


class TestTheTypesMatchTheConstants:
    def test_a_published_sensor_has_exactly_the_key_constants(self) -> None:
        keys = {
            exports.EFFY_KEY_ENTITY_ID,
            exports.EFFY_KEY_SERIES,
            exports.EFFY_KEY_SOURCE_ENTITY_ID,
            exports.EFFY_KEY_DIRECTION,
            exports.EFFY_KEY_ROLE,
            exports.EFFY_KEY_AGGREGATIONS,
        }
        assert set(exports.EffyPublishedSensor.__annotations__) == keys
        assert exports.EffyPublishedSensor.__optional_keys__ == frozenset()

    def test_the_published_attributes_have_exactly_the_attribute_constants(self) -> None:
        attrs = {
            exports.EFFY_ATTR_SCHEMA_VERSION,
            exports.EFFY_ATTR_MAX_HISTORY_DAYS,
            exports.EFFY_ATTR_SENSORS,
            exports.EFFY_ATTR_RESTORED_AS_OF,
            exports.EFFY_ATTR_RESTORED_AT,
        }
        assert set(exports.EffyPublishedAttributes.__annotations__) == attrs
        # Always present; the others come and go (ADR-021 decision 6).
        assert exports.EffyPublishedAttributes.__required_keys__ == {
            exports.EFFY_ATTR_SCHEMA_VERSION,
            exports.EFFY_ATTR_MAX_HISTORY_DAYS,
        }

    def test_the_literal_types_list_exactly_the_value_constants(self) -> None:
        assert set(typing.get_args(exports.EffySeries)) == {
            exports.EFFY_SERIES_EFFECTIVE,
            exports.EFFY_SERIES_DERIVED_POWER,
            exports.EFFY_SERIES_SMOOTHED,
            exports.EFFY_SERIES_STANDBY_POWER,
        }
        assert set(typing.get_args(exports.EffyDirection)) == {
            exports.EFFY_DIRECTION_INPUT,
            exports.EFFY_DIRECTION_OUTPUT,
        }
        assert set(typing.get_args(exports.EffyRole)) == {
            exports.EFFY_ROLE_PV,
            exports.EFFY_ROLE_BMS,
        }


class TestEffyHasNoSecondCopy:
    """Effy imports the contract from the file; it never spells it out again."""

    @pytest.mark.parametrize(
        "literal",
        [
            "sensor.effy_recalculated_from",
            "sensor.effy_standby_power",
            "schema_version",
            "restored_as_of",
            "restored_at",
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
