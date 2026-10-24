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
import logging
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from collections.abc import Callable
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
_stub(
    "homeassistant.const",
    EVENT_RECORDER_5MIN_STATISTICS_GENERATED="recorder_5min_statistics_generated",
)
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
_history_calls: list[tuple[Any, Any, datetime, Any]] = []
# Configurable by individual tests: what async_recalculate_recent should
# return this call — (written, earliest_touched_slot | None,
# touched_entity_ids, last_values) — last_values maps entity_id ->
# (value, unit) for the last slot written this run (ADR-016).
_history_return_value: list[tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]] = [
    (0, None, set(), {})
]


# trigger_since each call was made with (ADR-014 Amendment 2026-10-06), kept
# beside _history_calls so that tuple's shape stays what older tests unpack.
_history_trigger_since: list[Any] = []
# (previous_run, now) each recent_trigger_since() call received.
_trigger_since_args: list[tuple[datetime | None, datetime]] = []
# Set by a test to make the next async_recalculate_recent call raise.
_history_raise_next: list[bool] = [False]


async def _fake_async_recalculate_recent(
    hass: Any,
    entry_options: Any,
    now: datetime,
    energy_reading_cache: Any = None,
    trigger_since: Any = None,
) -> tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]:
    _history_calls.append((hass, entry_options, now, energy_reading_cache))
    _history_trigger_since.append(trigger_since)
    if _job_hook[0] is not None:
        await _job_hook[0]("recent")
    if _history_raise_next[0]:
        _history_raise_next[0] = False
        raise RuntimeError("recorder exploded")
    return _history_return_value[0]


def _fake_recent_trigger_since(previous_run: datetime | None, now: datetime) -> datetime:
    _trigger_since_args.append((previous_run, now))
    return _trigger_sentinel()


# The slot job's probe (TASK-0006): results are taken from this list, one per
# call; the last one repeats. ``_probe_calls`` counts the calls.
_probe_results: list[bool] = [True]
_probe_calls: list[datetime] = []
_probe_raise_next: list[bool] = [False]
# A hook a test can set to make the fakes below take time / record overlap.
_job_hook: list[Any] = [None]


async def _fake_async_probe_last_slot_ready(hass: Any, entry_options: Any, now: datetime) -> bool:
    index = len(_probe_calls)
    _probe_calls.append(now)
    if _probe_raise_next[0]:
        _probe_raise_next[0] = False
        raise RuntimeError("probe exploded")
    return _probe_results[min(index, len(_probe_results) - 1)]


_history_full_calls: list[tuple[Any, Any, Any]] = []
_history_full_return_value: list[
    tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]
] = [(0, None, set(), {})]


async def _fake_async_recalculate_history(
    hass: Any,
    entry_options: Any,
    energy_reading_cache: Any = None,
) -> tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]:
    _history_full_calls.append((hass, entry_options, energy_reading_cache))
    if _job_hook[0] is not None:
        await _job_hook[0]("history")
    return _history_full_return_value[0]


_hourly_calls: list[tuple[dict[str, str], datetime, bool]] = []
_hourly_raise_next: list[bool] = [False]


async def _fake_async_rewrite_hourly(hass: Any, units: dict[str, str], hour_start: datetime) -> int:
    """Records the call and whether the recalculation lock was held at that moment."""
    lock_held = _lock_probe[0]() if _lock_probe[0] is not None else False
    _hourly_calls.append((dict(units), hour_start, lock_held))
    if _job_hook[0] is not None:
        await _job_hook[0]("hourly")
    if _hourly_raise_next[0]:
        _hourly_raise_next[0] = False
        raise RuntimeError("hourly exploded")
    return len(units)


_lock_probe: list[Any] = [None]  # set per test: () -> bool, is the lock held now

# The standby machine's feed (TASK-0005 Stage 2): (options, state, now, lock_held) per call.
_standby_calls: list[tuple[Any, Any, datetime, bool]] = []
_standby_raise_next: list[bool] = [False]


async def _fake_async_update_standby(
    hass: Any, entry_options: Any, state: Any, now: datetime
) -> Any:
    """Records the call and whether the lock was held; returns a state that moved on."""
    lock_held = _lock_probe[0]() if _lock_probe[0] is not None else False
    _standby_calls.append((entry_options, state, now, lock_held))
    if _job_hook[0] is not None:
        await _job_hook[0]("standby")
    if _standby_raise_next[0]:
        _standby_raise_next[0] = False
        raise RuntimeError("standby exploded")
    return sys.modules["effy.calculation"].DrainState(estimate=12.0, count=state.count + 1)


_history_stub = ModuleType("effy.history")
_history_stub.async_rewrite_hourly = _fake_async_rewrite_hourly  # type: ignore[attr-defined]
_history_stub.async_update_standby = _fake_async_update_standby  # type: ignore[attr-defined]
_history_stub.async_probe_last_slot_ready = _fake_async_probe_last_slot_ready  # type: ignore[attr-defined]
_history_stub.async_recalculate_history = _fake_async_recalculate_history  # type: ignore[attr-defined]
_history_stub.async_recalculate_recent = _fake_async_recalculate_recent  # type: ignore[attr-defined]
_history_stub.recent_trigger_since = _fake_recent_trigger_since  # type: ignore[attr-defined]
_history_stub.__package__ = "effy"
sys.modules["effy.history"] = _history_stub
_effy_pkg.history = _history_stub  # type: ignore[attr-defined]

_load("effy.calculation", "calculation.py")
_load("effy.sensor_utils", "sensor_utils.py")
_coord_mod = _load("effy.coordinator", "coordinator.py")

if not TYPE_CHECKING:
    EffyCoordinator = _coord_mod.EffyCoordinator
_next_slot_trigger_delay = _coord_mod._next_slot_trigger_delay
SLOT_MINUTES = _coord_mod.SLOT_MINUTES
SLOT_TIMER_LAG_SECONDS = _coord_mod.SLOT_TIMER_LAG_SECONDS


def _ts(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2024, 1, 1, h, m, s, tzinfo=timezone.utc)


