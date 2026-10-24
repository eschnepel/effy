# Task: The pure machine for charge and discharge efficiency

- **Status:** todo
- **Capability:** CAP-8
- **Related ADRs:** \[ADR-023 (Draft) decisions 3, 4, 5, 7 and 13, ADR-018
  (activity, D3), ADR-000 §3/§6\]
- **Dependencies:** []
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

## Goal

A pure, zero-mock machine in `calculation.py` that turns time-ordered slot data
into the charge and discharge efficiency estimates: slot classification, pure
runs with the first and last slot ignored, standby energy, the decayed sums, and
a restore/dump pair. No Home Assistant, no recorder, no clock.

## Acceptance Criteria

- Given a slot and the four role IDs, then `classify_slot` returns `charge` only
  when exactly the grid import and charge counters are active, `discharge` only
  when exactly the discharge and grid export counters are active, and `None`
  otherwise (any PV input, any other counter, nothing active).
- Given a run of consecutive slots of one kind, then the first and last slot of
  the run are never counted; a run of 2 counts nothing, a run of 3 counts its
  middle slot; an impure slot, a slot without data and a gap each end the run.
- Given a slot, then it is counted exactly once, and only when it is at least
  125 min old at `now`, both neighbours are pure of the same kind, its standby
  energy is known and its import (or discharge) energy exceeds it.
- Given counted slots, then charge efficiency is Σ charge ÷ Σ (grid import −
  standby energy) and discharge efficiency Σ grid export ÷ Σ (discharge −
  standby energy), each as a decayed ratio: with `f = 0.95^x` for x kWh of gross
  import (or discharge), `N ← f·N + out`, `D ← f·D + in`, the first measurement
  seeds it, and a steady-state measurement of 1 kWh weighs 5 % and one of 3 kWh
  weighs 14.3 % (tested numerically); per-slot and batched feeding give the same
  result.
- Given a simulator of a battery system with known charge and discharge
  efficiency and a known standby, with pure runs of varied length and counter
  noise, then both estimates converge to the known values within 0.5 points
  after 60 kWh and stay there.
- Given a state dumped and restored between two chunks of slots, then the result
  equals feeding both chunks in one go; `run_state_from_restore` returns `None`
  for unusable data.
- Given `calculation.py`, then it still imports no Home Assistant module, and
  the existing standby machine and every existing function behave as before (the
  existing tests pass unchanged).

## Estimated File / Module Footprint (hint, not a commitment)

`calculation.py` (new section: `EffSlot`, `EffRoles`, `DecayedRatio`,
`decay_add`, `ratio_value`, `RunState`, `classify_slot`, `run_feed`, restore
functions), `tests/test_efficiency_runs.py`, `tests/` simulator helper.

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

- The activity notion of ADR-018 ("active" = value not zero) is consumed as
  data: the machine gets the active sensor IDs per slot, never reads statistics.
  — nothing else; this task has no code dependency.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

Names above are suggestions (ADR-023 ◇); the Delivered Artifacts block fixes
them for TASK-0020, TASK-0021 and TASK-0024.
