"""
The diagnostic "Re-calculate History" button (TASK-0028, slice 7).

No test loaded ``button.py`` before. Like ``test_standby_sensor`` this loads the real
file under a private package name with Home Assistant stubbed only while it loads,
and a fake coordinator that records what the button asks of it.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_BASE = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_PKG = "effy_button_under_test"


class _ButtonEntity:
    pass


class _ButtonEntityDescription:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _EntityCategory:
    DIAGNOSTIC = "diagnostic"


def _load() -> ModuleType:
    stub_names = [
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.button",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
    ]
    module_names = [f"{_PKG}.{n}" for n in ("const", "coordinator", "button")]
    touched = [*stub_names, _PKG, *module_names]
    saved = {name: sys.modules.get(name) for name in touched}

    def stub(name: str, **attrs: Any) -> None:
        mod = ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod

    try:
        stub("homeassistant")
        stub("homeassistant.components")
        stub(
            "homeassistant.components.button",
            ButtonEntity=_ButtonEntity,
            ButtonEntityDescription=_ButtonEntityDescription,
        )
        stub("homeassistant.config_entries", ConfigEntry=object)
        stub("homeassistant.core", HomeAssistant=object)
        stub("homeassistant.helpers")
        stub("homeassistant.helpers.entity", DeviceInfo=dict, EntityCategory=_EntityCategory)
        stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)

        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(_BASE)]
        pkg.__package__ = _PKG
        sys.modules[_PKG] = pkg

        coordinator = ModuleType(f"{_PKG}.coordinator")
        coordinator.EffyCoordinator = object  # type: ignore[attr-defined]
        coordinator.__package__ = _PKG
        sys.modules[f"{_PKG}.coordinator"] = coordinator
        pkg.coordinator = coordinator  # type: ignore[attr-defined]

        loaded: dict[str, ModuleType] = {}
        for name in ("const", "button"):
            spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _BASE / f"{name}.py")
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            mod.__package__ = _PKG
            sys.modules[f"{_PKG}.{name}"] = mod
            setattr(pkg, name, mod)
            spec.loader.exec_module(mod)
            loaded[name] = mod
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return loaded["button"]


_button = _load()
DOMAIN = "effy"


class _Entry:
    entry_id = "entry1"


class _Coordinator:
    def __init__(self, written: int = 7, error: Exception | None = None) -> None:
        self.written = written
        self.error = error
        self.calls = 0

    async def async_recalculate_history_and_report(self) -> int:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.written


def _hass(coordinator: Any | None) -> Any:
    data: dict[str, Any] = {DOMAIN: {}}
    if coordinator is not None:
        data[DOMAIN]["entry1"] = coordinator
    return SimpleNamespace(data=data)


def _press_button(hass: Any) -> Any:
    return _button.EffyRecalculateButton(hass, _Entry(), _button.RECALCULATE_BUTTON)


class TestSetup:
    async def test_the_platform_adds_the_one_button_without_updating_it_first(self) -> None:
        added: list[tuple[list[Any], dict[str, Any]]] = []

        def add_entities(entities: list[Any], **kwargs: Any) -> None:
            added.append((entities, kwargs))

        await _button.async_setup_entry(_hass(None), _Entry(), add_entities)

        assert len(added) == 1
        entities, kwargs = added[0]
        assert [type(e).__name__ for e in entities] == ["EffyRecalculateButton"]
        assert kwargs == {"update_before_add": False}


class TestTheEntity:
    def test_it_is_a_diagnostic_button_with_a_stable_unique_id(self) -> None:
        button = _press_button(_hass(None))
        description = button.entity_description
        assert description.key == "recalculate_history"
        assert description.name == "Re-calculate History"
        assert description.entity_category == "diagnostic"
        assert button._attr_unique_id == "entry1_recalculate_history"

    def test_it_belongs_to_the_one_effy_device(self) -> None:
        info = _press_button(_hass(None)).device_info
        assert info["identifiers"] == {(DOMAIN, "entry1")}
        assert info["name"] == "Effy"
        assert info["manufacturer"] == "Effy"
        assert info["model"] == "PV Loss Distributor"


class TestPress:
    async def test_a_press_runs_the_history_recalculation_of_its_entry_and_logs_the_count(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _Coordinator(written=42)
        with caplog.at_level(logging.INFO, logger=_button._LOGGER.name):
            await _press_button(_hass(coordinator)).async_press()

        assert coordinator.calls == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("starting history recalculation" in m for m in messages)
        assert any("42 slots written" in m for m in messages)

    async def test_a_failing_recalculation_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ADR-000 §8: a button press has no caller to propagate to."""
        coordinator = _Coordinator(error=RuntimeError("recorder busy"))
        with caplog.at_level(logging.INFO, logger=_button._LOGGER.name):
            await _press_button(_hass(coordinator)).async_press()  # must not raise

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "history recalculation failed" in errors[0].getMessage()
        assert "recorder busy" in caplog.text  # the traceback is in the log
        assert not any("slots written" in r.getMessage() for r in caplog.records)

    async def test_a_press_after_the_entry_was_unloaded_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=_button._LOGGER.name):
            await _press_button(_hass(None)).async_press()  # no coordinator any more

        assert [r.levelno for r in caplog.records if r.levelno >= logging.ERROR] == [logging.ERROR]
