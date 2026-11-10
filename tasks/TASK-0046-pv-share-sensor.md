# Task: The PV share of the effective inputs, the sensor PV share now

- **Status:** todo
- **Capability:** CAP-13 (added 2026-11-09)
- **Related ADRs:** \[ADR-027 (amendment 2026-11-09), ADR-026, ADR-001, ADR-016,
  ADR-021, ADR-024\]
- **Dependencies:** [TASK-0045a-pv-combined-series]
- **Needed by:** TASK-0045f (publishes the sensor), TASK-0045g (acceptance and
  README)
- **Gate:** Gate 2 approved by the human (2026-11-09); human request 2026-11-09,
  design questions answered the same day.

## Goal

Effy shows how much of what fed the outputs in the last completed slot came from
PV (and from another inverter through the EPS port), compared with the other
inputs (grid import, battery discharge), as one percentage.

## Acceptance Criteria

- Given a slot, then the PV share is

  ```
  share = 100 × (Σ effective PV + effective EPS input) / (Σ effective inputs)
  ```

  where the effective inputs are all effective values of the slot: every PV
  input, the EPS input (the negative part of the EPS sensor, ADR-026), the grid
  import and the discharge counter. Because Σ effective inputs = Σ outputs
  (ADR-001, ADR-026), this is the part of the outputs (charge, grid export, EPS
  output) that PV and the EPS input fed. The value lies in 0..100.

- Given a slot in which no effective input is not zero (nothing fed any output),
  then the share is **0**, not a missing row.

- Given two slots with the same inputs, the PV in W and kW in mixed units, then
  the units are normalised before the addition (ADR-002, ADR-008). Example:
  effective PV 300 W and 0.2 kW, effective grid import 500 W, nothing else: the
  share is 50.

- Given an entry without a PV input and without an EPS sensor, then the sensor
  is not created or is removed from the entity registry; its statistics stay.
  Given an entry with only an EPS sensor, then it exists (the EPS input can be
  the PV side).

- Given the slot job, the recent recalculation and the button, then all three
  produce the series, a slot whose value changed is rewritten and unchanged rows
  are not (ADR-024); a full recalculation and the recent recalculation over the
  same slots give identical rows (the CAP-1 equivalence test covers the new
  series).

- Given the hourly row, then it is **energy-weighted**: 100 × Σ numerator over
  the hour's slots / Σ denominator over the hour's slots, and 0 if the
  denominator is 0. It is **not** the mean of the slot shares.

- Given the sensor, then it is `sensor.effy_pv_share_now` ("PV share now",
  unique ID `<entry_id>_pv_share_now`): unit `%`, state class `measurement`, no
  device class, its live value the last completed slot's share, pushed with the
  other effective sensors (ADR-016) and restored after a restart like them.

- Given the sensor names, then the translations en and de name it.

- Given the published list, then it is **not** yet in it (TASK-0045f).

## Decisions

- Human, 2026-11-09, in answer to the questions of this task:
  1. Formula: PV + EPS input over all effective inputs (the EPS input counts on
     the PV side).
  1. One overall sensor for all outputs together; a share per output sensor is
     not part of this task (Effy cannot observe which input fed which output).
  1. Battery double counting accepted: PV that charged the battery and is
     discharged later counts as "discharge" in the later slot. Slot-local; the
     README says so.
  1. A slot with nothing fed gives 0.
  1. The hourly row is energy-weighted.
  1. Unit **percent 0..100** with `%`. The request first said "0..1"; the human
     chose percent in the form.
  1. Part of CAP-13, own amendment of ADR-027; TASK-0045f depends on this task.
  1. The sensor goes into `effy_exports.py` (TASK-0045f).

## Open points for the Stage 1 check of the worker (decide with the Lead, not alone)

- **The hourly row needs the numerator and denominator of every slot of the
  hour**, but a recent recalculation rewrites only a few slots of an hour and
  the stored share of the other slots cannot give the weights back. Check how
  the hourly rows of the other series are built in `history.py` and what ADR-024
  says about closed hours; either the run keeps the numerator and denominator of
  the whole hour (a read of the stored effective series of the inputs for the
  rest of the hour) or the hourly row is written only when the run holds the
  whole hour. The choice must keep the full and recent recalculations equal.
- `direction` of the published entry (`input` like the PV entries, or `both`):
  decided in TASK-0045f with the contract.

## Estimated File / Module Footprint (hint, not a commitment)

`history.py` (the slot loop, the hourly row), `sensor.py`, `coordinator.py`,
`const.py`, `translations/en.json`, `translations/de.json`, and tests for each.
A pure function for the ratio (zero-mock tests).

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (translations en and de for the
  sensor name; the README names the formula and the battery limit in "How the
  algorithm works" or the sensors section; TASK-0045g checks the README)
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-09). -->

- `history._compute_effective_slots`, `history.async_recalculate_history`,
  `history.async_recalculate_recent`, `history._unchanged_slots` in
  `custom_components/effy/history.py`.
- The PV combined series (→ task: TASK-0045a-pv-combined-series, done:
  `per_sensor[const.PV_SUM_ENTITY_ID]` = `{"unit": "W", "slot_values": [...]}`
  from `history._compute_effective_slots`; `const.PV_SUM_ENTITY_ID`,
  `const.PV_SUM_UNIQUE_ID_SUFFIX`, `const.PV_SUM_TRANSLATION_KEY`;
  `sensor.EffyPvSumSensor`, `sensor.pv_sum_unique_id`,
  `sensor._pv_sum_entities`).
- The EPS split and the effective EPS input series (TASK-0041, TASK-0043).
- `sensor.EffySensor`, `sensor._standby_entities` (a sensor that exists only
  under a condition), `sensor._async_restore_measurement`.
- `coordinator.subscribe`, `coordinator.notify_updated`, the `last_values` the
  recalculations return.
- `tests/test_history_recent_equivalence.py` (`_World`, `_install`,
  `_run_full_recalc`, `_equivalent`).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
