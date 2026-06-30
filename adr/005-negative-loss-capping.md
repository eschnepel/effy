# ADR-005 – Capping Negative Loss at Zero

**Date:** 2026-06-30
**Status:** Accepted

---

## Context

In theory `Σ inputs ≥ Σ outputs` always holds (energy conservation).
In practice, sensors report at slightly different timestamps, have different
update rates, and carry calibration offsets. This can cause `Σ outputs > Σ inputs`
momentarily, yielding a negative `total_loss`.

Two options were considered:

- **Distribute negative loss** – effective values would exceed raw input
  values, which is physically implausible and confusing for users.
- **Cap at zero** – if outputs temporarily exceed inputs, no adjustment is
  made and all effective values equal their raw values.

---

## Decision

```python
total_loss = max(0.0, sum_inputs_w - sum_outputs_w)
```

Negative loss is silently clamped to zero. No loss is distributed in such
intervals.

---

## Consequences

- **Pro:** Effective values are always ≤ raw input values – physically
  plausible and easy to reason about.
- **Pro:** No special-case UI or logging is needed for the common case of
  minor measurement noise.
- **Con:** In intervals where the clamp fires, `Σ effective_inputs > Σ outputs`
  (the conservation invariant is temporarily broken). This is acceptable
  because the cause is measurement artefact, not real energy flow.
- **Neutral:** The `total_loss_w` attribute on output sensors exposes the
  pre-cap value (always ≥ 0 after capping), so users can monitor the overall
  loss trend even if individual intervals clamp.
