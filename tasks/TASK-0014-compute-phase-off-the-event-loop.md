# Task: Run the Compute Phase Off the Event Loop

- **Status:** **skipped** (2026-10-25, human: not released by gate G-perf —
  compute is 0.4 % of a history recalculation, ADR-022 outcome). Kept as an
  audit trail; do not build.
- **Related ADRs:** [ADR-022, ADR-011 (Amendment 2026-10-16: one lock), ADR-014]
- **Dependencies:** [TASK-0010-recalculation-phase-timing]. It edits
  `history.py` like TASK-0013, so the two run one after the other, in the order
  ADR-022 Q5 sets.

## Goal

The synchronous compute segments of `_compute_effective_slots` run as one
executor job on plain data (no `hass`, no recorder access), so a long compute
phase does not hold the event loop. Fetch and write phases and the lock are
unchanged; results are identical.

## Acceptance Criteria

- Given a compute function that blocks for a measurable time and a ticker task
  on the loop, when a recalculation runs, then the ticker keeps running during
  the compute phase (it would not with today's code).
- Given the executor function, then it receives only plain data (dicts, lists,
  datetimes, numbers) and a test calls it with no `hass` object at all.
- Given a jump-extended recalculation (fetch, compute jump windows, refetch,
  compute), then the interleaving of fetch and compute is preserved and results
  equal today's.
- Given an exception inside the executor job, then it propagates to the caller
  as before and the lock is released.
- Given the TASK-0004 harness and the whole existing suite, then they pass
  unmodified.
- Given the TASK-0010 record, then `compute` counts the time spent awaiting the
  executor job.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/history.py`
- `tests/test_history_compute_executor.py` (new)

## Definition of Done

- Tests green · `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`,
  `mdformat --check` clean · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none)

## Consumed Interfaces

<!-- From TASK-0010's Delivered Artifacts (done 2026-10-19), then the
     pre-existing symbols (verified 2026-10-19): -->

- `_PhaseTimings` (`enter(phase | None)`, `recorder_job(recorder, func, *args)`,
  `note_raw_states`, `probe_parse`, `finish(rows)`; attributes `seconds`,
  `queries`, `slots`), `_NO_TIMINGS` and the trailing parameter
  `phases: _PhaseTimings = _NO_TIMINGS` of `_compute_effective_slots` and
  `_fetch_energy_raw_with_anchor`, from `custom_components/effy/history.py`. The
  compute segments book themselves with `phases.enter("compute")` /
  `phases.enter(None)`; moving them into an executor job must keep that
  accounting (the time awaiting the job counts as `compute`) and must not call
  `phases` from the executor thread.

- `_compute_effective_slots`, `async_recalculate_history`,
  `async_recalculate_recent` from `custom_components/effy/history.py`

- `EffyCoordinator._recorder_job_lock` (ADR-011 Amendment 2026-10-16) in
  `custom_components/effy/coordinator.py` (read, not changed)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->
