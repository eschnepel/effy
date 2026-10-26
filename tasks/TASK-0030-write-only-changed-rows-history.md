# Task: The History Recalculation Writes Only Rows That Differ

- **Status:** done (2026-10-25) — implemented inline, tests first, reviewer pass
  inline
- **Related ADRs:** \[ADR-024 (Draft), ADR-012 (Amendment 2026-10-25), ADR-004,
  ADR-003, ADR-022\]
- **Dependencies:** [TASK-0010-recalculation-phase-timing-patch-1]

## Goal

98 % of a history recalculation is the recorder writing rows, about 1 ms each,
and most rows equal what is already stored. Read the stored rows first and hand
the recorder only the new or different ones, for both tables, and report the
earliest change as `recalculated_from`. The values, the range and the recompute
do not change, and neither does what is stored once the run is over.

## Acceptance Criteria

Test with the in-memory recorder world of
`tests/test_history_recent_equivalence.py`, extended in the new test file so
that a read of stored rows answers from what the world's writer stored (the
harness gains a fake of the new read; the existing tests stay unmodified).

**The rule.**

- Given a stored 5-minute row with the same `start`, `mean` equal and `state`
  equal to the new value and `min`, `max`, `sum`, `last_reset` empty, then it is
  not written.
- Given a stored row that differs in `mean`, or in `state`, or still carries a
  `min`, `max`, `sum` or `last_reset` (a native row), or no stored row, then it
  is written. Equality is exact (Draft ADR-024 Q1 = A).
- Given an hourly row, then the same rule applies against the `mean` (average of
  the hour's slots) and the `state` (the hour's last value) the writer would
  derive; the derivation exists once and serves the writer and the comparison.
- Given a series whose stored unit differs from the new one, then every row of
  it is written.
- Given short-term slots older than the short-term cutoff, then they are neither
  compared nor written short-term, as today; their hours still take part in the
  hourly comparison.

**The history recalculation.**

- Given a history recalculation run twice on the same world with unchanged
  settings, then the second run writes no row, and returns `None` as
  `recalculated_from`.
- Given one raw value changed between two runs, then only the rows that value
  changes are written, and `recalculated_from` is the earliest of them (a
  short-term slot, or the hour of a changed hourly row).
- Given a first run (nothing stored), then it writes exactly the rows it wrote
  before this task, and `recalculated_from` is the earliest slot written.
- Given any run, then `touched_entity_ids` and `last_values` are what they were
  (every entity computed), and the stored rows after the run equal the ones a
  run without this task would have left.
- Given a run, then the DEBUG line and the timing record say how many rows were
  written and how many were already up to date; with nothing written the sensor
  is not pushed (ADR-012). *(Refined at review: the button's INFO line keeps "N
  slots written", now the rows actually written, so the history function's
  return shape stays as it is.)*

**Fail-safe and cost.**

- Given the read of stored rows raises (or the recorder module is not
  importable), then everything is written as before, one warning is logged, and
  the run completes.
- Given the detail block (TASK-0010 patch 1), then the stored-row reads appear
  as recorder jobs labelled `stored rows <entity>` (run, wait, rows) and the
  first line of the record gains `rows unchanged N`. The first line otherwise
  keeps its form; the TASK-0010 tests pass unmodified.
