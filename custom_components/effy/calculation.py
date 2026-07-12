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
from datetime import datetime, timedelta


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


# Maximum distribution window for a normal (non-offline) counter jump — see
# trapezoidal_slot_contributions. Not user-configurable (ADR-012): unlike
# ADR-009's smoothing, which this replaces, the window width here isn't a
# tunable heuristic, it's a fixed rule.
TRAPEZOID_MAX_MINUTES = 15


def _parse_energy_state(state: str) -> float | None:
    """Parse a raw recorder state string as a float, or None if invalid.

    None covers "unavailable", "unknown", and any other non-numeric
    string — used by trapezoidal_slot_contributions to detect offline
    gaps, which is why the caller must pass the *unfiltered* raw state
    history (including non-numeric entries), not a numeric-only series.
    """
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def trapezoidal_slot_contributions(
    raw_states: list[tuple[datetime, str]],
    slot_minutes: int = 5,
    max_minutes: int = TRAPEZOID_MAX_MINUTES,
) -> dict[datetime, float]:
    """Redistribute a TOTAL_INCREASING energy counter's raw jumps across
    5-minute slots using the trapezoidal rule (ADR-012, replaces ADR-009's
    neighbor-steal smoothing).

    Some energy meters only report their cumulative counter every so often
    — sometimes because the true delta is smaller than the counter's
    display resolution and simply hasn't ticked yet, sometimes because the
    sensor was genuinely offline. Reading the counter's raw ``change`` per
    fixed 5-minute statistics slot (the previous approach) attributes the
    *entire* jump to whichever slot happened to contain the next reading,
    leaving every slot in between at a spurious 0 — even though real,
    continuous power was very likely flowing throughout. This function
    instead spreads each jump evenly across the time it actually took to
    accumulate, using the trapezoidal rule.

    ``raw_states`` is the entity's raw state history, chronologically
    ordered, as (timestamp, state_string) pairs — including any
    "unavailable"/"unknown"/other non-numeric entries. This is what makes
    offline-gap detection possible; a pre-filtered, numeric-only series
    can't distinguish "the counter genuinely didn't move for 20 minutes"
    from "the sensor was offline for 20 minutes and only reported the
    accumulated delta once it came back".

    For each transition from one valid numeric reading (t1, v1) to the
    next valid numeric reading (t2, v2):
      - delta = max(0, v2 - v1) — a decrease is treated as a counter
        reset, exactly like the live/history clamping elsewhere; no
        negative contribution is ever distributed, and (t2, v2) simply
        becomes the new baseline for the following transition.
      - if the raw entry immediately preceding (t2, v2) was itself
        invalid (unavailable/unknown/non-numeric): the sensor was offline
        for this whole gap, so delta is spread evenly across the *entire*
        [t1, t2) span, uncapped.
      - otherwise (a normal, direct v1->v2 step with no gap in between):
        delta is spread evenly across at most the last ``max_minutes``
        minutes before t2, i.e. [max(t1, t2 - max_minutes), t2).

    Each 5-minute slot boundary that overlaps a transition's distribution
    window receives a share proportional to the overlap duration. A slot
    can receive contributions from more than one transition if two jumps
    happen close together; contributions are summed, not overwritten.

    Returns {slot_start: contribution}, in the same unit as the raw
    values (Wh or kWh) — this only replaces *where* a per-slot energy
    delta series comes from; the caller still runs the result through the
    same Wh/kWh → W-equivalent conversion (to_power_equivalent) as before.

    An input with fewer than 2 valid numeric readings produces no
    contributions (nothing to form a transition from).
    """
    slot_width = timedelta(minutes=slot_minutes)
    max_window = timedelta(minutes=max_minutes)

    # Find valid-numeric-reading transitions, tracking whether the entry
    # immediately preceding each one was invalid (offline gap detection).
    transitions: list[tuple[datetime, float, datetime, float, bool]] = []
    last_valid: tuple[datetime, float] | None = None
    prev_was_invalid = False

    for ts, state in raw_states:
        value = _parse_energy_state(state)
        if value is None:
            prev_was_invalid = True
            continue
        if last_valid is not None:
            t1, v1 = last_valid
            transitions.append((t1, v1, ts, value, prev_was_invalid))
        last_valid = (ts, value)
        prev_was_invalid = False

    contributions: dict[datetime, float] = {}

    for t1, v1, t2, v2, was_offline in transitions:
        delta = max(0.0, v2 - v1)
        if delta == 0.0 or t2 <= t1:
            continue

        window_start = t1 if was_offline else max(t1, t2 - max_window)
        window_seconds = (t2 - window_start).total_seconds()
        if window_seconds <= 0:
            continue
        rate_per_second = delta / window_seconds

        # Walk every 5-minute slot overlapping [window_start, t2).
        slot_cursor = window_start - timedelta(
            seconds=window_start.timestamp() % slot_width.total_seconds()
        )
        while slot_cursor < t2:
            slot_end = slot_cursor + slot_width
            overlap_start = max(window_start, slot_cursor)
            overlap_end = min(t2, slot_end)
            overlap_seconds = (overlap_end - overlap_start).total_seconds()
            if overlap_seconds > 0:
                contributions[slot_cursor] = (
                    contributions.get(slot_cursor, 0.0) + rate_per_second * overlap_seconds
                )
            slot_cursor = slot_end

    return contributions


def effective_in_original_unit(
    entity_id: str,
    distribution: LossDistribution,
    original_unit: str,
) -> float:
    """Return the effective value for a sensor converted back to its original unit."""
    return _from_w(distribution.effective_values_w[entity_id], original_unit)
