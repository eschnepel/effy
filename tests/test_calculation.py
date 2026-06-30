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

import pytest

_calc_path = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
)
_spec = importlib.util.spec_from_file_location("effy_calculation", _calc_path)
assert _spec is not None and _spec.loader is not None
_calculation = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation"] = _calculation
_spec.loader.exec_module(_calculation)

SensorReading = _calculation.SensorReading
distribute_loss = _calculation.distribute_loss
effective_in_original_unit = _calculation.effective_in_original_unit


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
