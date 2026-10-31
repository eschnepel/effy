# ADR-011 – Slot-Timer-Driven Single-Slot History Recalculation

**Date:** 2026-07-10 **Status:** Accepted — extends ADR-010 (slot timer),
ADR-006 (`EffyCoordinator` lifecycle), and the history path (`history.py`,
ADR-003/004/008/009).

**See also:** ADR-012 (2026-07-11) replaces the fixed single-slot design
described here (`async_recalculate_slot`) with a dynamic-range
`async_recalculate_recent`, since a trapezoidal-redistributed energy jump
(ADR-012) can touch more than one slot. Decisions 2–4 below (no long-term write,
timer timing, the live-push question) are otherwise unaffected — Decision 4's
"recalculated from" idea is what ADR-012 actually implements.

ADR-016 (2026-07-16) implements the additive live push Decision 4 below
concludes is possible — pushing each recalculation's last-written value as a
live state, superseding the "unknown"-only push described there and in the
Consequences section below. The core finding of Decision 4 is unchanged: no
public API can backdate a live state into an already-closed slot, so the
statistics write remains the only thing that corrects that slot; ADR-016 only
changes what the *additive* live push carries.

---

## Context

The live path was disabled 2026-07-09 (see `disabled/README.md`) until the
history path works correctly on its own. `EffyCoordinator` was kept as a reduced
shell specifically because its slot-aligned timer (ADR-010) was always intended
to be repurposed: instead of driving live event accumulation, it should compute
each slot, once, from the same underlying statistics the history path already
uses — "History nur für einen Slot" (history, but for exactly one slot).

Three concrete design questions came up while wiring this up.

---

## Decision 1 — reuse `async_recalculate_history`'s logic for one slot

`async_recalculate_history` had no `start`/`end` parameters; it always computed
`start = now − max_history_days`, `end = now`. Its core computation (unit
resolution — including the ADR-003 statistics-metadata fix, fetching statistics,
optional ADR-009 smoothing, per-slot `distribute_loss`) is identical regardless
of range size, so it was extracted into
`_compute_effective_slots(hass, entry_options, start, end)`, shared by both:

- `async_recalculate_history` — unchanged behaviour, calls the shared helper
  with the existing multi-day range.
- `async_recalculate_slot(hass, entry_options, slot_start)` (new) — calls the
  same helper with `[slot_start, slot_start + SLOT_MINUTES)`.

`_smooth_energy_rows` (ADR-009) needs at least 2 rows of neighbour context per
entity to do anything (`if len(sorted_starts) < 2: continue`) — for a
single-slot range this is naturally, harmlessly a no-op; no special-casing was
needed.

## Decision 2 — the single-slot write must skip the long-term (hourly) statistic

`_write_recorder_statistics` computes the hourly `mean` as the average of
*every* slot value that hour contains. Calling it once per slot with only that
one slot's value would write an hourly `mean` equal to just that slot —
overwriting (ADR-004 overwrite semantics) whatever correct, fully-averaged value
the last full history recalc had written, every 5 minutes.

`_write_recorder_statistics` gained an `include_long_term: bool = True`
parameter; `async_recalculate_slot` always passes `include_long_term=False`.
Long-term (hourly) aggregation remains exclusively the responsibility of
`async_recalculate_history` (button/service), which sees every slot in each hour
at once. `short_term_cutoff` was also made optional (`None` = no filtering)
since a single-slot call is always recent enough that the retention cutoff can
never exclude it — the caller doesn't need to compute one just to pass it
through unfiltered.

## Decision 3 — timer fires *after* the boundary, not before

ADR-010 originally fired the timer `SLOT_TIMER_LEAD_SECONDS` **before** each
boundary, computing the slot *two* boundaries back — trading fresher data for a
generous (~5 minute) safety margin for the recorder to have compiled that slot's
statistics.

This ADR changes that to `SLOT_TIMER_LAG_SECONDS` (same value, 5s) **after**
each boundary, computing the slot that *just* closed:

