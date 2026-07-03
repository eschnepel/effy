"""
Core loss calculation engine for Effy.

Pure logic, no Home Assistant imports — see ADR-000 §3 for why this module
boundary is enforced and how it is exploited for zero-mock unit testing.

Algorithm (full rationale in ADR-001):
  total_loss = max(0, sum(inputs_W) - sum(outputs_W))

  Waterfall distribution (ascending order by value, only non-zero inputs):
    1. Sort active (non-zero) inputs ascending by value.
    2. equal_share = remaining_loss / count_remaining_active
    3. For each sensor (ascending):
       - If sensor_value >= equal_share  → deduct equal_share, continue
       - If sensor_value <  equal_share  → deduct sensor_value (goes to 0),
         redistribute remaining_loss over remaining sensors.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SensorReading:
    """A single sensor reading in its original unit (not yet normalized).

    Normalization happens once, inside ``distribute_loss`` — see ADR-002 for
    why no pre-normalization is done here (avoids double-scaling kW/kWh).
    """

    entity_id: str
    raw_value: float  # value as reported by the sensor (W, kW, Wh, or kWh)
    original_unit: str  # original unit string


@dataclass
class LossDistribution:
    """Result of the loss distribution calculation."""

    total_loss_w: float
    shares: dict[str, float]  # entity_id -> loss share in W
    effective_values_w: dict[str, float]  # entity_id -> (value - share) in W


def _to_w(value: float, unit: str) -> float:
    """Normalize a sensor value to Watts (or Wh, treated identically per ADR-002)."""
    if unit in ("kW", "kWh"):
        return value * 1000.0
    return value


def _from_w(value_w: float, unit: str) -> float:
    """Convert an internal W value back to the sensor's original unit."""
    if unit in ("kW", "kWh"):
        return value_w / 1000.0
    return value_w


def distribute_loss(
    inputs: list[SensorReading],
    outputs: list[SensorReading],
) -> LossDistribution:
    """
    Calculate and distribute the total loss across input sensors.

    Parameters
    ----------
    inputs:  List of input sensor readings (PV sources, battery/grid import).
    outputs: List of output sensor readings (battery/grid export).

    Returns
    -------
    LossDistribution with per-sensor loss shares and effective values,
    all expressed in W internally.  Use ``effective_in_original_unit`` to
    retrieve values in the sensor's own unit.
    """
    # --- 1. Normalize all raw values to W (ADR-002: single normalization point) ---
    inputs_w = {r.entity_id: _to_w(r.raw_value, r.original_unit) for r in inputs}
    outputs_w = {r.entity_id: _to_w(r.raw_value, r.original_unit) for r in outputs}

    sum_in = sum(inputs_w.values())
    sum_out = sum(outputs_w.values())

    # Cap at 0 – negative loss (measurement noise) is ignored (ADR-005)
    total_loss = max(0.0, sum_in - sum_out)

    # --- 2. Waterfall distribution (ADR-001) ---
    shares: dict[str, float] = {r.entity_id: 0.0 for r in inputs}

    # Only non-zero inputs participate
    active = {eid: v for eid, v in inputs_w.items() if v > 0.0}
    remaining_loss = total_loss

    # Sort ascending by value so smallest sensors are processed first
    sorted_active = sorted(active.items(), key=lambda kv: kv[1])

    for idx, (eid, value) in enumerate(sorted_active):
        count_remaining = len(sorted_active) - idx
        if count_remaining == 0 or remaining_loss <= 0.0:
            break

        equal_share = remaining_loss / count_remaining

        if value >= equal_share:
            shares[eid] = equal_share
            remaining_loss -= equal_share
        else:
            # Sensor is too small for its equal share → absorbs its full value
            shares[eid] = value
            remaining_loss -= value

    # Floating-point safety: absorb any residual onto the largest active sensor
    if remaining_loss > 1e-6 and sorted_active:
        largest_eid = sorted_active[-1][0]
        shares[largest_eid] += remaining_loss

    # --- 3. Effective values (in W) ---
    effective_values_w: dict[str, float] = {
        r.entity_id: max(0.0, inputs_w[r.entity_id] - shares[r.entity_id]) for r in inputs
    }

    return LossDistribution(
        total_loss_w=total_loss,
        shares=shares,
        effective_values_w=effective_values_w,
    )


