# Task: Split the Input Sensors into PV and BMS Inputs

- **Status:** todo
- **Related ADRs:** [ADR-001, ADR-019]
- **Dependencies:** []

## Goal

Replace Effy's one mixed list of input sensors by two lists, PV inputs and BMS
inputs (Draft ADR-019, human decision 2026-10-13: "the list of input sensors is
mixed and needs to be split up", no migration path). The calculation does not
change; the role is needed by the standby power sensor (ADR-018, TASK-0005 Stage
2), whose drain source can only be a BMS input. Not released: ADR-019 is Draft
until a human confirms it.

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
- Given an old entry with `CONF_INPUT_SENSORS`, then the key is not read
  anywhere; there is no migration, no `VERSION` bump and no special error
  (human: the only installation, set up again); setting the integration up again
  yields the same `sensor.effy_*` entity IDs for the same sources.
- Given the README, then its configuration table lists PV inputs and BMS inputs
  separately.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/const.py`, `config_flow.py` (9 references),
  `coordinator.py`, `history.py`, `sensor.py`, `translations/en.json`,
  `translations/de.json`, `README.md`.
- `tests/test_coordinator_slot.py`, `tests/test_history_recent_equivalence.py`
  (they name the old key) and new config-flow validation tests.

## Definition of Done

- Tests green; `mypy --config-file mypy.ini custom_components/ tests/`,
  `ruff check .`, `ruff format --check .` clean; `mdformat --check` clean on
  edited `.md` files.
- ADR-019 confirmed by a human; `tasks/adr-summary.md` and `GLOSSARY.md`
  accurate.
- No new external dependency; `tasks/DEPENDENCIES.md` unchanged.
- `Delivered Artifacts` block completed and accurate.

## Consumed Interfaces

<!-- Read from the current code; refresh before the task starts. -->

- `CONF_INPUT_SENSORS` and `CONF_OUTPUT_SENSORS` from
  `custom_components/effy/const.py`; their use in `config_flow.py`,
  `coordinator.py`, `history.py`, `sensor.py` (to be listed by the Lead Agent
  before the task starts).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->
