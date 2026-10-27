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


class _ConfigEntryError(Exception):
    """Stand-in for HA's ConfigEntryError: keeps the translation arguments."""

    def __init__(
        self,
        *args: Any,
        translation_domain: str | None = None,
        translation_key: str | None = None,
    ) -> None:
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key


_stub("homeassistant.exceptions", ConfigEntryError=_ConfigEntryError)
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
CHARGE = _const.CONF_CHARGE_SENSOR
GRID_EXPORT = _const.CONF_GRID_EXPORT_SENSOR
GRID_IMPORT = _const.CONF_GRID_IMPORT_SENSOR
LEGACY = "input_sensors"
# the ADR-019 keys, ignored since ADR-023 decision 1
OLD_BMS = "bms_input_sensors"
OLD_OUT = "output_sensors"


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


def _refused(options: dict[str, Any]) -> tuple[_FakeHass, _FakeEntry, _ConfigEntryError]:
    """Set an entry up that must refuse; returns the hass, the entry and the error."""
    _CALLS.clear()
    _COORDINATOR_OPTIONS.clear()
    hass, entry = _FakeHass(), _FakeEntry(options)
    try:
        asyncio.run(_init.async_setup_entry(hass, entry))
    except _ConfigEntryError as err:
        return hass, entry, err
    raise AssertionError("the entry was set up, it should have refused")


class TestSilentMigration:
    """The old ``input_sensors`` key still moves into the PV list; the entry then
    refuses to set up, because no counter is assigned (ADR-023 decision 1)."""

    def test_old_list_is_written_into_the_pv_list_once(self) -> None:
        hass, entry, _err = _refused(
            {LEGACY: ["sensor.pv", "sensor.bms_bat"], OLD_OUT: ["sensor.export"]}
        )
        assert hass.config_entries.update_calls == [
            {PV: ["sensor.pv", "sensor.bms_bat"], OLD_OUT: ["sensor.export"]}
        ]
        assert dict(entry.options) == hass.config_entries.update_calls[0]
        assert LEGACY not in entry.options

    def test_one_warning_tells_the_user_to_assign_the_bms_counters(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            _refused({LEGACY: ["sensor.pv"], OLD_OUT: ["sensor.export"]})
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "PV" in message and "BMS" in message and "does not set up" in message

    def test_it_runs_first_and_then_the_entry_refuses_before_anything_is_created(self) -> None:
        _refused({LEGACY: ["sensor.pv"], OLD_OUT: ["sensor.export"]})
        assert _CALLS == ["async_update_entry"]  # no coordinator, no platforms, no listener
        assert _COORDINATOR_OPTIONS == []

    def test_no_reload_is_triggered(self) -> None:
        hass, _entry, _err = _refused({LEGACY: ["sensor.pv"], OLD_OUT: ["sensor.export"]})
        assert hass.config_entries.reloads == []

    def test_entry_identity_is_untouched(self) -> None:
        _hass, entry, _err = _refused({LEGACY: ["sensor.pv"], OLD_OUT: ["sensor.export"]})
        assert entry.entry_id == "entry-1"  # unique IDs are {entry_id}_{slug}: unchanged
        assert entry.version == 1

    def test_there_is_no_async_migrate_entry_and_no_version_bump(self) -> None:
        assert not hasattr(_init, "async_migrate_entry")

    def test_new_keys_win_and_the_old_key_is_dropped_silently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            hass, entry, ok = _setup(
                {LEGACY: ["sensor.old"], PV: ["sensor.pv"], CHARGE: "sensor.c"}
            )
        assert ok is True
        assert dict(entry.options) == {PV: ["sensor.pv"], CHARGE: "sensor.c"}
        assert hass.config_entries.update_calls == [dict(entry.options)]
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    def test_an_entry_without_the_old_key_is_not_rewritten(self) -> None:
        hass, entry, _ok = _setup({PV: ["sensor.pv"], CHARGE: "sensor.o"})
        assert hass.config_entries.update_calls == []
        assert dict(entry.options) == {PV: ["sensor.pv"], CHARGE: "sensor.o"}


class TestRefusalWithoutAnOutput:
    """An entry without a charge or a grid export counter refuses to set up (ADR-023)."""

    def test_an_entry_with_only_the_old_keys_refuses_with_a_translated_error(self) -> None:
        options = {PV: ["sensor.pv"], OLD_BMS: ["sensor.bms"], OLD_OUT: ["sensor.out"]}
        _hass, _entry, err = _refused(options)
        assert err.translation_domain == "effy"
        assert err.translation_key == "outputs_not_assigned"

    def test_it_creates_nothing_schedules_nothing_and_writes_nothing(self) -> None:
        hass, _entry, _err = _refused({PV: ["sensor.pv"], OLD_OUT: ["sensor.out"]})
        assert _CALLS == []  # no migration write, no coordinator, no platforms, no listener
        assert _COORDINATOR_OPTIONS == []
        assert hass.data == {}
        assert hass.config_entries.reloads == []

    def test_inputs_without_an_output_refuse(self) -> None:
        _refused({PV: ["sensor.pv"], GRID_IMPORT: "sensor.g"})

    def test_either_output_alone_is_enough(self) -> None:
        for options in ({CHARGE: "sensor.c"}, {GRID_EXPORT: "sensor.e"}):
            _hass, _entry, ok = _setup({PV: ["sensor.pv"], **options})
            assert ok is True

    def test_an_emptied_output_counts_as_unset(self) -> None:
        _refused({PV: ["sensor.pv"], CHARGE: "", GRID_EXPORT: None})

    def test_a_set_up_entry_registers_no_update_listener(self) -> None:
        # Home Assistant forbids one next to OptionsFlowWithReload, and a failed entry
        # would lose it anyway (the options flow reloads the entry instead)
        _setup({PV: ["sensor.pv"], CHARGE: "sensor.c"})
        assert "add_update_listener" not in _CALLS
        assert not hasattr(_init, "_async_reload_entry")

    def test_the_refusal_message_exists_in_both_languages(self) -> None:
        import json

        for lang in ("en", "de"):
            data = json.loads((_base / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
            assert data["exceptions"]["outputs_not_assigned"]["message"], lang


# ---------------------------------------------------------------------------
# TASK-0007b / ADR-020: the recorder exclusion requirement of ADR-017 is retired
# ---------------------------------------------------------------------------

_REPO = _base.parent.parent


class TestRecorderExclusionRetired:
    def test_setup_neither_probes_the_recorder_nor_touches_the_issue_registry(self) -> None:
        _RECORDER_CALLS.clear()
        _ISSUE_CALLS.clear()
        _setup({PV: ["sensor.pv"], CHARGE: "sensor.o"})
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
