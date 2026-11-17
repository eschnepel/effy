# Task: CAP-13 acceptance test and README

- **Status:** done (2026-11-16; reviewed inline by the Lead Agent, human
  confirmation open)
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-019, ADR-021, ADR-024]
- **Dependencies:** \[TASK-0045a-pv-combined-series, TASK-0045b-pv-delta-values,
  TASK-0045c-delta-derivation, TASK-0045d-pv-delta-integral-sensors,
  TASK-0045e-formula-signature-and-implicit-recalc,
  TASK-0045f-pv-published-entries, TASK-0046-pv-share-sensor\]. TASK-0045f now
  also waits for CAP-15 (TASK-0048 to TASK-0050), so this task runs last.
- **Gate:** Gate 2 approved by the human (2026-11-09, with Q27 = A); re-scoped
  in a design interview on 2026-11-07, split from the held TASK-0045.

## Goal

One acceptance module demonstrates CAP-13 end to end on the real recalculation,
and the README describes the sensors and what to expect from them.

## Acceptance Criteria

- Given one module `tests/test_pv_delta_capability.py` on the in-memory recorder
  with two PV inputs (one in W, one in kW) and an output, then it shows, with
  the numbers written out in the test and not taken from the code:
  - the PV combined series is the addition of the inputs' effective values;
  - the derivation gives both inputs and the combined integrals a history, the
    running total is the energy of all rows, and the combined integrals equal
    the per-input ones added up;
  - the next slot books its energy into the three integral kinds;
  - a formula-relevant option change runs the full recalculation and derivation
    by itself, and an option that does not influence the formula does not;
  - a button run after a correction books it in today and total (today may go
    negative) and the combined still equal the per-input ones added up;
  - the `change` of an hour equals the energy booked in it;
  - an entry without a PV input has none of the sensors;
  - the published list carries the PV kinds (TASK-0045f's model).
- Given the README, then a section describes: the sensors and their IDs; the PV
  delta and the formula; what the derivation writes and when it runs; the
  formula signature and that every existing entry recalculates once after the
  upgrade; that a period's delta is the `change` of the statistic and is shifted
  by one slot; that today can go below 0 after a correction; the contract table
  of ADR-029 (version 2: the grouped `sensors` attribute with `quantity`,
  `scope`, `span`, `function` on every group, the new `series` values, the YAML
  example) with every published entry, the CAP-15 household entries included;
  and the ADR table lists ADR-027 and ADR-029 with their titles.
- Given the published list, then the acceptance module checks every group of the
  ADR-029 table that the test entry configures.
- Given `tasks/adr-summary.md`, then the ADR-027 entry says implemented, with
  the state of decision 9; `GLOSSARY.md` agrees with the built code.
- Given the translations en and de, then every new text is there.

## Decisions

- ADR-027 (human, 2026-11-07). Decision 9 is closed by the human before this
  task.

## Estimated File / Module Footprint (hint, not a commitment)

`tests/test_pv_delta_capability.py` (new), `README.md`, `tasks/adr-summary.md`,
`GLOSSARY.md` if the code differs, and the translations if a text is missing.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (translations en and de for sensor
  names) · no open ADR conflicts (ADR-027 decision 9 decided: Q27 = A)
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-07). -->

- The tests and the harness of TASK-0039 to TASK-0044
  (`tests/test_eps_capability.py` is the model for this module),
  `tests/test_history_recent_equivalence.py`.
- Everything the other CAP-13 tasks deliver (→ task: TASK-0045a to TASK-0045f),
  as the acceptance module consumes it (verified against the code 2026-11-16):
  `history.async_derive_pv_delta_integrals`, `history.async_read_delta_bases`,
  `history.async_rederive_pv_combined`; `delta_integrals.DeltaIntegrals`; the
  coordinator's `async_run_formula_recalculation`,
  `async_recalculate_history_and_report`,
  `_async_recalculate_recent_and_report`, `pv_delta_enabled`,
  `published_descriptors`; `inputs.formula_signature` and
  `const.CONF_FORMULA_SIGNATURE`; the sensor platform's `async_setup_entry`,
  `pv_sum_unique_id`, `pv_share_unique_id` and the sensors it creates
  (`sensor.effy_effective_pv_sum_now`, `sensor.effy_pv_share_now`,
  `sensor.effy_<slug>_delta_integral_today`,
  `sensor.effy_pv_deltasum_integral_today` and `_total`);
  `const.PV_SUM_TRANSLATION_KEY`, `PV_SHARE_TRANSLATION_KEY`,
  `PV_DELTA_INPUT_TRANSLATION_KEY`, `PV_DELTA_TODAY_TRANSLATION_KEY`,
  `PV_DELTA_TOTAL_TRANSLATION_KEY`; `__init__.async_setup_entry`.
