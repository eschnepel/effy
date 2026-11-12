# ADR-028 – Additional Sensors with Derived Power and Smoothed Series

**Date:** 2026-11-09 **Status:** Accepted 2026-11-12 — by the human's
instruction in chat to work on TASK-0047 (the ADR's Gate 2), as for ADR-023. The
decisions are the human's answers of 2026-11-09 to the questions of TASK-0047;
the text was written by the Lead Agent and has not been read line by line by the
human. Terms follow `GLOSSARY.md`.

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
1. **No role twice.** A sensor that already is a PV input, a BMS counter, the
   EPS sensor, the state of charge sensor or the capacity entity is refused, as
   is one listed twice (ADR-019's rule that a sensor has one role). The unit
   must be W, kW, Wh or kWh.
1. **History.** The button writes it; saving the options starts no
   recalculation. When the formula signature of ADR-027 decision 6 exists, the
   list is part of it, so a change then recalculates by itself.
1. **Published contract** (ADR-021, additive, `schema_version` stays 1): `role`
   `other`, `direction` `unknown`, `function` `unknown` (the role of the sensor
   is not known to Effy), `series` `derived_power` or `smoothed`,
   `source_entity_id` the sensor.

## Alternatives considered

- **Two lists** (energy counters, power sensors): rejected by the human; the
  family decides, as for inputs.
- **Allow a role and an additional entry for one sensor**: rejected; two Effy
  entities would share one unique ID.
- **Leave gaps for an idle counter**: rejected; the zero-fill is the point of
  the use case.

## Consequences

- More recorder reads per run for each sensor; ADR-025's cache holds them.
- `direction` and `function` `unknown` are new values of existing contract keys.
- Until the button (or the signature of ADR-027) runs, a new sensor has no
  history.

## Amendment — 2026-11-12 (implementation, TASK-0047)

**Reason:** the implementation had to settle points the decisions leave open.

**Decision:**

1. **Unique ID.** The Effy entity of an additional sensor has the unique ID
   `<entry_id>_additional-<slug>_power` (or `_smoothed`); its entity ID is the
   input's, `sensor.effy_<slug>_power` / `_smoothed`. The hyphen cannot be part
   of a slug, so no input's entity can have such an ID and the registry cleanup
   of a sensor taken out of the list finds exactly the additional ones. A sensor
   still in the list keeps both of its possible entities (the other family is
   built late when its state appears).
1. **Slot union.** The additional sensors are fetched, converted and smoothed
   with the inputs, but their rows are not in the slot union the waterfall runs
   over: the effective rows are the same with and without them, a slot that only
   an additional sensor has starts no waterfall slot.
1. **Rewrite range.** The slot job's rewrite range stays global (ADR-014): a
   jump of an additional counter extends it for every series, like any
   counter's. The effective rows that this rewrites are recomputed from the same
   data, and unchanged rows are not written (ADR-024).
1. **Role check at read time.** A sensor listed as additional that also has a
   role in the options (a hand-edited entry) is not an additional sensor: the
   options flow refuses it, and `inputs.additional_ids` drops it.
1. **Formula signature.** The signature of ADR-027 decision 6 holds the sorted
   list under the option key `additional_sensors` (empty when there is none). An
   entry that stored the earlier signature differs from it and recalculates once
   more at its first start.
1. **Unit check.** A sensor that reports a unit other than W, kW, Wh or kWh is
   refused (`additional_unit_not_supported`); a sensor without a unit or without
   a state is accepted, as for the capacity entity.

**Known limit (not run against a real Home Assistant):** a sensor moved from a
role to the additional list keeps its old role entity in the registry, as a
sensor taken out of a role always did. The new additional entity has the same
entity ID and a different unique ID, so the registry may give it another entity
ID until the old entity is removed by hand. The cleanup deliberately touches
additional entities only.

**Decided by:** Lead Agent (◇ formalization), to be confirmed by the human
