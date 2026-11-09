# Task: The history button also rebuilds the standby machine, and every machine shows its first processed slot

- **Status:** done — closed by the human 2026-11-09 (was review since
  2026-11-01; built and checked inline; no separate Stage 1: the human decided
  the behaviour; the check on the instance was outstanding, see Findings)
- **Capability:** CAP-2 (standby), CAP-8 (efficiency), CAP-9 (battery)
- **Related ADRs:** \[ADR-018 §5 (the standby machine's replay; **amended by
  this task, the human's decision**), ADR-023 decision 9 (the efficiency and
  battery rebuild), ADR-012 and ADR-021 (attributes), ADR-025 (the button clears
  the cache)\]
- **Dependencies:** [TASK-0036-recorder-read-cache] (`review`: the rebuild reads
  through the storage the button has just cleared)

## Goal

The human (2026-11-01), after the question of what the button rebuilds:
*"Standby should be included in button recalc. All three sensors show at max 3
runs or intervals. To validate what is included add an attribute for the first
evaluated slot."* The second sentence is a **bug report** (a symptom seen in the
data), not a decision about how many there should be; the first and third are
the decisions.

1. **The history button rebuilds the standby machine** like it rebuilds the
   efficiency and battery machines: reset, then replay over the window
   `min(max_history_days, recorder keep_days)`.
1. **The symptom is explained, or its cause is found.** All three sensors show
   at most 3 counted intervals, runs or spans. Nothing in the code caps a count
   (they are plain increments), so the cause is how long each machine's state
   has lived; the findings say what the code and the data can and cannot show.
1. **Every machine says where its evaluation began.** The standby, charge and
   discharge efficiency, battery efficiency and roundtrip sensors get the
   attribute `first_processed_slot`: the first slot the machine took since its
   state began (a reset, which a button press or a first start is). It is
   persisted, so it survives a restart. With `last_processed_slot` it tells what
   a replay covered.

## Context (what was validated before, 2026-11-01)

- The button flow was history recalculation, efficiency rebuild, battery
  rebuild. The standby machine was fed only by the slot job and replayed at a
  restart (24 hours back, at most `STANDBY_REPLAY_MAX` = 3 days). Test
  `TestWhatTheHistoryButtonRebuilds` pinned that.
- The efficiency machine's default window is `EFFICIENCY_CATCHUP_MAX` = 3 days
  (the restart catch-up); the button widens it. That is probably where "at most
  3 runs or intervals" comes from after a restart, and the new attribute will
  show it.
- None of the three sensors has statistics that Effy writes: their history is
  the recorder's record of the live state. The rebuild corrects the current
  value and the attributes, not the past graph. The efficiency replay reads the
  **recorded** `sensor.effy_standby_power` statistic of each slot (ADR-023
  decision 3), which a standby rebuild does not rewrite.

## Acceptance Criteria

- Given the symptom, then the findings state which windows and data limits can
  produce it (the 3-day first-start caps, the age of the recorded standby
  statistic) and how the new attribute tells them apart from a defect in a
  rebuild.
- Given a history recalculation by the button and a restored standby machine,
  then after the efficiency-independent steps the standby machine is rebuilt
  **before** the efficiency rebuild, under the recorder-job lock, from a fresh
  state over `min(max_history_days, keep_days)`; the standby sensor is told.
- Given the standby machine is not enabled (no BMS input) or the sensor has not
  restored, then the button leaves it alone, as it does for the other machines.
- Given the standby rebuild fails, then the failure is logged, the old state
  stays and the history recalculation, the efficiency and battery rebuilds still
  run; a cancellation propagates.
- Given a machine that takes its first slot, then `first_slot` is that slot; a
  later slot, a gap that ends a run or a span, a slot without SoC and a repeated
  feed do not change it; a slot at or before the last processed one (ignored)
  does not set it.
- Given a persisted state, then `first_slot` is saved and restored, and data
  without the key restores with `first_slot` unset (the next slot taken sets
  it).
- Given each of the sensors, then its attributes carry `first_processed_slot` as
  an ISO time (or `None`), beside `last_processed_slot`; the roundtrip sensor
  shows the later of its two machines' first slots.
- Given the README, then it says the button rebuilds standby, what
  `first_processed_slot` means and that a rebuild does not rewrite the sensors'
  history.
- The whole suite, `ruff`, `mypy` and `mdformat` pass; no existing assertion is
  weakened.

## Estimated File / Module Footprint (hint, not a commitment)

`calculation.py` (the three states, their steps and persistence), `history.py`
(`async_update_standby(window=)`, `async_rebuild_standby`), `coordinator.py`
(`_async_rebuild_standby`, the button flow), `sensor.py` (four attribute
builders), `tests/test_standby*.py`, `test_efficiency_*.py`,
`test_valley_spans.py`, `test_battery_feed.py`,
`tests/test_coordinator_slot.py`, `README.md`, `adr/018-*.md`.

## Definition of Done

- Tests green, docs updated, no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependency

## Consumed Interfaces

- `DrainState`, `RunState`, `ValleyState` and their restore functions in
  `custom_components/effy/calculation.py`
- `async_update_standby`, `async_rebuild_efficiency`,
  `efficiency_rebuild_window` in `custom_components/effy/history.py`
- `EffyCoordinator._async_rebuild_efficiency`,
  `async_recalculate_history_and_report`

## Open points for the human

1. A rebuild starts from a fresh standby state, so an estimate learned over
   months is replaced by one learned over the window; if the window holds no
   counted interval the sensor is `unavailable` until one is counted (and the
   efficiency sensors with it). The efficiency rebuild already behaves like
   this. The alternative (keep the old estimate when the replay counts nothing)
   is not built.

