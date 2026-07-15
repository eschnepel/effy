# ADR-013 – Late-Bound Derived Sensors, Gap-Smoothing, Dashboard-Refresh Push, and Idle-Sensor Zero-Fill

**Date:** 2026-07-13
**Status:** Accepted — amends ADR-006 (`EffyCoordinator` push channels),
ADR-011 (`RECENT_RECALC_WINDOW`), ADR-012 (`trapezoidal_slot_contributions`,
derived-power sensor, recalculated-from tracking), and `sensor.py`'s /
`history.py`'s entity-creation and recalculation paths.

---

## Context

Five separate issues came up in the same working session, all touching
the derived-sensor / recalculation machinery ADR-012 introduced:

1. `sensor.effy_*_power` was sometimes completely missing from the
   integration page after a restart — not just lacking data.
2. Power-family (MEASUREMENT/TOTAL-as-power) *input* sensors occasionally
   have short reporting gaps; nothing bridged them.
3. `EffySensor`/`EffyDerivedPowerSensor`/(new) `EffySmoothedSensor` never
   receive a live push (by design, ADR-011 Decision 4), so a
   recalculation that rewrites their statistics gives dashboard cards
   keyed on `state_changed` (e.g. history/statistics graph cards) no
   signal to refetch.
4. A genuinely idle TOTAL_INCREASING/energy sensor (an empty battery's 0
   discharge, several zero-import days) produced *no* statistic at all
   for the idle stretch, rather than an explicit 0 — `trapezoidal_slot_contributions`
   skipped zero-delta transitions outright, and an input with no new
   reading at all had no second point to form a transition from in the
   first place.
5. `sensor.effy_recalculated_from` effectively always read "~4 hours
   ago", regardless of how much had actually changed — because
   `RECENT_RECALC_WINDOW` was 4 hours specifically so a single blanket
   fetch would also catch multi-hour offline gaps (ADR-011/012), and
   `async_recalculate_recent` reported the earliest slot timestamp across
   *everything* it touched that run, not just what was new. Item 4's fix,
   naively applied, would have made this worse: zero-filling the entire
   4-hour window every cycle means practically the whole window is
   "touched" every single time.

---

## Decision 1 — derived-sensor creation no longer races the source integration's own startup

`EffyDerivedPowerSensor` (and now `EffySmoothedSensor`, Decision 2) is only
created for a source sensor once its `state_class`/`unit` are known
(`is_energy_family` / `get_sensor_meta`, both reading `hass.states.get(entity_id)`).
That state only exists once the *source's own* integration has finished
loading and reported a first value. Effy cannot declare a static
`after_dependencies` on that integration in `manifest.json` — the source
is an arbitrary, user-chosen entity, only known at config time, not at
manifest-authoring time — so at HA startup `sensor.py`'s own platform
setup frequently ran *before* that source state existed. When it did,
`hass.states.get(entity_id)` returned `None`, `is_energy_family(None, "W")`
was `False`, and the derived entity was silently never created for the
rest of that HA session — not just left without data, entirely absent
from the integration page.

`EffySensor` never had this problem: it's created unconditionally for
every configured input, with no such classification gate.

**Fix:** `async_setup_entry` now splits configured sensors into those
whose state is already known (classified and built immediately via the
shared `_build_derived_entities` helper) and those that aren't yet
(`pending_ids`). For the latter, `_add_late_derived_entities` registers a
one-shot `async_track_state_change_event` listener across exactly those
`pending_ids`; the first time each one reports *any* state (even
`unavailable`/`unknown` — the point is just that the entity now exists),
`_build_derived_entities` runs for it and `async_add_entities` is called
again to add whatever derived entity applies. The listener removes
itself once every pending id has been resolved
(`entry.async_on_unload(unsub)`).

## Decision 2 — gap-interpolated `effy_*_smoothed` sensor for power-family *input* sensors

