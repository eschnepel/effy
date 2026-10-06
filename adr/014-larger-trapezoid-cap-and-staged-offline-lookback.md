# ADR-014 – Larger Trapezoidal Cap, and a Staged (Not Unbounded) Offline-Anchor Lookback

**Date:** 2026-07-13 **Status:** Accepted — amends ADR-012
(`TRAPEZOID_MAX_MINUTES`) and ADR-013 Decision 5 (`RECENT_RECALC_WINDOW`,
`_fetch_last_valid_state_before`). **Decision 2's "accepted trade-off" was
reversed on 2026-10-05 — see the Amendment at the end of this file.**

**See also:** ADR-015 (same day) refines Decision 3 further: the lookback now
also skips entirely when the fetch window has no recovery in it at all (a sensor
invalid for the whole window otherwise re-triggered the search every single
cycle for no benefit), and a volatile coordinator-level cache lets most
lookbacks that *do* fire skip the recorder query altogether.

______________________________________________________________________

## Context

Two related issues surfaced after ADR-013 shipped:

1. **Visible oscillation in derived-power data.** Real low-resolution energy
   meters often only tick every 20–90 minutes (their display resolution is
   coarser than what changes in a few minutes). With
   `TRAPEZOID_MAX_MINUTES = 15`, every such tick's delta was compressed into
   just the last 15 minutes before it arrived — combined with ADR-013's
   zero-fill (the prefix before that 15-minute window is now explicitly written
   as 0, not left blank), the result is a stark, repeating "0 for ~30–75
   minutes, then a spike for 15 minutes" pattern — an oscillating line, not the
   smooth curve a roughly-constant real power draw should produce.

1. **A Home Assistant bootstrap timeout**, reported directly:

   ```
   WARNING (MainThread) [homeassistant.bootstrap] Setup timed out for
   bootstrap waiting on {<Task ... EffyCoordinator._async_recalculate_recent_and_report()
   ...>, <Task ... ButtonEntity._async_press_action() ...>, ...} - moving forward
   ```

   Root cause: ADR-013's `_fetch_last_valid_state_before`, once triggered (the
   small `RECENT_RECALC_WINDOW` fetch's first entry is invalid), ran a single
   unbounded, descending query across the *entire* configured `max_history_days`
   (28 by default). Right after a Home Assistant restart, many energy-family
   sensors can plausibly all have an invalid first entry in their small window
   at once — their owning integrations simply haven't reconnected yet —
   independently triggering this expensive lookback for every one of them, at
   the worst possible time (mid-bootstrap).

Fixing (1) by simply raising the cap made (2) more likely to matter more often
too: a larger cap means more "is this actually a long-but-normal gap, or an
offline one?" situations for the lookback to resolve.

______________________________________________________________________

## Decision 1 — `TRAPEZOID_MAX_MINUTES` raised from 15 to 120 minutes

