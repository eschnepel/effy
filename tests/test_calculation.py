"""Unit tests for the Effy calculation engine.

calculation.py has zero Home Assistant dependencies, so it is loaded directly
by file path. This avoids importing custom_components.effy.__init__, which
pulls in homeassistant.* and is not installed in a plain test environment.
See ADR-000 §6 for the full testing-philosophy rationale (zero mocking,
direct file-path import, invariant assertions).
"""

from __future__ import annotations

import importlib.util
import inspect
import math
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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
split_signed_reading = _calculation.split_signed_reading
split_eps_readings = _calculation.split_eps_readings
household_consumption = _calculation.household_consumption
effective_in_original_unit = _calculation.effective_in_original_unit
trapezoidal_slot_contributions = _calculation.trapezoidal_slot_contributions
trapezoidal_jump_windows = _calculation.trapezoidal_jump_windows
recent_rewrite_start = _calculation.recent_rewrite_start
recent_trigger_from = _calculation.recent_trigger_from
drop_open_slot = _calculation.drop_open_slot
_parse_energy_state = _calculation._parse_energy_state
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


class TestSplitSignedReading:
    """The EPS sensor's signed reading becomes an input half and an output half (ADR-026)."""

    def test_positive_value_is_an_eps_output(self) -> None:
        eps_in, eps_out = split_signed_reading(_r("eps", 200))
        assert eps_in.raw_value == 0.0
        assert eps_out.raw_value == 200.0

    def test_negative_value_is_an_eps_input(self) -> None:
        eps_in, eps_out = split_signed_reading(_r("eps", -150))
        assert eps_in.raw_value == 150.0
        assert eps_out.raw_value == 0.0

    def test_zero_gives_two_zero_halves(self) -> None:
        eps_in, eps_out = split_signed_reading(_r("eps", 0))
        assert eps_in.raw_value == 0.0
        assert eps_out.raw_value == 0.0

    @pytest.mark.parametrize("value", [-300.0, -0.5, 0.0, 0.5, 300.0])
    def test_halves_recombine_and_at_most_one_is_set(self, value: float) -> None:
        eps_in, eps_out = split_signed_reading(_r("eps", value))
        assert eps_in.raw_value >= 0.0
        assert eps_out.raw_value >= 0.0
        assert eps_in.raw_value == 0.0 or eps_out.raw_value == 0.0
        assert eps_out.raw_value - eps_in.raw_value == pytest.approx(value)

    @pytest.mark.parametrize("value", [-5.0, 0.0, 5.0])
    def test_a_zero_half_is_never_a_negative_zero(self, value: float) -> None:
        for half in split_signed_reading(_r("eps", value)):
            assert math.copysign(1.0, half.raw_value) == 1.0

    @pytest.mark.parametrize("unit", ["W", "kW"])
    def test_halves_keep_entity_id_and_unit_and_do_not_convert(self, unit: str) -> None:
        eps_in, eps_out = split_signed_reading(_r("sensor.eps", -0.25, unit))
        assert eps_in.entity_id == eps_out.entity_id == "sensor.eps"
        assert eps_in.original_unit == eps_out.original_unit == unit
        assert eps_in.raw_value == 0.25

    def test_the_reading_is_not_mutated(self) -> None:
        reading = _r("eps", -40)
        split_signed_reading(reading)
        assert reading.raw_value == -40
        assert reading.original_unit == "W"


class TestSplitEpsReadings:
    """One slot's readings with the signed EPS reading among the inputs (ADR-026 decision 2)."""

    def test_a_negative_eps_reading_becomes_an_input_half_in_place(self) -> None:
        inputs = [_r("pv", 500), _r("eps", -150), _r("grid", 1000)]
        outputs = [_r("charge", 1300)]
        new_inputs, new_outputs = split_eps_readings("eps", inputs, outputs)
        assert [r.entity_id for r in new_inputs] == ["pv", "eps", "grid"]  # same place
        assert [r.raw_value for r in new_inputs] == [500, 150, 1000]
        assert new_outputs == outputs  # no output half

    def test_a_positive_eps_reading_is_an_input_of_zero_and_an_appended_output(self) -> None:
        inputs = [_r("pv", 500), _r("eps", 200)]
        outputs = [_r("charge", 600)]
        new_inputs, new_outputs = split_eps_readings("eps", inputs, outputs)
        assert [r.raw_value for r in new_inputs] == [500, 0.0]  # the sensor keeps its row
        assert [(r.entity_id, r.raw_value) for r in new_outputs] == [("charge", 600), ("eps", 200)]

    def test_a_zero_eps_reading_adds_no_output(self) -> None:
        new_inputs, new_outputs = split_eps_readings("eps", [_r("eps", 0.0)], [_r("charge", 1)])
        assert [r.raw_value for r in new_inputs] == [0.0]
        assert [r.entity_id for r in new_outputs] == ["charge"]

    def test_a_slot_without_an_eps_reading_is_unchanged(self) -> None:
        inputs, outputs = [_r("pv", 500)], [_r("charge", 400)]
        new_inputs, new_outputs = split_eps_readings("eps", inputs, outputs)
        assert new_inputs == inputs and new_outputs == outputs

    def test_the_given_lists_are_not_mutated_and_the_unit_is_kept(self) -> None:
        inputs, outputs = [_r("eps", 0.2, "kW")], [_r("charge", 1.0, "kW")]
        new_inputs, new_outputs = split_eps_readings("eps", inputs, outputs)
        assert inputs[0].raw_value == 0.2 and len(outputs) == 1
        assert new_outputs[-1].original_unit == "kW" and new_outputs[-1].raw_value == 0.2
        assert new_inputs is not inputs and new_outputs is not outputs

    def test_the_result_feeds_distribute_loss_as_the_formula_says(self) -> None:
        # 1000 W + 500 W in, 1300 W out, 150 W fed in through the port: loss 350 W
        inputs = [_r("grid", 1000), _r("pv", 500), _r("eps", -150)]
        new_inputs, new_outputs = split_eps_readings("eps", inputs, [_r("charge", 1300)])
        assert distribute_loss(new_inputs, new_outputs).total_loss_w == pytest.approx(350.0)


