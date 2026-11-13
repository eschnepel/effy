# ADR-027 – The Effective PV Sum and the PV Delta Integrals

**Date:** 2026-11-07 **Status:** Accepted (decision 9 decided by the human on
2026-11-09: Q27 = A). Decisions 1 to 8 were confirmed by the human in a design
interview on 2026-11-07 ("Agreed."); this text was written by the Lead Agent
from that interview and has not been re-read by the human line by line. It
replaces the draft of the same number, which asked questions Q1 to Q3 that the
interview settled differently. Terms follow `GLOSSARY.md`; "sum" is avoided for
addition (see decision 8).

---

## Context

TASK-0045 (CAP-13) asks for sensors that add up the effective values of all PV
inputs: the power now, and energy for the day and for the lifetime. The first
design (draft of this ADR) had Effy write a `sum` statistic for the energy
sensors. The interview ended at a smaller one: Home Assistant compiles the
statistics of the energy sensors from their live states, and Effy writes a `sum`
row only once, to give them a history.

**What Effy's statistics are today (verified in the code).** Every row is
`{start, mean, state}` with `has_mean=True`, `has_sum=False`; Effy never wrote a
running total or a `last_reset`. The stored rows are read on every run, in the
recorder's executor, but only the set of unchanged starts comes back
(`history._unchanged_slots`), not their values. Saving the options reloads the
entry (`OptionsFlowWithReload`) and starts no recalculation; only the button
does.

**What Home Assistant does (read from `homeassistant` 2026.10.4, not run).**

- For a `total` sensor the recorder adds `new state − last stored state` to the
  running total, with any sign. A changed `last_reset` starts a new cycle at 0.
  A `total_increasing` sensor that falls by more than 10 % counts as reset and
  its new value as fresh energy.
- An hourly row takes `state` and running total of the last 5-minute row of the
  hour. The energy device class allows only `total` and `total_increasing`.
- A sensor whose state is `unavailable` produces no statistics row.

## Decision

1. **PV combined series.** One more series in the slot loop of
   `history._compute_effective_slots`: the effective values of all PV inputs
   added up per slot, in W (kW inputs normalised, ADR-002 and ADR-008). It is
   written and rewritten like every effective series, so the slot job, the
   recent recalculation and the button all produce it and a changed slot is
   rewritten. A PV input without a value in a slot adds 0; a slot without a
   value from any PV input has no row. The EPS sensor and the BMS inputs are not
   in it. Sensor: "Effective PV Sum now", `sensor.effy_effective_pv_sum_now`,
   state class `measurement`, device class power.

1. **PV delta.** Each run computes, per PV input, the PV delta (glossary) from
   the old values of the rows it writes. `_unchanged_slots` therefore returns
   the stored values, not only the unchanged starts, of the PV inputs' effective
   series, which cross to the event loop where the delta is computed with the
   rest of the calculation (ADR-024 Amendment 2026-11-09); no new recorder read.
   A stored value is converted to the new unit by the unit in its metadata; with
   no metadata it counts as 0. If the stored rows cannot be read, the run books
   no delta and logs a warning (it writes every row, ADR-024). An input that has
   no delta base yet books no delta (the derivation provides its base). The
   deltas are returned by the recalculation and booked by the coordinator only
   after the write has succeeded. Hourly rows do not count.

1. **PV delta integrals.** Per PV input
   `sensor.effy_<slug>_delta_integral_today`, name "<source name> delta integral
   today" (Wh if the effective unit is W, kWh if kW); combined
   `sensor.effy_pv_deltasum_integral_today` and
   `sensor.effy_pv_deltasum_integral_total` (kWh; IDs per the Amendment
   2026-11-10). All have device class energy and state class `total`; *today*
   has `last_reset` at local midnight, *total* none. Booking adds the delta to
   the live state at the time of the write, whichever slot it concerns, so a
   correction to earlier days lands in today and the value may go negative; it
   is not clamped. Effy resets *today* to 0 at wall-clock local midnight and
   sets the new `last_reset`; a restored state of an earlier day is reset the
   same way. They exist only while the entry has at least one PV input and are
   removed from the registry otherwise (the per-input one when its input is
   removed). There is no per-run sensor: the delta of a period is the `change`
   of the statistic.

1. **Statistics belong to Home Assistant.** In normal operation Effy writes none
   for the PV delta integrals; the recorder compiles them from the live states.
   A statistic therefore shows an energy one slot late, and a period's `change`
   is what was booked in it. The button never touches them.