def _trigger_sentinel() -> datetime:
    """What the fake recent_trigger_since returns: a fixed, recognisable value,
    so the tests prove the coordinator forwards *its* result instead of
    re-deriving it (the real policy is tested in
    test_history_recent_equivalence.py)."""
    return _ts(0, 0)


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
    def __init__(
        self, input_ids: list[str], output_ids: list[str], bms_ids: list[str] | None = None
    ) -> None:
        self.options = {
            "pv_input_sensors": input_ids,
            "bms_input_sensors": bms_ids or [],
            "output_sensors": output_ids,
        }


class _FakeBus:
    def __init__(self) -> None:
        self.listeners: list[tuple[str, Any]] = []
        self.unsubscribed: list[tuple[str, Any]] = []

    def async_listen(self, event_type: str, listener: Any) -> Any:
        self.listeners.append((event_type, listener))

        def _unsub() -> None:
            self.unsubscribed.append((event_type, listener))

        return _unsub


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()

    def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
        return asyncio.ensure_future(coro)


class TestEffyCoordinatorShell:
    def _coordinator(self) -> EffyCoordinator:
        entry = _FakeEntry(["sensor.pv"], ["sensor.grid_export"])
        return EffyCoordinator(_FakeHass(), entry)

    def test_stores_input_and_output_ids(self) -> None:
        coord = self._coordinator()
        assert coord._input_ids == ["sensor.pv"]
        assert coord._output_ids == ["sensor.grid_export"]

    def test_exposes_the_two_input_roles_and_the_mixed_pool(self) -> None:
        entry = _FakeEntry(["sensor.pv_a", "sensor.pv_b"], ["sensor.out"], ["sensor.bms"])
        coord = EffyCoordinator(_FakeHass(), entry)
        assert coord.pv_input_ids == ["sensor.pv_a", "sensor.pv_b"]
        assert coord.bms_input_ids == ["sensor.bms"]
        assert coord._input_ids == ["sensor.pv_a", "sensor.pv_b", "sensor.bms"]
        coord.pv_input_ids.append("sensor.other")  # a copy: the coordinator is unaffected
        assert coord.pv_input_ids == ["sensor.pv_a", "sensor.pv_b"]

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

    def test_shutdown_without_a_setup_does_nothing(self) -> None:
        """Unloading an entry whose setup never ran (or failed) must not raise."""
        coord = self._coordinator()
        coord.async_shutdown()
        assert coord._unsub_recorder_event is None and coord._unsub_slot_timer is None
        assert coord._slot_job_task is None

    def test_a_second_shutdown_releases_nothing_twice(self) -> None:
        coord = self._coordinator()
        coord.async_setup()
        timer_calls: list[bool] = []
        coord._unsub_slot_timer = lambda: timer_calls.append(True)
        coord.async_shutdown()
        coord.async_shutdown()
        assert timer_calls == [True]
        assert len(coord._hass.bus.unsubscribed) == 1  # the recorder listener, once

    def test_shutdown_releases_whichever_of_the_two_is_still_held(self) -> None:
        for held in ("recorder_event", "slot_timer"):
            coord = self._coordinator()
            released: list[str] = []
            if held == "recorder_event":
                coord._unsub_recorder_event = lambda: released.append("recorder_event")
            else:
                coord._unsub_slot_timer = lambda: released.append("slot_timer")
            coord.async_shutdown()
            assert released == [held]

    def test_an_entity_update_subscription_ends_with_its_unsubscribe(self) -> None:
        """After the unsubscribe a recalculation no longer pushes to the entity (it was
        removed); the other subscribers are untouched."""
        coord = self._coordinator()
        gone: list[tuple[Any, Any]] = []
        kept: list[tuple[Any, Any]] = []
        unsub = coord.subscribe_updates("sensor.effy_a", lambda v, u: gone.append((v, u)))
        coord.subscribe_updates("sensor.effy_b", lambda v, u: kept.append((v, u)))
        last = {"sensor.effy_a": (1.0, "W"), "sensor.effy_b": (2.0, "W")}

        coord.notify_updated(["sensor.effy_a", "sensor.effy_b"], last)
        unsub()
        unsub()  # the entity may be removed and the entry unloaded: harmless twice
        coord.notify_updated(["sensor.effy_a", "sensor.effy_b"], last)

        assert gone == [(1.0, "W")]
        assert kept == [(2.0, "W"), (2.0, "W")]

    def test_the_recalculated_from_unsubscribe_is_harmless_twice(self) -> None:
        coord = self._coordinator()
        first: list[datetime] = []
        second: list[datetime] = []
        unsub = coord.subscribe_recalculated_from(first.append)
        coord.subscribe_recalculated_from(second.append)
        unsub()
        unsub()
        coord.set_recalculated_from(_ts(11, 0, 0))
        assert first == [] and second == [_ts(11, 0, 0)]

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
        coord._on_slot_timer(None)  # the watchdog always reschedules itself
        await asyncio.sleep(0)  # let a scheduled task run
        assert coord._unsub_slot_timer is not None
        assert _coord_mod.async_call_later.call_count == call_count_before + 1

    @pytest.mark.asyncio
    async def test_slot_job_calls_recalculate_recent_with_current_time(self) -> None:
        """coordinator.py no longer computes slot boundaries itself
        (ADR-012) -- it hands the raw current time to
        async_recalculate_recent, which determines the affected range
        dynamically."""
        _history_calls.clear()
        coord = self._coordinator()
        coord.async_setup()

        before = datetime.now(timezone.utc)
        coord._on_recorder_statistics(None)  # the first job waits for the recorder (TASK-0007a)
        await asyncio.sleep(0)  # let the scheduled task actually run
        after = datetime.now(timezone.utc)

        assert len(_history_calls) == 1
        hass_arg, options_arg, now_arg, cache_arg = _history_calls[0]
        assert hass_arg is coord._hass
        assert options_arg is coord._entry.options
        assert before <= now_arg <= after

    @pytest.mark.asyncio
    async def test_slot_job_passes_its_own_energy_reading_cache(self) -> None:
        """The coordinator's volatile last-known-valid-reading cache
        (ADR-015) must be passed through to async_recalculate_recent by
        reference, so history.py's updates to it persist across cycles."""
        _history_calls.clear()
        coord = self._coordinator()
        coord.async_setup()

        coord._on_recorder_statistics(None)  # the first job waits for the recorder (TASK-0007a)
        await asyncio.sleep(0)

        assert len(_history_calls) == 1
        _hass_arg, _options_arg, _now_arg, cache_arg = _history_calls[0]
        assert cache_arg is coord.last_valid_energy_readings

    @pytest.mark.asyncio
    async def test_slot_job_reports_earliest_touched_slot(self) -> None:
        """When async_recalculate_recent finds something to write, the
        returned earliest-touched-slot must be pushed to
        set_recalculated_from (ADR-012)."""
        _history_calls.clear()
        earliest = _ts(10, 0, 0)
        _history_return_value[0] = (
            3,
            earliest,
            {"sensor.effy_pv_roof"},
            {"sensor.effy_pv_roof": (42.0, "W")},
        )
        try:
            coord = self._coordinator()
            coord.async_setup()
            received: list[datetime] = []
            coord.subscribe_recalculated_from(received.append)

            coord._on_recorder_statistics(None)  # the first job waits for the recorder (TASK-0007a)
            await asyncio.sleep(0)

            assert coord.recalculated_from == earliest
            assert received == [earliest]
        finally:
            _history_return_value[0] = (0, None, set(), {})

    @pytest.mark.asyncio
    async def test_slot_job_pushes_last_value_to_touched_entities_only(self) -> None:
        """After a recalculation, only entities in touched_entity_ids get
        a notify_updated push -- an untouched subscriber is left alone --
        and that push carries the entity's last-written-slot (value, unit)
        rather than a hardcoded "unknown" (ADR-016)."""
        _history_calls.clear()
        _history_return_value[0] = (
            3,
            _ts(10, 0, 0),
            {"sensor.effy_pv_roof"},
            {"sensor.effy_pv_roof": (123.456, "W")},
        )
        try:
            coord = self._coordinator()
            coord.async_setup()
            touched_pushes: list[tuple[float | None, str | None]] = []
            untouched_pushes: list[tuple[float | None, str | None]] = []
            coord.subscribe_updates(
                "sensor.effy_pv_roof", lambda v, u: touched_pushes.append((v, u))
            )
            coord.subscribe_updates(
                "sensor.effy_pv_roof_power", lambda v, u: untouched_pushes.append((v, u))
            )

            coord._on_recorder_statistics(None)  # the first job waits for the recorder (TASK-0007a)
            await asyncio.sleep(0)

            assert touched_pushes == [(123.456, "W")]
            assert untouched_pushes == []
        finally:
            _history_return_value[0] = (0, None, set(), {})

    @pytest.mark.asyncio
    async def test_notify_updated_falls_back_to_none_for_missing_last_value(self) -> None:
        """A touched entity_id absent from last_values (shouldn't normally
        happen -- both come from the same recalculation call) gets a
        defensive (None, None) push rather than a KeyError (ADR-016)."""
        _history_calls.clear()
        _history_return_value[0] = (1, _ts(10, 0, 0), {"sensor.effy_pv_roof"}, {})
        try:
            coord = self._coordinator()
            coord.async_setup()
            pushes: list[tuple[float | None, str | None]] = []
            coord.subscribe_updates("sensor.effy_pv_roof", lambda v, u: pushes.append((v, u)))

            coord._on_recorder_statistics(None)  # the first job waits for the recorder (TASK-0007a)
            await asyncio.sleep(0)

            assert pushes == [(None, None)]
        finally:
            _history_return_value[0] = (0, None, set(), {})

    @pytest.mark.asyncio
    async def test_slot_job_does_not_touch_recalculated_from_when_nothing_written(
        self,
    ) -> None:
        """If async_recalculate_recent returns None (nothing to write this
        cycle), recalculated_from must stay whatever it was -- not get
        reset to None."""
        _history_calls.clear()
        _history_return_value[0] = (0, None, set(), {})
        coord = self._coordinator()
        coord.recalculated_from = _ts(9, 0, 0)  # pre-existing value
        coord.async_setup()

        coord._on_recorder_statistics(None)  # the first job waits for the recorder (TASK-0007a)
        await asyncio.sleep(0)

        assert coord.recalculated_from == _ts(9, 0, 0)  # unchanged

    @pytest.mark.asyncio
    async def test_trigger_since_follows_the_last_completed_cycle(self) -> None:
        """ADR-014 Amendment 2026-10-06: the coordinator remembers the end
        time of the last *completed* cycle and asks history.py for the
        matching trigger_since; the first cycle of a session has none."""
        _history_calls.clear()
        _history_trigger_since.clear()
        _trigger_since_args.clear()
        _history_raise_next[0] = False
        coord = self._coordinator()
        assert coord._last_recent_run is None

        await coord._async_recalculate_recent_and_report()
        await coord._async_recalculate_recent_and_report()

        (_, now1), (previous2, now2) = _trigger_since_args[0], _trigger_since_args[1]
        assert _trigger_since_args[0][0] is None  # first cycle: nothing known
        assert previous2 == now1  # second cycle: the first one's end time
        assert coord._last_recent_run == now2
        assert _history_trigger_since == [_trigger_sentinel(), _trigger_sentinel()]

    @pytest.mark.asyncio
    async def test_failed_cycle_does_not_advance_the_marker(self) -> None:
        """A cycle that raised never applied anything, so the next one must
        still reach back over it."""
        _history_calls.clear()
        _trigger_since_args.clear()
        _history_raise_next[0] = False
        coord = self._coordinator()

        await coord._async_recalculate_recent_and_report()
        good = coord._last_recent_run
        assert good is not None

        _history_raise_next[0] = True
        with pytest.raises(RuntimeError):
            await coord._async_recalculate_recent_and_report()
        assert coord._last_recent_run == good  # unchanged by the failed cycle

        await coord._async_recalculate_recent_and_report()
        assert _trigger_since_args[2][0] == good  # still reaches back over the failure

    @pytest.mark.asyncio
    async def test_marker_never_moves_backwards_when_cycles_overlap(self) -> None:
        coord = self._coordinator()
        later = _ts(23, 59) + timedelta(days=365 * 100)  # far in the future
        coord._last_recent_run = later  # as if a newer cycle had already finished
        await coord._async_recalculate_recent_and_report()  # an older one finishes now
        assert coord._last_recent_run == later

    def test_marker_is_in_memory_only(self) -> None:
        """A new coordinator (= a Home Assistant start) knows nothing."""
        assert self._coordinator()._last_recent_run is None