class TestEpsFormula:
    """What the split does to the loss formula and the waterfall (ADR-026, ADR-001, ADR-005).

    Base case without an EPS sensor: grid import 1000 W and PV 500 W in,
    charge 1300 W out, so the loss is 200 W.
    """

    @staticmethod
    def _base_inputs() -> list[SensorReading]:
        return [_r("grid_import", 1000), _r("pv", 500)]

    @staticmethod
    def _with_eps(value: float, unit: str = "W") -> tuple[list[SensorReading], list[SensorReading]]:
        eps_in, eps_out = split_signed_reading(_r("eps", value, unit))
        inputs = [*TestEpsFormula._base_inputs(), eps_in]
        outputs = [_r("charge", 1300), eps_out]
        return inputs, outputs

    def test_base_case(self) -> None:
        dist = distribute_loss(self._base_inputs(), [_r("charge", 1300)])
        assert dist.total_loss_w == pytest.approx(200.0)

    def test_an_eps_output_lowers_the_loss_by_its_power(self) -> None:
        # 200 W leave through the EPS port: that energy is no longer "loss".
        inputs, outputs = self._with_eps(200)
        dist = distribute_loss(inputs, outputs)
        assert dist.total_loss_w == pytest.approx(0.0)

    def test_a_smaller_eps_output_lowers_it_by_exactly_that_much(self) -> None:
        inputs, outputs = self._with_eps(120)
        dist = distribute_loss(inputs, outputs)
        assert dist.total_loss_w == pytest.approx(200.0 - 120.0)

    def test_an_eps_output_above_the_loss_is_capped_at_zero_loss(self) -> None:
        inputs, outputs = self._with_eps(500)
        dist = distribute_loss(inputs, outputs)
        assert dist.total_loss_w == 0.0
        assert all(share == 0.0 for share in dist.shares.values())

    def test_an_eps_input_raises_the_input_total_and_takes_an_equal_share(self) -> None:
        inputs, outputs = self._with_eps(-150)
        dist = distribute_loss(inputs, outputs)
        # 1650 W in, 1300 W out: loss 350 W, shared 350 / 3 each (150 W can bear it).
        assert dist.total_loss_w == pytest.approx(350.0)
        assert dist.shares["eps"] == pytest.approx(350.0 / 3)
        assert dist.shares["grid_import"] == pytest.approx(350.0 / 3)
        assert dist.shares["pv"] == pytest.approx(350.0 / 3)
        assert dist.effective_values_w["eps"] == pytest.approx(150.0 - 350.0 / 3)

    def test_an_eps_input_too_small_for_its_share_passes_the_rest_on(self) -> None:
        inputs, outputs = self._with_eps(-50)
        dist = distribute_loss(inputs, outputs)
        # 1550 W in, 1300 W out: loss 250 W; 50 W absorbs only its own value.
        assert dist.total_loss_w == pytest.approx(250.0)
        assert dist.shares["eps"] == pytest.approx(50.0)
        assert dist.shares["grid_import"] == pytest.approx(100.0)
        assert dist.shares["pv"] == pytest.approx(100.0)
        assert dist.effective_values_w["eps"] == pytest.approx(0.0)

    @pytest.mark.parametrize("value", [-150.0, -50.0, 0.0, 120.0, 200.0])
    def test_effective_inputs_equal_outputs(self, value: float) -> None:
        inputs, outputs = self._with_eps(value)
        dist = distribute_loss(inputs, outputs)
        total_out = sum(r.raw_value for r in outputs)
        if dist.total_loss_w > 0.0:
            assert sum(dist.effective_values_w.values()) == pytest.approx(total_out)

    def test_an_exporting_port_is_an_input_of_zero_that_takes_no_share(self) -> None:
        inputs, outputs = self._with_eps(120)
        dist = distribute_loss(inputs, outputs)
        assert dist.shares["eps"] == 0.0
        assert dist.effective_values_w["eps"] == 0.0
        assert "eps" in dist.effective_values_w  # the sensor keeps a row

    def test_kw_sensor_gives_the_same_result_as_w(self) -> None:
        in_w, out_w = self._with_eps(-150)
        in_kw, out_kw = self._with_eps(-0.15, "kW")
        dist_w = distribute_loss(in_w, out_w)
        dist_kw = distribute_loss(in_kw, out_kw)
        assert dist_kw.total_loss_w == pytest.approx(dist_w.total_loss_w)
        assert effective_in_original_unit("eps", dist_kw, "kW") == pytest.approx(
            dist_w.effective_values_w["eps"] / 1000.0
        )

    def test_without_the_halves_a_negative_reading_would_be_wrong(self) -> None:
        # Documents why the split exists: a signed value in the outputs adds to the loss
        # instead of acting as an input that takes a share.
        dist = distribute_loss(self._base_inputs(), [_r("charge", 1300), _r("eps", -150)])
        assert dist.total_loss_w == pytest.approx(350.0)
        assert "eps" not in dist.shares


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

    def test_normal_gap_over_the_cap_is_capped_with_zero_filled_prefix(self) -> None:
        """A valid-to-valid gap longer than max_minutes (no offline in
        between) must NOT spread the real delta across the full gap --
        only the last max_minutes, anchored at t2. Per ADR-013, the slots
        before that window are no longer left with no entry at all: the
        counter was genuinely sitting at v1 with no jump yet, so they get
        an explicit 0.0 rather than nothing. Uses an explicit max_minutes
        so this test exercises the capping *mechanism* regardless of
        whatever TRAPEZOID_MAX_MINUTES' actual default happens to be."""
        raw = [(_ts(10, 0), "100.0"), (_ts(10, 30), "101.0")]
        result = trapezoidal_slot_contributions(raw, max_minutes=15)
        assert result == pytest.approx(
            {
                _ts(10, 0): 0.0,
                _ts(10, 5): 0.0,
                _ts(10, 10): 0.0,
                _ts(10, 15): 1 / 3,
                _ts(10, 20): 1 / 3,
                _ts(10, 25): 1 / 3,
            }
        )
        assert sum(result.values()) == pytest.approx(1.0)

    def test_default_cap_is_120_minutes(self) -> None:
        """ADR-014: raised from 15 to 120 minutes -- 15 minutes was too
        tight for real low-resolution energy meters (20-90 minute tick
        intervals), compressing every tick into a visibly oscillating
        "0, then a spike" derived-power curve."""
        assert TRAPEZOID_MAX_MINUTES == 120

    def test_gap_within_the_default_120_minute_cap_is_not_capped(self) -> None:
        """A 30-minute gap -- capped under the old 15-minute rule -- must
        now spread smoothly across the *entire* gap with the default cap,
        not just the last 15 minutes, since 30 < 120."""
        raw = [(_ts(10, 0), "100.0"), (_ts(10, 30), "101.0")]
        result = trapezoidal_slot_contributions(raw)
        expected_slots = [_ts(10, m) for m in (0, 5, 10, 15, 20, 25)]
        assert set(result.keys()) == set(expected_slots)
        for slot in expected_slots:
            assert result[slot] == pytest.approx(1 / 6)
        assert sum(result.values()) == pytest.approx(1.0)

    def test_gap_over_the_default_120_minute_cap_is_capped_with_zero_filled_prefix(
        self,
    ) -> None:
        """A gap of 150 minutes (2.5h) exceeds the default 120-minute cap
        -- the last 120 minutes get the real, smooth spread, and the
        30-minute prefix before that gets explicit 0.0 (ADR-013/014)."""
        raw = [(_ts(10, 0), "100.0"), (_ts(12, 30), "101.0")]
        result = trapezoidal_slot_contributions(raw)
        prefix_slots = [_ts(10, m) for m in (0, 5, 10, 15, 20, 25)]
        window_start = _ts(10, 30)
        window_slots = [window_start + timedelta(minutes=5 * i) for i in range(24)]
        for slot in prefix_slots:
            assert result[slot] == pytest.approx(0.0)
        for slot in window_slots:
            assert result[slot] == pytest.approx(1 / 24)
        assert set(result.keys()) == set(prefix_slots) | set(window_slots)
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

    def test_offline_gap_with_zero_delta_fills_every_slot_with_zero(self) -> None:
        """Offline AND zero delta (came back to the exact same value) --
        both conditions independently call for the uncapped full-span
        window, so every slot in the gap gets an explicit 0.0, same as a
        plain (online) zero-delta gap would."""
        raw = [
            (_ts(10, 0), "100.0"),
            (_ts(10, 5), "unavailable"),
            (_ts(10, 20), "100.0"),
        ]
        result = trapezoidal_slot_contributions(raw)
        expected_slots = [_ts(10, m) for m in (0, 5, 10, 15)]
        assert set(result.keys()) == set(expected_slots)
        for slot in expected_slots:
            assert result[slot] == pytest.approx(0.0)

    def test_counter_reset_produces_zero_not_negative_contribution(self) -> None:
        """A decrease is treated as a counter reset: delta clamps to 0
        (explicit 0.0 entries, per ADR-013, not skipped), and the lower
        value becomes the new baseline for whatever comes after it."""
        raw = [(_ts(10, 0), "100.0"), (_ts(10, 5), "50.0"), (_ts(10, 10), "55.0")]
        result = trapezoidal_slot_contributions(raw)
        # First transition (100 -> 50) clamps to delta=0 -> explicit 0.0.
        # Second transition (50 -> 55, delta=5) contributes normally.
        assert result == pytest.approx({_ts(10, 0): 0.0, _ts(10, 5): 5.0})

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

    def test_zero_delta_produces_explicit_zero_contribution(self) -> None:
        """A genuinely idle reading (same value, still online) now writes
        an explicit 0.0 -- e.g. an empty battery's 0 discharge, or several
        zero-import days -- instead of no entry at all (ADR-013)."""
        raw = [(_ts(10, 0), "100.0"), (_ts(10, 5), "100.0")]
        assert trapezoidal_slot_contributions(raw) == {_ts(10, 0): 0.0}

    def test_zero_delta_over_a_long_gap_fills_every_slot(self) -> None:
        """The zero-fill is uncapped -- a multi-hour flat stretch (e.g.
        several days of 0 grid import) gets an explicit 0.0 for every
        slot, not just the last max_minutes."""
        raw = [(_ts(10, 0), "5.0"), (_ts(11, 0), "5.0")]  # flat for 1 hour
        result = trapezoidal_slot_contributions(raw)
        assert len(result) == 12  # 12 five-minute slots in one hour
        assert all(v == pytest.approx(0.0) for v in result.values())

    def test_custom_slot_and_cap_minutes(self) -> None:
        raw = [(_ts(10, 0), "0.0"), (_ts(10, 20), "2.0")]
        result = trapezoidal_slot_contributions(raw, slot_minutes=10, max_minutes=10)
        # capped to the last 10 minutes -> one 10-minute slot at 10:10,
        # zero-filled prefix at 10:00 (ADR-013)
        assert result == pytest.approx({_ts(10, 0): 0.0, _ts(10, 10): 2.0})

    def test_now_param_fills_slots_since_last_reading_when_no_new_value_arrived(
        self,
    ) -> None:
        """No new reading since the last valid one -- with `now` given,
        this is treated as a zero-delta continuation up to `now`, filling
        the slots in between with explicit 0.0 (this is what lets the
        slot-timer-driven recalc show 0, not "no data", for a sensor that
        simply hasn't ticked yet)."""
        raw = [(_ts(10, 0), "42.0")]
        result = trapezoidal_slot_contributions(raw, now=_ts(10, 15))
        assert result == pytest.approx({_ts(10, 0): 0.0, _ts(10, 5): 0.0, _ts(10, 10): 0.0})

    def test_now_param_ignored_when_last_reading_is_currently_invalid(self) -> None:
        """A sensor that is currently unavailable/unknown must NOT get a
        synthetic zero-delta continuation -- we genuinely don't know its
        value, so it's better left with no contribution than assumed 0."""
        raw = [(_ts(10, 0), "42.0"), (_ts(10, 5), "unavailable")]
        assert trapezoidal_slot_contributions(raw, now=_ts(10, 20)) == {}

    def test_now_param_no_effect_when_a_real_transition_already_reaches_now(self) -> None:
        """`now` only synthesizes a continuation when the last reading
        predates it -- if the last reading already *is* `now` (or later),
        nothing extra is added."""
        raw = [(_ts(10, 0), "0.0"), (_ts(10, 5), "1.0")]
        with_now = trapezoidal_slot_contributions(raw, now=_ts(10, 5))
        without_now = trapezoidal_slot_contributions(raw)
        assert with_now == pytest.approx(without_now)

    def test_now_param_combines_with_a_real_transition_before_it(self) -> None:
        """A real jump followed by a still-idle stretch up to `now`: the
        real transition contributes its own (possibly capped) window as
        before, and the synthetic zero-delta continuation separately fills
        the slots since that jump up to `now`."""
        raw = [(_ts(10, 0), "0.0"), (_ts(10, 5), "1.0")]
        result = trapezoidal_slot_contributions(raw, now=_ts(10, 15))
        assert result == pytest.approx({_ts(10, 0): 1.0, _ts(10, 5): 0.0, _ts(10, 10): 0.0})


