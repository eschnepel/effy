# Task: The history recalculation rebuilds charge and discharge efficiency

- **Status:** done — 2026-10-27, reviewer PASS (inline)
- **Capability:** CAP-8
- **Related ADRs:** [ADR-023 decision 9, ADR-022 (Draft; no dependency)]
- **Dependencies:** [TASK-0021-charge-and-discharge-efficiency-sensors]
- **Gate:** ADR-023 accepted 2026-10-27.

## Goal

Every history recalculation resets the charge and discharge machine and replays
it over the slots it has just rewritten, so a reconfigured system gets values at
once instead of after weeks.

## Acceptance Criteria

- Given a history recalculation, then afterwards the machine's state equals a
  replay from empty over the slots of the window
  `min(max_history_days, instance.keep_days)` with the standby statistics the
  recorder holds.
- Given slots without a standby statistic, then they do not count (decision 3)
  and the rebuild does not fail.
- Given the rebuild finishes, then the slot job continues from the last
  processed slot and the next job changes nothing that the replay already
  counted (idempotence test).
- Given the hook, then it is called where the recalculation is triggered (the
  caller of `async_recalculate_history`), not inside
  `history.async_recalculate_history`, so it does not conflict with the Draft
  ADR-022 tasks that rework that function.
- Given a rebuild error, then it is logged, the old state stays and the history
  recalculation itself still succeeds.

## Estimated File / Module Footprint (hint, not a commitment)

`coordinator.py` and/or `button.py` (the call site), `history.py`
(`async_rebuild_efficiency`, reusing the feed), `tests/test_efficiency_feed.py`,
README.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only)

## Consumed Interfaces

- From TASK-0020: `history.async_update_efficiency` (now with an optional
  `window`), `EFFICIENCY_CATCHUP_MAX`, `efficiency_roles`, `efficiency_enabled`;
  `coordinator.efficiency_state`, `efficiency_enabled`,
  `_efficiency_subscribers`.
- `history._get_short_term_retention_days` (→ TASK-0005's `keep_days` handling),
  `CONF_MAX_HISTORY_DAYS`, `DEFAULT_MAX_HISTORY_DAYS`.
- `coordinator.async_recalculate_history_and_report` (the caller of
  `history.async_recalculate_history`; the button calls it).

## Delivered Artifacts

- `history.py`: `efficiency_rebuild_window(options, keep_days) -> timedelta`
  (`min(max_history_days, keep_days)` days);
  `async_rebuild_efficiency(hass, options, now) -> RunState` (a replay from an
  empty state over that window);
  `async_update_efficiency(..., window=EFFICIENCY_CATCHUP_MAX)` takes the window
  as an optional argument, the default is the 3 days of TASK-0020.
  `async_recalculate_history` is **untouched** (no mention of efficiency in it).
- `coordinator.py`: `_async_rebuild_efficiency()`, called in
  `async_recalculate_history_and_report` under the lock, after `notify_updated`
  and before the closing step; it needs a restored machine and a complete pair,
  logs a failure and keeps the old state, lets a cancellation through, and tells
  the efficiency sensors.
- README (the efficiency section).
- Tests: `tests/test_efficiency_feed.py`
  (`TestRebuildAfterTheHistoryRecalculation`, 8),
  `tests/test_coordinator_slot.py` (`TestEfficiencyRebuildAfterHistory`, 6).
- External dependencies added: none.

## Review

**PASS** (inline; 819 tests, ruff, mypy on source and tests). The rebuild equals
the incremental result and a replay from empty; it ignores a stale previous
state (a reconfigured system); the next jobs continue from its last slot and
agree with a new replay at every step (idempotence); missing standby statistics
cost counted energy and never fail it; it reads only inside the window and
writes nothing; the hook is at the caller, and `async_recalculate_history`'s
source does not mention efficiency. Twenty-one mutants of this and TASK-0020's
glue are caught.

Two of them **survived the first time**, which were gaps in my tests, not in the
code: the window passed to the replay was invisible because the simulation (10
h) fits inside a 3-day and a 5-day window alike (now a spy on the call checks
it), and a coordinator test lost its own evidence because building the second
coordinator reset the recorded calls (now asserted before that).

### The test cost found on the way (2026-10-27, reported by the human)

`tests/test_efficiency_feed.py` of TASK-0020 and TASK-0021 took 130 s and the
suite 38 s (3 s before). Profile of one simulation pass (9.5 s, 109 cycles of
the real recalculation): 62 % in `trapezoidal_slot_contributions` and
`trapezoidal_jump_windows`, because the simulated counters ticked on every Wh
(about 9,000 readings per counter, one per second at 3 kW); 6 % each for the
fake recorder's raw fetch and the standby update; **0.3 % for the efficiency
feed itself**. A real integration reports every few seconds to a minute: the
Pi's own record shows 156 raw states and 0.19 s for a recent recalculation. Fix,
in the test file only: the simulated counter is polled every 30 s (a state only
when a poll sees a new value), 361 readings per counter, 0.21 s per pass, the
file 1.7 s, the suite 4.8 s; no assertion changed and the estimates are the same
(charge 95.0 %, discharge 89.1 %). A guard test keeps the density below 600
readings per counter. Not changed: production redistribution cost grows with the
readings per window; a very chatty real sensor is the one that would feel it.
