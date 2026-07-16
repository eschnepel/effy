# ADR-014 – Larger Trapezoidal Cap, and a Staged (Not Unbounded) Offline-Anchor Lookback

**Date:** 2026-07-13
**Status:** Accepted — amends ADR-012 (`TRAPEZOID_MAX_MINUTES`) and ADR-013
Decision 5 (`RECENT_RECALC_WINDOW`, `_fetch_last_valid_state_before`).

**See also:** ADR-015 (same day) refines Decision 3 further: the lookback
now also skips entirely when the fetch window has no recovery in it at
all (a sensor invalid for the whole window otherwise re-triggered the
search every single cycle for no benefit), and a volatile
coordinator-level cache lets most lookbacks that *do* fire skip the
recorder query altogether.

---

## Context

Two related issues surfaced after ADR-013 shipped:

1. **Visible oscillation in derived-power data.** Real low-resolution
   energy meters often only tick every 20–90 minutes (their display
   resolution is coarser than what changes in a few minutes). With
   `TRAPEZOID_MAX_MINUTES = 15`, every such tick's delta was compressed
   into just the last 15 minutes before it arrived — combined with
   ADR-013's zero-fill (the prefix before that 15-minute window is now
   explicitly written as 0, not left blank), the result is a stark,
   repeating "0 for ~30–75 minutes, then a spike for 15 minutes" pattern —
   an oscillating line, not the smooth curve a roughly-constant real power
   draw should produce.
2. **A Home Assistant bootstrap timeout**, reported directly:

   ```
   WARNING (MainThread) [homeassistant.bootstrap] Setup timed out for
   bootstrap waiting on {<Task ... EffyCoordinator._async_recalculate_recent_and_report()
   ...>, <Task ... ButtonEntity._async_press_action() ...>, ...} - moving forward
   ```

   Root cause: ADR-013's `_fetch_last_valid_state_before`, once triggered
   (the small `RECENT_RECALC_WINDOW` fetch's first entry is invalid), ran
   a single unbounded, descending query across the *entire* configured
   `max_history_days` (28 by default). Right after a Home Assistant
   restart, many energy-family sensors can plausibly all have an invalid
   first entry in their small window at once — their owning integrations
   simply haven't reconnected yet — independently triggering this
   expensive lookback for every one of them, at the worst possible time
   (mid-bootstrap).

Fixing (1) by simply raising the cap made (2) more likely to matter more
often too: a larger cap means more "is this actually a long-but-normal
gap, or an offline one?" situations for the lookback to resolve.

---

## Decision 1 — `TRAPEZOID_MAX_MINUTES` raised from 15 to 120 minutes

The distribution window for a normal (non-offline) counter jump is now
capped at 120 minutes instead of 15. This comfortably covers realistic
low-resolution reporting intervals (20–90 minutes) without compressing
them into an artificially short, artificially high-rate window — the
direct fix for the oscillation. It remains a firm cap, not "however long
it takes": a gap longer than 120 minutes (with no offline indication in
between) still gets capped to the last 120 minutes, with the prefix
before that zero-filled (ADR-013 Decision 4's rule, unchanged in kind,
just operating at a larger scale now). A genuinely offline gap remains
uncapped regardless of this value, exactly as before.

## Decision 2 — `RECENT_RECALC_WINDOW` decoupled from `TRAPEZOID_MAX_MINUTES`

Before this, both `RECENT_RECALC_WINDOW` (how many slots the slot-timer-
driven recalc rewrites each cycle) and `_RAW_HISTORY_BOUNDARY_MARGIN`
(how much *extra raw history* is read to correctly anchor that smaller
write) were defined by the same formula, `TRAPEZOID_MAX_MINUTES + 5`. That
was fine at a 15-minute cap (~20 minutes either way) but would have meant
both silently growing to ~125 minutes with Decision 1's new cap —
reintroducing almost exactly the write-volume cost (and the
"`recalculated_from` always shows the window size" symptom) ADR-013
shrank `RECENT_RECALC_WINDOW` away from in the first place, just at a
different size.

These two constants now serve genuinely different purposes and are sized
independently:

- **`_RAW_HISTORY_BOUNDARY_MARGIN`** stays tied to `TRAPEZOID_MAX_MINUTES`
  (now ~125 minutes) — this is a single, bounded, indexed range *read* of
  one entity's raw history, not a write, and not the kind of cost that
  caused the bootstrap timeout. It needs to scale with the cap so a
  normal transition's own start stays visible.