class TestTrapezoidalJumpWindows:
    """ADR-014 Amendment 2026-10-05: expose each real jump's distribution
    window (t2, window_start) so the slot-timer path knows how far back a
    newly arrived jump changes already-written slots."""

    def test_normal_jump_within_cap_starts_at_t1(self) -> None:
        raw = [(_ts(10, 0), "5.00"), (_ts(10, 35), "5.01")]
        assert trapezoidal_jump_windows(raw) == [(_ts(10, 35), _ts(10, 0))]

    def test_normal_jump_over_the_cap_is_capped_at_max_minutes(self) -> None:
        """300 min gap, no offline in between: window starts 120 min before t2."""
        raw = [(_ts(5, 0), "5.00"), (_ts(10, 0), "5.01")]
        assert trapezoidal_jump_windows(raw) == [(_ts(10, 0), _ts(8, 0))]
        assert TRAPEZOID_MAX_MINUTES == 120  # the default the line above relies on

    def test_explicit_max_minutes_is_honoured(self) -> None:
        raw = [(_ts(10, 0), "5.00"), (_ts(10, 30), "5.01")]
        assert trapezoidal_jump_windows(raw, max_minutes=15) == [(_ts(10, 30), _ts(10, 15))]

    def test_offline_recovery_jump_is_uncapped_and_starts_at_t1(self) -> None:
        raw = [
            (_ts(5, 0), "5.00"),
            (_ts(5, 30), "unavailable"),
            (_ts(10, 0), "5.01"),
        ]
        assert trapezoidal_jump_windows(raw) == [(_ts(10, 0), _ts(5, 0))]

    def test_zero_delta_transition_is_not_a_jump(self) -> None:
        raw = [(_ts(10, 0), "5.00"), (_ts(10, 30), "5.00")]
        assert trapezoidal_jump_windows(raw) == []

    def test_zero_delta_offline_recovery_is_a_trigger_starting_at_t1(self) -> None:
        """AUDIT-0001: a counter that is unchanged across an outage still
        needs the outage zero-filled (ADR-013 Decision 4), so the recovery
        reading triggers a rewrite back to the last pre-outage reading."""
        raw = [(_ts(8, 0), "5.00"), (_ts(8, 30), "unavailable"), (_ts(11, 0), "5.00")]
        assert trapezoidal_jump_windows(raw) == [(_ts(11, 0), _ts(8, 0))]

    def test_zero_delta_offline_recovery_is_uncapped(self) -> None:
        raw = [(_ts(1, 0), "5.00"), (_ts(1, 30), "unknown"), (_ts(11, 0), "5.00")]
        assert trapezoidal_jump_windows(raw) == [(_ts(11, 0), _ts(1, 0))]

    def test_counter_reset_across_an_outage_is_a_trigger(self) -> None:
        """Recovery with a *lower* reading (delta clamps to 0) is an offline
        recovery all the same: a full recalc zero-fills the outage."""
        raw = [(_ts(8, 0), "5.00"), (_ts(8, 30), "unavailable"), (_ts(11, 0), "0.02")]
        assert trapezoidal_jump_windows(raw) == [(_ts(11, 0), _ts(8, 0))]

    def test_non_numeric_state_without_recovery_is_not_a_trigger(self) -> None:
        raw = [(_ts(8, 0), "5.00"), (_ts(8, 30), "unavailable")]
        assert trapezoidal_jump_windows(raw) == []

    def test_zero_delta_after_recovery_is_still_not_a_jump(self) -> None:
        """Only the first reading after the outage is an offline recovery."""
        raw = [
            (_ts(8, 0), "5.00"),
            (_ts(8, 30), "unavailable"),
            (_ts(9, 0), "5.00"),
            (_ts(9, 30), "5.00"),
        ]
        assert trapezoidal_jump_windows(raw) == [(_ts(9, 0), _ts(8, 0))]

    def test_counter_reset_decrease_is_not_a_jump(self) -> None:
        raw = [(_ts(10, 0), "5.00"), (_ts(10, 30), "0.02")]
        assert trapezoidal_jump_windows(raw) == []

    def test_reset_becomes_new_baseline_for_the_next_jump(self) -> None:
        raw = [(_ts(10, 0), "5.00"), (_ts(10, 20), "0.02"), (_ts(10, 50), "0.03")]
        assert trapezoidal_jump_windows(raw) == [(_ts(10, 50), _ts(10, 20))]

    def test_several_jumps_come_back_in_chronological_order(self) -> None:
        raw = [
            (_ts(10, 0), "5.00"),
            (_ts(10, 30), "5.01"),
            (_ts(10, 50), "5.02"),
            (_ts(11, 9), "5.03"),
        ]
        assert trapezoidal_jump_windows(raw) == [
            (_ts(10, 30), _ts(10, 0)),
            (_ts(10, 50), _ts(10, 30)),
            (_ts(11, 9), _ts(10, 50)),
        ]

    def test_fewer_than_two_valid_readings_yield_nothing(self) -> None:
        assert trapezoidal_jump_windows([]) == []
        assert trapezoidal_jump_windows([(_ts(10, 0), "5.00")]) == []
        assert trapezoidal_jump_windows([(_ts(10, 0), "unavailable"), (_ts(10, 5), "5.00")]) == []

    def test_has_no_synthetic_now_continuation(self) -> None:
        """The ADR-013 synthetic zero-delta transition never counts as a
        jump -- and the function has no `now` parameter that could add it."""
        raw = [(_ts(10, 0), "5.00"), (_ts(10, 30), "5.01")]
        assert trapezoidal_jump_windows(raw) == [(_ts(10, 30), _ts(10, 0))]
        assert "now" not in inspect.signature(trapezoidal_jump_windows).parameters

    def test_window_start_matches_where_slot_contributions_actually_begin(self) -> None:
        """Single-source-of-truth guard (item 5): the first slot receiving a
        non-zero contribution is the slot containing window_start."""
        raw = [(_ts(5, 0), "5.00"), (_ts(10, 0), "5.01")]
        ((t2, window_start),) = trapezoidal_jump_windows(raw)
        contributions = trapezoidal_slot_contributions(raw)
        first_nonzero = min(slot for slot, value in contributions.items() if value > 0)
        assert first_nonzero == window_start
        assert t2 == _ts(10, 0)


