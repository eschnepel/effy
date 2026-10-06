# Audit Task: Slot-timer integration in `history.py` & acceptance tests

- **Status:** done
- **Group:** `history.py` — `RECENT_REWRITE_MAX_LOOKBACK`,
  `_fetch_energy_raw_with_anchor`, `_compute_effective_slots(extend_for_jumps)`,
  `async_recalculate_recent`; `tests/test_history_recent_equivalence.py`
- **Related ADRs:** \[ADR-003, ADR-004, ADR-011, ADR-012, ADR-013, ADR-014
  (Amendment 2026-10-05), ADR-015, ADR-016\]
- **Source Tasks:** \[TASK-0003-history-extended-rewrite-range,
  TASK-0004-slot-timer-equivalence-test\]

## Scope

How the slot-timer recalculation uses the pure layer: global extended range,
second raw fetch, power-family fetch start, bounds, unchanged full recalc, live
push, and the acceptance tests. Audit date 2026-10-05; nothing was modified
while auditing. Probes were run with the committed harness's helpers (plus the
pre-change `history.py` from commit `3ad37b5` for differentials).

## Findings — Code Logic vs. ADRs

- **Energy is still lost when the slot timer was not running for longer than
  `RECENT_RECALC_WINDOW` while a jump arrived** (HA restart, long blocking,
  suspended host). The trigger condition is `now - window <= t2`, so a jump
  older than the window at the first cycle after the pause is never extended;
  its older distribution window stays at stale 0s until a full recalc — the same
  failure the amendment fixed for the always-running case. Reproduced: the
  screenshot scenario with no cycles from 11:55 to 12:40 stores 0.0418 kWh vs
  0.0600 kWh in the full recalc (the 12:14 tick is lost; 12:34 is still caught).
  Original ADR-014 Decision 2 mentioned this only as "long-neglected sensor →
  full recalc". See Open Question 1.
- **Pre-existing, visible in the right edge of the user's chart, not changed by
  this work:** every cycle writes the 5-second-old *in-progress* slot (value ≈
  0, no data for it yet) and ADR-016's live push picks that slot as the "last
  slot" — so the pushed live state is almost always 0 and the statistics graph
  ends in a drop to 0 until the next cycle overwrites it. Reproduced: cycle at
  12:00:05 writes slot 12:00 with 0.0 and pushes `(0.0, 'kW')`. Also, the base
  range start `now - 20 min` is unaligned, so the effective idle window is 15
  min of closed slots plus the open slot (the pure rule keeps this on purpose,
  amendment "Unchanged"). See Open Question 2.
- **Unverified assumption (not an ADR conflict):** the test fake stamps the
  start-of-range raw state with the range start, as HA's
  `state_changes_during_period(include_start_time_state=True)` is believed to
  do; the committed behaviour is correct under either timestamping because the
  margin before the (extended) start keeps every relevant jump's real start
  visible, but this was not confirmed against a real HA instance. Single
  resolution, no choice involved: on the live instance, after the next ticks,
  compare the derived-power series with a manual recalc (the same check that
  validated the diagnosis).
