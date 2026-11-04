"""Tests for ``utils/import_debug_export.py`` (TASK-0037): what the anonymizer replaces, what it
keeps, that it refuses to leave an original name behind, and that it is deterministic."""

from __future__ import annotations

import gzip
import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

_path = Path(__file__).resolve().parent.parent / "utils" / "import_debug_export.py"
_spec = importlib.util.spec_from_file_location("effy_import_debug_export", _path)
assert _spec is not None and _spec.loader is not None
tool: Any = importlib.util.module_from_spec(_spec)
sys.modules["effy_import_debug_export"] = tool
_spec.loader.exec_module(tool)

PV, GI, SOC = "sensor.acme_roof_west_power", "sensor.acme_grid_in_total", "sensor.acme_soc"


def _document() -> dict[str, Any]:
    return {
        "format_version": 1,
        "meta": {"time_zone": "Europe/Berlin", "written_at": "2026-11-04T08:17:40.814108+00:00"},
        "config": {
            "pv_input_sensors": [PV],
            "grid_import_sensor": GI,
            "soc_sensor": SOC,
            "max_history_days": 28.0,
            "debug_export": True,
            "owner": "Jane Doe",
        },
        "inputs": {"ran_at": "2026-11-04T08:17:36.728261+00:00", "energy_reading_cache": {}},
        "result": {"rebuilds": {}},
        "cache": {
            "raw": {
                GI.replace("sensor.", "sensor."): {"events": [["2026-11-03T22:00:00+00:00", "1.5"]]}
            },
            "stats": {
                "sensor.effy_acme_roof_west_power_smoothed": {
                    "rows": [
                        {"start": "2026-11-03T22:00:00+00:00", "end": 1793743500.0, "mean": 3.0}
                    ]
                },
                "sensor.effy_standby_power": {"rows": []},
                "sensor.someone_elses_thing": {"rows": []},
            },
            "observed": {
                "statistics_units": {f"{GI},{PV},{SOC}": {GI: "kWh"}},
                "unchanged_rows": {"sensor.effy_x|0123456789012345678901234567890123456789": {}},
                "units": {GI: "kWh"},
            },
        },
    }


class TestEntityMap:
    def test_roles_get_generic_names_and_pv_strings_are_numbered(self) -> None:
        document = _document()
        document["config"]["pv_input_sensors"] = [PV, "sensor.acme_roof_east_power"]
        mapping = tool.entity_map(document["config"])
        assert mapping == {
            PV: "sensor.pv_string_1_power",
            "sensor.acme_roof_east_power": "sensor.pv_string_2_power",
            GI: "sensor.grid_import_energy",
            SOC: "sensor.battery_soc",
        }

    def test_a_number_entity_keeps_its_domain(self) -> None:
        mapping = tool.entity_map({"capacity_entity": "number.acme_capacity"})
        assert mapping == {"number.acme_capacity": "number.battery_capacity"}


