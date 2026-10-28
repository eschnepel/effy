# Task: The charge and discharge efficiency sensors

- **Status:** done — 2026-10-27, reviewer PASS (inline)
- **Capability:** CAP-8
- **Related ADRs:** \[ADR-023 decision 2, ADR-021 (Amendments 2026-10-23),
  ADR-020\]
- **Dependencies:** \[TASK-0018-function-key-on-source-series,
  TASK-0020-efficiency-slot-feed-and-restore\]
- **Gate:** ADR-023 accepted 2026-10-27.

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

- `EffyStandbyPowerSensor`, `_StandbyExtraData`, `_standby_entities`,
  `describe_standby`, `EFFY_STANDBY_AGGREGATIONS` (→ TASK-0005, TASK-0009).
- From TASK-0020: `EffyCoordinator.restore_efficiency(data) -> RunState`,
  `subscribe_efficiency(cb) -> unsubscribe`, `standby_state`, `grid_import_id`,
  `discharge_id`, `charge_id`, `grid_export_id` (→ TASK-0017).
- From TASK-0019: `RunState`, `EffEstimate`, `run_restore_data`,
  `run_state_from_restore`.
- From TASK-0018: `catalog.py`'s conventions (`function` only on a source
  series).

## Delivered Artifacts

- `effy_exports.py`: `EFFY_CHARGE_EFFICIENCY_ENTITY_ID`
  (`"sensor.effy_charge_efficiency"`), `EFFY_DISCHARGE_EFFICIENCY_ENTITY_ID`,
  `EFFY_SERIES_CHARGE_EFFICIENCY` (`"charge_efficiency"`),
  `EFFY_SERIES_DISCHARGE_EFFICIENCY`, `EFFY_EFFICIENCY_AGGREGATIONS` (`mean`,
  `min`, `max`); `EffySeries` has the two new values. The pinned table and the
  literal guard of `test_effy_exports.py` cover them.
  `EFFY_PUBLISHED_SCHEMA_VERSION` is still 1 (additive).
- `catalog.py`: `describe_efficiency(entity_id, series)`: output, role `bms`,
  `source_entity_id` `None`, aggregations `[mean, min, max]`, no `function`.
- `sensor.py`: `EffyEfficiencySensor(hass, entry, coordinator, kind)` with
  `kind` `charging` or `discharging`; `_EfficiencyExtraData`;
  `_efficiency_entities(hass, entry, coordinator)` (created in
  `async_setup_entry` after the standby sensor; removes a registered sensor
  whose pair is gone); `_efficiency_unique_id` (`{entry_id}_charge_efficiency` /
  `{entry_id}_discharge_efficiency`).
- Behaviour ◇: the state is `estimate × 100`, rounded to 0.1, not capped;
  `available` needs the sensor's own measurement and a standby estimate on the
  coordinator, and follows both (the sensor also subscribes to the standby
  state); attributes `last_counted_value` (in %, 0.1), `last_counted_end`,
  `counted_kwh` (0.001), `counted_runs`, `last_processed_slot`; the stored extra
  data is the whole machine dump, as ADR-023 Open decision 4 was settled.
- README (the section "Charge and discharge efficiency", the published list).
- No translation files changed: the integration has no entity-name translations
  (the standby sensor's name is `_attr_name` too), and no option text changed in
  this task.
- Tests: `tests/test_efficiency_sensor.py` (23), `tests/test_catalog.py`,
  `tests/test_effy_exports.py`.
- External dependencies added: none.

## Review

**PASS** (inline; 797 tests, ruff, mypy on source and tests). Each acceptance
criterion has its tests: existence per pair, including a half pair and the
removal of a registered sensor (1); unavailable until a measurement and a
standby value, the state in % rounded to 0.1 and not capped, measurement without
a device class (2); the attributes (3); the whole dump stored, restored and
handed to the coordinator, unusable data starting empty (4); the published entry
with series, direction, role, null source, `[mean, min, max]` and no `function`
(5); the README (6). Fourteen mutants of the sensor are caught (no ×100,
rounding to a whole number, a cap at 100, standby ignored, available without a
measurement, kinds swapped, unrounded `counted_kwh`, the last value as a
fraction, no standby subscription, a restore not handed over, a pair as "or", a
stale sensor never removed, not published, a device class).

Another second spelling caught by the TASK-0018 guard: the unique-id suffix
first spelled the series name again; it now uses the series constant.

Not verifiable in the sandbox: that the recorder really compiles `mean`, `min`
and `max` for a `%` measurement sensor without a device class (it does for any
sensor with a state class and a numeric state, per ADR-020), and the sensors'
appearance in Home Assistant.
