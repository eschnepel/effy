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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal


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


def drop_open_slot(
    slot_values: list[tuple[datetime, float]],
    now: datetime,
    slot_minutes: int = 5,
) -> list[tuple[datetime, float]]:
    """Remove the still-open slot: Effy writes no statistic for it.

    ADR-016 Amendment 2026-10-16 (TASK-0007a; supersedes the provisional value
    of the 2026-10-06 amendment). The slot that contains ``now`` has only
    seconds of data behind it, and the recorder inserts its own row for every
    slot when the slot ends (``sensor.effy_*`` is recorded, ADR-017
    superseded). A row Effy inserted first would make that insert fail with
    a UNIQUE constraint error. Effy therefore only ever *updates* a row after
    the recorder's compile. The live state keeps carrying the last completed
    slot's value, because the live push uses the last slot written.

    ``slot_values`` is one entity's ``(slot_start, value)`` list in ascending
    slot order; the entry of the slot containing ``now`` is dropped, every
    other entry is kept as it is. Returns a new list; the input is never
    mutated.
    """
    open_slot = _slot_aligned(now, timedelta(minutes=slot_minutes))
    return [(slot, value) for slot, value in slot_values if slot != open_slot]


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


# ---------------------------------------------------------------------------
# Standby power: drain intervals (ADR-018, TASK-0005 Stage 2)
# ---------------------------------------------------------------------------
#
# Standby power stays inside the loss (ADR-001/005, nothing above changes). It is
# read separately from the *peaks* of the BMS inputs: the span between two
# consecutive peaks of one input that have the same height, while no other
# sensor is active, is a *drain interval*; its power equivalent is one value of
# the standby power. Everything here is pure: the adapters turn raw readings into
# events, ``drain_step`` is the one transition ``(state, event) -> state``.

# Redistribution cap (TRAPEZOID_MAX_MINUTES) plus one slot of margin: a lagging
# counter has redistributed its energy into earlier slots by then (ADR-018 §3).
DRAIN_VALIDATION_DELAY = timedelta(minutes=TRAPEZOID_MAX_MINUTES + 5)
# Weight of one counted interval in the live estimate (ADR-018 §3).
DRAIN_ESTIMATE_WEIGHT = 0.05
# An armed turn or pending interval whose first peak is older than this before
# the slot being processed is dropped. It is the replay lookback of ADR-018 §5:
# what the live machine may still hold is exactly what a restart can rebuild.
DRAIN_LOOKBACK = timedelta(hours=24)

_DRAIN_SLOT = timedelta(minutes=5)
# Activity marker for a slot with no statistics at all: "known" is a condition
# of ADR-018 §3, so an unknown slot is never quiet.
DRAIN_UNKNOWN = "?"
_UNKNOWN_ACTIVITY: frozenset[str] = frozenset({DRAIN_UNKNOWN})

# slot start -> source entity IDs of the sensors with flow in it
DrainActivity = Mapping[datetime, frozenset[str]]


@dataclass(frozen=True)
class DrainPeak:
    """A peak of a BMS input. ``height`` is its first derivation: the energy of the
    jump (Wh) for an energy-family sensor, the reading (W) for a power-family one.
    ``power_w`` is its power equivalent and ``t_prev`` the time of the directly
    preceding peak; both are ``None`` when no peak precedes it directly."""

    t: datetime
    source: str
    t_prev: datetime | None
    power_w: float | None
    height: float
    is_energy: bool


@dataclass(frozen=True)
class DrainBreak:
    """Offline, counter reset, or sustained power: nothing spans across it."""

    t: datetime
    source: str


@dataclass(frozen=True)
class DrainSeen:
    """A non-zero reading of a power-family sensor, seen only to learn its resolution."""

    t: datetime
    source: str
    value: float


@dataclass(frozen=True)
class DrainSlot:
    """Slot ``slot`` has completed (at ``slot`` + 5 min)."""

    slot: datetime


DrainEvent = DrainPeak | DrainBreak | DrainSeen | DrainSlot


@dataclass(frozen=True)
class DrainArmed:
    """The peak a turn starts from: the next peak of ``source`` closes an interval."""

    source: str
    t: datetime
    height: float
    is_energy: bool


