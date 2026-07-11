# ADR-011 – Slot-Timer-Driven Single-Slot History Recalculation

**Date:** 2026-07-10
**Status:** Accepted — extends ADR-010 (slot timer), ADR-006 (`EffyCoordinator`
lifecycle), and the history path (`history.py`, ADR-003/004/008/009).

---

## Context

The live path was disabled 2026-07-09 (see `disabled/README.md`) until the
history path works correctly on its own. `EffyCoordinator` was kept as a
reduced shell specifically because its slot-aligned timer (ADR-010) was
always intended to be repurposed: instead of driving live event
accumulation, it should compute each slot, once, from the same underlying
statistics the history path already uses — "History nur für einen Slot"
(history, but for exactly one slot).

Three concrete design questions came up while wiring this up.

---

## Decision 1 — reuse `async_recalculate_history`'s logic for one slot

`async_recalculate_history` had no `start`/`end` parameters; it always
computed `start = now − max_history_days`, `end = now`. Its core
computation (unit resolution — including the ADR-003 statistics-metadata
fix, fetching statistics, optional ADR-009 smoothing, per-slot
`distribute_loss`) is identical regardless of range size, so it was
extracted into `_compute_effective_slots(hass, entry_options, start, end)`,
shared by both:

- `async_recalculate_history` — unchanged behaviour, calls the shared
  helper with the existing multi-day range.
- `async_recalculate_slot(hass, entry_options, slot_start)` (new) — calls
  the same helper with `[slot_start, slot_start + SLOT_MINUTES)`.

`_smooth_energy_rows` (ADR-009) needs at least 2 rows of neighbour context
per entity to do anything (`if len(sorted_starts) < 2: continue`) — for a
single-slot range this is naturally, harmlessly a no-op; no special-casing
was needed.

## Decision 2 — the single-slot write must skip the long-term (hourly) statistic

`_write_recorder_statistics` computes the hourly `mean` as the average of
*every* slot value that hour contains. Calling it once per slot with only
that one slot's value would write an hourly `mean` equal to just that slot
— overwriting (ADR-004 overwrite semantics) whatever correct, fully-averaged
value the last full history recalc had written, every 5 minutes.

`_write_recorder_statistics` gained an `include_long_term: bool = True`
parameter; `async_recalculate_slot` always passes `include_long_term=False`.
Long-term (hourly) aggregation remains exclusively the responsibility of
`async_recalculate_history` (button/service), which sees every slot in
each hour at once. `short_term_cutoff` was also made optional
(`None` = no filtering) since a single-slot call is always recent enough
that the retention cutoff can never exclude it — the caller doesn't need
to compute one just to pass it through unfiltered.

## Decision 3 — timer fires *after* the boundary, not before

ADR-010 originally fired the timer `SLOT_TIMER_LEAD_SECONDS` **before**
each boundary, computing the slot *two* boundaries back — trading fresher
data for a generous (~5 minute) safety margin for the recorder to have
compiled that slot's statistics.

This ADR changes that to `SLOT_TIMER_LAG_SECONDS` (same value, 5s) **after**
each boundary, computing the slot that *just* closed:

```
slot to recalculate = [boundary − SLOT_MINUTES, boundary)
trigger point        = boundary + SLOT_TIMER_LAG_SECONDS
```

This gives fresher history data (available within ~5s of a slot closing,
instead of waiting almost a full extra slot) at the cost of a much
tighter margin for the recorder to have compiled that slot's statistics
in time. If it hasn't, `_compute_effective_slots` simply finds no rows for
that slot, `async_recalculate_slot` logs a debug message, and writes
nothing — there's no partial/incorrect write, only a possible missed
slot, and a chance to still catch it on the *next* full history recalc.
`_next_slot_trigger_delay` was updated accordingly (`trigger_at =
current_slot_start + lag_seconds`, skip to the next slot if that point has
already passed) and `EffyCoordinator._on_slot_timer` recomputes the
just-closed slot's boundary from its own actual firing time (not the
time it was scheduled for), so ordinary timer jitter can't shift which
slot gets processed.

## Decision 4 (open question, resolved) — could a live push replace the history write?

Raised while reviewing this design: if the live path is eventually
restored (or partially re-enabled) to *push* a computed value to
`EffySensor`, would that push, picked up by HA's own automatic statistics
compiler, make the explicit `async_recalculate_slot` write redundant?

**No.** `async_write_ha_state()` (and `hass.states.async_set`) always
timestamp the state change at the moment they're called — there is no
public API to backdate a live entity state into an already-closed slot.
A push happening shortly after a slot closes would be attributed by HA's
own compiler to whatever slot is *currently in progress at push time*,
not the slot that was just computed. Only the statistics import API
(`async_import_statistics`, what `_write_recorder_statistics` already
uses) can write an arbitrary historical timestamp directly into the
statistics tables, bypassing the `states` table entirely — which is
exactly why `history.py` is built the way it is (see the module-level
WARNING docstring in `history.py` on internal-API usage).

Conclusion: a live push, if added later, is **additive** — it would
update the currently-displayed value for whatever slot is in progress at
push time — not a substitute for the backdated 5-minute history write
implemented here. `EffyCoordinator._subscribers` stays wired up and ready
for that (`subscribe`/`unsubscribe` unchanged), but nothing currently
publishes to it — `async_recalculate_slot` only writes statistics.

---

## Consequences

- Historical graphs (5-minute resolution) for `effy_*` sensors now stay
  current on their own, roughly once per slot, without any state-change
  activity being required on the input/output sensors — solving the
  original "no recalculation happens at all in a fully quiet system"
  problem (ADR-010's Problem C) via the history path instead of live
  event accumulation.
- Long-term (hourly) statistics are *not* kept current by this timer —
  they still require a full `async_recalculate_history` run (button,
  or whatever schedule is set up for it) to be accurate.
- The live-displayed entity state (`EffySensor.native_value`) is not
  updated by this change — see Decision 4. It stays whatever it last was
  before the live path was disabled, or `unknown` for a newly added entity.
- If the recorder hasn't compiled a slot's statistics within
  `SLOT_TIMER_LAG_SECONDS` of it closing, that slot is silently skipped
  (debug-logged) rather than retried — it will only be picked up by a
  subsequent full history recalc.
