# Task: The two EPS Effy sensors exist and are published with role and function `eps`

- **Status:** todo
- **Capability:** CAP-12
- **Related ADRs:** [ADR-026 decision 6, ADR-021 and Amendment 2026-11-06, ADR-023 decision 11, ADR-013 decision 2]
- **Dependencies:** [TASK-0041-eps-split-in-slot-loop]
- **Gate:** Gate 2 pending (2026-11-06); ADR-026 is a Draft until the human confirms it.

## Goal

The EPS sensor's two Effy sensors (`sensor.effy_<slug>`, the effective value of
the input half, and `sensor.effy_<slug>_smoothed`, the signed series) are
created by the code that builds them for a power-family input, restored and
pushed like the others, and appear in the published `sensors` list with the
new additive values.

## Acceptance Criteria

- Given an EPS sensor, then `EffySensor` and `EffySmoothedSensor` exist for it,
  also when its source has no state at setup (the late-binding path), with the
  naming of the other sensors; the smoothed one is device class power, in the
  source's unit, and its restore and live push work as for any smoothed sensor.
- Given the published `sensors` list, then the EPS sensor has two entries:
  `series` `effective`, `direction` `input`, and `series` `smoothed`, `direction`
  `both`; both have `role` `eps`, `function` `eps`, `source_entity_id` the EPS
  sensor and the aggregations of the other source series.
- Given `effy_exports.py`, then it defines `EFFY_ROLE_EPS` (`"eps"`),
  `EFFY_FUNCTION_EPS` (`"eps"`) and `EFFY_DIRECTION_BOTH` (`"both"`), the literals
  `EffyRole`, `EffyFunction`, `EffyDirection` carry them, the sign convention of
  the signed series is stated in the file's comments, and
  `EFFY_PUBLISHED_SCHEMA_VERSION` stays 1; the file keeps its rules (standard
  library only, no import from Effy, prefixed symbols).
- Given the test that pins every contract value and fails on a contract literal
  spelled out in another module, then it is extended and green.
- Given the coordinator, then it exposes `eps_id` for the catalog like the four
  counter IDs.
- Given the README section "For other integrations", then the new role, function
  and direction and the sign convention are described.
- Given an entry without an EPS sensor, then the published list is unchanged.

## Estimated File / Module Footprint (hint, not a commitment)

`effy_exports.py`, `catalog.py` (`describe_source_series` gains an EPS branch),
`coordinator.py` (`eps_id`), `sensor.py` (`_source_descriptor`, verification of
the entity building), `README.md`, `tests/test_effy_exports.py`,
`tests/test_catalog.py`, `tests/test_published_sensors.py`,
`tests/test_sensor_setup.py`.

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

- Pre-existing, `catalog.py`: `describe_source_series(entity_id, series,
  source_entity_id, *, pv_input_ids, grid_import_id, discharge_id, charge_id,
  grid_export_id)`, `published_attributes`.
- Pre-existing, `sensor.py`: `_source_descriptor`, `_build_derived_entities`,
  `_add_late_derived_entities`, `EffySensor`, `EffySmoothedSensor`.
- Pre-existing, `effy_exports.py`: the `EFFY_ROLE_*`, `EFFY_FUNCTION_*`,
  `EFFY_DIRECTION_*` symbols and the `Effy*` literals.
- To be filled by the Lead at Phase 3 from the Delivered Artifacts of
  TASK-0041-eps-split-in-slot-loop.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- _(open)_