def smooth_zero_noise(values: list[float]) -> list[float]:
    """Smooth spurious zero-valued slots in a per-slot raw energy-delta series.

    Some energy meters (observed with certain BMS sensors) only report their
    cumulative kWh counter with 2 decimal digits of precision. Over a short
    slot (e.g. 5 minutes) the true energy delta is often smaller than that
    resolution, so the counter doesn't tick between two consecutive readings
    and the computed delta comes out as exactly 0 — even though real, non-zero
    power was flowing. Left alone, this quantisation noise shows up as
    frequent 0 W readings for an otherwise steadily-producing/consuming
    sensor, and can distort the loss distribution for that slot (ADR-009).

    This performs two rounds of "neighbor steal" redistribution:

    Round 1 — for every slot whose *original* raw value is exactly 0.0, it
    takes 25% of each direct neighbor's value (previous and next slot in the
    sequence) and adds it to itself; each donor neighbor's value is reduced
    by the same amount. A slot that has only one neighbor (start/end of the
    series) only steals from that one side. This alone fully flattens an
    isolated zero — e.g. an alternating pattern like [10, 0, 10, 0, ...],
    where no zero slot is adjacent to another zero slot — to a constant
    [.., 5, 5, 5, ..] (except at the two ends of the series, which only
    have one neighbor to draw from and end up slightly higher).

    Round 2 — targets only slots that belong to a run of *two or more*
    consecutive originally-zero slots ("larger gaps"). A single round only
    pulls from immediate neighbors, so such a run isn't fully smoothed by
    round 1 alone (a zero slot next to another zero slot steals 0 from that
    side in round 1, since the neighbor was still 0 at that point). Round 2
    steals 10% from each direct (±1) neighbor and 5% from each ±2 neighbor,
    using the round-1-adjusted values as input, letting the redistribution
    reach one step further without requiring an unbounded number of passes.
    Isolated (run-length-1) zeros are deliberately excluded from round 2:
    round 1 already gives them their ideal flat result, and re-running the
    same kind of steal on an already-flat value would just reintroduce
    boundary-driven unevenness for no benefit.

    The total sum of the series is preserved exactly by both rounds (only
    redistribution, never creation or destruction of energy).

    This must run on the raw value in its original unit (Wh or kWh) —
    *before* any Wh/kWh → W/kW conversion — so the redistribution operates
    on genuine energy amounts. Converting to power first would make the
    "steal" amounts a function of the (possibly varying) slot duration
    instead of the raw meter reading.
    """
    n = len(values)
    if n < 2:
        return list(values)

    zero_mask = [v == 0.0 for v in values]
    if not any(zero_mask):
        return list(values)

    def _steal_round(
        current: list[float],
        mask: list[bool],
        near_pct: float,
        far_pct: float = 0.0,
    ) -> list[float]:
        gains = [0.0] * n
        losses = [0.0] * n
        for i in range(n):
            if not mask[i]:
                continue
            for distance, pct in ((1, near_pct), (2, far_pct)):
                if pct == 0.0:
                    continue
                if i - distance >= 0:
                    amount = pct * current[i - distance]
                    gains[i] += amount
                    losses[i - distance] += amount
                if i + distance < n:
                    amount = pct * current[i + distance]
                    gains[i] += amount
                    losses[i + distance] += amount
        return [current[i] + gains[i] - losses[i] for i in range(n)]

    def _runs_of_two_or_more(mask: list[bool]) -> list[bool]:
        """True for indices belonging to a run of >=2 consecutive True values."""
        result = [False] * n
        i = 0
        while i < n:
            if not mask[i]:
                i += 1
                continue
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= 2:
                for k in range(i, j):
                    result[k] = True
            i = j
        return result

    round1 = _steal_round(values, zero_mask, near_pct=0.25)

    larger_gap_mask = _runs_of_two_or_more(zero_mask)
    if not any(larger_gap_mask):
        return round1

    round2 = _steal_round(round1, larger_gap_mask, near_pct=0.10, far_pct=0.05)
    return round2


def effective_in_original_unit(
    entity_id: str,
    distribution: LossDistribution,
    original_unit: str,
) -> float:
    """Return the effective value for a sensor converted back to its original unit."""
    return _from_w(distribution.effective_values_w[entity_id], original_unit)