The distribution window for a normal (non-offline) counter jump is now capped at
120 minutes instead of 15. This comfortably covers realistic low-resolution
reporting intervals (20–90 minutes) without compressing them into an
artificially short, artificially high-rate window — the direct fix for the
oscillation. It remains a firm cap, not "however long it takes": a gap longer
than 120 minutes (with no offline indication in between) still gets capped to
the last 120 minutes, with the prefix before that zero-filled (ADR-013 Decision
4's rule, unchanged in kind, just operating at a larger scale now). A genuinely
offline gap remains uncapped regardless of this value, exactly as before.

## Decision 2 — `RECENT_RECALC_WINDOW` decoupled from `TRAPEZOID_MAX_MINUTES`

Before this, both `RECENT_RECALC_WINDOW` (how many slots the slot-timer- driven
recalc rewrites each cycle) and `_RAW_HISTORY_BOUNDARY_MARGIN` (how much *extra
raw history* is read to correctly anchor that smaller write) were defined by the
same formula, `TRAPEZOID_MAX_MINUTES + 5`. That was fine at a 15-minute cap (~20
minutes either way) but would have meant both silently growing to ~125 minutes
with Decision 1's new cap — reintroducing almost exactly the write-volume cost
(and the "`recalculated_from` always shows the window size" symptom) ADR-013
shrank `RECENT_RECALC_WINDOW` away from in the first place, just at a different
size.

These two constants now serve genuinely different purposes and are sized
independently:

- **`_RAW_HISTORY_BOUNDARY_MARGIN`** stays tied to `TRAPEZOID_MAX_MINUTES` (now
  ~125 minutes) — this is a single, bounded, indexed range *read* of one
  entity's raw history, not a write, and not the kind of cost that caused the
  bootstrap timeout. It needs to scale with the cap so a normal transition's own
  start stays visible.
- **`RECENT_RECALC_WINDOW`** stays fixed at 20 minutes, regardless of the cap.
  It controls how many slots get *rewritten* every ~5-minute cycle — a real,
  recurring write-volume cost, unrelated to how wide a single transition's
  window is allowed to be.

**Accepted trade-off:** when a jump takes longer than `RECENT_RECALC_WINDOW` to
arrive, its correct, smooth (never capped/inflated) rate is computed and written
immediately for whatever recent slots fall within that window — but the older
portion of that same jump's distribution keeps whatever it was previously
written as (typically 0, from the "no new reading yet" synthetic continuation,
ADR-013 Decision 4) until the next full history recalc rewrites it. This is a
*staleness* window, not an *incorrect rate* — the rate itself is never
capped/inflated regardless of how long it takes to reach this window. Widening
`RECENT_RECALC_WINDOW` to close this staleness gap sooner was considered and
rejected for now, in favor of keeping per-cycle cost low; the full history
recalc remains the correctness backstop, as it already was for offline gaps.

## Decision 3 — `_fetch_last_valid_state_before` staged: 30 min → 1 day → full history

Replaces ADR-013's single jump straight to `max_history_days`. Tries
`_OFFLINE_ANCHOR_LOOKBACK_STAGES` (30 minutes, then 1 day) first, each a
bounded, `limit`-capped, descending query; only if *both* come up completely
empty does it fall back to searching the entire configured `max_history_days`
(still `limit`-capped). This is the fix for the reported bootstrap timeout:
instead of every simultaneously-affected sensor running one unbounded, days-long
scan at boot, most resolve at the 30-minute or 1-day stage — a small fraction of
the cost — with the full, expensive search reserved for the genuinely rare case
(a sensor offline for more than a day). This function is still only invoked for
a sensor that's actually invalid right at the point the regular fetch starts — a
merely slow-ticking, still-online sensor never reaches it at all, since
`include_start_time_state=True` already finds its true last reading for free in
the regular fetch, however old that reading is.

**Refinement — only search when the window also contains a recovery.** The
trigger above ("the window's first entry is invalid") isn't quite enough by
itself: a sensor that stays invalid for the *entire* window — e.g. a battery
empty all night, if its integration reports the discharge sensor as unavailable
rather than a valid 0 — would otherwise re-trigger this lookback on *every
single cycle* for as long as the outage lasts, even though the anchor it finds
is never actually used: with no valid reading following the invalid stretch
anywhere in that window, `trapezoidal_slot_contributions` never forms a
transition from it (no recovery to anchor), and the "currently invalid" sensor
also doesn't qualify for the synthetic now-continuation (that requires the
sensor to be currently *valid*, ADR-013). So the caller now also checks whether
the fetched window contains at least one valid reading anywhere (a genuine
recovery within this specific window) before searching at all. In practice: no
search runs while the outage is ongoing and nothing has changed; exactly one
search runs on the cycle the sensor's first post-outage reading actually
arrives.

______________________________________________________________________

## Consequences

- Derived-power data for low-resolution energy meters (20–90 minute tick
  intervals) is now a smooth curve reflecting the actual average rate over each
  real interval, not a repeating zero/spike oscillation.
- The regular per-cycle raw-history read grows from ~20 minutes to ~2 hours of
  one entity's history — a single bounded, indexed range query, not the kind of
  cost this ADR is otherwise reducing.
- `RECENT_RECALC_WINDOW` (slot-timer write volume per cycle) is unchanged at 20
  minutes — Decision 1 does not increase how many statistics rows get rewritten
  every ~5 minutes, only how far back a single transition's *rate* can
  legitimately be computed from.
- The `_fetch_last_valid_state_before` lookback — previously capable of
  triggering a Home Assistant bootstrap timeout when many sensors hit it
  simultaneously — now resolves the overwhelming majority of real-world cases (a
  blip, an overnight outage, an HA restart) within a 30-minute or 1-day query,
  reserving the expensive full-history search for a sensor genuinely offline
  longer than a day.
- A sensor that's continuously invalid for an extended stretch (e.g. a battery
  reported as unavailable, not 0, all night while empty) no longer re-triggers
  this lookback on every single cycle for the whole duration — only once, on the
  cycle its first post-outage reading actually arrives (Decision 3's
  refinement).
- Accepted limitation (Decision 2): a jump that took between
  `RECENT_RECALC_WINDOW` (20 min) and `TRAPEZOID_MAX_MINUTES` (120 min) to
  arrive has its older slots' correction delayed until the next full history
  recalc, even though the rate itself, once computed, is already correct and
  un-inflated.

______________________________________________________________________

## Amendment — 2026-10-05

