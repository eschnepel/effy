# Task: The optional EPS/UPS sensor picker, its validation and its accessor

- **Status:** done
- **Capability:** CAP-12
- **Related ADRs:** \[ADR-026 decision 1, ADR-019 Amendment 2026-11-06, ADR-023
  decision 1 and Amendment 2026-11-06\]
- **Dependencies:** []
- **Gate:** Gate 2 approved by the human (2026-11-06); ADR-026 accepted.

## Goal

One more optional picker in the config and options flow, `eps_sensor`, with its
validation, its translations and its README row, and an accessor in `inputs.py`.
The option is only stored and validated here: it changes no pool and no
calculation until TASK-0041.

## Acceptance Criteria

- Given the options flow, then it offers `eps_sensor`: one optional entity
  picker, domain `sensor`, device class `power`, with a suggested (never a
  default) value, so it can be cleared.
- Given an EPS sensor without state class `measurement` (or without a state),
  then validation rejects it with a translated error (`eps_needs_measurement`
  ◇); given a unit other than W and kW (when the entity has one), then it is
  rejected (`eps_unit_not_power` ◇); the device class is re-checked at submit
  like the outputs'.
- Given the EPS sensor also chosen as a PV input or as one of the four counters,
  then the existing one-role-per-entity error fires (`sensor_in_two_roles`).
- Given an entry whose only inputs or outputs would be the EPS sensor, then the
  two minimums are not satisfied by it: `at_least_one_input` and
  `at_least_one_output` fire as before.
- Given `inputs.eps_id(options)`, then it returns the ID or `None` (unset or
  emptied); `all_input_ids`, `bms_input_ids`, `output_ids` and `pv_input_ids`
  are **unchanged** (a test pins this).
- Given `_normalised`, then the EPS sensor is saved only if set.
- Given the translations (en, de) and the README configuration table, then the
  option, its two errors and its meaning (signed: positive out, negative in) are
  described, including the consequence for standby and efficiency (ADR-026
  decision 5).
- Given an entry without an EPS sensor, then nothing changes.

## Estimated File / Module Footprint (hint, not a commitment)

`const.py` (`CONF_EPS_SENSOR = "eps_sensor"`), `inputs.py`, `config_flow.py`,
`translations/en.json`, `translations/de.json`, `README.md`,
`tests/test_inputs.py`, `tests/test_config_flow.py`.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- Pre-existing, `config_flow.py`: `_build_schema(...)` (keyword parameters per
  role, then `max_history_days`, `debug_export`),
  `_picker(key, suggested, *, device_class, domains=None)`,
  `_validate_user_input(hass, user_input)` (the first failing check wins; the
  battery checks run last), `_normalised`,
  `_entity_attribute(hass, entity_id, attribute)`, `_validate_battery_options`
  (the model for the state-class check), `_COUNTER_KEYS`, `_OUTPUT_KEYS`.
- Pre-existing, `inputs.py`: `_role_id`, `pv_input_ids`, `grid_import_id`,
  `discharge_id`, `charge_id`, `grid_export_id`, `soc_id`, `capacity_id`,
  `bms_input_ids`, `output_ids`, `all_input_ids`.
- Pre-existing, `const.py`: the `CONF_*` keys.
- Pre-existing, tests: `tests/test_config_flow.py` (the stubs, `_FakeState`,
  `_FakeHass`), `tests/test_inputs.py`, `tests/support.py`.
- No dependency tasks.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- `custom_components/effy/const.py` → `CONF_EPS_SENSOR = "eps_sensor"`,
  `EPS_UNITS = ("W", "kW")`.
- `custom_components/effy/inputs.py` →
  `eps_id(options: Mapping[str, Any]) -> str | None` (unset or emptied gives
  `None`). `all_input_ids`, `bms_input_ids`, `output_ids` and `pv_input_ids` are
  unchanged and do not contain the EPS sensor; TASK-0041 adds it to
  `all_input_ids`.
- `custom_components/effy/config_flow.py` → picker `eps_sensor` (single sensor,
  domain `sensor`, device class `["power"]`, suggested value only, placed after
  the four counters and before the SoC sensor); `_picker(..., device_class)` now
  takes `bool | list[str]` (`True` still means power and energy);
  `_EPS_DEVICE_CLASSES = ["power"]`; `_validate_eps_option(hass, user_input)`
  (device class, state class, unit; run after `_validate_battery_options`); the
  EPS sensor is in the one-role-per-sensor loop (the error sits on `eps_sensor`)
  and in `_normalised`'s optional keys (saved only if set);
  `_build_schema(..., eps_sensor=None, ...)`; the options flow passes the saved
  value. Error keys: `eps_needs_power_class` ◇ (device class is not `power`, or
  the entity has no state; a third key beyond the two the task named, so the
  message is not the outputs' "power or energy" one), `eps_needs_measurement`,
  `eps_unit_not_power`. The two minimums (`at_least_one_input`,
  `at_least_one_output`) are unchanged and the EPS sensor satisfies neither.
- `custom_components/effy/translations/en.json`, `de.json` → data key
  `eps_sensor`, the three error keys, and one sentence in the description, in
  both flows (`config`, `options`).
- `README.md` → a configuration table row and a paragraph (meaning of the sign,
  validation, role rules, consequence for standby and efficiency). The paragraph
  describes the behaviour of the whole capability: the option is stored and
  validated here and has no effect on the calculation until TASK-0041.
- `tests/test_config_flow.py` → `TestEpsOptionInTheForm`,
  `TestEpsOptionValidation`, `TestEpsTranslations`; `tests/test_inputs.py` →
  `TestEpsAccessor` and a key test.
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged).
- Verification: 1686 tests pass (1663 before, 23 new); `ruff check` and
  `mypy --strict` clean; `ruff format --check` flags only `tests/test_cache.py`
  (already flagged before CAP-12, not touched).
