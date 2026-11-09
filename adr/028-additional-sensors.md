# ADR-028 – Additional Sensors with Derived Power and Smoothed Series

**Date:** 2026-11-09 **Status:** Draft (Gate 1 pending). The decisions are the
human's answers of 2026-11-09 to the questions of TASK-0047; the text was written
by the Lead Agent and has not been read by the human. Terms follow
`GLOSSARY.md`.

---

## Context

Effy's useful series logic (the trapezoidal redistribution of a low-resolution
energy counter, ADR-012; the idle zero-fill and the gap smoothing, ADR-013) runs
only for the sensors that take part in the loss. The human wants it for a sensor
that does not: the house's grid import. Such a sensor must not enter any loss,
effective, standby, efficiency or battery calculation.

## Decision

1. **Additional sensors.** One optional multi-entity list in the config and
   options flows. Each sensor in it gets the series of an input before the loss
   is distributed, chosen by its family as for inputs: an energy-family sensor
   the derived power series (`effy_<slug>_power`), a power-family sensor the
   smoothed series (`effy_<slug>_smoothed`). No effective sensor, no loss share.
1. **Same machinery.** The series are produced by the code that produces them
   for inputs (ADR-012, ADR-013, ADR-014, ADR-024): idle zero-fill applies, a
   late-bound sensor is created when its state appears, the slot job, the recent
   recalculation and the button write them, the live value is the last completed
   slot's. The series are not passed to the waterfall.
1. **No role twice.** A sensor that already is a PV input, a BMS counter, the EPS
   sensor, the state of charge sensor or the capacity entity is refused, as is
   one listed twice (ADR-019's rule that a sensor has one role). The unit must be
   W, kW, Wh or kWh.
1. **History.** The button writes it; saving the options starts no
   recalculation. When the formula signature of ADR-027 decision 6 exists, the
   list is part of it, so a change then recalculates by itself.
1. **Published contract** (ADR-021, additive, `schema_version` stays 1): `role`
   `other`, `direction` `unknown`, `function` `unknown` (the role of the sensor
   is not known to Effy), `series` `derived_power` or
   `smoothed`, `source_entity_id` the sensor.

## Alternatives considered

- **Two lists** (energy counters, power sensors): rejected by the human; the
  family decides, as for inputs.
- **Allow a role and an additional entry for one sensor**: rejected; two Effy
  entities would share one unique ID.
- **Leave gaps for an idle counter**: rejected; the zero-fill is the point of the
  use case.

## Consequences

- More recorder reads per run for each sensor; ADR-025's cache holds them.
- `direction` and `function` `unknown` are new values of existing contract keys.
- Until the button (or the signature of ADR-027) runs, a new sensor has no
  history.