class TestRecentRewriteStart:
    """ADR-014 Amendment 2026-10-05, items 1-4: pure rule for where the
    slot-timer path's rewrite range starts."""

    NOW = _ts(12, 0, 5)
    WINDOW = timedelta(minutes=20)
    LOOKBACK = timedelta(days=1)

    def _start(self, windows: list[tuple[datetime, datetime]]) -> datetime:
        result: datetime = recent_rewrite_start(windows, self.NOW, self.WINDOW, self.LOOKBACK)
        return result

    def test_no_jump_windows_returns_exactly_now_minus_window(self) -> None:
        assert self._start([]) == self.NOW - self.WINDOW  # 11:40:05, unaligned

    def test_recent_jump_with_older_window_start_extends_to_slot_start(self) -> None:
        # t2 inside the last 20 min; its window began 10:02:30 -> slot 10:00
        assert self._start([(_ts(11, 55), _ts(10, 2, 30))]) == _ts(10, 0)

    def test_several_jumps_return_the_global_minimum(self) -> None:
        windows = [
            (_ts(11, 50), _ts(11, 20)),  # sensor A
            (_ts(11, 58), _ts(10, 31)),  # sensor B -> earliest
            (_ts(11, 45), _ts(11, 0)),  # sensor C
        ]
        assert self._start(windows) == _ts(10, 30)

    def test_zero_delta_offline_recovery_extends_to_the_pre_outage_slot(self) -> None:
        """AUDIT-0001: fed from real raw history, not hand-made windows."""
        raw = [(_ts(8, 0), "5.00"), (_ts(8, 30), "unavailable"), (_ts(11, 55), "5.00")]
        assert self._start(trapezoidal_jump_windows(raw)) == _ts(8, 0)

    def test_jump_older_than_the_recent_window_does_not_extend(self) -> None:
        # t2 = 11:39 < now - window (11:40:05): already applied by earlier cycles
        assert self._start([(_ts(11, 39), _ts(9, 0))]) == self.NOW - self.WINDOW

    def test_jump_in_the_future_does_not_extend(self) -> None:
        assert self._start([(_ts(12, 10), _ts(9, 0))]) == self.NOW - self.WINDOW

    def test_window_start_inside_the_base_window_returns_base_unchanged(self) -> None:
        assert self._start([(_ts(11, 55), _ts(11, 45))]) == self.NOW - self.WINDOW

    def test_window_start_just_before_base_inside_the_straddling_slot(self) -> None:
        """11:40:03 < base 11:40:05 -> its slot 11:40 must be rewritten too."""
        assert self._start([(_ts(11, 55), _ts(11, 40, 3))]) == _ts(11, 40)

    def test_extension_is_clamped_to_slot_aligned_max_lookback(self) -> None:
        """An offline-recovery jump whose pre-outage reading is 3 days back."""
        result = self._start([(_ts(11, 58), self.NOW - timedelta(days=3))])
        assert result == _ts(12, 0) - self.LOOKBACK  # aligned(now - 1 day)
        assert result > self.NOW - timedelta(days=3)

    def test_clamp_never_makes_the_start_later_than_the_base(self) -> None:
        """Pathological config: lookback shorter than the base window."""
        result = recent_rewrite_start(
            [(_ts(11, 55), _ts(9, 0))],
            self.NOW,
            timedelta(minutes=20),
            timedelta(minutes=5),
        )
        assert result == self.NOW - self.WINDOW

    def test_slot_minutes_is_respected(self) -> None:
        result = recent_rewrite_start(
            [(_ts(11, 55), _ts(10, 7))],
            self.NOW,
            self.WINDOW,
            self.LOOKBACK,
            slot_minutes=15,
        )
        assert result == _ts(10, 0)


