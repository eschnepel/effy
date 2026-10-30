# Task: Feed the valley machine from the recorder, restore and catch up

- **Status:** done — 2026-10-29, reviewer PASS (inline)
- **Capability:** CAP-9
- **Related ADRs:** [ADR-023 decisions 6, 9, 10, ADR-021 decision 4]
- **Dependencies:** \[TASK-0020-efficiency-slot-feed-and-restore,
  TASK-0023-soc-capacity-and-hysteresis-options, TASK-0024-valley-span-machine\]
- **Gate:** ADR-023 accepted 2026-10-27; its Open decisions 1 and 2 are settled.

## Goal

The glue for battery efficiency: read the SoC 5-minute `mean` statistics and the
capacity entity's raw state history, with the charge and discharge derived power
statistics, and feed the valley machine in the same job step as TASK-0020.

## Acceptance Criteria

- Given SoC and capacity configured, then the job reads the SoC statistics
  (`mean`) and the capacity entity's state at each span boundary and every
  change in between from the state history; a missing SoC slot reaches the
  machine as missing.
- Given the job, then it runs in the same place as TASK-0020's step (after
  standby, before the closing step, under the lock); failures are logged and
  leave the state unchanged.
- Given a restart during an open span (state restored, then catch-up of at most
  3 days), then the result equals uninterrupted operation.
- Given a span longer than the catch-up window, then it is lost as specified,
  not corrupted (test).
- Given the battery machine is not configured, then nothing is read.
- Given the existing series and the standby sensor, then they are identical with
  and without the battery feed (regression).

## Estimated File / Module Footprint (hint, not a commitment)

`history.py` (`async_update_battery_efficiency` or an extension of TASK-0020's
function), `coordinator.py`, `tests/test_battery_feed.py`.

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

- From TASK-0023 (done 2026-10-29, filled by the Lead):
  `inputs.soc_id(options)`, `inputs.capacity_id(options)` (both `str | None`),
  `inputs.valley_hysteresis(options) -> float` (default 30);
  `const.CAPACITY_UNITS` (`("Wh", "kWh")`), `const.CONF_SOC_SENSOR`,
  `const.CONF_CAPACITY_ENTITY`, `const.CONF_VALLEY_HYSTERESIS`,
  `const.DEFAULT_VALLEY_HYSTERESIS`, `const.MIN_VALLEY_HYSTERESIS`. Note: the
  options flow accepts a capacity entity **without a unit** (or without a state
  yet); the feed must decide what a capacity of unknown unit means (see the
  decisions of TASK-0023).
- From TASK-0024 (done 2026-10-29, filled by the Lead; `calculation.py`):
  `BatSlot(start, soc, charge_kwh, discharge_kwh, capacity_kwh)`, `ValleyState`,
  `ValleyEstimate` (`.value`, `ratio`, `counted_kwh`, `counted_spans`,
  `last_value`, `last_span_begin`, `last_span_end`),
  `valley_feed(state, slots, now, hysteresis, delay)`, `valley_step`,
  `valley_restore_data(state, hysteresis)`,
  `valley_state_from_restore(data, hysteresis)` (`None` for unusable data,
  another hysteresis, or a state that processed nothing), `SpanDrop` and the
  `SPAN_DROP_*` reasons, `VALLEY_BAND`, `CAPACITY_TOLERANCE`. The capacity goes
  in **kWh**; the hysteresis must be above 10 (`valley_step` raises `ValueError`
  otherwise); slots are consecutive 5-minute slots, a gap or a missing SoC
  resets the detection.
- From TASK-0020 (done, filled by the Lead from its Delivered Artifacts):
  `history.py`: `EFFICIENCY_CATCHUP_MAX` (3 days),
  `efficiency_rebuild_window (options, keep_days)`,
  `async_read_efficiency_energy(hass, options, start, end) -> (energy, standby)`
  (`energy: slot -> {counter entity -> kWh}`, the derived power series times 5
  min; a statistic that does not exist is absent, never zero),
  `async_update_efficiency(hass, options, state, now, window)` (its slot range
  logic is the pattern: settled slots only, `last_slot + 5 min` or at most
  `window` back), `async_rebuild_efficiency`; `coordinator.py`:
  `efficiency_enabled`, `efficiency_state`, `restore_efficiency(data)` (the dump
  with the later `last_slot` wins), `subscribe_efficiency(cb)`,
  `_async_update_efficiency(now)` (after `_async_update_standby`, before the
  closing step; failures logged, state left), `_async_rebuild_efficiency()`
  (called by `async_recalculate_history_and_report`). Tests:
  `tests/test_efficiency_feed.py` (the faked recorder `_World`/`_install` of
  `test_history_recent_equivalence`), `tests/test_coordinator_slot.py`
  (`TestEfficiencyInTheSlotJob`, the stubbed `effy.history`).