- **`RECENT_RECALC_WINDOW`** stays fixed at 20 minutes, regardless of the
  cap. It controls how many slots get *rewritten* every ~5-minute cycle —
  a real, recurring write-volume cost, unrelated to how wide a single
  transition's window is allowed to be.

**Accepted trade-off:** when a jump takes longer than `RECENT_RECALC_WINDOW`
to arrive, its correct, smooth (never capped/inflated) rate is computed
and written immediately for whatever recent slots fall within that
window — but the older portion of that same jump's distribution keeps
whatever it was previously written as (typically 0, from the "no new
reading yet" synthetic continuation, ADR-013 Decision 4) until the next
full history recalc rewrites it. This is a *staleness* window, not an
*incorrect rate* — the rate itself is never capped/inflated regardless of
how long it takes to reach this window. Widening `RECENT_RECALC_WINDOW`
to close this staleness gap sooner was considered and rejected for now,
in favor of keeping per-cycle cost low; the full history recalc remains
the correctness backstop, as it already was for offline gaps.

## Decision 3 — `_fetch_last_valid_state_before` staged: 30 min → 1 day → full history

Replaces ADR-013's single jump straight to `max_history_days`. Tries
`_OFFLINE_ANCHOR_LOOKBACK_STAGES` (30 minutes, then 1 day) first, each a
bounded, `limit`-capped, descending query; only if *both* come up
completely empty does it fall back to searching the entire configured
`max_history_days` (still `limit`-capped). This is the fix for the
reported bootstrap timeout: instead of every simultaneously-affected
sensor running one unbounded, days-long scan at boot, most resolve at the
30-minute or 1-day stage — a small fraction of the cost — with the full,
expensive search reserved for the genuinely rare case (a sensor offline
for more than a day). This function is still only invoked for a sensor
that's actually invalid right at the point the regular fetch starts — a
merely slow-ticking, still-online sensor never reaches it at all, since
`include_start_time_state=True` already finds its true last reading for
free in the regular fetch, however old that reading is.

**Refinement — only search when the window also contains a recovery.**
The trigger above ("the window's first entry is invalid") isn't quite
enough by itself: a sensor that stays invalid for the *entire* window —
e.g. a battery empty all night, if its integration reports the discharge
sensor as unavailable rather than a valid 0 — would otherwise re-trigger
this lookback on *every single cycle* for as long as the outage lasts,
even though the anchor it finds is never actually used: with no valid
reading following the invalid stretch anywhere in that window,
`trapezoidal_slot_contributions` never forms a transition from it (no
recovery to anchor), and the "currently invalid" sensor also doesn't
qualify for the synthetic now-continuation (that requires the sensor to
be currently *valid*, ADR-013). So the caller now also checks whether the
fetched window contains at least one valid reading anywhere (a genuine
recovery within this specific window) before searching at all. In
practice: no search runs while the outage is ongoing and nothing has
changed; exactly one search runs on the cycle the sensor's first
post-outage reading actually arrives.

---

## Consequences

- Derived-power data for low-resolution energy meters (20–90 minute tick
  intervals) is now a smooth curve reflecting the actual average rate
  over each real interval, not a repeating zero/spike oscillation.
- The regular per-cycle raw-history read grows from ~20 minutes to ~2
  hours of one entity's history — a single bounded, indexed range query,
  not the kind of cost this ADR is otherwise reducing.
- `RECENT_RECALC_WINDOW` (slot-timer write volume per cycle) is unchanged
  at 20 minutes — Decision 1 does not increase how many statistics rows
  get rewritten every ~5 minutes, only how far back a single transition's
  *rate* can legitimately be computed from.
- The `_fetch_last_valid_state_before` lookback — previously capable of
  triggering a Home Assistant bootstrap timeout when many sensors hit it
  simultaneously — now resolves the overwhelming majority of real-world
  cases (a blip, an overnight outage, an HA restart) within a 30-minute or
  1-day query, reserving the expensive full-history search for a sensor
  genuinely offline longer than a day.
- A sensor that's continuously invalid for an extended stretch (e.g. a
  battery reported as unavailable, not 0, all night while empty) no
  longer re-triggers this lookback on every single cycle for the whole
  duration — only once, on the cycle its first post-outage reading
  actually arrives (Decision 3's refinement).
- Accepted limitation (Decision 2): a jump that took between
  `RECENT_RECALC_WINDOW` (20 min) and `TRAPEZOID_MAX_MINUTES` (120 min) to
  arrive has its older slots' correction delayed until the next full
  history recalc, even though the rate itself, once computed, is already
  correct and un-inflated.
