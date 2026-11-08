# Task: An active EPS sensor, in either direction, makes a slot impure and breaks a drain interval

- **Status:** done
- **Capability:** CAP-12
- **Related ADRs:** \[ADR-026 decision 5, ADR-018 decisions 2 and 3, ADR-023
  decisions 3 and 4 and Amendments 2026-11-04, 2026-11-06\]
- **Dependencies:** [TASK-0041-eps-split-in-slot-loop]
- **Gate:** Gate 2 approved by the human (2026-11-06); ADR-026 accepted.

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
- Given a slot in which only the grid import and charge counters flow and the
  EPS sensor is active (either sign), then `classify_slot` gives no kind and the
  slot is not pure; the EPS sensor is not subject to the 5 % rule of
  `_significant_flow`.
- Given a drain interval and an active EPS sensor in any slot of its span, then
  the pending interval is dropped, as for any sensor (ADR-018 decision 3).
- Given an EPS sensor that is never a BMS input, then it is never a drain source
  and `bms_input_ids` / `standby_enabled` are unaffected.
- Given the efficiency energy read (`async_read_efficiency_energy`-style reads
  of the four counters), then the EPS sensor is not one of the counters and is
  not read as one.
- Given no EPS sensor, then every existing standby and efficiency test is
  unchanged and green.

## Estimated File / Module Footprint (hint, not a commitment)

`history.py` (`async_read_drain_activity` and the efficiency read: tests first,
change only if a test fails), `tests/test_standby_glue.py`,
`tests/test_efficiency_feed.py`, `tests/test_efficiency_runs.py`.

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

- Pre-existing, `history.py`:
  `async_read_drain_activity(hass, entry_options, start, end, storage)`
  (statistic id per source: `effy_*_power` for an energy counter,
  `effy_*_smoothed` for a power-family input in `all_input_ids`, the source's
  own id for a power-family output; active = `mean != 0.0`),
  `_classify_sensors`, the efficiency energy read that keys on
  `efficiency_roles`.
- Pre-existing, `calculation.py`: `classify_slot(active, roles)`,
  `_significant_flow(slot, roles)`, `EffRoles`, `EffSlot`, `drain_step`,
  `drain_feed`.
- From TASK-0041-eps-split-in-slot-loop: `inputs.all_input_ids(options)` ends
  with the EPS sensor (`inputs.eps_id`), so `history.async_read_drain_activity`
  reads it as a power-family input, from `effy_*_smoothed`
  (`history._effy_smoothed_entity_id(eps)`), which holds the *signed*,
  gap-interpolated series; `history._compute_effective_slots` writes that series
  and the effective series (`history._effy_entity_id(eps)`).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- **No production code changed.** The tests came first and all passed: the
  activity reading of `history.async_read_drain_activity` already takes a
  power-family input from its smoothed series and tests `value != 0.0`, which
  holds for a negative value; the EPS sensor is not one of the four counters, so
  `classify_slot` and `_significant_flow` treat it as an "other sensor", like a
  PV input.
- `tests/test_eps_activity.py` (new, 18 tests) → `TestPureRules`
  (`classify_slot` and `_significant_flow` with an EPS sensor),
  `TestActivityReading` (activity names the EPS sensor for +120 W and -80 W and
  not for 0; read from its smoothed statistic; the efficiency energy read never
  reads it), `TestStandbyMachine` (end to end on the simulation of
  `test_standby_glue`: idle EPS leaves 12 W; +5 W and -5 W in every slot break
  every drain interval; a one-hour stretch costs only the intervals it touches;
  the EPS sensor is never a drain source), `TestEpsSensorIsNoBmsInput` (without
  a BMS input standby stays disabled and nothing is read),
  `TestEfficiencyMachine` (end to end on the simulation of
  `test_efficiency_feed`: idle EPS changes neither estimate; an active EPS
  sensor, either sign, leaves no pure slot during its stretch and the other run
  is untouched). The module serves a power-family source from `power_means` and
  the statistics Effy wrote from `store`.
- Negative control run once and not kept: changing the activity test to
  `value > 0.0` made exactly the four negative-sign tests fail.
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged).
- Verification: 1725 tests pass (1707 before, 18 new); `ruff check` and
  `mypy --strict` clean; `ruff format --check` flags only `tests/test_cache.py`
  (already flagged before CAP-12, not touched).