@dataclass(frozen=True)
class DrainInterval:
    """A span between two consecutive peaks of one source, waiting to be confirmed."""

    source: str
    t1: datetime
    t2: datetime
    power_w: float
    start_height: float
    end_height: float
    is_energy: bool


@dataclass(frozen=True)
class DrainState:
    """The whole state of the machine; every field is plain data."""

    resolution: Mapping[str, float] = field(default_factory=dict)
    last_nonzero: Mapping[str, float] = field(default_factory=dict)
    armed: DrainArmed | None = None
    pending: tuple[DrainInterval, ...] = ()
    last_slot: datetime | None = None
    last_slot_active: frozenset[str] = frozenset()
    estimate: float | None = None
    count: int = 0
    last_value: float | None = None
    last_end: datetime | None = None
    last_source: str | None = None
    # Intervals confirmed (quiet and old enough), counted or not, and the last one.
    confirmed: int = 0
    last_confirmed: DrainInterval | None = None
    # Replay after a restart: a confirmation at a slot up to here is already in the
    # restored estimate and must not count again.
    suppress_until: datetime | None = None


def energy_drain_events(
    source: str,
    raw_states: list[tuple[datetime, str]],
    wh_per_unit: float,
) -> list[DrainPeak | DrainBreak]:
    """Peaks and breaks of an energy-family BMS input from its raw state history.

    A jump becomes a peak: its height is the energy of the jump in Wh
    (``wh_per_unit`` converts the counter's unit), its power equivalent that
    energy divided by the time since the previous jump — only when the transition
    right before it was a jump too. An offline recovery or a counter reset is a
    break; an unchanged reading is neither and ends the chain. Pass the
    *unfiltered* raw history (offline entries included), like
    ``trapezoidal_slot_contributions``.
    """
    events: list[DrainPeak | DrainBreak] = []
    previous_jump: datetime | None = None
    for t1, v1, t2, v2, was_offline in _find_transitions(raw_states):
        delta_wh = (v2 - v1) * wh_per_unit
        if was_offline or delta_wh < 0.0:
            events.append(DrainBreak(t2, source))
            previous_jump = None
        elif delta_wh == 0.0:
            previous_jump = None
        else:
            hours = (t2 - previous_jump).total_seconds() / 3600.0 if previous_jump else None
            events.append(
                DrainPeak(
                    t2,
                    source,
                    previous_jump,
                    delta_wh / hours if hours else None,
                    delta_wh,
                    True,
                )
            )
            previous_jump = t2
    return events


def power_drain_events(
    source: str,
    raw_states: list[tuple[datetime, str]],
    w_per_unit: float,
) -> list[DrainPeak | DrainBreak | DrainSeen]:
    """Peaks, breaks and seen readings of a power-family BMS input.

    A reading above 0 with no reading above 0 directly before or after it is a
    peak; its height and power equivalent are the reading (in W, ``w_per_unit``
    converts the sensor's unit). Two consecutive readings above 0 are sustained
    power: no peak, a break. An offline entry is a break. Every reading above 0 is
    also reported as seen, so the resolution can be learned from steps between
    distinct readings. A reading that stays unchanged for long is a single entry
    in a state history: it looks isolated until its successor arrives.
    """
    values: list[tuple[datetime, float | None]] = []
    for t, state in raw_states:
        parsed = _parse_energy_state(state)
        values.append((t, None if parsed is None else parsed * w_per_unit))
    events: list[DrainPeak | DrainBreak | DrainSeen] = []
    previous_peak: datetime | None = None
    for i, (t, value) in enumerate(values):
        if value is None:
            events.append(DrainBreak(t, source))
            previous_peak = None
        elif value > 0.0:
            events.append(DrainSeen(t, source, value))
            before = values[i - 1][1] if i > 0 else 0.0
            after = values[i + 1][1] if i + 1 < len(values) else 0.0
            if (before or 0.0) > 0.0 or (after or 0.0) > 0.0:
                events.append(DrainBreak(t, source))
                previous_peak = None
            else:
                events.append(DrainPeak(t, source, previous_peak, value, value, False))
                previous_peak = t
    return events


