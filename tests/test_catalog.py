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
GRID_IN = "sensor.bms_grid_in"
DISCHARGE = "sensor.bms_discharge"
CHARGE = "sensor.bms_charge"
GRID_OUT = "sensor.bms_grid_out"


def _describe(
    entity_id: str,
    series: str,
    source: str,
    *,
    grid_import: str | None = GRID_IN,
    discharge: str | None = DISCHARGE,
    charge: str | None = CHARGE,
    grid_export: str | None = GRID_OUT,
) -> dict[str, object]:
    entry: dict[str, object] = catalog.describe_source_series(
        entity_id,
        series,
        source,
        pv_input_ids=PV,
        grid_import_id=grid_import,
        discharge_id=discharge,
        charge_id=charge,
        grid_export_id=grid_export,
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
            "function": None,
            "aggregations": ["mean", "state"],
        }

    @pytest.mark.parametrize(
        ("source", "direction", "function"),
        [
            (GRID_IN, "input", "grid_import"),
            (DISCHARGE, "input", "discharge"),
            (CHARGE, "output", "charge"),
            (GRID_OUT, "output", "grid_export"),
        ],
    )
    def test_a_bms_counter_has_the_direction_role_and_function_of_its_role(
        self, source: str, direction: str, function: str
    ) -> None:
        for series in ("effective", "derived_power", "smoothed"):
            entry = _describe("sensor.effy_x", series, source)
            assert (entry["direction"], entry["role"], entry["function"]) == (
                direction,
                "bms",
                function,
            )

    def test_a_pv_input_has_function_none_for_all_three_series(self) -> None:
        for series in ("effective", "derived_power", "smoothed"):
            entry = _describe("sensor.effy_pv_south", series, "sensor.pv_south")
            assert "function" in entry and entry["function"] is None

    def test_the_function_is_the_one_of_the_role_not_of_the_entity_name(self) -> None:
        # only the discharge counter is set: the charge role is unset, so the entity
        # that would have been the charge counter is no longer a configured source
        only = {"grid_import": None, "charge": None, "grid_export": None}
        entry = _describe("sensor.effy_d", "effective", DISCHARGE, **only)
        assert entry["function"] == "discharge"
        with pytest.raises(ValueError, match="sensor.bms_charge"):
            _describe("sensor.effy_c", "effective", CHARGE, **only)

    def test_the_function_constants_are_the_ones_published(self) -> None:
        assert (
            exports.EFFY_FUNCTION_GRID_IMPORT,
            exports.EFFY_FUNCTION_DISCHARGE,
            exports.EFFY_FUNCTION_CHARGE,
            exports.EFFY_FUNCTION_GRID_EXPORT,
        ) == ("grid_import", "discharge", "charge", "grid_export")

    def test_the_keys_come_in_the_published_order(self) -> None:
        entry = _describe("sensor.effy_pv_south", "effective", "sensor.pv_south")
        assert list(entry) == [
            "entity_id",
            "series",
            "source_entity_id",
            "direction",
            "role",
            "function",
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
        # the standby entry has no function (ADR-021 Amendment 2026-10-23)
        assert set(catalog.describe_standby("sensor.effy_standby_power")) == keys - {"function"}

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
