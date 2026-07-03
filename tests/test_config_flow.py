"""
Unit tests for the Effy config flow's output-sensor device_class validation.

Covers:
  - _outputs_missing_device_class

Uses the same importlib-by-file-path + HA-module-stubbing pattern as
test_coordinator_slot.py (ADR-000 §6). Neither homeassistant nor voluptuous
need to be installed to run these tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from effy.config_flow import _outputs_missing_device_class as _outputs_missing_device_class

# ---------------------------------------------------------------------------
# 1. HA / voluptuous stubs – registered before config_flow.py is loaded
# ---------------------------------------------------------------------------


def _stub(name: str, **attrs: Any) -> ModuleType:
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _FlowHandlerBase:
    """Minimal stand-in for HA's FlowHandler/ConfigFlow/OptionsFlow.

    Only needs to support being subclassed with a `domain=` kwarg (used by
    `class EffyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):`) —
    nothing in this test instantiates or drives the flow classes themselves,
    only the module-level `_outputs_missing_device_class` helper.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__()


_stub("voluptuous")
_stub(
    "homeassistant.config_entries",
    ConfigFlow=_FlowHandlerBase,
    OptionsFlow=_FlowHandlerBase,
    ConfigEntry=object,
    FlowResult=object,
)
_stub("homeassistant")
_stub("homeassistant.core", callback=lambda f: f)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.selector", selector=lambda *_a, **_kw: None)

# ---------------------------------------------------------------------------
# 2. Load config_flow.py by file path
# ---------------------------------------------------------------------------

_base = Path(__file__).resolve().parent.parent / "custom_components" / "effy"

_effy_pkg = ModuleType("effy")
_effy_pkg.__path__ = [str(_base)]
_effy_pkg.__package__ = "effy"
sys.modules["effy"] = _effy_pkg


def _load(reg_name: str, filename: str) -> ModuleType:
    path = _base / filename
    spec = importlib.util.spec_from_file_location(reg_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "effy"
    sys.modules[reg_name] = mod
    spec.loader.exec_module(mod)
    return mod


_const = _load("effy.const", "const.py")
_config_flow = _load("effy.config_flow", "config_flow.py")

_outputs_missing_device_class = _config_flow._outputs_missing_device_class


# ---------------------------------------------------------------------------
# 3. Test doubles
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, device_class: str | None) -> None:
        self.attributes = {"device_class": device_class} if device_class else {}


class _FakeStates:
    def __init__(self, states: dict[str, _FakeState]) -> None:
        self._states = states

    def get(self, entity_id: str) -> _FakeState | None:
        return self._states.get(entity_id)


class _FakeHass:
    def __init__(self, states: dict[str, _FakeState]) -> None:
        self.states = _FakeStates(states)


# ---------------------------------------------------------------------------
# 4. Tests
# ---------------------------------------------------------------------------


class TestOutputsMissingDeviceClass:
    def test_all_have_device_class_returns_false(self) -> None:
        hass = _FakeHass(
            {
                "sensor.grid_export": _FakeState("energy"),
                "sensor.battery_export": _FakeState("power"),
            }
        )
        assert (
            _outputs_missing_device_class(hass, ["sensor.grid_export", "sensor.battery_export"])
            is False
        )

    def test_one_missing_device_class_returns_true(self) -> None:
        hass = _FakeHass(
            {
                "sensor.grid_export": _FakeState("energy"),
                "sensor.battery_export": _FakeState(None),
            }
        )
        assert (
            _outputs_missing_device_class(hass, ["sensor.grid_export", "sensor.battery_export"])
            is True
        )

    def test_missing_entity_state_counts_as_missing(self) -> None:
        """An entity_id with no known state (e.g. unavailable) can't be
        confirmed to have a device_class, so it's treated as missing.
        """
        hass = _FakeHass({})
        assert _outputs_missing_device_class(hass, ["sensor.does_not_exist"]) is True

    def test_empty_output_list_returns_false(self) -> None:
        hass = _FakeHass({})
        assert _outputs_missing_device_class(hass, []) is False

    def test_empty_string_device_class_counts_as_missing(self) -> None:
        """An explicit empty-string device_class is falsy and must not pass."""
        hass = _FakeHass({"sensor.x": _FakeState("")})
        assert _outputs_missing_device_class(hass, ["sensor.x"]) is True
