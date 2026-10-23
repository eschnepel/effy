# Task Index

## Task table

| Slug | Title | Status | Dependencies | Worker |
| -- | -- | -- | -- | -- |
| TASK-0001-jump-window-helper | Expose each real jump's distribution window (pure) | done | — | worker-inline + reviewer-inline |
| TASK-0002-rewrite-start-rule | Pure rule for extended rewrite start + simulation test | done | TASK-0001-jump-window-helper | worker-inline + reviewer-inline |
| TASK-0003-history-extended-rewrite-range | Wire extended range into `async_recalculate_recent` | done | TASK-0001-jump-window-helper, TASK-0002-rewrite-start-rule | worker-inline + reviewer-inline |
| TASK-0004-slot-timer-equivalence-test | Slot timer vs full recalc equivalence test (CAP-1 acceptance) | done | TASK-0003-history-extended-rewrite-range | worker-inline + reviewer-inline |
| TASK-0005-standby-power-sensor | Investigate and expose the battery system's standby power (CAP-2; Stage 1 investigation + Draft ADR-018 → human gate → Stage 2 implementation, which needs TASK-0007b and TASK-0008, both done now: only the ADR-018 confirmation is missing) | review (Stage 1 done; waiting at the gate) | — | worker-inline + reviewer-inline (Stage 1) |
| TASK-0006-recorder-synchronized-slot-trigger | Recorder-synchronized slot trigger, as Shady's TASK-0041, plus one lock serializing recent and history recalculation (CAP-3; Home Assistant minimum 2026.3) | done | — | worker-inline + reviewer-inline |
| TASK-0007a-effy-writes-after-the-recorder | Effy never inserts a row the recorder will insert: no open-slot statistic, first job waits for the recorder, hourly row rewritten at the hour's last slot (CAP-3; approved 2026-10-16) | done | TASK-0006-recorder-synchronized-slot-trigger | worker-inline + reviewer-inline |
| TASK-0007b-retire-recorder-exclusion | Retire the recorder exclusion of ADR-017 (CAP-3; needed by TASK-0005 Stage 2; approved 2026-10-16) | done | TASK-0007a-effy-writes-after-the-recorder | worker-inline + reviewer-inline |
| TASK-0008-split-input-sensors-by-role | Split the input sensors into PV and BMS inputs (CAP-4, ADR-019 Accepted; silent migration of the old key; needed by TASK-0005 Stage 2) | done | TASK-0006-recorder-synchronized-slot-trigger | worker-inline + reviewer-inline |

Execution order: strictly sequential. 0001 and 0002 both edit `calculation.py` /
`tests/test_calculation.py` (shared files → sequential, Phase 3); 0003 consumes
both; 0004 consumes 0003. No parallelism possible. 0005 is independent of
0001–0004 (all `done`); its Stage 1 changes no production file, and it stops at
a human gate (Draft ADR-018) before Stage 2. 0006 was released 2026-10-16 and
runs first; 0008 follows it (both edit `coordinator.py` and
`tests/test_coordinator_slot.py` → sequential, Phase 3). 0007 stays deferred
until 0006 is `done` and the human has decided the open-slot question; TASK-0005
Stage 2 needs 0007 and 0008.

