# ADR-002 – Unit Normalisation: W and Wh Treated Identically Within an Interval

**Date:** 2026-06-30
**Status:** Accepted

---

## Context

Input sensors may report in four units:

| Unit | Meaning |
|---|---|
| W | Instantaneous power (MEASUREMENT or TOTAL state class) |
| kW | Instantaneous power, scaled |
| Wh | Energy accumulated since last reset (TOTAL / TOTAL_INCREASING) |
| kWh | Energy accumulated, scaled |

The loss calculation is `Σ inputs − Σ outputs`. For this subtraction to be
physically meaningful, all terms must be in the same unit within a single
time interval.

A conversion factor of `Δt` (hours) exists between W and Wh. For a fixed
5-minute interval `Δt = 5/60 h`. Since `Δt` applies equally to every term,
it cancels in the loss equation:

```
loss_Wh = (Σ inputs_W − Σ outputs_W) × Δt
        = Δt × loss_W
```

The distribution ratios are identical regardless of whether we work in W or Wh.

---

## Decision

- `SensorReading.raw_value` stores the value **as reported by the sensor**,
  without any pre-normalization.
- Normalization (**kW → W**, **kWh → Wh**, ×1 000) happens **once** inside
  `distribute_loss`, which is the single entry point for loss calculation.
- Treat W and Wh as numerically identical within one interval (no `× Δt`
  conversion, per the derivation above).
- Output sensor values are converted **back to the source sensor's original
  unit** via `effective_in_original_unit` before being written to
  `native_value` (live sensor) or `StatisticData.mean` (history).

---

## Consequences

- **Pro:** No time-interval arithmetic is needed anywhere in the codebase.
- **Pro:** Output units match input units exactly – a kW source produces a kW
  output, a Wh source produces a Wh output.
- **Pro:** Normalization is performed exactly once, eliminating the risk of
  double-scaling (e.g. kW ×1000 in the reader and again in the calculator).
- **Con:** Mixing W sensors and Wh sensors in the same config is technically
  an apples-to-oranges comparison, but the mathematical identity holds as
  shown above, so the result is correct.
- **Neutral:** kW/kWh sensors are rare in practice for individual PV strings
  but are supported transparently.
