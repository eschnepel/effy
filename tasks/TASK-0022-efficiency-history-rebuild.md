# Task: The history recalculation rebuilds charge and discharge efficiency

- **Status:** todo
- **Capability:** CAP-8
- **Related ADRs:** [ADR-023 (Draft) decision 9, ADR-022 (Draft; no dependency)]
- **Dependencies:** [TASK-0021-charge-and-discharge-efficiency-sensors]
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

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

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- `async_update_efficiency` and the machine (→ TASK-0020, TASK-0019) — **filled
  by the Lead before start**
- `history.async_recalculate_history` and its call site (→ TASK-0003-history-
  extended-rewrite-range)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->
