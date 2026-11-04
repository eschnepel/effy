"""Tests for ``tests/fixture_support.py`` and the fixture runner's own logic (TASK-0037), after
shady's ``test_csv_fixture_support.py``: the comparison and the ``expected`` handling, so a
fixture that disagrees with the replay is caught, and one marked ``FAIL`` is required to."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.fixture_support import (
    compare_values,
    dumps,
    expected_outcome,
    fixture_files,
    with_rebuilds,
)
from tests.replay import debug_export
from tests.test_debug_export_fixtures import check_fixture

CURATED = Path(__file__).resolve().parent / "fixtures" / "debug_export" / "curated"


class TestCompareValues:
    def test_equal_structures_have_no_problem(self) -> None:
        value = {"a": [1, 2.5, "x"], "b": {"c": None, "d": True}}
        assert compare_values(value, json.loads(json.dumps(value))) == []

    def test_numbers_compare_with_a_relative_tolerance(self) -> None:
        assert compare_values({"v": 1.0}, {"v": 1.0000000001}) == []
        assert compare_values({"v": 1.0}, {"v": 1.1}) == ["/v: 1.0 != 1.1"]

    def test_an_integer_and_the_same_float_are_the_same(self) -> None:
        assert compare_values({"v": 3}, {"v": 3.0}) == []

    def test_a_boolean_is_not_a_number(self) -> None:
        assert compare_values({"v": True}, {"v": 1}) != []

    def test_strings_and_none_compare_exactly(self) -> None:
        assert compare_values({"s": "a"}, {"s": "b"}) == ["/s: 'a' != 'b'"]
        assert compare_values({"s": None}, {"s": 0}) != []

    def test_a_key_on_one_side_only_is_named(self) -> None:
        problems = compare_values({"a": 1}, {"b": 1})
        assert problems == [
            "/a: present in the fixture only",
            "/b: present in the regenerated result only",
        ]

    def test_lists_compare_by_position_and_length(self) -> None:
        assert compare_values({"l": [1, 2]}, {"l": [1, 3]}) == ["/l[1]: 2 != 3"]
        assert compare_values({"l": [1]}, {"l": [1, 2]}) == ["/l: length differs (1 != 2)"]

    def test_a_nested_difference_names_its_path(self) -> None:
        old = {"rebuilds": {"efficiency": {"charging_percent": 93.4}}}
        new = {"rebuilds": {"efficiency": {"charging_percent": 156.2}}}
        assert compare_values(old, new) == ["/rebuilds/efficiency/charging_percent: 93.4 != 156.2"]


class TestExpected:
    def test_the_default_is_pass(self) -> None:
        assert expected_outcome({"meta": {}}, "f") == "PASS"
        assert expected_outcome({}, "f") == "PASS"

    @pytest.mark.parametrize("value", ["fail", "FAIL", "Fail"])
    def test_fail_is_case_insensitive(self, value: str) -> None:
        assert expected_outcome({"meta": {"fixture": {"expected": value}}}, "f") == "FAIL"

    def test_anything_else_is_refused_naming_the_file(self) -> None:
        with pytest.raises(ValueError, match=r"my\.json\.gz.*PASS or FAIL.*'MAYBE'"):
            expected_outcome({"meta": {"fixture": {"expected": "maybe"}}}, "my.json.gz")


class TestStoring:
    def test_the_bytes_are_deterministic_and_load_back(self) -> None:
        document = {"a": [1, 2], "b": "ü"}
        assert dumps(document) == dumps(document)
        assert json.loads(__import__("gzip").decompress(dumps(document))) == document

    def test_with_rebuilds_copies_and_replaces_only_the_rebuilds(self) -> None:
        document = {"result": {"rows_written": 3, "rebuilds": {"old": 1}}}
        copy = with_rebuilds(document, {"new": 2})
        assert copy == {"result": {"rows_written": 3, "rebuilds": {"new": 2}}}
        assert document["result"]["rebuilds"] == {"old": 1}

    def test_every_fixture_file_is_gzip(self) -> None:
        files = fixture_files()
        assert files, "the repository ships at least one curated fixture"
        assert all(p.name.endswith(".json.gz") for p in files)
        assert all(p.read_bytes()[:2] == b"\x1f\x8b" for p in files)


class TestTheRunner:
    """The pass/fail logic on a copy of the shipped fixture, tampered in a temp folder."""

    @staticmethod
    def _copy(tmp_path: Path, mutate: Any, name: str = "case.json.gz") -> Path:
        source = next(iter(CURATED.glob("*.json.gz")))
        document = debug_export.loads(source.read_bytes())
        mutate(document)
        target = tmp_path / "curated" / name
        target.parent.mkdir()
        target.write_bytes(dumps(document))
        return target

    @pytest.mark.asyncio
    async def test_an_untouched_fixture_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await check_fixture(self._copy(tmp_path, lambda d: None), monkeypatch, tmp_path / "gen")
        assert not (tmp_path / "gen").exists()

    @pytest.mark.asyncio
    async def test_a_tampered_result_fails_and_leaves_the_regenerated_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def tamper(document: dict[str, Any]) -> None:
            document["result"]["rebuilds"]["efficiency"]["charging_percent"] = 156.2

        path = self._copy(tmp_path, tamper)
        with pytest.raises(pytest.fail.Exception, match=r"charging_percent: 156\.2 != 93\.4"):
            await check_fixture(path, monkeypatch, tmp_path / "gen")
        regenerated = debug_export.loads((tmp_path / "gen" / "curated" / path.name).read_bytes())
        assert regenerated["result"]["rebuilds"]["efficiency"]["charging_percent"] == pytest.approx(
            93.4, abs=0.1
        )

    @pytest.mark.asyncio
    async def test_a_tampered_fixture_marked_fail_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def tamper(document: dict[str, Any]) -> None:
            document["result"]["rebuilds"]["battery"]["counted_spans"] = 99
            document["meta"]["fixture"]["expected"] = "FAIL"

        await check_fixture(self._copy(tmp_path, tamper), monkeypatch, tmp_path / "gen")

    @pytest.mark.asyncio
    async def test_a_clean_fixture_marked_fail_is_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def mark(document: dict[str, Any]) -> None:
            document["meta"]["fixture"]["expected"] = "FAIL"

        with pytest.raises(AssertionError, match="marked FAIL"):
            await check_fixture(self._copy(tmp_path, mark), monkeypatch, tmp_path / "gen")
