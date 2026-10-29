# Task: The pure valley and span machine for battery efficiency

- **Status:** todo — held for the human: ADR-023 Open decision 1 (an estimate
  above 100 % for a valley span)
- **Capability:** CAP-9
- **Related ADRs:** \[ADR-023 decisions 6, 7, 13 and Open decisions 1 and 2,
  ADR-000 §3/§6\]
- **Dependencies:** [TASK-0019-efficiency-pure-run-machine]
- **Gate:** ADR-023 accepted 2026-10-27. Held until the human decides Open
  decision 1 for a valley span (publish as it is / cap at 100 % / do not count);
  the machine is built to that decision and a test pins it.

## Goal

A pure machine in `calculation.py` that turns SoC slots, capacity and the charge
and discharge energies into the battery efficiency: the valley zigzag, the
valley spans, Δstored, the capacity check and the decayed ratio shared with
TASK-0019. It also settles the boundary convention by simulation.

## Acceptance Criteria

- Given a SoC series, then a valley is real when SoC has risen to its lowest
  point plus the hysteresis, it ends at the last slot at or below the lowest
  point plus 10 before that rise, and the next valley begins at the first slot
  after the peak at or below its own lowest point plus 10; a dip smaller than
  the hysteresis widens the valley.
- Given a missing SoC slot, then the detection resets and no span contains it.
- Given a closed span, then the battery efficiency measurement is Σ discharge ÷
  (Σ charge − Δstored) with the middle-of-boundary-slot convention, Δstored =
  (SoC_end · capacity_end − SoC_begin · capacity_begin) ÷ 100, and the decayed
  ratio of TASK-0019 with weight Σ charge − Δstored.
- Given a capacity that changes by more than 1 % relative to the span's start
  anywhere inside it, or is unavailable, or a missing charge or discharge
  statistic, or Σ charge − Δstored not above 0, then the span is dropped and
  only the span (no reset of the estimate).
- Given a span, then it closes only when the next valley is real and its
  beginning is 125 min old.
- Given a simulator of a battery of known efficiency over several days with
  wobbles and one capacity change, then the estimate converges to the known
  value and the bias of the boundary convention is below 0.5 points; if not, the
  convention is changed and the change recorded in ADR-023 (Open decision 2).
- Given the Open decision 1 (an estimate above 100 %) as the human decided, then
  the machine does exactly that and a test pins it.
- Given a state dumped and restored between two chunks of slots, then the result
  equals one go; `calculation.py` stays free of Home Assistant.

## Estimated File / Module Footprint (hint, not a commitment)

`calculation.py` (new section: `ValleyState`, `valley_feed`, span accounting,
restore functions), `tests/test_valley_spans.py`, simulator helper shared with
TASK-0019.

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

- `DecayedRatio`, `decay_add`, `ratio_value` and the simulator helper from
  `calculation.py` / `tests/` (→ task: TASK-0019-efficiency-pure-run-machine) —
  **filled by the Lead before start**

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->