# ---------------------------------------------------------------------------
# Recorder-synchronized slot trigger (TASK-0006, as Shady's TASK-0041)
# ---------------------------------------------------------------------------

_EVENT = "recorder_5min_statistics_generated"


def _reset_slot_job_fakes() -> None:
    _hourly_calls.clear()
    _hourly_raise_next[0] = False
    _standby_calls.clear()
    _standby_raise_next[0] = False
    _lock_probe[0] = None
    _history_calls.clear()
    _history_full_calls.clear()
    _probe_calls.clear()
    _probe_results[:] = [True]
    _probe_raise_next[0] = False
    _history_raise_next[0] = False
    _job_hook[0] = None
    _history_return_value[0] = (0, None, set(), {})
    _history_full_return_value[0] = (0, None, set(), {})


class _SlotCoordinatorBase:
    def _coordinator(self) -> EffyCoordinator:
        _reset_slot_job_fakes()
        coord = EffyCoordinator(_FakeHass(), _FakeEntry(["sensor.pv"], ["sensor.grid_export"]))
        coord._probe_interval = timedelta(milliseconds=1)
        return coord

    @staticmethod
    async def _drain(coord: EffyCoordinator) -> None:
        task = getattr(coord, "_slot_job_task", None)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        else:  # no slot job (pre-change code): just let the scheduled work run
            await asyncio.sleep(0.05)


