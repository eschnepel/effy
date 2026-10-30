# Task: Find and fix the duplicate hourly statistic the recorder's roll-up hits

- **Status:** todo — two stages: **Stage 1 evaluation** (no code change), then
  **Stage 2 fix** (human report 2026-10-30, after TASK-0033's finding)
- **Capability:** CAP-3 (Effy writes after the recorder)
- **Related ADRs:** \[ADR-020 (Effy only ever updates a row the recorder
  inserted), ADR-017 (superseded; this is the failure it described), ADR-011
  Amendment 2026-10-16, ADR-016 Amendment 2026-10-16, ADR-003, ADR-004, ADR-012,
  ADR-024\]
- **Dependencies:** \[TASK-0007a-effy-writes-after-the-recorder,
  TASK-0007b-retire-recorder-exclusion\] (both `done`; this task finds where
  they are incomplete)
- **Priority:** high. The failed compile may have cost the statistics of **other
  integrations' entities**, not only Effy's (see Impact). Its finding also
  decides what the README says about the exclusion (TASK-0033 was closed without
  building its warning and its "must be removed" wording).
- **Gate:** Stage 2 follows Stage 1 without a stop. The exception is a safe rule
  that needs a private recorder internal (for example `StatisticsRuns`): the
  human gives the go for that before it is built.

## Goal

Stage 1: find out which Effy write put an hourly (long-term) row in the database
before the recorder's own roll-up reached that hour, so that the roll-up's plain
insert failed. Stage 2: make that impossible for every Effy write path, with a
test rig that is strict enough to have caught it, and record in ADR-020 what
holds.

## Context (the human's report, 2026-10-30)

The human removed the recorder exclusion (TASK-0033's finding), restarted Home
Assistant, and got this in the log, once, at 15:02:33:

```text
Logger: homeassistant.components.recorder.util
Blocked attempt to insert duplicated statistic rows
  ... recorder/statistics.py, line 637, in compile_missing_statistics
  ... recorder/statistics.py, line 835, in _compile_statistics
        session.flush()  # populate the ids of the new StatisticsShortTerm rows
sqlite3.IntegrityError: UNIQUE constraint failed: statistics.metadata_id, statistics.start_ts
[SQL: INSERT INTO statistics (created, created_ts, metadata_id, start, start_ts, mean,
      mean_weight, min, max, last_reset, last_reset_ts, state, sum) ...]
[parameters: (None, 1793368952.9558432, 271, None, 1793365200.0, 9.383561158766474,
      None, None, None, None, None, 4.556307257389503, None)]
```

Read from it (to be confirmed in Stage 1):

- **The table is `statistics` (hourly, long-term), not
  `statistics_short_term`.** ADR-017 and ADR-020 are about the 5-minute table.
  ADR-020's "first live check" asked the human to watch for
  `UNIQUE constraint failed: statistics_short_term`, so it could not have shown
  this.
- **The row is the recorder's roll-up of an hour.** `start_ts` 1793365200 is
  2026-10-30 13:00:00 UTC (14:00 local, Europe/Berlin after the change of
  2026-10-25), and `created_ts` 1793368952.96 is 14:02:33 UTC (15:02:33 local).
  So the roll-up of the hour 14:00 to 15:00 local, which the recorder does in
  the compile of the hour's last slot (13:55 UTC), ran 2.5 minutes after the
  hour ended and found an hourly row for that hour already there.
- **It is the catch-up compile** (`compile_missing_statistics`), which the
  recorder runs at start and re-queues, for the slots missed while Home
  Assistant was down.
- **The row looks like Effy's data.** It has a `state` (4.556) and no `min` and
  `max`, which is what Effy's rows carry (ADR-003: `start`, `mean`, `state`) and
  what a roll-up of Effy-written 5-minute rows gives. A native measurement
  sensor's roll-up has `min` and `max`. Entity `metadata_id` 271 is not known
  yet (the query is below).

## The suspect

ADR-020 says "Effy only ever *updates* a row the recorder inserted". The code
does not guarantee that for the hourly table:

- `async_recalculate_history` (the button's full history recalculation) calls
  `_write_recorder_statistics(..., include_long_term=True)` for every hour of
  its range, which ends at the last completed slot. That range includes the
  **hour that is still open**, and after a restart the **hour that closed while
  Home Assistant was down** and that the recorder's catch-up has not rolled up
  yet. An hourly row Effy inserts for such an hour makes the recorder's later
  plain insert fail.
- ADR-020 lists a residual race only for "the seconds between a slot boundary
  and the recorder's compile". The window for the button is up to **an hour**,
  and longer after a restart.
- The slot job's `async_rewrite_hourly` runs after the recorder's roll-up (the
  last completed slot is the one at minute 55), but after a restart the job
  starts at the first recorder event, which the catch-up may fire while later
  hours are still to be rolled up (ADR-011 Amendment 2026-10-16 says the
  catch-up "may re-queue it in chunks").
- The recent recalculation writes no hourly row (`include_long_term=False`).

The test rig did not catch it: the fake recorder stores of the tests accept an
insert for a row that exists (TASK-0030's patch-2 had the same kind of blind
spot).

## Acceptance Criteria

### Stage 1 — evaluation (no file under `custom_components/` changes)

- Given the human's log and database, then the report gives the **timeline**:
  the restart times, every press of the history button and every
  `Effy recalculation timing (history)` line (README: how to read it) around
  14:00 to 15:03 local, and the `created_ts` of the existing hourly row of
  `metadata_id` 271 for 13:00 UTC, so that the writer of that row is named, not
  assumed.
- Given the entity, then the report names `metadata_id` 271 (`statistics_meta`)
  and states whether the other Effy statistics and the human's native statistics
  have a hourly row and a 5-minute row for 13:00 UTC and 13:55 UTC.
- Given the code, then the report lists **every path that can write a row to the
  hourly table** (the full history recalculation, the slot job's
  `async_rewrite_hourly`, anything else found by search) with, for each, the
  hours it may touch relative to the recorder's roll-up (open hour, hour closed
  during a restart, hour of the last completed slot) and whether it can insert a
  row the recorder has not rolled up yet. The result is checked against the
  timeline.
- Given the source of the human's Home Assistant (2026.10.x, the tag of the
  installed version), then the report states from it: how the roll-up reads the
  5-minute rows and inserts the hourly row; how `compile_missing_statistics`
  chunks and re-queues, and whether it fires the 5-minute event per period; what
  `async_import_statistics` does for an existing and for a missing row; and
  **what a UNIQUE failure does to the compile**: whether the period's rows of
  *all* entities are rolled back, whether later periods are compiled, whether it
  repeats at every start.
- Given that, then the report states the **impact**: which statistics of the
  human's instance are now missing (the slot 13:55 UTC and the hour 13:00 UTC,
  for which entities), whether the Energy dashboard or long-term statistics show
  a gap, and whether and how the gap can be repaired. This is written for the
  human and goes into the README's troubleshooting part in Stage 2.
