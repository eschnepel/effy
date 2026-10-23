# Task: Effy Writes Only After the Recorder (Prerequisite of Retiring ADR-017)

- **Status:** done
- **Related ADRs:** [ADR-010, ADR-011, ADR-012, ADR-016, ADR-017]
- **Dependencies:** [TASK-0006-recorder-synchronized-slot-trigger]

## Goal

Once `sensor.effy_*` is recorded (TASK-0007b), the recorder's own compile
inserts a short-term row for every slot, with a plain insert guarded only by the
`StatisticsRuns` check; Effy's `async_import_statistics` is update-or-insert.
Effy must therefore never insert a row the recorder is about to insert, and must
only ever update rows after the recorder's compile. Verified against Home
Assistant 2025.1.4 (2026.3.4 was not available in the sandbox: **re-verify
against the pinned version**). Three decisions of the human, 2026-10-16, make
this so; all three are safe while the exclusion is still in force, which is why
this task comes first.

1. **No statistic for the open slot (option A).** Effy never inserts a
   short-term row for the open (current) slot, in the slot job and in the
   history recalculation alike. The live state carries the last completed slot's
   calculated value ("better than nothing"). At the next slot the recorder
   inserts the row for the slot that just ended (natively, from the live states)
   and Effy then updates it with its calculated values.
1. **First job after a restart waits for the recorder.** HA queues its catch-up
   compile of the slots missed during downtime at recorder start and may
   re-queue it in chunks; a job started by the watchdog at the next boundary
   could insert a slot's row before that compile reaches it (ADR-017's original
   `compile_missing_statistics` traceback). The first job is held until the
   first recorder event; the watchdog starts one if no event arrives within
   `WATCHDOG` (6 min) of setup.
1. **Hourly row rewritten by the slot job.** The recorder rolls up the hour's
   long-term row from the 12 short-term rows in the same compile call that
   inserts the hour's last slot (the slot starting at minute 55, compiled at
   boundary + 10 s), before the 5-minute event fires. That slot therefore enters
   the roll-up with its native, carried-forward value, and the roll-up's `min` /
   `max` come from that one native row (Effy's rows carry none: HA's
   `_update_statistics` replaces every column, so an Effy update sets `min` and
   `max` to NULL). When the last completed slot starts at minute 55, the slot
   job re-reads the 12 short-term rows of that hour after its recent
   recalculation, under the same lock, and writes the hour's long-term `mean`
   for every Effy statistic (`min`/`max` NULL, as the history recalculation
   writes them). A failure is logged and not retried; the next history
   recalculation corrects the hour. This amends ADR-011 decision 2 (the timer
   path wrote short-term only).

## Acceptance Criteria

- Given the slot job (recent recalculation), then no short-term row is written
  for the slot that contains `now` (neither the provisional nor a computed
  value); `calculation.carry_forward_open_slot` is no longer used for
  statistics. Given the same for the history recalculation (button), then it
  neither writes nor pushes the ≈ 0 open slot.
- Given the live push after either path, then the pushed value of each series is
  the calculated value of its last *completed* slot, i.e. the same number the
  provisional write produced before (ADR-016 behaviour kept, without the row). A
  series with no completed slot in the window is pushed as before (unknown).
- Given a short-term row that already exists for a slot (inserted by the
  recorder, with a `mean`, `min` and `max`), when Effy updates it, then the row
  holds Effy's `mean` and NULL `min`/`max` (a test on the fake store; the real
  behaviour is `_update_statistics`, re-verified on the pinned version).
- Given a restart, when `async_setup` has run and no recorder event has arrived,
  then the watchdog starts no job until `WATCHDOG` has passed since setup; the
  first recorder event starts the first job at once; with no event for
  `WATCHDOG` the watchdog starts one (replaces TASK-0006's "none ever started"
  rule; the other watchdog tests are unchanged).
- Given the last completed slot starts at minute 55, when the slot job has
  finished its recent recalculation, then it rewrites that hour's long-term
  `mean` (the mean of the 12 short-term slot rows read back, including the
  just-updated slot) for every Effy statistic, once, under the lock. Given any
  other slot, then no hourly row is written. Given the rewrite raises, then it
  is logged and the slot job ends normally.
