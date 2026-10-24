# Task: Effy's Published Contract as a Copyable Symbol File

- **Status:** done — worker-inline 2026-10-23; closed by the human 2026-10-24
  ("Task 16 is done"), the open points below stand as written
- **Related ADRs:** \[ADR-021 (Amendment 2026-10-23), ADR-000 (Amendment
  2026-10-23), ADR-019, ADR-018\]
- **Dependencies:** [TASK-0009-config-attributes-on-recalculated-from] (done)

## Goal

One file, `custom_components/effy/effy_exports.py`, holds the symbols of what
Effy publishes (ADR-021): other projects copy it into their own namespace
without a dependency on Effy, and Effy imports the very same file, so the
contract is spelled out nowhere else. Human request 2026-10-23; every symbol
carries the prefix `EFFY_` (types: `Effy`).

## Acceptance Criteria

- Given the file, when it is loaded by path as a top-level module under any name
  (including a copy in another directory), then it imports and defines the same
  symbols; it imports only `typing`, uses no relative import and defines no
  function.
- Given every symbol of the file, then its name starts with `EFFY_` or `Effy`,
  and its value equals the pinned table in the test.
- Given `EffyPublishedSensor` and `EffyPublishedAttributes`, then their keys are
  exactly the `EFFY_KEY_*` and `EFFY_ATTR_*` values, `restored_*` and `sensors`
  are optional, and the literal types list exactly the value constants.
- Given the entries `describe_source_series` and `describe_standby` build, then
  their keys are exactly those of `EffyPublishedSensor`.
- Given the sources of the integration, then the contract literals (the two
  entity IDs, `schema_version`, `restored_as_of`, `restored_at`) appear quoted
  only in `effy_exports.py`.
- Given a running entry, then everything Effy publishes is unchanged: all 440
  tests that existed before still pass unchanged in behaviour.

## Estimated File / Module Footprint (hint, not a commitment)

`effy_exports.py` (new), `const.py`, `catalog.py`, `sensor.py`,
`tests/test_effy_exports.py` (new), `tests/test_catalog.py`, the loaders of
`tests/test_standby_sensor.py` and `tests/test_history_recent_equivalence.py`,
README, glossary.

## Definition of Done

- Tests green · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only)

## Consumed Interfaces

- `catalog.describe_source_series`, `describe_standby`, `base_attributes`,
  `published_attributes` from `custom_components/effy/catalog.py` (→ task:
  TASK-0009-config-attributes-on-recalculated-from)
- `EffyRecalculatedFromSensor._async_restore` and the entity-ID properties from
  `custom_components/effy/sensor.py` (→ task: TASK-0009)

## Delivered Artifacts

- `custom_components/effy/effy_exports.py` → constants
  `EFFY_PUBLISHED_SCHEMA_VERSION`, `EFFY_RECALCULATED_FROM_ENTITY_ID`,
  `EFFY_STANDBY_POWER_ENTITY_ID`,
  `EFFY_ATTR_{SCHEMA_VERSION,MAX_HISTORY_DAYS,SENSORS,RESTORED_AS_OF,RESTORED_AT}`,
  `EFFY_KEY_{ENTITY_ID,SERIES,SOURCE_ENTITY_ID,DIRECTION,ROLE,AGGREGATIONS}`,
  `EFFY_SERIES_{EFFECTIVE,DERIVED_POWER,SMOOTHED,STANDBY_POWER}`,
  `EFFY_DIRECTION_{INPUT,OUTPUT}`, `EFFY_ROLE_{PV,BMS}`,
  `EFFY_AGGREGATION_{MEAN,MIN,MAX,STATE}`, `EFFY_AGGREGATIONS`,
  `EFFY_STANDBY_AGGREGATIONS`; types `EffySeries`, `EffyDirection`, `EffyRole`,
  `EffyPublishedSensor`, `EffyPublishedAttributes`
- `const.py` → removed `PUBLISHED_SCHEMA_VERSION`, `EFFY_AGGREGATIONS`,
  `STANDBY_AGGREGATIONS` (now in `effy_exports.py`)
- `catalog.py` → removed `SERIES_*`, `DIRECTION_*`, `ROLE_*`; the two
  `describe_*` functions return `EffyPublishedSensor`; `describe_source_series`
  takes `series: EffySeries`
- `sensor.py` → uses the symbols; `_source_descriptor` takes `EffySeries`,
  returns `EffyPublishedSensor`
- `tests/test_effy_exports.py` → `TestTheFileIsCopyable`,
  `TestTheContractIsPinned`, `TestTheTypesMatchTheConstants`,
  `TestEffyHasNoSecondCopy`
- External dependencies added: none

## Open points for the human

- The types carry the prefix `Effy` (CamelCase), not `EFFY_`: Python names a
  class that way and ADR-000 §5 already prefixes Effy's classes `Effy`. Say if
  you want `EFFY_` on the types too.
- Only the accepted contract (ADR-021, ADR-018) is in the file. The efficiency
  series and `function` key of Draft ADR-023 are not, so that nothing unaccepted
  is copied downstream; they join with ADR-023.
- Home Assistant is not installed in the sandbox: `sensor.py` was exercised only
  through the existing stub-based tests, never against a real Home Assistant.
- The gate here: `pytest` 456 passed (440 before, 16 new); `ruff check` and
  `mypy --strict` show exactly the findings the repository already had (43 and
  3); `ruff format` clean.
