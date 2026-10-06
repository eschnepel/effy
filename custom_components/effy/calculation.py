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


# One valid-reading -> next-valid-reading step of an energy counter:
# (t1, v1, t2, v2, was_offline). ``was_offline`` is True when the raw entry
# immediately preceding (t2, v2) was invalid (unavailable/unknown/non-numeric).
_Transition = tuple[datetime, float, datetime, float, bool]


def _find_transitions(
    raw_states: list[tuple[datetime, str]],
    now: datetime | None = None,
) -> list[_Transition]:
    """Find valid-numeric-reading transitions in a raw state history,
    tracking whether the entry immediately preceding each one was invalid
    (offline gap detection). Shared by ``trapezoidal_slot_contributions``
    and ``trapezoidal_jump_windows`` so both always agree on what a
    "transition" is (ADR-014 Amendment 2026-10-05, item 5).

    If ``now`` is given and the last known reading is still valid (not
    currently unavailable) but predates ``now``, a final synthetic
    zero-delta transition from that last reading up to ``now`` is appended
    (ADR-013).
    """
    transitions: list[_Transition] = []
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

    # Synthetic zero-delta continuation up to `now`.
    if now is not None and last_valid is not None and not prev_was_invalid:
        last_ts, last_value = last_valid
        if now > last_ts:
            transitions.append((last_ts, last_value, now, last_value, False))

    return transitions


def _distribution_window_start(
    t1: datetime,
    t2: datetime,
    delta: float,
    was_offline: bool,
    max_window: timedelta,
) -> datetime:
    """Start of the window over which a transition's ``delta`` is spread.

    An offline-recovery transition or a zero-delta one uses the entire
    uncapped [t1, t2) span; a normal positive-delta step uses at most the
    last ``max_window`` before ``t2`` (ADR-012/013/014).
    """
    if was_offline or delta == 0.0:
        return t1
    return max(t1, t2 - max_window)


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

    transitions = _find_transitions(raw_states, now)

    contributions: dict[datetime, float] = {}

    for t1, v1, t2, v2, was_offline in transitions:
        delta = max(0.0, v2 - v1)
        if t2 <= t1:
            continue

        window_start = _distribution_window_start(t1, t2, delta, was_offline, max_window)
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


def trapezoidal_jump_windows(
    raw_states: list[tuple[datetime, str]],
    max_minutes: int = TRAPEZOID_MAX_MINUTES,
) -> list[tuple[datetime, datetime]]:
    """Return ``(t2, window_start)`` for every rewrite-triggering event in an
    energy sensor's raw state history, in chronological order (ADR-014
    Amendment 2026-10-05, items 2 and 5; extended by Amendment 2026-10-06).

    A trigger is either a *real jump* — a transition between two valid
    numeric readings with a positive delta — or an *offline recovery*, i.e.
    the first valid reading after an offline stretch, **whatever its
    delta** (Amendment 2026-10-06: a full recalc zero-fills the whole
    outage, ADR-013 Decision 4, so the slot-timer path must rewrite back to
    it even when the counter did not move, otherwise the outage stays
    "no data" instead of 0). Zero-delta and counter-reset transitions
    between two directly consecutive valid readings, and the synthetic
    "now" continuation of ADR-013 (this function has no ``now`` parameter),
    are not triggers and never appear.

    ``window_start`` is the start of the window over which that jump's
    energy is distributed by ``trapezoidal_slot_contributions``:
    ``max(t1, t2 - max_minutes)`` for a normal jump, ``t1`` (uncapped) for
    an offline-recovery jump. The slot-timer path uses it to know how far
    back a newly arrived jump changes already-written slots. The values
    are exact timestamps, not slot-aligned.
    """
    max_window = timedelta(minutes=max_minutes)
    windows: list[tuple[datetime, datetime]] = []
    for t1, v1, t2, v2, was_offline in _find_transitions(raw_states):
        delta = max(0.0, v2 - v1)
        if t2 <= t1 or (delta == 0.0 and not was_offline):
            continue
        windows.append((t2, _distribution_window_start(t1, t2, delta, was_offline, max_window)))
    return windows