- Given 14 series of 8,064 slots, then the read and the comparison of one series
  happen in one executor job that returns the set of unchanged slots, not the
  rows, and one series at a time (bounded memory).

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/history.py`: a shared helper deriving the hourly row;
  an executor job reading and comparing one series; the writer honouring
  optional `unchanged_short` and `unchanged_hourly` sets in each series entry
  (its signature stays, the fake writer of the tests has the old one); wiring in
  `async_recalculate_history`; the earliest change.
- `custom_components/effy/button.py`, `coordinator.py`: log wording and
  docstrings ("touched" → "changed") only.
- `tests/test_history_write_only_changed.py` (new), README (timing section and
  the button), `adr-summary.md`.

## Definition of Done

- Tests green · `ruff format --check`, `ruff check`, `mypy`, `pytest`,
  `mdformat --check` clean · docs updated · no open ADR conflicts
- Mutation checks on the rule (each column of the comparison, the unit check,
  the cutoff, the fail-safe) · `Delivered Artifacts` block completed and
  accurate
- The cost of the read measured on a synthetic 14 × 8,064 run and reported in
  the review (the Pi's number comes from the human's next record)
- No new external dependency

## Consumed Interfaces

<!-- To be filled by the Lead Agent from the Delivered Artifacts of TASK-0010
     patch 1 before the worker starts. -->

- `_PhaseTimings.recorder_job(..., label=, source=)`, `_fetch_statistics`,
  `_get_statistics_units`, `_write_recorder_statistics`,
  `async_recalculate_history` from `custom_components/effy/history.py`
- `_world`, `_install`, `_rig`, `_history_recalc` from the existing test modules

## Delivered Artifacts

- `custom_components/effy/history.py`
  - `_hourly_rows(slot_values) -> dict[datetime, tuple[float, float]]` — the one
    derivation of the hourly rows (hour → (mean, state)), used by the writer and
    the comparison.
  - `_CLEARED_COLUMNS`, `_COMPARED_TYPES`;
    `_unchanged_slots(hass, statistic_id, period, expected) -> frozenset[datetime]`
    and
    `_unchanged_rows(hass, statistic_id, unit, short, hourly) -> {"short": frozenset, "hourly": frozenset}`
    — blocking, run in the recorder's executor, one series per job; a series
    whose stored unit differs or is unknown has nothing unchanged.
  - `async _mark_unchanged(hass, all_series, *, short_term_cutoff, include_long_term, include_short_term=True, phases)`
    — adds `unchanged_short` / `unchanged_hourly` frozensets to each series
    entry in place; on any read failure it removes all marks, logs one warning
    and returns.
  - `_earliest_change(series, *, short_term_cutoff, include_long_term)` and
    `_count_unchanged(all_series)`.
  - `_write_recorder_statistics`: signature unchanged; skips rows named in the
    entry's optional marks; returns the short-term rows actually written.
  - `_PhaseTimings.unchanged_rows`, `note_unchanged(rows)`; `_rows_of` counts
    sets; the first line of a record that has the figure ends
    `, rows unchanged N`.
  - `async_recalculate_history`: reads and marks before the write phase; returns
    `(rows written, earliest change or None, touched, last_values)` — `touched`
    and `last_values` unchanged (every entity computed).
- `tests/test_history_write_only_changed.py` — 31 tests (the rule column by
  column, hourly, unit, native rows, exact equality, cutoff, fail-safe, timing
  record, a stateful recorder harness with fakes of Home Assistant's
  `statistics_during_period` and `get_metadata`).
  `tests/test_history_phase_timing.py`: nine added lines in `_rig` switch the
  new step off there (see Review).
- `README.md`, `GLOSSARY.md` (**Unchanged row**), docstrings of `button.py`,
  `coordinator.py`, `history.py`.
- External dependencies added: none.

## Review

**PASS.** `pytest` 635 passed, `ruff`, `mypy` (36 files) clean,
`mdformat --check` clean on the edited files (with the `exclude` key removed
temporarily: Python 3.12).

- **One compatibility cost, found and handled openly.** "The existing suite
  passes unmodified" could not hold literally: three TASK-0010 tests assert the
  exact number of recorder queries and fetch seconds of a history run, and the
  new stored-row reads are legitimately more recorder jobs (5 queries, 10 fake
  seconds). Their assertions are untouched; the shared rig (`_rig` in
  `test_history_phase_timing.py`) switches the new step off with nine added
  lines, and the new tests cover the combination, including the reads in the
  detail block.
- **Mutation checks:** fifteen deliberate faults (a column of the rule dropped,
  the unit check, exactness, the writer ignoring either mark, the cutoff in the
  compare and in the earliest change, the old `start` as the result, the
  fail-safe raising or warning per series, `touched` narrowed, the count not on
  the record, the wrong period) are each caught. Two survived at first: the
  synthetic world lay wholly inside the cutoff, so the cutoff tests were
  vacuous; precise tests with the cutoff inside a series were added and all
  fifteen are caught.
- **Bug found by testing TASK-0031's slot job against this code:**
  `_earliest_change` counted an absent hourly mark as a change even for a run
  that writes no hourly rows; the parameter `include_long_term` fixed it.
- **Cost of the comparison, measured:** 5.0 ms per series of 8,064 rows, about
  70 ms for 14 series on this machine (probably under 0.5 s on the Pi). The
  database read behind it (`statistics_during_period`) is not measurable here:
  the Pi's next record shows it as `stored rows` jobs.
- **Limits:** verified against fakes of Home Assistant's read API written from
  its source (`dev` branch), not against a real Home Assistant or the Pi; a
  first run on an empty store reads nothing (no metadata) and costs what it did;
  after a change of settings every value is new and the run costs what it did.

## First run on the Pi (human, 2026-10-26) — the feature did not take effect

Two history recalculations in a row, then a recent cycle:
`rows written 109242, rows unchanged 0` each time, although the second run's
stored rows were exactly the first run's. All 14 `stored rows` jobs ran (0.09 to
0.13 s each, `rows 0`), no warning, so the read succeeded and no row counted as
unchanged. The test suite could not catch this, since its fake store returns
what the code assumes; it also cannot tell which gate failed (unit, `start`,
`mean`, `state` or a cleared column). A job of about 0.1 s rules the unit gate
out (the metadata lookup alone takes under 10 ms), which leaves the comparison.
TASK-0030 patch 1 makes the record say why; the cause is read from the next run.
The same log answers a question for ADR-024: the write phase is CPU-bound
(106.14 s of CPU in 106.10 s of wall).