class TestRecentTriggerWindow:
    """ADR-014 Amendment 2026-10-06: the trigger window is dynamic. A jump
    that arrived while no cycle was running must still trigger."""

    NOW = _ts(12, 0, 5)
    WINDOW = timedelta(minutes=20)
    LOOKBACK = timedelta(days=1)

    def _from(self, trigger_since: datetime | None) -> datetime:
        result: datetime = recent_trigger_from(self.NOW, self.WINDOW, self.LOOKBACK, trigger_since)
        return result

    def _start(
        self, windows: list[tuple[datetime, datetime]], trigger_since: datetime | None
    ) -> datetime:
        result: datetime = recent_rewrite_start(
            windows, self.NOW, self.WINDOW, self.LOOKBACK, trigger_since=trigger_since
        )
        return result

    def test_default_is_now_minus_window(self) -> None:
        assert self._from(None) == self.NOW - self.WINDOW

    def test_recent_or_future_trigger_since_changes_nothing(self) -> None:
        """Normal 5-minute cadence: the last cycle ran inside the window."""
        assert self._from(self.NOW - timedelta(minutes=5)) == self.NOW - self.WINDOW
        assert self._from(self.NOW + timedelta(hours=1)) == self.NOW - self.WINDOW  # clock step

    def test_a_pause_reaches_back_to_the_last_completed_cycle(self) -> None:
        assert self._from(_ts(10, 30, 5)) == _ts(10, 30, 5)

    def test_never_reaches_back_further_than_the_max_lookback(self) -> None:
        assert self._from(self.NOW - timedelta(days=3)) == self.NOW - self.LOOKBACK

    def test_jump_during_a_pause_triggers_only_with_trigger_since(self) -> None:
        """The audit's case: t2 older than the window at the first cycle after
        the pause, window start far back."""
        windows = [(_ts(11, 20), _ts(10, 5))]
        assert self._start(windows, None) == self.NOW - self.WINDOW  # old behaviour: lost
        assert self._start(windows, _ts(11, 0)) == _ts(10, 5)  # last cycle was before it

    def test_jump_already_seen_by_the_last_completed_cycle_does_not_trigger(self) -> None:
        windows = [(_ts(11, 20), _ts(10, 5))]
        assert self._start(windows, _ts(11, 30)) == self.NOW - self.WINDOW

    def test_jump_older_than_the_max_lookback_does_not_turn_into_a_day_rewrite(self) -> None:
        windows = [(self.NOW - timedelta(days=2), self.NOW - timedelta(days=2, hours=2))]
        assert self._start(windows, self.NOW - timedelta(days=3)) == self.NOW - self.WINDOW

    def test_extension_is_still_clamped_to_the_max_lookback(self) -> None:
        """First cycle of a session (catch-up) + an old offline recovery."""
        windows = [(_ts(11, 58), self.NOW - timedelta(days=3))]
        result = self._start(windows, self.NOW - self.LOOKBACK)
        assert result == _ts(12, 0) - self.LOOKBACK


