"""TASK-0005 — how today's loss model treats the battery system's standby power.

Zero-mock scenarios (``calculation.py`` only, ADR-000 §6) that pin what ADR-018
rests on: standby power is *inside the loss* (ADR-001/005), absent from the
effective series, and not separable per slot. They do not change that behaviour;
if ADR-001/005 ever change, they are expected to be revisited. The standby
machine itself is tested in ``test_standby.py``.

Simulation convention: constant powers, perfect conversion (η = 1) for the
consumption part so the arithmetic stays checkable by hand.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    # Static-only import (ADR-000 §6) — runtime loads the module by file path.
    from effy.calculation import SensorReading as SensorReading

_calc_path = (
    Path(__file__).resolve().parent.parent / "custom_components" / "effy" / "calculation.py"
)
_spec = importlib.util.spec_from_file_location("effy_calculation_standby", _calc_path)
assert _spec is not None and _spec.loader is not None
_calculation = importlib.util.module_from_spec(_spec)
sys.modules["effy_calculation_standby"] = _calculation
_spec.loader.exec_module(_calculation)

if not TYPE_CHECKING:
    SensorReading = _calculation.SensorReading
distribute_loss = _calculation.distribute_loss
trapezoidal_slot_contributions = _calculation.trapezoidal_slot_contributions
_find_transitions = _calculation._find_transitions

UTC = timezone.utc
SLOT = timedelta(minutes=5)
T0 = datetime(2026, 1, 10, 22, 0, 0, tzinfo=UTC)

RawStates = list[tuple[datetime, str]]
Interval = tuple[datetime, datetime, float]  # (t1, t2, energy_wh)


# --------------------------------------------------------------------------
# helpers shared by both classes
# --------------------------------------------------------------------------


def _kw_reading(eid: str, wh_in_slot: float) -> SensorReading:
    """What history._readings_for_slot hands to distribute_loss for a kWh
    sensor (ADR-008): Wh → kWh, × 12 (5-min slot) → W-equivalent, unit kW."""
    return SensorReading(entity_id=eid, raw_value=wh_in_slot / 1000.0 * 12.0, original_unit="kW")


def _w_reading(eid: str, watts: float) -> SensorReading:
    return SensorReading(entity_id=eid, raw_value=watts, original_unit="W")


def _simulate_counter(
    segments: list[tuple[datetime, datetime, int]],
    start: datetime,
    end: datetime,
    resolution_wh: int = 10,
    base_kwh: float = 12.30,
    jitter_s: int = 0,
) -> RawStates:
    """Raw state history of a kWh counter with ``resolution_wh`` resolution fed
    by piecewise-constant power ``segments`` (start, end, watts; overlapping
    segments add up). A state is recorded the second the quantised value
    changes (+ ``k * 7 % (jitter_s + 1)`` seconds of polling delay, if asked).
    Integer arithmetic only — jump times are exact."""
    total_s = int((end - start).total_seconds())
    watts = [0] * total_s
    for seg_start, seg_end, w in segments:
        for i in range(
            int((seg_start - start).total_seconds()), int((seg_end - start).total_seconds())
        ):
            if 0 <= i < total_s:
                watts[i] += w
    states: RawStates = [(start, f"{base_kwh:.2f}")]
    energy_ws = 0
    quantum_ws = resolution_wh * 3600
    jumps_seen = 0
    for i in range(total_s):
        energy_ws += watts[i]
        steps = energy_ws // quantum_ws
        if steps > jumps_seen:
            jumps_seen = steps
            delay = (len(states) * 7) % (jitter_s + 1) if jitter_s else 0
            value = base_kwh + steps * resolution_wh / 1000.0
            states.append((start + timedelta(seconds=i + 1 + delay), f"{value:.2f}"))
    return states


# --------------------------------------------------------------------------
# 1. How today's model treats standby (Findings Q2)
# --------------------------------------------------------------------------


class TestStandbyOnlySlotToday:
    """A slot with standby draw but no other flow: 12 W × 5 min = 1 Wh leaves the
    battery (assumption A1: the discharge counter includes standby energy)."""

    def test_standby_only_slot_is_all_loss_and_effective_is_zero(self) -> None:
        inputs = [
            _kw_reading("bms_discharge", 1.0),
            _kw_reading("bms_grid_in", 0.0),
            _w_reading("pv_south", 0.0),
        ]
        outputs = [_kw_reading("bms_bat_out", 0.0), _kw_reading("bms_grid_out", 0.0)]
        dist = distribute_loss(inputs, outputs)

        # total_loss_w attribute: the whole standby draw
        assert dist.total_loss_w == pytest.approx(12.0)
        # effy_bms_discharge (effective): nothing left
        assert dist.effective_values_w["bms_discharge"] == pytest.approx(0.0)
        # effy_bms_discharge_power (pre-waterfall derived power) is the raw 12 W
        assert inputs[0].raw_value * 1000.0 == pytest.approx(12.0)
        # conservation holds trivially: Σ effective = Σ outputs = 0
        assert sum(dist.effective_values_w.values()) == pytest.approx(0.0)

    def test_standby_is_shared_with_pv_when_pv_is_active(self) -> None:
        # 100 W PV + 12 W standby from the battery, 100 W delivered → loss 12 W,
        # split equally (ADR-001): the standby cannot be attributed to the battery.
        inputs = [_w_reading("pv_south", 100.0), _w_reading("bms_discharge", 12.0)]
        outputs = [_w_reading("bms_grid_out", 100.0)]
        dist = distribute_loss(inputs, outputs)

        assert dist.total_loss_w == pytest.approx(12.0)
        assert dist.shares["pv_south"] == pytest.approx(6.0)
        assert dist.shares["bms_discharge"] == pytest.approx(6.0)
        assert sum(dist.effective_values_w.values()) == pytest.approx(100.0)

    def test_standby_and_conversion_loss_are_indistinguishable_per_slot(self) -> None:
        # Pure standby (12 W, nothing delivered) vs. 100 W delivered from a
        # 112 W discharge (≈ 10.7 % conversion loss): same total_loss_w.
        standby_only = distribute_loss([_w_reading("d", 12.0)], [_w_reading("o", 0.0)])
        conversion = distribute_loss([_w_reading("d", 112.0)], [_w_reading("o", 100.0)])
        assert standby_only.total_loss_w == pytest.approx(conversion.total_loss_w)

    def test_quiet_night_standby_energy_is_fully_inside_the_loss(self) -> None:
        """End to end through the real slot pipeline: 12 W standby, 10 Wh counter
        resolution (a jump every 50 min). Energy that left the battery is
        carried slot by slot into ``total_loss`` — none of it reaches the
        effective discharge series."""
        end = T0 + timedelta(hours=6)
        raw = _simulate_counter([(T0, end, 12)], T0, end)
        last_jump = raw[-1][0]
        contributions: dict[datetime, float] = trapezoidal_slot_contributions(raw, now=end)
        # kWh → Wh
        slot_wh = {slot: kwh * 1000.0 for slot, kwh in contributions.items()}

        total_loss_wh = 0.0
        total_effective_wh = 0.0
        for slot, wh in slot_wh.items():
            if slot + SLOT > last_jump:  # the open tail after the last jump carries 0 Wh
                continue
            dist = distribute_loss([_kw_reading("d", wh)], [_kw_reading("o", 0.0)])
            total_loss_wh += dist.total_loss_w / 12.0
            total_effective_wh += dist.effective_values_w["d"] / 12.0

        discharged_wh = (float(raw[-1][1]) - float(raw[0][1])) * 1000.0
        assert total_effective_wh == pytest.approx(0.0, abs=1e-6)
        # the slot containing last_jump is partially skipped above; everything
        # before it is accounted for within one slot's energy (1 Wh)
        assert discharged_wh - total_loss_wh <= 1.0 + 1e-6
        assert total_loss_wh > 0.0
