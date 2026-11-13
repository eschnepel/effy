"""Realistic branches of ``calculation.py`` the other test files leave out.

Loaded by file path like the others (ADR-000 §6): no Home Assistant, no mocks.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

_calc_path = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
)
_spec = importlib.util.spec_from_file_location("effy_calculation_branches", _calc_path)
assert _spec is not None and _spec.loader is not None
_calculation: Any = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation_branches"] = _calculation
_spec.loader.exec_module(_calculation)

SensorReading = _calculation.SensorReading
DrainState = _calculation.DrainState
distribute_loss = _calculation.distribute_loss
drain_restore_data = _calculation.drain_restore_data
drain_state_from_restore = _calculation.drain_state_from_restore
trapezoidal_slot_contributions = _calculation.trapezoidal_slot_contributions

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
SLOT = timedelta(minutes=5)


def _r(entity_id: str, value: float, unit: str = "W") -> Any:
    return SensorReading(entity_id=entity_id, raw_value=value, original_unit=unit)


class TestLossBeyondWhatTheInputsCanCarry:
    def test_a_negative_output_leaves_a_residual_the_largest_input_absorbs(self) -> None:
        # A reading below zero on the output side makes the loss exceed the whole input:
        # the waterfall empties both inputs and 50 W are left over.
        result = distribute_loss(
            [_r("pv_small", 100.0), _r("pv_large", 300.0)],
            [_r("export", -50.0)],
        )
        assert result.total_loss_w == pytest.approx(450.0)
        assert result.shares["pv_small"] == pytest.approx(100.0)
        assert result.shares["pv_large"] == pytest.approx(350.0)
        assert sum(result.shares.values()) == pytest.approx(result.total_loss_w)

    def test_the_effective_value_of_the_absorbing_input_stays_at_zero(self) -> None:
        result = distribute_loss(
            [_r("pv_small", 100.0), _r("pv_large", 300.0)],
            [_r("export", -50.0)],
        )
        assert result.effective_values_w == {"pv_small": 0.0, "pv_large": 0.0}

    def test_without_active_inputs_there_is_nobody_to_absorb_it(self) -> None:
        result = distribute_loss([_r("pv", 0.0)], [_r("export", -50.0)])
        assert result.total_loss_w == pytest.approx(50.0)
        assert result.shares == {"pv": 0.0}


class TestAWindowThatIsNotThere:
    RAW = [(T0, "10.0"), (T0 + 2 * SLOT, "20.0")]

    def test_a_zero_max_window_spreads_nothing_and_leaves_zero_slots(self) -> None:
        result = trapezoidal_slot_contributions(self.RAW, max_minutes=0)
        assert result == {T0: 0.0, T0 + SLOT: 0.0}

    def test_a_negative_max_window_spreads_nothing_either(self) -> None:
        result = trapezoidal_slot_contributions(self.RAW, max_minutes=-5)
        assert result
        assert all(value == 0.0 for value in result.values())

    def test_the_same_step_with_a_real_window_does_spread_its_delta(self) -> None:
        result = trapezoidal_slot_contributions(self.RAW, max_minutes=30)
        assert result == {T0: pytest.approx(5.0), T0 + SLOT: pytest.approx(5.0)}


class TestDrainRestoreWithoutAResolutionMapping:
    def _data(self) -> dict[str, Any]:
        state = DrainState(
            resolution={"discharge": 0.01},
            estimate=12.5,
            count=4,
            last_value=11.0,
            last_slot=T0,
        )
        return drain_restore_data(state)  # type: ignore[no-any-return]

    def test_the_resolution_round_trips_when_it_is_a_mapping(self) -> None:
        restored = drain_state_from_restore(self._data())
        assert restored is not None
        assert dict(restored.resolution) == {"discharge": 0.01}

    @pytest.mark.parametrize("resolution", [None, "0.01", [("discharge", 0.01)], 0.01])
    def test_anything_else_restores_an_empty_resolution_and_keeps_the_rest(
        self, resolution: object
    ) -> None:
        data = self._data()
        data["resolution"] = resolution
        restored = drain_state_from_restore(data)
        assert restored is not None
        assert dict(restored.resolution) == {}
        assert restored.estimate == 12.5
        assert restored.count == 4
        assert restored.suppress_until == T0

    def test_a_missing_resolution_restores_an_empty_one(self) -> None:
        data = self._data()
        del data["resolution"]
        restored = drain_state_from_restore(data)
        assert restored is not None
        assert dict(restored.resolution) == {}
        assert restored.estimate == 12.5