class TestRecorderEventSubscription(_SlotCoordinatorBase):
    def test_setup_subscribes_to_the_recorder_event(self) -> None:
        coord = self._coordinator()
        coord.async_setup()
        bus = coord._hass.bus
        assert [e for e, _ in bus.listeners] == [_EVENT]
        assert bus.listeners[0][1] == coord._on_recorder_statistics

    def test_shutdown_unsubscribes_and_cancels_the_in_flight_job(self) -> None:
        async def run() -> None:
            coord = self._coordinator()
            _probe_results[:] = [False]  # the job keeps probing
            coord._probe_interval = timedelta(seconds=30)
            coord.async_setup()
            coord._on_recorder_statistics(None)
            await asyncio.sleep(0)
            task = coord._slot_job_task
            assert task is not None and not task.done()
            coord.async_shutdown()
            await asyncio.gather(task, return_exceptions=True)
            assert task.cancelled()
            assert coord._hass.bus.unsubscribed == coord._hass.bus.listeners
            assert _history_calls == []  # a shut-down job never runs its body

        asyncio.run(run())

    def test_handler_is_a_plain_callback_that_only_schedules(self) -> None:
        async def run() -> None:
            coord = self._coordinator()
            coord.async_setup()
            result = coord._on_recorder_statistics(None)
            assert result is None  # not a coroutine
            assert not asyncio.iscoroutinefunction(coord._on_recorder_statistics)
            assert _history_calls == [] and _probe_calls == []  # nothing ran inline
            await self._drain(coord)
            assert len(_history_calls) == 1

        asyncio.run(run())


