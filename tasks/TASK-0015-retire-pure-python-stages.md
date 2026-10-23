# Task: Retire the Pure-Python Stages from Production (reference stays in tests)

- **Status:** todo — draft, blocked: gate G-perf, Gate 2, and ADR-022 Q3 = A
- **Related ADRs:** [ADR-022, ADR-000 (§3, §6), ADR-012, ADR-001]
- **Dependencies:** [TASK-0013-wire-array-native-compute-path]

## Goal

After the cut-over, production code no longer carries two implementations of the
same maths. Functions no production caller uses any more move to `tests/` as the
oracle the grid module is tested against; the functions that other features
still use stay in `calculation.py`.

## Acceptance Criteria

- Given each of `trapezoidal_slot_contributions`, `interpolate_slot_gaps`,
  `distribute_loss`, `effective_in_original_unit`, then a script-checked search
  of `custom_components/` finds no production caller before the move (the Worker
  lists the callers found); a function that still has one stays in place and is
  named in the task note with its caller.
- Given `_find_transitions`, `_distribution_window_start`,
  `trapezoidal_jump_windows`, `recent_rewrite_start` (jump windows, rewrite
  range, drain events), then they stay in `calculation.py` unless the same
  search proves them unused.
- Given the moved functions, then they live in `tests/reference_pipeline.py`
  with their semantics unchanged, and every test that used them still passes
  (`tests/test_calculation.py` loads them from there; the TASK-0011/0012/0013
  oracle tests too).
- Given `calculation.py`, then it imports nothing new and ADR-000 §6 still
  holds.
- Given the README and ADR table, then they match.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/calculation.py`, `tests/reference_pipeline.py` (new),
  `tests/test_calculation.py` and the oracle tests of TASK-0011 to TASK-0013
- `README.md`, `tasks/adr-summary.md`

## Definition of Done

- Tests green · `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`,
  `mdformat --check` clean · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none)

## Consumed Interfaces

<!-- Lead Agent fills from the Delivered Artifacts of TASK-0013. Pre-existing
     (verified 2026-10-19): -->

- the functions named above from `custom_components/effy/calculation.py`

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->
