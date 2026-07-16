# ADR-016 – Push the Last Recalculated Slot's Value as a Live State

**Date:** 2026-07-16
**Status:** Accepted — implements the "additive live push" ADR-011 Decision 4
concluded was possible; supersedes the "unknown"-only push described there
(and in effect since, in `EffyCoordinator.notify_updated` /
`EffySensor._on_updated` and its siblings).

---

## Context

Since ADR-011, every recalculation (the slot timer, or a manual rewrite via
`button.py`) writes corrected *statistics* for the touched `effy_*`
entities, then calls `EffyCoordinator.notify_updated` purely to fire a
`state_changed` event — the push itself always carried `None` ("unknown"),
never a real value, because ADR-011 Decision 4 established that no public
API can backdate a live state into an already-closed slot. That reasoning
is still correct, but it had an unintended side effect: with the live path
disabled, `EffySensor`/`EffyDerivedPowerSensor`/`EffySmoothedSensor`'s
*displayed* state stays "unknown" (`Unbekannt`) indefinitely for anyone
looking at a plain entity/current-value card rather than a history or
statistics-graph card — even though a perfectly good number was just
computed and written to the statistics tables one push away.

The concrete ask: whenever the last slot for a sensor is written, push
that same value as the sensor's live state too. It will land in whichever
slot is currently open — the wrong one — but that's an acceptable
trade-off: it gives a real number to look at instead of "unknown", and the
next recalculation of that now-current slot corrects it in turn, exactly
as every other slot already gets corrected once it closes.

---

## Decision

1. **`history._last_slot_values(*series)`** extracts, for every touched
   statistic_id across the effective/derived-power/smoothed series, the
   `(value, unit)` of the chronologically *last* entry in that entity's
   `slot_values` list — the most recent slot this recalculation run
   actually produced a value for (not necessarily literally "now": a
   sensor recovering from an outage mid-window may have its latest write
   short of `now`, but it's still the best available stand-in for "the
   current value").
2. **`async_recalculate_history`/`async_recalculate_recent`** both gained a
   fourth return value, `last_values: dict[str, tuple[float, str]]`,
   alongside the existing `(written, recalculated_from, touched_entity_ids)`.
3. **`EffyCoordinator.notify_updated`** now takes an optional `last_values`
   mapping and calls each subscriber with `(value, unit)` instead of no
   arguments (`EntityUpdateCallback` changed from `Callable[[], None]` to
   `Callable[[float | None, str | None], None]`). An entity_id present in
   `entity_ids` but absent from `last_values` (shouldn't normally happen —
   both come from the same recalculation call) falls back to
   `(None, None)`, i.e. still "unknown", rather than raising.
4. **`EffySensor`/`EffyDerivedPowerSensor`/`EffySmoothedSensor`'s
   `_on_updated`** now set `_attr_native_value` to the pushed value
   (rounded to 3 decimals, matching `EffySensor._on_distribution`'s
   existing precision) and `_attr_native_unit_of_measurement` to the
   pushed unit, instead of unconditionally writing `None`.
5. Both call sites forward the new value through:
   `EffyCoordinator._async_recalculate_recent_and_report` (slot timer) and
   `EffyRecalculateButton.async_press` (manual rewrite) now unpack the
   4-tuple and pass `last_values` into `notify_updated` alongside
   `touched`.

ADR-011 Decision 4's core finding is unchanged by any of this: there is
still no public API to backdate a live state into an already-closed slot.
This push is an ordinary state change timestamped "now", like any other —
it lands in whatever 5-minute slot is currently open, not the slot it was
actually computed for. The *statistics* write (unchanged by this ADR)
remains the only thing that correctly back-fills the closed slot itself.
What changes here is only what the additive live push, already anticipated
as possible in ADR-011, actually carries: a real number instead of a
hardcoded "unknown".

This is safe specifically because the mis-attribution never accumulates:
the next time the slot timer fires (at most `SLOT_MINUTES` later), the
now-current slot closes and gets its own correct statistics write — and
its own live push in turn, which again overwrites today's provisional
number with a fresh one for the next open slot. Every cycle both corrects
the previous cycle's provisional value in the statistics table and
supplies a new provisional one for the live state, so the live display is
never more than one slot-cycle away from correct, and the statistics
tables (what history/energy-dashboard graphs actually read) are never
wrong at all.

---

## Consequences

- A plain entity/current-value card for `sensor.effy_*` /
  `sensor.effy_*_power` / `sensor.effy_*_smoothed` now shows a real,
  fresh-looking number between recalculations instead of "unknown"
  indefinitely, while the live path itself stays disabled.
- That number is provisionally attributed to whichever slot is open at
  push time, not the slot it was actually computed for, until the next
  recalculation cycle corrects it — a history/statistics-graph card is
  unaffected either way, since those read the recorder's statistics
  tables directly, which this ADR does not change.
- If a recalculation cycle writes nothing for an entity (e.g. the
  recorder hasn't compiled the relevant slot yet, ADR-011 Decision 3, or
  the sensor is genuinely offline), no push happens for it that cycle
  either — its previous live value (correct or provisionally wrong)
  simply persists until the next successful cycle, same as before.
- `EntityUpdateCallback`'s signature change is breaking for any future
  subscriber of `subscribe_updates`; all three current subscribers
  (`EffySensor`, `EffyDerivedPowerSensor`, `EffySmoothedSensor`) were
  updated alongside it.
- No change to what's written to the recorder's statistics tables, to
  `async_recalculate_history`/`async_recalculate_recent`'s recomputation
  logic, or to the "recalculated from" tracking (ADR-012) — this is
  purely an additional, best-effort live-state push layered on top of the
  existing, unchanged statistics write.
