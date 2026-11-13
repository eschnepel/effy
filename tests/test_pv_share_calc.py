"""
The PV share's arithmetic (TASK-0046, CAP-13, ADR-027 amendment 2026-11-09).

``calculation.py`` has no Home Assistant dependency, so zero mocks (ADR-000 §6).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
_spec = importlib.util.spec_from_file_location("effy_calculation_pv_share", _path)
assert _spec is not None and _spec.loader is not None
_calculation = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation_pv_share"] = _calculation
_spec.loader.exec_module(_calculation)

pv_share = _calculation.pv_share
weighted_share = _calculation.weighted_share


class TestOneSlot:
    def test_the_example_of_the_task_is_fifty_percent(self) -> None:
        # effective PV 300 W and 0.2 kW (= 200 W), grid import 500 W, nothing else
        assert pv_share(300.0 + 200.0, 300.0 + 200.0 + 500.0) == pytest.approx(50.0)

    @pytest.mark.parametrize(
        ("numerator", "denominator", "expected"),
        [(0.0, 400.0, 0.0), (400.0, 400.0, 100.0), (100.0, 400.0, 25.0), (1.0, 3.0, 100 / 3)],
    )
    def test_the_percentage_of_the_inputs(
        self, numerator: float, denominator: float, expected: float
    ) -> None:
        assert pv_share(numerator, denominator) == pytest.approx(expected)

    def test_nothing_fed_is_zero_not_an_error(self) -> None:
        assert pv_share(0.0, 0.0) == 0.0

    @pytest.mark.parametrize(("numerator", "denominator"), [(5.0, 0.0), (-1.0, 0.0), (0.0, -3.0)])
    def test_a_zero_or_negative_denominator_is_zero(
        self, numerator: float, denominator: float
    ) -> None:
        assert pv_share(numerator, denominator) == 0.0

    def test_float_noise_cannot_leave_the_range(self) -> None:
        assert pv_share(100.00000000000001, 100.0) == 100.0
        assert pv_share(-1e-12, 100.0) == 0.0

    def test_the_value_lies_in_0_to_100_for_any_split(self) -> None:
        for numerator in (0.0, 0.1, 17.0, 250.0, 999.9, 1000.0):
            assert 0.0 <= pv_share(numerator, 1000.0) <= 100.0


class TestOneHour:
    def test_it_is_energy_weighted_not_the_mean_of_the_slot_shares(self) -> None:
        # slot 1: 100 % of 100 W; slot 2: 0 % of 300 W. Energy: 100 of 400 = 25 %, not 50 %
        assert weighted_share([100.0, 0.0], [100.0, 300.0]) == pytest.approx(25.0)

    def test_equal_weights_give_the_plain_mean(self) -> None:
        assert weighted_share([10.0, 20.0, 60.0], [5.0, 5.0, 5.0]) == pytest.approx(30.0)

    def test_an_hour_in_which_nothing_fed_is_zero(self) -> None:
        assert weighted_share([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_a_slot_without_weight_does_not_count(self) -> None:
        assert weighted_share([100.0, 0.0], [200.0, 0.0]) == pytest.approx(100.0)

    def test_no_slot_is_zero(self) -> None:
        assert weighted_share([], []) == 0.0

    def test_the_hour_is_the_100_times_numerator_over_denominator_of_its_slots(self) -> None:
        slots = [(300.0, 1000.0), (0.0, 500.0), (250.0, 250.0)]
        values = [pv_share(n, d) for n, d in slots]
        weights = [d for _n, d in slots]
        expected = 100 * sum(n for n, _d in slots) / sum(d for _n, d in slots)
        assert weighted_share(values, weights) == pytest.approx(expected)