def drain_activity_start(state: DrainState) -> datetime | None:
    """The oldest slot whose activity the machine still needs: the slot before the
    first peak of the armed turn or of any pending interval (``None`` if neither)."""
    starts = [_slot_aligned(p.t1, _DRAIN_SLOT) for p in state.pending]
    if state.armed is not None:
        starts.append(_slot_aligned(state.armed.t, _DRAIN_SLOT))
    return min(starts) - _DRAIN_SLOT if starts else None


def _drain_disturbed(activity: DrainActivity, source: str, t1: datetime, slot: datetime) -> bool:
    """True if any sensor other than ``source`` is active (or a slot is unknown)
    in any slot from the slot before ``t1`` up to ``slot``."""
    current = _slot_aligned(t1, _DRAIN_SLOT) - _DRAIN_SLOT
    while current <= slot:
        if activity.get(current, _UNKNOWN_ACTIVITY) - {source}:
            return True
        current += _DRAIN_SLOT
    return False


def _drain_peak(state: DrainState, peak: DrainPeak) -> DrainState:
    resolution = state.resolution
    if peak.is_energy:
        known = resolution.get(peak.source)
        resolution = {
            **resolution,
            peak.source: peak.height if known is None else min(known, peak.height),
        }
    turn = DrainArmed(peak.source, peak.t, peak.height, peak.is_energy)
    armed = state.armed
    if (
        armed is not None
        and armed.source == peak.source
        and peak.t_prev == armed.t
        and peak.power_w is not None
    ):
        interval = DrainInterval(
            peak.source, armed.t, peak.t, peak.power_w, armed.height, peak.height, peak.is_energy
        )
        return replace(state, resolution=resolution, pending=(*state.pending, interval), armed=turn)
    quiet_before = state.last_slot == _slot_aligned(peak.t, _DRAIN_SLOT) - _DRAIN_SLOT and not (
        state.last_slot_active - {peak.source}
    )
    return replace(state, resolution=resolution, armed=turn if quiet_before else None)


def _drain_seen(state: DrainState, seen: DrainSeen) -> DrainState:
    before = state.last_nonzero.get(seen.source)
    resolution = state.resolution
    if before is not None and before != seen.value:
        step = abs(before - seen.value)
        known = resolution.get(seen.source)
        resolution = {**resolution, seen.source: step if known is None else min(known, step)}
    return replace(
        state, resolution=resolution, last_nonzero={**state.last_nonzero, seen.source: seen.value}
    )


def _drain_confirm(state: DrainState, interval: DrainInterval, slot: datetime) -> DrainState:
    """Quiet over its span and old enough. It counts only if its peaks have the same
    height (they differ by less than the learned resolution) and, for an
    energy-family sensor, both are exactly one step (ADR-018 §3, D1/D2)."""
    state = replace(state, confirmed=state.confirmed + 1, last_confirmed=interval)
    resolution = state.resolution.get(interval.source)
    if resolution is None:
        return state  # no resolution learned yet: nothing can be validated
    same = abs(interval.start_height - interval.end_height) < resolution
    if interval.is_energy:
        same = (
            same
            and interval.start_height < 1.5 * resolution
            and interval.end_height < 1.5 * resolution
        )
    if not same or (state.suppress_until is not None and slot <= state.suppress_until):
        return state
    estimate = (
        interval.power_w
        if state.estimate is None
        else (1.0 - DRAIN_ESTIMATE_WEIGHT) * state.estimate
        + DRAIN_ESTIMATE_WEIGHT * interval.power_w
    )
    return replace(
        state,
        estimate=estimate,
        count=state.count + 1,
        last_value=interval.power_w,
        last_end=interval.t2,
        last_source=interval.source,
    )


def _drain_slot(
    state: DrainState,
    event: DrainSlot,
    activity: DrainActivity,
    validation_delay: timedelta,
) -> DrainState:
    slot = event.slot
    horizon = slot - DRAIN_LOOKBACK
    armed = state.armed
    if armed is not None and (
        armed.t < horizon or _drain_disturbed(activity, armed.source, armed.t, slot)
    ):
        armed = None
    pending = [
        p
        for p in state.pending
        if p.t1 >= horizon and not _drain_disturbed(activity, p.source, p.t1, slot)
    ]
    ready = [
        p
        for p in pending
        if slot >= _slot_aligned(p.t2, _DRAIN_SLOT) + _DRAIN_SLOT + validation_delay
    ]
    state = replace(
        state,
        armed=armed,
        pending=tuple(p for p in pending if p not in ready),
        last_slot=slot,
        last_slot_active=activity.get(slot, _UNKNOWN_ACTIVITY),
    )
    for interval in ready:
        state = _drain_confirm(state, interval, slot)
    return state