```
slot to recalculate = [boundary − SLOT_MINUTES, boundary)
trigger point        = boundary + SLOT_TIMER_LAG_SECONDS
```

This gives fresher history data (available within ~5s of a slot closing, instead
of waiting almost a full extra slot) at the cost of a much tighter margin for
the recorder to have compiled that slot's statistics in time. If it hasn't,
`_compute_effective_slots` simply finds no rows for that slot,
`async_recalculate_slot` logs a debug message, and writes nothing — there's no
partial/incorrect write, only a possible missed slot, and a chance to still
catch it on the *next* full history recalc. `_next_slot_trigger_delay` was
updated accordingly (`trigger_at = current_slot_start + lag_seconds`, skip to
the next slot if that point has already passed) and
`EffyCoordinator._on_slot_timer` recomputes the just-closed slot's boundary from
its own actual firing time (not the time it was scheduled for), so ordinary
timer jitter can't shift which slot gets processed.

## Decision 4 (open question, resolved) — could a live push replace the history write?

Raised while reviewing this design: if the live path is eventually restored (or
partially re-enabled) to *push* a computed value to `EffySensor`, would that
push, picked up by HA's own automatic statistics compiler, make the explicit
`async_recalculate_slot` write redundant?

**No.** `async_write_ha_state()` (and `hass.states.async_set`) always timestamp
the state change at the moment they're called — there is no public API to
backdate a live entity state into an already-closed slot. A push happening
shortly after a slot closes would be attributed by HA's own compiler to whatever
slot is *currently in progress at push time*, not the slot that was just
computed. Only the statistics import API (`async_import_statistics`, what
`_write_recorder_statistics` already uses) can write an arbitrary historical
timestamp directly into the statistics tables, bypassing the `states` table
entirely — which is exactly why `history.py` is built the way it is (see the
module-level WARNING docstring in `history.py` on internal-API usage).

Conclusion: a live push, if added later, is **additive** — it would update the
currently-displayed value for whatever slot is in progress at push time — not a
substitute for the backdated 5-minute history write implemented here.
`EffyCoordinator._subscribers` stays wired up and ready for that
(`subscribe`/`unsubscribe` unchanged), but nothing currently publishes to it —
`async_recalculate_slot` only writes statistics.

---

## Consequences

- Historical graphs (5-minute resolution) for `effy_*` sensors now stay current
  on their own, roughly once per slot, without any state-change activity being
  required on the input/output sensors — solving the original "no recalculation
  happens at all in a fully quiet system" problem (ADR-010's Problem C) via the
  history path instead of live event accumulation.
- Long-term (hourly) statistics are *not* kept current by this timer — they
  still require a full `async_recalculate_history` run (button, or whatever
  schedule is set up for it) to be accurate.
- The live-displayed entity state (`EffySensor.native_value`) is not updated by
  this change — see Decision 4. It stays whatever it last was before the live
  path was disabled, or `unknown` for a newly added entity.
- If the recorder hasn't compiled a slot's statistics within
  `SLOT_TIMER_LAG_SECONDS` of it closing, that slot is silently skipped
  (debug-logged) rather than retried — it will only be picked up by a subsequent
  full history recalc.

---

## Amendment — 2026-10-06

**Reason:** Audit AUDIT-0002. The slot timer now also has to know when the
previous cycle completed (a jump that arrived while no cycle ran must still be
applied — ADR-014 Amendment 2026-10-06).

**Decision:** `EffyCoordinator` keeps the end time of the last completed cycle
in memory (`_last_recent_run`, volatile, never persisted) and passes the derived
`trigger_since` to
`async_recalculate_recent(hass, entry_options, now, energy_reading_cache=None, trigger_since=None)`.
The parameter is optional and the return shape
`(written, recalculated_from, touched, last_values)` is unchanged; Decisions 2–4
above are unaffected (short-term only; timer timing). The slot containing `now`
is written with a provisional value — see ADR-016 Amendment 2026-10-06.

