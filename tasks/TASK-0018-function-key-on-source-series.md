# Task: Publish the `function` of every source series

- **Status:** done — 2026-10-27, reviewer PASS (inline)
- **Capability:** CAP-7
- **Related ADRs:** \[ADR-023 decision 11, ADR-021 (Amendment 2026-10-23, both),
  ADR-019\]
- **Dependencies:** [TASK-0017-counter-roles-in-config]
- **Gate:** ADR-023 accepted 2026-10-27.

## Goal

Every source series in the published `sensors` list says which function its
source has (`grid_import`, `discharge`, `charge`, `grid_export`, or null for a
PV input), so Shady and Chargy pick by function. The symbols join
`effy_exports.py` with the `EFFY_` prefix; `schema_version` stays 1.

## Acceptance Criteria

- Given `effy_exports.py`, then it defines `EFFY_KEY_FUNCTION`,
  `EFFY_FUNCTION_GRID_IMPORT`, `EFFY_FUNCTION_DISCHARGE`,
  `EFFY_FUNCTION_CHARGE`, `EFFY_FUNCTION_GRID_EXPORT` and the type
  `EffyFunction`; `EffyPublishedSensor` has `function` as a `NotRequired` key;
  the pinned table and the type tests are updated on purpose;
  `EFFY_PUBLISHED_SCHEMA_VERSION` is still 1.
- Given a PV input, then its effective, derived-power and smoothed entries carry
  `function: None`; given a BMS input or output, then they carry the function of
  its role.
- Given the standby entry, then it has no `function` key.
- Given a restored `recalculated_from` state from before this task, then it is
  restored as before (entries without `function` stay valid) until the next
  recalculation replaces it.
- Given the README section on the published configuration, then the example and
  the key list show `function`.
- Given the contract tests, then no quoted contract literal appears outside
  `effy_exports.py`.

## Estimated File / Module Footprint (hint, not a commitment)

`effy_exports.py`, `catalog.py`, `sensor.py` (descriptor construction),
`tests/test_effy_exports.py`, `tests/test_catalog.py`,
`tests/test_published_sensors.py`, README.

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

- role accessors `grid_import_id`, `discharge_id`, `charge_id`, `grid_export_id`
  from `custom_components/effy/inputs.py` (→ task: TASK-0017-counter-roles-in-
  config) — **filled by the Lead from TASK-0017's Delivered Artifacts before
  this task starts**
- `catalog.describe_source_series`, `describe_standby` from
  `custom_components/effy/catalog.py` (→ TASK-0009-config-attributes-on-
  recalculated-from, TASK-0016-effy-exports-contract-file)

## Delivered Artifacts

- `effy_exports.py`: `EFFY_KEY_FUNCTION` (`"function"`),
  `EFFY_FUNCTION_GRID_IMPORT` (`"grid_import"`), `EFFY_FUNCTION_DISCHARGE`,
  `EFFY_FUNCTION_CHARGE`, `EFFY_FUNCTION_GRID_EXPORT`, the type `EffyFunction`;
  `EffyPublishedSensor.function: NotRequired[EffyFunction | None]`.
  `EFFY_PUBLISHED_SCHEMA_VERSION` is still 1.
- `catalog.py`:
  `describe_source_series(entity_id, series, source_entity_id, *, pv_input_ids, grid_import_id, discharge_id, charge_id, grid_export_id)`
  (the `bms_input_ids` and `output_ids` parameters are gone); the entry carries
  `function` between `role` and `aggregations`: the role's function, `None` for
  a PV input. `describe_standby` is unchanged (no `function` key).
- `sensor.py`: `_source_descriptor` passes the coordinator's four role ids.
- README (example and key table), ADR-021 amendment heading.
- Tests: `test_effy_exports.py` (pinned table, optional key, `EffyFunction`,
  schema version 1, the four literals spelled out only in the file),
  `test_catalog.py`, `test_published_sensors.py` (every series sensor, the
  closing step, a restored state from before the key).
- External dependencies added: none.

## Review

**PASS** (inline; 683 tests, ruff, mypy). Each acceptance criterion has its
test: the contract table and types (1), `function` per role and `None` for PV
(2), no key on the standby entry (3), a pre-task restored state stays valid
until the next closing step (4), README (5), no quoted literal outside the file
(6).

**Finding for the human, not changed here:** the contract publishes
`aggregations: [mean, state]` for the source series (`EFFY_AGGREGATIONS`) and
the README says so. TASK-0030 patch 2 showed that Home Assistant 2026.10.4 does
not return `state` through `statistics_during_period` for a series whose
metadata has `has_sum` false, which Effy's always has. A reader that asks for
`state` gets nothing. Whether the value is stored is unverified. Changing a
published value is a contract decision; it needs the human.
