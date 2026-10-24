# Task: Feed the charge and discharge machine from the recorder, restore and catch up

- **Status:** todo
- **Capability:** CAP-8
- **Related ADRs:** \[ADR-023 (Draft) decisions 3, 9, 10, ADR-018 (activity
  reading, slot job), ADR-021 decision 4, ADR-012\]
- **Dependencies:** \[TASK-0017-counter-roles-in-config,
  TASK-0019-efficiency-pure-run-machine\]
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

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

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- the machine's names from TASK-0019's Delivered Artifacts — **filled by the
  Lead before start**
- role accessors from TASK-0017 — **filled by the Lead before start**
- `history.async_read_drain_activity`, `async_update_standby`,
  `_fetch_statistics` from `custom_components/effy/history.py` (→
  TASK-0005-standby-power-sensor)
- `EffyCoordinator._async_update_standby`, `restore_standby`,
  `subscribe_standby`, `_recorder_job_lock` from
  `custom_components/effy/coordinator.py` (→ TASK-0005,
  TASK-0006-recorder-synchronized-slot-trigger)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

ADR-023 Open decision 4 (persistence): recommended — each sensor keeps its own
part of the state as `ExtraStoredData`, like `_StandbyExtraData`; the
coordinator only holds the live state. Confirm or change it in the Delivered
Artifacts.
