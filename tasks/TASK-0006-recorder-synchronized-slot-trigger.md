# Task: Recorder-Synchronized Slot Trigger (Deferred — Same Change as Shady's TASK-0041)

- **Status:** todo
- **Related ADRs:** \[ADR-010, ADR-011 (Decision 3 and Amendment 2026-10-06),
  ADR-014, ADR-016\]
- **Dependencies:** []

## Goal

**DEFERRED — do not start until the human releases it.** Recorded 2026-10-13 at
the human's request so the idea is not lost; it is not part of CAP-1 or CAP-2
and blocks nothing.

Effy's slot timer fires `SLOT_TIMER_LAG_SECONDS` (5 s) after every slot boundary
(ADR-011 Decision 3, `coordinator.py`), on the wall clock. Shady (the sibling
project) found that the recorder compiles each slot's 5-minute statistics about
10 s after the boundary and replaced its wall-clock tick with the recorder's own
signal, `EVENT_RECORDER_5MIN_STATISTICS_GENERATED` (Shady
`TASK-0041-recorder-synchronized-slot-trigger`, `done` 2026-10-08). Change
Effy's slot trigger the same way, so the recent recalculation never starts
before the last completed slot's statistics can be read.

**Decided by the human (2026-10-13):**

- The problem was **validated already for Shady**; there is no Stage 0
  reproducer that decides whether to proceed. The task goes straight to building
  (a failing-first test is still required, see below).
- The Home Assistant minimum is raised to **the same as Shady's: 2026.3** (Shady
  `hacs.json`; Effy's is 2024.1.0).
- Parameters as Shady: probe every 1 s, at most 5 probes, watchdog 6 min; the
  wall-clock timer stays as a watchdog; the supersession of ADR-011 Decision 3
  and ADR-010 is recorded as Amendments once the human confirms.
- Retiring the recorder exclusion of ADR-017 (no longer needed once this is
  done) is its own task, TASK-0007.

**Facts carried over from Shady (re-verify against the pinned version):**

- The recorder compiles live 5-minute statistics via
  `async_track_utc_time_change(..., minute=range(0, 60, 5), second=10)`.
- It fires `EVENT_RECORDER_5MIN_STATISTICS_GENERATED` (a constant in
  `homeassistant.const`, no payload) from inside the compile, **before** the
  session commits.
- The startup catch-up compile fires the event once, for its last missing
  period.
- `Recorder.async_block_till_done()` is not a barrier for the running statistics
  task; do not use it.

## Acceptance Criteria

- Given a simulated recorder that compiles each slot's power-family `mean` 10 s
  after its boundary (readable a few hundred ms later) and the pre-change slot
  timer at boundary + 5 s, when the recent recalculation runs, then the last
  completed slot's power-family statistic is not yet readable. This test is
  written first and fails against the pre-change code.
- Given the coordinator is set up, then it subscribes with
  `hass.bus.async_listen(EVENT_RECORDER_5MIN_STATISTICS_GENERATED, ...)`, keeps
  the unsubscribe, and `async_shutdown` removes it and cancels any in-flight
  slot job.
- Given an event, then the handler is a `@callback` that only schedules a task
  on the loop; no recorder access, no blocking work.
- Given a slot job, then it probes until the last completed slot is readable for
  every power-family sensor that had a value in the slot before (a sensor with
  no data never delays the job), waiting `PROBE_INTERVAL` with `await` on the
  loop and probing at most `MAX_PROBES` times, then runs the former timer body
  (`_async_recalculate_recent_and_report`) once.
- Given two triggers for the same last completed slot (duplicate event, or
  watchdog right after an event), then the body runs once; given a newer event
  while an earlier job is still probing, then the earlier job ends without
  running its body.
- Given the existing wall-clock timer, then it stays as a watchdog that starts a
  job only if none was started for `WATCHDOG`.
- Given the CAP-1 equivalence harness (TASK-0004), then it is unchanged and
  green: slot-timer output still equals a history recalculation at the same
  time.
- Given `hacs.json`, then its `homeassistant` minimum is 2026.3, and the README
  states it if it states a minimum anywhere.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/coordinator.py` (`async_setup`, `async_shutdown`,
  `_on_slot_timer`, `_schedule_next_slot_timer`, `_next_slot_trigger_delay`,
  `SLOT_TIMER_LAG_SECONDS`, new slot-job state, module banner text);
  `hacs.json`; `tests/test_coordinator_slot.py` and a new test for the compile
  timing; ADR-010/011 text.

## Definition of Done

- Tests green; each new behaviour test shown to fail against the pre-change code
  first.
- `mypy --config-file mypy.ini custom_components/ tests/`, `ruff check .`,
  `ruff format --check .` clean; `mdformat --check` clean on edited `.md` files.
- ADR-011/ADR-010 Amendments present and consistent with what was built;
  `tasks/adr-summary.md` and `GLOSSARY.md` (*Slot timer* becomes the
  recorder-synchronized trigger) accurate.
- No new external dependency (the event constant comes from `homeassistant`);
  `tasks/DEPENDENCIES.md` unchanged.
- `Delivered Artifacts` block completed and accurate.

## Consumed Interfaces

<!-- Read from the current code; refresh before the task starts. -->

- `EffyCoordinator.async_setup(self) -> None`,
  `EffyCoordinator.async_shutdown(self) -> None`,
  `EffyCoordinator._on_slot_timer(self, _now: Any) -> None` (`@callback`),
  `EffyCoordinator._async_recalculate_recent_and_report(self) -> None`,
  `EffyCoordinator._schedule_next_slot_timer(self) -> None`, attribute
  `_unsub_slot_timer`, from `custom_components/effy/coordinator.py` (→ task:
  TASK-0004-slot-timer-equivalence-test, `done`)
- `coordinator._next_slot_trigger_delay(now, slot_minutes, lag_seconds) -> float`
  and `SLOT_TIMER_LAG_SECONDS` from `custom_components/effy/coordinator.py`
- `history.async_recalculate_recent(...)` and
  `history.recent_trigger_since(...)` from `custom_components/effy/history.py`
  (→ task: TASK-0003-history-extended-rewrite-range, `done`)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->