class TestAnonymize:
    def _run(self, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return tool.anonymize(_document(), **kwargs)

    def test_no_original_name_is_left_anywhere(self) -> None:
        out, _report = self._run()
        text = json.dumps(out)
        for word in ("acme", "Berlin", "Jane", "someone_elses"):
            assert word not in text, word

    def test_the_derived_statistics_follow_their_source(self) -> None:
        out, _report = self._run()
        assert "sensor.effy_pv_string_1_power_smoothed" in out["cache"]["stats"]

    def test_effys_own_statistic_stays_and_a_foreign_entity_becomes_numbered(self) -> None:
        out, report = self._run()
        assert "sensor.effy_standby_power" in out["cache"]["stats"]
        assert "sensor.entity_1" in out["cache"]["stats"]
        assert report["entities"]["sensor.someone_elses_thing"] == "sensor.entity_1"

    def test_the_times_move_by_whole_days_to_the_anchor(self) -> None:
        out, report = self._run(anchor=date(2026, 3, 15))
        assert out["inputs"]["ran_at"] == "2026-03-15T08:17:36.728261+00:00"
        assert report["date_shift_days"] == -234
        event = out["cache"]["raw"]["sensor.grid_import_energy"]["events"][0]
        assert event[0] == "2026-03-14T22:00:00+00:00"  # the time of day is kept
        assert out["cache"]["stats"]["sensor.effy_pv_string_1_power_smoothed"]["rows"][0][
            "end"
        ] == pytest.approx(1793743500.0 - 234 * 86400)

    def test_without_an_anchor_the_times_are_kept(self) -> None:
        out, report = self._run(anchor=None)
        assert out["inputs"]["ran_at"] == _document()["inputs"]["ran_at"]
        assert report["date_shift_days"] == 0

    def test_the_time_zone_is_generic_and_the_fixture_marked(self) -> None:
        out, _report = self._run()
        assert out["meta"]["time_zone"] == "UTC"
        assert out["meta"]["fixture"] == {
            "anonymized": True,
            "date_shift_days": -234,
            "expected": "PASS",
        }

    def test_numbers_and_flags_of_the_config_are_kept_and_other_text_is_redacted(self) -> None:
        out, report = self._run()
        assert out["config"]["max_history_days"] == 28.0
        assert out["config"]["debug_export"] is True
        assert out["config"]["owner"] == "anonymized"
        assert report["redacted_config_keys"] == ["owner"]

    def test_the_statistics_units_key_is_sorted_again_and_unchanged_rows_dropped(self) -> None:
        out, _report = self._run()
        [key] = out["cache"]["observed"]["statistics_units"]
        assert key == ",".join(sorted(key.split(",")))
        assert out["cache"]["observed"]["unchanged_rows"] == {}

    def test_the_input_is_not_changed(self) -> None:
        document = _document()
        tool.anonymize(document)
        assert document == _document()

    def test_a_configuration_without_a_known_role_is_refused(self) -> None:
        document = _document()
        document["config"] = {"max_history_days": 28.0}
        with pytest.raises(tool.ImportRefused, match="no sensor"):
            tool.anonymize(document)

    def test_a_role_name_is_replaced_in_every_string_not_only_in_ids(self) -> None:
        document = _document()
        document["config"]["pv_input_sensors"] = [PV]
        document["cache"]["raw"][GI]["note"] = "sensor.acme_roof_west_power"
        # the role's object id is replaced everywhere, so only a name it cannot see is a leak
        out, _report = tool.anonymize(document)
        assert "acme_roof" not in json.dumps(out)


class TestFiles:
    def test_the_same_document_gives_the_same_bytes(self) -> None:
        out, _report = tool.anonymize(_document())
        assert tool.fixture_bytes(out) == tool.fixture_bytes(json.loads(json.dumps(out)))

    def test_the_bytes_are_gzip_of_compact_ascii_json(self) -> None:
        raw = gzip.decompress(tool.fixture_bytes({"a": "ü", "b": [1, 2]}))
        assert raw == b'{"a":"\\u00fc","b":[1,2]}'

    def test_load_reads_plain_json_and_gzip_and_refuses_other_files(self) -> None:
        payload = json.dumps(_document()).encode()
        assert tool.load(payload) == tool.load(gzip.compress(payload)) == _document()
        with pytest.raises(tool.ImportRefused, match="not a debug export"):
            tool.load(b'{"x": 1}')

    def test_the_command_writes_the_fixture_and_refuses_to_overwrite(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "debug.json"
        source.write_text(json.dumps(_document()))
        args = [str(source), "--name", "case", "--out", str(tmp_path / "out")]
        assert tool.main(args) == 0
        written = tmp_path / "out" / "case.json.gz"
        assert re.search(r"wrote .*case\.json\.gz", capsys.readouterr().out)
        assert json.loads(gzip.decompress(written.read_bytes()))["meta"]["fixture"]["anonymized"]
        with pytest.raises(SystemExit):
            tool.main(args)
        assert tool.main([*args, "--force"]) == 0

    def test_a_bad_name_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            tool.main([str(tmp_path / "x.json"), "--name", "../evil"])

    def test_the_shipped_fixture_holds_no_original_names(self) -> None:
        for path in (_path.parent.parent / "tests" / "fixtures" / "debug_export").rglob(
            "*.json.gz"
        ):
            text = gzip.decompress(path.read_bytes()).decode()
            assert not re.search(r"solakon|netz|batterie|leistung|Berlin|2026-1[01]", text), path
            found = set(re.findall(r"\b(?:sensor|number)\.[a-z0-9_]+", text))
            generic = re.compile(
                r"(effy_)?(pv_string_\d_power|grid_(import|export)_energy|"
                r"battery_(charge|discharge)_energy|battery_soc|battery_capacity)"
                r"(_power|_smoothed)?|effy_standby_power"
            )
            assert all(generic.fullmatch(name.split(".", 1)[1]) for name in found), found
