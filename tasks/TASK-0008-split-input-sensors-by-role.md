# Task: Split the Input Sensors into PV and BMS Inputs

- **Status:** done
- **Related ADRs:** [ADR-001, ADR-019]
- **Dependencies:** [TASK-0006-recorder-synchronized-slot-trigger]

## Goal

Replace Effy's one mixed list of input sensors by two lists, PV inputs and BMS
inputs (ADR-019, human decision 2026-10-13: "the list of input sensors is mixed
and needs to be split up"; silent migration of the old key decided 2026-10-16).
The calculation does not change; the role is needed by the standby power sensor
(ADR-018, TASK-0005 Stage 2), whose drain source can only be a BMS input.
Released 2026-10-16 (ADR-019 Accepted; Gate 2 approved), to run after TASK-0006,
because both edit `coordinator.py` and `tests/test_coordinator_slot.py`.

## Acceptance Criteria

- Given the config flow and the options flow, then both show two multi-selects,
  PV input sensors (`CONF_PV_INPUT_SENSORS` = `pv_input_sensors`) and BMS input
  sensors (`CONF_BMS_INPUT_SENSORS` = `bms_input_sensors`), and
  `CONF_INPUT_SENSORS` no longer exists.
- Given no input sensor in either list, then the form returns the error key
  `at_least_one_input`; given a sensor in both lists, then it returns a new
  translatable error key; given no BMS input and at least one PV input, then the
  entry is accepted.
- Given the translations, then `en.json` and `de.json` carry the new field
  names, descriptions and error keys.
- Given the coordinator, history and sensor modules, then the inputs of the loss
  model are the PV and BMS inputs together and every existing test passes with
  unchanged expected values (the TASK-0003/TASK-0004 equivalence harness
  included).
- Given the coordinator, then the two roles are available as two collections of
  source entity IDs (names filled into Delivered Artifacts), for TASK-0005 Stage
  2\.
- Given the same sources, then splitting them into the two lists gives results
  identical to the unsplit list, in either order within a list (the waterfall
  sees one mixed pool).
- Given an old entry whose options contain the old key `input_sensors` and
  neither new key, when `async_setup_entry` runs, then the old list is written
  to `pv_input_sensors`, `bms_input_sensors` is empty, the old key is removed,
  one warning is logged, no reload is triggered (the update listener is not yet
  registered), there is no `VERSION` bump and no `async_migrate_entry`, and the
  entry ID and the `sensor.effy_*` unique IDs are unchanged.
- Given an entry that already has either new key, then the old key (if still
  present) is dropped unread and the new keys are untouched.
- Given a migrated entry, then the options flow shows the PV list filled and the
  BMS list empty, and saving a split into both lists is accepted.
- Given the README, then its configuration table lists PV inputs and BMS inputs
  separately.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/const.py`, `config_flow.py` (9 references),
  `coordinator.py`, `history.py`, `sensor.py`, `translations/en.json`,
  `translations/de.json`, `README.md`.
- `custom_components/effy/__init__.py` (migration in `async_setup_entry`).
- `tests/test_coordinator_slot.py`, `tests/test_history_recent_equivalence.py`
  (they name the old key), new config-flow validation tests and new migration
  tests.

## Definition of Done

- Tests green; `mypy --config-file mypy.ini custom_components/ tests/`,
  `ruff check .`, `ruff format --check .` clean; `mdformat --check` clean on
  edited `.md` files.
- ADR-019 Accepted (2026-10-16); `tasks/adr-summary.md` and `GLOSSARY.md`
  accurate.
- No new external dependency; `tasks/DEPENDENCIES.md` unchanged.
- `Delivered Artifacts` block completed and accurate.

## Consumed Interfaces

<!-- Refreshed by the Lead Agent 2026-10-16 from the code and from the
     Delivered Artifacts of TASK-0006. -->

- `CONF_INPUT_SENSORS` (to be removed publicly) and `CONF_OUTPUT_SENSORS` from
  `custom_components/effy/const.py`. Current uses of the old key, all to be
  changed: `config_flow.py` (schema default, both validation branches, options
  prefill), `coordinator.py` (`EffyCoordinator.__init__` → `_input_ids`),
  `sensor.py` (`async_setup_entry` → `input_ids`), `history.py`
  (`_compute_effective_slots`, and `async_probe_last_slot_ready` added by
  TASK-0006), `translations/en.json` and `de.json` (both flow steps), and the
  tests `tests/test_history_recent_equivalence.py` (`_options`) and
  `tests/test_coordinator_slot.py` (`_FakeEntry`).
- `__init__.async_setup_entry(hass, entry) -> bool` from
  `custom_components/effy/__init__.py`: the migration runs at its start, before
  `entry.add_update_listener(_async_reload_entry)`.
- From TASK-0006 (→ task: TASK-0006-recorder-synchronized-slot-trigger, `done`),
  `custom_components/effy/coordinator.py`: `EffyCoordinator.async_setup`,
  `async_shutdown`, `_on_recorder_statistics`, `_on_slot_timer(now)`,
  `_start_slot_job`, `_async_slot_job`, `_async_recalculate_recent_and_report`,
  `async_recalculate_history_and_report() -> int`; and
  `history.async_probe_last_slot_ready(hass, entry_options, now) -> bool`.

## Delivered Artifacts

- `custom_components/effy/const.py` →
  `CONF_PV_INPUT_SENSORS = "pv_input_sensors"`,
  `CONF_BMS_INPUT_SENSORS = "bms_input_sensors"`; `CONF_INPUT_SENSORS` removed.
- `custom_components/effy/inputs.py` (new) →
  `LEGACY_CONF_INPUT_SENSORS = "input_sensors"` (migration only),
  `pv_input_ids(options) -> list[str]`, `bms_input_ids(options) -> list[str]`,
  `all_input_ids(options) -> list[str]` (PV first),
  `legacy_inputs_were_moved(options) -> bool`,
  `migrate_legacy_input_options(options) -> dict | None`.
- `custom_components/effy/__init__.py` →
  `_async_migrate_input_options(hass, entry)`, called first in
  `async_setup_entry` (before the coordinator and before `add_update_listener`);
  one warning when inputs were moved; no `async_migrate_entry`, no `VERSION`
  bump.
- `custom_components/effy/config_flow.py` →
  `_build_schema(pv_input_sensors, bms_input_sensors, output_sensors, max_history_days)`
  (two optional multi-selects with a *suggested* value, so either list can be
  cleared), `_validate_user_input(hass, user_input) -> dict[str, str]` (shared
  by both flows; new error key `sensor_in_both_input_lists`),
  `_normalised(user_input)` (both lists always saved).
- `custom_components/effy/coordinator.py` → `EffyCoordinator.pv_input_ids` and
  `.bms_input_ids` (properties, copies); `_input_ids` is the mixed pool.
- `custom_components/effy/sensor.py`, `history.py` (`_compute_effective_slots`,
  `async_probe_last_slot_ready`) use `all_input_ids`.
- `translations/en.json`, `de.json` (both flows: two fields, new error,
  at-least-one-input text); `README.md` (parameter table, upgrade note).
- Tests: `tests/test_inputs.py`, `tests/test_migration.py` (new),
  `tests/test_config_flow.py` (schema, validation, translations, `VERSION`),
  `tests/test_history_recent_equivalence.py` (`TestSplitInputsAreOnePool`: full
  recalculation and timer path identical for every split),
  `tests/test_coordinator_slot.py`. The new test files failed at collection
  before the change (the constants and `inputs.py` did not exist).
- External dependencies added: none; `tasks/DEPENDENCIES.md` unchanged.