def recent_trigger_from(
    now: datetime,
    window: timedelta,
    max_lookback: timedelta,
    trigger_since: datetime | None = None,
) -> datetime:
    """Earliest ``t2`` that may still trigger an extension of the slot-timer
    rewrite range (the start of the *trigger window*, ADR-014 Amendment
    2026-10-06).

    Default ``now - window``: a trigger is applied by the ~4 consecutive
    cycles that run while it is inside that window. If ``trigger_since`` is
    given — the end time of the last cycle that completed, or ``now -
    max_lookback`` for the first cycle of a session — the window reaches back
    that far, so a trigger that arrived while no cycle was running is still
    caught. Never earlier than ``now - max_lookback`` (nothing older can
    change anything inside the bounded range) and never later than
    ``now - window`` (a recent or future ``trigger_since`` changes nothing).
    The raw-history fetch in history.py must reach at least this far back,
    which is why this is shared rather than recomputed there.
    """
    base = now - window
    if trigger_since is None:
        return base
    return min(base, max(trigger_since, now - max_lookback))


def recent_rewrite_start(
    jump_windows: list[tuple[datetime, datetime]],
    now: datetime,
    window: timedelta,
    max_lookback: timedelta,
    slot_minutes: int = 5,
    trigger_since: datetime | None = None,
) -> datetime:
    """Start of the slot-timer path's rewrite range (ADR-014 Amendment
    2026-10-05, items 1-4; trigger window made dynamic by Amendment
    2026-10-06).

    ``jump_windows`` is the concatenation of ``trapezoidal_jump_windows``
    over *all* energy-family sensors, so the result is a single global
    start applied to every sensor and series (item 3).

    - With no qualifying trigger (idle cycle, item 1) the result is exactly
      ``now - window`` — the pre-amendment behaviour, unaligned on purpose
      (the 15-vs-20-minute effect of that is a separate, undecided finding).
    - A trigger qualifies when it arrived inside the *trigger window*
      (``trigger_from <= t2 <= now``) and its distribution window starts
      before ``now - window``. Triggers older than that were already
      applied by earlier cycles and never extend the range (item 2).
      ``trigger_from`` is ``now - window`` by default. If ``trigger_since``
      is given — the end time of the last cycle that *completed*, or, for
      the first cycle of a session, ``now - max_lookback`` (a one-off
      catch-up) — the trigger window reaches back that far instead, so a
      trigger that arrived while the timer was not running (HA restart,
      suspended host, a failed cycle) is still applied. It never reaches
      back further than ``now - max_lookback`` (nothing older can change
      anything inside the bounded range) and is never later than
      ``now - window``, so a ``trigger_since`` that is recent (the normal
      5-minute cadence) or in the future leaves the default untouched.
    - The result is the earliest qualifying ``window_start``, rounded down
      to its slot start, so the whole first slot of the window is rewritten.
    - It is clamped to the slot-aligned ``now - max_lookback`` (item 4; an
      older gap is left to a full history recalc) and is never later than
      ``now - window``.
    """
    slot_width = timedelta(minutes=slot_minutes)
    base = now - window
    trigger_from = recent_trigger_from(now, window, max_lookback, trigger_since)
    start = base
    for t2, window_start in jump_windows:
        if not trigger_from <= t2 <= now or window_start >= base:
            continue
        start = min(start, _slot_aligned(window_start, slot_width))
    floor = min(_slot_aligned(now - max_lookback, slot_width), base)
    return max(start, floor)


def carry_forward_open_slot(
    slot_values: list[tuple[datetime, float]],
    now: datetime,
    slot_minutes: int = 5,
) -> list[tuple[datetime, float]]:
    """Give the still-open slot a provisional value: the previous slot's.

    The slot-timer path runs seconds after a boundary, so the slot that
    contains ``now`` has only a few seconds of data behind it and its
    computed value is ≈ 0 — which draws a spurious drop to 0 at the right
    edge of the statistics graph and makes ADR-016's live push (the last
    slot written) almost always 0. Amendment 2026-10-06 (ADR-016): assume
    the open slot has the same value as the last closed slot instead. That
    is a guess, and the next cycle replaces it with the real value once
    the slot has closed — but a guess is closer than 0.

    ``slot_values`` is one entity's ``(slot_start, value)`` list in
    ascending slot order. The value of the slot containing ``now`` is
    replaced by the value of the slot immediately before it, and only if
    *both* are present: a series that has no entry for the open slot (a
    sensor that is offline right now, a power-family sensor with nothing
    compiled yet) is not extended, and a series without a preceding slot
    has nothing to carry forward and is returned unchanged. Returns a new
    list; the input is never mutated.
    """
    slot_width = timedelta(minutes=slot_minutes)
    open_slot = _slot_aligned(now, slot_width)
    previous_slot = open_slot - slot_width
    values = dict(slot_values)
    if open_slot not in values or previous_slot not in values:
        return list(slot_values)
    carried = values[previous_slot]
    return [(slot, carried if slot == open_slot else value) for slot, value in slot_values]


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
