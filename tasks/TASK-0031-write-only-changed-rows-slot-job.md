# Task: The Slot Job and the Hour Rewrite Write Only Rows That Differ

- **Status:** done (2026-10-25) — implemented inline, reviewer pass inline
- **Related ADRs:** \[ADR-024 (Draft), ADR-012 (Amendment 2026-10-25), ADR-011,
  ADR-014, ADR-016\]
- **Dependencies:** [TASK-0030-write-only-changed-rows-history]

## Goal

The same rule on the 5-minute path: every cycle recomputes about 16 slots of 14
series (more after a counter jump or a reload) and most rows equal what is
stored. Write only the ones that differ, report the earliest change to
`sensor.effy_recalculated_from`, and let the hour's long-term rewrite skip an
hourly row that is already right. The live states still go out for every entity
computed.

## Acceptance Criteria

- Given a cycle whose recomputed rows equal the stored ones, then nothing is
  written and the earliest change is `None`: the coordinator does not push the
  sensor (ADR-012), and `touched_entity_ids` and `last_values` are still those
  of every computed entity, so `notify_updated` pushes every live state as
  before.
- Given a normal cycle in which the last completed slot's recorder-native row is
  stored (its `min`/`max` set), then that slot is written and is the earliest
  change.
- Given the slot's native row is not yet readable, then the slot counts as
  changed and is written (an extra write, never a missed one).
- Given a counter jump that extends the window back, then only the rows whose
  stored value differs are written, and the earliest change is the earliest of
  those, not the start of the extended window.
- Given the hour's rewrite, then an hourly row equal to what the writer would
  derive from the stored short-term rows is not written.
- Given the read of stored rows fails, then the cycle writes everything and logs
  one warning (TASK-0030's fail-safe).
- Given the slot-timer equivalence harness (CAP-1, TASK-0004), then it passes
  with its tolerances unchanged; the DEBUG line says `earliest changed slot` and
  the number of unchanged rows.
- Given the whole existing suite, then it passes unmodified, and after every
  cycle the stored rows equal those of writing every row (end state of ADR-004).

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/history.py`: `async_recalculate_recent`,
  `async_rewrite_hourly`; `coordinator.py` docstrings.
- `tests/test_history_write_only_changed_recent.py` (new), `adr-summary.md`.

## Definition of Done

- Tests green · `ruff`, `mypy`, `pytest`, `mdformat` clean · docs updated · no
  open ADR conflicts
- Mutation checks on the earliest change and on `touched` (an entity with only
  unchanged rows is still pushed)
- `Delivered Artifacts` block completed and accurate · no new dependency

## Consumed Interfaces

- `_mark_unchanged`, `_earliest_change`, `_count_unchanged`, `_hourly_rows`,
  `_PhaseTimings.note_unchanged` from `custom_components/effy/history.py` (→
  task: TASK-0030-write-only-changed-rows-history)
- `StoredRecorder`, `_rig`, `_snapshot`, `SLOT` from
  `tests/test_history_write_only_changed.py`

## Delivered Artifacts

- `custom_components/effy/history.py`
  - `async_recalculate_recent`: marks the unchanged rows
    (`include_long_term=False`), writes the rest, returns
    `(rows written, earliest change or None, touched, last_values)`; `touched`
    and `last_values` stay every entity computed. The DEBUG line reads
    `wrote N short-term rows (M already up to date) … earliest changed slot …`;
    the DEBUG timing record carries `rows unchanged M`.
  - `async_rewrite_hourly`: marks the hourly side only
    (`_mark_unchanged(..., include_short_term=False)`), so an hourly row that is
    already right is not imported; it still returns the number of statistics
    examined.
  - `_mark_unchanged` gained `include_short_term=True`; `_earliest_change`
    gained `include_long_term`.
- `tests/test_history_write_only_changed_recent.py` — 10 tests, including a
  cycle-by-cycle comparison of the stored rows with a write-everything run.
- External dependencies added: none.

## Review

**PASS.** Whole suite 635 passed. Seven mutants (the pre-change behaviour, the
window start as the earliest, the absent-hourly-mark bug, `touched` narrowed to
changed entities, the hour rewrite writing everything or also reading the
short-term side, the figure missing from the record) are each caught. The
slot-timer equivalence harness (CAP-1, TASK-0004) passes with its tolerances
unchanged. Tests were written after the code for this slice (the reverse of
TASK-0030); the mutation checks and the first test run, which found the
`include_long_term` bug, are the evidence that they discriminate. Not run
against a real Home Assistant.
