# Task: The Stored-Rows Check Must Say Why It Found Nothing Unchanged

- **Status:** done — diagnostics delivered, reviewer pass inline. **Defect moved
  to TASK-0030-write-only-changed-rows-history-patch-2:** on the Pi no stored
  row counted as unchanged; the record named the cause (`state` is hidden by the
  API when `has_sum` is false).
- **Related ADRs:** [ADR-024, ADR-012 (Amendment 2026-10-25)]
- **Dependencies:** [TASK-0030-write-only-changed-rows-history]

## Goal

TASK-0030 passed 635 tests, but on the Pi (Home Assistant 2026.10.4, Python
3.14, SQLite) two history recalculations in a row wrote all 109,242 rows and
reported `rows unchanged 0`; the recent cycle did the same. Every series was
read (`stored rows` jobs of about 0.1 s each, no warning), so the read worked
and the comparison found every row different, or a gate stopped it. The tests
could not see this: their stored-row fake returns what the code assumes. Make
the record say, per series, what was read and why each row was or was not equal,
so one more run names the cause.

## Acceptance Criteria

- Given a history recalculation, then the detail block lists per series: rows
  read, equal, missing, start unexpected, and the rows that differ in `mean`,
  `state` or a column the writer clears, each period (5 minutes, hour) on one
  line, with the first differing pair of values and its time.
- Given the stored unit differs from the new one, or the series has no metadata,
  then the line says so, with both units, and nothing was read.
- Given existing callers of `_unchanged_rows`, then the new `notes` parameter is
  optional. The first line of the record and the behaviour of the rule are
  unchanged.
- Given the whole suite, then it passes.

## Delivered Artifacts

- `custom_components/effy/history.py`: `_unchanged_slots` returns
  `(frozenset, diagnosis)`; `_unchanged_rows(..., notes=None)`;
  `_mark_unchanged` collects the notes; `_PhaseTimings.note_stored_checks`,
  `stored_checks`; the block `stored rows check`. `history days` prints without
  a trailing `.0`.
- `tests/test_history_write_only_changed.py`: `TestTheCheckExplainsItself` (10
  tests).
- External dependencies added: none.

## Review

**PASS** (645 tests, ruff, mypy clean; five mutants caught after one assertion
was added for the hourly side). Not a fix: the cause is not known yet.
