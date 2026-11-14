# Task: Household consumption energy sensors, and the generalised delta mechanism

- **Status:** todo
- **Capability:** CAP-15
- **Related ADRs:** [ADR-029, ADR-027, ADR-024, ADR-016, ADR-011]
- **Dependencies:** [TASK-0049-household-consumption-power-sensor]. It changes
  code of the `done` tasks TASK-0045b to TASK-0045e and shares `calculation.py`,
  `history.py`, `coordinator.py`, `sensor.py`, `inputs.py` and the translations
  with TASK-0048 and TASK-0049.
- **Gate:** ADR-029 accepted by the human on 2026-11-13 ("ADR is accepted"); the
  task is not yet released for work. Requested by the human on 2026-11-13:
  "power and energy sensors", the mechanism generalised (option A) rather than
  copied (B) or replaced by a live-only counter (C).

## Goal

Effy shows the energy the household used, today and in total, as two Home
Assistant energy sensors whose statistics follow every correction Effy makes, by
the mechanism of ADR-027 (PV delta booked at write time, history derived once),
now working on **any series** and not only on the effective series of PV inputs.
This task does **not** publish the sensors (TASK-0045f).

## Acceptance Criteria

- Given the mechanism of ADR-027 (the PV delta of a run, the booking into live
  states, the midnight reset, the delta base, the delta derivation and its
  hand-over), then it is **generalised** to take the series it works on as a
  parameter: the PV inputs' effective series and the household consumption
  series are two users of it. **No behaviour of the CAP-13 sensors changes**:
  the existing tests of TASK-0045b to TASK-0045e pass unchanged except for
  renamed symbols, and a test pins a CAP-13 entry's numbers before and after.

- Given a series name and a unit, then the glossary terms follow the code:
  `GLOSSARY.md` generalises "PV delta" and "PV delta integral" (or adds the
  general terms and keeps the PV ones as the PV cases), whichever the worker
  proposes at Stage 1 and the Lead agrees.

- Given a household import sensor in the options, then
  `sensor.effy_household_consumption_today` and
  `sensor.effy_household_consumption_total` exist: unit kWh, device class
  energy, state class `total`, *today* with `last_reset` at local midnight that
  Effy resets at wall-clock midnight, *total* without. Their unique IDs are
  `<entry_id>_household_consumption_today` and `…_total`. They are removed from
  the entity registry when no household import is configured; their statistics
  stay.

- Given a run that rewrites rows of the consumption power series, then it books
  the **delta** of those rows (new − stored) × 5/60 h, converted to kWh, into
  *today* and *total* at the time of the write, as for the PV inputs (ADR-027
  decision 2 and 3); a run that writes nothing books nothing.

- Given a full history recalculation, then the delta derivation writes the
  history of both sensors once, from the stored consumption rows, as in ADR-027
  decision 5 (5-minute rows where they exist, hourly rows further back, hourly
  rows only for closed hours, running-total rows, `has_sum=True`); the sensors
  stay `unavailable` until their base is written.

- Given a change of the household import or export sensor, then the formula
  signature (TASK-0048) starts the full recalculation by itself and the
  consumption sensors are derived **again** as in Q27 = A of ADR-027 (cleared
  and rewritten, `unavailable` meanwhile, then the new base). An option that
  does not influence the formula does not.

- Given a button run after a correction, then it books the correction in *today*
  and *total* (today may go below 0), and `change` of an hour equals the energy
  booked in it.

- Given the translations en and de, then the two names are there.

- Given the README, then the sensors section describes them, how they are booked
  (one slot late, as the PV ones), and that they follow corrections.

## Decisions

Human, 2026-11-13: option A (generalise); the sensors are `today` and `total`
only (no per-source variant, no separate per-run sensor); the entity IDs follow
the pattern of the power sensor.

## Open points for Stage 1 (decide with the Lead, not alone)

- How the PV-specific names (`pv_delta`, `EffyPvDeltaIntegralSensor`,
  `PV_DELTA_*`, `delta derivation`) become general without breaking the entity
  and unique IDs of existing PV sensors (they must not change; the entity
  registry would orphan them).
- Whether the formula-signature recalculation that already clears and rederives
  the combined PV integrals (Q27 = A) also clears the consumption ones in the
  same pass.

## Estimated File / Module Footprint (hint, not a commitment)

`calculation.py` (`pv_delta` and its helpers), `history.py` (the delta
computation and the derivation, one parameter more), `coordinator.py` (booking,
midnight reset, `hand_over_delta_bases`), `sensor.py`
(`EffyPvDeltaIntegralSensor`, `_pv_delta_entities`), `const.py`, `inputs.py`,
`GLOSSARY.md`, `translations/en.json`, `translations/de.json`, `README.md`, and
tests for each.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (README sensors section, glossary;
  translations en and de) · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-13). -->

- `calculation.pv_delta`; the delta computation in `history`; the sensors
  `sensor.EffyPvDeltaIntegralSensor`, `sensor._pv_delta_entities`; the
  coordinator's booking and `hand_over_delta_bases`; `Storage.stored_integral`;
  `Recorder.async_clear_statistics` use of TASK-0045e (→ tasks: TASK-0045b to
  TASK-0045e, done).
- The consumption power series and its entity (→ task:
  TASK-0049-household-consumption-power-sensor, done): the series is a member of
  the first result (`per_sensor`) of `history._compute_effective_slots` under
  `const.HOUSEHOLD_CONSUMPTION_ENTITY_ID`
  (`sensor.effy_household_consumption_now`), unit `"W"`, one row per slot where
  the household import has a value, produced by
  `history._household_consumption_series` from
  `calculation.household_consumption`; the entity is
  `sensor.EffyHouseholdConsumptionSensor`, created by
  `sensor._household_consumption_entities` while
  `coordinator.household_import_id` is set (statistics and registry removal as
  the PV sum's). It is **not** an effective series of an input: code that walks
  the first result and assumes `_effy_entity_id(input)` keys must skip it, as it
  skips the PV sum and the PV share.
- `inputs.formula_signature` with the household keys (→ task:
  TASK-0048-household-meter-inputs).
- `tests/test_delta_*.py`, `tests/test_pv_delta_*.py`,
  `tests/test_coordinator_delta_integrals.py`,
  `tests/test_formula_recalc_trigger.py`, `tests/test_formula_signature.py`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