1. **Delta derivation.** After a full history recalculation, for every PV input
   with no statistics for its integral, and for the combined integrals (decision
   9), Effy derives the history once: the energy of a slot is the effective
   row's power times 5/60 h, taken from the 5-minute rows where they exist and
   from the hourly rows (mean times 1 h) further back. Per row: `state` is the
   energy accumulated since the local midnight of that row's day, `last_reset`
   that midnight (none for *total*, whose `state` is its running total), and the
   running total accumulates from the oldest row. Both tables are written,
   metadata `has_sum=True`, `has_mean=False`, the unit of the sensor. Hourly
   rows only for closed hours (the settled rule of ADR-024), 5-minute rows only
   up to the last completed slot, so the recorder never compiles a slot Effy
   derived. The sensors stay `unavailable` until their base is written, then
   push exactly the last derived `state`; after that they book deltas. A sensor
   with nothing to derive starts at 0. A restart restores the last state like
   the other Effy sensors; after a crash it can be up to 15 minutes stale and
   Home Assistant books the difference as one small step, and a delta lost
   between the write and the booking is not recovered. This is accepted.

1. **Formula signature and implicit full recalculation.** The configured PV
   inputs, grid import, discharge, charge, grid export and EPS sensors form the
   formula signature, kept in the entry's data (not its options, so storing it
   reloads nothing). At setup, if it differs from the current options or is
   absent (first start after the upgrade), Effy runs in the background, under
   the recalculation lock: the full history recalculation, the delta derivation
   for what lacks a base, and then stores the signature. If a step fails, the
   signature is not stored, a warning is logged and the next setup retries; the
   slot jobs keep working meanwhile. No other option triggers it.

1. **Published contract** (ADR-021, additive, `schema_version` stays 1; `role`
   `pv`, `direction` `input`, no `function` key):

   | Entry | `series` | `source_entity_id` | Unit | Aggregations |
   | -- | -- | -- | -- | -- |
   | Effective PV Sum now | `pv_sum` | none | W | `mean`, `state` |
   | per PV input | `pv_delta_integral` | the PV input | Wh or kWh | `state`, `sum`, `change` |
   | combined today | `pv_delta_integral_today` | none | kWh | `state`, `sum`, `change` |
   | combined total | `pv_delta_integral_total` | none | kWh | `state`, `sum`, `change` |

   `effy_exports.py` gets the four series values and the `change` aggregation.

1. **Vocabulary.** The glossary says running total for Home Assistant's `sum`
   column, change for its difference over a period, and combined for the
   addition across inputs. The human-facing sensor names keep "Sum".

1. **Decided by the human, 2026-11-09 (Q27 = A):** when the set of PV inputs
   changes after the integrals exist, the combined integrals are derived again,
   as the per-slot addition of the per-input statistics of the configured inputs
   (one-time rewrite, the sensors `unavailable` meanwhile, then the new base);
   an input that is removed leaves the combined history, so the combined is
   always the addition of the configured inputs. (Option B, leaving the combined
   statistics untouched so an input counts only from the moment it was added,
   was not chosen.)

## Alternatives considered

- **Effy writes a delta series as statistics** (the first reading of the
  proposal, one row per run). Rejected by the human: every statistic of the
  delta integrals belongs to Home Assistant; the history is derived once.
- **`total_increasing` for the lifetime sensor.** Any correction beyond 10 %
  would count as a reset and inflate the energy.
- **A new `last_reset` on every push** so that each pushed delta is a cycle.
  Replaced by the daily cycle, which keeps the state meaningful and the change
  of any period readable.
- **`measurement` for a delta sensor.** Home Assistant's hourly value is a mean,
  not an addition of the slots.

## Consequences

- Effy gets a `has_sum` writer, used once per derivation. It is not covered by
  the stored-rows check of ADR-024. It has not been run against a real recorder
  (the sandbox cannot); the first live check is the human's.
- **Every existing entry runs one full history recalculation after the
  upgrade**, because no signature is stored yet.
- A period's `change` is shifted by one slot against the physical slot.
- Corrections of earlier days appear in "today" and in the `change` of the
  period in which Effy learned them.

## Amendment 2026-11-09 (TASK-0046): the PV share

