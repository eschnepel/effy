# Task: Publish Effy's Configuration as Attributes of the Recalculated-From Sensor

- **Status:** done — reviewer PASS 2026-10-18 (inline, iteration 1 of 3)
- **Related ADRs:** \[ADR-021, ADR-012 (Amendment 2026-10-18), ADR-003, ADR-011,
  ADR-016, ADR-018, ADR-019, ADR-020\]
- **Dependencies:** \[TASK-0008-split-input-sensors-by-role,
  TASK-0005-standby-power-sensor\] (TASK-0005 is in `review`; the human decided
  2026-10-18 that TASK-0009 depends only on its delivered artifacts, which
  exist)

## Goal

`sensor.effy_recalculated_from` carries Effy's configuration as attributes
(`schema_version`, `max_history_days`, `sensors`) so that Shady and Chargy can
discover Effy's sensors and history days by reading one entity, and it is the
last step of every recalculation, so a listener sees final data. After a reboot
every Effy sensor restores its state, and the recalculated-from sensor also
restores its attributes and marks them as restored. The contract is ADR-021;
this task implements it and documents it.

## Acceptance Criteria

**Content.** Fixture: one PV input with an energy-family source, one BMS input
with a power-family source, one output with an energy-family source.

- Given that entry after a closing step, when the attributes are read, then
  `schema_version` is `1`, `max_history_days` equals the configured value, and
  `sensors` has exactly these entries and no other (the sensor itself is not
  listed): the PV input's `effective` and `derived_power`; the BMS input's
  `effective` and `smoothed`; the output's `derived_power`; and `standby_power`.
- Given the entries, then `direction` is `input` for inputs and `output` for the
  output; `role` is `pv` or `bms` per the input list, `bms` for the output;
  `source_entity_id` is the configured source; every key of ADR-021 decision 3
  is present and there is no `unit` key.
- Given a configuration without a BMS input, then there is no `standby_power`
  entry; given a BMS input, then that entry has `source_entity_id: null`,
  `direction: output`, `role: bms`.

**Aggregations.**

- Given the Effy series (effective, derived power, smoothed), then
  `aggregations` equals the one constant, and a test fails when the keys of the
  rows the history writer builds (short-term and the hourly rewrite) differ from
  that constant.
- Given the standby sensor, then `aggregations` is `[mean, min, max]` — or, if
  reading Home Assistant's source shows otherwise, the corrected value with an
  amendment to ADR-021 raised to the Lead Agent before merge.

**Late and removed sensors.**

- Given a derived or smoothed sensor created after setup (late binding), when
  the next closing step runs, then its entry is listed; before it, it is not.
- Given an Effy sensor removed from Home Assistant, when the next closing step
  runs, then its entry is gone.

**Closing step.**

- Given a slot job, when it completes, then `set_recalculated_from` was called
  after `notify_updated`, after the hour rewrite (when the slot completes an
  hour) and after the standby update; given the manual history rewrite, after
  `notify_updated`.
- Given an hour rewrite or standby update that fails (logged), then the closing
  step still happens; given a recalculation that raises, or one that touches
  nothing, then it does not, and the attributes are not refreshed.
- Given two closing steps with equal timestamp and attributes, then two
  `state_changed` events fire (`force_update`).

**Restore of all Effy sensors.**

- Given each of the effective, derived power and smoothed sensors with a
  previous state (value, unit, attributes), when Home Assistant restarts, then
  value, unit and attributes equal the previous ones, and there is no
  `restored_*` attribute.
- Given a previous state of `unknown` or `unavailable`, then that sensor starts
  `unknown`.
- Given the standby power sensor, then its restore behaves as before (ADR-018).
- Given a restored state, then nothing writes a statistic at restore time; the
  first `notify_updated` push replaces the restored value.

**Restore of the recalculated-from sensor.**

- Given a previous state with a timestamp and these attributes, when Home
  Assistant restarts, then the sensor's state and all attributes (including
  `sensors`, `max_history_days`, `schema_version`) equal the previous ones, plus
  `restored_as_of` (the previous state's `last_updated`) and `restored_at` (the
  restoration time).
- Given a restored sensor, when the next closing step writes, then
  `restored_as_of` and `restored_at` are gone and the list is current.
- Given no previous state, or a last state of `unknown` or `unavailable`, then
  the state is `unknown`, `sensors` is absent, `schema_version` and
  `max_history_days` are present, and there is no `restored_*` attribute.
- Given a changed `max_history_days` option (the entry reloads), then the
  restored attribute is the old value until the next closing step and the new
  value after it.

**Documentation and regression.**

- The README documents the contract in a short section for other integrations,
  corrects the statistics paragraph ("mean only") to `mean` and `state`, and the
  ADR table lists ADR-021; `GLOSSARY.md` terms are used as written.
