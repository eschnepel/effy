# Task: The PV sensors in the published list and effy_exports.py

- **Status:** todo
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-021]
- **Dependencies:** \[TASK-0045a-pv-combined-series,
  TASK-0045d-pv-delta-integral-sensors\]
- **Gate:** Gate 2 pending (re-scoped by the human in a design interview on
  2026-11-07; split from the held TASK-0045). No code before approval.

## Goal

The four kinds of new sensors appear in the published `sensors` list with the
role, series and aggregations the contract names, and `effy_exports.py` carries
the new literals.

## Acceptance Criteria

- Given the published list, then it holds these entries, `role` `pv`,
  `direction` `input`, no `function` key:

  | Sensor | `series` | `source_entity_id` | Unit | Aggregations |
  | -- | -- | -- | -- | -- |
  | Effective PV Sum now | `pv_sum` | none | W | `mean`, `state` |
  | per PV input | `pv_delta_integral` | the PV input | Wh or kWh | `state`, `sum`, `change` |
  | combined today | `pv_delta_integral_today` | none | kWh | `state`, `sum`, `change` |
  | combined total | `pv_delta_integral_total` | none | kWh | `state`, `sum`, `change` |

- Given the entries, then `schema_version` stays 1, the list is sorted like
  before and the existing contract tests pass unchanged.

- Given `effy_exports.py`, then it has the four series values and the `change`
  aggregation as constants, and a copy of it still works without Effy installed.

- Given a sensor that is removed from the registry, then its entry leaves the
  list.

## Decisions

- ADR-027 decision 7 and the amendment of ADR-021 (human, 2026-11-07).

## Estimated File / Module Footprint (hint, not a commitment)

`catalog.py`, `effy_exports.py`, `sensor.py` (registering the descriptors), and
tests for each (`test_catalog`, `test_effy_exports`, `test_published_sensors`).

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (translations en and de for sensor
  names) · no open ADR conflicts (ADR-027, decision 9, is the only open point)
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-07). -->

- `catalog.describe_source_series`, `catalog.describe_efficiency`,
  `catalog.describe_standby`, `catalog.published_attributes`.
- `effy_exports.EffySeries`, `EFFY_AGGREGATIONS`,
  `EFFY_EFFICIENCY_AGGREGATIONS`, `EFFY_PUBLISHED_SCHEMA_VERSION`.
- `coordinator.register_published`, `coordinator.published_descriptors`.
- The sensors (→ task: TASK-0045a-pv-combined-series and
  TASK-0045d-pv-delta-integral-sensors).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
