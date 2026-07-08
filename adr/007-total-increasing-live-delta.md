# ADR-007 – Live Accumulation and Wh→W Conversion for Energy Sensors

**Date:** 2026-07-01
**Status:** Accepted — supersedes initial revision (slot-anchor approach)
**Amended:** 2026-07-01 (LiveReading accumulator; time-based Wh→W conversion)

**See also:** ADR-010 (2026-07-07) amends `LiveReading.reset()` for the
ENERGY family (idle entities no longer collapse `reset_ts`/`updated_ts`
together) and corrects the now-outdated "`reset` zeroes `avg`" statement in
the POWER section below (see ADR-006's 2026-07-03 amendment instead).

---

## Context

`TOTAL_INCREASING` (and `TOTAL`-as-energy) sensors expose a monotonically
growing absolute counter total in Wh or kWh.  `MEASUREMENT` and
`TOTAL`-as-power sensors report instantaneous power in W or kW.

Both must be fed to `distribute_loss` on the same basis.  Two sub-problems
need solving:

1. **Counter → delta:** the absolute counter total must be converted to an
   *energy increment* for the current interval; the absolute total is
   physically meaningless in `distribute_loss`.
2. **Wh → W:** the energy delta (Wh) must be converted to a W-equivalent
   average power so that all sensors are comparable in `distribute_loss`.
   Failure to do this produces systematically wrong loss shares (see ADR-008).

The history path (ADR-003) solves both correctly:
- `change` from `statistics_during_period` is the per-5-minute delta → solves (1).
- Dividing by `5/60 h` converts Wh to W-equivalent → solves (2).

The live path requires an analogous mechanism without the fixed 5-minute
slot grid.

---

## Initial approach (slot-anchor, now superseded)

The first revision of this ADR introduced a `_slot_anchor` dict mapping each
`TOTAL_INCREASING` entity to `(slot_start_dt, absolute_at_slot_start)`.  On
every event the coordinator computed `delta = current − anchor`, rolling the
anchor at each 5-minute wall-clock boundary.

**Problem 1 – No Wh→W conversion.**  The slot-delta Wh value was passed
directly to `distribute_loss`.  For a `MEASUREMENT` W sensor with value 1 000
and a `TOTAL_INCREASING` Wh sensor with a 5-minute delta of 83.3 Wh (the
correct energy equivalent), `distribute_loss` saw `1 000 + 83.3` instead of
`1 000 + 1 000`.  Sensor B was assigned ~12× less loss than its fair share.

**Problem 2 – Fixed slot boundary, not actual elapsed time.**  The slot
anchor is reset on the wall-clock 5-minute boundary.  If recalculation fires
immediately before a boundary (window almost full) and again immediately after
(window almost empty), the Wh value seen in the second recalculation is tiny
but it is divided by the same fixed `5/60 h` as if the full window had
elapsed.  The live value is thus only accurate in the middle of a slot.

Both problems are resolved by the `LiveReading` accumulator described below.

---

## Requirements

1. All sensors — regardless of state class or unit — must enter
   `distribute_loss` in W-equivalent so that loss shares are physically
   correct.
2. The conversion must use the *actual elapsed time* of the accumulation
   window, not a hardcoded slot width, so that the live value is valid at
   every point in time, not just at slot midpoints.
3. The accumulation window must roll forward after each recalculation so
   that no energy is double-counted across consecutive calls.
4. Counter resets (new absolute < previous absolute) must not produce
   negative deltas.
5. Multiple state-change events between two recalculations must be handled
   correctly: the accumulator must absorb all of them.

---

## Decision – `LiveReading` accumulator

Each watched entity has a `LiveReading` instance in the coordinator cache.
It is mutated in-place on every event and reset after each recalculation.

### State-class family

```python
def _state_class_family(state_class, unit) -> "power" | "energy":
    if state_class == TOTAL_INCREASING:          return "energy"
    if state_class == TOTAL and unit in Wh/kWh:  return "energy"
    return "power"   # MEASUREMENT or TOTAL-as-power
```

### ENERGY family (`TOTAL_INCREASING`, `TOTAL` with Wh/kWh)

`LiveReading` tracks:

| Field | Meaning |
|---|---|
| `raw_start` | Absolute counter at `reset_ts` (set once per window; not changed by events) |
| `raw_last` | Absolute counter from the most recent event |
| `reset_ts` | Timestamp of the last window reset |
| `updated_ts` | Timestamp of the most recent event |

On each `update_energy(absolute, event_ts)`:
- First call (not seeded): `raw_start = raw_last = absolute`, timestamps set.
- Subsequent calls: `raw_last = absolute`, `updated_ts = event_ts`.
- Counter reset (`absolute < raw_last`): `raw_start` moves to `absolute`
  (clamp), so the remainder of the window starts cleanly from the new value.

`to_sensor_reading(now)` converts to W-equivalent:

```
elapsed_h = (updated_ts − reset_ts).total_seconds() / 3600
delta_Wh  = max(0, raw_last − raw_start)
W_equiv   = delta_Wh / elapsed_h          # Wh/h = W
```

If `elapsed_h == 0` (only one event has fired, window has no duration yet)
the result is 0 W — a conservative fallback that avoids division by zero.

> **Amendment (see ADR-010, 2026-07-07):** this fallback is also what ran on
> *every* cycle where some other entity's event triggered recalculation
> before this entity reported again — which, for entities updating less
> often than whatever else is triggering cycles, was most cycles. ADR-010
> fixes this at the `reset()` level (see below), not by changing this
> formula or this fallback.

After recalculation, `reset` rolls the window:
```
reset_ts  = updated_ts      # no gap between windows
raw_start = raw_last        # next window starts from last known absolute
```

> **Amendment (see ADR-010, 2026-07-07):** this roll-forward is now
> conditional on whether the entity itself reported an event since its last
> reset. If it did not (idle), `reset_ts` stays put and only `updated_ts`
> advances to the real current time — see ADR-010 for the full mechanism
> and why an unconditional roll-forward caused live values to flatline at
> whatever `elapsed_h == 0` produces (0 W) on almost every cycle for
> entities that update less often than whatever else triggers
> recalculation.

### POWER family (`MEASUREMENT`, `TOTAL` with W/kW)

`LiveReading` tracks a **time-weighted running average**:

| Field | Meaning |
|---|---|
| `avg` | Weighted average of all readings in the current window |
| `reset_ts` | Timestamp of the last window reset |
| `updated_ts` | Timestamp of the most recent event |

On each `update_power(value, event_ts)`:

```
old_elapsed = (updated_ts − reset_ts).total_seconds()
new_elapsed = (event_ts   − updated_ts).total_seconds()
total       = old_elapsed + new_elapsed

if total > 0:
    avg = (avg × old_elapsed + value × new_elapsed) / total
else:
    avg = value   # first event at t == reset_ts
```

`to_sensor_reading` returns `avg` in the original unit (W or kW); no time
conversion is needed because the unit is already instantaneous power.

After recalculation, `reset` zeroes `avg` and moves `reset_ts = updated_ts`.

> **Correction (see ADR-006, amendment 2026-07-03):** `avg` is *not* zeroed
> here — it is carried forward unchanged across the reset. Zeroing it caused
> live values to read ~0 W on almost every cycle for any sensor that didn't
> happen to fire within the same debounce window as whichever sensor
> triggered the recalculation.

---

## Comparison with the slot-anchor approach

| Criterion | Slot-anchor (superseded) | LiveReading (current) |
|---|---|---|
| Wh→W conversion | ✗ not done | ✓ delta / elapsed_h |
| Elapsed time basis | Fixed 5 min (wall clock) | Actual window duration |
| Handles bursts before recalc | Partly (last delta only) | ✓ all events accumulated |
| Counter reset | Clamped to 0 | Clamped (raw_start moves) |
| Multiple resets in one window | Not handled | Handled (each clamp moves anchor) |
| Extra data structures | `_slot_anchor` dict | `LiveReading` per entity (in-place) |
| POWER sensors | Passed through as-is | Time-weighted average |

---

## History path alignment

The history path uses `_to_power_equivalent(change_Wh, unit, slot_minutes)`:

```
W_equiv = change_Wh / (slot_minutes / 60)
```

For the standard 5-minute slot this is `change_Wh × 12`.  The live path uses
actual elapsed time; over a full 5-minute window the two are numerically
identical.  Mid-window the live value reflects partial accumulation, which is
correct — it represents the average power produced *so far* in the interval.

The `_stat_field_for` function in `history.py` now treats `TOTAL` with Wh/kWh
the same as `TOTAL_INCREASING`, reading `change` instead of `mean`.  This
mirrors the live path's `_state_class_family` mapping.

---

## Consequences

- **Pro:** All sensors enter `distribute_loss` in W-equivalent; loss shares
  are physically correct for any combination of state classes and units
  within the same unit family (ADR-008).
- **Pro:** The live value is valid at every instant, not only at slot
  midpoints.
- **Pro:** Multiple events between two recalculations are fully accumulated;
  no event is silently dropped.
- **Pro:** Counter resets within a window are handled gracefully.
- **Con:** The first recalculation after HA startup produces 0 W for energy
  sensors (no elapsed time yet in the first window).  This is the correct
  conservative behaviour — the coordinator has no information about what
  happened before it started.
- **Con:** POWER sensors: the very first event at `t == reset_ts` uses a
  zero-duration window and seeds `avg = value` directly (the else-branch in
  `update_power`).  This is correct: there is no prior interval to weight.
- **Neutral:** `_slot_anchor`, `_delta_reading`, and `_is_total_increasing`
  are removed from `EffyCoordinator`.  `get_current_value` in
  `sensor_utils.py` is no longer called in the event hot-path (only during
  initial setup seeding).
