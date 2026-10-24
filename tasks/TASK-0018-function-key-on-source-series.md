# Task: Publish the `function` of every source series

- **Status:** todo
- **Capability:** CAP-7
- **Related ADRs:** \[ADR-023 (Draft) decision 11, ADR-021 (Amendment
  2026-10-23, both), ADR-019\]
- **Dependencies:** [TASK-0017-counter-roles-in-config]
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

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

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->
