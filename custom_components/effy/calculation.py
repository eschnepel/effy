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
# trapezoidal_slot_contributions. Not user-configurable: unlike ADR-009's
# smoothing, which this replaces, the window width here isn't a tunable
# heuristic, it's a fixed rule. Raised from 15 to 120 minutes (ADR-014):
# 15 minutes was too tight for real low-resolution energy meters that only
# tick every 20-90 minutes — every such tick got compressed into the last
# 15 minutes, producing a visibly oscillating "0, then a spike, then 0
# again" derived-power curve instead of a smooth one, even though the
# meter's actual behaviour was almost certainly closer to a steady rate
# the whole time. 120 minutes comfortably covers realistic low-resolution
# reporting intervals while still being a firm cap, not "however long it
# takes" — see the offline branch just below for genuinely unknown-shape
# gaps, which remain uncapped regardless of this value.
TRAPEZOID_MAX_MINUTES = 120


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


def _slot_aligned(ts: datetime, slot_width: timedelta) -> datetime:
    """Round a timestamp down to its containing slot's start."""
    return ts - timedelta(seconds=ts.timestamp() % slot_width.total_seconds())


def _fill_zero_slots(
    contributions: dict[datetime, float],
    range_start: datetime,
    range_end: datetime,
    slot_width: timedelta,
) -> None:
    """Ensure every slot boundary in [range_start, range_end) has an
    explicit entry in ``contributions``, defaulting to 0.0.

    Uses ``setdefault`` — never overwrites a slot that already has a real
    (possibly nonzero) contribution from some other transition. The caller
    is responsible for choosing range_end so it doesn't include the slot
    that a subsequent windowed distribution will itself write to (see
    trapezoidal_slot_contributions below) — that slot must be left for the
    real overlap computation, even though part of it falls in the zero
    prefix.
    """
    cursor = _slot_aligned(range_start, slot_width)
    end_aligned = _slot_aligned(range_end, slot_width)
    while cursor < end_aligned:
        contributions.setdefault(cursor, 0.0)
        cursor += slot_width


