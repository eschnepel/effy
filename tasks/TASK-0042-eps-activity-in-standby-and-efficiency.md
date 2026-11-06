# Task: An active EPS sensor, in either direction, makes a slot impure and breaks a drain interval

- **Status:** todo
- **Capability:** CAP-12
- **Related ADRs:** [ADR-026 decision 5, ADR-018 decisions 2 and 3, ADR-023 decisions 3 and 4 and Amendments 2026-11-04, 2026-11-06]
- **Dependencies:** [TASK-0041-eps-split-in-slot-loop]
- **Gate:** Gate 2 pending (2026-11-06); ADR-026 is a Draft until the human confirms it.

## Goal

Pin by tests that the standby and the efficiency machines see the EPS sensor as
active whenever its signed smoothed series is not zero, a negative value in
particular, and that nothing else in them changes. The activity reading
(`async_read_drain_activity`) already takes a power-family input from its
smoothed series and tests `mean != 0`; this task proves it for the EPS sensor
and fixes whatever the tests find (expected: nothing).

## Acceptance Criteria

- Given an EPS sensor whose smoothed series is positive in a slot, then the
  slot's activity contains the EPS source entity; given negative, then it
  contains it too; given 0, then it does not.
- Given a slot in which only the grid import and charge counters flow and the EPS
  sensor is active (either sign), then `classify_slot` gives no kind and the
  slot is not pure; the EPS sensor is not subject to the 5 % rule of
  `_significant_flow`.
- Given a drain interval and an active EPS sensor in any slot of its span, then
  the pending interval is dropped, as for any sensor (ADR-018 decision 3).
- Given an EPS sensor that is never a BMS input, then it is never a drain
  source and `bms_input_ids` / `standby_enabled` are unaffected.
- Given the efficiency energy read (`async_read_efficiency_energy`-style reads
  of the four counters), then the EPS sensor is not one of the counters and is
  not read as one.
- Given no EPS sensor, then every existing standby and efficiency test is
  unchanged and green.

## Estimated File / Module Footprint (hint, not a commitment)

`history.py` (`async_read_drain_activity` and the efficiency read: tests first, change
only if a test fails), `tests/test_standby_glue.py`, `tests/test_efficiency_feed.py`,
`tests/test_efficiency_runs.py`.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- Pre-existing, `history.py`: `async_read_drain_activity(hass, entry_options,
  start, end, storage)` (statistic id per source: `effy_*_power` for an energy
  counter, `effy_*_smoothed` for a power-family input in `all_input_ids`, the
  source's own id for a power-family output; active = `mean != 0.0`),
  `_classify_sensors`, the efficiency energy read that keys on `efficiency_roles`.
- Pre-existing, `calculation.py`: `classify_slot(active, roles)`,
  `_significant_flow(slot, roles)`, `EffRoles`, `EffSlot`, `drain_step`,
  `drain_feed`.
- To be filled by the Lead at Phase 3 from the Delivered Artifacts of
  TASK-0041-eps-split-in-slot-loop.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- _(open)_
