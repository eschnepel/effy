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
smooth_zero_noise = _calculation.smooth_zero_noise


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


class TestSmoothZeroNoise:
    """ADR-009: 25%-neighbor-steal smoothing for low-resolution kWh sensors."""

    def test_alternating_single_zeros_becomes_constant(self) -> None:
        """[10, 0, 10, 0, 10, 0, 10, 0] -> constant 5 for all interior slots.

        The two series ends only have one neighbor to draw from / donate to,
        so they land slightly off from the interior value - this is the
        expected, documented boundary behaviour, not a bug.
        """
        values = [10.0, 0.0, 10.0, 0.0, 10.0, 0.0, 10.0, 0.0]
        result = smooth_zero_noise(values)
        # Interior slots (indices 1..6) all converge to exactly 5.0
        for v in result[1:-1]:
            assert v == pytest.approx(5.0)

    def test_sum_is_conserved(self) -> None:
        """Smoothing only redistributes energy, never creates or destroys it."""
        values = [10.0, 0.0, 10.0, 0.0, 0.0, 10.0, 0.0, 3.0]
        result = smooth_zero_noise(values)
        assert sum(result) == pytest.approx(sum(values))

    def test_no_zeros_is_a_no_op(self) -> None:
        values = [4.0, 5.0, 6.0, 7.0]
        assert smooth_zero_noise(values) == values

    def test_all_zeros_stays_all_zero(self) -> None:
        """Nothing to steal from when every slot is 0 - no NaN, no crash."""
        values = [0.0, 0.0, 0.0, 0.0]
        result = smooth_zero_noise(values)
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_short_series_returned_unchanged(self) -> None:
        assert smooth_zero_noise([]) == []
        assert smooth_zero_noise([5.0]) == [5.0]

    def test_isolated_single_zero_is_untouched_by_round_two(self) -> None:
        """A single isolated zero (surrounded by non-zero values on both
        sides, i.e. not part of a run of 2+) is entirely handled by round 1.
        Round 2 must not touch it, or it would reintroduce the exact
        boundary-driven unevenness round 1 already fixed.
        """
        values = [0.0, 20.0, 20.0]
        result = smooth_zero_noise(values)
        # Round 1 only: v0 has no left neighbor, gains 0.25*20=5 -> 5.0;
        # v1 loses 5 (to v0's steal) -> 15.0. v0's run length is 1 (only
        # index 0 is zero), so round 2 skips it entirely.
        assert result[0] == pytest.approx(5.0)
        assert result[1] == pytest.approx(15.0)
        assert result[2] == pytest.approx(20.0)  # never a neighbor of any zero slot
        assert sum(result) == pytest.approx(sum(values))

    def test_double_zero_gap_uses_wider_round_two_neighborhood(self) -> None:
        """A run of two consecutive zeros ([10, 0, 0, 10]) is only partially
        smoothed by round 1 alone ([7.5, 2.5, 2.5, 7.5]) since each zero's
        same-run neighbor was still 0 during round 1. Round 2 (10% from +-1,
        5% from +-2) pulls the two middle values up further, while still
        preserving the total sum.
        """
        values = [10.0, 0.0, 0.0, 10.0]
        result = smooth_zero_noise(values)
        assert result == pytest.approx([6.375, 3.625, 3.625, 6.375])
        assert sum(result) == pytest.approx(sum(values))

    def test_larger_gap_mask_uses_run_length_not_recomputed_zero_check(self) -> None:
        """Round 2 must target slots based on the *original* zero-run
        length, not on whether the value is still exactly 0.0 after round 1
        (round 1 already moves every zero away from 0.0, so a naive 'is this
        currently 0.0' check would find nothing to do for any gap size and
        silently become a no-op for exactly the case round 2 exists for).
        """
        values = [10.0, 0.0, 0.0, 10.0]
        result = smooth_zero_noise(values)
        round1_only = [7.5, 2.5, 2.5, 7.5]
        assert result != pytest.approx(round1_only)

    def test_round_two_reaches_two_slots_away(self) -> None:
        """A three-zero run pulls from its +-2 neighbors in round 2, not
        just its immediate +-1 neighbors - i.e. a non-adjacent value can
        still be affected by a gap two slots away.
        """
        values = [10.0, 0.0, 0.0, 0.0, 10.0]
        result = smooth_zero_noise(values)
        # The two outer 10s are each a +-2 neighbor of the far end of the
        # 3-zero run, so both must move from their original 10.0.
        assert result[0] != pytest.approx(10.0)
        assert result[4] != pytest.approx(10.0)
        assert sum(result) == pytest.approx(sum(values))