class TestDropOpenSlot:
    """ADR-016 Amendment 2026-10-16 (TASK-0007a): Effy writes no statistic for the
    slot that contains ``now``; the recorder inserts that row itself."""

    NOW = _ts(12, 0, 10)  # open slot 12:00, last completed slot 11:55

    def test_the_open_slot_is_removed(self) -> None:
        values = [(_ts(11, 50), 3.0), (_ts(11, 55), 7.0), (_ts(12, 0), 0.01)]
        assert drop_open_slot(values, self.NOW) == [(_ts(11, 50), 3.0), (_ts(11, 55), 7.0)]

    def test_input_is_not_mutated(self) -> None:
        values = [(_ts(11, 55), 7.0), (_ts(12, 0), 0.01)]
        drop_open_slot(values, self.NOW)
        assert values == [(_ts(11, 55), 7.0), (_ts(12, 0), 0.01)]

    def test_closed_slots_are_kept_untouched(self) -> None:
        values = [(_ts(11, 45), 1.0), (_ts(11, 50), 3.0), (_ts(11, 55), 7.0)]
        assert drop_open_slot(values, self.NOW) == values

    def test_now_exactly_on_a_boundary_opens_that_slot(self) -> None:
        values = [(_ts(11, 55), 7.0), (_ts(12, 0), 0.0)]
        assert drop_open_slot(values, _ts(12, 0)) == [(_ts(11, 55), 7.0)]

    def test_nothing_is_carried_or_invented(self) -> None:
        assert drop_open_slot([(_ts(12, 0), 0.01)], self.NOW) == []
        assert drop_open_slot([], self.NOW) == []

    def test_slots_after_the_open_one_are_kept(self) -> None:
        """Only the slot containing ``now`` is removed (a future slot cannot exist)."""
        values = [(_ts(11, 55), 7.0), (_ts(12, 0), 0.01), (_ts(12, 5), 9.0)]
        assert drop_open_slot(values, self.NOW) == [(_ts(11, 55), 7.0), (_ts(12, 5), 9.0)]

    def test_slot_minutes_is_respected(self) -> None:
        values = [(_ts(11, 45), 7.0), (_ts(12, 0), 0.01)]
        assert drop_open_slot(values, self.NOW, slot_minutes=15) == [(_ts(11, 45), 7.0)]


_SimRule = Callable[[list[tuple[datetime, datetime]], datetime], datetime]

_SIM_WINDOW = timedelta(minutes=20)
_SIM_LOOKBACK = timedelta(days=1)
# history.py's _RAW_HISTORY_BOUNDARY_MARGIN: TRAPEZOID_MAX_MINUTES + one slot
_SIM_MARGIN = timedelta(minutes=TRAPEZOID_MAX_MINUTES + 5)


