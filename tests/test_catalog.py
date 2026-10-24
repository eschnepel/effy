"""Unit tests for the published description of the Effy sensors (TASK-0009, ADR-021).

``catalog.py`` imports only ``effy_exports``, so no Home Assistant stubs are needed. Same
importlib-by-file-path pattern as the other test modules (ADR-000 §6).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_base = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_pkg = ModuleType("effy_catalog_pkg")
_pkg.__path__ = [str(_base)]
_pkg.__package__ = "effy_catalog_pkg"
sys.modules["effy_catalog_pkg"] = _pkg


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"effy_catalog_pkg.{name}", _base / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "effy_catalog_pkg"
    sys.modules[f"effy_catalog_pkg.{name}"] = mod
    setattr(_pkg, name, mod)
    spec.loader.exec_module(mod)
    return mod


exports = _load("effy_exports")
catalog = _load("catalog")

PV = ["sensor.pv_south"]
BMS = ["sensor.bms_discharge"]
OUT = ["sensor.bms_charge"]


def _describe(entity_id: str, series: str, source: str) -> dict[str, object]:
    entry: dict[str, object] = catalog.describe_source_series(
        entity_id, series, source, pv_input_ids=PV, bms_input_ids=BMS, output_ids=OUT
    )
    return entry


class TestDescribeSourceSeries:
    def test_a_pv_input_is_an_input_with_role_pv(self) -> None:
        assert _describe("sensor.effy_pv_south", "effective", "sensor.pv_south") == {
            "entity_id": "sensor.effy_pv_south",
            "series": "effective",
            "source_entity_id": "sensor.pv_south",
            "direction": "input",
            "role": "pv",
            "aggregations": ["mean", "state"],
        }

    def test_a_bms_input_is_an_input_with_role_bms(self) -> None:
        entry = _describe("sensor.effy_bms_discharge_smoothed", "smoothed", "sensor.bms_discharge")
        assert (entry["direction"], entry["role"]) == ("input", "bms")

    def test_an_output_is_an_output_and_always_bms(self) -> None:
        entry = _describe("sensor.effy_bms_charge_power", "derived_power", "sensor.bms_charge")
        assert (entry["direction"], entry["role"]) == ("output", "bms")

    def test_the_keys_come_in_the_published_order(self) -> None:
        entry = _describe("sensor.effy_pv_south", "effective", "sensor.pv_south")
        assert list(entry) == [
            "entity_id",
            "series",
            "source_entity_id",
            "direction",
            "role",
            "aggregations",
        ]

    def test_there_is_no_unit_key(self) -> None:
        assert "unit" not in _describe("sensor.effy_pv_south", "effective", "sensor.pv_south")

    def test_a_source_that_is_not_configured_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="sensor.other"):
            _describe("sensor.effy_other", "effective", "sensor.other")

    def test_the_series_names_are_the_published_ones(self) -> None:
        assert (
            exports.EFFY_SERIES_EFFECTIVE,
            exports.EFFY_SERIES_DERIVED_POWER,
            exports.EFFY_SERIES_SMOOTHED,
            exports.EFFY_SERIES_STANDBY_POWER,
        ) == ("effective", "derived_power", "smoothed", "standby_power")

    def test_an_entry_has_exactly_the_contract_keys(self) -> None:
        keys = set(exports.EffyPublishedSensor.__annotations__)
        assert set(_describe("sensor.effy_pv_south", "effective", "sensor.pv_south")) == keys
        assert set(catalog.describe_standby("sensor.effy_standby_power")) == keys

    def test_the_aggregations_are_a_copy_of_the_constant(self) -> None:
        entry = _describe("sensor.effy_pv_south", "effective", "sensor.pv_south")
        assert isinstance(entry["aggregations"], list)
        entry["aggregations"].append("sum")
        assert exports.EFFY_AGGREGATIONS == ("mean", "state")
        assert _describe("sensor.effy_pv_south", "effective", "sensor.pv_south")[
            "aggregations"
        ] == [
            "mean",
            "state",
        ]


class TestDescribeStandby:
    def test_the_standby_sensor_is_an_output_with_no_source(self) -> None:
        assert catalog.describe_standby("sensor.effy_standby_power") == {
            "entity_id": "sensor.effy_standby_power",
            "series": "standby_power",
            "source_entity_id": None,
            "direction": "output",
            "role": "bms",
            "aggregations": ["mean", "min", "max"],
        }


class TestPublishedAttributes:
    def test_base_attributes_carry_the_version_and_the_history_days_but_no_list(self) -> None:
        assert catalog.base_attributes(14) == {"schema_version": 1, "max_history_days": 14}

    def test_the_list_is_sorted_by_entity_id_and_the_scalars_are_present(self) -> None:
        b = _describe("sensor.effy_b", "effective", "sensor.pv_south")
        a = catalog.describe_standby("sensor.effy_a")
        attrs = catalog.published_attributes([b, a], 28)
        assert list(attrs) == ["schema_version", "max_history_days", "sensors"]
        assert attrs["schema_version"] == 1 and attrs["max_history_days"] == 28
        assert [s["entity_id"] for s in attrs["sensors"]] == ["sensor.effy_a", "sensor.effy_b"]

    def test_an_empty_registry_gives_an_empty_list(self) -> None:
        assert catalog.published_attributes([], 28)["sensors"] == []

    def test_the_input_descriptors_are_not_shared_with_the_result(self) -> None:
        source = catalog.describe_standby("sensor.effy_a")
        attrs = catalog.published_attributes([source], 28)
        attrs["sensors"][0]["series"] = "changed"
        assert source["series"] == "standby_power"

    def test_the_schema_version_is_one(self) -> None:
        assert exports.EFFY_PUBLISHED_SCHEMA_VERSION == 1