- Given the finding, then the report reproduces the failure **in a test rig that
  has the recorder's behaviour**: a fake whose plain insert raises on a
  duplicate `(metadata_id, start)` and whose roll-up runs at the slot of minute
  55, with the history recalculation started at a minute inside the open hour,
  at the last second before the roll-up, and during a catch-up. The report says
  which of these fail today, and that the test passes through the real writer,
  not around it.
- Given the paths, then the report evaluates **how Effy can know that an hour
  has been rolled up** and what each way costs, so that Stage 2 chooses on
  facts:
  - the hour's end plus a margin (cheap, but wrong after a restart);
  - an hourly row of the source sensors or of any native statistic for that hour
    (a read; present only if the recorder has rolled up);
  - the recorder's own run table (a private internal);
  - a rule that makes the question moot: the history recalculation writes hourly
    rows only for hours **before** the last hour the recorder has certainly
    rolled up, and the hour in question is left to the slot job's rewrite, which
    already updates after the roll-up.
- Given ADR-020 and TASK-0033's Stage 2 wording, then the report says whether
  the README's "may remove the exclusion" and the planned "must remove" are true
  today, and what the README must say meanwhile.

### Stage 2 — fix (tests first, each shown failing against the code as it was)

- Given the test rig of Stage 1 (a recorder fake whose insert fails on a
  duplicate and whose roll-up runs at minute 55, added to the tests' shared
  support so every writer test can use it), then **no Effy write path inserts an
  hourly or 5-minute row the recorder has yet to insert**: the history
  recalculation started in the open hour, at the last second before the roll-up,
  in the seconds after it, after a restart before and during the catch-up, and
  the slot job's hourly rewrite in the same situations. The recorder's roll-up
  succeeds in every case.