**Reason:** Decision 2's accepted trade-off — a jump that took longer than
`RECENT_RECALC_WINDOW` (20 min) to arrive keeps its older slots at 0 until the
next full history recalc — turned out to be the *normal* case for low-resolution
energy meters (BMS charge counter at 0.01 kWh resolution, ticking every 19–35
min), not an edge case. Observed in production on `BMS Batterie Ladung derived`:
every 10 Wh tick showed up only as a short (~15 min) bump at the correct rate,
separated by flat-0 gaps — the very oscillation Decision 1 was meant to remove.
The stored series violated the energy-conservation guarantee of ADR-012 (area
under the derived power ≈ half of the raw energy in a reproduction: 30.2 Wh vs.
60 Wh for 6 ticks). A manual full recalc produced the correct, conserving series
(validated by the human on the live instance), confirming the algorithm is right
and only the slot-timer's rewrite range is too narrow. The "staleness window,
not an incorrect rate" framing in Decision 2 understated this: the *rate* was
right, the *energy* was not.

**Decision:** The slot-timer path (`async_recalculate_recent`) rewrites back to
wherever a newly arrived jump's own distribution window starts, instead of
always exactly `RECENT_RECALC_WINDOW`:

1. **Idle cycles are unchanged.** With no new jump, the rewrite range is still
   the last `RECENT_RECALC_WINDOW` (20 min) — no extra write cost in the common
   case.
1. **A jump extends the range** *(trigger widened to every offline recovery by
   Amendment 2026-10-06 below)*. For every energy-family sensor, each *real*
   jump — a transition between two valid readings with `delta > 0`, including
   the first reading after an offline stretch, but excluding zero-delta
   transitions and the synthetic "now" continuation (ADR-013) — whose end `t2`
   lies within `RECENT_RECALC_WINDOW` before `now` moves the rewrite start back
   to the slot containing that jump's distribution-window start:
   `max(t1, t2 − TRAPEZOID_MAX_MINUTES)` for a normal jump, `t1` (uncapped) for
   an offline-recovery jump.
1. **The extended start is global.** It is the minimum over all sensors and
   applies to *all* sensors and all three series (effective, derived power,
   smoothed) for that cycle. Never per-entity: `distribute_loss` needs every
   sensor's reading for a slot, so a narrower range for one sensor would run the
   waterfall on a subset and overwrite correct older rows with wrong ones.
1. **Bounded.** The extension never reaches earlier than
   `now − RECENT_REWRITE_MAX_LOOKBACK` (1 day). An offline gap longer than that
   is still corrected by the next full history recalc, as before.
1. **Single source of truth for jump detection.** The transition detection
   inside `calculation.trapezoidal_slot_contributions` is factored into a shared
   private helper; a new pure function in `calculation.py` exposes each real
   jump's `(t2, window_start)` from that same helper — no duplicated transition
   logic (ADR-000 §3: pure, HA-free, zero-mock tests).
1. **Raw-history margin.** `_RAW_HISTORY_BOUNDARY_MARGIN` is applied before the
   *extended* start, so jumps overlapping the extended range still see their own
   start.
1. **Testable invariant.** After every slot-timer cycle at time `T`, each closed
   slot the cycle wrote equals what a full history recalc at `T` would produce
   for that slot, and the area under the derived power over a jump equals the
   jump's delta (conservation).

**Unchanged:** `RECENT_RECALC_WINDOW` stays 20 min; short-term statistics only
(ADR-011 Decision 2) — hourly long-term values still come exclusively from a
full recalc; zero-fill semantics (ADR-013 Decision 4); `TRAPEZOID_MAX_MINUTES` =
120\.

**Consequences of this amendment:**

- On a cycle where a jump arrived, up to ~28 slots per sensor per series are
  rewritten instead of 4 (a jump stays inside the 20-min trigger window for ~4
  consecutive cycles). Idle cycles cost the same as before.
- `sensor.effy_recalculated_from` (ADR-012/013) can now read up to ~2 h back on
  a cycle where a jump arrived (it reports the earliest touched slot); that is
  accurate — those slots really were rewritten.