- Filled by the Lead (2026-10-29).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

`custom_components/effy/history.py`

- New: `battery_enabled(entry_options) -> bool` (charge, discharge, SoC and
  capacity set; no pair, no standby);
  `_fetch_capacity_states(hass, entity_id, start, end) -> list[(datetime, str, str | None)]`
  (the entity's raw states with the unit of each state, the state in effect at
  `start` first; runs in the executor);
  `async_update_battery(hass, entry_options, state, now, window=EFFICIENCY_CATCHUP_MAX) -> ValleyState`;
  `async_rebuild_battery(hass, entry_options, now) -> ValleyState` (from empty
  over `efficiency_rebuild_window`);
  `_unseen_settled_slots(last_slot, now, window) -> tuple[datetime, datetime] | None`.
- Changed, behaviour for existing callers unchanged (TASK-0020's tests are green
  untouched):
  `async_read_efficiency_energy(..., *, only=None, with_standby=True)` and
  `async_update_efficiency` (its slot range now comes from
  `_unseen_settled_slots`).
- Imports added: `BatSlot`, `ValleyState`, `valley_feed`, `capacity_by_slot`,
  `battery_hysteresis`, `capacity_id`, `soc_id`, `Collection`.

`custom_components/effy/calculation.py` (pure)

- `capacity_kwh(state, unit) -> float | None` (Wh and kWh only; any other unit,
  none, or a state that is no positive finite number is `None`),
  `capacity_by_slot(rows, slots) -> dict[datetime, float | None]` (the state in
  effect in the *middle* of the slot), private `_CAPACITY_TO_KWH`.

`custom_components/effy/inputs.py`

- `battery_hysteresis(options) -> float`: the saved number, never below 15, the
  default for no or an unusable value. `valley_hysteresis` is unchanged (it
  shows what is saved).

`custom_components/effy/coordinator.py`

- `BatteryCallback = Callable[[ValleyState], None]`; properties
  `battery_enabled`, `battery_hysteresis`, `battery_state` (`None` until a
  sensor restored); `restore_battery(data) -> ValleyState` (the dump with the
  later `last_slot` wins; a dump made with another hysteresis, unusable or
  missing data start from an empty state);
  `subscribe_battery(cb) -> unsubscribe`; private `_async_update_battery(now)`
  (in the slot job right after `_async_update_efficiency`, under the lock;
  failures logged, state left; a span not counted is logged at INFO with its
  reason) and `_async_rebuild_battery()` (in
  `async_recalculate_history_and_report` right after
  `_async_rebuild_efficiency`; failure logged, old state kept); attributes
  `_soc_id`, `_capacity_id`, `_battery_hysteresis`, `_battery_state`,
  `_battery_subscribers`.

For TASK-0026 (the sensors): restore with `coordinator.restore_battery(data)`,
store `valley_restore_data(state, coordinator.battery_hysteresis)` (from
`calculation`), get the state after every slot job from `subscribe_battery`, and
read `state.estimate.value`, `counted_kwh`, `counted_spans`, `last_value`,
`last_span_begin`, `last_span_end` and `state.last_slot`.

