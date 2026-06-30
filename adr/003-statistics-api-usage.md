# ADR-003 – Statistics API Usage for History Recalculation

**Date:** 2026-06-30
**Status:** Accepted

---

## Context

Home Assistant's recorder stores long-term statistics at 5-minute resolution.
For history recalculation Effy must read these statistics for all configured
sensors and write corrected values for its own `effy_*` sensors.

Several design questions arose:

1. **Which statistic field to read** for each state class?
2. **How many API calls** to use for fetching?
3. **Which fields to write** for the output statistics?

---

## Decision

### Reading

Use `statistics_during_period` with `types={"mean", "change"}` in a **single
call** for all sensors:

- `TOTAL_INCREASING` sensors → read `change` (HA computes the per-interval
  delta internally, including counter-reset handling). No manual
  `sum[t] − sum[t−1]` bookkeeping is needed and there is no risk of negative
  deltas from sensor resets leaking into the calculation.
- `TOTAL` / `MEASUREMENT` sensors → read `mean` (already an instantaneous
  rate or average).

Requesting both fields in one call is simpler and cheaper than splitting
sensors into two groups and issuing two separate requests.

### Writing

Every `effy_*` statistic is written with `StatisticData(start=ts, mean=val)`
– **mean only, no state field**.

| Source state class | Field read | `StatisticData` written |
|---|---|---|
| `TOTAL_INCREASING` | `change` | `mean=val` |
| `TOTAL` | `mean` | `mean=val` |
| `MEASUREMENT` | `mean` | `mean=val` |

`state` is intentionally omitted: it represents the live cumulative sensor
reading at the end of the interval, which is not available during history
recalculation without re-reading the raw `states` table. `mean` alone is
sufficient for all HA dashboard and Energy use-cases.

`TOTAL_INCREASING` is deliberately **downgraded to TOTAL** on output: the
effective value is already an interval delta (Wh per 5 min), which is
precisely what `TOTAL.mean` represents. Building a running `sum` would
require managing counter resets and adds complexity without benefit.

All writes use `async_add_external_statistics`, which **overwrites** existing
rows for the same `statistic_id` + timestamp. This is intentional (see
ADR-004).

---

## Consequences

- **Pro:** Counter-reset handling is delegated to HA's own statistics engine
  (on the read side via `change`).
- **Pro:** A single `statistics_during_period` call is simpler and has lower
  overhead than per-state-class calls.
- **Pro:** The write path is fully uniform – one `StatisticData(mean=val)` for
  every source state class, no branching.
- **Con:** Both `mean` and `change` are always fetched for every sensor, even
  though each sensor only uses one field. The unused field is a small amount
  of extra data in the result dict.
