"""``get_current_value`` of ``sensor_utils.py``: the reading of a source's current state.

The module is loaded by file path under a private package name, with Home Assistant stubbed
only while it loads.
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
_PKG = "effy_sensor_utils_branches_under_test"
ENTITY = "sensor.pv_south"


def _load() -> tuple[ModuleType, ModuleType]:
    stub_names = ["homeassistant", "homeassistant.core", "homeassistant.components"]
    stub_names.append("homeassistant.components.sensor")
    touched = [*stub_names, _PKG, f"{_PKG}.calculation", f"{_PKG}.sensor_utils"]
    saved = {name: sys.modules.get(name) for name in touched}

    class _StateClass:
        TOTAL_INCREASING = "total_increasing"
        TOTAL = "total"
        MEASUREMENT = "measurement"

    def stub(name: str, **attrs: Any) -> None:
        mod = ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod

    try:
        stub("homeassistant")
        stub("homeassistant.core", HomeAssistant=object)
        stub("homeassistant.components")
        stub("homeassistant.components.sensor", SensorStateClass=_StateClass)
        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(_BASE)]
        pkg.__package__ = _PKG
        sys.modules[_PKG] = pkg
        loaded: dict[str, ModuleType] = {}
        for name in ("calculation", "sensor_utils"):
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
    return loaded["calculation"], loaded["sensor_utils"]


_calc, _utils = _load()


def _hass(state: str | None, **attributes: Any) -> Any:
    """A Home Assistant whose one entity has ``state`` (``None``: no state at all)."""
    states: dict[str, Any] = {}
    if state is not None:
        states[ENTITY] = SimpleNamespace(state=state, attributes=attributes)
    return SimpleNamespace(states=SimpleNamespace(get=states.get))


class TestGetCurrentValue:
    def test_a_numeric_state_is_the_raw_value_in_the_original_unit(self) -> None:
        reading = _utils.get_current_value(_hass("1234.5", unit_of_measurement="kWh"), ENTITY)

        assert reading == _calc.SensorReading(
            entity_id=ENTITY, raw_value=1234.5, original_unit="kWh"
        )

    def test_a_state_without_a_unit_is_in_watts(self) -> None:
        reading = _utils.get_current_value(_hass("-42"), ENTITY)

        assert reading.raw_value == -42.0
        assert reading.original_unit == "W"

    def test_an_entity_without_a_state_has_no_reading(self) -> None:
        assert _utils.get_current_value(_hass(None), ENTITY) is None

    @pytest.mark.parametrize("state", ["unavailable", "unknown", ""])
    def test_a_state_that_is_no_value_has_no_reading(self, state: str) -> None:
        assert _utils.get_current_value(_hass(state, unit_of_measurement="W"), ENTITY) is None

    def test_an_unparsable_state_has_no_reading_and_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_utils.__name__):
            reading = _utils.get_current_value(_hass("on", unit_of_measurement="W"), ENTITY)

        assert reading is None
        assert [r.getMessage() for r in caplog.records] == [
            f"Effy: cannot parse state 'on' for {ENTITY}"
        ]

    def test_a_state_that_is_no_value_is_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=_utils.__name__):
            _utils.get_current_value(_hass("unavailable"), ENTITY)

        assert caplog.records == []
