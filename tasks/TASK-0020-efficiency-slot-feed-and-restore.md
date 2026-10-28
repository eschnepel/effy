# Task: Feed the charge and discharge machine from the recorder, restore and catch up

- **Status:** done — 2026-10-27, reviewer PASS (inline)
- **Capability:** CAP-8
- **Related ADRs:** \[ADR-023 decisions 3, 9, 10, ADR-018 (activity reading,
  slot job), ADR-021 decision 4, ADR-012\]
- **Dependencies:** \[TASK-0017-counter-roles-in-config,
  TASK-0019-efficiency-pure-run-machine\]
- **Gate:** ADR-023 accepted 2026-10-27.

## Goal

The glue: after the standby update and before the closing step of every slot
job, read the slots the machine has not seen (at most 3 days back) and feed
them; keep the machine's state in the coordinator and hand it to the sensors to
restore and persist. Standby's pattern, with a catch-up instead of a rebuild.

## Acceptance Criteria

- Given configured grid import and charge (or discharge and grid export)
  counters, then the job reads the derived power statistics (`effy_*_power`) of
  the four counters, the activity of every input with the reading ADR-018
  already uses, and the recorded `mean` statistic of `sensor.effy_standby_power`
  per slot; a missing statistic reaches the machine as a missing slot, never as
  zero.
- Given the slot job, then the update runs after `_async_update_standby` and
  before the closing step, under the recorder job lock, and a failure is logged
  and leaves the state unchanged.
- Given a coordinator before its sensors restored, then nothing is fed (as for
  standby); after `restore_efficiency(data)` the slots after
  `last_processed_slot` are processed, at most 3 days back.
- Given a restart in the middle of a run (state restored, then catch-up), then
  the result equals uninterrupted operation, shown with the stubbed-recorder
  test pattern of `tests/test_coordinator_slot.py`.
- Given the machine is not configured (no pair of counters), then nothing is
  read and no cost is added to the slot job.
- Given the entry, then every existing `effy_*` series and the standby sensor
  are identical with and without the efficiency feed (regression test, ADR-023
  decision 12).
- Given Home Assistant startup, then the feed starts after the first slot job
  and never blocks startup.

## Estimated File / Module Footprint (hint, not a commitment)

`history.py` (new `async_update_efficiency`), `coordinator.py`
(`efficiency_enabled`, `restore_efficiency`, `subscribe_efficiency`, the hook),
`tests/test_efficiency_feed.py`, `tests/test_coordinator_slot.py`.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only)

## Consumed Interfaces

- From TASK-0019 (`calculation.py`): `EffRoles`, `EffSlot`, `RunState`,
  `run_feed(state, slots, roles, now, delay)`, `run_restore_data`,
  `run_state_from_restore`, `EFF_VALIDATION_DELAY`.
- From TASK-0017: `inputs.grid_import_id`, `discharge_id`, `charge_id`,
  `grid_export_id`, `all_input_ids`; the coordinator's four role properties.
- `history.async_read_drain_activity`, `async_update_standby`,
  `_fetch_statistics`, `_classify_sensors`, `_get_statistics_units`,
  `_effy_power_entity_id`, `_effy_smoothed_entity_id` (→ TASK-0005).
- `EffyCoordinator._async_update_standby`, `restore_standby`,
  `subscribe_standby`, `_recorder_job_lock` (→ TASK-0005, TASK-0006).

## Delivered Artifacts

- `history.py`: `EFFICIENCY_CATCHUP_MAX` (3 days);
  `efficiency_roles(options) -> EffRoles`; `efficiency_enabled(options)` (grid
  import with charge, or discharge with grid export);
  `async_read_efficiency_energy(hass, options, start, end) -> (energy, standby)`,
  `energy: slot -> {counter entity -> kWh}`, `standby: slot -> kWh`;
  `async_update_efficiency(hass, options, state, now) -> RunState`.
- `coordinator.py`: `efficiency_enabled`, `efficiency_state`,
  `restore_efficiency(data) -> RunState`,
  `subscribe_efficiency(cb) -> unsubscribe`, `_async_update_efficiency(now)`,
  called after `_async_update_standby` and before the closing step; the type
  `EfficiencyCallback`.
- Series read ◇: the energy of a counter is its derived power series (the series
  before the loss is distributed) times 5 min — `effy_*_power` for an
  energy-family sensor, the smoothed series for a power-family input, its own
  `mean` for a power-family output; the standby energy is the recorded `mean` of
  `sensor.effy_standby_power` (W) times 5 min. A slot lacking the activity or a
  configured counter's statistic is not handed to the machine at all (a missing
  slot, never a zero); one lacking the standby statistic is handed over with
  `standby_kwh=None`.
- Settled here: ADR-023 Open decision 4 (persistence): every efficiency sensor
  keeps the whole machine dump, the coordinator adopts the dump with the later
  `last_slot` (see the ADR).
- Tests: `tests/test_efficiency_feed.py` (15, the real recent recalculation and
  feed on a simulated battery with 1 Wh counters),
  `tests/test_coordinator_slot.py` (`TestEfficiencyInTheSlotJob`, the closing
  order), `tests/support.py` unchanged.
- External dependencies added: none.

## Review

**PASS** (inline; 773 tests, ruff, mypy on source and tests). Simulated battery
(95 % charge, 90 % discharge, 20 W standby, 1 Wh counters), the real recent
recalculation, standby and efficiency feed every 5 minutes: charge 95.0 %,
discharge 89.2 %; a restart in the middle of the charge or of the discharge, a
restart with an older state and a first start (catch-up) all give exactly the
uninterrupted result; every existing `effy_*` series, the number and flags of
the writes and the standby state are identical with and without the feed.
Fourteen mutants of the glue are caught (kW read as W, no 5-minute factor,
standby not read, standby unit ignored, a missing counter read as zero, no age
gate, no 3-day bound, a pair as "and", a restart that re-reads from the floor,
the earlier dump kept, a restore that resets, the feed before standby, a failure
that moves the state, a feed without a pair).

Findings that shape the next tasks and the first run on the Pi:

- **Pure slots are rare in short runs.** A counter that does not tick is
  redistributed backwards over up to 120 min when it next ticks (ADR-012), and
  activity is "not zero" without a tolerance (ADR-023 decision 3). So in a
  charge shorter than about two hours the idle counters (discharge, grid export)
  look active in every slot and nothing counts; a long run keeps pure slots in
  its early part. A first simulation with 10 Wh counters and 1-hour runs counted
  nothing at all. This follows from the accepted decisions, not from the feed;
  the Pi's first records will show how many slots really count (`counted_kwh`,
  `counted_runs`).
- The statistic ID of the standby sensor is the constant
  `EFFY_STANDBY_POWER_ENTITY_ID`; an entity renamed in the registry would not be
  found, and the machine would then count nothing (no slot has standby energy).