- Given the CAP-1 equivalence harness (TASK-0004) and all TASK-0006 tests, then
  they pass unchanged except where the open-slot write is asserted (adjusted
  with a note).
- Given the docs, then ADR-016 and ADR-011 carry amendments (open slot: no
  statistic; the slot job writes one hourly row per hour; the first job waits
  for the recorder); `tasks/adr-summary.md` and `GLOSSARY.md` are updated.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/history.py` (`async_recalculate_recent`,
  `async_recalculate_history`, a new hourly rewrite function reusing
  `_write_recorder_statistics`), `calculation.py`, `coordinator.py` (watchdog
  rule, hourly step in the slot job), `tests/test_coordinator_slot.py`,
  `tests/test_history_recent_equivalence.py`, ADR-011 / ADR-016 amendments,
  `tasks/adr-summary.md`, `GLOSSARY.md`.

## Definition of Done

- Tests green; `mypy`, `ruff check`, `ruff format --check`, `mdformat --check`
  clean; every new test shown failing against the pre-change code first.
- No open ADR conflicts; no new external dependency; `Delivered Artifacts`
  completed.

## Consumed Interfaces

<!-- Refreshed by the Lead Agent 2026-10-16 from TASK-0006 (done). -->

- `custom_components/effy/coordinator.py` (→ task: TASK-0006): `EffyCoordinator`
  `_on_slot_timer(now)`, `_on_recorder_statistics`, `_start_slot_job`,
  `_async_slot_job`, `_recorder_job_lock`,
  `_async_recalculate_recent_and_report`,
  `async_recalculate_history_and_report`, `_last_slot_job_at`, `WATCHDOG`.
- `custom_components/effy/history.py`: `async_recalculate_recent`,
  `async_recalculate_history`,
  `_write_recorder_statistics(..., include_long_term)`,
  `async_probe_last_slot_ready`; `calculation.carry_forward_open_slot`.

## Delivered Artifacts

- `custom_components/effy/calculation.py` →
  `drop_open_slot(slot_values, now, slot_minutes=5) -> list[tuple[datetime, float]]`;
  `carry_forward_open_slot` removed (superseded; its tests replaced by
  `TestDropOpenSlot`).
- `custom_components/effy/history.py`
  - `_drop_open_slot(series, now)` replaces `_carry_forward_open_slot`; applied
    to all three series in `async_recalculate_recent` and
    `async_recalculate_history` (entities left empty are dropped, so they are
    neither written nor reported).
  - `async_rewrite_hourly(hass, units: dict[str, str], hour_start) -> int`
    (statistics rewritten): one `_fetch_statistics` read of
    `[hour_start, hour_start + 1 h)`, then
    `_write_recorder_statistics(..., include_long_term=True, include_short_term=False)`.
  - `_write_recorder_statistics(..., include_short_term: bool = True)`.
  - Written rows are, as before, exactly `start` / `mean` / `state` (guarded by
    a test).
- `custom_components/effy/coordinator.py` → `_setup_at` (set in `async_setup`);
  `_on_slot_timer(now)` counts from `_last_slot_job_at or _setup_at` (a missing
  reference, only possible without `async_setup`, still starts a job);
  `_async_recalculate_recent_and_report(slot=None)`; new
  `_async_rewrite_hour(slot, last_values) -> None` (under the lock; failure
  logged, not retried; skipped when nothing was touched or the slot does not
  complete an hour).
- Tests: `tests/test_calculation.py` (`TestDropOpenSlot`),
  `tests/test_history_recent_equivalence.py` (`TestNoOpenSlotStatistic`,
  `TestEffyWritesMeanAndStateOnly`, `TestRewriteHourly`; the table stubs are now
  two distinct classes), `tests/test_coordinator_slot.py`
  (`TestFirstJobWaitsForTheRecorder`, `TestHourlyRewriteInTheSlotJob`; five
  older shell tests start the job through the recorder event now, since the
  watchdog no longer starts the first job at once). Written first and shown
  failing against the pre-change code.
- Docs: ADR-016 and ADR-011 Amendments 2026-10-16; `tasks/adr-summary.md`;
  `GLOSSARY.md` (*Open slot*).
- External dependencies added: none; `tasks/DEPENDENCIES.md` unchanged.
