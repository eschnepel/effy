# Task: Feed the valley machine from the recorder, restore and catch up

- **Status:** todo
- **Capability:** CAP-9
- **Related ADRs:** [ADR-023 (Draft) decisions 6, 9, 10, ADR-021 decision 4]
- **Dependencies:** \[TASK-0020-efficiency-slot-feed-and-restore,
  TASK-0023-soc-capacity-and-hysteresis-options, TASK-0024-valley-span-machine\]
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

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
- `async_update_efficiency`, `restore_efficiency` (→ TASK-0020) · the valley
  machine (→ TASK-0024) — **to be filled by the Lead when TASK-0024 is done**

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->
