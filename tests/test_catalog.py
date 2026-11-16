"""Unit tests for the published description of the Effy sensors (ADR-021, ADR-029).

``catalog.py`` imports only ``effy_exports``, so no Home Assistant stubs are needed. Same
importlib-by-file-path pattern as the other test modules (ADR-000 §6).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.support import GROUP_KEYS

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

PV = ["sensor.pv_south", "sensor.pv_north"]
GRID_IN = "sensor.bms_grid_in"
DISCHARGE = "sensor.bms_discharge"
CHARGE = "sensor.bms_charge"
GRID_OUT = "sensor.bms_grid_out"
EPS = "sensor.eps_port"
EXTRA = "sensor.wallbox"
HOUSE_IN = "sensor.house_import"
HOUSE_OUT = "sensor.house_export"


def _describe(
    entity_id: str,
    series: str,
    source: str,
    *,
    grid_import: str | None = GRID_IN,
    discharge: str | None = DISCHARGE,
    charge: str | None = CHARGE,
    grid_export: str | None = GRID_OUT,
    eps: str | None = None,
    additional: tuple[str, ...] = (),
    house_import: str | None = None,
    house_export: str | None = None,
    signed: bool = False,
) -> dict[str, Any]:
    descriptor: dict[str, Any] = catalog.describe_source_series(
        entity_id,
        series,
        source,
        pv_input_ids=PV,
        grid_import_id=grid_import,
        discharge_id=discharge,
        charge_id=charge,
        grid_export_id=grid_export,
        eps_id=eps,
        additional_ids=additional,
        household_import_id=house_import,
        household_export_id=house_export,
        household_import_signed=signed,
    )
    return descriptor


def _attributes(descriptor: dict[str, Any]) -> list[Any]:
    return [descriptor[key] for key in GROUP_KEYS]


class TestDescribeSourceSeries:
    @pytest.mark.parametrize("series", ["effective", "derived", "smoothed"])
    def test_a_pv_input_is_an_input_of_role_and_function_pv(self, series: str) -> None:
        descriptor = _describe("sensor.effy_x", series, PV[0])
        assert _attributes(descriptor) == [
            series,
            "power",
            "source",
            "slot",
            "pv",
            "input",
            "pv",
            ["mean", "state"],
        ]
        assert descriptor["source_entity_id"] == PV[0]
        assert descriptor["entity_id"] == "sensor.effy_x"

    @pytest.mark.parametrize(
        ("source", "direction", "function"),
        [
            (GRID_IN, "input", "grid_import"),
            (DISCHARGE, "input", "discharge"),
            (CHARGE, "output", "charge"),
            (GRID_OUT, "output", "grid_export"),
        ],
    )
    def test_each_bms_counter_has_its_direction_and_function(
        self, source: str, direction: str, function: str
    ) -> None:
        descriptor = _describe("sensor.effy_x", "effective", source)
        assert descriptor["role"] == "bms"
        assert descriptor["direction"] == direction
        assert descriptor["function"] == function

    def test_a_bms_series_is_the_same_kind_of_entry_as_a_pv_series(self) -> None:
        pv = _describe("sensor.effy_a", "smoothed", PV[0])
        bms = _describe("sensor.effy_b", "smoothed", DISCHARGE)
        for key in ("series", "quantity", "scope", "span", "aggregations"):
            assert pv[key] == bms[key]

    def test_an_unconfigured_source_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="not a configured sensor"):
            _describe("sensor.effy_x", "effective", "sensor.unknown")

    def test_a_role_that_is_not_configured_matches_nothing(self) -> None:
        # a None role id must not match a source: the source is then not configured
        with pytest.raises(ValueError):
            _describe("sensor.effy_x", "effective", GRID_IN, grid_import=None)


class TestDescribeEps:
    def test_the_effective_series_is_an_input_and_the_smoothed_series_is_both(self) -> None:
        effective = _describe("sensor.effy_e", "effective", EPS, eps=EPS)
        smoothed = _describe("sensor.effy_s", "smoothed", EPS, eps=EPS)
        assert (effective["role"], effective["function"]) == ("eps", "eps")
        assert (smoothed["role"], smoothed["function"]) == ("eps", "eps")
        assert effective["direction"] == "input"
        assert smoothed["direction"] == "both"

    def test_the_other_roles_are_unchanged_when_an_eps_sensor_is_configured(self) -> None:
        assert _attributes(_describe("sensor.effy_x", "effective", PV[0], eps=EPS))[4:7] == [
            "pv",
            "input",
            "pv",
        ]
        assert _attributes(_describe("sensor.effy_x", "effective", CHARGE, eps=EPS))[4:7] == [
            "bms",
            "output",
            "charge",
        ]


class TestDescribeAdditional:
    def test_an_additional_sensor_is_role_other_and_everything_unknown(self) -> None:
        descriptor = _describe("sensor.effy_x", "smoothed", EXTRA, additional=(EXTRA,))
        assert descriptor["role"] == "other"
        assert descriptor["direction"] == "unknown"
        assert descriptor["function"] == "unknown"  # present, with the value 'unknown'


class TestDescribeHousehold:
    def test_the_import_meter_is_an_input_with_its_own_function(self) -> None:
        descriptor = _describe("sensor.effy_x", "derived", HOUSE_IN, house_import=HOUSE_IN)
        assert _attributes(descriptor)[4:7] == ["household", "input", "household_import"]

    def test_the_export_meter_is_an_output_with_its_own_function(self) -> None:
        descriptor = _describe("sensor.effy_x", "smoothed", HOUSE_OUT, house_export=HOUSE_OUT)
        assert _attributes(descriptor)[4:7] == ["household", "output", "household_export"]

    @pytest.mark.parametrize(
        ("series", "signed", "direction"),
        [
            ("smoothed", True, "both"),  # the only signed series there is
            ("smoothed", False, "input"),
            ("derived", True, "input"),  # a counter has no sign
            ("derived", False, "input"),
        ],
    )
    def test_the_import_series_is_both_exactly_when_it_is_the_signed_smoothed_one(
        self, series: str, signed: bool, direction: str
    ) -> None:
        descriptor = _describe(
            "sensor.effy_x", series, HOUSE_IN, house_import=HOUSE_IN, signed=signed
        )
        assert descriptor["direction"] == direction

    def test_the_signed_flag_does_not_touch_other_sources(self) -> None:
        descriptor = _describe("sensor.effy_x", "smoothed", PV[0], signed=True)
        assert descriptor["direction"] == "input"


class TestSystemDescriptors:
    def test_the_standby_sensor_is_an_output_of_the_system(self) -> None:
        descriptor = catalog.describe_standby("sensor.effy_standby_power")
        assert _attributes(descriptor) == [
            "standby",
            "power",
            "system",
            "slot",
            "bms",
            "output",
            "standby",
            ["mean", "min", "max"],
        ]
        assert descriptor["source_entity_id"] is None

    @pytest.mark.parametrize("series", ["charge", "discharge", "battery", "roundtrip"])
    def test_an_efficiency_sensor_is_an_output_of_the_system(self, series: str) -> None:
        descriptor = catalog.describe_efficiency(f"sensor.effy_{series}_efficiency", series)
        assert _attributes(descriptor) == [
            series,
            "efficiency",
            "system",
            "slot",
            "bms",
            "output",
            "efficiency",
            ["mean", "min", "max"],
        ]
        assert descriptor["source_entity_id"] is None

    def test_the_pv_sum_is_the_combined_effective_power_of_all_pv(self) -> None:
        descriptor = catalog.describe_pv_sum("sensor.effy_effective_pv_sum_now")
        assert _attributes(descriptor) == [
            "effective",
            "power",
            "combined",
            "slot",
            "pv",
            "input",
            "pv",
            ["mean", "state"],
        ]
        assert descriptor["source_entity_id"] is None

    def test_the_pv_share_is_a_share_of_the_whole_system(self) -> None:
        descriptor = catalog.describe_pv_share("sensor.effy_pv_share_now")
        assert _attributes(descriptor) == [
            "pv",
            "share",
            "system",
            "slot",
            "pv",
            "input",
            "share",
            ["mean", "state"],
        ]
        assert descriptor["source_entity_id"] is None

    def test_the_combined_pv_energy_is_in_no_source_list(self) -> None:
        for span in ("today", "total"):
            descriptor = catalog.describe_pv_energy("sensor.effy_x", span)
            assert _attributes(descriptor) == [
                "effective",
                "energy",
                "combined",
                span,
                "pv",
                "input",
                "pv",
                ["state", "sum", "change"],
            ]
            assert descriptor["source_entity_id"] is None

    def test_the_pv_energy_of_one_input_depends_on_that_input(self) -> None:
        descriptor = catalog.describe_pv_energy("sensor.effy_x", "today", PV[0])
        assert descriptor["scope"] == "source" and descriptor["span"] == "today"
        assert descriptor["source_entity_id"] == PV[0]

    def test_an_input_has_no_total_energy_sensor_and_no_slot_energy(self) -> None:
        with pytest.raises(ValueError):
            catalog.describe_pv_energy("sensor.effy_x", "total", PV[0])
        with pytest.raises(ValueError):
            catalog.describe_pv_energy("sensor.effy_x", "slot")

    @pytest.mark.parametrize(
        ("quantity", "span", "aggregations"),
        [
            ("power", "slot", ["mean", "state"]),
            ("energy", "today", ["state", "sum", "change"]),
            ("energy", "total", ["state", "sum", "change"]),
        ],
    )
    def test_the_household_consumption_is_an_output_of_the_system(
        self, quantity: str, span: str, aggregations: list[str]
    ) -> None:
        descriptor = catalog.describe_household_consumption("sensor.effy_x", quantity, span)
        assert _attributes(descriptor) == [
            "consumption",
            quantity,
            "system",
            span,
            "household",
            "output",
            "consumption",
            aggregations,
        ]
        assert descriptor["source_entity_id"] is None

    @pytest.mark.parametrize(
        ("quantity", "span"),
        [("power", "today"), ("energy", "slot"), ("share", "slot")],
    )
    def test_a_consumption_with_an_impossible_span_is_an_error(
        self, quantity: str, span: str
    ) -> None:
        with pytest.raises(ValueError):
            catalog.describe_household_consumption("sensor.effy_x", quantity, span)

    def test_descriptors_do_not_share_their_aggregations_list(self) -> None:
        first = catalog.describe_pv_sum("sensor.effy_a")
        second = catalog.describe_pv_sum("sensor.effy_b")
        first["aggregations"].append("max")
        assert second["aggregations"] == ["mean", "state"]


class TestPublishedAttributes:
    def test_base_attributes_carry_the_version_and_the_history_days_only(self) -> None:
        assert catalog.base_attributes(5) == {
            "schema_version": exports.EFFY_PUBLISHED_SCHEMA_VERSION,
            "max_history_days": 5,
        }

    def test_the_schema_version_is_two(self) -> None:
        assert exports.EFFY_PUBLISHED_SCHEMA_VERSION == 2
        assert catalog.base_attributes(1)["schema_version"] == 2

    def test_nothing_registered_gives_empty_groups_and_sources(self) -> None:
        attributes = catalog.published_attributes([], 7)
        assert attributes == {
            "schema_version": 2,
            "max_history_days": 7,
            "sensors": [],
            "sources": {},
        }

    def test_sensors_with_the_same_attributes_share_one_group_with_sorted_ids(self) -> None:
        descriptors = [
            _describe("sensor.effy_pv_north_smoothed", "smoothed", PV[1]),
            _describe("sensor.effy_pv_south_smoothed", "smoothed", PV[0]),
        ]
        groups = catalog.published_attributes(descriptors, 5)["sensors"]
        assert len(groups) == 1
        assert groups[0]["sensors"] == [
            "sensor.effy_pv_north_smoothed",
            "sensor.effy_pv_south_smoothed",
        ]

    def test_a_group_has_the_group_keys_in_order_and_no_entity_id_or_source(self) -> None:
        group = catalog.published_attributes([catalog.describe_pv_sum("sensor.effy_s")], 5)[
            "sensors"
        ][0]
        assert list(group) == [*GROUP_KEYS, "sensors"]
        assert group["sensors"] == ["sensor.effy_s"]

    def test_groups_are_sorted_by_the_attributes_in_the_contract_order(self) -> None:
        descriptors = [
            catalog.describe_standby("sensor.effy_standby_power"),
            catalog.describe_pv_energy("sensor.effy_total", "total"),
            catalog.describe_pv_energy("sensor.effy_today", "today"),
            catalog.describe_pv_sum("sensor.effy_sum"),
            _describe("sensor.effy_derived", "derived", PV[0]),
            _describe("sensor.effy_effective", "effective", PV[0]),
        ]
        groups = catalog.published_attributes(descriptors, 5)["sensors"]
        keys = [
            tuple(tuple(g[k]) if k == "aggregations" else g[k] for k in GROUP_KEYS) for g in groups
        ]
        assert keys == sorted(keys)
        # series first: derived, effective (source, then combined), standby
        assert [g["series"] for g in groups] == [
            "derived",
            "effective",
            "effective",
            "effective",
            "effective",
            "standby",
        ]

    def test_the_result_does_not_depend_on_the_order_of_registration(self) -> None:
        descriptors = [
            _describe("sensor.effy_b", "effective", PV[1]),
            _describe("sensor.effy_a", "effective", PV[0]),
            catalog.describe_pv_sum("sensor.effy_sum"),
            catalog.describe_standby("sensor.effy_standby_power"),
        ]
        forward = catalog.published_attributes(descriptors, 5)
        backward = catalog.published_attributes(list(reversed(descriptors)), 5)
        assert forward == backward

    def test_the_aggregations_are_part_of_the_group_key(self) -> None:
        # same attributes, different aggregations: two groups (never merged)
        mean = _describe("sensor.effy_a", "effective", PV[0])
        other = dict(mean, entity_id="sensor.effy_b", aggregations=["mean"])
        groups = catalog.published_attributes([mean, other], 5)["sensors"]
        assert [g["aggregations"] for g in groups] == [["mean"], ["mean", "state"]]

    def test_the_sources_map_each_source_to_the_sorted_sensors_that_depend_on_it(self) -> None:
        descriptors = [
            _describe("sensor.effy_pv_south_smoothed", "smoothed", PV[0]),
            _describe("sensor.effy_pv_south_effective", "effective", PV[0]),
            catalog.describe_pv_energy("sensor.effy_pv_south_today", "today", PV[0]),
            _describe("sensor.effy_pv_north_effective", "effective", PV[1]),
            catalog.describe_pv_sum("sensor.effy_sum"),
            catalog.describe_standby("sensor.effy_standby_power"),
            catalog.describe_household_consumption("sensor.effy_cons", "power", "slot"),
        ]
        sources = catalog.published_attributes(descriptors, 5)["sources"]
        assert sources == {
            PV[1]: ["sensor.effy_pv_north_effective"],
            PV[0]: [
                "sensor.effy_pv_south_effective",
                "sensor.effy_pv_south_smoothed",
                "sensor.effy_pv_south_today",
            ],
        }
        assert list(sources) == sorted(sources)

    def test_a_sensor_with_no_source_is_in_no_list_but_in_a_group(self) -> None:
        descriptors = [catalog.describe_pv_sum("sensor.effy_sum")]
        attributes = catalog.published_attributes(descriptors, 5)
        assert attributes["sources"] == {}
        assert attributes["sensors"][0]["sensors"] == ["sensor.effy_sum"]

    def test_a_source_that_has_no_sensor_has_no_key(self) -> None:
        sources = catalog.published_attributes([_describe("sensor.effy_a", "effective", PV[0])], 5)[
            "sources"
        ]
        assert PV[1] not in sources

    def test_the_result_shares_nothing_with_the_input_descriptors(self) -> None:
        descriptor = _describe("sensor.effy_a", "effective", PV[0])
        attributes = catalog.published_attributes([descriptor], 5)
        attributes["sensors"][0]["aggregations"].append("max")
        attributes["sensors"][0]["sensors"].append("sensor.effy_z")
        assert descriptor["aggregations"] == ["mean", "state"]
        assert "entity_id" in descriptor and "source_entity_id" in descriptor

    def test_a_published_group_never_holds_a_descriptors_private_keys(self) -> None:
        attributes = catalog.published_attributes(
            [_describe("sensor.effy_a", "effective", PV[0])], 5
        )
        assert set(attributes["sensors"][0]) == {*GROUP_KEYS, "sensors"}
