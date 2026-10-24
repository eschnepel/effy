# Task: Options for state of charge, battery capacity and valley hysteresis

- **Status:** todo
- **Capability:** CAP-9
- **Related ADRs:** [ADR-023 (Draft) decision 1 and 6]
- **Dependencies:** [TASK-0017-counter-roles-in-config]
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

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

- `EffyConfigFlow`, `EffyOptionsFlow`, `_validate_user_input` and the role
  accessors (→ TASK-0017) — **filled by the Lead before start**

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->
