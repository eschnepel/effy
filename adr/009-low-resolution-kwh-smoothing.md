# ADR-009 – Smoothing Low-Resolution kWh Sensor Noise

**Date:** 2026-07-03
**Status:** Accepted

---

## Context

Some energy meters — observed in the field with certain BMS (battery
management system) sensors — only report their cumulative kWh counter with
2 decimal digits of precision. Over a short interval (a 5-minute history
slot, or a single live debounce window) the true energy delta is frequently
smaller than that resolution, so the counter simply doesn't tick between two
consecutive readings. The computed delta for that interval comes out as
exactly 0 Wh/kWh even though real, non-zero power was actually flowing.

This is not the same failure mode as the live-accumulator bugs fixed
earlier (2026-07-03 amendments to ADR-006/007): those made *every* sensor
read 0 on most cycles regardless of data quality. This is a genuine
quantisation artifact in the *source* sensor's own data, which shows up as
frequent, spurious 0 W readings for that one sensor specifically, and
distorts `distribute_loss`'s waterfall for the slots where it happens (a
sensor reading exactly 0 doesn't participate in the active-sensor set at
all — see `distribute_loss`'s `active = {... if v > 0.0}` filter — so its
real share of input power is silently reassigned to other sensors for that
slot).

## Decision

Add an opt-in config option, `smooth_low_res_kwh` (default `False`), that
runs each energy-family sensor's raw per-slot `change` series through
`smooth_zero_noise` (`calculation.py`) before any Wh/kWh → W/kW conversion:

- **Round 1:** every slot whose *original* raw value is exactly `0.0` takes
  25% of each direct neighbor's value (previous and next slot) and adds it
  to itself; each donor neighbor is reduced by the same amount. A slot at
  either end of the series only has one neighbor to draw from. This alone
  flattens a strictly alternating pattern like `[10, 0, 10, 0, ...]` (Wh) —
  where no zero slot is adjacent to another zero slot — to a constant
  `[.., 5, 5, 5, ..]` (aside from the two series ends, which only had one
  neighbor to draw from and end up slightly higher).
- **Round 2:** targets only slots that belong to a run of *two or more*
  consecutive originally-zero slots ("larger gaps"), using the
  round-1-adjusted values as input. It steals 10% from each direct (±1)
  neighbor and 5% from each ±2 neighbor. A single round only reaches one
  slot in each direction, so a run of 2+ zero slots isn't fully smoothed by
  round 1 alone — each zero slot in the run stole 0% from its still-zero
  neighbor in round 1. Isolated (run-length-1) zeros are deliberately
  excluded from round 2: round 1 already gives them their ideal flat
  result, and re-running a steal on an already-flat value would just
  reintroduce boundary-driven unevenness for no benefit.

The total sum of the series is preserved exactly by both rounds — this is
pure redistribution, never creation or destruction of energy.

### Why this must run before Wh/kWh → W conversion

`smooth_zero_noise` operates on genuine raw energy amounts (Wh or kWh).
Running it after conversion to W would make the 25%-steal amounts a
function of the slot duration (which can vary, e.g. at the edges of the
requested history window) rather than of the actual meter readings,
breaking the "sum is preserved" invariant and coupling an otherwise
duration-independent smoothing step to an unrelated parameter.

### Why this only targets the ENERGY family

Only sensors resolved to the `change` statistics field — `TOTAL_INCREASING`,
or `TOTAL` with a Wh/kWh unit, see `_stat_field_for` — are candidates.
`MEASUREMENT` / TOTAL-as-power sensors report instantaneous W/kW directly
and don't exhibit this counter-quantisation failure mode; smoothing them
the same way would just be an arbitrary low-pass filter on genuinely
noisy-but-real power readings, which is a different problem this ADR does
not address.

### Why this is opt-in and defaults to `False`

The smoothing measurably changes historical output for the slots it
touches. Sensors that already report at full resolution (most non-BMS
energy meters) have no zero-valued slots to smooth and are unaffected
either way, but making this the default for all users would mean silently
altering results for setups Effy can't verify need it. Users who observe
frequent 0 W noise from a specific sensor with a known low-resolution
counter can opt in explicitly.

### Scope: historical path only

`smooth_zero_noise` fundamentally needs both a "previous" and a "next"
neighbor value for each slot it adjusts. The history recalculation
(`history.py`) already processes a fixed, ordered array of past slots per
sensor, so both neighbors are always available. The live coordinator
(`coordinator.py`) processes one accumulation window at a time as events
arrive in real time — the "next" slot's value doesn't exist yet when the
current slot is being finalized, so this smoothing cannot be applied
there without deliberately delaying every live reading by a full slot to
wait for its future neighbor. That is a materially different (and
significantly more invasive) design than a stateless per-recalculation
pass, and is not implemented as part of this ADR. Live 0 W flicker from a
low-resolution sensor is therefore only corrected retroactively, the next
time history is recalculated (manually via the diagnostic button, or
however recalculation is otherwise scheduled).

## Consequences

- **Pro:** Directly addresses the observed BMS low-resolution counter
  quantisation noise, without touching sensors that don't exhibit it.
- **Pro:** Pure, dependency-free logic in `calculation.py`, consistent with
  ADR-000 §3's zero-mock testing philosophy.
- **Con:** Only available for the history recalculation path; live sensor
  values from a low-resolution energy meter will still show 0 W noise
  in real time until the next history recalculation.
- **Neutral:** Two fixed rounds is a bounded heuristic, not a full
  convergence algorithm — very long runs of consecutive zero slots (more
  than a couple) will still show some residual unevenness after
  smoothing, tapering off toward the middle of the run.
