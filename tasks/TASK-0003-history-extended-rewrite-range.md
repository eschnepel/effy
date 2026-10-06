# Task: Wire the extended rewrite range into the slot-timer recalculation

- **Status:** done
- **Related ADRs:** \[ADR-000, ADR-003, ADR-004, ADR-011, ADR-012, ADR-013,
  ADR-014 (Amendment 2026-10-05, items 1–6), ADR-015\]
- **Dependencies:** [TASK-0001-jump-window-helper, TASK-0002-rewrite-start-rule]

## Goal

`history.async_recalculate_recent` rewrites back to the earliest slot touched by
any newly arrived jump (any energy-family sensor), for *all* sensors and all
three series, bounded by a 1-day lookback — so the slot-timer output equals a
full recalc. Idle cycles must behave and cost exactly as today.

## Acceptance Criteria

- Given no jump in the last `RECENT_RECALC_WINDOW` Then range, queries, writes
  and return values are identical to today (idle-cycle regression guard).
- Given a jump with `t2` in the last `RECENT_RECALC_WINDOW` Then every sensor's
  slots from `recent_rewrite_start(...)` up to `now` are recomputed and written
  (all three series, short-term only — `include_long_term=False` unchanged).
- The extended start is global: power-family sensors' statistics are fetched
  from it too, and no sensor is computed over a narrower range than another.
- Raw energy history is fetched from
  `extended_start - _RAW_HISTORY_BOUNDARY_MARGIN`; the second raw fetch happens
  only when the range was actually extended.
- Offline-recovery jumps use their pre-outage reading as `t1` (anchor lookback /
  ADR-015 cache logic unchanged and applied before jump windows are derived);
  extension is clamped by `RECENT_REWRITE_MAX_LOOKBACK = timedelta(days=1)`
  (module constant at the top of `history.py`, ADR-000 §5).
- `async_recalculate_recent` signature and 4-tuple return are unchanged;
  `recalculated_from` is the earliest slot written (naturally the extended start
  on a jump cycle).
- `async_recalculate_history` (full recalc) results are unchanged.
- Comments/docstrings that describe the old "accepted staleness trade-off"
  (`RECENT_RECALC_WINDOW` comment, `async_recalculate_recent`,
  `_compute_effective_slots`) are updated to reference the ADR-014 amendment.
- Sensor classification (units / state class → energy vs power ids) is not
  duplicated: extract a shared helper or pass results through.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/history.py` (`RECENT_REWRITE_MAX_LOOKBACK`,
  `async_recalculate_recent`, `_compute_effective_slots`, possibly small
  extracted helpers)

## Definition of Done

- Tests green (existing + TASK-0004) · `ruff`/`mypy --strict` clean · no open
  ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none
  expected)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from TASK-0001/0002 Delivered Artifacts. -->

- `trapezoidal_jump_windows(raw_states, max_minutes=TRAPEZOID_MAX_MINUTES) -> list[tuple[datetime, datetime]]`
  from `custom_components/effy/calculation.py` (→ task:
  TASK-0001-jump-window-helper)
- `recent_rewrite_start(jump_windows: list[tuple[datetime, datetime]], now: datetime, window: timedelta, max_lookback: timedelta, slot_minutes: int = 5) -> datetime`
  from `custom_components/effy/calculation.py` (→ task:
  TASK-0002-rewrite-start-rule)
- Existing: `trapezoidal_slot_contributions`, `_parse_energy_state`,
  `_slot_aligned`, `TRAPEZOID_MAX_MINUTES` from
  `custom_components/effy/calculation.py`; `RECENT_RECALC_WINDOW`,
  `_RAW_HISTORY_BOUNDARY_MARGIN`, `_fetch_raw_energy_states`,
  `_fetch_last_valid_state_before`, `_write_recorder_statistics`,
  `_last_slot_values` in `custom_components/effy/history.py`

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->

- `custom_components/effy/history.py` →
  - **new constant** `RECENT_REWRITE_MAX_LOOKBACK = timedelta(days=1)` (module
    level, next to `RECENT_RECALC_WINDOW`).
  - **new private**
    `_fetch_energy_raw_with_anchor(hass, recorder, eid, raw_history_start, end, max_history_days, energy_reading_cache) -> list[tuple[datetime, str]]`
    — the raw fetch + offline-anchor lookback + ADR-015 cache warm-up, extracted
    verbatim from `_compute_effective_slots` so both fetches share one
    implementation.
  - **changed**
    `_compute_effective_slots(hass, entry_options, start, end, energy_reading_cache=None, extend_for_jumps: bool = False)`
    — with `extend_for_jumps=True` it derives jump windows from all energy
    sensors' raw history, asks
    `recent_rewrite_start(..., end, end - start, RECENT_REWRITE_MAX_LOOKBACK, SLOT_MINUTES)`
    and, only if that is earlier than `start`, rebinds `start` (global, all
    sensors/series) and refetches raw history from
    `start - _RAW_HISTORY_BOUNDARY_MARGIN`. Power-family statistics are now
    fetched *after* this decision, from the (possibly extended) `start`.
    Full-recalc call path unchanged (`extend_for_jumps=False`).
  - **changed** `async_recalculate_recent` passes `extend_for_jumps=True`;
    signature and 4-tuple return unchanged; `recalculated_from` is naturally the
    earliest slot written.
  - **imports added** `recent_rewrite_start`, `trapezoidal_jump_windows` (from
    `calculation`). Docstrings/comments describing the old accepted-staleness
    trade-off updated (`RECENT_RECALC_WINDOW` comment,
    `async_recalculate_recent`, `_compute_effective_slots`).
- **Changed by AUDIT-0002 (2026-10-06, ADR-014/016/011 Amendments 2026-10-06):**
  - **new public**
    `recent_trigger_since(previous_run: datetime | None, now: datetime) -> datetime`
    — `previous_run` or, on the first cycle of a session,
    `now - RECENT_REWRITE_MAX_LOOKBACK`.
  - **changed**
    `_compute_effective_slots(..., extend_for_jumps=False, trigger_since: datetime | None = None)`
    — forwards it to `recent_rewrite_start`; the *first* raw fetch starts
    `_RAW_HISTORY_BOUNDARY_MARGIN` before
    `min(start, recent_trigger_from(...))`.
  - **changed**
    `async_recalculate_recent(hass, entry_options, now, energy_reading_cache=None, trigger_since=None)`
    — same 4-tuple; the open slot (containing `now`) of the effective and
    derived-power series is written with the previous slot's value (**new
    private** `_carry_forward_open_slot`), so `last_values` carries that guess.
    `async_recalculate_history` unchanged.
  - **imports added** `carry_forward_open_slot`, `recent_trigger_from`.
- No new external dependencies.
- Reviewer evidence: differential full recalc old vs new `history.py` (4
  sensors, 7 entities, 432 slots) identical values and identical query counts; 7
  mutants on the new logic killed by `tests/test_history_recent_equivalence.py`
  (revert to pre-change, extension disabled, per-sensor power fetch start, no
  second raw fetch, no lookback bound, tiny trigger window, no margin on
  extended fetch).
- Known unverified assumption (see Audit): the test fake stamps the
  start-of-range raw state with the range start (HA's
  `include_start_time_state=True` behaviour as far as known); not verified
  against a real HA instance.