- **Documentation drift (single fix):** ADR-012 Decision 3 and ADR-013 Decision
  5 (and ADR-013's Consequence "`recalculated_from` is normally within a few
  minutes") still read as the final word on the recent window; only ADR-014's
  header points to the amendment. Fix: a one-line "Amended by ADR-014 Amendment
  2026-10-05" cross-reference amendment in ADR-012 and ADR-013. No code impact.
- No other deviations found: full history recalc is unchanged (differential vs.
  `3ad37b5`: same entities, same 432 slot values, same query counts); idle
  cycles make exactly one raw fetch per energy sensor over the unchanged range;
  the extension is global across sensors and series and bounded by
  `RECENT_REWRITE_MAX_LOOKBACK`; the offline-anchor logic is shared by both
  fetches (one implementation); recorder-anchor queries per offline-recovery
  cycle are identical to before (`[0,1,1,1,1]` old vs new); timer path still
  writes short-term only (`include_long_term=False` unchanged in code);
  `async_recalculate_recent`'s signature and return shape are unchanged; 7
  mutants of the new logic were each caught by the acceptance tests.

## Findings — Test Coverage vs. ADRs

- Amendment items 1, 3, 4, 6, 7 (idle unchanged, global range, bound, margin,
  equivalence) → covered fully by `tests/test_history_recent_equivalence.py`
  (screenshot ±jitter, offline recovery, 3-sensor waterfall incl. sparse output
  counter, idle fetch pattern, jump-cycle fetch pattern, bound; negative
  controls included).
- Gap interpolation of power-family inputs (`effy_*_smoothed`,
  ADR-012/gap-smoothing) over the extended range → **not covered by committed
  tests**. A probe (power input with two missing-slot gaps next to the jumpy
  counter) is equivalent to the full recalc, so this is a coverage gap, not a
  bug. Single fix: add that scenario as a test.
- ADR-015 reading cache with the extended fetch → **partially covered**: the
  committed tests pass no shared cache in the bound test; the probe with a
  shared cache showed no regression. Single fix: commit a test asserting
  recorder-anchor queries per cycle with a shared cache stay at the pre-change
  counts.
- ADR-011 Decision 2 (timer path writes short-term only) → **partially
  covered**: the fake writer ignores `include_long_term`. Single fix: have it
  record the flag and assert it is `False` for every timer-path write.
- ADR-016 live push on jump cycles (`touched_entity_ids`, `last_values`) →
  **partially covered**: not asserted on extended cycles. Single fix: assert
  `last_values` come from the newest slot, not the extended start.
- Timer-not-running gap → **not covered** (it currently fails by design);
  depends on the Open Question below.

## Open Questions

- **Issue 1:** A jump that arrives while the slot timer was not running for
  longer than `RECENT_RECALC_WINDOW` is never extended, so its energy stays
  partly lost until a full recalc.
  - Option A: Make the trigger window dynamic: the coordinator remembers when
    the last cycle ran (in memory) and passes it so jumps since then trigger; on
    the first cycle after start-up use a one-off catch-up trigger window of
    `RECENT_REWRITE_MAX_LOOKBACK`. Closes the gap for restarts and pauses;
    touches `coordinator.py` and `async_recalculate_recent`'s signature (an
    ADR-011 matter), plus tests. The first cycle after each HA start may rewrite
    up to a day for sensors that jumped meanwhile.
  - Option B: Accept and document: after HA downtime or a long pause, press the
    recalculate-history button (or restart-time automation). No code change;
    ADR-014 Amendment gets a "Known limitation" note.
  - **Decision:** Option A (human, 2026-10-06). Implemented as `trigger_since` =
    end time of the last *completed* cycle (in memory in `EffyCoordinator`),
    one-off `RECENT_REWRITE_MAX_LOOKBACK` catch-up on the first cycle of a
    session.
- **Issue 2:** The in-progress slot is written (≈0) and pushed as the live value
  every cycle; the idle base window is effectively 15 minutes.
  - Option A: Write only closed slots in the timer path
    (`slot < slot_aligned(now)`), align the base start down to a slot boundary,
    and base ADR-016's live push on the newest *closed* slot. Removes the
    drop-to-0 at the chart edge and a mostly-0 live state; needs an ADR-016
    amendment and tests.
  - Option B: Keep the behaviour (it is what ADR-016 describes) and only
    document it as expected: the last slot is provisional.
  - **Decision:** New option c (human, 2026-10-06): assume the open slot has the
    same value as the last slot; the next cycle's rewrite changes it to the
    correct value — "the guess is better than 0". Implemented for the effective
    and derived-power series on the slot-timer path only; the base-window
    alignment (the "15 minutes" half of this issue) was **not** part of the
    decision and is unchanged.

## Definition of Done (for Phase 8)