- All existing tests stay green; `ruff check`, `ruff format --check`, `mypy` and
  `mdformat --check` are clean with the locked toolchain.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/sensor.py`: descriptor registration in the Effy sensor
  classes, restore of `EffySensor`, `EffyDerivedPowerSensor` and
  `EffySmoothedSensor` (value, unit, attributes; Home Assistant's
  `RestoreSensor` mixin is a natural fit), `EffyRecalculatedFromSensor` (restore
  entity, attributes, `force_update`)
- `custom_components/effy/coordinator.py`: descriptor registry, call order in
  `_async_recalculate_recent_and_report` and
  `async_recalculate_history_and_report`
- `custom_components/effy/const.py`: `schema_version`, the Effy aggregations
  constant; likely a new pure module that imports only `const` (like
  `inputs.py`) for building a descriptor
- `custom_components/effy/history.py` only if the aggregations constant is used
  by the writer
- `README.md`, `tests/` (new tests; the existing slot-job order tests change)

## Definition of Done

- Tests green · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none
  expected)
- ADR-021 is Accepted; a correction from its unverified points is applied by
  amendment before merge

## Consumed Interfaces

- `pv_input_ids(options)`, `bms_input_ids(options)`, `all_input_ids(options)`
  from `custom_components/effy/inputs.py`; `CONF_PV_INPUT_SENSORS`,
  `CONF_BMS_INPUT_SENSORS` from `custom_components/effy/const.py`;
  `EffyCoordinator.pv_input_ids`, `.bms_input_ids` from
  `custom_components/effy/coordinator.py` (→ task:
  TASK-0008-split-input-sensors-by-role)
- `EffyStandbyPowerSensor`, `standby_unique_id(entry)`, `_standby_entities`,
  `_StandbyExtraData` (the restore pattern to follow) from
  `custom_components/effy/sensor.py`; `EffyCoordinator.standby_enabled`,
  `_async_update_standby(now)` (called last in the slot job) from
  `custom_components/effy/coordinator.py` (→ task:
  TASK-0005-standby-power-sensor)
- `EffyCoordinator.set_recalculated_from(ts)`,
  `subscribe_recalculated_from(cb)`, `.recalculated_from`,
  `notify_updated(touched, last_values)`,
  `_async_recalculate_recent_and_report(slot)`,
  `async_recalculate_history_and_report()`, `_async_rewrite_hour` from
  `custom_components/effy/coordinator.py` (→ task:
  TASK-0006-recorder-synchronized-slot-trigger)
- `EffySensor`, `EffyDerivedPowerSensor`, `EffySmoothedSensor`,
  `EffyRecalculatedFromSensor`, `_build_derived_entities`,
  `_add_late_derived_entities` from `custom_components/effy/sensor.py`
  (pre-ADR-018 code; no task slug)

## Delivered Artifacts

- `custom_components/effy/const.py` → constants `PUBLISHED_SCHEMA_VERSION`
  (`1`), `EFFY_AGGREGATIONS` (`("mean", "state")`), `STANDBY_AGGREGATIONS`
  (`("mean", "min", "max")`)
- `custom_components/effy/catalog.py` (new, pure, imports only `const`) →
  constants `SERIES_EFFECTIVE`, `SERIES_DERIVED_POWER`, `SERIES_SMOOTHED`,
  `SERIES_STANDBY_POWER`, `DIRECTION_INPUT`, `DIRECTION_OUTPUT`, `ROLE_PV`,
  `ROLE_BMS`; functions
  `describe_source_series(entity_id, series, source_entity_id, *, pv_input_ids, bms_input_ids, output_ids)`,
  `describe_standby(entity_id)`, `base_attributes(max_history_days)`,
  `published_attributes(descriptors, max_history_days)`
- `custom_components/effy/coordinator.py` →
  `EffyCoordinator.register_published(descriptor) -> Callable[[], None]`,
  `EffyCoordinator.published_descriptors() -> list[dict]`; the closing step is
  now the last call of `_async_recalculate_recent_and_report` and of
  `async_recalculate_history_and_report`
- `custom_components/effy/sensor.py` → `EffySensor`, `EffyDerivedPowerSensor`,
  `EffySmoothedSensor` are now `(RestoreEntity, SensorEntity)`: they restore
  value, unit (and, for `EffySensor`, its three attributes) and register /
  unregister their descriptor;
  `EffyRecalculatedFromSensor(RestoreEntity, SensorEntity)` with
  `_attr_force_update`, `extra_state_attributes`, `_async_restore`,
  `_on_recalculated_from`; `EffyStandbyPowerSensor` registers / unregisters its
  descriptor; module helpers `_NO_STATE`, `_EFFECTIVE_ATTRIBUTE_KEYS`,
  `_utcnow`, `_async_restore_measurement`, `_max_history_days`,
  `_source_descriptor`
- `README.md` → "Effy sensors" (renamed from "Output entities"), corrected
  statistics paragraph, new "For other integrations" section, ADR table row 021
- Tests: new `tests/test_catalog.py` (14), `tests/test_published_sensors.py`
  (63); added `TestClosingStep` (5) and `TestPublishedRegistry` (4) to
  `tests/test_coordinator_slot.py`, one aggregations test to
  `tests/test_history_recent_equivalence.py`; the standby test double gained
  `register_published` and `async_get_last_state` in
  `tests/test_standby_sensor.py`
- ADR-021: two amendments (verification against Home Assistant 2026.2.3;
  `restored_as_of` keeps the data age after a second reboot, confirmed by the
  human)
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged)

Worker decisions inside the ADR, for the Lead's attention: a recalculation that
finished before the sensor was added takes over only the timestamp and keeps the
restored attributes (the other sensors' descriptors may not be registered yet);
`sensors` is sorted by `entity_id`; an unconfigured source raises `ValueError`.
