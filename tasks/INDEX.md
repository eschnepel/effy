# Task Index

## Task table

| Slug | Title | Status | Dependencies | Worker |
| -- | -- | -- | -- | -- |
| TASK-0001-jump-window-helper | Expose each real jump's distribution window (pure) | done | — | worker-inline + reviewer-inline |
| TASK-0002-rewrite-start-rule | Pure rule for extended rewrite start + simulation test | done | TASK-0001-jump-window-helper | worker-inline + reviewer-inline |
| TASK-0003-history-extended-rewrite-range | Wire extended range into `async_recalculate_recent` | done | TASK-0001-jump-window-helper, TASK-0002-rewrite-start-rule | worker-inline + reviewer-inline |
| TASK-0004-slot-timer-equivalence-test | Slot timer vs full recalc equivalence test (CAP-1 acceptance) | done | TASK-0003-history-extended-rewrite-range | worker-inline + reviewer-inline |

Execution order: strictly sequential. 0001 and 0002 both edit `calculation.py` /
`tests/test_calculation.py` (shared files → sequential, Phase 3); 0003 consumes
both; 0004 consumes 0003. No parallelism possible.

Process notes (no structural change): worked inline (no sub-agent tool
available), strictly sequential, worker pass and reviewer pass done as separate
steps per task. The TASK-0004 harness was authored while reviewing TASK-0003 (it
was the reviewer's evidence) and then reviewed again on its own as TASK-0004.

## Refinement Log

| Date | Trigger task | Action | Reason |
| -- | -- | -- | -- |
| 2026-10-06 | AUDIT-0001-jump-model-and-rewrite-rule | `trapezoidal_jump_windows` also returns zero-delta offline recoveries (TASK-0001 artifact behaviour change; ADR-014 Amendment 2026-10-06) | audit finding: outage stayed "no data" on the timer path, a full recalc zero-fills it (item 7) |
| 2026-10-06 | AUDIT-0002-slot-timer-integration | `async_recalculate_recent(…, trigger_since=None)`, new `history.recent_trigger_since`, `calculation.recent_trigger_from` / `carry_forward_open_slot`, optional param on `recent_rewrite_start`; `EffyCoordinator._last_recent_run` (TASK-0002/0003 artifacts; ADR-014/016/011 Amendments 2026-10-06) | Issue 1 Option A (dynamic trigger window + one-off catch-up) and Issue 2 option c (open slot carries the previous slot's value) |

## Audit Groups

| Group | Artifacts covered | Source tasks | Rationale |
| -- | -- | -- | -- |
| AUDIT-0001-jump-model-and-rewrite-rule | `calculation.py`: `_find_transitions`, `_distribution_window_start`, `trapezoidal_jump_windows`, `recent_rewrite_start`; `tests/test_calculation.py` new classes | TASK-0001, TASK-0002 | The pure, zero-mock layer: what counts as a "jump" and where the rewrite range starts. One technical concern (ADR-000 §3/§6, ADR-012/013/014 jump semantics). |
| AUDIT-0002-slot-timer-integration | `history.py`: `RECENT_REWRITE_MAX_LOOKBACK`, `_fetch_energy_raw_with_anchor`, `_compute_effective_slots(extend_for_jumps)`, `async_recalculate_recent`; `tests/test_history_recent_equivalence.py` | TASK-0003, TASK-0004 | The recorder-facing glue and its acceptance tests. One technical concern (ADR-003/004/011/013/014/015/016 recorder reads/writes and live push). |

Both groups belong to the single capability CAP-1 (`tasks/adr-capability.md`);
split along the pure/impure boundary because the findings and the
people-decisions differ per side.
