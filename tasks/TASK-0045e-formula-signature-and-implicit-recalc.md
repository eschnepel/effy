# Task: Formula signature and the implicit full recalculation with the delta derivation

- **Status:** done
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-019, ADR-024, ADR-012]
- **Dependencies:** \[TASK-0045c-delta-derivation,
  TASK-0045d-pv-delta-integral-sensors\]
- **Gate:** Gate 2 approved by the human (2026-11-09, with Q27 = A); re-scoped
  in a design interview on 2026-11-07, split from the held TASK-0045.

## Goal

A change of the configured sensors that influence the formula, and the first
start after the upgrade, runs the full history recalculation and the delta
derivation by itself, then hands the bases to the sensors.

## Acceptance Criteria

- Given the options, then a pure function returns the formula signature: the PV
  inputs (order does not matter), grid import, discharge, charge, grid export
  and EPS sensor. A change of history days, SoC, capacity, valley hysteresis or
  debug export does not change it.
- Given setup of an entry whose data holds no signature or a different one, then
  a background task (setup does not wait for it) runs under the recalculation
  lock: the full history recalculation, the delta derivation for every PV input
  that has no statistics, the combined integrals (see decision below), the
  hand-over of all bases to the sensors, and finally the signature is stored in
  the entry's **data**. Storing it reloads nothing and no update listener is
  added.
- Given an equal signature, then nothing runs at setup.
- Given an input without a base during that recalculation, then its deltas are
  not booked (the derivation gives its base); inputs that have a base book
  normally. Which inputs have no base is decided by whether their integral has
  statistics, not by the signature.
- Given a PV input that is added, then it is derived after the recalculation;
  given an input that is removed and added again, then its statistics exist,
  nothing is derived and its base is its last statistic row (`state`,
  `last_reset`), and the recalculation books the gap as deltas.
- Given any failing step, then the signature is not stored, a warning is logged,
  the sensors stay `unavailable`, the slot jobs keep working, and the next setup
  tries again.
- Given an entry without a PV input, then a signature change still runs the full
  recalculation and derives nothing.
- Given the button after the bases exist, then its deltas are booked normally.
- Given the options flow, then a formula-relevant change reloads the entry and
  triggers the above, and any other change reloads and triggers nothing.

## Decisions