**Decided by:** human (AUDIT-0002, Issue 1, Option A); Lead Agent (signature) —
pending human confirmation.

## Amendment — 2026-10-16

**Reason:** TASK-0006 (human release 2026-10-16; same change as Shady's
TASK-0041). Decision 3 fires the recent recalculation on the wall clock, 5 s
after the boundary. The recorder compiles each slot about 10 s after its
boundary and fires `EVENT_RECORDER_5MIN_STATISTICS_GENERATED` from inside the
compile, before its session commits, so the last completed slot's power-family
`mean` is not yet readable at boundary + 5 s.

**Decision:** Decision 3 is superseded. The recent recalculation is started by a
*slot job*, triggered by the recorder's own event. The job probes
(`history.async_probe_last_slot_ready`: the slot before the last completed one
has a `mean` and the last completed one has none, for any power-family sensor)
every `PROBE_INTERVAL` = 1 s, at most `MAX_PROBES` = 5 times, with an `await` on
the event loop, and then runs the former timer body once; a sensor with no data
never delays it, a failing probe does not stop it. Two triggers for the same
last completed slot run the body once; a newer event ends a still-probing job
without its body. The wall-clock timer of Decision 3 stays as a watchdog: it
starts a job only if none was started for `WATCHDOG` = 6 min (this also starts
the first job after a restart). The Home Assistant minimum is raised to 2026.3
(`hacs.json`). Decisions 1, 2 and 4 are unaffected. Additionally, one
`asyncio.Lock` serializes the recent recalculation and the history recalculation
(button); the second waits, it is not skipped.

**Decided by:** human (2026-10-13: parameters as Shady, minimum 2026.3;
2026-10-16: serialization).

## Amendment — 2026-10-16 (TASK-0007a)

**Reason:** Once `sensor.effy_*` is recorded (ADR-017 superseded by TASK-0007b),
two things follow from how the recorder compiles (verified against Home
Assistant 2025.1.4; re-verified on the pinned version in TASK-0007b). First, a
restart: HA queues its catch-up compile of the slots missed during downtime at
recorder start and may re-queue it in chunks, so a job that starts at the next
boundary could insert a slot's row before the compile reaches it. Second, the
hourly long-term row: the recorder rolls an hour up in the same compile call
that inserts the hour's last slot (the one starting at minute 55, compiled at
boundary + 10 s), before the 5-minute event fires. That slot therefore enters
the roll-up with the recorder's own value, and the roll-up's `min`/`max` come
from that one native row, because HA's update of an existing row replaces every
column and Effy's rows carry no `min`/`max`.

**Decision:** (1) The first slot job after a restart is held until the first
recorder event; the watchdog counts from setup before any job and starts one
only after `WATCHDOG` (6 min) without an event. (2) Decision 2 gets one
exception: when the last completed slot is the last of its hour, the slot job
rewrites that hour's long-term row after its recent recalculation, under the
same lock, from the 12 short-term rows read back
(`history.async_rewrite_hourly`; `mean` and `state` as the full history
recalculation writes them, no `min`/`max`). It is written once per hour; a
failure is logged and not retried, and the next history recalculation corrects
the hour. No second event subscription: the recorder's hourly event fires in the
same compile call, before the commit, together with the 5-minute event.

**Decided by:** human (2026-10-16).

## Amendment — 2026-10-30

**Reason:** TASK-0035 (ADR-020 Amendment 2026-10-30). The Amendment 2026-10-16
says a failed hour rewrite is corrected by "the next history recalculation".
Since the history recalculation leaves every hour the recorder has not surely
rolled up to this rewrite, that holds only for hours it writes.

**Decision:** The slot job's hour rewrite is the writer of the open hour's
hourly row after a history recalculation. A job started by the watchdog (no
event proves the compile) works as of 30 s earlier, so the slot it writes was
compiled before its imports are queued. A job started by the event that waited
for the lock serves only the slot of that event.

**Decided by:** Lead Agent (not yet confirmed by the human)