## Findings

### Built (2026-11-01)

`first_slot` on `DrainState`, `RunState` and `ValleyState` (set once, kept
through gaps and replays, saved and restored; data without it restores without
it); `first_processed_slot` on the standby, charge, discharge, battery and
roundtrip sensors (the roundtrip shows the later of its two machines' first
slots, and `None` while either is unknown);
`history.async_update_standby(window=)` and `async_rebuild_standby`; the
coordinator's `_async_rebuild_standby`, run by the button after the history
recalculation and **before** the efficiency rebuild, under the lock, failing
soft (old state stays) and propagating a cancellation, skipped without a BMS
input or before the sensor restored.

Tests: pure tests of the three machines (`TestFirstSlot` in `test_standby.py`,
`test_efficiency_runs.py`, `test_valley_spans.py`), the history-level rebuild
(`TestTheButtonRebuild` in `test_standby_glue.py`), the coordinator
(`TestStandbyRebuildAfterHistory`, `TestWhatTheHistoryButtonRebuilds`), the
sensor attributes. Twenty-one mutants of the new behaviour were run and all are
killed: the first slot moving, not set, lost in a gap reset, not saved, not
restored (three machines); the window ignored, the final cap not widened, a
rebuild that is not fresh; no rebuild, standby after efficiency, run before a
restore or without a BMS input, sensor not told, a failure that carries on; the
roundtrip taking the earlier slot or showing with one machine unknown; no
attribute. One mutant first looked as if it survived because its pattern did not
match the file; it was rerun with the exact text and killed. Not every test was
written before the code; the mutants stand in for a failing-first run.

Existing tests changed, nothing weakened: the battery-feed dump comparison now
leaves out `first_slot` (where a machine began, not what it measured; the live
machine began before the data, the direct one at it, and the test now asserts
both), the order expectations of two coordinator tests gained the
`rebuild_standby` step, four sensor expectations gained the new attribute, a spy
accepts the `storage` keyword.

### The symptom: all three sensors show at most 3 runs or intervals

What the code and the harness establish (not the instance):

- **No count is capped.** `count`, `counted_runs` and `counted_spans` are plain
  increments. What limits them is how long a machine's state has lived and which
  slots it was fed.
- **The windows were short.** A standby machine with no restorable state replays
  24 hours (measured: first slot one day before the last, 20 intervals counted
  in the simulated world; at most 3 days when an older state was restored:
  `STANDBY_REPLAY_MAX`). The efficiency and battery default window is 3 days
  (`EFFICIENCY_CATCHUP_MAX`); only the button widened it, to
  `min(max_history_days, keep_days)`. The standby machine had no wider path at
  all until now. A rebuild over 5 days counted 110 intervals in the same
  simulated world.
- **The efficiency estimates also depend on a recorded standby statistic** that
  exists only since the recorder exclusion was removed (2026-10-30, TASK-0033),
  so at the time of the report they could cover about three days whatever window
  is used; the battery estimate does not use it.
- **A simulated world counts dozens of intervals per day, a real one far
  fewer.** A real drain interval needs quiet slots, a counter that ticks, and no
  other flow; so a count of 1 to 3 can also be the data.

What is **not** established: why the instance shows 3. It could be the short
first-start windows on a young installation, a state that is not restored after
a restart (then every restart begins again with the 24-hour or 3-day replay), or
genuinely few valid intervals.

How the attribute tells them apart (for the human): press **Re-calculate
History**, then read `first_processed_slot` and the count on each sensor. If
`first_processed_slot` is about `min(max_history_days, keep_days)` before
`last_processed_slot` and the count is still at most 3, the window is not the
limit: the data is (the INFO log line
`Effy: N valley spans of the battery not counted (last reason: ...)` says why
battery spans were dropped; for standby look at `last_drain_interval_end`). If
it is later than that, a window is cut and that is a defect to chase. After a
restart `first_processed_slot` should not move; if it jumps to a day or three
before the restart, the state was not restored.

### Open for the human

1. The check on the instance described above; please report the five
   `first_processed_slot` values, their `last_processed_slot` and counts, and
   whether any of them is `unavailable` after a press.
1. A rebuild replaces a long-learned standby estimate with the window's and can
   leave the sensor (and the efficiency sensors that wait for it) unavailable
   until an interval counts (ADR-018 Amendment). Keep as is, or keep the old
   estimate when the replay counts nothing?
1. Confirmation of the amendments (ADR-018, ADR-023).

## Delivered Artifacts

- `custom_components/effy/calculation.py` → `DrainState.first_slot`,
  `RunState.first_slot`, `ValleyState.first_slot`; the key `first_slot` in
  `drain_restore_data`, `run_restore_data`, `valley_restore_data` and the three
  `*_from_restore`
- `custom_components/effy/history.py` → `async_update_standby(..., window=)`,
  `async_rebuild_standby`
- `custom_components/effy/coordinator.py` →
  `EffyCoordinator._async_rebuild_standby`; the button flow runs it before
  `_async_rebuild_efficiency`
- `custom_components/effy/sensor.py` → the attribute `first_processed_slot` of
  `EffyStandbyPowerSensor`, the charge/discharge efficiency sensor,
  `EffyBatteryEfficiencySensor` and `EffyRoundtripEfficiencySensor`
- Tests: `TestFirstSlot` (three files), `TestTheButtonRebuild`,
  `TestStandbyRebuildAfterHistory`, `TestWhatTheHistoryButtonRebuilds`, the
  sensor attribute tests
- `README.md`, `GLOSSARY.md` (First processed slot), `adr/018` and `adr/023`
  (Amendments 2026-11-01)
- External dependencies added: none
