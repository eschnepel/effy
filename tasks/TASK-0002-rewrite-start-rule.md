# Task: Pure rule for the extended slot-timer rewrite start

- **Status:** done
- **Related ADRs:** \[ADR-000, ADR-011 (D2), ADR-014 (Amendment 2026-10-05,
  items 1–4, 7)\]
- **Dependencies:** [TASK-0001-jump-window-helper]

## Goal

Implement amendment items 1–4 as one pure, HA-free function: given the jump
windows of all energy sensors, `now`, the base window and the max lookback,
return the (global) start of the slot-timer rewrite range. Also add a pure,
mock-free simulation test that proves the invariant of item 7 at the algorithm
level (cycle-by-cycle store == full recalc; energy conserved).

## Acceptance Criteria

- Given no jump windows When called Then returns exactly `now - window` (current
  behaviour, unaligned — the 15-vs-20-min finding is explicitly out of scope).
- Given a jump with `t2 >= now - window` and `window_start` earlier than
  `now - window` Then returns that `window_start` rounded down to its slot
  start.
- Given several qualifying jumps (possibly from different sensors) Then returns
  the earliest of them (global minimum).
- Given a jump with `t2 < now - window` (already handled by earlier cycles) Then
  it does not extend the range.
- Given a `window_start` earlier than `now - max_lookback` Then the result is
  clamped to `slot-aligned(now - max_lookback)` and never earlier.
- Given a jump whose `window_start >= now - window` Then the base `now - window`
  is returned unchanged (never *later* than the base).
- Simulation test (pure, `trapezoidal_slot_contributions` +
  `trapezoidal_jump_windows` + this function; no mocks): replay the screenshot
  scenario (readings at 07:30, 10:50, 11:25, 11:52, 12:14, 12:34, 12:53, +0.01
  kWh each; cycles every 5 min at HH:MM:05 from 10:00:05 to 13:05:05; each cycle
  overwrites slots in `[start, now)`): the final store equals a full recalc at
  13:05:05 for every slot in `[10:00, 13:05)` and total area = 60 Wh (without
  this task's rule the same simulation yields 30.2 Wh — assert the test fails if
  the rule always returns `now - window`).

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/calculation.py` (new `recent_rewrite_start`)
- `tests/test_calculation.py` (new `TestRecentRewriteStart`,
  `TestSlotTimerEquivalenceSimulation`)

## Definition of Done

- Tests green · `ruff format`/`ruff check`/`mypy --strict` clean · no open ADR
  conflicts
- `calculation.py` still HA-free; `RECENT_REWRITE_MAX_LOOKBACK` is NOT defined
  here (owned by `history.py`, passed in as argument)
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none
  expected)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from TASK-0001's Delivered Artifacts. -->

- `trapezoidal_jump_windows(raw_states: list[tuple[datetime, str]], max_minutes: int = TRAPEZOID_MAX_MINUTES) -> list[tuple[datetime, datetime]]`
  from `custom_components/effy/calculation.py` (→ task:
  TASK-0001-jump-window-helper) — `(t2, window_start)` per real jump,
  chronological, exact (unaligned) timestamps
- `trapezoidal_slot_contributions(...)`, `_slot_aligned(ts, slot_width)`,
  `TRAPEZOID_MAX_MINUTES` from `custom_components/effy/calculation.py`
  (existing)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->

- `custom_components/effy/calculation.py` →
  - **new public**
    `recent_rewrite_start(jump_windows: list[tuple[datetime, datetime]], now: datetime, window: timedelta, max_lookback: timedelta, slot_minutes: int = 5) -> datetime`
    — global rewrite start. No qualifying jump → exactly `now - window`
    (unaligned). Qualifying jump = `now - window <= t2 <= now` and
    `window_start < now - window`; result = earliest such `window_start` rounded
    down to its slot start; clamped to `slot_aligned(now - max_lookback)`; never
    later than `now - window`.
- `tests/test_calculation.py` →
  - new `TestRecentRewriteStart` (10 tests), new
    `TestSlotTimerEquivalenceSimulation` (5 tests, incl. negative control:
    fixed-window rule keeps < 0.04 of 0.06 kWh); new module helpers
    `_sim_fetch`, `_sim_rule_real`, `_sim_rule_fixed`, `_sim_timer_path`,
    `_sim_full_recalc`, `_in_range`; new imports `Callable`,
    `_parse_energy_state`.
- **Changed by AUDIT-0002 (2026-10-06, ADR-014 Amendment 2026-10-06):**
  `recent_rewrite_start(..., slot_minutes: int = 5, trigger_since: datetime | None = None)`
  — new optional last parameter; with it the trigger window reaches back to
  `trigger_since` (never earlier than `now - max_lookback`, never later than
  `now - window`), default behaviour unchanged. **New public**
  `recent_trigger_from(now, window, max_lookback, trigger_since=None) -> datetime`
  (the shared trigger-window start) and
  `carry_forward_open_slot(slot_values, now, slot_minutes=5) -> list[tuple[datetime, float]]`
  (open slot := previous slot's value, only if both exist). New tests
  `TestRecentTriggerWindow` (8), `TestCarryForwardOpenSlot` (9).
- External dependencies added: none.
- **Note for TASK-0003:** the simulation only reproduces an offline-recovery
  jump because its fetch emulates history.py's offline-anchor lookback (a fetch
  starting inside an outage must still prepend the last valid reading). In
  `history.py` the anchor logic must therefore run on *both* the first
  (base-window) fetch that feeds `trapezoidal_jump_windows` and the second
  (extended) fetch.
- Reviewer: mutation check (5 mutants: no trigger-window test, no clamp, no slot
  alignment, max-instead-of-min, offline treated as capped) — all caught.
