# Task: The charge and discharge efficiency sensors

- **Status:** todo
- **Capability:** CAP-8
- **Related ADRs:** \[ADR-023 (Draft) decision 2, ADR-021 (Amendments
  2026-10-23), ADR-020\]
- **Dependencies:** \[TASK-0018-function-key-on-source-series,
  TASK-0020-efficiency-slot-feed-and-restore\]
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

## Goal

`sensor.effy_charge_efficiency` and `sensor.effy_discharge_efficiency`, in %,
fed by the coordinator, restored after a restart, listed in the published
configuration.

## Acceptance Criteria

- Given grid import and charge configured, then `sensor.effy_charge_efficiency`
  exists (and `sensor.effy_discharge_efficiency` with discharge and grid
  export); otherwise not.
- Given no first measurement, or no `sensor.effy_standby_power` value, then the
  sensor is `unavailable` (not unknown); given a measurement, then its state is
  the estimate in % on a 0 to 100 scale, rounded to 0.1, state class
  measurement, no device class, recorded with the recorder's own mean/min/max
  statistics (Effy writes none).
- Given the sensor, then its attributes are `last_counted_value`,
  `last_counted_end`, `counted_kwh`, `counted_runs` and `last_processed_slot`.
- Given a restart, then the state and the machine's part of the state are
  restored (ExtraStoredData) and handed to the coordinator; an unusable restore
  starts empty.
- Given the published configuration, then each sensor appears with series
  `charge_efficiency` / `discharge_efficiency`, direction `output`, role `bms`,
  `source_entity_id` null, aggregations `[mean, min, max]`, no `function` key;
  the series symbols join `effy_exports.py` and its pinned test.
- Given the README and the translations (en, de), then both sensors and their
  rules are described.

## Estimated File / Module Footprint (hint, not a commitment)

`sensor.py`, `catalog.py` (`describe_efficiency`), `effy_exports.py`,
`const.py`, `translations/*.json`, README, `tests/test_efficiency_sensor.py`,
`tests/test_effy_exports.py`, `tests/test_catalog.py`.

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

- `EffyStandbyPowerSensor`, `_StandbyExtraData`, `describe_standby` from
  `custom_components/effy/sensor.py` and `catalog.py` (→ TASK-0005, TASK-0009)
- `restore_efficiency`, `subscribe_efficiency`, the machine's restore functions
  (→ TASK-0020, TASK-0019) — **filled by the Lead before start**
- the `function` conventions of `catalog.py` (→ TASK-0018) — **filled by the
  Lead before start**

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->
