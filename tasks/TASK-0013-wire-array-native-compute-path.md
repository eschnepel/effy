# Task: Wire the Array-Native Compute Path into the Recalculations

- **Status:** **skipped** (2026-10-25, human: not released by gate G-perf —
  compute is 0.4 % of a history recalculation, ADR-022 outcome). Kept as an
  audit trail; do not build.
- **Related ADRs:** [ADR-022, ADR-011, ADR-012, ADR-014, ADR-016, ADR-000]
- **Dependencies:** \[TASK-0011-array-native-trapezoid,
  TASK-0012-array-native-waterfall-and-interpolation\] (and TASK-0010, which
  both depend on)

## Goal

`_compute_effective_slots` computes on one slot grid with the functions of
TASK-0011/0012 and converts to the `(datetime, value)` lists the writer consumes
once, at the end. Fetch and write phases are untouched. Which recalculations use
it follows ADR-022 Q3: A = both, B = only the history recalculation. The
statistics written are the same as before within the tolerance policy of ADR-022
Decision 7.

## Acceptance Criteria

- Given the TASK-0004 harness, then it passes with `_equivalent` comparing slot
  keys exactly and values with `abs=1e-9` (the tolerance is explicit in the
  helper, replacing `pytest.approx`'s defaults), for the slot timer against the
  history recalculation at the same time.
- Given a seeded synthetic dataset of 7 days (4 inputs, 2 outputs, energy- and
  power-family sources, offline stretches, gaps), when the reference pipeline
  (pure-Python stages) and the new one run on the same fake world, then the
  written slots are identical in keys and equal within tolerance, for every
  effective, derived-power and smoothed series.
- Given the TASK-0010 record, then `compute` still covers exactly the compute
  segments and `fetch`/`write` are unchanged.
- Given `async_recalculate_recent` and `async_recalculate_history` return values
  (`recalculated_from`, touched entity IDs, last values), then they equal the
  reference's.
- Given Q3 = A, then the slot job also runs the array path; given Q3 = B, then
  it still runs the pure-Python path and a test pins that.
- Given Q4's answer, then the import-failure behaviour is as decided and tested
  (A: the integration does not load and the message names numpy).
- Given the numbers, then before/after wall times of `compute` on the 28-day
  synthetic dataset are in the task note (sandbox); the Pi numbers are a human
  step after merge.
- Given the README, then it says Effy needs numpy and why (one short paragraph).

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/history.py` (`_compute_effective_slots`, helpers)
- `tests/test_history_recent_equivalence.py` (tolerance helper),
  `tests/test_history_array_pipeline_equivalence.py` (new)
- `README.md`, `adr/022-*.md` (status and amendments, human confirms)

## Definition of Done

- Tests green · `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`,
  `mdformat --check` clean · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none beyond
  TASK-0011's)

## Consumed Interfaces

<!-- The Lead Agent fills this from the Delivered Artifacts of TASK-0011 and
     TASK-0012 before the start. Pre-existing symbols (verified 2026-10-19): -->

- `_compute_effective_slots`, `_readings_for_slot`, `_stat_field_for`,
  `_drop_open_slot`, `async_recalculate_history`, `async_recalculate_recent`
  from `custom_components/effy/history.py`
- `_equivalent`, `_World`, `_run_full_recalc` from
  `tests/test_history_recent_equivalence.py`

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->