def trapezoidal_slot_contributions(
    raw_states: list[tuple[datetime, str]],
    slot_minutes: int = 5,
    max_minutes: int = TRAPEZOID_MAX_MINUTES,
    now: datetime | None = None,
) -> dict[datetime, float]:
    """Redistribute a TOTAL_INCREASING energy counter's raw jumps across
    5-minute slots using the trapezoidal rule (ADR-012, replaces ADR-009's
    neighbor-steal smoothing; zero-fill behaviour added in ADR-013).

    Some energy meters only report their cumulative counter every so often
    — sometimes because the true delta is smaller than the counter's
    display resolution and simply hasn't ticked yet, sometimes because the
    sensor was genuinely offline. Reading the counter's raw ``change`` per
    fixed 5-minute statistics slot (the original approach, pre-ADR-012)
    attributes the *entire* jump to whichever slot happened to contain the
    next reading, leaving every slot in between at a spurious 0 — even
    though real, continuous power was very likely flowing throughout. This
    function instead spreads each jump evenly across the time it actually
    took to accumulate, using the trapezoidal rule — and, per ADR-013,
    explicitly writes 0 (rather than nothing at all) for a genuinely idle
    stretch, so e.g. an empty battery's 0 discharge or several zero-import
    days shows up as 0 in the statistic instead of "no data".

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
        invalid (unavailable/unknown/non-numeric), OR delta == 0 (the
        counter genuinely didn't move at all, however long that took):
        the distribution window is the *entire* uncapped [t1, t2) span.
        For the offline case this is because it's genuinely unknown how
        consumption/production was distributed while offline. For the
        zero-delta case it doesn't actually matter how wide the window
        is — the rate is 0 either way — but using the full span is what
        produces an explicit 0 entry for every slot in the gap, however
        long, instead of silently producing nothing.
      - otherwise (a normal, direct v1->v2 step with a positive delta, no
        gap in between): delta is spread evenly across at most the last
        ``max_minutes`` minutes before t2, i.e.
        [max(t1, t2 - max_minutes), t2) — same as before ADR-013. Unlike
        before, the *prefix* this cap leaves uncovered (t1 up to the start
        of that window, whenever the gap is longer than max_minutes) is no
        longer left with no entry at all: the counter was still sitting at
        v1 with no jump yet throughout that prefix, i.e. it genuinely
        contributed 0, so it is filled with explicit 0.0 entries too.

    If ``now`` is given and the sensor's last known reading is still valid
    (not currently unavailable) but predates ``now``, a final synthetic
    zero-delta transition from that last reading up to ``now`` is
    considered as well — this is what lets a sensor that simply hasn't
    reported anything new *yet* (as opposed to one whose latest jump was
    already processed above) still get explicit 0 entries for the slots
    since its last real reading, following the exact same zero-delta rule
    as above. Without this, an idle sensor with fewer than 2 readings in
    the queried range would produce no contributions at all, even though
    "no new reading" and "reading changed by exactly 0" mean the same
    thing physically.

    Each 5-minute slot boundary that overlaps a transition's distribution
    window receives a share proportional to the overlap duration. A slot
    can receive contributions from more than one transition if two jumps
    happen close together; contributions are summed, not overwritten.

    Returns {slot_start: contribution}, in the same unit as the raw
    values (Wh or kWh) — this only replaces *where* a per-slot energy
    delta series comes from; the caller still runs the result through the
    same Wh/kWh → W-equivalent conversion (to_power_equivalent) as before.

    An input with fewer than 2 valid numeric readings, and no ``now``
    (or a ``now`` that isn't after the single reading, or a currently-
    invalid last reading), produces no contributions (nothing to form a
    transition from).
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

    # Synthetic zero-delta continuation up to `now` — see docstring above.
    if now is not None and last_valid is not None and not prev_was_invalid:
        last_ts, last_value = last_valid
        if now > last_ts:
            transitions.append((last_ts, last_value, now, last_value, False))

    contributions: dict[datetime, float] = {}

    for t1, v1, t2, v2, was_offline in transitions:
        delta = max(0.0, v2 - v1)
        if t2 <= t1:
            continue

        if was_offline or delta == 0.0:
            window_start = t1
        else:
            window_start = max(t1, t2 - max_window)
            if window_start > t1:
                _fill_zero_slots(contributions, t1, window_start, slot_width)

        window_seconds = (t2 - window_start).total_seconds()
        if window_seconds <= 0:
            continue
        rate_per_second = delta / window_seconds

        # Walk every 5-minute slot overlapping [window_start, t2).
        slot_cursor = _slot_aligned(window_start, slot_width)
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


# Maximum number of consecutive missing slots that get bridged by linear
# interpolation (EffySmoothedSensor / history.py's `effy_*_smoothed`
# series, power-family INPUT sensors only). Not user-configurable — a
# fixed rule, same spirit as TRAPEZOID_MAX_MINUTES above. Two slots (10
# minutes at the default 5-minute slot width) is short enough that a
# straight line between the surrounding readings is still a reasonable
# estimate; a longer silence is left as a genuine gap rather than
# extrapolated across.
INTERPOLATION_MAX_GAP_SLOTS = 2


def interpolate_slot_gaps(
    slot_values: dict[datetime, float],
    slot_minutes: int = 5,
    max_gap_slots: int = INTERPOLATION_MAX_GAP_SLOTS,
) -> dict[datetime, float]:
    """Linearly interpolate short gaps in a sparse per-slot value series.

    ``slot_values`` is a sparse ``{slot_start: value}`` mapping — e.g. a
    MEASUREMENT/TOTAL-as-power sensor's compiled 5-minute ``mean`` values,
    which occasionally has a missing slot where the recorder simply never
    compiled a reading (a short connectivity blip, a slow-polling source,
    etc.). A *missing* slot is represented by its key being entirely
    absent, not by an explicit ``None``/NaN value — callers must drop
    None entries before calling this, the same convention
    trapezoidal_slot_contributions uses for offline detection above.

    For every pair of consecutive *known* slots (t1, v1) -> (t2, v2), if
    the number of missing slots strictly between them is between 1 and
    ``max_gap_slots`` (inclusive), each missing slot in between is filled
    with a linearly-interpolated value along the straight line from v1 to
    v2. A gap longer than ``max_gap_slots`` is left untouched entirely —
    bridging it would mean extrapolating a straight line across too long
    a silence to still be a reasonable guess, so it's better reported as
    genuinely missing than smoothed over.

    Returns a new dict containing every original entry plus the
    interpolated ones; ``slot_values`` itself is never mutated. Leading or
    trailing gaps (before the first, or after the last, known slot) are
    never filled — there is no second point to interpolate against.
    """
    if len(slot_values) < 2:
        return dict(slot_values)

    slot_width = timedelta(minutes=slot_minutes)
    known = sorted(slot_values.items())
    result: dict[datetime, float] = dict(slot_values)

    for (t1, v1), (t2, v2) in zip(known, known[1:]):
        steps = round((t2 - t1) / slot_width)
        gap_slots = steps - 1
        if gap_slots <= 0 or gap_slots > max_gap_slots:
            continue
        for i in range(1, steps):
            result[t1 + slot_width * i] = v1 + (v2 - v1) * (i / steps)

    return result


def effective_in_original_unit(
    entity_id: str,
    distribution: LossDistribution,
    original_unit: str,
) -> float:
    """Return the effective value for a sensor converted back to its original unit."""
    return _from_w(distribution.effective_values_w[entity_id], original_unit)
