# Task: The EPS sensor in the waterfall path: input list, smoothing on the signed series, split in the slot loop

- **Status:** todo
- **Capability:** CAP-12
- **Related ADRs:** \[ADR-026 decisions 2, 3, 8, ADR-013 decision 2 and
  Amendment 2026-11-06, ADR-012, ADR-014, ADR-001, ADR-005\]
- **Dependencies:** \[TASK-0039-eps-signed-split-pure,
  TASK-0040-eps-picker-in-config\]
- **Gate:** Gate 2 pending (2026-11-06); ADR-026 is a Draft until the human
  confirms it.

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
- To be filled by the Lead at Phase 3 from the Delivered Artifacts of
  TASK-0039-eps-signed-split-pure (the split function: name, signature) and
  TASK-0040-eps-picker-in-config (`CONF_EPS_SENSOR`, `inputs.eps_id`).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- _(open)_