- Given the history button, then it still writes the hourly rows of every hour
  the recorder has rolled up (older history of Effy's entities is not lost), and
  the hours it leaves are written by the slot job's rewrite after the roll-up,
  so that the hourly row equals what a full recalculation would write (the
  TASK-0004 equivalence harness extended to the hourly table).
- Given an entity that the recorder never compiled natively (for example an Effy
  row for an hour before the exclusion was removed), then Effy still writes its
  history rows for hours the recorder has finished with.
- Given the history recalculation's reported earliest change
  (`recalculated_from`, ADR-012) and the write-only-changed-rows read (ADR-024),
  then neither reports nor skips an hour Effy deliberately left, and the
  `Effy recalculation timing` record says how many hourly rows were left for the
  slot job.
- Given ADR-020, then it is **amended**: the residual race is replaced by what
  now holds and how Effy knows an hour is rolled up; ADR-011 and ADR-016
  amendments say the same where they claim it; `tasks/adr-summary.md` is
  updated. The README's "Recorder" section says what is true, and its
  troubleshooting names the log message of this task, what it cost, and how to
  repair the gap.
- Given the fix, then the whole suite, `ruff`, `mypy` and `mdformat` pass, no
  existing assertion is weakened, and a mutation of the new rule (hours left,
  the margin, the check) is caught.
- Given the fix is released, then the **human verifies on the instance**: the
  history button pressed in the middle of an hour and again just before and just
  after an hour boundary, and a restart in the middle of an hour, with no
  `Blocked attempt to insert duplicated statistic rows` in the log across one
  full hour, and the hourly rows of the Effy entities for those hours present.
  This is ADR-020's first live check, now including the hourly table. Until then
  the task stays in `review`, not `done`.

## Evidence the human has to supply

- The Home Assistant log from shortly before the restart to after 15:03 local
  (filter for `effy` and `recorder`), and the times of the restart(s) and of any
  press of **Re-calculate History** that day.

- Which entity is `metadata_id` 271, and the stored hourly row (adjust names to
  the schema of the running Home Assistant):

  ```sql
  SELECT id, statistic_id, source, unit_of_measurement, has_mean, has_sum
    FROM statistics_meta WHERE id = 271;

  SELECT metadata_id, start_ts, created_ts, mean, min, max, state, sum
    FROM statistics WHERE metadata_id = 271 AND start_ts >= 1793358000
   ORDER BY start_ts;

  -- which entities have an hourly row for the hour that failed, and which do not
  SELECT m.statistic_id, s.created_ts
    FROM statistics_meta m
    LEFT JOIN statistics s ON s.metadata_id = m.id AND s.start_ts = 1793365200
   ORDER BY s.created_ts IS NULL, m.statistic_id;

  -- the same for the 5-minute slot 13:55 UTC (1793368500)
  SELECT m.statistic_id, t.created_ts
    FROM statistics_meta m
    LEFT JOIN statistics_short_term t ON t.metadata_id = m.id AND t.start_ts = 1793368500
   ORDER BY t.created_ts IS NULL, m.statistic_id;
  ```

- Whether the Energy dashboard or a long-term statistics card shows a gap at
  14:00 local on 2026-10-30.

- The Home Assistant version (the log says Python 3.14).

## Interim advice for the human (unverified until Stage 1 confirms the writer)

Until the cause is confirmed, treat **Re-calculate History** as the likely
trigger and avoid pressing it close to an hour boundary or soon after a restart.
Do not put the recorder exclusion back as a workaround without reading
TASK-0033's finding: it is what hid this and kept the standby and efficiency
sensors empty.

## Estimated File / Module Footprint (hint, not a commitment)

Stage 1: none in production; the report is written into this file, with a
scratch script (not committed) for the timeline. Stage 2: `history.py`
(`async_recalculate_history`, `async_rewrite_hourly`,
`_write_recorder_statistics`, the rule for which hours are written),
`coordinator.py` (the slot job after a restart), `tests/support.py` and the
history tests (the strict recorder fake),
`tests/test_history_recent_equivalence.py` (hourly equivalence), `README.md`,
`adr/020-*.md`, `adr/011-*.md`, `adr/016-*.md`, `tasks/adr-summary.md`.

## Definition of Done

- The findings section below is complete: the timeline, the writer, the paths,
  the source facts, the impact, the repair, what Stage 2 built
- Stage 2 tests were shown failing first against a recorder fake that rejects
  duplicates; `pytest`, `ruff format --check`, `ruff check`, `mypy` and
  `mdformat --check` pass
- Docs updated (README, ADR-020 amendment, ADR-011 and ADR-016 where they claim
  it) · no open ADR conflicts
- The human's live check over a full hour is recorded
- `Delivered Artifacts` block completed and accurate
- No new external dependencies

## Consumed Interfaces

- `async_recalculate_history`, `_write_recorder_statistics`, `_hourly_rows`,
  `async_rewrite_hourly`, `_mark_unchanged` in
  `custom_components/effy/history.py` (→ TASK-0007a, TASK-0030, TASK-0031); read
  in Stage 1, changed in Stage 2.
- The slot job and the recorder-event trigger in
  `custom_components/effy/coordinator.py` (→ TASK-0006, TASK-0007a); read in
  Stage 1, changed in Stage 2 where the finding requires.
- `tests/test_history_recent_equivalence.py` (→ TASK-0004) and the shared fake
  recorder of the history tests.

## Findings

<!-- Filled by the worker after the evidence is in. -->

## Delivered Artifacts

<!-- Filled by the worker: what Stage 2 changed, exactly. -->
