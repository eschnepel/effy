# ADR-012 – Trapezoidal Energy Redistribution, Derived-Power Sensor, and Recalculated-From Tracking

**Date:** 2026-07-11
**Status:** Accepted — supersedes ADR-009; amends ADR-003 (per-slot `change`
sourcing), ADR-011 (slot-timer recalculation range), and `history.py`'s
write path.

---

## Context

ADR-009's neighbor-percentage smoothing operated on already-aggregated
5-minute `change` values, redistributing *some* of a spurious zero slot's
share from its neighbors as a heuristic correction. It could not
distinguish "the counter genuinely didn't tick this slot" from "the sensor
was offline this slot", and its correction strength was a percentage
guess, not derived from how much real time a jump actually took.

Instead: read the *raw* state-change history for TOTAL_INCREASING/energy
sensors directly (not pre-aggregated statistics), and redistribute each
observed jump across the exact wall-clock time it took to accumulate,
using the trapezoidal rule. This is strictly more precise, handles the
low-resolution-counter case ADR-009 targeted as one instance of a more
general rule, and additionally distinguishes normal reporting gaps from
genuine sensor-offline periods.

Three further decisions came out of implementing this.

---

## Decision 1 — the trapezoidal algorithm and its two distribution modes

`calculation.trapezoidal_slot_contributions(raw_states, slot_minutes,
max_minutes)` (pure, HA-independent, alongside `distribute_loss` — ADR-000
§3) takes an entity's raw, **unfiltered** state history (including
`unavailable`/`unknown` entries — filtering those out would make offline
detection impossible) and, for every consecutive pair of valid numeric
readings (t1, v1) → (t2, v2):

- `delta = max(0, v2 - v1)` — a decrease is a counter reset, matching the
  clamping already used in the live path (`disabled/coordinator_live.py`)
  and the previous statistics-based history path; no negative contribution
  is ever distributed, and (t2, v2) becomes the new baseline.
- **If the entry immediately before (t2, v2) was itself invalid**
  (unavailable/unknown/non-numeric): the sensor was offline for this whole
  gap. `delta` spreads evenly across the *entire* `[t1, t2)` span,
  uncapped.
