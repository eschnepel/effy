# ADR-004 – Overwrite (Not Append) for History Statistics

**Date:** 2026-06-30
**Status:** Accepted

---

## Context

When the *Re-calculate History* button is pressed, Effy must decide whether
to **overwrite** existing `effy_*` statistics or only **fill in missing
slots**.

---

## Decision

Always **overwrite** existing statistics via `async_add_external_statistics`.
HA's recorder replaces rows with the same `statistic_id` + `start` timestamp.

---

## Rationale

The primary use cases for history recalculation are:

1. **First installation** – the BMS has been running for days/weeks before
   Effy was installed. There are no `effy_*` statistics yet; overwrite and
   append are equivalent.
2. **Configuration change** – the user adds or removes a sensor. The previous
   effective values were computed with a different set of inputs/outputs and
   are therefore wrong. They must be replaced, not preserved.
3. **Bug fix / algorithm change** – correcting past values requires
   overwriting.

An append-only strategy would leave stale rows from a previous configuration,
producing incorrect energy totals in dashboards.

---

## Consequences

- **Pro:** History is always internally consistent with the current
  configuration.
- **Pro:** No logic is needed to detect and skip already-correct slots.
- **Con:** A full rewrite of up to 28 days × 288 slots/day = 8 064 rows per
  sensor is triggered on every button press. This is a one-shot operation
  with negligible performance impact on typical hardware.