def _sim_fetch(
    readings: list[tuple[datetime, str]], start: datetime, end: datetime
) -> list[tuple[datetime, str]]:
    """state_changes_during_period(include_start_time_state=True) emulation,
    plus history.py's offline-anchor lookback (ADR-014/015): if the entry
    in effect at ``start`` is non-numeric, the last numeric reading before it
    is prepended so an offline-recovery jump still knows its t1."""
    before = [r for r in readings if r[0] < start]
    head = before[-1:]
    if head and _parse_energy_state(head[0][1]) is None:
        head = [r for r in before if _parse_energy_state(r[1]) is not None][-1:] + head
    inside = [r for r in readings if start <= r[0] <= end]
    return head + inside


def _sim_rule_real(windows: list[tuple[datetime, datetime]], now: datetime) -> datetime:
    start: datetime = recent_rewrite_start(windows, now, _SIM_WINDOW, _SIM_LOOKBACK)
    return start


def _sim_rule_fixed(_windows: list[tuple[datetime, datetime]], now: datetime) -> datetime:
    """Negative control: the pre-amendment rule (always exactly the window)."""
    return now - _SIM_WINDOW


def _sim_timer_path(
    readings: list[tuple[datetime, str]],
    first_cycle: datetime,
    last_cycle: datetime,
    rule: _SimRule,
) -> dict[datetime, float]:
    """Emulate the slot timer: every 5 min recompute and overwrite the slots
    in [start, now), as history.async_recalculate_recent does."""
    store: dict[datetime, float] = {}
    now = first_cycle
    while now <= last_cycle:
        base = now - _SIM_WINDOW
        raw = _sim_fetch(readings, base - _SIM_MARGIN, now)
        start = rule(trapezoidal_jump_windows(raw), now)
        if start < base:
            raw = _sim_fetch(readings, start - _SIM_MARGIN, now)
        for slot, value in trapezoidal_slot_contributions(raw, now=now).items():
            if start <= slot < now:
                store[slot] = value
        now += timedelta(minutes=5)
    return store


def _sim_full_recalc(readings: list[tuple[datetime, str]], now: datetime) -> dict[datetime, float]:
    raw = _sim_fetch(readings, now - timedelta(days=3), now)
    return {s: v for s, v in trapezoidal_slot_contributions(raw, now=now).items() if s < now}


def _in_range(store: dict[datetime, float], lo: datetime, hi: datetime) -> dict[datetime, float]:
    return {s: v for s, v in store.items() if lo <= s < hi}


class TestSlotTimerEquivalenceSimulation:
    """ADR-014 Amendment 2026-10-05, item 7, at the algorithm level: after
    the slot-timer cycles have run, every closed slot equals what a full
    recalc produces, and the area under the curve equals the raw energy.
    Replays the BMS battery-charge screenshot (0.01 kWh ticks every 19-35
    min); history.py glue is covered by TASK-0004."""

    TICKS = [(7, 30), (10, 50), (11, 25), (11, 52), (12, 14), (12, 34), (12, 53)]

    def _readings(self, jitter_seconds: int) -> list[tuple[datetime, str]]:
        return [
            (_ts(h, m, jitter_seconds), f"{5.0 + 0.01 * i:.2f}")
            for i, (h, m) in enumerate(self.TICKS)
        ]

    @pytest.mark.parametrize("jitter_seconds", [0, 37])
    def test_timer_path_equals_full_recalc_and_conserves_energy(self, jitter_seconds: int) -> None:
        readings = self._readings(jitter_seconds)
        store = _sim_timer_path(readings, _ts(10, 0, 5), _ts(13, 5, 5), _sim_rule_real)
        full = _sim_full_recalc(readings, _ts(13, 5, 5))

        lo, hi = _ts(10, 0), _ts(13, 5)  # slot 13:05 is still in progress
        assert _in_range(store, lo, hi) == pytest.approx(_in_range(full, lo, hi))
        # 6 jumps of 0.01 kWh, all of it present in the stored series
        assert sum(_in_range(store, _ts(0, 0), hi).values()) == pytest.approx(0.06)

    def test_screenshot_peak_rates_are_unchanged(self) -> None:
        """5 W for the first jump (10 Wh over the 120 min cap), then
        10 Wh / interval -- the same peaks as the user's chart."""
        store = _sim_timer_path(self._readings(0), _ts(10, 0, 5), _ts(13, 5, 5), _sim_rule_real)
        watts = sorted({round(v * 1000 * 12) for v in store.values() if v > 0})
        assert 5 in watts and 17 in watts and 32 in watts

    def test_negative_control_fixed_window_loses_half_the_energy(self) -> None:
        """Without the amendment (always rewrite exactly the last window) the
        same simulation leaves ~half the energy -- the production bug."""
        readings = self._readings(0)
        store = _sim_timer_path(readings, _ts(10, 0, 5), _ts(13, 5, 5), _sim_rule_fixed)
        full = _sim_full_recalc(readings, _ts(13, 5, 5))
        hi = _ts(13, 5)
        assert sum(_in_range(store, _ts(0, 0), hi).values()) < 0.04
        assert _in_range(store, _ts(10, 0), hi) != pytest.approx(_in_range(full, _ts(10, 0), hi))

    def test_offline_recovery_jump_rewrites_the_whole_outage(self) -> None:
        readings = [
            (_ts(8, 0), "5.00"),
            (_ts(8, 30), "unavailable"),
            (_ts(11, 0), "5.05"),
        ]
        store = _sim_timer_path(readings, _ts(8, 0, 5), _ts(11, 35, 5), _sim_rule_real)
        full = _sim_full_recalc(readings, _ts(11, 35, 5))
        lo, hi = _ts(8, 0), _ts(11, 35)
        assert _in_range(store, lo, hi) == pytest.approx(_in_range(full, lo, hi))
        assert sum(_in_range(store, lo, hi).values()) == pytest.approx(0.05)

    def test_zero_delta_offline_recovery_zero_fills_the_whole_outage(self) -> None:
        """AUDIT-0001 finding: counter unchanged across an outage. A full
        recalc zero-fills the outage; the timer path used to leave it as
        "no data" because nothing triggered the extension."""
        readings = [
            (_ts(8, 0), "5.00"),
            (_ts(8, 30), "unavailable"),
            (_ts(11, 0), "5.00"),
        ]
        store = _sim_timer_path(readings, _ts(8, 0, 5), _ts(11, 35, 5), _sim_rule_real)
        full = _sim_full_recalc(readings, _ts(11, 35, 5))
        lo, hi = _ts(8, 0), _ts(11, 35)
        assert _in_range(full, _ts(8, 30), _ts(11, 0))  # the outage really is in the full store
        assert _in_range(store, lo, hi) == pytest.approx(_in_range(full, lo, hi))
        assert all(v == 0.0 for v in _in_range(store, _ts(8, 30), _ts(11, 0)).values())