- **Otherwise** (a direct v1→v2 step, no gap): `delta` spreads evenly
  across at most the last `TRAPEZOID_MAX_MINUTES` (15, not user-configurable
  — a fixed rule, not a tunable heuristic like ADR-009's percentages) minutes
  before t2, i.e. `[max(t1, t2 - 15min), t2)`.

Each 5-minute slot boundary overlapping a jump's distribution window
receives a share proportional to the time overlap; a slot can receive
contributions from more than one jump, summed. The total distributed
always equals the observed delta exactly (conservation, same guarantee
ADR-009 made).

`history.py`'s `_compute_effective_slots` uses this in place of
`statistics_during_period`'s `change` field for every TOTAL_INCREASING /
TOTAL-as-energy sensor; MEASUREMENT / TOTAL-as-power sensors are
unaffected, still sourced from `mean` as before. Both then flow through
the same `distribute_loss` waterfall as before (ADR-001/002/005/008).

## Decision 2 — a new `effy_*_power` derived-power sensor, independent of the waterfall

The trapezoidal result for an entity, converted to a W/kW-equivalent via
`to_power_equivalent` (ADR-008), is exactly the quantity that used to be
discarded once it went into `distribute_loss`. This is now also written as
its own statistic series — `sensor.effy_{slug}_power` — for **every**
energy-family sensor, inputs *and* outputs alike (unlike the existing
"effective" `effy_*` sensor, which only exists for inputs, since only
inputs receive a post-waterfall share). The entity (`EffyDerivedPowerSensor`,
sensor.py) exists purely so statistics have a stable place to attach to,
exactly like the pre-existing `EffySensor` — it receives no live push
while the live path is disabled (`disabled/README.md`).

`_compute_effective_slots` captures this value from the same
`_readings_for_slot` call already made for `distribute_loss`'s inputs —
no extra computation, just capturing the reading before the waterfall
step consumes it.

Written with both short-term and long-term statistics from a full history
recalc (`async_recalculate_history`), and short-term only from the
slot-timer path (`async_recalculate_recent`) — the same
include_long_term=False rule from ADR-011 Decision 2 applies identically
to this series.

## Decision 3 — the slot-timer recalculation range is now dynamic, not fixed at one slot

A single trapezoidal jump can touch more than one slot: up to 3 for a
normal (15-minute-capped) jump, or many more for a genuine offline gap.
ADR-011's `async_recalculate_slot(hass, options, slot_start)` — always
exactly one fixed slot — can no longer guarantee it rewrites everything a
jump affected. It is replaced by `async_recalculate_recent(hass, options,
now)`, which recomputes and rewrites **every** slot touched by *any*
sensor's raw history within `RECENT_RECALC_WINDOW` (4 hours) of `now` —
bounding the query cost of a call that runs every ~5 minutes, while still
comfortably covering both the 15-minute cap and multi-hour offline gaps
(WiFi drop, HA restart). A genuinely longer offline gap is still corrected
by the next full history recalc, whose own window (`max_history_days`) is
far larger. `EffyCoordinator._on_slot_timer` no longer computes a slot
boundary itself — it hands the raw current time to
`async_recalculate_recent` and lets `history.py` determine the affected
range.

A second, smaller, fixed margin (`_RAW_HISTORY_BOUNDARY_MARGIN`, 20
minutes) is fetched before *any* requested `[start, end)` range regardless
of caller — purely so a jump whose end falls right at the range's start
still has its own start (and any preceding offline gap) visible. This is
deliberately separate from, and much smaller than, `RECENT_RECALC_WINDOW`
above or `max_history_days` (the full recalc's own window) — it only
exists to avoid a boundary artifact at the edge of whatever range a caller
already chose, not to control how far back an offline gap can be detected.

### The "recalculated from" sensor

Both recalculation entry points now report the earliest slot they
touched: `async_recalculate_history` always returns its range's own
`start` (a full recalc unconditionally rewrites every slot in range);
`async_recalculate_recent` returns the minimum touched-slot timestamp
across both the effective and derived-power series, or `None` if nothing
was written that cycle (e.g. the recorder hadn't compiled the relevant
slot(s) yet).

`EffyCoordinator` gained a single-value push channel, separate from the
per-entity `_subscribers` registry used for effective-value distributions:
`set_recalculated_from(ts)` / `subscribe_recalculated_from(cb)`. The new
`sensor.effy_recalculated_from` (global per config entry,
`device_class: timestamp`, `EffyRecalculatedFromSensor` in sensor.py)
subscribes to it. `EffyCoordinator._on_slot_timer` calls
`set_recalculated_from` after a successful `async_recalculate_recent`
run; `button.py`'s manual history rewrite calls it directly after
`async_recalculate_history` returns, via
`hass.data[DOMAIN][entry.entry_id]` — both recalculation paths funnel
through the same method so the sensor doesn't need to know which one
fired. Always overwrites (not a running minimum): the sensor reflects
what the *most recent* recalculation touched, not the oldest ever seen. A
run that touches nothing (`None`) leaves the sensor's previous value
untouched rather than clearing it — "nothing needed recalculating" isn't
itself a new recalculation event worth reporting.

This is the mechanism the question in ADR-011 Decision 4 anticipated:
"subsequent integrations" (automations, other statistics consumers) can
watch this single timestamp to know when to re-derive anything built on
top of Effy's output, without needing to diff historical values
themselves.

---

## Consequences

- Energy-family sensors' per-slot values are now sourced from raw state
  history, not pre-aggregated statistics — one additional recorder query
  per energy sensor per recalculation, in exchange for exact,
  physically-grounded redistribution instead of a percentage heuristic.
- `CONF_SMOOTH_LOW_RES_KWH` / `DEFAULT_SMOOTH_LOW_RES_KWH` and
  `smooth_zero_noise` are removed entirely (const.py, config_flow.py,
  calculation.py, and both translation files) — there is no longer an
  opt-in toggle for this; the trapezoidal rule always applies to
  energy-family sensors.
- Every energy-family sensor (input or output) now gets a second
  `effy_*_power` entity/statistic series, in addition to whatever
  "effective" series it already had (inputs only).
- A new global `sensor.effy_recalculated_from` timestamp entity exists per
  config entry, updated by both recalculation paths.
- The slot-timer-driven recalculation's range is dynamic (up to
  `RECENT_RECALC_WINDOW` = 4h look-back) rather than a fixed single slot —
  more rows can be rewritten per cycle than before, bounded by that window.
- Not verified against a real HA instance (`state_changes_during_period`'s
  exact behaviour, timing of recorder statistics compilation relative to
  raw state availability) — see the module WARNING docstring's general
  caveat on `history.py`'s internal-API-adjacent code; the algorithmic
  core (`trapezoidal_slot_contributions`) is fully unit-tested and
  HA-independent, so this risk is isolated to the fetch/write glue.
