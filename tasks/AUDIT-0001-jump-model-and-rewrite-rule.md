# Audit Task: Jump model & rewrite-start rule (pure layer)

- **Status:** done
- **Group:** `calculation.py` — `_find_transitions`,
  `_distribution_window_start`, `trapezoidal_jump_windows`,
  `recent_rewrite_start`; `tests/test_calculation.py` —
  `TestTrapezoidalJumpWindows`, `TestRecentRewriteStart`,
  `TestSlotTimerEquivalenceSimulation`
- **Related ADRs:** [ADR-000, ADR-012, ADR-013, ADR-014 (Amendment 2026-10-05)]
- **Source Tasks:** [TASK-0001-jump-window-helper, TASK-0002-rewrite-start-rule]

## Scope

What counts as a counter "jump" and how the slot-timer's rewrite start is
derived from jump windows (amendment items 1–5, 7 at the algorithm level). Audit
date 2026-10-05; nothing was modified while auditing.

## Findings — Code Logic vs. ADRs

- **Deviation from amendment item 7 (invariant), by the wording of item 2:**
  `calculation.trapezoidal_jump_windows` excludes zero-delta transitions,
  including a zero-delta *offline recovery* (counter unchanged across an outage,
  e.g. 5.00 → unavailable → 5.00). A full recalc writes explicit zeros over that
  outage (ADR-013 Decision 4); the slot-timer path never rewrites back to it,
  because nothing triggers the extension. Reproduced with the history harness:
  08:00 reading, `unavailable` 08:30–11:00, recovery reading equal to the first
  — 27 outage slots exist in the full-recalc store and are missing from the
  cycle-by-cycle store, all other slots equal. Effect is "no data" instead of
  "0" during the outage until the next full recalc; no energy is lost (delta is
  0). See Open Question 1.
- No other deviations found: `calculation.py` still imports only
  `dataclasses`/`datetime` (ADR-000 §3); transition detection and window-start
  rule exist exactly once and are shared (amendment item 5); the refactored
  `trapezoidal_slot_contributions` produced identical output to the pre-change
  version on 20,000 randomized histories (reviewer check, TASK-0001);
  `recent_rewrite_start` returns exactly `now - window` when idle, extends only
  for jumps with `now - window <= t2 <= now`, clamps to the slot-aligned
  `now - max_lookback`, and is never later than the base (items 1, 2, 4);
  `RECENT_REWRITE_MAX_LOOKBACK` is not defined in `calculation.py` (ADR-000 §5).

## Findings — Test Coverage vs. ADRs

- Item 1 (idle = `now - window`, unaligned) → covered fully,
  `TestRecentRewriteStart::test_no_jump_windows_returns_exactly_now_minus_window`.
- Item 2 (trigger window, global minimum, slot alignment, future/older jumps) →
  covered fully; 5 mutants (no trigger test, no clamp, no alignment,
  max-instead-of-min, offline treated as capped) were each caught.
- Item 4 (lookback clamp, never later than base) → covered fully
  (`test_extension_is_clamped_...`,
  `test_clamp_never_makes_the_start_later_than_the_base`).
- Item 5 (single source of truth) → covered, by
  `test_window_start_matches_where_slot_contributions_actually_begin` plus the
  whole pre-existing `TestTrapezoidalSlotContributions` suite. The 20k-case
  differential was a reviewer-only check and is **not** committed as a test
  (partially covered; acceptable given the shared helper).
- Item 7 at algorithm level → covered for the screenshot scenario (± jitter) and
  normal offline recovery; **not covered** for zero-delta offline recovery
  (consistent with the finding above).
- ADR-013 Decision 4 (explicit zero-fill) interplay with jump windows → covered
  only indirectly through the equivalence simulation.

## Open Questions

- **Issue 1:** Zero-delta offline recovery is not a jump, so the timer path
  leaves the outage unwritten while a full recalc zero-fills it (violates item 7
  as worded).
  - Option A: Make *every* offline recovery a trigger (include zero-delta
    recoveries in `trapezoidal_jump_windows`, window start = `t1`), still
    bounded by `RECENT_REWRITE_MAX_LOOKBACK`. Item 7 then holds without
    exception; costs one wide (≤1 day) rewrite per outage recovery. Needs
    ADR-014 Amendment item 2 reworded ("real jump" → "real jump or offline
    recovery") and a new test.
  - Option B: Keep the behaviour and reword item 7 to except zero-delta offline
    recoveries ("no data" during an outage is arguably more truthful than 0, and
    the next full recalc fills it). No code change; documentation only.
  - **Decision:** Option A (human, 2026-10-06). Every offline recovery is a
    trigger, whatever its delta.

## Definition of Done (for Phase 8)

- Every Open Question above has a Decision
- Fix implemented per the chosen option(s)
- Test coverage gap closed
- No new ADR conflicts introduced
- `Delivered Artifacts` block below completed and accurate

## Delivered Artifacts

<!-- Filled by the Worker during Phase 8 -->

- `custom_components/effy/calculation.py` →
  `trapezoidal_jump_windows(raw_states, max_minutes)` — **behaviour change,
  signature unchanged**: an offline-recovery transition is now returned even
  when its delta is 0 (or a counter reset), with `window_start = t1` (uncapped).
  Zero-delta/reset transitions between two directly consecutive valid readings
  and the synthetic "now" continuation are still excluded.
  `trapezoidal_slot_contributions`, `_find_transitions`,
  `_distribution_window_start`, `recent_rewrite_start` untouched (differential
  on 20,000 random histories: 0 slot-contribution differences; trigger set only
  grew by offline zero-delta recoveries).
- `tests/test_calculation.py` → new:
  `TestTrapezoidalJumpWindows::{test_zero_delta_offline_recovery_is_a_trigger_starting_at_t1, test_zero_delta_offline_recovery_is_uncapped, test_counter_reset_across_an_outage_is_a_trigger, test_non_numeric_state_without_recovery_is_not_a_trigger, test_zero_delta_after_recovery_is_still_not_a_jump}`,
  `TestRecentRewriteStart::test_zero_delta_offline_recovery_extends_to_the_pre_outage_slot`,
  `TestSlotTimerEquivalenceSimulation::test_zero_delta_offline_recovery_zero_fills_the_whole_outage`.
- `tests/test_history_recent_equivalence.py` → new
  `TestSlotTimerEqualsFullRecalc::test_zero_delta_offline_recovery_zero_fills_the_outage`
  (the audit's reproduction: 30 outage slots, all explicit 0, timer store ==
  full-recalc store).
- `adr/014-…md` → Amendment 2026-10-06 (item 2 widened to offline recoveries) +
  pointer in item 2; `tasks/adr-summary.md` updated.
- No new external dependencies (`tasks/DEPENDENCIES.md` unchanged).
- Verification: the 7 new tests fail against the pre-change `calculation.py` and
  pass now; ruff format clean, no new ruff-check findings vs. baseline, mypy
  --strict clean, 129 tests pass. Reviewer: PASS.
