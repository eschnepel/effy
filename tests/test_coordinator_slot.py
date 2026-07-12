"""
Unit tests for the reduced EffyCoordinator shell (live path disabled,
2026-07-09 — see disabled/README.md) and its slot-timer -> history wiring
(ADR-011).

Covers:
  - _next_slot_trigger_delay (fires SLOT_TIMER_LAG_SECONDS *after* each
    SLOT_MINUTES boundary — see ADR-011 for why this changed from "before")
  - EffyCoordinator's surviving public surface: subscribe/unsubscribe,
    async_setup/async_shutdown scheduling the slot timer, force_refresh()
    as a no-op placeholder.
  - _on_slot_timer: computes the just-finished slot's boundary and hands it
    to history.async_recalculate_slot via hass.async_create_task.

`effy.history` is stubbed out (not the real history.py) before loading
coordinator.py, so these tests exercise coordinator.py's own boundary-
calculation and wiring logic in isolation, without needing the heavy
homeassistant.components.recorder stubbing history.py's real internals
would require (history.py has no dedicated test file of its own for the
same reason — recorder internals are impractical to unit test without a
real HA instance).

LiveReading and everything event-driven moved to
disabled/coordinator_live.py — see disabled/test_coordinator_live.py for
its (currently inactive) test coverage.

Uses the same importlib-by-file-path pattern as test_calculation.py
(ADR-000 §6). HA modules are stubbed before loading so no homeassistant
install is needed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from effy.coordinator import EffyCoordinator as EffyCoordinator

# ---------------------------------------------------------------------------
# 1. HA stubs – registered before any effy module is loaded
# ---------------------------------------------------------------------------


def _stub(name: str, **attrs: Any) -> ModuleType:
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_stub("homeassistant")
_stub("homeassistant.config_entries", ConfigEntry=object)
_stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.event", async_call_later=MagicMock())
_stub("homeassistant.components")


class _SensorStateClass:
    TOTAL_INCREASING = "total_increasing"
    TOTAL = "total"
    MEASUREMENT = "measurement"


_stub("homeassistant.components.sensor", SensorStateClass=_SensorStateClass)

# ---------------------------------------------------------------------------
# 2. Load effy modules by file path
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
    setattr(_effy_pkg, filename.replace(".py", ""), mod)
    spec.loader.exec_module(mod)
    return mod


# Stub effy.history BEFORE loading coordinator.py, which does
# `from .history import async_recalculate_recent` — sys.modules is checked
# before the filesystem, so this fake is what coordinator.py gets. See
# module docstring for why the real history.py isn't loaded here.
_history_calls: list[tuple[Any, Any, datetime]] = []
# Configurable by individual tests: what async_recalculate_recent should
# return this call — (written, earliest_touched_slot | None).
_history_return_value: list[tuple[int, datetime | None]] = [(0, None)]


async def _fake_async_recalculate_recent(
    hass: Any, entry_options: Any, now: datetime
) -> tuple[int, datetime | None]:
    _history_calls.append((hass, entry_options, now))
    return _history_return_value[0]


_history_stub = ModuleType("effy.history")
_history_stub.async_recalculate_recent = _fake_async_recalculate_recent  # type: ignore[attr-defined]
_history_stub.__package__ = "effy"
sys.modules["effy.history"] = _history_stub
_effy_pkg.history = _history_stub  # type: ignore[attr-defined]

_calc_mod = _load("effy.calculation", "calculation.py")
_su_mod = _load("effy.sensor_utils", "sensor_utils.py")
_coord_mod = _load("effy.coordinator", "coordinator.py")

if not TYPE_CHECKING:
    EffyCoordinator = _coord_mod.EffyCoordinator
_next_slot_trigger_delay = _coord_mod._next_slot_trigger_delay
SLOT_MINUTES = _coord_mod.SLOT_MINUTES
SLOT_TIMER_LAG_SECONDS = _coord_mod.SLOT_TIMER_LAG_SECONDS


def _ts(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2024, 1, 1, h, m, s, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _next_slot_trigger_delay — fires LAG_SECONDS *after* each boundary (ADR-011)
# ---------------------------------------------------------------------------


class TestNextSlotTriggerDelay:
    def test_delay_at_boundary_itself(self) -> None:
        # Exactly on a boundary -> trigger point (boundary + 5s) is still ahead.
        now = _ts(10, 0, 0)
        assert _next_slot_trigger_delay(now) == pytest.approx(5.0)

    def test_delay_just_before_trigger_point(self) -> None:
        now = _ts(10, 0, 4)
        assert _next_slot_trigger_delay(now) == pytest.approx(1.0)

    def test_delay_exactly_at_trigger_point_skips_to_next_slot(self) -> None:
        # now == boundary + 5s: this slot's own trigger point has arrived/passed
        # -> skip forward to the following slot's trigger, not delay=0.
        now = _ts(10, 0, 5)
        assert _next_slot_trigger_delay(now) == pytest.approx(300.0)

    def test_delay_mid_slot_skips_to_next_slot(self) -> None:
        now = _ts(10, 2, 30)
        assert _next_slot_trigger_delay(now) == pytest.approx(155.0)

    def test_custom_slot_and_lag(self) -> None:
        now = _ts(10, 0, 0)
        assert _next_slot_trigger_delay(now, slot_minutes=1, lag_seconds=2) == pytest.approx(2.0)

    def test_uses_shared_slot_minutes_constant(self) -> None:
        """SLOT_MINUTES must be the same 5-minute grid as the history path
        (sensor_utils.SLOT_MINUTES) -- not a separately invented interval."""
        assert SLOT_MINUTES == 5
        assert SLOT_TIMER_LAG_SECONDS == 5


# ---------------------------------------------------------------------------
# EffyCoordinator shell (live path disabled)
# ---------------------------------------------------------------------------


class _FakeEntry:
    def __init__(self, input_ids: list[str], output_ids: list[str]) -> None:
        self.options = {"input_sensors": input_ids, "output_sensors": output_ids}


class _FakeHass:
    def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
        return asyncio.ensure_future(coro)


class TestEffyCoordinatorShell:
    def _coordinator(self) -> EffyCoordinator:
        entry = _FakeEntry(["sensor.pv"], ["sensor.grid_export"])
        return EffyCoordinator(_FakeHass(), entry)  # type: ignore[arg-type]

    def test_stores_input_and_output_ids(self) -> None:
        coord = self._coordinator()
        assert coord._input_ids == ["sensor.pv"]
        assert coord._output_ids == ["sensor.grid_export"]

    def test_subscribe_registers_and_unsubscribe_removes(self) -> None:
        coord = self._coordinator()
        calls: list[object] = []
        unsub = coord.subscribe("sensor.effy_pv", calls.append)
        assert "sensor.effy_pv" in coord._subscribers
        unsub()
        assert "sensor.effy_pv" not in coord._subscribers

    def test_async_setup_schedules_slot_timer(self) -> None:
        coord = self._coordinator()
        assert coord._unsub_slot_timer is None
        coord.async_setup()
        assert coord._unsub_slot_timer is not None

    def test_async_shutdown_cancels_slot_timer(self) -> None:
        coord = self._coordinator()
        coord.async_setup()
        cancelled: list[bool] = []
        coord._unsub_slot_timer = lambda: cancelled.append(True)
        coord.async_shutdown()
        assert cancelled == [True]
        assert coord._unsub_slot_timer is None

    def test_force_refresh_does_not_raise(self) -> None:
        """No-op placeholder while disabled — must be safe for sensor.py's
        unconditional call on platform setup."""
        coord = self._coordinator()
        coord.force_refresh()  # must not raise

    def test_subscribe_recalculated_from_and_unsubscribe(self) -> None:
        coord = self._coordinator()
        received: list[datetime] = []
        unsub = coord.subscribe_recalculated_from(received.append)

        coord.set_recalculated_from(_ts(11, 0, 0))
        assert received == [_ts(11, 0, 0)]
        assert coord.recalculated_from == _ts(11, 0, 0)

        unsub()
        coord.set_recalculated_from(_ts(12, 0, 0))
        assert received == [_ts(11, 0, 0)]  # no new callback after unsub
        assert coord.recalculated_from == _ts(12, 0, 0)  # state still updates

    @pytest.mark.asyncio
    async def test_on_slot_timer_reschedules(self) -> None:
        coord = self._coordinator()
        coord.async_setup()
        call_count_before = _coord_mod.async_call_later.call_count
        coord._on_slot_timer(None)
        await asyncio.sleep(0)  # let the scheduled task run
        assert coord._unsub_slot_timer is not None
        assert _coord_mod.async_call_later.call_count == call_count_before + 1

    @pytest.mark.asyncio
    async def test_on_slot_timer_calls_recalculate_recent_with_current_time(self) -> None:
        """coordinator.py no longer computes slot boundaries itself
        (ADR-012) -- it hands the raw current time to
        async_recalculate_recent, which determines the affected range
        dynamically."""
        _history_calls.clear()
        coord = self._coordinator()
        coord.async_setup()

        before = datetime.now(timezone.utc)
        coord._on_slot_timer(None)
        await asyncio.sleep(0)  # let the scheduled task actually run
        after = datetime.now(timezone.utc)

        assert len(_history_calls) == 1
        hass_arg, options_arg, now_arg = _history_calls[0]
        assert hass_arg is coord._hass
        assert options_arg is coord._entry.options
        assert before <= now_arg <= after

    @pytest.mark.asyncio
    async def test_on_slot_timer_reports_earliest_touched_slot(self) -> None:
        """When async_recalculate_recent finds something to write, the
        returned earliest-touched-slot must be pushed to
        set_recalculated_from (ADR-012)."""
        _history_calls.clear()
        earliest = _ts(10, 0, 0)
        _history_return_value[0] = (3, earliest)
        try:
            coord = self._coordinator()
            coord.async_setup()
            received: list[datetime] = []
            coord.subscribe_recalculated_from(received.append)

            coord._on_slot_timer(None)
            await asyncio.sleep(0)

            assert coord.recalculated_from == earliest
            assert received == [earliest]
        finally:
            _history_return_value[0] = (0, None)

    @pytest.mark.asyncio
    async def test_on_slot_timer_does_not_touch_recalculated_from_when_nothing_written(
        self,
    ) -> None:
        """If async_recalculate_recent returns None (nothing to write this
        cycle), recalculated_from must stay whatever it was -- not get
        reset to None."""
        _history_calls.clear()
        _history_return_value[0] = (0, None)
        coord = self._coordinator()
        coord.recalculated_from = _ts(9, 0, 0)  # pre-existing value
        coord.async_setup()

        coord._on_slot_timer(None)
        await asyncio.sleep(0)

        assert coord.recalculated_from == _ts(9, 0, 0)  # unchanged