**Decision.** One more series, the sensor `sensor.effy_pv_share_now` ("PV share
now"): per slot
`100 × (Σ effective PV + effective EPS input) / Σ effective inputs` (all PV
inputs, the EPS input, grid import, discharge), in `%`, state class
`measurement`, no device class. Because Σ effective inputs = Σ outputs (ADR-001,
ADR-026), it is the part of the outputs PV and the EPS input fed. A slot with no
effective input is 0. The hourly row is energy-weighted (Σ numerator over Σ
denominator of its slots), not the mean of the slot shares. It exists while the
entry has a PV input or an EPS sensor. It is published (`series` `pv_share`,
unit `%`, `mean` and `state`, TASK-0045f).

**Reason.** Human request 2026-11-09. Slot-local by choice: PV that charged the
battery and comes back as discharge counts as "discharge" later; the README
states this limit. One overall sensor, because Effy cannot observe which input
fed which output.

## Amendment — 2026-11-13 (TASK-0046 implementation: how the energy-weighted hour is written)

**Reason:** the PV share's hourly row is not the mean of its slots, but every
place that derives, compares or rewrites an hourly row did exactly that.

**Decision:**

1. **Weights travel with the series.** The share's entry in `per_sensor` has,
   beside `unit` and `slot_values`, `weights` (slot → the slot's denominator,
   all effective inputs in W) and `sources` (the statistic IDs of the effective
   series it is computed from). The one derivation of an hourly row
   (`history._hourly_rows`) averages energy-weighted when `weights` are given:
   `100 × Σ numerators / Σ denominators`, 0 for an hour whose weights add up to
   nothing, `state` the last slot's value.
1. **A slot's value.** `pv_share(numerator, denominator)`: 0 for a denominator
   that is not positive; a ratio within 1e-12 of 0 or 1 is exactly 0 or 100 (the
   waterfall's float noise must not make "all PV" 99.99999999999999 %). A slot
   in which no input has a row has no row at all, as for every effective series;
   a slot in which inputs have rows but nothing is fed is 0.
1. **History run (ADR-024 Amendment 2026-10-26 stands: no hourly row is read).**
   An hour of the share is unchanged only if its own 5-minute rows are unchanged
   **and** the 5-minute rows of the effective series in `sources` are unchanged
   in that hour: together they fix the numerators and the denominators. An hour
   in which every slot has the same share (PV only: 100 % all day) ignores the
   sources, since its weighted mean is that value whatever the weights. So a
   change in an input's data in an hour rewrites the share's one hourly row even
   when its own 5-minute rows are equal; that costs one row and no read.
1. **Hour rewrite of the slot job** (`async_rewrite_hourly`). The stored
   5-minute shares cannot give the weights back. The share's hour is recomputed
   over the whole hour with `_compute_effective_slots`, the code of the full
   recalculation, from the entry options (new parameter `entry_options`, passed
   by the coordinator): one extra fetch and compute of one hour, once an hour.
   Without options the share is left out and the next history recalculation
   corrects the hour; it is never written as the mean of its slots. The hourly
   rows of the other series are rewritten from the stored 5-minute rows as
   before.
1. **Entity.** `sensor.effy_pv_share_now` exists with a PV input or an EPS
   sensor; otherwise a registered entity is removed, its statistics stay (as the
   PV sum). Unique ID `<entry_id>_pv_share_now`.

**Decided by:** Lead Agent, as the task's open point asked (decided with the
Lead, not by the worker), to be confirmed by the human

## Amendment — 2026-11-10 (the combined integrals' entity IDs read `deltasum`)

**Reason:** TASK-0045c found that a PV source whose slug is `pv` (for example
`sensor.pv`) gets the per-input ID `sensor.effy_pv_delta_integral_today`, which
is the combined *today* ID of decision 3. Two sensors cannot have one entity ID
or one statistic ID, and the derivation had to leave such an input out.

**Decision:** The entity IDs (and so the statistic IDs) of the two integrals
that combine all PV inputs are renamed: `sensor.effy_pv_deltasum_integral_today`
and `sensor.effy_pv_deltasum_integral_total`. The per-input IDs stay
`sensor.effy_<slug>_delta_integral_today`. With that the two kinds cannot
collide: a per-input ID ends in `_delta_integral_today`, and no slug can turn
that into `pv_deltasum_integral_…`. Nothing is skipped or warned about any more.
The sensor names ("PV delta integral today", "PV delta integral total"), and the
published `series` values of decision 7 (`pv_delta_integral_today`,
`pv_delta_integral_total`) are not part of this decision and stay as they are.
The unique IDs follow the entity IDs, for the same reason (TASK-0045d): the
combined ones are `<entry_id>_pv_deltasum_integral_today` and
`<entry_id>_pv_deltasum_integral_total`, the per-input ones
`<entry_id>_<slug>_delta_integral_today`; otherwise a source named `pv` would
share its unique ID with the combined *today* sensor, which the entity registry
refuses.

**Decided by:** human (2026-11-10, answering the open point of TASK-0045c: the
rename of the entity IDs); Lead Agent (the same rule for the unique IDs, a
consequence of it, to be confirmed by the human with TASK-0045d)

## Amendments in other ADRs (written with this ADR, in force with it)

ADR-019 (the signature in the entry's data and the implicit recalculation at
setup), ADR-021 (the four series values and the `change` aggregation) and
ADR-024 (the derivation's write mode) each carry an amendment of 2026-11-07.