Unlike energy-family sensors (whose raw counter history is redistributed
via the trapezoidal rule, ADR-012), a MEASUREMENT/TOTAL-as-power *input*
sensor's compiled 5-minute `mean` occasionally has a short gap — a slot
the recorder simply never compiled a reading for (a connectivity blip, a
slow-polling source). Left alone, this is a genuine missing statistic,
and it also affects `distribute_loss`, since a slot with no reading for
one input is a slot that input contributes nothing to (rather than its
true, merely-un-recorded, value).

**Fix:** `calculation.interpolate_slot_gaps(slot_values, slot_minutes,
max_gap_slots=INTERPOLATION_MAX_GAP_SLOTS)` linearly interpolates a gap of
up to `INTERPOLATION_MAX_GAP_SLOTS` (2) consecutive missing slots between
the two known readings on either side; a longer gap is left alone rather
than extrapolated across. `_compute_effective_slots` merges the
interpolated values straight into the same per-slot series `distribute_loss`
consumes (`indexed[eid]`, via `setdefault` so an actual recorder row is
never overwritten) — the waterfall sees the smoothed series, not the
gappy raw one — and separately captures the same interpolated values as
their own `effy_*_smoothed` statistic (`EffySmoothedSensor`,
`sensor.effy_{slug}_smoothed`), so the smoothing itself is visible rather
than an invisible internal correction. Scope is deliberately narrow: input
sensors only, power-family only (energy-family sensors have their own,
different, mechanism — the trapezoidal rule already handles their gaps).
`EffySmoothedSensor` is created via the same `_build_derived_entities` /
late-binding path as `EffyDerivedPowerSensor` (Decision 1) — it needed the
same fix, not a second one.

## Decision 3 — a "push unknown" channel so dashboard cards notice a recalculation happened

`EffySensor`/`EffyDerivedPowerSensor`/`EffySmoothedSensor` never receive a
live numeric push while the live path is disabled (ADR-011 Decision 4) —
their statistics are written directly to the recorder by `history.py`.
This means no `state_changed` event ever fires for them, which is a
problem specifically for dashboard cards that only refresh their history
view on that event (e.g. history/statistics graph cards): such a card
could keep showing stale data indefinitely, with no way to know a
recalculation had just rewritten its underlying statistics.

**Fix:** `EffyCoordinator` gained a second push registry, independent of
the existing (currently dormant) live-distribution one:
`subscribe_updates(entity_id, cb)` / `notify_updated(entity_ids)`, keyed
by each derived entity's *own* entity_id. `async_recalculate_history` and
`async_recalculate_recent` both now return a third value,
`touched_entity_ids` — every `effy_*` entity_id that got at least one
slot actually written that run. The slot timer
(`_async_recalculate_recent_and_report`) and the manual recalculation
button (`button.py`) both call `coordinator.notify_updated(touched)`
afterwards. Each subscribed entity's callback
(`EffySensor`/`EffyDerivedPowerSensor`/`EffySmoothedSensor._on_updated`)
sets its own `_attr_native_value = None` and calls `async_write_ha_state()`
— i.e. it pushes **`unknown`**, never the real computed value. This isn't
a compromise: per ADR-011 Decision 4, there is no public API to backdate a
live entity state into an already-closed slot, so there is no correct
*value* to push at all — this exists purely to make a `state_changed`
event fire, and only for entities actually touched this run, not every
configured sensor.

## Decision 4 — `trapezoidal_slot_contributions` writes explicit 0 for a genuinely idle stretch

Two related gaps in the original (ADR-012) algorithm both silently
produced *no* contribution — not a 0 — for a stretch where the answer
should have been exactly 0:

- A transition with `delta == 0.0` (the counter reported the same value
  twice) was skipped outright (`continue`), regardless of whether the gap
  was 5 minutes or 5 days. A battery sitting at 0 discharge, or several
  zero-grid-import days, showed as "no statistics found" instead of 0.