Tests: new `tests/test_battery_feed.py` (49), `tests/test_coordinator_slot.py`
(+27: `TestBatteryInTheSlotJob`, `TestBatteryRestore`,
`TestBatteryRebuildAfterHistory` and the stub's two new functions),
`tests/test_valley_spans.py` (+29: the pure capacity helpers),
`tests/test_inputs.py` (+4), `tests/test_history_recorder_helpers.py` (+2: the
capacity fetch against HA's `state_changes_during_period`). Not touched:
sensors, `const.py`, translations, README (no user-visible change before
TASK-0026). External dependencies added: none.

## Decisions taken inside the task (◇), for the human's confirmation

1. **A capacity without a Wh or kWh unit counts as unknown** (the options flow
   accepted such an entity, TASK-0023 decision 2): the spans it is in are
   dropped with the reason `capacity_unavailable`, never guessed. It is visible
   in the log (INFO, once per dropped span), not on the sensor. If you would
   rather have the options flow refuse a unit-less entity, say so (a small patch
   to TASK-0023).
1. **The capacity of a slot is the state in effect in its middle** (like its
   SoC), so a flicker shorter than a slot between two middles is not seen; what
   lasts a slot is, and the 1 % tolerance of the machine absorbs a BMS's jitter.
1. **The battery machine needs the charge and discharge counters, the SoC and
   the capacity, and nothing else**: no grid counter, no standby sensor (ADR-023
   decision 2). It reads exactly those two derived power series and the SoC
   mean; a power-family counter is read the way TASK-0020 reads it.
1. **`battery_hysteresis` is a new accessor** (never below 15); the stored value
   stays what the options form shows. A changed hysteresis refuses the restored
   dump and the machine replays three days: the decayed estimate of the old
   valleys is lost on purpose (they were different valleys).
1. **A rebuild after a history recalculation is part of this task**, beyond the
   acceptance criteria listed here: ADR-023 decision 9 replays the valley
   machine with the same machinery, and without it the state would go stale
   after every recalculation. This covers most of TASK-0027 (see its note); it
   is still to be reviewed there.
1. **A fresh start before the recorder has data** feeds the empty slots of the
   catch-up window (all missing, so resets): harmless, and it keeps the slots
   consecutive.
1. **A small refactor of TASK-0020's code** (`_unseen_settled_slots` shared by
   both feeds, two keyword-only parameters with the old behaviour as defaults),
   to avoid a copy of the slot-range arithmetic; TASK-0020's tests pass
   unchanged.

## Review

**PASS** (inline; 1057 tests (946 before), `ruff check`, `ruff format`, `mypy`
on source and tests; `coordinator.py` and `inputs.py` are fully covered, the
remaining misses in `history.py` are older lines and the recorder-bound
fetchers' glue, the new one of which is now tested against a faked
`state_changes_during_period`).

Acceptance criteria to tests: SoC `mean`, the capacity's states and units and
what is read (`TestWhatIsRead`, `TestTheCapacityTheMachineGets`, the pure
helpers, the fetch tests); a missing SoC slot, a SoC row without a mean and a
missing energy statistic reach the machine as missing
(`TestTheSlotsTheMachineGets`); the place in the slot job, the lock and the
failure handling (`TestBatteryInTheSlotJob`); a restart in the middle of a span,
of a valley, in a descent, and with a state two days behind equal uninterrupted
operation, through JSON, on ten simulated days (`TestTheSimulatedBattery`); a
span longer than the catch-up window is lost, not counted, with the estimate
kept (the machine's detection equals a first start's); nothing is read without
the four settings, and the grid counters are not read even when configured; the
existing series, the standby and the efficiency feed are identical with and
without the machine (a coordinator test, and the feed only reads, never writes).

**Mutants.** 82 mutants of the new code in `history.py`, `coordinator.py`,
`inputs.py` and the capacity helpers (the enabling conditions, the slot range
and its age gate, the end of the read, which counters, the conversion factors
and unit handling, the middle of the slot, the SoC and its missing rows, the
hysteresis, the order of the two feeds and of the two rebuilds, the restore's
choice between two dumps, the notifications, the logging of a dropped span, the
failure handling). Survivors of the first round and what they were: one real gap
(the battery must not read configured grid counters), one redundant filter of
mine (removed rather than tested), one gap in the restore tests (a sensor that
restored nothing before another one restored a dump) and two mutation strings
that hit TASK-0020's older, identical text first. Three survivors are
equivalent: `CancelledError` is a `BaseException`, so an explicit
`except ...: raise` before `except Exception` changes nothing (kept for the
pattern of the efficiency code, twice), and an equal `last_slot` means an
identical state (`>=` for `>`).

**Simulation.** Ten days of a 92 % battery (seed 3: nine spans, 57 kWh, none
dropped): the estimate through the real chain (derived kW series, SoC mean,
capacity states) is within two points and equals the pure machine fed the same
slots directly, to the rounding of kWh to kW and back. The seed was chosen by a
scan so that every window tested (the last three days, the last five, an open
span and a running descent at the cuts) holds spans; the earlier seed had dull
days at the end, which is the weather and not the feed.

**Observation, not changed here.** The first-restore branch of
`restore_efficiency` (a sensor that restored nothing before another one restored
a dump) is not pinned by TASK-0020's coordinator tests: a mutant that removes it
survives them. The battery's equivalent is pinned now. Worth a one-line test in
a patch task if you want it.

Not verifiable here: the real recorder (that `state_changes_during_period`
returns the state in effect at `start` first, and the attributes of each state,
as the fake and the existing raw-energy reader assume), and how long the
5-minute job takes with ten days of capacity states on a Pi.