- Every Open Question above has a Decision
- Fix implemented per the chosen option(s)
- Test coverage gaps closed (gap interpolation, ADR-015 cache queries,
  short-term-only write flag, live-push values on jump cycles)
- Cross-reference amendments added to ADR-012 and ADR-013
- No new ADR conflicts introduced
- `Delivered Artifacts` block below completed and accurate

## Delivered Artifacts

<!-- Filled by the Worker during Phase 8 -->

- `custom_components/effy/calculation.py` → **new public**
  `recent_trigger_from(now, window, max_lookback, trigger_since=None) -> datetime`;
  **changed** `recent_rewrite_start(..., slot_minutes=5, trigger_since=None)`
  (new optional last parameter, default behaviour unchanged); **new public**
  `carry_forward_open_slot(slot_values, now, slot_minutes=5) -> list[tuple[datetime, float]]`.
  Still imports only `dataclasses`/`datetime` (ADR-000 §3).
- `custom_components/effy/history.py` → **new public**
  `recent_trigger_since(previous_run, now) -> datetime`; **new private**
  `_carry_forward_open_slot(series, now)`; **changed**
  `_compute_effective_slots(..., extend_for_jumps=False, trigger_since=None)`
  and
  `async_recalculate_recent(hass, entry_options, now, energy_reading_cache=None, trigger_since=None)`
  (same 4-tuple return). `async_recalculate_history` unchanged (differential:
  identical values, 308 slots / 5 entities).
- `custom_components/effy/coordinator.py` → **new attribute**
  `EffyCoordinator._last_recent_run: datetime | None` (volatile);
  `_async_recalculate_recent_and_report` passes
  `trigger_since=recent_trigger_since(self._last_recent_run, now)` and advances
  the marker (with `max()`) only after the call returned. Imports
  `recent_trigger_since` from `.history`.
- Tests → `tests/test_calculation.py`: `TestRecentTriggerWindow` (8),
  `TestCarryForwardOpenSlot` (9). `tests/test_coordinator_slot.py`: 4 marker
  tests (first cycle, failed cycle, overlapping cycles, volatile).
  `tests/test_history_recent_equivalence.py`: harness extended (`_Cycle`
  records, `pause` / `restart_after_pause` / `catch_up` options in
  `_run_timer_cycles`, `write_flags`), new `TestCatchUpAfterPause` (8 incl.
  negative controls and policy), `TestOpenSlotProvisionalValue` (5),
  `TestAuditCoverageGaps` (4: gap interpolation over the extended range, ADR-015
  anchor queries `[0, 0, 1, 1, 1]` = pre-change count, short-term-only write
  flag, live push on a jump cycle). Existing tests unchanged apart from
  `_fixed_window_rule` accepting `trigger_since`.
- ADRs → ADR-014 Amendment 2026-10-06 Part 2 (new item 8), ADR-016 Amendment
  2026-10-06, ADR-011 Amendment 2026-10-06, ADR-012 and ADR-013 cross-reference
  amendments (the audit's documentation-drift finding); `tasks/adr-summary.md`
  updated; `TASK-0001/0002/0003` Delivered Artifacts updated.
- No new external dependencies (`tasks/DEPENDENCIES.md` unchanged).
- Verification: 11 mutants of the new logic (trigger_since not forwarded, first
  fetch not widened, rule ignoring it, no lookback clamp, carry-forward missing
  for either series, carry-forward leaking into the full recalc, no adjacency
  check, marker advanced on failure, marker not forwarded, no first-cycle
  catch-up) — all caught. The audit's reproduction (BMS scenario, no cycles
  11:55–12:40) now stores 0.0600 kWh instead of 0.0418, for a pause and for a
  restart. ruff format clean, no new ruff-check findings vs. baseline, mypy
  --strict clean, 167 tests pass. Reviewer: PASS.
- **Left for the human (no code, per the audit):** the "unverified assumption"
  about how a real HA stamps the start-of-range state
  (`include_start_time_state=True`) — on the live instance, after the next
  ticks, compare the derived-power series with a manual recalc.