def drain_step(
    state: DrainState,
    event: DrainEvent,
    activity: DrainActivity,
    validation_delay: timedelta = DRAIN_VALIDATION_DELAY,
) -> DrainState:
    """The one transition of the machine (ADR-018 §3): ``(state, event) -> state``.

    Events must arrive in time order, a slot completion before a peak at the
    same instant. ``activity`` is only read for a ``DrainSlot``: the sensors with
    flow per slot as the statistics know them now; a slot missing from it is
    unknown, which is never quiet.
    """
    if isinstance(event, DrainSlot):
        return _drain_slot(state, event, activity, validation_delay)
    if isinstance(event, DrainPeak):
        return _drain_peak(state, event)
    if isinstance(event, DrainSeen):
        return _drain_seen(state, event)
    return replace(state, armed=None, pending=())


def drain_feed(
    state: DrainState,
    events: Iterable[DrainPeak | DrainBreak | DrainSeen],
    slots: Iterable[datetime],
    activity: DrainActivity,
    validation_delay: timedelta = DRAIN_VALIDATION_DELAY,
) -> DrainState:
    """Feed peaks/breaks/seen readings and completed slots in time order.

    ``slots`` are the starts of the slots that have completed; events at or after
    the end of the last of them belong to a slot that is still open and are left
    out. A slot completion sorts before an event at the same instant.
    """
    slot_list = sorted(slots)
    if not slot_list:
        return state
    until = slot_list[-1] + _DRAIN_SLOT
    timeline: list[tuple[datetime, int, int, DrainEvent]] = [
        (slot + _DRAIN_SLOT, 0, i, DrainSlot(slot)) for i, slot in enumerate(slot_list)
    ]
    timeline.extend((e.t, 1, i, e) for i, e in enumerate(events) if e.t < until)
    timeline.sort(key=lambda item: item[:3])
    for _when, _kind, _index, event in timeline:
        state = drain_step(state, event, activity, validation_delay)
    return state


def drain_restore_data(state: DrainState) -> dict[str, Any]:
    """What survives a restart (plain JSON types): the aggregates and the learned
    resolution. The armed turn and pending intervals are rebuilt by replay.

    ``last_slot`` is the slot the aggregates are valid up to. A state restored but
    not yet fed still carries it as ``suppress_until``, which is what a second
    restart needs."""
    processed = state.last_slot or state.suppress_until
    return {
        "estimate": state.estimate,
        "count": state.count,
        "last_value": state.last_value,
        "last_end": state.last_end.isoformat() if state.last_end else None,
        "last_source": state.last_source,
        "last_slot": processed.isoformat() if processed is not None else None,
        "resolution": dict(state.resolution),
    }


def _restore_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def drain_state_from_restore(data: Mapping[str, Any]) -> DrainState | None:
    """The state to start a replay from, or ``None`` when ``data`` holds no estimate.

    The result has no processed slot (the replay feeds them again) and
    ``suppress_until`` set to the last processed slot, so intervals confirmed up to
    then are not counted twice. Malformed data gives ``None``: the replay then
    starts from nothing, which is correct because without an estimate nothing
    was counted yet.
    """
    estimate = data.get("estimate")
    count = data.get("count")
    last_slot = _restore_time(data.get("last_slot"))
    if (
        isinstance(estimate, bool)
        or not isinstance(estimate, int | float)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or last_slot is None
    ):
        return None
    last_value = data.get("last_value")
    last_source = data.get("last_source")
    raw_resolution = data.get("resolution")
    resolution: dict[str, float] = {}
    if isinstance(raw_resolution, Mapping):
        resolution = {
            str(k): float(v)
            for k, v in raw_resolution.items()
            if isinstance(v, int | float) and not isinstance(v, bool)
        }
    return DrainState(
        resolution=resolution,
        estimate=float(estimate),
        count=count,
        last_value=float(last_value) if isinstance(last_value, int | float) else None,
        last_end=_restore_time(data.get("last_end")),
        last_source=last_source if isinstance(last_source, str) else None,
        suppress_until=last_slot,
    )