- ADR-027 decision 6 (human, 2026-11-07).
- **Decision 9 / Q27 decided by the human, 2026-11-09: A.** When the PV inputs
  change after the integrals exist, the combined integrals are derived again as
  the per-slot addition of the per-input statistics of the configured inputs
  (the per-slot energy of an input is the change of its running total; a new
  input's comes from its derivation), written as a one-time rewrite while the
  sensors are `unavailable`, and a removed input leaves the combined history. B
  (combined series left untouched) was not chosen.
- **TASK-0047 (2026-11-09):** the list of additional sensors (CAP-14) is part of
  the formula signature, so adding or removing one starts the full
  recalculation. Whichever of the two tasks is done second makes that change.

## Estimated File / Module Footprint (hint, not a commitment)

`__init__.py`, `coordinator.py`, `inputs.py` (the signature), `history.py` (the
orchestration and the reader of per-input statistics), and tests for each.

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

- `history._compute_effective_slots(hass, options, start, end, ...)`,
  `history.async_recalculate_history`, `history.async_recalculate_recent` in
  `custom_components/effy/history.py`; `history._effy_entity_id`,
  `history._effy_smoothed_entity_id`.
- `inputs.pv_input_ids(options)` in `custom_components/effy/inputs.py`.
- `__init__.async_setup_entry` (entry data, `async_update_entry`; the options
  flow is `OptionsFlowWithReload`), `coordinator._recorder_job_lock`,
  `coordinator.async_recalculate_history_and_report`.
- The derivation, its writer and the delta base (→ task:
  TASK-0045c-delta-derivation, done:
  `history.async_derive_pv_delta_integrals(hass, options, *, now=None, storage=None) -> dict[statistic ID, history.DeltaBase(state, last_reset, total)]`
  (only derived series); `history._pv_delta_integral_id(source)`,
  `const.PV_DELTA_TODAY_ENTITY_ID`, `const.PV_DELTA_TOTAL_ENTITY_ID`;
  `calculation.slot_energy_steps`, `integral_rows`, `combined_integral_rows`;
  `Storage.stored_means`).
- The sensors, booking and base handover (→ task:
  TASK-0045d-pv-delta-integral-sensors, done):
  `coordinator.EffyCoordinator.hand_over_delta_bases(bases, units=None)` takes
  what `history.async_derive_pv_delta_integrals` returned (keyed by statistic
  ID, which is the sensor's entity ID) and makes each sensor show exactly its
  base, available; `units` optionally names the unit each derived series was
  written in so that the sensor continues in it. `coordinator.pv_delta_enabled`,
  the live integrals `coordinator._delta_integrals`
  (`delta_integrals.DeltaIntegrals.has_base(source)` says whether an input has a
  base: the slot job and the button already pass `delta_skip=` for those without
  one and book the deltas after the write). A base of an earlier local day is
  reset like a restored state. The combined IDs read `deltasum` (ADR-027
  Amendment 2026-11-10): `sensor.effy_pv_deltasum_integral_today` / `_total`;
  per input `sensor.effy_<slug>_delta_integral_today`; `delta_integrals.py`,
  `sensor.EffyPvDeltaIntegralSensor`. Nothing calls `hand_over_delta_bases` yet.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- `custom_components/effy/const.py` → `CONF_FORMULA_SIGNATURE`
  (`"formula_signature"`, a key of the entry's **data**).
- `custom_components/effy/inputs.py` → `formula_signature(options) -> dict`
  (keyed by the option constants `CONF_PV_INPUT_SENSORS` (sorted, de-duplicated
  list), `CONF_GRID_IMPORT_SENSOR`, `CONF_DISCHARGE_SENSOR`,
  `CONF_CHARGE_SENSOR`, `CONF_GRID_EXPORT_SENSOR`, `CONF_EPS_SENSOR`; `None` for
  an unset role; JSON-safe) and
  `signature_pv_inputs(signature) -> list[str] | None` (`None` = unknown, `[]` =
  no PV input). **TASK-0047 adds the additional sensors to `formula_signature`
  (one more key).**
- `custom_components/effy/calculation.py` →
  `steps_from_running_totals(rows_5min, rows_hourly, slot_minutes=5) -> list[IntegralStep]`
  (the inverse of `integral_rows`; negative steps kept; the first row only fixes
  the starting point).
- `custom_components/effy/storage.py` → dataclass
  `IntegralStat(state, total, last_reset=None)` and
  `Storage.stored_integral(statistic_id, start, end) -> (5-minute rows, hourly rows)`;
  implemented in `recorder_storage.py` (`RecorderStorage.stored_integral`),
  passed through (observed, never reused) in `cache.py`
  (`Cache.stored_integral`), reader `history._fetch_integral_stored` (registered
  in the reader guard of `tests/test_history_cache.py`).
- `custom_components/effy/history.py` →
  `async_read_delta_bases(hass, entry_options, *, now=None, storage=None) -> (dict[statistic ID, DeltaBase], dict[statistic ID, unit])`
  (the last stored row of every integral that has statistics) and
  `async_rederive_pv_combined(hass, entry_options, *, now=None, storage=None) -> (dict[statistic ID, DeltaBase], dict[statistic ID, "kWh"])`
  (clears the two combined series, then writes them as the addition of the
  per-input statistics; `({}, {})` without a PV input).
- `custom_components/effy/delta_integrals.py` →
  `DeltaIntegrals.invalidate( entity_ids) -> list[DeltaIntegral]`.
- `custom_components/effy/coordinator.py` →
  `EffyCoordinator.async_run_formula_recalculation(signature) -> bool` (public,
  the background task), private `_async_formula_steps(step)`,
  `_async_recalculate_history_locked()` (the button's body, now shared:
  `async_recalculate_history_and_report` = lock + this).
- `custom_components/effy/__init__.py` → `async_setup_entry` compares
  `formula_signature(entry.options)` with `entry.data[CONF_FORMULA_SIGNATURE]`
  and, if different or absent, calls
  `entry.async_create_background_task(hass, coordinator.async_run_formula_recalculation(signature), "effy_formula_recalculation_<entry_id>")`
  after the platforms are set up.
- No new sensors, so no translation changes. External dependencies added: none
  (`tasks/DEPENDENCIES.md` unchanged).
- Tests (new): `tests/test_formula_signature.py` (29),
  `tests/test_integral_steps_from_totals.py` (14),
  `tests/test_delta_rederivation.py` (24, the in-memory recorder),
  `tests/test_coordinator_formula_recalc.py` (30),
  `tests/test_formula_recalc_trigger.py` (13); extended:
  `tests/test_delta_integrals.py` (`invalidate`), `tests/test_history_cache.py`
  (the pass-through and the reader guard), and the shared fakes
  `tests/support.py`, `tests/test_coordinator_slot.py`,
  `tests/test_history_write_only_changed.py` (`async_clear_statistics`),
  `tests/test_migration.py`.
- Behaviour to know, **decisions of the Lead Agent within the task, flagged for
  the human**:
  1. *Q27 = A is a clear and rewrite.* The combined series are cleared with
     `Recorder.async_clear_statistics` and written again from the per-input
     statistics over their whole stored range, not only the history window. That
     keeps the running total continuous and leaves no row of the earlier
     composition, but it also drops combined rows the per-input statistics do
     not explain, and the combined series has no rows for the last, unsettled
     minutes until Home Assistant compiles them.
  1. The rewrite runs when combined statistics exist and the stored signature
     names another set of PV inputs, **or names none** (entry data without a
     signature). The first row of every per-input series only fixes its starting
     point, so one step at the far end of the history is not counted.
  1. The base of a series that has statistics is handed over before the
     recalculation (so the gap is booked), only to sensors that are unavailable;
     a restored state is kept. The combined sensors are made unavailable while
     they are rewritten.
  1. A failing step logs one warning naming the step; the run is not retried
     before the next setup.
  1. The background task starts right after setup; it does not wait for Home
     Assistant to finish starting.
- Not verifiable here (no Home Assistant in the sandbox; the in-memory recorder
  models its read and write rules): `Recorder.async_clear_statistics` and
  `async_block_till_done` as used, that `async_update_entry(data=...)` reloads
  nothing in a real Home Assistant, and the run time of the first recalculation.
- Reviewer (inline, no sub-agent available): PASS. Every acceptance criterion
  has a test; the code stays within ADR-027 decisions 6 and 9; the full suite
  passes (2073, 1958 before this task); `ruff check` and `mypy --strict`
  (package and tests) are clean; `ruff format` flags only `tests/test_cache.py`,
  as before. Eight mutations of the coordinator's run (no invalidate, rewrite
  always or never for an unknown signature, no hand-over of the waiting bases, a
  restored state overwritten, the signature stored on failure, no lock, the
  derived bases not handed over) each make a test fail.