- A normal (non-offline), `delta > 0` transition further apart than
  `TRAPEZOID_MAX_MINUTES` had its distribution capped to the last 15
  minutes before the new reading — correctly — but the prefix before that
  window (during which the counter was still sitting at its old value,
  i.e. also genuinely contributing 0) was left with no entry at all.
- An input with fewer than 2 valid readings in the queried range produced
  nothing, even when the *reason* was simply "no new reading has arrived
  yet" (as opposed to "this is a brand new/currently-offline sensor") —
  physically indistinguishable from a `delta == 0.0` transition, but with
  no second reading to even form one.

**Fix — one unified rule, applied per transition (t1, v1) → (t2, v2):**

- **`was_offline` OR `delta == 0.0`** → the distribution window is the
  *entire*, uncapped `[t1, t2)`. For the offline case this is unchanged
  from ADR-012 (spread the unknown-shape consumption evenly across the
  whole outage). For the zero-delta case, the window's width doesn't
  actually change the *value* (the rate is 0 either way) — using the full
  span is what produces an explicit `0.0` entry for every slot in the
  gap, however long, instead of nothing.
- **Otherwise** (a direct step, positive delta, no offline) → capped to
  `[max(t1, t2 - max_minutes), t2)` as before, but the prefix this leaves
  uncovered (`[t1, window_start)`, whenever the gap exceeds
  `max_minutes`) is now explicitly filled with `0.0` via a new
  `_fill_zero_slots` helper (using `setdefault`, so it never overwrites a
  slot that legitimately already has a nonzero contribution from some
  other transition).
- **A new `now: datetime | None` parameter**: if the sensor's last known
  reading is still valid (not currently unavailable) and predates `now`,
  a final synthetic zero-delta transition `(last_reading → now)` is
  considered as well, using the exact same rule above. This is what lets
  a sensor that simply hasn't reported anything new *yet* still produce
  explicit 0 entries for the slots since its last reading — without it,
  such a sensor would have fewer than 2 readings in range and produce
  nothing at all. `_compute_effective_slots` passes `now=end` (the
  recalculation's own upper bound, "now" in both the recent and full
  history-recalc paths).

Because the *same* algorithm, with the *same* `now=end` argument, now
runs identically whether it's `async_recalculate_recent` (small window,
every ~5 minutes) or `async_recalculate_history` (the full
`max_history_days` range, on demand) computing it, both paths produce the
same result for the same underlying raw data — an idle stretch is
zero-filled the same way regardless of which recalculation found it, and
a real jump's capped/prefix-zero-filled split is identical either way.

## Decision 5 — `RECENT_RECALC_WINDOW` shrinks from 4 hours to ~20 minutes, with a targeted offline-anchor lookback replacing its old role

`RECENT_RECALC_WINDOW` was 4 hours specifically so one blanket fetch would
also comfortably contain a multi-hour offline gap (ADR-011/012). This had
two costs: every ~5-minute cycle re-fetched and reprocessed a 4-hour raw
history window for every configured sensor (almost all of which, in the
common case, produces the exact same result as the previous cycle), and
`async_recalculate_recent`'s reported `earliest` touched-slot timestamp
was essentially always ≈ `now − 4h`, since real data almost always exists
somewhere near the start of that window — regardless of whether anything
in it had actually changed. Decision 4's zero-fill, applied against a
4-hour window, would have made this strictly worse: with every slot in
the window now getting an explicit value (real or zero-filled), the
"touched" set would have become the *entire* window on every single
cycle, permanently pinning `recalculated_from` at "~4 hours ago".

