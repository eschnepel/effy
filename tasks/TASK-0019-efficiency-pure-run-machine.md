# Task: The pure machine for charge and discharge efficiency

- **Status:** done — 2026-10-27, reviewer PASS (inline)
- **Capability:** CAP-8
- **Related ADRs:** \[ADR-023 decisions 3, 4, 5, 7 and 13, ADR-018 (activity,
  D3), ADR-000 §3/§6\]
- **Dependencies:** []
- **Gate:** ADR-023 accepted 2026-10-27.

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

All in `custom_components/effy/calculation.py` (no Home Assistant import):

- Constants: `EFF_VALIDATION_DELAY` (= `DRAIN_VALIDATION_DELAY`, 125 min),
  `EFF_DECAY_PER_KWH` (0.95), `EFF_KIND_CHARGE` (`"charging"`),
  `EFF_KIND_DISCHARGE` (`"discharging"`), the type `EffKind`. The kinds are not
  spelled `charge`/`discharge`: those are the published `function` values
  (TASK-0018's guard test reserves them for `effy_exports.py`).
- Types: `EffRoles(grid_import, discharge, charge, grid_export)`,
  `EffSlot(start, active, grid_import_kwh, discharge_kwh, charge_kwh, grid_export_kwh, standby_kwh)`,
  `DecayedRatio(num, den)`,
  `EffEstimate(ratio, counted_kwh, counted_runs, last_value, last_end; .value)`,
  `EffRecord`,
  `RunState(charge, discharge, prev1, prev2, run_counted, last_slot)`.
- Functions: `classify_slot(active, roles)`,
  `decay_add(ratio, gross_kwh, out, inp)`, `ratio_value(ratio)`,
  `run_step(state, slot, roles)`, `run_feed(state, slots, roles, now, delay)`,
  `run_restore_data(state)`, `run_state_from_restore(data)` (the persisted
  estimates sit under the keys `charging` and `discharging`).
- Conventions fixed here ◇: slots enter in time order and only when 125 min old,
  so a slot's neighbours are settled too (a slot is therefore counted when its
  successor has settled, at the earliest 130 min after its start); a slot that
  does not directly follow the last one is a gap and ends the run; a slot at or
  before the last processed one is ignored; a slot without a standby statistic
  does not count but stays in the run; `counted_runs` counts runs that counted
  at least one slot; `last_value` is the last counted slot's own ratio.
- `tests/test_efficiency_runs.py` (59 tests): classification, the decayed ratio
  by hand (1 kWh weighs 5 %, 3 kWh 14.3 %, no cap), the run rules, age, batch
  equals step by step and replay, the restore round trip including a restore in
  the middle of a run, and a simulator of a battery with known efficiencies and
  cumulative counters that tick in 10 Wh.
- External dependencies added: none.

## Review

**PASS** (inline; 744 tests, ruff, mypy on source and tests). The simulator (90
days, 25,678 slots, 1,340 mixed slots, a few missing slots): 2,880 kWh counted
in 568 charge runs, the charge estimate stays within 0.19 points of 95 % after
the first 60 kWh and the discharge estimate within 0.18 points of 93 %; after a
step of the true efficiencies (95/93 % to 90/88 %) both follow within 0.5
points. Twenty mutants of the machine are caught (decay on net instead of gross,
base 0.9, gross denominator, a cap, extras allowed in a slot, the first slot
counted, no gap reset, standby ignored, input equal to standby counting, no age
gate, an age gate off by a slot, no replay guard, runs counted per slot, the run
flag never reset, `last_end` at the slot start, an unsorted feed, a restore that
drops the records or the run flag or accepts NaN).

One of the first test runs found a real clash: the new code first spelled the
kinds `"charge"` and `"discharge"` and the guard test of TASK-0018 failed;
renamed.