# ---------------------------------------------------------------------------
# Charge and discharge efficiency (ADR-023 decisions 3, 4, 5 and 7)
# ---------------------------------------------------------------------------
# The machine measures how much of the energy that enters the BMS on one side comes
# out on the other, in *pure* slots: slots in which only the two counters of one
# process flow (grid import and charge, or discharge and grid export). It is fed
# time-ordered slots of kWh and the sensors with flow in each slot; it reads no
# statistics and has no clock. ``run_feed`` is given ``now`` so that only slots old
# enough to be settled enter.

# A slot enters the machine only when it is this old (ADR-023 decision 3, ADR-018 D3):
# a late counter jump has redistributed its energy into earlier slots by then. The
# neighbours of a slot are settled too, because they enter before it is counted ◇.
EFF_VALIDATION_DELAY = DRAIN_VALIDATION_DELAY
# 5 % per kWh of gross input (ADR-023 decision 7): a measurement of x kWh multiplies
# what was there before by ``0.95 ** x``.
EFF_DECAY_PER_KWH = 0.95

# The process of a pure slot. Not the published ``function`` of a source (ADR-021): that is
# a property of a counter, this is what the whole slot is doing.
EFF_KIND_CHARGE: Literal["charging"] = "charging"
EFF_KIND_DISCHARGE: Literal["discharging"] = "discharging"
EffKind = Literal["charging", "discharging"]


@dataclass(frozen=True)
class EffRoles:
    """The source entity IDs of the four counters; a role that is not set is ``None``."""

    grid_import: str | None = None
    discharge: str | None = None
    charge: str | None = None
    grid_export: str | None = None


@dataclass(frozen=True)
class EffSlot:
    """One slot of statistics, in kWh. ``active`` are the source entity IDs with flow in
    it, PV inputs included (ADR-018's reading). ``standby_kwh`` is the recorded standby
    power of the slot × 5 min, ``None`` when the slot has no standby statistic."""

    start: datetime
    active: frozenset[str]
    grid_import_kwh: float = 0.0
    discharge_kwh: float = 0.0
    charge_kwh: float = 0.0
    grid_export_kwh: float = 0.0
    standby_kwh: float | None = None


@dataclass(frozen=True)
class DecayedRatio:
    """A numerator and a denominator that fade at 5 % per kWh of gross input."""

    num: float = 0.0
    den: float = 0.0


def decay_add(ratio: DecayedRatio, gross_kwh: float, out: float, inp: float) -> DecayedRatio:
    """Add one measurement: ``f = 0.95 ** x``, ``N <- f*N + out``, ``D <- f*D + in``.

    ``gross_kwh`` (x) is the gross input energy that weighs the measurement; ``inp`` is
    the denominator's own addend (the gross input less standby for charge and
    discharge efficiency). The first measurement seeds the ratio (it fades 0).
    """
    f = EFF_DECAY_PER_KWH**gross_kwh
    return DecayedRatio(num=f * ratio.num + out, den=f * ratio.den + inp)


def ratio_value(ratio: DecayedRatio) -> float | None:
    """The estimate N / D as a fraction, or ``None`` before the first measurement."""
    return ratio.num / ratio.den if ratio.den > 0 else None


@dataclass(frozen=True)
class EffEstimate:
    """One efficiency: the decayed sums and what the sensor shows as attributes."""

    ratio: DecayedRatio = field(default_factory=DecayedRatio)
    # Σ gross input of the counted slots since the estimate began (ADR-023 decision 2).
    counted_kwh: float = 0.0
    # Pure runs that have counted at least one slot.
    counted_runs: int = 0
    # The last counted slot's own ratio (not the estimate) and where it ended.
    last_value: float | None = None
    last_end: datetime | None = None

    @property
    def value(self) -> float | None:
        return ratio_value(self.ratio)


@dataclass(frozen=True)
class EffRecord:
    """A processed slot, kept for two slots: its kind and what counting it needs.
    ``energy_in`` is the grid import (charge) or the discharge (discharge); ``energy_out``
    the charge or the grid export. Both are 0 for a slot that is not pure."""

    start: datetime
    kind: EffKind | None
    energy_in: float
    energy_out: float
    standby_kwh: float | None


