# Task: Expose each real counter jump's distribution window (pure helper)

- **Status:** done
- **Related ADRs:** \[ADR-000, ADR-012, ADR-013, ADR-014 (Amendment 2026-10-05,
  item 2 + 5)\]
- **Dependencies:** []

## Goal

The slot-timer path must know, for every real counter jump in a sensor's raw
history, where its distribution window starts, so it can rewrite back that far.
Provide this as a pure function in `calculation.py`, built on the *same*
transition detection `trapezoidal_slot_contributions` already uses (no
duplicated logic).

## Acceptance Criteria

- Given raw states `[(t1,"5.00"), (t2,"5.01")]` with `t2-t1 < 120 min` When
  `trapezoidal_jump_windows` runs Then it returns `[(t2, t1)]`.
- Given the same with `t2-t1 = 300 min` (normal jump, no offline) Then it
  returns `[(t2, t2 - 120 min)]` (cap = `max_minutes`).
- Given an `unavailable` entry between the two valid readings (offline recovery)
  Then window_start is `t1` (uncapped), whatever the gap.
- Given a zero-delta transition, or a decrease (counter reset → delta clamps to
  0\) Then no entry is returned for it.
- Given 3 consecutive ticks Then the result has 3 entries in chronological
  order, one per real jump.
- The synthetic "now" continuation (ADR-013) never appears in the result (the
  function has no `now` parameter).
- All existing `TestTrapezoidalSlotContributions` tests pass unchanged (pure
  refactor of the shared detection).

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/calculation.py` (extract private transition helper;
  add `trapezoidal_jump_windows`)
- `tests/test_calculation.py` (new `TestTrapezoidalJumpWindows`, zero mocking,
  ADR-000 §6)

## Definition of Done

- Tests green · `ruff format`/`ruff check`/`mypy --strict` clean · no open ADR
  conflicts
- `calculation.py` still has zero HA imports and imports no other Effy module
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none
  expected)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. -->

- `trapezoidal_slot_contributions(raw_states, slot_minutes, max_minutes, now)`
  from `custom_components/effy/calculation.py` (existing; transition-detection
  loop to be factored out, behaviour must not change)
- `_parse_energy_state(state: str) -> float | None` from
  `custom_components/effy/calculation.py` (existing)
- `TRAPEZOID_MAX_MINUTES` from `custom_components/effy/calculation.py`
  (existing, = 120)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->

- `custom_components/effy/calculation.py` →
  - **new public**
    `trapezoidal_jump_windows(raw_states: list[tuple[datetime, str]], max_minutes: int = TRAPEZOID_MAX_MINUTES) -> list[tuple[datetime, datetime]]`
    — `(t2, window_start)` per real jump (valid→valid, delta > 0, incl. offline
    recovery), chronological; `window_start = max(t1, t2 - max_minutes)` normal,
    `t1` offline-recovery; unaligned timestamps; no `now` parameter, no
    synthetic continuation.
  - **new private** `_Transition` (type alias),
    `_find_transitions(raw_states, now=None) -> list[_Transition]`,
    `_distribution_window_start(t1, t2, delta, was_offline, max_window) -> datetime`.
  - **changed (behaviour-preserving)** `trapezoidal_slot_contributions` now
    calls the two private helpers; signature and results unchanged.
- `tests/test_calculation.py` → new `TestTrapezoidalJumpWindows` (11 tests); new
  import `inspect`.
- External dependencies added: none (see `tasks/DEPENDENCIES.md`).
- Note for downstream: ~~a zero-delta offline-recovery transition is
  deliberately *not* a jump~~ — **changed by AUDIT-0001 (2026-10-06, ADR-014
  Amendment 2026-10-06):** `trapezoidal_jump_windows` now also returns offline
  recoveries with delta 0 or a counter reset (`window_start = t1`);
  zero-delta/reset between two consecutive valid readings and the synthetic
  continuation are still excluded. `TestTrapezoidalJumpWindows` has 5 more
  tests.