class TestSlotJob(_SlotCoordinatorBase):
    @pytest.mark.asyncio
    async def test_probes_until_the_slot_is_readable_then_runs_the_body_once(self) -> None:
        coord = self._coordinator()
        _probe_results[:] = [False, False, True]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert len(_probe_calls) == 3
        assert len(_history_calls) == 1

    @pytest.mark.asyncio
    async def test_runs_the_body_anyway_after_max_probes(self) -> None:
        coord = self._coordinator()
        _probe_results[:] = [False]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert len(_probe_calls) == 1 + coord._max_probes  # first read + retries
        assert len(_history_calls) == 1  # a sensor that never delivers never blocks

    @pytest.mark.asyncio
    async def test_a_failing_probe_does_not_stop_the_body(self) -> None:
        coord = self._coordinator()
        _probe_raise_next[0] = True
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert len(_history_calls) == 1

    @pytest.mark.asyncio
    async def test_duplicate_trigger_for_the_same_slot_runs_the_body_once(self) -> None:
        coord = self._coordinator()
        coord._now = lambda: _ts(10, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        coord._on_recorder_statistics(None)  # duplicate event
        coord._on_slot_timer(_ts(10, 0, 15))  # watchdog right after an event
        await self._drain(coord)
        assert len(_history_calls) == 1

    @pytest.mark.asyncio
    async def test_a_newer_event_ends_a_still_probing_job_without_its_body(self) -> None:
        coord = self._coordinator()
        _probe_results[:] = [False, False, False, False, False, False, True]
        coord._probe_interval = timedelta(milliseconds=20)
        coord._now = lambda: _ts(10, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        first = coord._slot_job_task
        await asyncio.sleep(0.005)  # the first job is probing
        coord._now = lambda: _ts(10, 5, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)  # the next slot arrived
        assert first is not None
        await asyncio.gather(first, return_exceptions=True)
        await self._drain(coord)
        assert len(_history_calls) == 1  # only the newer job ran its body

    @pytest.mark.asyncio
    async def test_a_job_that_started_its_body_is_not_cancelled_by_a_newer_event(self) -> None:
        coord = self._coordinator()
        started = asyncio.Event()
        release = asyncio.Event()

        async def hook(_which: str) -> None:
            started.set()
            await release.wait()

        _job_hook[0] = hook
        coord._now = lambda: _ts(10, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        first = coord._slot_job_task
        await started.wait()
        coord._now = lambda: _ts(10, 5, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        release.set()
        assert first is not None
        await asyncio.gather(first, return_exceptions=True)
        assert not first.cancelled()
        await self._drain(coord)
        assert len(_history_calls) == 2  # both bodies ran, one after the other


class TestWatchdog(_SlotCoordinatorBase):
    @pytest.mark.asyncio
    async def test_starts_a_job_when_none_was_ever_started(self) -> None:
        coord = self._coordinator()
        coord._on_slot_timer(_ts(10, 0, 5))
        await self._drain(coord)
        assert len(_history_calls) == 1

    @pytest.mark.asyncio
    async def test_does_not_start_a_job_while_the_event_keeps_arriving(self) -> None:
        coord = self._coordinator()
        coord._now = lambda: _ts(10, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        coord._on_slot_timer(_ts(10, 5, 5))  # 4 min 55 s after the job: < WATCHDOG
        coord._on_slot_timer(_ts(10, 5, 5))
        await self._drain(coord)
        assert len(_history_calls) == 1

    @pytest.mark.asyncio
    async def test_starts_a_job_when_the_event_has_been_silent_for_the_watchdog(self) -> None:
        coord = self._coordinator()
        coord._now = lambda: _ts(10, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        coord._now = lambda: _ts(10, 10, 5)  # type: ignore[method-assign]
        coord._on_slot_timer(_ts(10, 10, 5))  # 9 min 55 s of silence
        await self._drain(coord)
        assert len(_history_calls) == 2

    @pytest.mark.asyncio
    async def test_the_wall_clock_timer_keeps_rescheduling(self) -> None:
        coord = self._coordinator()
        coord.async_setup()
        before = _coord_mod.async_call_later.call_count
        coord._on_slot_timer(_ts(10, 0, 5))
        await self._drain(coord)
        assert _coord_mod.async_call_later.call_count == before + 1

    def test_defaults_are_shadys(self) -> None:
        assert _coord_mod.PROBE_INTERVAL == timedelta(seconds=1)
        assert _coord_mod.MAX_PROBES == 5
        assert _coord_mod.WATCHDOG == timedelta(minutes=6)


class TestSimulatedRecorderCompileTiming(_SlotCoordinatorBase):
    """Written first, against the pre-change code: the recorder compiles a
    slot 10 s after its boundary and the data becomes readable a moment after
    the event. The body must never run before the last completed slot's
    power-family statistic is readable."""

    @pytest.mark.asyncio
    async def test_body_runs_only_once_the_last_slot_is_readable(self) -> None:
        coord = self._coordinator()
        coord._probe_interval = timedelta(milliseconds=4)  # 5 retries outlast the 10 ms commit
        readable = {"slot": False}
        seen_at_body: list[bool] = []

        async def probe(hass: Any, options: Any, now: datetime) -> bool:
            _probe_calls.append(now)
            return readable["slot"]

        async def hook(_which: str) -> None:
            seen_at_body.append(readable["slot"])

        _job_hook[0] = hook
        _history_stub.async_probe_last_slot_ready = probe  # type: ignore[attr-defined]
        _coord_mod.async_probe_last_slot_ready = probe  # type: ignore[attr-defined]
        try:

            async def recorder_commits_later() -> None:
                await asyncio.sleep(0.01)  # "a few hundred ms" after the event
                readable["slot"] = True

            commit = asyncio.ensure_future(recorder_commits_later())
            coord._on_recorder_statistics(None)  # the event fires before the commit
            await self._drain(coord)
            await commit
        finally:
            _history_stub.async_probe_last_slot_ready = _fake_async_probe_last_slot_ready  # type: ignore[attr-defined]
            _coord_mod.async_probe_last_slot_ready = _fake_async_probe_last_slot_ready  # type: ignore[attr-defined]
        assert seen_at_body == [True]

    @pytest.mark.asyncio
    async def test_the_early_wall_clock_trigger_also_waits_for_the_recorder(self) -> None:
        """The pre-change timer at boundary + 5 s ran the body at once, while
        the slot was not yet compiled."""
        coord = self._coordinator()
        coord._probe_interval = timedelta(milliseconds=4)  # 5 retries outlast the 10 ms commit
        readable = {"slot": False}
        seen_at_body: list[bool] = []

        async def probe(hass: Any, options: Any, now: datetime) -> bool:
            return readable["slot"]

        async def hook(_which: str) -> None:
            seen_at_body.append(readable["slot"])

        _job_hook[0] = hook
        _coord_mod.async_probe_last_slot_ready = probe  # type: ignore[attr-defined]
        try:

            async def recorder_commits_later() -> None:
                await asyncio.sleep(0.01)
                readable["slot"] = True

            commit = asyncio.ensure_future(recorder_commits_later())
            coord._on_slot_timer(_ts(10, 0, 5))
            await self._drain(coord)
            await commit
        finally:
            _coord_mod.async_probe_last_slot_ready = _fake_async_probe_last_slot_ready  # type: ignore[attr-defined]
        assert seen_at_body == [True]


class TestSerializedRecalculations(_SlotCoordinatorBase):
    """One lock: the recent and the history recalculation never overlap."""

    @pytest.mark.asyncio
    async def test_recent_and_history_dispatched_together_never_overlap(self) -> None:
        coord = self._coordinator()
        running: list[str] = []
        overlap: list[tuple[str, ...]] = []
        order: list[str] = []

        async def hook(which: str) -> None:
            running.append(which)
            if len(running) > 1:
                overlap.append(tuple(running))
            await asyncio.sleep(0.01)
            order.append(which)
            running.remove(which)

        _job_hook[0] = hook
        await asyncio.gather(
            coord._async_recalculate_recent_and_report(),
            coord.async_recalculate_history_and_report(),
        )
        assert overlap == []
        assert sorted(order) == ["history", "recent"]  # both completed, none skipped

    @pytest.mark.asyncio
    async def test_the_second_job_waits_instead_of_being_skipped(self) -> None:
        coord = self._coordinator()
        release = asyncio.Event()
        started: list[str] = []

        async def hook(which: str) -> None:
            started.append(which)
            if which == "history":
                await release.wait()

        _job_hook[0] = hook
        history = asyncio.ensure_future(coord.async_recalculate_history_and_report())
        await asyncio.sleep(0)
        recent = asyncio.ensure_future(coord._async_recalculate_recent_and_report())
        await asyncio.sleep(0.01)
        assert started == ["history"]  # recent waits for the lock
        release.set()
        await asyncio.gather(history, recent)
        assert started == ["history", "recent"]

    @pytest.mark.asyncio
    async def test_a_failed_job_releases_the_lock(self) -> None:
        coord = self._coordinator()
        _history_raise_next[0] = True
        with pytest.raises(RuntimeError):
            await coord._async_recalculate_recent_and_report()
        await coord.async_recalculate_history_and_report()  # must not hang
        assert len(_history_full_calls) == 1

    @pytest.mark.asyncio
    async def test_history_recalculation_reports_like_the_button_did(self) -> None:
        coord = self._coordinator()
        earliest = _ts(9, 0, 0)
        _history_full_return_value[0] = (
            7,
            earliest,
            {"sensor.effy_pv"},
            {"sensor.effy_pv": (5.0, "W")},
        )
        pushes: list[tuple[float | None, str | None]] = []
        coord.subscribe_updates("sensor.effy_pv", lambda v, u: pushes.append((v, u)))
        written = await coord.async_recalculate_history_and_report()
        assert written == 7
        assert coord.recalculated_from == earliest
        assert pushes == [(5.0, "W")]
        _hass, options, cache = _history_full_calls[0]
        assert options is coord._entry.options
        assert cache is coord.last_valid_energy_readings


# ---------------------------------------------------------------------------
# TASK-0007a — the first job waits for the recorder; the hour's row is rewritten
# ---------------------------------------------------------------------------


class TestFirstJobWaitsForTheRecorder(_SlotCoordinatorBase):
    @pytest.mark.asyncio
    async def test_the_watchdog_starts_no_job_before_the_watchdog_time_has_passed_since_setup(
        self,
    ) -> None:
        coord = self._coordinator()
        coord._now = lambda: _ts(10, 0, 0)  # type: ignore[method-assign]
        coord.async_setup()
        coord._on_slot_timer(_ts(10, 0, 5))  # the first boundary timer after a restart
        coord._on_slot_timer(_ts(10, 5, 5))  # 5 min 5 s: still < WATCHDOG
        await self._drain(coord)
        assert _history_calls == [] and _probe_calls == []

    @pytest.mark.asyncio
    async def test_the_watchdog_starts_the_first_job_once_the_recorder_stayed_silent(self) -> None:
        coord = self._coordinator()
        coord._now = lambda: _ts(10, 0, 0)  # type: ignore[method-assign]
        coord.async_setup()
        coord._on_slot_timer(_ts(10, 6, 5))  # 6 min 5 s after setup, no event ever
        await self._drain(coord)
        assert len(_history_calls) == 1

    @pytest.mark.asyncio
    async def test_the_first_recorder_event_starts_the_first_job_at_once(self) -> None:
        coord = self._coordinator()
        coord._now = lambda: _ts(10, 0, 0)  # type: ignore[method-assign]
        coord.async_setup()
        coord._now = lambda: _ts(10, 5, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)  # the first compile event after startup
        await self._drain(coord)
        assert len(_history_calls) == 1

    @pytest.mark.asyncio
    async def test_after_a_job_the_watchdog_counts_from_the_job_not_from_setup(self) -> None:
        coord = self._coordinator()
        coord._now = lambda: _ts(10, 0, 0)  # type: ignore[method-assign]
        coord.async_setup()
        coord._now = lambda: _ts(10, 5, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        coord._on_slot_timer(_ts(10, 10, 5))  # 4 min 55 s after the job: < WATCHDOG
        await self._drain(coord)
        assert len(_history_calls) == 1


def _clock_at(hour: int, minute: int, second: int) -> Callable[[], datetime]:
    return lambda: _ts(hour, minute, second)


class TestHourlyRewriteInTheSlotJob(_SlotCoordinatorBase):
    UNITS = {"sensor.effy_x": "kW", "sensor.effy_y": "W"}

    def _prepare(self) -> EffyCoordinator:
        coord = self._coordinator()
        _history_return_value[0] = (
            2,
            _ts(11, 55),
            set(self.UNITS),
            {eid: (1.0, unit) for eid, unit in self.UNITS.items()},
        )
        _lock_probe[0] = lambda: coord._recorder_job_lock().locked()
        return coord

    @pytest.mark.asyncio
    async def test_rewrites_the_hour_when_the_last_completed_slot_starts_at_minute_55(self) -> None:
        coord = self._prepare()
        coord._now = lambda: _ts(12, 0, 10)  # type: ignore[method-assign]  # slot 11:55 done
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert len(_history_calls) == 1
        assert _hourly_calls == [(self.UNITS, _ts(11, 0), True)]  # hour 11:00, under the lock

    @pytest.mark.asyncio
    async def test_the_rewrite_comes_after_the_recent_recalculation(self) -> None:
        coord = self._prepare()
        order: list[str] = []

        async def hook(which: str) -> None:
            order.append(which)

        _job_hook[0] = hook
        coord._now = lambda: _ts(12, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert order == ["recent", "hourly"]

    @pytest.mark.asyncio
    async def test_any_other_slot_writes_no_hourly_row(self) -> None:
        coord = self._prepare()
        for minute in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55):  # last completed != :55
            _hourly_calls.clear()
            coord._slot_job_slot = None
            coord._now = _clock_at(11, minute, 10)  # type: ignore[method-assign]
            coord._on_recorder_statistics(None)
            await self._drain(coord)
            assert _hourly_calls == [], minute

    @pytest.mark.asyncio
    async def test_a_failing_rewrite_is_logged_and_the_job_ends_normally(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coord = self._prepare()
        _hourly_raise_next[0] = True
        coord._now = lambda: _ts(12, 0, 10)  # type: ignore[method-assign]
        with caplog.at_level(logging.ERROR):
            coord._on_recorder_statistics(None)
            await self._drain(coord)
        task = coord._slot_job_task
        assert task is not None and task.done() and task.exception() is None
        assert len(_hourly_calls) == 1  # not retried
        assert any("hour" in r.getMessage().lower() for r in caplog.records)
        # the lock is free again and the next job runs normally
        coord._now = lambda: _ts(12, 5, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert len(_history_calls) == 2

    @pytest.mark.asyncio
    async def test_nothing_touched_means_no_rewrite(self) -> None:
        coord = self._coordinator()  # the stub returns no touched entities
        coord._now = lambda: _ts(12, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert _hourly_calls == []

    @pytest.mark.asyncio
    async def test_the_direct_body_call_without_a_slot_writes_no_hourly_row(self) -> None:
        coord = self._prepare()
        await coord._async_recalculate_recent_and_report()
        assert _hourly_calls == []


# ---------------------------------------------------------------------------
# The standby power machine in the slot job (TASK-0005 Stage 2, ADR-018)
# ---------------------------------------------------------------------------


class TestStandbyInTheSlotJob(_SlotCoordinatorBase):
    def _bms_coordinator(self, *, restored: bool = True) -> EffyCoordinator:
        _reset_slot_job_fakes()
        coord = EffyCoordinator(
            _FakeHass(),
            _FakeEntry(["sensor.pv"], ["sensor.grid_export"], ["sensor.bms_discharge"]),
        )
        coord._probe_interval = timedelta(milliseconds=1)
        if restored:
            coord.restore_standby(None)
        _lock_probe[0] = lambda: coord._recorder_job_lock().locked()
        return coord

    async def _job(self, coord: EffyCoordinator, at: datetime) -> None:
        coord._now = lambda: at  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)

    def test_the_standby_exists_only_with_a_bms_input(self) -> None:
        assert self._bms_coordinator().standby_enabled
        assert not self._coordinator().standby_enabled

    @pytest.mark.asyncio
    async def test_without_a_bms_input_nothing_is_fed(self) -> None:
        coord = self._coordinator()
        coord.restore_standby(None)
        await self._job(coord, _ts(12, 0, 10))
        assert _standby_calls == []

    @pytest.mark.asyncio
    async def test_nothing_is_fed_before_the_sensor_restored(self) -> None:
        coord = self._bms_coordinator(restored=False)
        assert coord.standby_state is None
        await self._job(coord, _ts(12, 0, 10))
        assert len(_history_calls) == 1  # the job itself ran
        assert _standby_calls == []

    @pytest.mark.asyncio
    async def test_the_job_feeds_the_machine_under_the_lock_after_the_recalculation(self) -> None:
        coord = self._bms_coordinator()
        order: list[str] = []

        async def hook(which: str) -> None:
            order.append(which)

        _job_hook[0] = hook
        _history_return_value[0] = (
            1,
            _ts(11, 55),
            {"sensor.effy_x"},
            {"sensor.effy_x": (1.0, "W")},
        )
        await self._job(coord, _ts(12, 0, 10))  # slot 11:55 done: the hourly rewrite runs too
        assert order == ["recent", "hourly", "standby"]
        options, state, now, lock_held = _standby_calls[0]
        assert options == coord._entry.options and now == _ts(12, 0, 10) and lock_held
        assert state.estimate is None
        assert coord.standby_state is not None and coord.standby_state.estimate == 12.0

    @pytest.mark.asyncio
    async def test_the_next_job_continues_from_the_state_of_the_last(self) -> None:
        coord = self._bms_coordinator()
        await self._job(coord, _ts(12, 0, 10))
        await self._job(coord, _ts(12, 5, 10))
        assert [call[1].count for call in _standby_calls] == [0, 1]

    @pytest.mark.asyncio
    async def test_subscribers_get_the_state_and_can_unsubscribe(self) -> None:
        coord = self._bms_coordinator()
        seen: list[Any] = []
        unsubscribe = coord.subscribe_standby(seen.append)
        await self._job(coord, _ts(12, 0, 10))
        assert [state.estimate for state in seen] == [12.0]
        unsubscribe()
        unsubscribe()  # idempotent
        await self._job(coord, _ts(12, 5, 10))
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_a_cancelled_feed_is_not_a_failure_and_leaves_the_state(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Shutdown cancels the in-flight job; that must reach the task as a
        cancellation, not be logged as "updating the standby power estimate failed"
        and not move the state (TASK-0028)."""
        coord = self._bms_coordinator()
        seen: list[Any] = []
        coord.subscribe_standby(seen.append)

        async def cancel_in_the_feed(which: str) -> None:
            if which == "standby":
                raise asyncio.CancelledError

        _job_hook[0] = cancel_in_the_feed
        with caplog.at_level(logging.ERROR):
            await self._job(coord, _ts(12, 0, 10))

        task = coord._slot_job_task
        assert task is not None and task.cancelled()
        assert seen == []
        assert coord.standby_state is not None and coord.standby_state.count == 0
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []

    @pytest.mark.asyncio
    async def test_a_failing_feed_is_logged_and_neither_fails_the_job_nor_moves_the_state(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coord = self._bms_coordinator()
        seen: list[Any] = []
        coord.subscribe_standby(seen.append)
        _standby_raise_next[0] = True
        with caplog.at_level(logging.ERROR):
            await self._job(coord, _ts(12, 0, 10))
        task = coord._slot_job_task
        assert task is not None and task.done() and task.exception() is None
        assert seen == [] and coord.standby_state is not None and coord.standby_state.count == 0
        assert any("standby" in r.getMessage().lower() for r in caplog.records)
        assert coord._last_recent_run is not None  # the recalculation itself completed
        await self._job(coord, _ts(12, 5, 10))  # retried with the same state
        assert [call[1].count for call in _standby_calls] == [0, 0]
        assert coord.standby_state.count == 1

    def test_restoring_gives_the_replay_its_starting_state(self) -> None:
        coord = self._bms_coordinator(restored=False)
        data = {
            "estimate": 12.5,
            "count": 7,
            "last_slot": "2024-01-01T11:55:00+00:00",
            "resolution": {"sensor.bms_discharge": 10.0},
        }
        state = coord.restore_standby(data)
        assert state.estimate == 12.5 and state.count == 7
        assert state.suppress_until == _ts(11, 55)
        assert coord.standby_state is state
        # unusable data starts empty: the first feed replays the last 24 hours
        fresh = coord.restore_standby({"estimate": "x"})
        assert fresh.estimate is None and fresh.count == 0 and fresh.suppress_until is None

    def test_setup_and_restore_never_touch_the_recorder(self) -> None:
        coord = self._bms_coordinator(restored=False)
        coord.async_setup()
        coord.restore_standby(None)
        assert _standby_calls == [] and _history_calls == []
        coord.async_shutdown()

    @pytest.mark.asyncio
    async def test_the_existing_recalculation_is_identical_with_and_without_the_machine(
        self,
    ) -> None:
        results: list[tuple[list[Any], list[Any]]] = []
        for bms in (False, True):
            _reset_slot_job_fakes()
            ids = ["sensor.bms_discharge"] if bms else None
            coord = EffyCoordinator(_FakeHass(), _FakeEntry(["sensor.pv"], ["sensor.out"], ids))
            coord._probe_interval = timedelta(milliseconds=1)
            coord.restore_standby(None)
            notified: list[Any] = []

            def record(entity_ids: Any, values: Any = None, sink: list[Any] = notified) -> None:
                sink.append((set(entity_ids), dict(values or {})))

            coord.notify_updated = record  # type: ignore[method-assign,assignment]
            _history_return_value[0] = (
                1,
                _ts(11, 55),
                {"sensor.effy_x"},
                {"sensor.effy_x": (1.0, "W")},
            )
            await self._job(coord, _ts(12, 0, 10))
            calls = [(c[2], c[3] is coord.last_valid_energy_readings) for c in _history_calls]
            results.append((calls, notified))
        assert results[0] == results[1]
        assert results[0][0] and results[0][1]


class TestClosingStep(_SlotCoordinatorBase):
    """TASK-0009, ADR-012 Amendment 2026-10-18, ADR-021 §4: the update of the
    recalculated-from sensor is the *last* step of both recalculation entry
    points, so a listener sees final statistics and states."""

    def _bms(self) -> EffyCoordinator:
        _reset_slot_job_fakes()
        coord = EffyCoordinator(
            _FakeHass(),
            _FakeEntry(["sensor.pv"], ["sensor.grid_export"], ["sensor.bms_discharge"]),
        )
        coord._probe_interval = timedelta(milliseconds=1)
        coord.restore_standby(None)
        return coord

    def _record_order(self, coord: EffyCoordinator) -> list[str]:
        order: list[str] = []

        async def hook(which: str) -> None:
            order.append(which)

        _job_hook[0] = hook
        coord.subscribe_updates("sensor.effy_x", lambda _value, _unit: order.append("notify"))
        coord.subscribe_recalculated_from(lambda _ts: order.append("closing"))
        return order

    @pytest.mark.asyncio
    async def test_the_slot_job_closes_after_notify_hour_rewrite_and_standby(self) -> None:
        coord = self._bms()
        order = self._record_order(coord)
        _history_return_value[0] = (
            1,
            _ts(11, 55),
            {"sensor.effy_x"},
            {"sensor.effy_x": (1.0, "W")},
        )
        coord._now = lambda: _ts(12, 0, 10)  # type: ignore[method-assign]  # slot 11:55: hour ends
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert order == ["recent", "notify", "hourly", "standby", "closing"]
        assert coord.recalculated_from == _ts(11, 55)

    @pytest.mark.asyncio
    async def test_the_manual_history_rewrite_closes_after_notify(self) -> None:
        coord = self._bms()
        order = self._record_order(coord)
        _history_full_return_value[0] = (
            3,
            _ts(9, 0),
            {"sensor.effy_x"},
            {"sensor.effy_x": (1.0, "W")},
        )
        assert await coord.async_recalculate_history_and_report() == 3
        assert order == ["history", "notify", "closing"]
        assert coord.recalculated_from == _ts(9, 0)

    @pytest.mark.asyncio
    async def test_a_failing_hour_rewrite_or_standby_update_still_closes(self) -> None:
        coord = self._bms()
        order = self._record_order(coord)
        _hourly_raise_next[0] = True
        _standby_raise_next[0] = True
        _history_return_value[0] = (
            1,
            _ts(11, 55),
            {"sensor.effy_x"},
            {"sensor.effy_x": (1.0, "W")},
        )
        coord._now = lambda: _ts(12, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert order == ["recent", "notify", "hourly", "standby", "closing"]

    @pytest.mark.asyncio
    async def test_a_recalculation_that_raises_does_not_close(self) -> None:
        coord = self._bms()
        order = self._record_order(coord)
        _history_raise_next[0] = True
        coord._now = lambda: _ts(12, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert "closing" not in order
        assert coord.recalculated_from is None

    @pytest.mark.asyncio
    async def test_a_run_that_touches_nothing_does_not_close(self) -> None:
        coord = self._bms()
        order = self._record_order(coord)
        _history_return_value[0] = (0, None, set(), {})
        coord._now = lambda: _ts(12, 0, 10)  # type: ignore[method-assign]
        coord._on_recorder_statistics(None)
        await self._drain(coord)
        assert "closing" not in order
        assert coord.recalculated_from is None


class TestPublishedRegistry:
    """TASK-0009, ADR-021 §5: Effy sensors register a descriptor of themselves."""

    def _coordinator(self) -> EffyCoordinator:
        return EffyCoordinator(_FakeHass(), _FakeEntry(["sensor.pv"], ["sensor.out"]))

    @staticmethod
    def _descriptor(entity_id: str) -> dict[str, Any]:
        return {"entity_id": entity_id, "series": "effective"}

    def test_a_registered_descriptor_is_listed_and_unregistering_removes_it(self) -> None:
        coord = self._coordinator()
        assert coord.published_descriptors() == []
        unregister = coord.register_published(self._descriptor("sensor.effy_a"))
        assert coord.published_descriptors() == [self._descriptor("sensor.effy_a")]
        unregister()
        assert coord.published_descriptors() == []

    def test_unregistering_twice_is_harmless(self) -> None:
        coord = self._coordinator()
        unregister = coord.register_published(self._descriptor("sensor.effy_a"))
        unregister()
        unregister()
        assert coord.published_descriptors() == []

    def test_a_stale_unregister_does_not_remove_a_re_registered_entity(self) -> None:
        coord = self._coordinator()
        stale = coord.register_published(self._descriptor("sensor.effy_a"))
        coord.register_published(self._descriptor("sensor.effy_a"))  # removed and added again
        stale()
        assert coord.published_descriptors() == [self._descriptor("sensor.effy_a")]

    def test_descriptors_are_copies(self) -> None:
        coord = self._coordinator()
        source = self._descriptor("sensor.effy_a")
        coord.register_published(source)
        source["series"] = "changed"  # the registered copy is unaffected
        listed = coord.published_descriptors()
        listed[0]["series"] = "changed too"
        assert coord.published_descriptors() == [self._descriptor("sensor.effy_a")]
