# Task: The EPS sensor in the waterfall path: input list, smoothing on the signed series, split in the slot loop

- **Status:** done
- **Capability:** CAP-12
- **Related ADRs:** \[ADR-026 decisions 2, 3, 8, ADR-013 decision 2 and
  Amendment 2026-11-06, ADR-012, ADR-014, ADR-001, ADR-005\]
- **Dependencies:** \[TASK-0039-eps-signed-split-pure,
  TASK-0040-eps-picker-in-config\]
- **Gate:** Gate 2 approved by the human (2026-11-06); ADR-026 accepted.

## Goal

With an EPS sensor configured, the history recalculation and the slot job count
the EPS output as an output and the EPS input as an input: the formula of
ADR-026 decision 2, on the smoothed signed series (decision 3). One function
serves both paths, so the CAP-1 invariant (the slot job equals a full
recalculation) must hold with the sensor.

## Acceptance Criteria

- Given an EPS sensor, then `all_input_ids` ends with it (after the PV and BMS
  inputs) and `output_ids` does not contain it; given none, then both are as
  before.
- Given the EPS sensor's statistic `mean` in a slot, then the slot loop turns
  the signed value into the two halves (TASK-0039's function): the input half
  replaces the sensor's reading in the input readings, the output half is added
  to the output readings; the sensor is fetched, smoothed and indexed once.
- Given an EPS output of 200 W, then the slot's total loss is 200 W lower than
  without the sensor; given an EPS input of 150 W, then the input total is 150 W
  higher and the EPS input takes an equal share; the effective value of
  `sensor.effy_<slug>` is the effective value of the input half (0 while the
  port exports).
- Given a gap of one or two slots in the EPS sensor, also across a change of
  sign, then the signed series is interpolated before the split and both halves
  see the filled value; given a longer gap, then it stays a gap; the smoothed
  statistic written for `sensor.effy_<slug>_smoothed` is the signed series.
- Given an entry without an EPS sensor, then full recalculation and slot timer
  give results identical to before (the TASK-0004 harness, unchanged and green).
- Given an EPS sensor, then after every cycle of the slot timer the written
  closed slots equal a full recalculation at the same time, for the effective
  and the smoothed EPS series and for every other series (the harness extended
  with one EPS case).
- Given the live-state push (`notify_updated`) and the coordinator's use of
  `all_input_ids`, then both EPS entities are covered as any power-family
  input's; any place where the signed value would be read as an input value (a
  negative power) is found and handled, and listed in the Delivered Artifacts.
- Given the standby, efficiency and battery feeds, then they are not changed in
  this task.

## Estimated File / Module Footprint (hint, not a commitment)

`inputs.py` (`all_input_ids`), `history.py` (`_compute_effective_slots`, the
slot loop), possibly `coordinator.py` and `sensor.py` (verification first),
`tests/test_history_recent_equivalence.py`,
`tests/test_history_compute_edges.py`, `tests/test_coordinator_slot.py`, one new
`tests/test_eps_waterfall.py`.

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

- Pre-existing, `history.py`: `_compute_effective_slots` (the slot loop calls
  `_readings_for_slot` for the inputs and the outputs, `distribute_loss`,
  `effective_in_original_unit`; the smoothing loop `for eid in input_ids` calls
  `interpolate_slot_gaps` and merges filled rows into `indexed` with
  `setdefault`), `_readings_for_slot`, `_effy_entity_id`,
  `_effy_smoothed_entity_id`.
- Pre-existing, `calculation.py`: `INTERPOLATION_MAX_GAP_SLOTS` (2),
  `interpolate_slot_gaps`.
- From TASK-0039-eps-signed-split-pure:
  `calculation.split_signed_reading(reading: SensorReading) -> tuple[SensorReading, SensorReading]`
  from `custom_components/effy/calculation.py`; returns
  `(input_half, output_half)`, both keep `entity_id` and `original_unit`, no
  unit conversion, the input half is returned even when 0.