**Fix:** `RECENT_RECALC_WINDOW` shrinks to `TRAPEZOID_MAX_MINUTES + 5`
(the same value as `_RAW_HISTORY_BOUNDARY_MARGIN`, ~20 minutes) — just
enough to comfortably cover the trapezoidal cap plus a missed timer tick
or two. This window is no longer responsible for containing a whole
offline gap; instead, `_compute_effective_slots` performs a single,
targeted lookback when needed: after fetching each energy-family sensor's
raw states for the (now small) window, if the chronologically-*first*
fetched entry is itself invalid (unavailable/unknown/non-numeric) — i.e.
the window happens to start mid-outage, so there's no valid reading
anywhere in it to anchor a redistribution against once the sensor comes
back online — `_fetch_last_valid_state_before(hass, entity_id, before,
search_days=_OFFLINE_ANCHOR_SEARCH_DAYS)` runs one descending,
`include_start_time_state=False` query to find the real pre-outage
baseline, however far back it is (bounded only by
`_OFFLINE_ANCHOR_SEARCH_DAYS`, defaulted to the same reach as a full
history recalc). If found, it's prepended to the fetched raw states
before handing them to `trapezoidal_slot_contributions`. This lookback is
a single targeted query, run only when this specific pattern is detected
— not a per-cycle cost for every sensor the way widening the whole window
would be.

This has no effect on `async_recalculate_history` (the full history
recalc) beyond gaining the same lookback for consistency — its own
`raw_history_start` already comfortably spans the entire configured
`max_history_days`, so the "window starts mid-outage" case is already rare
there; a genuinely still-uncovered case (the outage started before
`max_history_days` even began) is accepted as an edge case with no
baseline to redistribute against, same as before.

`recalculated_from` is unchanged in *how* it's computed (still the
earliest slot timestamp among everything `_compute_effective_slots`
touched this run) — only in what that now means in practice: normally a
handful of minutes, occasionally further back on the rare cycle where the
offline-anchor lookback actually fires and finds a baseline from further
away, and — as an accepted limitation — a run touching *only* a genuinely
long-idle sensor (zero-filled by Decision 4, but with a baseline reading
found via this lookback) can still report `earliest` as far back as that
baseline, since "touched" doesn't distinguish a real value from a
zero-fill. This was considered and accepted rather than adding a
real-vs-fill distinction: the small window bounds how often this can
happen to the rare offline-recovery case, rather than every cycle.

---

## Consequences

- `sensor.effy_*_power` and (new) `sensor.effy_*_smoothed` reliably appear
  on the integration page even when their source's owning integration
  loads after Effy's own platform setup — previously a startup-order race
  that silently, permanently dropped the entity for that HA session.
- Power-family input sensors with short (≤ 2 slot) reporting gaps get a
  smoothed, gap-free `effy_*_smoothed` statistic, and the waterfall
  calculation itself uses the smoothed series rather than the raw gappy
  one.
- Dashboard cards that key off `state_changed` (history/statistics graph
  cards) now reliably refresh shortly after a recalculation, via a
  content-free `unknown` push — this is a UX fix only; it changes no
  computed value and writes no statistic itself.
- A genuinely idle energy-family sensor's statistic reads as an explicit
  0 (an empty battery's 0 discharge, several zero-import days, …) instead
  of appearing as missing data — and the incremental (slot-timer) and full
  (button/service) recalculations now produce identical results for the
  same underlying raw data, since both call the same, now-unified,
  `trapezoidal_slot_contributions` with the same `now` semantics.
- `RECENT_RECALC_WINDOW` shrinking to ~20 minutes reduces the raw-history
  query volume of the slot-timer-driven recalculation by roughly 12x per
  cycle (4h → ~20min) for the common case, at the cost of one extra
  targeted query per energy-family sensor on the rare cycle where a
  sensor's return-from-offline is first observed.
- `sensor.effy_recalculated_from` now normally reads within a few minutes
  of "now" rather than reading ~4 hours back on every single cycle,
  making it meaningful again as a "how far back might something have just
  changed" signal for anything built on top of Effy's output — with the
  accepted limitation noted in Decision 5 for the rare
  zero-fill-only-via-lookback case.
