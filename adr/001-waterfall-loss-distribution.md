# ADR-001 – Waterfall Model for Absolute Loss Distribution

**Date:** 2026-06-30
**Status:** Accepted

---

## Context

A PV + BMS system has multiple input sources (PV strings, battery import,
grid import) and multiple output sinks (battery export, grid export).
The difference between the sum of inputs and the sum of outputs is a real
conversion/wiring/measurement loss that must be attributed to the input side.

Two distribution strategies were considered:

- **Relative (proportional):** each active sensor absorbs `value / Σ active × total_loss`.
- **Absolute (equal share):** each active sensor absorbs `total_loss / count_active`.

The requirement is an **absolutely equal** distribution: every active source
should carry the same absolute watt burden, not the same percentage.

---

## Decision

Use an **absolute waterfall model**:

1. Sort active (non-zero) input sensors **ascending by value**.
2. Compute `equal_share = remaining_loss / count_remaining`.
3. For each sensor (smallest first):
   - If `sensor_value ≥ equal_share` → assign `equal_share`, continue.
   - If `sensor_value < equal_share` → assign `sensor_value` (sensor reaches
     zero), redistribute the remainder equally over the remaining sensors.

This produces the most even possible absolute distribution given the
constraint that no effective value may go below zero.

---

## Consequences

- **Pro:** The model is transparent and deterministic.
- **Pro:** The invariant `Σ effective_inputs = Σ outputs` always holds.
- **Pro:** Sensors with very small readings naturally absorb less and reach
  zero rather than going negative.
- **Con:** The smallest sensors disproportionately "lose" their contribution
  in overflow scenarios; this is physically reasonable (a 5 W source cannot
  absorb a 10 W share).
- **Neutral:** A floating-point residual guard absorbs rounding errors onto
  the largest active sensor.