class TestInterpolateSlotGaps:
    """Gap-interpolation for power-family INPUT sensors' `mean` series."""

    def test_single_slot_gap_filled_with_midpoint(self) -> None:
        values = {_ts(10, 0): 100.0, _ts(10, 10): 200.0}
        result = interpolate_slot_gaps(values)
        assert result == pytest.approx({_ts(10, 0): 100.0, _ts(10, 5): 150.0, _ts(10, 10): 200.0})

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


class TestHouseholdConsumption:
    """``household_consumption`` (ADR-029 decision 3, TASK-0049), zero-mock.

    ``max(0, hi - he + system export + EPS output - system import)`` in W.
    """

    @staticmethod
    def _value(
        hi: SensorReading | None,
        he: SensorReading | None = None,
        exports: list[SensorReading] | None = None,
        imports: list[SensorReading] | None = None,
        *,
        signed: bool = False,
    ) -> float | None:
        result: float | None = household_consumption(
            hi, he, exports or [], imports or [], signed_import=signed
        )
        return result

    def test_the_example_of_the_task(self) -> None:
        # hi 300 W, he 0, system export 200 W, EPS output 50 W, system import 100 W: 450 W
        value = self._value(
            _r("hi", 300),
            None,
            [_r("grid_export", 200), _r("eps", 50)],
            [_r("grid_import", 100)],
        )
        assert value == pytest.approx(450.0)

    def test_the_household_export_is_subtracted(self) -> None:
        assert self._value(_r("hi", 300), _r("he", 120)) == pytest.approx(180.0)

    def test_readings_in_kw_and_in_w_add_up_after_normalisation(self) -> None:
        value = self._value(_r("hi", 0.3, "kW"), None, [], [_r("grid_import", 100)])
        assert value == pytest.approx(200.0)
        value = self._value(_r("hi", 300), _r("he", 0.05, "kW"), [_r("grid_export", 0.2, "kW")])
        assert value == pytest.approx(450.0)

    def test_a_missing_term_counts_as_zero(self) -> None:
        assert self._value(_r("hi", 300)) == pytest.approx(300.0)
        assert self._value(_r("hi", 300), None, [], [_r("grid_import", 100)]) == pytest.approx(
            200.0
        )

    def test_no_household_import_reading_is_no_value_whatever_the_other_terms_are(self) -> None:
        assert self._value(None, _r("he", 50), [_r("grid_export", 200)], [_r("gi", 1)]) is None

    def test_a_negative_sum_is_clamped_to_zero(self) -> None:
        value = self._value(_r("hi", 100), None, [], [_r("grid_import", 400)])
        assert value == 0.0
        assert math.copysign(1.0, value or 0.0) == 1.0  # not a negative zero either

    def test_the_sum_may_be_exactly_zero(self) -> None:
        assert self._value(_r("hi", 100), None, [], [_r("grid_import", 100)]) == 0.0

    def test_the_eps_output_adds_and_the_eps_input_is_not_subtracted(self) -> None:
        """Only the output half is passed in ``system_exports`` (the caller selects it); the
        input half of the EPS sensor is no argument at all."""
        eps_in, eps_out = split_signed_reading(_r("eps", 60))
        assert eps_in.raw_value == 0.0
        assert self._value(_r("hi", 300), None, [eps_out]) == pytest.approx(360.0)
        eps_in, eps_out = split_signed_reading(_r("eps", -80))
        assert eps_out.raw_value == 0.0
        assert self._value(_r("hi", 300), None, [eps_out]) == pytest.approx(300.0)

    def test_a_negative_household_import_counts_as_zero_without_the_signed_rule(self) -> None:
        assert self._value(_r("hi", -120), None, [_r("grid_export", 200)]) == pytest.approx(200.0)

    def test_a_negative_household_export_counts_as_zero(self) -> None:
        assert self._value(_r("hi", 300), _r("he", -50)) == pytest.approx(300.0)

    def test_a_signed_import_splits_into_import_and_export(self) -> None:
        # +300 W is a household import of 300, -120 W is a household export of 120
        assert self._value(_r("hi", 300), signed=True) == pytest.approx(300.0)
        value = self._value(_r("hi", -120), None, [_r("grid_export", 200)], signed=True)
        assert value == pytest.approx(80.0)  # 0 - 120 + 200
        assert self._value(_r("hi", -120), signed=True) == 0.0  # clamped

    def test_the_signed_split_works_in_kw(self) -> None:
        value = self._value(_r("hi", -0.12, "kW"), None, [_r("grid_export", 200)], signed=True)
        assert value == pytest.approx(80.0)

    def test_the_signed_rule_ignores_a_household_export_reading(self) -> None:
        """``signed_import`` is only true without an export sensor; if a caller passes one all
        the same, the signed import is split and the export reading is not used twice."""
        assert self._value(_r("hi", -120), _r("he", 40), [_r("grid_export", 200)], signed=True) == (
            pytest.approx(80.0)
        )

    def test_the_inputs_are_not_changed(self) -> None:
        hi, exports = _r("hi", -120), [_r("grid_export", 200)]
        self._value(hi, None, exports, signed=True)
        assert hi.raw_value == -120 and exports[0].raw_value == 200
