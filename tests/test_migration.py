"""
Wiring tests for the silent options migration in ``async_setup_entry``
(TASK-0008, ADR-019 decision 4).

``effy/__init__.py`` is loaded with Home Assistant and the coordinator stubbed
(same importlib-by-file-path pattern as the other test modules, ADR-000 §6).
The fake config-entries manager records the *order* of the calls, because the
point of the design is that the migration is written before the update listener
is registered, so it cannot trigger a reload.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

import pytest

_base = Path(__file__).resolve().parent.parent / "custom_components" / "effy"


def _stub(name: str, **attrs: Any) -> ModuleType:
    mod = ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


_CALLS: list[str] = []
_COORDINATOR_OPTIONS: list[dict[str, Any]] = []


class _FakeCoordinator:
    def __init__(self, hass: Any, entry: Any) -> None:
        _CALLS.append("coordinator")
        _COORDINATOR_OPTIONS.append(dict(entry.options))

    def async_setup(self) -> None:
        _CALLS.append("coordinator.async_setup")


_stub("homeassistant")
_stub("homeassistant.components")
_RECORDER_CALLS: list[str] = []
_ISSUE_CALLS: list[str] = []


def _tracking_get_instance(hass: Any) -> Any:
    _RECORDER_CALLS.append("get_instance")


_stub("homeassistant.components.recorder", get_instance=_tracking_get_instance)
_stub("homeassistant.config_entries", ConfigEntry=object)
_stub("homeassistant.core", HomeAssistant=object)
_stub("homeassistant.helpers")
_ir = _stub("homeassistant.helpers.issue_registry")
_ir.IssueSeverity = type("IssueSeverity", (), {"WARNING": "warning"})  # type: ignore[attr-defined]
_ir.async_create_issue = lambda *a, **k: _ISSUE_CALLS.append("create")  # type: ignore[attr-defined]
_ir.async_delete_issue = lambda *a, **k: _ISSUE_CALLS.append("delete")  # type: ignore[attr-defined]
sys.modules["homeassistant.helpers"].issue_registry = _ir  # type: ignore[attr-defined]

_pkg = ModuleType("effy_mig_pkg")
_pkg.__path__ = [str(_base)]
_pkg.__package__ = "effy_mig_pkg"
sys.modules["effy_mig_pkg"] = _pkg
_stub("effy_mig_pkg.coordinator", EffyCoordinator=_FakeCoordinator)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"effy_mig_pkg.{name}", _base / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "effy_mig_pkg"
    sys.modules[f"effy_mig_pkg.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_const = _load("const")
_init = _load("__init__")

PV = _const.CONF_PV_INPUT_SENSORS
BMS = _const.CONF_BMS_INPUT_SENSORS
OUT = _const.CONF_OUTPUT_SENSORS
LEGACY = "input_sensors"


class _FakeEntry:
    def __init__(self, options: dict[str, Any]) -> None:
        self.entry_id = "entry-1"
        self.version = 1
        self.options: MappingProxyType[str, Any] = MappingProxyType(options)

    def add_update_listener(self, listener: Any) -> Any:
        _CALLS.append("add_update_listener")
        return lambda: None

    def async_on_unload(self, unsub: Any) -> None:
        _CALLS.append("async_on_unload")


class _FakeConfigEntries:
    def __init__(self) -> None:
        self.update_calls: list[dict[str, Any]] = []
        self.reloads: list[str] = []

    def async_update_entry(self, entry: _FakeEntry, *, options: dict[str, Any]) -> bool:
        _CALLS.append("async_update_entry")
        self.update_calls.append(dict(options))
        entry.options = MappingProxyType(dict(options))  # as HA does: replaced at once
        return True

    async def async_forward_entry_setups(self, entry: Any, platforms: Any) -> None:
        _CALLS.append("async_forward_entry_setups")

    async def async_reload(self, entry_id: str) -> None:
        self.reloads.append(entry_id)


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = _FakeConfigEntries()


def _setup(options: dict[str, Any]) -> tuple[_FakeHass, _FakeEntry, bool]:
    _CALLS.clear()
    _COORDINATOR_OPTIONS.clear()
    hass, entry = _FakeHass(), _FakeEntry(options)
    ok = asyncio.run(_init.async_setup_entry(hass, entry))
    return hass, entry, ok


class TestSilentMigration:
    def test_old_list_is_written_into_the_pv_list_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            hass, entry, ok = _setup(
                {LEGACY: ["sensor.pv", "sensor.bms_bat"], OUT: ["sensor.export"]}
            )
        assert ok is True
        assert hass.config_entries.update_calls == [
            {PV: ["sensor.pv", "sensor.bms_bat"], BMS: [], OUT: ["sensor.export"]}
        ]
        assert dict(entry.options) == hass.config_entries.update_calls[0]
        assert LEGACY not in entry.options

    def test_one_warning_tells_the_user_to_assign_the_bms_inputs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            _setup({LEGACY: ["sensor.pv"], OUT: ["sensor.export"]})
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "PV" in warnings[0].getMessage() and "BMS" in warnings[0].getMessage()

    def test_it_runs_before_the_coordinator_and_before_the_update_listener(self) -> None:
        _setup({LEGACY: ["sensor.pv"], OUT: ["sensor.export"]})
        assert _CALLS.index("async_update_entry") < _CALLS.index("coordinator")
        assert _CALLS.index("async_update_entry") < _CALLS.index("add_update_listener")
        # the coordinator already sees the migrated options
        assert _COORDINATOR_OPTIONS[0][PV] == ["sensor.pv"]
        assert LEGACY not in _COORDINATOR_OPTIONS[0]

    def test_no_reload_is_triggered(self) -> None:
        hass, _entry, _ok = _setup({LEGACY: ["sensor.pv"], OUT: ["sensor.export"]})
        assert hass.config_entries.reloads == []

    def test_entry_identity_is_untouched(self) -> None:
        _hass, entry, _ok = _setup({LEGACY: ["sensor.pv"], OUT: ["sensor.export"]})
        assert entry.entry_id == "entry-1"  # unique IDs are {entry_id}_{slug}: unchanged
        assert entry.version == 1

    def test_there_is_no_async_migrate_entry_and_no_version_bump(self) -> None:
        assert not hasattr(_init, "async_migrate_entry")

    def test_new_keys_win_and_the_old_key_is_dropped_silently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            hass, entry, _ok = _setup(
                {LEGACY: ["sensor.old"], PV: ["sensor.pv"], BMS: ["sensor.bms"], OUT: ["sensor.o"]}
            )
        assert dict(entry.options) == {PV: ["sensor.pv"], BMS: ["sensor.bms"], OUT: ["sensor.o"]}
        assert hass.config_entries.update_calls == [dict(entry.options)]
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    def test_an_entry_without_the_old_key_is_not_rewritten(self) -> None:
        hass, entry, _ok = _setup({PV: ["sensor.pv"], BMS: [], OUT: ["sensor.o"]})
        assert hass.config_entries.update_calls == []
        assert dict(entry.options) == {PV: ["sensor.pv"], BMS: [], OUT: ["sensor.o"]}


# ---------------------------------------------------------------------------
# TASK-0007b / ADR-020: the recorder exclusion requirement of ADR-017 is retired
# ---------------------------------------------------------------------------

_REPO = _base.parent.parent


class TestRecorderExclusionRetired:
    def test_setup_neither_probes_the_recorder_nor_touches_the_issue_registry(self) -> None:
        _RECORDER_CALLS.clear()
        _ISSUE_CALLS.clear()
        _setup({PV: ["sensor.pv"], BMS: [], OUT: ["sensor.o"]})
        assert _RECORDER_CALLS == []
        assert _ISSUE_CALLS == []

    def test_the_check_and_its_constants_are_gone(self) -> None:
        assert not hasattr(_init, "_async_check_recorder_exclusion")
        for name in (
            "RECORDER_EXCLUDE_ENTITY_GLOB",
            "RECORDER_EXCLUSION_PROBE_ENTITY_ID",
            "ISSUE_RECORDER_NOT_EXCLUDED",
        ):
            assert not hasattr(_const, name), name

    def test_the_repair_strings_are_gone_from_both_translations(self) -> None:
        import json

        for lang in ("en", "de"):
            data = json.loads((_base / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
            assert "recorder_not_excluded" not in data.get("issues", {}), lang
            assert "issues" not in data or data["issues"] == {}, lang

    def test_the_repair_issue_was_never_persistent(self) -> None:
        """An upgrade needs no explicit delete: the old issue was created without
        ``is_persistent``, so Home Assistant does not store it across the restart
        that installs this version. The source of the old call is in git history;
        here it is enough that nothing in the integration creates issues now."""
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(_base.glob("*.py")))
        assert "async_create_issue" not in source
        assert "async_delete_issue" not in source

    def test_the_readme_no_longer_requires_an_exclusion(self) -> None:
        readme = (_REPO / "README.md").read_text(encoding="utf-8")
        assert "## Required: exclude Effy" not in readme
        assert "Repair issue" not in readme
        # it says what to do with an existing exclusion, and the outage behaviour
        assert "entity_globs" in readme  # mentioned only to say it may be removed
        assert "may remove" in readme
        assert "outage" in readme
