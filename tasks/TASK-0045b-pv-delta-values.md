# Task: PV delta of every run, from the stored values

- **Status:** todo
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-024, ADR-002]
- **Dependencies:** [TASK-0045a-pv-combined-series]
- **Gate:** Gate 2 approved by the human (2026-11-09, with Q27 = A); re-scoped
  in a design interview on 2026-11-07, split from the held TASK-0045.

## Goal

Every recalculation reports, per PV input, how much energy it changed in the
stored rows of that input's effective series (the PV delta), computed from the
values that were stored before. No sensor uses it yet.

## Acceptance Criteria

- Given a run that writes rows, then the PV delta of an input is the addition of
  `(new − stored) × 5/60` over the rows it wrote, in Wh for an input whose
  effective unit is W and in kWh for kW. Example: stored 300 W is rewritten to
  290 W and a new slot gets 310 W: `(−10 + 310) × 5/60 = 25 Wh`.
- Given unchanged rows, then they add 0; given a run that writes nothing, then
  every PV input reports 0. Hourly rows do not count.
- Given the stored rows, then `history._unchanged_slots` returns their values as
  well as the unchanged starts, with no additional recorder read (a test counts
  the reads).
- Given a stored row in another unit than the new one, then it is converted by
  the unit in its metadata; given no metadata, then its value counts as 0.
- Given the stored rows cannot be read, then the run reports no delta for any
  input (not 0), logs a warning and writes every row as before.
- Given an input the caller marks as having no base yet, then it is left out of
  the result.
- Given `async_recalculate_recent` and `async_recalculate_history`, then both
  return the deltas, and `coordinator` receives them and books nothing (booking
  is TASK-0045d); all existing tests pass unchanged apart from the result shape.
- Given the EPS sensor and the BMS inputs, then they are not reported.
- A full recalculation over an empty recorder reports, per input, the energy of
  all its rows (equivalence test).

## Decisions

- ADR-027 decision 2 (human, 2026-11-07): deltas are booked only after the write
  has succeeded; that is the coordinator's job in TASK-0045d.

## Estimated File / Module Footprint (hint, not a commitment)

`history.py`, `calculation.py` (a pure delta function), `coordinator.py`
(receives the result), and tests for each.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (translations en and de for sensor
  names) · no open ADR conflicts (ADR-027 decision 9 decided: Q27 = A)
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-07). -->

- `history._compute_effective_slots(hass, options, start, end, ...)`,
  `history.async_recalculate_history`, `history.async_recalculate_recent` in
  `custom_components/effy/history.py`; `history._effy_entity_id`,
  `history._effy_smoothed_entity_id`.
- `inputs.pv_input_ids(options)` in `custom_components/effy/inputs.py`.
- `history._unchanged_slots`, `history._write_recorder_statistics` and the call
  site in `async_recalculate_recent` / `async_recalculate_history` that reads
  the stored rows (ADR-024).
- The PV combined series of TASK-0045a (→ task: TASK-0045a-pv-combined-series,
  done: `per_sensor[const.PV_SUM_ENTITY_ID]` =
  `{"unit": "W", "slot_values": [...]}` from `history._compute_effective_slots`;
  `const.PV_SUM_ENTITY_ID`, `const.PV_SUM_UNIQUE_ID_SUFFIX`,
  `const.PV_SUM_TRANSLATION_KEY`; `sensor.EffyPvSumSensor`,
  `sensor.pv_sum_unique_id`, `sensor._pv_sum_entities`), for the shape of the
  slot loop's result.
- `coordinator._async_recalculate_recent_and_report`,
  `coordinator.async_recalculate_history_and_report`,
  `coordinator._recorder_job_lock`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
