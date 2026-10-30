# Task: The history recalculation rebuilds battery and roundtrip efficiency too

- **Status:** done — 2026-10-30 (inline)
- **Capability:** CAP-9
- **Related ADRs:** [ADR-023 decision 9]
- **Dependencies:** \[TASK-0022-efficiency-history-rebuild,
  TASK-0026-battery-and-roundtrip-efficiency-sensors\]
- **Gate:** ADR-023 accepted 2026-10-27.

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

- No production code: the replay (`history.async_rebuild_battery`,
  `coordinator._async_rebuild_battery`, called after the efficiency rebuild and
  before the closing step) was delivered by TASK-0025, see the note above.
- Test added:
  `test_a_history_rebuild_replaces_both_machines_and_the_roundtrip_follows` in
  `tests/test_efficiency_sensor.py` (the roundtrip sensor takes the rebuilt
  efficiency and valley states, and is unavailable, not stale, when the rebuild
  finds no span). The other criteria stay pinned by `TestTheRebuild` and
  `TestBatteryRebuildAfterHistory`.
- README: the rebuild of the battery and roundtrip efficiency.
- Not verifiable here: the rebuild against a real recorder; only the faked
  recorder reads of the tests were exercised.
- External dependencies added: none.
