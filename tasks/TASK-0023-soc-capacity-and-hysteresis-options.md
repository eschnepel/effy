# Task: Options for state of charge, battery capacity and valley hysteresis

- **Status:** done — 2026-10-29, reviewer PASS (inline)
- **Capability:** CAP-9
- **Related ADRs:** [ADR-023 decision 1 and 6]
- **Dependencies:** [TASK-0017-counter-roles-in-config]
- **Gate:** ADR-023 accepted 2026-10-27.

## Goal

Three more options: the state-of-charge sensor, the battery-capacity entity and
the valley hysteresis, with the validation of ADR-023.

## Acceptance Criteria

- Given the options flow, then it offers `soc_sensor`, `capacity_entity` and
  `valley_hysteresis` (a number, default 30, minimum 15), all optional.
- Given SoC without capacity or capacity without SoC, then validation rejects
  it; given a SoC sensor without state class `measurement`, then it is rejected.
- Given the capacity entity, then the sensor, number and input_number domains
  are accepted and a unit other than Wh and kWh is an error when the entity has
  a unit.
- Given the options, then accessors in `inputs.py` return the IDs and the
  hysteresis (the default when unset).
- Given the translations (en, de) and the README, then the three options and the
  errors are described.
- Given an entry without them, then nothing changes (the battery efficiency
  simply does not exist).

## Estimated File / Module Footprint (hint, not a commitment)

`const.py`, `inputs.py`, `config_flow.py`, `translations/*.json`, README,
`tests/test_inputs.py`, config-flow tests.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- From TASK-0017 (`config_flow.py`):
  `_build_schema(pv_input_sensors, grid_import_sensor, discharge_sensor, charge_sensor, grid_export_sensor, max_history_days)`,
  `_validate_user_input(hass, user_input)` (error keys `at_least_one_input`,
  `sensor_in_two_roles` (on the later field), `at_least_one_output`,
  `output_needs_device_class`; the first failing check wins),
  `_normalised(user_input)`, `_picker(key, suggested, *, device_class)`,
  `EffyOptionsFlow(config_entries.OptionsFlowWithReload)`, `EffyConfigFlow`.
- From TASK-0017 (`inputs.py`): `pv_input_ids`, `_role_id`, `grid_import_id`,
  `discharge_id`, `charge_id`, `grid_export_id`, `bms_input_ids`, `output_ids`,
  `all_input_ids`. These pools must **not** change: SoC and capacity are neither
  inputs nor outputs.
- From TASK-0017 (`const.py`): `CONF_PV_INPUT_SENSORS`,
  `CONF_GRID_IMPORT_SENSOR`, `CONF_DISCHARGE_SENSOR`, `CONF_CHARGE_SENSOR`,
  `CONF_GRID_EXPORT_SENSOR`, `CONF_MAX_HISTORY_DAYS`,
  `DEFAULT_MAX_HISTORY_DAYS`.
- From TASK-0017 (`tests/`): `tests/test_config_flow.py` (the stubs and
  `_FakeState`/`_FakeHass`), `tests/test_inputs.py`, `tests/support.py`.
- Filled by the Lead (2026-10-27) from the Delivered Artifacts of TASK-0017.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- `const.py`: `CONF_SOC_SENSOR` (`"soc_sensor"`), `CONF_CAPACITY_ENTITY`
  (`"capacity_entity"`), `CONF_VALLEY_HYSTERESIS` (`"valley_hysteresis"`),
  `DEFAULT_VALLEY_HYSTERESIS` (30), `MIN_VALLEY_HYSTERESIS` (15),
  `CAPACITY_UNITS` (`("Wh", "kWh")`).
- `inputs.py`: `soc_id(options) -> str | None`,
  `capacity_id(options) -> str | None` (an unset or emptied picker is `None`),
  `valley_hysteresis(options) -> float` (the saved number, else 30).
  `all_input_ids`, `bms_input_ids` and `output_ids` are unchanged: SoC and
  capacity are in neither pool (test).
- `config_flow.py`:
  `_build_schema(..., soc_sensor, capacity_entity, valley_hysteresis, max_history_days)`
  (the three new parameters sit before `max_history_days`; no other caller
  exists); `_picker(..., domains=None)`;
  `_validate_battery_options(hass, user_input)`;
  `_entity_attribute(hass, entity_id, attribute)`; `_BATTERY_KEYS`;
  `_CAPACITY_DOMAINS` (`sensor`, `number`, `input_number`);
  `_validate_user_input` (the two entities join the one-role-per-entity rule,
  then the battery checks run last); `_normalised` (the SoC sensor and the
  capacity entity are saved only if set, the hysteresis as submitted). New error
  keys: `soc_and_capacity_together` (on the *missing* field),
  `soc_needs_measurement`, `capacity_unit_not_energy`.
- `translations/en.json`, `de.json`: the three fields and three errors in both
  flows; the description of both flows names the options.
- README: the Configuration table (three rows) and a paragraph.
- Tests: `tests/test_inputs.py` (`TestBatteryOptionAccessors`, key and constant
  pins), `tests/test_config_flow.py` (`TestBatteryOptionsInTheForm`,
  `TestBatteryOptionsValidation`, one translation test).
- External dependencies added: none.

## Decisions taken inside the task (◇), for the human's confirmation

1. **Hysteresis upper bound 100.** ADR-023 fixes the minimum (15) and the
   default (30) and no maximum; the number selector has `max: 100`, the SoC
   scale.
1. **A capacity entity without a unit, or without a state, is accepted when
   saving**, as this task's criterion words it ("an error when the entity has a
   unit"); ADR-023's text ("any other unit is an error") is silent on it. What
   happens at feed time with a capacity whose unit is not known is TASK-0025's
   to settle; the Wh/kWh check is one of the Lead's assumptions listed in
   ADR-023 for the Gate.
1. **A SoC sensor without a state counts as without state class** and is
   rejected (`soc_needs_measurement`), the same as an output without a device
   class; a SoC sensor that is `unavailable` at the moment of saving may lose
   its `state_class` attribute in Home Assistant and be rejected until it is
   back. Not verified in the sandbox (no Home Assistant).
1. **The minimum of 15 is held by the number selector alone**, as the frontend's
   and the backend's schema validation enforce it; there is no second check and
   no clamp in `valley_hysteresis`. TASK-0024's machine may still refuse a value
   below the band.
1. `valley_hysteresis` is returned as a float (the number selector delivers
   floats).

## Review

**PASS** (inline; 847 tests (819 before), ruff, ruff format and mypy on source
and tests, `mdformat` on the edited markdown). Written tests first: both test
files failed at collection (the constants did not exist), then went green with
the implementation. Acceptance criteria to tests: the three optional fields in
both flows with their selector configuration (1), the pair rule on the missing
field and the state class (2), the three capacity domains and the unit check
(3), the accessors and the default (4), en and de texts and the README (5), an
entry without the options saves exactly what it did before and the pools do not
move (6). The declared symbols were checked against the code by script.

Twenty-eight mutants were run against the new code; all real ones are caught
(minimum, default and maximum, the unit list, the state class check and its
leniency, the pair rule on each field, the one-role rule for the new entities,
the three domains, saving empty pickers, the suggestions and the default in the
options flow, the accessors' keys and type, SoC leaking into the inputs, and the
order of the checks). One survivor was an equivalent mutant of my own making
(`errors or {}`), replaced by a real order mutation, which is caught.

Not verifiable in the sandbox: the real frontend (whether the number box and the
three-domain picker look as intended), and Home Assistant's validation of the
selector's `min`.