@dataclass(frozen=True)
class RunState:
    """The whole state of the machine; every field is plain data (ADR-023 decision 9)."""

    charge: EffEstimate = field(default_factory=EffEstimate)
    discharge: EffEstimate = field(default_factory=EffEstimate)
    prev1: EffRecord | None = None  # the slot before the one being processed
    prev2: EffRecord | None = None  # and the one before that
    # The current pure run has already counted a slot (so ``counted_runs`` counts it once).
    run_counted: bool = False
    last_slot: datetime | None = None


def classify_slot(active: Iterable[str], roles: EffRoles) -> EffKind | None:
    """``charging`` only when exactly the grid import and charge counters are active,
    ``discharging`` only when exactly the discharge and grid export counters are;
    anything else (a PV input, another counter, nothing, an unset role) is ``None``."""
    flow = frozenset(active)
    if roles.grid_import and roles.charge and flow == {roles.grid_import, roles.charge}:
        return EFF_KIND_CHARGE
    if roles.discharge and roles.grid_export and flow == {roles.discharge, roles.grid_export}:
        return EFF_KIND_DISCHARGE
    return None


def _eff_record(slot: EffSlot, kind: EffKind | None) -> EffRecord:
    if kind == EFF_KIND_CHARGE:
        return EffRecord(slot.start, kind, slot.grid_import_kwh, slot.charge_kwh, slot.standby_kwh)
    if kind == EFF_KIND_DISCHARGE:
        return EffRecord(
            slot.start, kind, slot.discharge_kwh, slot.grid_export_kwh, slot.standby_kwh
        )
    return EffRecord(slot.start, None, 0.0, 0.0, slot.standby_kwh)


def _eff_count(estimate: EffEstimate, record: EffRecord, new_run: bool) -> EffEstimate | None:
    """The estimate with ``record`` counted, or ``None`` when it does not count: no
    standby statistic, or an input not larger than the standby energy (decision 5)."""
    standby = record.standby_kwh
    if standby is None or record.energy_in <= standby:
        return None
    net = record.energy_in - standby
    return EffEstimate(
        ratio=decay_add(estimate.ratio, record.energy_in, record.energy_out, net),
        counted_kwh=estimate.counted_kwh + record.energy_in,
        counted_runs=estimate.counted_runs + (1 if new_run else 0),
        last_value=record.energy_out / net,
        last_end=record.start + _DRAIN_SLOT,
    )


def run_step(state: RunState, slot: EffSlot, roles: EffRoles) -> RunState:
    """The one transition of the machine: a slot that has settled enters.

    Slots must arrive in time order; one at or before the last processed slot is
    ignored (each slot counts once). A slot that does not directly follow the last one
    ends the run (an offline gap or a slot without statistics). The slot *before* the
    new one is counted when it and both its neighbours are of one kind, which is what
    leaves the first and the last slot of a run out (decision 4).
    """
    if state.last_slot is not None and slot.start <= state.last_slot:
        return state
    prev1, prev2, run_counted = state.prev1, state.prev2, state.run_counted
    if state.last_slot is None or slot.start != state.last_slot + _DRAIN_SLOT:
        prev1 = prev2 = None
        run_counted = False
    kind = classify_slot(slot.active, roles)
    charge, discharge = state.charge, state.discharge
    if kind is None or prev1 is None or prev1.kind != kind:
        run_counted = False  # no run, or a new one begins
    if kind is not None and prev1 is not None and prev2 is not None:
        if prev1.kind == kind and prev2.kind == kind:
            current = charge if kind == EFF_KIND_CHARGE else discharge
            counted = _eff_count(current, prev1, new_run=not run_counted)
            if counted is not None:
                run_counted = True
                if kind == EFF_KIND_CHARGE:
                    charge = counted
                else:
                    discharge = counted
    return RunState(
        charge=charge,
        discharge=discharge,
        prev1=_eff_record(slot, kind),
        prev2=prev1,
        run_counted=run_counted,
        last_slot=slot.start,
    )


