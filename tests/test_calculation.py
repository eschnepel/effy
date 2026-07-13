"""Unit tests for the Effy calculation engine.

calculation.py has zero Home Assistant dependencies, so it is loaded directly
by file path. This avoids importing custom_components.effy.__init__, which
pulls in homeassistant.* and is not installed in a plain test environment.
See ADR-000 §6 for the full testing-philosophy rationale (zero mocking,
direct file-path import, invariant assertions).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    # Static-only import so mypy can resolve SensorReading as a real type.
    # Not executed at runtime — the actual module is loaded by file path
    # below (ADR-000 §6) to avoid importing homeassistant via __init__.py.
    from effy.calculation import SensorReading as SensorReading

_calc_path = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
)
_spec = importlib.util.spec_from_file_location("effy_calculation", _calc_path)
assert _spec is not None and _spec.loader is not None
_calculation = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation"] = _calculation
_spec.loader.exec_module(_calculation)

if not TYPE_CHECKING:
    SensorReading = _calculation.SensorReading
distribute_loss = _calculation.distribute_loss
effective_in_original_unit = _calculation.effective_in_original_unit
trapezoidal_slot_contributions = _calculation.trapezoidal_slot_contributions
TRAPEZOID_MAX_MINUTES = _calculation.TRAPEZOID_MAX_MINUTES
interpolate_slot_gaps = _calculation.interpolate_slot_gaps
INTERPOLATION_MAX_GAP_SLOTS = _calculation.INTERPOLATION_MAX_GAP_SLOTS


def _r(eid: str, value: float, unit: str = "W") -> SensorReading:
    return SensorReading(entity_id=eid, raw_value=value, original_unit=unit)


class TestBasicExample:
    def setup_method(self) -> None:
        self.inputs = [
            _r("pv_roof", 800),
            _r("pv_carport", 200),
            _r("bms_bat_in", 0),
            _r("bms_grid_in", 100),
        ]
        self.outputs = [_r("bms_bat_out", 600), _r("bms_grid_out", 350)]
        self.dist = distribute_loss(self.inputs, self.outputs)

    def test_total_loss(self) -> None:
        assert self.dist.total_loss_w == pytest.approx(150.0)

    def test_equal_shares(self) -> None:
        assert self.dist.shares["pv_roof"] == pytest.approx(50.0)
        assert self.dist.shares["pv_carport"] == pytest.approx(50.0)
        assert self.dist.shares["bms_grid_in"] == pytest.approx(50.0)

    def test_zero_sensor_gets_no_share(self) -> None:
        assert self.dist.shares["bms_bat_in"] == pytest.approx(0.0)

    def test_effective_values(self) -> None:
        assert self.dist.effective_values_w["pv_roof"] == pytest.approx(750.0)
        assert self.dist.effective_values_w["pv_carport"] == pytest.approx(150.0)
        assert self.dist.effective_values_w["bms_bat_in"] == pytest.approx(0.0)
        assert self.dist.effective_values_w["bms_grid_in"] == pytest.approx(50.0)

    def test_sum_identity(self) -> None:
        eff_sum = sum(self.dist.effective_values_w.values())
        assert eff_sum == pytest.approx(950.0, abs=1e-6)


class TestWaterfallHardOverflow:
    """bms_grid_in = 5 W clearly below its share → goes to 0 (ADR-001 waterfall overflow)."""

    def setup_method(self) -> None:
        self.inputs = [
            _r("pv_roof", 800),
            _r("pv_carport", 200),
            _r("bms_bat_in", 0),
            _r("bms_grid_in", 5),
        ]
        self.outputs = [_r("bms_bat_out", 600), _r("bms_grid_out", 350)]
        self.dist = distribute_loss(self.inputs, self.outputs)

    def test_total_loss(self) -> None:
        assert self.dist.total_loss_w == pytest.approx(55.0)

    def test_small_sensor_fully_consumed(self) -> None:
        # equal_share = 55/3 ≈ 18.33, bms_grid_in=5 < 18.33
        # → shares: grid=5, remaining=50/2=25 for carport and roof
        assert self.dist.shares["bms_grid_in"] == pytest.approx(5.0)
        assert self.dist.shares["pv_carport"] == pytest.approx(25.0)
        assert self.dist.shares["pv_roof"] == pytest.approx(25.0)

    def test_effective_zero_for_small_sensor(self) -> None:
        assert self.dist.effective_values_w["bms_grid_in"] == pytest.approx(0.0)

    def test_sum_identity(self) -> None:
        eff_sum = sum(self.dist.effective_values_w.values())
        assert eff_sum == pytest.approx(950.0, abs=1e-6)


class TestNegativeLossCapped:
    """Output exceeds input due to measurement noise → loss capped at 0 (ADR-005)."""

    def test_no_loss_distributed(self) -> None:
        inputs = [_r("pv", 500), _r("grid", 100)]
        outputs = [_r("battery", 700)]
        dist = distribute_loss(inputs, outputs)
        assert dist.total_loss_w == pytest.approx(0.0)
        for eid, eff in dist.effective_values_w.items():
            src = next(r for r in inputs if r.entity_id == eid)
            assert eff == pytest.approx(src.raw_value)


class TestAllZeroInputs:
    def test_zero_inputs(self) -> None:
        inputs = [_r("pv", 0), _r("grid", 0)]
        outputs = [_r("battery", 0)]
        dist = distribute_loss(inputs, outputs)
        assert dist.total_loss_w == pytest.approx(0.0)
        for v in dist.effective_values_w.values():
            assert v == pytest.approx(0.0)


class TestKwNormalization:
    """kW/kWh sensors normalize to W internally and convert back on output (ADR-002)."""

    def test_kw_normalized(self) -> None:
        inputs = [_r("pv_roof", 0.8, "kW"), _r("pv_carport", 0.2, "kW")]
        outputs = [_r("load", 0.95, "kW")]
        dist = distribute_loss(inputs, outputs)
        assert dist.total_loss_w == pytest.approx(50.0)

    def test_effective_value_in_original_unit(self) -> None:
        inputs = [_r("pv", 1.0, "kW")]
        outputs = [_r("load", 0.8, "kW")]
        dist = distribute_loss(inputs, outputs)
        eff = effective_in_original_unit("pv", dist, "kW")
        assert eff == pytest.approx(0.8)


class TestSingleSensor:
    def test_single_input_absorbs_all_loss(self) -> None:
        inputs = [_r("pv", 1000)]
        outputs = [_r("load", 900)]
        dist = distribute_loss(inputs, outputs)
        assert dist.total_loss_w == pytest.approx(100.0)
        assert dist.shares["pv"] == pytest.approx(100.0)
        assert dist.effective_values_w["pv"] == pytest.approx(900.0)

    def test_effective_equals_output(self) -> None:
        inputs = [_r("pv", 1000)]
        outputs = [_r("load", 900)]
        dist = distribute_loss(inputs, outputs)
        assert sum(dist.effective_values_w.values()) == pytest.approx(900.0)


def _ts(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2024, 1, 1, h, m, s, tzinfo=timezone.utc)


class TestTrapezoidalSlotContributions:
    """ADR-012: trapezoidal-rule energy redistribution, replaces ADR-009's
    neighbor-steal smoothing."""

    def test_transition_fully_within_one_slot(self) -> None:
        """A 0.3 delta over 3 minutes, entirely inside [10:00, 10:05), goes
        entirely to that one slot."""
        raw = [(_ts(10, 0), "100.0"), (_ts(10, 3), "100.3")]
        result = trapezoidal_slot_contributions(raw)
        assert result == pytest.approx({_ts(10, 0): 0.3})

    def test_transition_spanning_two_slots_splits_by_overlap(self) -> None:
        """A 0.4 delta over 4 minutes crossing the 10:05 boundary (2 min in
        each slot) splits evenly, proportional to the time overlap."""
        raw = [(_ts(10, 3), "100.0"), (_ts(10, 7), "100.4")]
        result = trapezoidal_slot_contributions(raw)
        assert result == pytest.approx({_ts(10, 0): 0.2, _ts(10, 5): 0.2})

    def test_normal_gap_over_15_minutes_is_capped_and_anchored_at_end(self) -> None:
        """A valid-to-valid gap of 30 minutes (no offline in between) must
        NOT spread across the full 30 minutes -- only the last 15, anchored
        at t2. The three slots before the 15-minute window get nothing."""
        raw = [(_ts(10, 0), "100.0"), (_ts(10, 30), "101.0")]
        result = trapezoidal_slot_contributions(raw)
        assert result == pytest.approx({_ts(10, 15): 1 / 3, _ts(10, 20): 1 / 3, _ts(10, 25): 1 / 3})
        assert _ts(10, 0) not in result
        assert _ts(10, 5) not in result
        assert _ts(10, 10) not in result
        assert sum(result.values()) == pytest.approx(1.0)

    def test_offline_gap_spreads_over_the_full_span_uncapped(self) -> None:
        """If the sensor was unavailable before the new reading, the delta
        spreads across the *entire* gap (here 35 min = 7 slots), not just
        the last 15 minutes -- this is the key difference from the
        no-offline case above."""
        raw = [
            (_ts(10, 0), "100.0"),
            (_ts(10, 5), "unavailable"),
            (_ts(10, 35), "101.0"),
        ]
        result = trapezoidal_slot_contributions(raw)
        expected_slots = [_ts(10, m) for m in (0, 5, 10, 15, 20, 25, 30)]
        assert set(result.keys()) == set(expected_slots)
        for slot in expected_slots:
            assert result[slot] == pytest.approx(1.0 / 7)
        assert sum(result.values()) == pytest.approx(1.0)

    def test_unknown_state_also_triggers_offline_handling(self) -> None:
        """ "unknown" must be treated the same as "unavailable" for offline detection."""
        raw = [(_ts(10, 0), "50.0"), (_ts(10, 10), "unknown"), (_ts(10, 40), "51.0")]
        result = trapezoidal_slot_contributions(raw)
        # Full 40-minute span (8 slots), uncapped, not the 15-minute cap.
        assert sum(result.values()) == pytest.approx(1.0)
        assert len(result) == 8

    def test_counter_reset_produces_no_negative_contribution(self) -> None:
        """A decrease is treated as a counter reset -- no contribution for
        that transition, and the lower value becomes the new baseline for
        whatever comes after it."""
        raw = [(_ts(10, 0), "100.0"), (_ts(10, 5), "50.0"), (_ts(10, 10), "55.0")]
        result = trapezoidal_slot_contributions(raw)
        # Only the second transition (50.0 -> 55.0, delta=5) contributes.
        assert result == pytest.approx({_ts(10, 5): 5.0})

    def test_multiple_transitions_in_the_same_slot_are_summed(self) -> None:
        raw = [(_ts(10, 0), "0.0"), (_ts(10, 1), "0.1"), (_ts(10, 2), "0.3")]
        result = trapezoidal_slot_contributions(raw)
        assert result == pytest.approx({_ts(10, 0): 0.3})

    def test_transition_ending_exactly_on_a_slot_boundary(self) -> None:
        """A transition ending exactly at 10:00 must not leak into the
        [10:00, 10:05) slot -- the delta accumulated strictly before the
        boundary."""
        raw = [(_ts(9, 58), "0.0"), (_ts(10, 0), "1.0")]
        result = trapezoidal_slot_contributions(raw)
        assert result == pytest.approx({_ts(9, 55): 1.0})
        assert _ts(10, 0) not in result

    def test_fewer_than_two_valid_readings_yields_no_contributions(self) -> None:
        assert trapezoidal_slot_contributions([]) == {}
        assert trapezoidal_slot_contributions([(_ts(10, 0), "100.0")]) == {}
        assert (
            trapezoidal_slot_contributions([(_ts(10, 0), "unavailable"), (_ts(10, 5), "unknown")])
            == {}
        )

    def test_zero_delta_produces_no_contribution(self) -> None:
        raw = [(_ts(10, 0), "100.0"), (_ts(10, 5), "100.0")]
        assert trapezoidal_slot_contributions(raw) == {}

    def test_custom_slot_and_cap_minutes(self) -> None:
        raw = [(_ts(10, 0), "0.0"), (_ts(10, 20), "2.0")]
        result = trapezoidal_slot_contributions(raw, slot_minutes=10, max_minutes=10)
        # capped to the last 10 minutes -> one 10-minute slot at 10:10
        assert result == pytest.approx({_ts(10, 10): 2.0})


class TestInterpolateSlotGaps:
    """Gap-interpolation for power-family INPUT sensors' `mean` series."""

    def test_single_slot_gap_filled_with_midpoint(self) -> None:
        values = {_ts(10, 0): 100.0, _ts(10, 10): 200.0}
        result = interpolate_slot_gaps(values)
        assert result == pytest.approx(
            {_ts(10, 0): 100.0, _ts(10, 5): 150.0, _ts(10, 10): 200.0}
        )

    def test_two_slot_gap_filled_with_thirds(self) -> None:
        values = {_ts(10, 0): 0.0, _ts(10, 15): 300.0}
        result = interpolate_slot_gaps(values)
        assert result == pytest.approx(
            {_ts(10, 0): 0.0, _ts(10, 5): 100.0, _ts(10, 10): 200.0, _ts(10, 15): 300.0}
        )

    def test_gap_longer_than_max_is_left_untouched(self) -> None:
        """A 3-slot gap exceeds INTERPOLATION_MAX_GAP_SLOTS (2) -> nothing
        in between gets filled, the two known points are untouched."""
        values = {_ts(10, 0): 0.0, _ts(10, 20): 400.0}
        result = interpolate_slot_gaps(values)
        assert result == pytest.approx({_ts(10, 0): 0.0, _ts(10, 20): 400.0})
        assert len(result) == 2

    def test_adjacent_slots_are_unaffected(self) -> None:
        """No gap at all -> nothing added, values pass through unchanged."""
        values = {_ts(10, 0): 5.0, _ts(10, 5): 7.0, _ts(10, 10): 9.0}
        result = interpolate_slot_gaps(values)
        assert result == pytest.approx(values)

    def test_leading_and_trailing_gaps_are_never_extrapolated(self) -> None:
        """Only gaps *between* two known points are filled; there is no
        second point to interpolate against before the first, or after
        the last, known slot."""
        values = {_ts(10, 10): 50.0}
        result = interpolate_slot_gaps(values)
        assert result == {_ts(10, 10): 50.0}

    def test_fewer_than_two_known_slots_returns_copy(self) -> None:
        assert interpolate_slot_gaps({}) == {}
        single = {_ts(10, 0): 42.0}
        result = interpolate_slot_gaps(single)
        assert result == single
        assert result is not single

    def test_original_dict_is_not_mutated(self) -> None:
        values = {_ts(10, 0): 0.0, _ts(10, 10): 10.0}
        original = dict(values)
        interpolate_slot_gaps(values)
        assert values == original

    def test_multiple_gaps_in_one_series_each_handled_independently(self) -> None:
        values = {_ts(10, 0): 0.0, _ts(10, 10): 100.0, _ts(10, 15): 200.0}
        result = interpolate_slot_gaps(values)
        assert result == pytest.approx(
            {
                _ts(10, 0): 0.0,
                _ts(10, 5): 50.0,  # interpolated: gap between 10:00 and 10:10
                _ts(10, 10): 100.0,
                _ts(10, 15): 200.0,  # adjacent to 10:10, no gap
            }
        )

    def test_custom_slot_minutes_and_max_gap_slots(self) -> None:
        values = {_ts(10, 0): 0.0, _ts(10, 30): 3.0}
        result = interpolate_slot_gaps(values, slot_minutes=10, max_gap_slots=2)
        assert result == pytest.approx(
            {_ts(10, 0): 0.0, _ts(10, 10): 1.0, _ts(10, 20): 2.0, _ts(10, 30): 3.0}
        )

    def test_default_matches_interpolation_max_gap_slots_constant(self) -> None:
        assert INTERPOLATION_MAX_GAP_SLOTS == 2