Process notes (no structural change): TASK-0007b (2026-10-16), same inline flow:
tests first (5 failed against the pre-change code), removal, separate review —
PASS (284 tests, ruff, mypy, format clean; no reference to the removed names is
left in the code). ADR-020 is a **Draft** (supersedes ADR-017, whose status now
says so) until the human confirms it. Honest limits: Shady has no statistics
import and no exclusion, so nothing it validated carries over; Home Assistant
2026.3 and a live instance were not available, so ADR-020's verification rests
on the 2025.1.4 source and the first live check is the human's. TASK-0007a
(2026-10-16), same inline flow: tests first (8 history and 4 coordinator tests
failed on behaviour; the calculation tests failed at collection),
implementation, separate review — PASS (declared symbols checked by script; the
only removed assertions are the old open-slot ones, replaced one for one; 279
tests, ruff, mypy, format clean). Deliberate side effects:
`carry_forward_open_slot` and its tests are removed; five older shell tests
start the job through the recorder event, because the watchdog no longer starts
the first job at once. Residual race, not part of the approved scope: a history
recalculation (button) started in the seconds between a slot boundary and the
recorder's compile at boundary + 10 s still writes the last completed slot
before the recorder has inserted it. Not run: a real Home Assistant recorder;
the update-replaces-every-column behaviour and the compile order are taken from
the 2025.1.4 source. TASK-0008 (2026-10-16) followed TASK-0006, same inline
flow: new tests first (they failed at collection: the constants and `inputs.py`
did not exist), implementation, separate review — PASS (declared symbols checked
by script; the split is proven equivalent to the unsplit list for the full
recalculation and the timer path in the TASK-0004 harness; `VERSION` still 1, no
`async_migrate_entry`; 265 tests, ruff, mypy, format clean). One addition beyond
the task text: the two input fields use a suggested value instead of a default,
so either list can be cleared in the options flow (a default would bring a
cleared list back). Not run: a real Home Assistant config flow and the migration
against a real entry — only stubs here. TASK-0006 (2026-10-16) was worked
inline: tests first (20 new, shown failing against the pre-change code, the two
compile-timing tests on behaviour), then the implementation, then a separate
review pass — PASS: every declared symbol checked against the code by script,
the TASK-0004 equivalence harness unchanged and green, 224 tests, ruff, mypy and
`ruff format --check` clean, `mdformat` clean on the edited files (run with the
`exclude` key of `.mdformat.toml` removed temporarily: it needs Python 3.13+,
the sandbox has 3.12). Limit: the sandbox's package mirror stops at Home
Assistant 2025.1.4, so the event constant and the recorder's compile order were
checked there, not on 2026.3.4; the live behaviour on the human's instance is
unverified. Earlier tasks were worked inline (no sub-agent tool available),
strictly sequential, worker pass and reviewer pass done as separate steps per
task. The TASK-0004 harness was authored while reviewing TASK-0003 (it was the
reviewer's evidence) and then reviewed again on its own as TASK-0004. TASK-0005
Stage 1 (2026-10-13) was reviewed inline against its Stage 1 criteria: PASS,
with one note — the README ADR table (it stops at ADR-008) was not updated for
ADR-018; that is part of Stage 2. After each of the human's two rounds of
amendments the same day it was reviewed again: gates re-run, the quoted
prototype numbers re-derived from the tests, no file under `custom_components/`
changed.

## Refinement Log

| Date | Trigger task | Action | Reason |
| -- | -- | -- | -- |
| 2026-10-06 | AUDIT-0001-jump-model-and-rewrite-rule | `trapezoidal_jump_windows` also returns zero-delta offline recoveries (TASK-0001 artifact behaviour change; ADR-014 Amendment 2026-10-06) | audit finding: outage stayed "no data" on the timer path, a full recalc zero-fills it (item 7) |
| 2026-10-06 | AUDIT-0002-slot-timer-integration | `async_recalculate_recent(…, trigger_since=None)`, new `history.recent_trigger_since`, `calculation.recent_trigger_from` / `carry_forward_open_slot`, optional param on `recent_rewrite_start`; `EffyCoordinator._last_recent_run` (TASK-0002/0003 artifacts; ADR-014/016/011 Amendments 2026-10-06) | Issue 1 Option A (dynamic trigger window + one-off catch-up) and Issue 2 option c (open slot carries the previous slot's value) |
| 2026-10-13 | (external) Chargy ADR-0012 / glossary alignment | added TASK-0005-standby-power-sensor and a CAP-2 proposal in `tasks/adr-capability.md` (pending Gate 1) | standby power is an energy-accounting question that belongs in Effy; Chargy would pick the sensor instead of estimating it |
| 2026-10-13 | (external) human request, Effy glossary session | added deferred TASK-0006-recorder-synchronized-slot-trigger and a CAP-3 proposal in `tasks/adr-capability.md` (pending Gate 1) | the human asked to change the slot trigger the way Shady did (its TASK-0041); not yet shown to matter for Effy, so Stage 0 measures first |
| 2026-10-13 | TASK-0006 | human decisions: dropped Stage 0 (validated for Shady), Home Assistant minimum raised to Shady's 2026.3, added Stage 2 retiring the ADR-017 recorder exclusion | the exclusion only mitigated the race with the recorder writer; the standby sensor (ADR-018) must be recorded |
| 2026-10-13 | TASK-0005 | Draft ADR-018 amended after the human's review: no config option, state machine with 5 % weight, recorded sensor, A2 corrected; prototype tests replaced | human review at the Stage 1 gate |
| 2026-10-13 | TASK-0006 | Stage 2 (retire the ADR-017 recorder exclusion) split out as deferred TASK-0007 | human decision (suggested split): one concern per task and a clean dependency for TASK-0005 Stage 2 |
| 2026-10-13 | TASK-0005 | added TASK-0008-split-input-sensors-by-role and Draft ADR-019 (CAP-4); Draft ADR-018 amended again: peaks, same-value rule, BMS inputs only, A1 confirmed | human: PV gives no evidence what is drain and what is loss; the input list is mixed and must be split, no migration path |
| 2026-10-13 | TASK-0005 | Draft ADR-018 amended again: same-value rule is about peak heights (the human's BMS: 10 Wh steps, high standby = shorter time between peaks), my power-agreement rule dropped; ADR-019 / TASK-0008: mixed pool as before, no migration and no version bump | human clarification of the same-value rule and the loss model |
| 2026-10-13 | TASK-0005 | all open decisions of Draft ADR-018 and ADR-019 taken by the human (learned resolution, exactly one step, delay kept, sensor only with a BMS input, both-lists error, no BMS required); prototype updated | human round 4: all agreed |
| 2026-10-13 | TASK-0005 | resolution without a starting value; the 0.01 tolerance becomes the learned resolution itself; power resolution learned from all non-zero readings; prototype and Draft ADR-018 updated | human correction after round 4 |
| 2026-10-16 | TASK-0006 | released (Gate 2); added the serialization criterion: one lock for the recent and the history recalculation (Shady's `async_run_cache_job`); CAP-3 approved for TASK-0006 only | human round 5: Q3 agreed, Q2 (provisional open-slot statistic vs a recorded sensor; hourly roll-up at minute 55) deferred until TASK-0006 is done |
| 2026-10-16 | TASK-0008 | ADR-019 Accepted with a silent migration of the old key into the PV list (no `VERSION` bump, persisted before the update listener, one warning, new keys win); TASK-0008 depends on TASK-0006 (shared files); CAP-4 approved | human: the Draft's "no migration, set up again" replaced; entry and entity IDs stay the same |
| 2026-10-16 | TASK-0007 | split into [TASK-0007a-effy-writes-after-the-recorder, TASK-0007b-retire-recorder-exclusion]; decided: Effy never writes the open slot's statistic (option A); first job after a restart is held until the first recorder event (Q10); hourly roll-up question still open | human, Q8 and Q10 |
| 2026-10-16 | TASK-0007 | hourly row rewritten by the slot job when the last completed slot starts at minute 55, no second event subscription (Q9); native `min`/`max` of an updated row become NULL (HA `_update_statistics`), so the rewrite also fixes the hourly `min`/`max` | human, Q9; Gate 2 for 0007a/0007b pending |

## Audit Groups

| Group | Artifacts covered | Source tasks | Rationale |
| -- | -- | -- | -- |
| AUDIT-0001-jump-model-and-rewrite-rule | `calculation.py`: `_find_transitions`, `_distribution_window_start`, `trapezoidal_jump_windows`, `recent_rewrite_start`; `tests/test_calculation.py` new classes | TASK-0001, TASK-0002 | The pure, zero-mock layer: what counts as a "jump" and where the rewrite range starts. One technical concern (ADR-000 §3/§6, ADR-012/013/014 jump semantics). |
| AUDIT-0002-slot-timer-integration | `history.py`: `RECENT_REWRITE_MAX_LOOKBACK`, `_fetch_energy_raw_with_anchor`, `_compute_effective_slots(extend_for_jumps)`, `async_recalculate_recent`; `tests/test_history_recent_equivalence.py` | TASK-0003, TASK-0004 | The recorder-facing glue and its acceptance tests. One technical concern (ADR-003/004/011/013/014/015/016 recorder reads/writes and live push). |

Both groups belong to the single capability CAP-1 (`tasks/adr-capability.md`);
split along the pure/impure boundary because the findings and the
people-decisions differ per side.
