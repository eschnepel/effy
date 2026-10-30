# Task: The history recalculation rebuilds battery and roundtrip efficiency too

- **Status:** todo
- **Capability:** CAP-9
- **Related ADRs:** [ADR-023 (Draft) decision 9]
- **Dependencies:** \[TASK-0022-efficiency-history-rebuild,
  TASK-0026-battery-and-roundtrip-efficiency-sensors\]
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

## Goal

Extend the rebuild of TASK-0022 to the valley machine: reset, replay over the
rewritten slots, continue.

## Acceptance Criteria

- Given a history recalculation, then the valley machine's state equals a replay
  from empty over `min(max_history_days, instance.keep_days)` days; spans that
  start before the window are not formed.
- Given the capacity entity has no history in the window, then no span forms and
  nothing fails.
- Given the rebuild, then the next slot job is idempotent over what it replayed
  and the roundtrip sensor updates.
- Given an error, then the old state stays and the history recalculation
  succeeds.

## Note from the Lead (2026-10-29, while finishing TASK-0025)

The replay half of this task was built inside TASK-0025, because the feed, the
rebuild and their coordinator hook are one piece of machinery (ADR-023 decision
9): `history.async_rebuild_battery` and `coordinator._async_rebuild_battery`,
tested in `tests/test_battery_feed.py` (`TestTheRebuild`) and
`tests/test_coordinator_slot.py` (`TestBatteryRebuildAfterHistory`). Of the
acceptance criteria below those tests already pin: the state equals a replay
from empty over `min(max_history_days, retention)` days with no span before the
window; no capacity history means no span and no failure; the next slot job is
idempotent over what the rebuild replayed; an error keeps the old state and the
history recalculation succeeds. What this task still has to do: the roundtrip
sensor updating after a rebuild (needs TASK-0026), a review of the above against
the real recorder, and the README. Do not build the same thing twice.

## Estimated File / Module Footprint (hint, not a commitment)

`history.py`, `coordinator.py`/`button.py` (call site),
`tests/test_battery_feed.py`.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- `async_rebuild_efficiency` (→ TASK-0022) · the valley machine and feed (→
  TASK-0024, TASK-0025) — **filled by the Lead before start**

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->