- From TASK-0045f-pv-published-entries (done 2026-11-15), exact: the contract
  `custom_components/effy/effy_exports.py` (`EFFY_PUBLISHED_SCHEMA_VERSION` = 2,
  `EFFY_KEY_*` including `EFFY_KEY_SENSORS`, `EFFY_ATTR_SOURCES`,
  `EFFY_SERIES_*`, `EFFY_QUANTITY_*`, `EFFY_SCOPE_*`, `EFFY_SPAN_*`,
  `EFFY_FUNCTION_PV`, `EFFY_FUNCTION_SHARE`, `EFFY_ENERGY_AGGREGATIONS`,
  `EFFY_PV_SUM_ENTITY_ID`, `EFFY_PV_ENERGY_TODAY_ENTITY_ID`,
  `EFFY_PV_ENERGY_TOTAL_ENTITY_ID`, `EFFY_PV_SHARE_ENTITY_ID`,
  `EffyPublishedGroup`, `EffyPublishedAttributes`) and
  `custom_components/effy/catalog.py` (`describe_pv_sum`, `describe_pv_energy`,
  `describe_pv_share`, `published_attributes` returning `sensors` as groups and
  `sources`). The PV sum, the PV energy sensors and the PV share are published:
  PV sum = series `effective`, quantity `power`, scope `combined`; PV energy =
  `effective`, `energy`, scope `source` (span `today`) or `combined` (`today`,
  `total`); PV share = series `pv`, quantity `share`, scope `system`, function
  `share`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- `tests/test_pv_delta_capability.py` (new, 46 test functions, 60 tests with the
  parametrized ones). Loader and rig: `_load_integration()` (the real
  `delta_integrals.py`, `coordinator.py` and `__init__.py`, registered in the
  private package of `test_history_recent_equivalence` for the time of the load,
  Home Assistant stubbed), `_Rig`, `_Entry`, `_ConfigEntries` (runs the real
  sensor platform), `_Clock`, `_Compile` (a model of Home Assistant's compile of
  a `total` sensor, only for the `change` of an hour), `_world()`,
  `_snapshot()`. Test classes: `TestThePvCombinedSeries`, `TestTheDerivation`,
  `TestTheNextSlotBooksItsEnergy`, `TestTheFormulaRecalculationRunsByItself`,
  `TestAButtonRunAfterACorrection`, `TestTheChangeOfAnHourIsWhatWasBookedInIt`,
  `TestAnEntryWithoutAPvInputHasNoneOfTheSensors`, `TestThePublishedList` (an
  exact literal list of the 7 groups, and every row of the ADR-029 table the
  entry configures, parsed from the ADR file), `TestTheTranslations`,
  `TestTheReadme`.
- `tests/test_history_recent_equivalence.py` →
  `_load_history_modules(mean_type)` and `_MODULES` added; `_load_history`
  delegates to it and returns what it returned before (a behaviour-neutral
  refactor so that the loaded modules stay reachable).
- `README.md` → new section "PV sum now and PV energy" (sensors and IDs, the
  delta and the formula, what the derivation writes and when, the formula
  signature and the recalculation of every existing entry after the upgrade,
  `change` and the one-slot shift, today below 0 after a correction, the known
  limit of the combined rewrite) and "The published entries" (the ADR-029 table,
  every entry, the household ones included); the ADR-029 row of the ADR table
  now carries its title.
- `GLOSSARY.md` → "PV combined series" no longer says it is published as
  `pv_sum`.
- `tasks/adr-summary.md` → the ADR-027 entry says implemented, with decision 9
  and the known limit.
- Translations en and de: nothing to add, every text of the capability exists in
  both (checked by `TestTheTranslations`). No code under `custom_components/`
  changed.
- External dependencies added: none; `tasks/DEPENDENCIES.md` unchanged.
- **Found while writing the acceptance test (open for the human):** after the
  one-time rewrite of the combined integrals (Q27 = A, a change of the PV
  inputs) the combined statistics lack the oldest slot of the history, and the
  combined *total* sensor is that slot's energy short (0.075 kWh in the test
  scenario). It is the limit TASK-0045e already flagged ("the first row of every
  per-input series only fixes its starting point"). The test pins it
  (`test_a_new_pv_input_is_derived_and_…`) and the README states it. Fixing it
  is a patch task (Phase 6, Scenario C) and the human's call.
