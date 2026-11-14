# Task: Household consumption power sensor

- **Status:** todo
- **Capability:** CAP-15
- **Related ADRs:** \[ADR-029, ADR-001, ADR-002, ADR-005, ADR-016, ADR-024,
  ADR-026, ADR-027\]
- **Dependencies:** [TASK-0048-household-meter-inputs]. It shares
  `calculation.py`, `history.py`, `sensor.py`, `coordinator.py` and the
  translations with TASK-0048 and TASK-0050.
- **Gate:** ADR-029 accepted by the human on 2026-11-13 ("ADR is accepted"); the
  task is not yet released for work. Requested by the human on 2026-11-13;
  design questions answered in chat the same day.

## Goal

Effy shows how much power the household used in each slot as
`sensor.effy_household_consumption_now`, computed at the battery system's AC
port from the household meter, the system's grid counters and the EPS output.
This task does **not** publish it (TASK-0045f) and does **not** add the energy
sensors (TASK-0050).

## Acceptance Criteria

- Given a slot, then in W

  ```
  consumption = max(0, hi − he + sys_export + eps_out − sys_import)
  ```

  where `hi` and `he` are the household import and export series, `sys_export`
  and `sys_import` the series of the system's grid export and grid import
  counters, and `eps_out` the positive part of the EPS sensor (ADR-026). All are
  the series **before** the loss is distributed (derived power or smoothed),
  never the effective values. The EPS input is **not** subtracted. Example: `hi`
  300 W, `he` 0, `sys_export` 200 W, `eps_out` 50 W, `sys_import` 100 W: 450 W.
  Example with units: `hi` 0.3 kW and `sys_import` 100 W are added after unit
  normalisation (ADR-002, ADR-008).

- Given a power-family household import sensor with no household export sensor
  (a signed series, TASK-0048), then `hi = max(v, 0)` and `he = max(−v, 0)` with
  the split of `calculation.split_eps_readings` (the rule of ADR-026). Given a
  household export sensor, then a negative reading of either sensor counts as 0.
  A counter with no export sensor gives `he = 0`.

- Given a slot whose result is below 0 (meter noise, timing offset between the
  meter and the BMS counters), then it is **0**, not negative (ADR-005's rule
  for the loss).

- Given a slot with no household import value (no zero-fill either), then the
  slot has **no row**. Any other missing term counts as 0, like a PV input
  without a value in the PV sum (ADR-027 decision 1).

- Given the slot job, the recent recalculation and the button, then all three
  produce the series, a slot whose value changed is rewritten and unchanged rows
  are not (ADR-024), the hourly row is the mean of its slots, the live value is
  the last completed slot's (ADR-016) and it is restored after a restart. A full
  recalculation and the recent recalculation over the same slots give identical
  rows (the CAP-1 equivalence test covers the new series).

- Given the entity, then it is `sensor.effy_household_consumption_now`
  ("Household consumption now", unique ID
  `<entry_id>_household_consumption_now`): unit W, device class power, state
  class `measurement`. It exists while a household import sensor is configured
  and is removed from the entity registry otherwise; its statistics stay.

- Given the household consumption series, then it takes part in no loss,
  effective, share, standby, efficiency or battery calculation: the effective
  rows of an entry are identical with and without the household sensors (a test
  pins this).

- Given the translations en and de, then the sensor name is there.

- Given the README, then the sensors section names the sensor with the formula,
  states that the EPS input is not subtracted and that the formula balances at
  the system's AC port, and the "How the algorithm works" section is unchanged.

## Decisions

Human, 2026-11-13, in answer to the questions of this task:

1. The house sits on the grid side of the system's AC port (the system is the
   BMS): `consumption = hi − he + sys_export − sys_import`, plus the EPS output
   (energy leaving the BMS counts as household); the EPS input is not
   subtracted.
1. The system's counters enter as the series before the loss, not effective.
1. A negative result is clamped to 0; a missing household import is a gap, any
   other missing term is 0.
1. The sensor is a power sensor, written like the PV sum; the energy side is
   TASK-0050.

## Estimated File / Module Footprint (hint, not a commitment)

`calculation.py` (`household_consumption(...)`, pure), `history.py` (the slot
loop builds the series from the series TASK-0048 provides and from the
unweighted system series), `sensor.py` (the entity, like `EffyPvSumSensor`),
`const.py`, `coordinator.py`, `translations/en.json`, `translations/de.json`,
`README.md`, and tests for each (`tests/test_calculation.py` for the pure
formula).

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (README sensors section;
  translations en and de) · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-13). -->

- The household series and `inputs.household_import_id(options)` /
  `household_export_id(options)` / `household_ids(options)` (→ task:
  TASK-0048-household-meter-inputs, done): the series are in the 2nd (derived
  power, energy family) and 3rd (smoothed, power family) result of
  `history._compute_effective_slots` under `sensor.effy_<slug>_power` and
  `sensor.effy_<slug>_smoothed` (`history._effy_power_entity_id`,
  `_effy_smoothed_entity_id`), **as measured and never clamped**; the source
  family is `world.state_classes`/units at compute time.
  `inputs.household_import_is_signed(options)` is true exactly when the import
  series is to be split by sign (a household import and no valid export): a
  negative reading is then the household export; otherwise each series is taken
  as it is and a negative reading counts as 0. `inputs.series_only_ids` lists
  the additional and the household sensors;
  `EffyCoordinator.household_import_id`, `.household_export_id`,
  `.household_ids`.
- `calculation.split_eps_readings`, `history._compute_effective_slots` (the PV
  combined series is the model for a series that is not an input's),
  `sensor.EffyPvSumSensor`, `const.PV_SUM_ENTITY_ID` and its unique ID helper.
- `history.async_recalculate_history`, `history.async_recalculate_recent`,
  `history._hourly_rows` (mean hour).
- `tests/test_history_recent_equivalence.py` (`_World`, `_install`,
  `_run_full_recalc`, `_equivalent`), `tests/test_eps_capability.py` as the
  model for an acceptance module.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