- From TASK-0040-eps-picker-in-config: `const.CONF_EPS_SENSOR` (`"eps_sensor"`)
  and `inputs.eps_id(options) -> str | None` from
  `custom_components/effy/inputs.py`; until this task `all_input_ids`,
  `bms_input_ids`, `output_ids` and `pv_input_ids` do not contain the EPS
  sensor, and a test (`TestEpsAccessor.test_the_eps_sensor_is_in_no_pool_yet`)
  pins that; this task changes the pin on purpose.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- `custom_components/effy/calculation.py` → function
  `split_eps_readings(eps_entity_id: str, input_readings: list[SensorReading], output_readings: list[SensorReading]) -> tuple[list[SensorReading], list[SensorReading]]`
  (new, not in the footprint hint; pure, so the list-level rule is tested with
  no mocking): the EPS reading among the inputs is replaced in place by its
  input half (even when 0), its output half is appended to the outputs when not
  zero; a slot without an EPS reading, or the other readings, are untouched; the
  given lists are not mutated. Module-level use of `split_signed_reading` from
  TASK-0039.
- `custom_components/effy/inputs.py` → `all_input_ids(options)` now returns PV,
  then BMS, then the EPS sensor last (if set); `eps_id` docstring updated;
  `bms_input_ids`, `output_ids`, `pv_input_ids` unchanged.
- `custom_components/effy/history.py` → `_compute_effective_slots`: imports
  `eps_id` and `split_eps_readings`; after the readings of a slot are built, if
  an EPS sensor is configured the two reading lists go through
  `split_eps_readings`; a comment on the smoothing loop records that the EPS
  sensor is the one output-side exception (its signed series is interpolated
  before the split). No other function in `history.py` changed: the EPS sensor,
  being in `input_ids`, is fetched once, interpolated by the existing loop, gets
  an effective series (`_effy_entity_id(eps)`, the effective value of the input
  half,
  > = 0, unit W/kW) and a smoothed series (`_effy_smoothed_entity_id(eps)`,
  > signed).
- Verified, no code change needed: `coordinator._input_ids` is assigned and
  never read (the live path is disabled); no code in `sensor.py`,
  `coordinator.py`, `sensor_utils.py` or `recorder_storage.py` clamps or
  reinterprets a negative power; `EffySensor` and `EffySmoothedSensor` are
  created for every member of `all_input_ids` by `sensor.async_setup_entry` and
  `_build_derived_entities`, so the EPS sensor gets both (TASK-0043 pins that
  and the published entries). The other readers of `all_input_ids` in
  `history.py` (`async_probe_last_slot_ready`, `async_read_drain_activity`, the
  efficiency energy read) behave as follows: the probe includes the EPS sensor;
  the efficiency energy read only looks at the four counters and is unaffected;
  `async_read_drain_activity` now sees the EPS sensor through its smoothed
  series (`mean != 0`), which TASK-0042 pins.
- Not yet done by design (TASK-0043): `describe_source_series` does not know the
  EPS sensor, so until then its published entry carries the classification a
  source that is neither PV nor a counter gets today.
- `tests/test_eps_waterfall.py` (new) → `TestTheEpsSensorInTheWaterfall`,
  `TestSmoothingTheSignedSeries`,
  `TestSlotTimerEqualsFullRecalcWithTheEpsSensor` (built on the recorder world
  of `test_history_recent_equivalence`); `tests/test_calculation.py` →
  `TestSplitEpsReadings`; `tests/test_inputs.py` → the pin of TASK-0040 replaced
  by `test_the_eps_sensor_is_the_last_input_and_in_no_other_pool` and
  `test_an_eps_sensor_alone_is_the_only_input`.
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged).
- Verification: 1707 tests pass (1686 before, 21 new); `ruff check` and
  `mypy --strict` clean; `ruff format --check` flags only `tests/test_cache.py`
  (already flagged before CAP-12, not touched).