- Supersedes the "Accepted trade-off" paragraph of Decision 2 and the last
  bullet of the original Consequences section ("a jump that took between
  `RECENT_RECALC_WINDOW` and `TRAPEZOID_MAX_MINUTES` to arrive has its older
  slots' correction delayed until the next full history recalc").
- Not addressed here (separate findings, not decided): the unaligned
  `start = now − RECENT_RECALC_WINDOW` filter makes the effective window 15 min
  rather than 20; the 5-second-old in-progress slot is written (as ~0) and
  picked as the "last slot" for ADR-016's live push.

**Decided by:** human (Option 1 selected, diagnosis validated by a manual recalc
on the live instance); design details 1–7 by Lead Agent — pending human
confirmation.

______________________________________________________________________

## Amendment — 2026-10-06

### Part 1 — offline recoveries of any delta (AUDIT-0001)

**Reason:** Audit AUDIT-0001 found that the 2026-10-05 amendment's item 2
excluded zero-delta transitions without exception, including a zero-delta
*offline recovery* (counter unchanged across an outage, e.g. 5.00 → unavailable
→ 5.00). A full recalc zero-fills such an outage (ADR-013 Decision 4); the
slot-timer path never rewrote back to it, so item 7 (the timer path equals a
full recalc) was violated: "no data" instead of 0 during the outage until the
next full recalc. No energy was lost (delta 0).

**Decision:** Every offline recovery is a trigger, whatever its delta. Item 2
now reads: *each real jump (a transition between two valid readings with
`delta > 0`) **or offline recovery (the first valid reading after an offline
stretch, including `delta == 0` and a counter reset)** whose end `t2` lies
within the trigger window before `now` moves the rewrite start back to the slot
containing that event's distribution-window start* — `t1` (uncapped) for an
offline recovery, `max(t1, t2 − TRAPEZOID_MAX_MINUTES)` for a normal jump.
Zero-delta and reset transitions between two directly consecutive valid readings
and the synthetic "now" continuation are still not triggers. Still bounded by
`RECENT_REWRITE_MAX_LOOKBACK` (item 4); the cost is one wide (≤ 1 day) rewrite
per outage recovery. Implemented in `calculation.trapezoidal_jump_windows`.

**Decided by:** human (AUDIT-0001, Issue 1, Option A).

### Part 2 — the trigger window follows the last completed cycle (AUDIT-0002, Issue 1)

**Reason:** The trigger condition of item 2 was
`now − RECENT_RECALC_WINDOW <= t2`. A jump that arrived while the slot timer was
not running for longer than that window (HA restart, long blocking, suspended
host, failed cycles) was therefore older than the window at the first cycle
afterwards and never extended the range: the older part of its distribution
window stayed at stale 0s until a full recalc — the very energy loss the
2026-10-05 amendment fixed for the always-running case. Reproduced: the BMS
scenario with no cycles from 11:55 to 12:40 stored 0.0418 kWh instead of 0.0600
kWh. This also retires the "long-neglected sensor → full recalc" caveat of
Decision 2 for pauses of up to `RECENT_REWRITE_MAX_LOOKBACK`.

**Decision:** The trigger window is dynamic (new item 8):

8. A jump / offline recovery triggers the extension when
   `trigger_from <= t2 <= now`, where `trigger_from` is
   `now − RECENT_RECALC_WINDOW` by default and reaches back to `trigger_since`
   when one is passed:
   `min(now − window, max(trigger_since, now − RECENT_REWRITE_MAX_LOOKBACK))`
   (`calculation.recent_trigger_from`, single source of truth). `trigger_since`
   is the `now` of the last cycle that **completed without raising**, kept in
   memory by `EffyCoordinator` (never persisted; advanced with `max()` because
   cycles may overlap) — so a failed cycle is covered by the next one. On the
   first cycle of a session (no previous cycle) a one-off catch-up window of
   `RECENT_REWRITE_MAX_LOOKBACK` is used (`history.recent_trigger_since`). At
   the normal 5-minute cadence `trigger_since` lies inside the window and
   changes nothing: idle cycles keep the same range, the same single raw fetch
   per sensor and the same writes. The *first* raw fetch of a cycle starts
   `_RAW_HISTORY_BOUNDARY_MARGIN` before `min(base start, trigger_from)`;
   otherwise a jump older than the base window would not be visible at all. The
   extension itself is still bounded by `RECENT_REWRITE_MAX_LOOKBACK` (item 4);
   a jump older than that bound is ignored because it can no longer change
   anything inside the bounded range, so a long pause does not become a day-long
   rewrite for nothing.

`async_recalculate_recent` gains the optional keyword
`trigger_since: datetime | None = None` (default = pre-amendment behaviour;
return shape unchanged) — an ADR-011 matter, noted there.

**Consequences:** The first cycle after every Home Assistant start reads up to
one day (+ margin) of raw history per energy sensor and rewrites back to the
earliest jump/outage window of the last day if any sensor had one — up to ~288
slots per sensor and series, once per start (idle sensors cost only the read).
This is an accepted trade-off for closing the restart gap; it runs as the usual
background task of the slot timer, but happens close to Home Assistant's
bootstrap — if bootstrap warnings (ADR-014 Context 2) ever reappear, this
catch-up is the first suspect.

**Decided by:** human (AUDIT-0002, Issue 1, Option A: dynamic trigger window,
one-off catch-up of `RECENT_REWRITE_MAX_LOOKBACK` after start-up); parameter
shape and the "last *completed* cycle" rule by Lead Agent — pending human
confirmation.
