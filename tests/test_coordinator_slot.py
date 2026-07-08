"""
Unit tests for the LiveReading accumulator in EffyCoordinator.

Covers:
  - LiveReading.update_energy / update_power
  - LiveReading.to_sensor_reading (Wh→W conversion, time-weighted average)
  - LiveReading.reset (window roll-forward)
  - _state_class_family mapping

Uses the same importlib-by-file-path pattern as test_calculation.py (ADR-000 §6).
HA modules are stubbed before loading so no homeassistant install is needed.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    # Static-only imports so mypy can resolve these as real types. Not
    # executed at runtime — the actual modules are loaded by file path
    # below (ADR-000 §6) to avoid importing homeassistant via __init__.py.
    from effy.coordinator import LiveReading as LiveReading

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
_stub("homeassistant.core", HomeAssistant=object, Event=object, callback=lambda f: f)
_stub("homeassistant.helpers")
_stub(
    "homeassistant.helpers.event",
    async_call_later=MagicMock(),
    async_track_state_change_event=MagicMock(),
)
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
    # No submodule_search_locations here: these are plain modules, not
    # packages. Passing one (even []) makes importlib treat the module as
    # a package, so spec.parent becomes reg_name itself (e.g.
    # "effy.coordinator") instead of "effy" — which then no longer
    # matches the __package__ = "effy" set below, and Python's relative
    # import machinery raises "DeprecationWarning: __package__ !=
    # __spec__.parent" the moment the module does `from .calculation
    # import ...`.
    spec = importlib.util.spec_from_file_location(reg_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "effy"
    sys.modules[reg_name] = mod
    setattr(_effy_pkg, filename.replace(".py", ""), mod)
    spec.loader.exec_module(mod)
    return mod


_calc_mod = _load("effy.calculation", "calculation.py")
_coord_mod = _load("effy.coordinator", "coordinator.py")

if not TYPE_CHECKING:
    LiveReading = _coord_mod.LiveReading
    SensorReading = _calc_mod.SensorReading
_state_class_family = _coord_mod._state_class_family
_FAMILY_POWER = _coord_mod._FAMILY_POWER
_FAMILY_ENERGY = _coord_mod._FAMILY_ENERGY
_next_slot_trigger_delay = _coord_mod._next_slot_trigger_delay
SLOT_MINUTES = _coord_mod.SLOT_MINUTES
SLOT_TIMER_LEAD_SECONDS = _coord_mod.SLOT_TIMER_LEAD_SECONDS

SC = _SensorStateClass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2024, 1, 1, h, m, s, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _state_class_family
# ---------------------------------------------------------------------------


class TestStateClassFamily:
    def test_total_increasing_is_energy(self) -> None:
        assert _state_class_family(SC.TOTAL_INCREASING, "Wh") == _FAMILY_ENERGY
        assert _state_class_family(SC.TOTAL_INCREASING, "kWh") == _FAMILY_ENERGY
        # Even with a W unit, TOTAL_INCREASING is always energy
        assert _state_class_family(SC.TOTAL_INCREASING, "W") == _FAMILY_ENERGY

    def test_total_with_wh_is_energy(self) -> None:
        assert _state_class_family(SC.TOTAL, "Wh") == _FAMILY_ENERGY
        assert _state_class_family(SC.TOTAL, "kWh") == _FAMILY_ENERGY

    def test_total_with_w_is_power(self) -> None:
        assert _state_class_family(SC.TOTAL, "W") == _FAMILY_POWER
        assert _state_class_family(SC.TOTAL, "kW") == _FAMILY_POWER

    def test_measurement_is_power(self) -> None:
        assert _state_class_family(SC.MEASUREMENT, "W") == _FAMILY_POWER
        assert _state_class_family(SC.MEASUREMENT, "kW") == _FAMILY_POWER

    def test_none_is_power(self) -> None:
        assert _state_class_family(None, "W") == _FAMILY_POWER


# ---------------------------------------------------------------------------
# LiveReading – energy family (TOTAL_INCREASING / TOTAL-as-energy)
# ---------------------------------------------------------------------------


class TestLiveReadingEnergy:
    def _fresh(self, unit: str = "Wh") -> LiveReading:
        return LiveReading(entity_id="sensor.pv", unit=unit, family=_FAMILY_ENERGY)

    # --- first event seeds anchor, delta = 0 ---

    def test_first_event_not_seeded_before(self) -> None:
        lr = self._fresh()
        assert not lr.is_seeded()

    def test_first_event_sets_anchor(self) -> None:
        lr = self._fresh()
        t0 = _ts(10, 0)
        lr.update_energy(1000.0, t0)
        assert lr.raw_start == 1000.0
        assert lr.raw_last == 1000.0

    def test_first_event_seeded_after(self) -> None:
        lr = self._fresh()
        lr.update_energy(1000.0, _ts(10, 0))
        assert lr.is_seeded()

    # --- second event accumulates delta ---

    def test_delta_accumulates(self) -> None:
        lr = self._fresh()
        lr.update_energy(1000.0, _ts(10, 0))
        lr.update_energy(1003.6, _ts(10, 3))  # +3.6 Wh in 3 min
        assert lr.raw_last == pytest.approx(1003.6)
        assert lr.raw_start == pytest.approx(1000.0)

    # --- to_sensor_reading: Wh/h → W ---

    def test_wh_to_w_conversion(self) -> None:
        """3.6 Wh in 0.05 h (3 minutes) = 72 W."""
        lr = self._fresh(unit="Wh")
        lr.update_energy(1000.0, _ts(10, 0))
        lr.update_energy(1003.6, _ts(10, 3))  # 3 min = 0.05 h
        reading = lr.to_sensor_reading()
        assert reading is not None
        assert reading.original_unit == "W"
        assert reading.raw_value == pytest.approx(3.6 / (3 / 60))

    def test_kwh_to_kw_conversion(self) -> None:
        """0.5 kWh in 0.5 h (30 minutes) = 1 kW."""
        lr = self._fresh(unit="kWh")
        lr.update_energy(100.0, _ts(10, 0))
        lr.update_energy(100.5, _ts(10, 30))
        reading = lr.to_sensor_reading()
        assert reading is not None
        assert reading.original_unit == "kW"
        assert reading.raw_value == pytest.approx(1.0)

    def test_zero_delta_returns_zero_w(self) -> None:
        lr = self._fresh()
        lr.update_energy(500.0, _ts(10, 0))
        lr.update_energy(500.0, _ts(10, 5))
        reading = lr.to_sensor_reading()
        assert reading is not None
        assert reading.raw_value == 0.0

    def test_not_seeded_returns_none(self) -> None:
        lr = self._fresh()
        assert lr.to_sensor_reading() is None

    # --- counter reset clamping ---

    def test_counter_reset_clamps_raw_start(self) -> None:
        lr = self._fresh()
        lr.update_energy(9990.0, _ts(10, 0))
        lr.update_energy(9995.0, _ts(10, 2))
        # device reboot: counter reset mid-window
        lr.update_energy(5.0, _ts(10, 3))
        # raw_start moves to 5.0 so subsequent delta is non-negative
        assert lr.raw_start == pytest.approx(5.0)

    def test_after_reset_subsequent_delta_is_correct(self) -> None:
        lr = self._fresh()
        lr.update_energy(9990.0, _ts(10, 0))
        lr.update_energy(0.0, _ts(10, 1))  # reset
        lr.update_energy(2.0, _ts(10, 2))  # +2 Wh after reset
        assert lr.raw_last - lr.raw_start == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# LiveReading – power family (MEASUREMENT / TOTAL-as-power)
# ---------------------------------------------------------------------------


class TestLiveReadingPower:
    def _fresh(self, unit: str = "W") -> LiveReading:
        return LiveReading(entity_id="sensor.inv", unit=unit, family=_FAMILY_POWER)

    def test_first_event_seeds_avg_and_updated_ts(self) -> None:
        """First event at t==reset_ts: total_elapsed==0, so the else branch fires and
        avg is seeded directly with the new value.  updated_ts is set so subsequent
        events can compute a proper elapsed time from it.
        """
        lr = self._fresh()
        lr.reset_ts = _ts(10, 0)
        lr.update_power(400.0, _ts(10, 0))
        assert lr.avg == pytest.approx(400.0)
        assert lr.updated_ts == _ts(10, 0)

    def test_time_weighted_average_two_events(self) -> None:
        """
        Event at t=0: 200 W
        Event at t=4: 600 W
        Window [reset=t0, updated=t4]:
          old_elapsed = 0-0 = 0 s → weight of first sample = 0
          After first event: avg = 200  (window has no area yet)
          new_elapsed = 240 s
          avg = (200*0 + 600*240) / 240 = 600
        """
        lr = self._fresh()
        lr.reset_ts = _ts(10, 0)
        lr.update_power(200.0, _ts(10, 0))
        lr.update_power(600.0, _ts(10, 4))
        # old_elapsed = (_ts(10,0) - _ts(10,0)).total_seconds() = 0
        # new_elapsed = 240
        # avg = (200*0 + 600*240) / 240 = 600
        assert lr.avg == pytest.approx(600.0)

    def test_time_weighted_average_three_events(self) -> None:
        """
        reset_ts = t=0
        t=0:  400 W  → avg=400, updated=0
        t=2:  600 W  → old_el=0, new_el=120 → avg=(400*0+600*120)/120=600
        t=4: 1200 W  → old_el=120, new_el=120 → avg=(600*120+1200*120)/240=900
        """
        lr = self._fresh()
        lr.reset_ts = _ts(10, 0)
        lr.update_power(400.0, _ts(10, 0))
        lr.update_power(600.0, _ts(10, 2))
        lr.update_power(1200.0, _ts(10, 4))
        assert lr.avg == pytest.approx(900.0)

    def test_first_ever_event_is_not_swamped_by_epoch(self) -> None:
        """Regression test: a fresh LiveReading's reset_ts defaults to the
        1970-01-01 epoch sentinel. Before the fix, the first-ever
        update_power call computed old_elapsed against that sentinel
        (decades of seconds) versus new_elapsed=0 for the brand new sample,
        swamping the new value's weight to ~0 and making avg come out as
        ~0.0 regardless of the real reading. reset_ts must be anchored to
        the event's own timestamp on the true first call, without the
        caller having to pre-set reset_ts (production code never does).
        """
        lr = self._fresh()
        assert not lr.is_seeded()
        lr.update_power(400.0, _ts(10, 0))
        assert lr.avg == pytest.approx(400.0)

    def test_to_sensor_reading_returns_avg(self) -> None:
        lr = self._fresh()
        lr.reset_ts = _ts(10, 0)
        lr.update_power(300.0, _ts(10, 0))
        lr.update_power(500.0, _ts(10, 5))
        reading = lr.to_sensor_reading()
        assert reading is not None
        assert reading.original_unit == "W"
        assert reading.raw_value == pytest.approx(500.0)  # (300*0 + 500*300)/300

    def test_kw_unit_preserved(self) -> None:
        lr = self._fresh(unit="kW")
        lr.reset_ts = _ts(10, 0)
        lr.update_power(3.5, _ts(10, 0))
        reading = lr.to_sensor_reading()
        assert reading is not None
        assert reading.original_unit == "kW"

    def test_not_seeded_returns_none(self) -> None:
        lr = self._fresh()
        assert lr.to_sensor_reading() is None


# ---------------------------------------------------------------------------
# LiveReading.reset
# ---------------------------------------------------------------------------


class TestLiveReadingReset:
    def test_power_reset_carries_avg_forward(self) -> None:
        """avg must NOT be zeroed on reset.

        _do_refresh resets every watched entity on every debounce cycle,
        not just the entity that triggered that particular cycle. If avg
        were zeroed here, any sensor that doesn't happen to fire within the
        same 0.3s window as the triggering sensor would report 0 W on the
        next recalculation even though its last known value is still valid.
        See ADR-006 amendment 2026-07-03.
        """
        lr = LiveReading(entity_id="s", unit="W", family=_FAMILY_POWER)
        lr.update_power(500.0, _ts(10, 0))
        lr.update_power(700.0, _ts(10, 3))
        lr.reset(_ts(10, 3))
        assert lr.avg == pytest.approx(700.0)

    def test_power_reading_survives_a_cycle_with_no_new_event(self) -> None:
        """Regression test for the 'live updates are 99% zero' bug.

        Simulates a sensor that reported once, then a *different* sensor's
        event triggers a recalculation (and therefore a reset of this one
        too) before this sensor reports again. The carried-forward average
        must still be the last known value, not zero.
        """
        lr = LiveReading(entity_id="s", unit="W", family=_FAMILY_POWER)
        lr.update_power(400.0, _ts(10, 0))
        reading_before = lr.to_sensor_reading()
        assert reading_before is not None
        assert reading_before.raw_value == pytest.approx(400.0)

        # A burst triggered by some other entity resets this one too,
        # even though this sensor produced no new event.
        lr.reset(_ts(10, 0, 30))

        reading_after = lr.to_sensor_reading()
        assert reading_after is not None
        assert reading_after.raw_value == pytest.approx(400.0)

    def test_power_reset_ts_moves_to_last_updated(self) -> None:
        lr = LiveReading(entity_id="s", unit="W", family=_FAMILY_POWER)
        t_event = _ts(10, 3)
        lr.update_power(500.0, _ts(10, 0))
        lr.update_power(700.0, t_event)
        lr.reset(_ts(10, 3))
        assert lr.reset_ts == t_event
        assert lr.updated_ts == t_event

    def test_energy_reset_moves_raw_start_to_raw_last(self) -> None:
        lr = LiveReading(entity_id="s", unit="Wh", family=_FAMILY_ENERGY)
        lr.update_energy(1000.0, _ts(10, 0))
        lr.update_energy(1005.0, _ts(10, 5))
        lr.reset(_ts(10, 5))
        assert lr.raw_start == pytest.approx(1005.0)
        assert lr.raw_last == pytest.approx(1005.0)

    def test_energy_reset_ts_moves_to_last_updated(self) -> None:
        lr = LiveReading(entity_id="s", unit="Wh", family=_FAMILY_ENERGY)
        t_last = _ts(10, 5)
        lr.update_energy(1000.0, _ts(10, 0))
        lr.update_energy(1005.0, t_last)
        lr.reset(_ts(10, 5))
        assert lr.reset_ts == t_last
        assert lr.updated_ts == t_last

    def test_new_window_accumulates_correctly_after_reset(self) -> None:
        """After reset, delta in next window is relative to raw_last of previous window."""
        lr = LiveReading(entity_id="s", unit="Wh", family=_FAMILY_ENERGY)
        lr.update_energy(1000.0, _ts(10, 0))
        lr.update_energy(1005.0, _ts(10, 5))
        lr.reset(_ts(10, 5))
        # New window: 1005 is now the anchor
        lr.update_energy(1008.0, _ts(10, 8))
        assert lr.raw_last - lr.raw_start == pytest.approx(3.0)

    def test_energy_reads_honest_zero_while_idle_across_unrelated_resets(self) -> None:
        """Regression test for the 'battery discharge stuck at a stale rate' bug.

        A recalculation resets EVERY watched entity, including ones that
        produced no event of their own — typically triggered by some other,
        faster-updating entity. While this energy entity stays genuinely
        idle, reset_ts must stay pinned at its last real event (not roll
        forward with every unrelated reset), so elapsed_h keeps growing
        honestly and delta stays 0 -> the reading is a real 0, not an
        artifact of a zero-width window, and not a stale carried-forward
        rate from whenever the entity last actually reported.
        """
        lr = LiveReading(entity_id="battery_discharge", unit="Wh", family=_FAMILY_ENERGY)
        lr.update_energy(1000.0, _ts(10, 0, 0))
        lr.reset(_ts(10, 0, 0))  # closes the window right after the real event

        # Three unrelated recalculation cycles fire while this entity is idle.
        lr.reset(_ts(10, 0, 10))
        lr.reset(_ts(10, 0, 20))
        lr.reset(_ts(10, 0, 30))

        assert lr.reset_ts == _ts(10, 0, 0), "anchor must not move while idle"
        assert lr.updated_ts == _ts(10, 0, 30), "eval point advances honestly"
        assert lr.raw_start == pytest.approx(1000.0)
        assert lr.raw_last == pytest.approx(1000.0)

        reading_idle = lr.to_sensor_reading()
        assert reading_idle is not None
        assert reading_idle.raw_value == pytest.approx(0.0)

        # The real next event finally arrives, 30 Wh accumulated over the
        # full 30s gap -- must be averaged over that whole real interval,
        # not some artificially shrunk window from the idle resets above.
        lr.update_energy(1030.0, _ts(10, 0, 30))
        reading_real = lr.to_sensor_reading()
        assert reading_real is not None
        assert reading_real.raw_value == pytest.approx(3600.0)  # 30 Wh / 30s

    def test_energy_idle_reset_does_not_disturb_touched_reset_behavior(self) -> None:
        """A reset for an entity that WAS touched must behave exactly as before,
        regardless of how many idle resets happened to other entities in between.
        """
        lr = LiveReading(entity_id="s", unit="Wh", family=_FAMILY_ENERGY)
        lr.update_energy(1000.0, _ts(10, 0, 0))
        lr.update_energy(1005.0, _ts(10, 0, 5))
        lr.reset(_ts(10, 0, 5))
        assert lr.reset_ts == _ts(10, 0, 5)
        assert lr.updated_ts == _ts(10, 0, 5)
        assert lr.raw_start == pytest.approx(1005.0)


# ---------------------------------------------------------------------------
# _next_slot_trigger_delay
# ---------------------------------------------------------------------------


class TestNextSlotTriggerDelay:
    def test_delay_from_start_of_slot(self) -> None:
        now = _ts(10, 0, 0)
        assert _next_slot_trigger_delay(now) == pytest.approx(295.0)

    def test_delay_mid_slot(self) -> None:
        now = _ts(10, 2, 30)
        assert _next_slot_trigger_delay(now) == pytest.approx(145.0)

    def test_delay_skips_to_next_slot_inside_lead_window(self) -> None:
        # 10:04:57 is only 3s before the 10:05:00 boundary -- inside the 5s
        # lead window, so this slot's trigger point has already passed.
        now = _ts(10, 4, 57)
        assert _next_slot_trigger_delay(now) == pytest.approx(298.0)

    def test_delay_exactly_at_boundary(self) -> None:
        now = _ts(10, 5, 0)
        assert _next_slot_trigger_delay(now) == pytest.approx(295.0)

    def test_custom_slot_and_lead(self) -> None:
        now = _ts(10, 0, 0)
        assert _next_slot_trigger_delay(now, slot_minutes=1, lead_seconds=2) == pytest.approx(58.0)

    def test_uses_shared_slot_minutes_constant(self) -> None:
        """SLOT_MINUTES must be the same 5-minute grid as the history path
        (sensor_utils.SLOT_MINUTES) -- not a separately invented interval."""
        assert SLOT_MINUTES == 5
        assert SLOT_TIMER_LEAD_SECONDS == 5