def run_feed(
    state: RunState,
    slots: Iterable[EffSlot],
    roles: EffRoles,
    now: datetime,
    delay: timedelta = EFF_VALIDATION_DELAY,
) -> RunState:
    """Feed time-ordered slots; only those at least ``delay`` old at ``now`` enter.

    A slot that is too young stops the feed (every later one is younger): it enters
    at a later call, so feeding slot by slot or in batches gives the same result.
    """
    cutoff = now - delay
    for slot in sorted(slots, key=lambda s: s.start):
        if slot.start > cutoff:
            break
        state = run_step(state, slot, roles)
    return state


def _estimate_data(estimate: EffEstimate) -> dict[str, Any]:
    return {
        "num": estimate.ratio.num,
        "den": estimate.ratio.den,
        "counted_kwh": estimate.counted_kwh,
        "counted_runs": estimate.counted_runs,
        "last_value": estimate.last_value,
        "last_end": estimate.last_end.isoformat() if estimate.last_end else None,
    }


def _record_data(record: EffRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "start": record.start.isoformat(),
        "kind": record.kind,
        "energy_in": record.energy_in,
        "energy_out": record.energy_out,
        "standby_kwh": record.standby_kwh,
    }


def run_restore_data(state: RunState) -> dict[str, Any]:
    """The whole state as plain JSON types: decayed sums and attributes of both
    estimates, the classification of the last two slots and the run flag."""
    return {
        EFF_KIND_CHARGE: _estimate_data(state.charge),
        EFF_KIND_DISCHARGE: _estimate_data(state.discharge),
        "prev1": _record_data(state.prev1),
        "prev2": _record_data(state.prev2),
        "run_counted": state.run_counted,
        "last_slot": state.last_slot.isoformat() if state.last_slot else None,
    }


def _restore_number(value: Any, *, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if number == number and number >= minimum and number != float("inf") else None


def _estimate_from_data(data: Any) -> EffEstimate | None:
    if not isinstance(data, Mapping):
        return None
    num, den = _restore_number(data.get("num")), _restore_number(data.get("den"))
    kwh = _restore_number(data.get("counted_kwh"))
    runs = data.get("counted_runs")
    if num is None or den is None or kwh is None:
        return None
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 0:
        return None
    last_value = data.get("last_value")
    return EffEstimate(
        ratio=DecayedRatio(num, den),
        counted_kwh=kwh,
        counted_runs=runs,
        last_value=_restore_number(last_value, minimum=-float("inf")),
        last_end=_restore_time(data.get("last_end")),
    )


def _record_from_data(data: Any) -> EffRecord | None | Literal[False]:
    """The record, ``None`` for no record, ``False`` for unusable data."""
    if data is None:
        return None
    if not isinstance(data, Mapping):
        return False
    start = _restore_time(data.get("start"))
    kind = data.get("kind")
    energy_in, energy_out = (
        _restore_number(data.get("energy_in")),
        _restore_number(data.get("energy_out")),
    )
    standby = data.get("standby_kwh")
    if start is None or kind not in (EFF_KIND_CHARGE, EFF_KIND_DISCHARGE, None):
        return False
    if energy_in is None or energy_out is None:
        return False
    if standby is not None and _restore_number(standby) is None:
        return False
    return EffRecord(
        start, kind, energy_in, energy_out, None if standby is None else float(standby)
    )


def run_state_from_restore(data: Mapping[str, Any]) -> RunState | None:
    """The state to continue from, or ``None`` when ``data`` is unusable (the caller
    then starts from nothing, which is correct: nothing was counted yet)."""
    charge = _estimate_from_data(data.get(EFF_KIND_CHARGE))
    discharge = _estimate_from_data(data.get(EFF_KIND_DISCHARGE))
    prev1, prev2 = _record_from_data(data.get("prev1")), _record_from_data(data.get("prev2"))
    last_slot = _restore_time(data.get("last_slot"))
    run_counted = data.get("run_counted")
    if charge is None or discharge is None or last_slot is None:
        return None
    if prev1 is False or prev2 is False or not isinstance(run_counted, bool):
        return None
    if prev1 is None and prev2 is not None:
        return None  # the two records are the last two slots: a hole in them is nonsense
    return RunState(
        charge=charge,
        discharge=discharge,
        prev1=prev1,
        prev2=prev2,
        run_counted=run_counted,
        last_slot=last_slot,
    )
