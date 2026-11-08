# Task: CAP-13 acceptance test and README

- **Status:** todo
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-019, ADR-021, ADR-024]
- **Dependencies:** \[TASK-0045a-pv-combined-series, TASK-0045b-pv-delta-values,
  TASK-0045c-delta-derivation, TASK-0045d-pv-delta-integral-sensors,
  TASK-0045e-formula-signature-and-implicit-recalc,
  TASK-0045f-pv-published-entries\]
- **Gate:** Gate 2 pending (re-scoped by the human in a design interview on
  2026-11-07; split from the held TASK-0045). No code before approval.

## Goal

One acceptance module demonstrates CAP-13 end to end on the real recalculation,
and the README describes the sensors and what to expect from them.

## Acceptance Criteria

- Given one module `tests/test_pv_delta_capability.py` on the in-memory recorder
  with two PV inputs (one in W, one in kW) and an output, then it shows, with
  the numbers written out in the test and not taken from the code:
  - the PV combined series is the addition of the inputs' effective values;
  - the derivation gives both inputs and the combined integrals a history, the
    running total is the energy of all rows, and the combined integrals equal
    the per-input ones added up;
  - the next slot books its energy into the three integral kinds;
  - a formula-relevant option change runs the full recalculation and derivation
    by itself, and an option that does not influence the formula does not;
  - a button run after a correction books it in today and total (today may go
    negative) and the combined still equal the per-input ones added up;
  - the `change` of an hour equals the energy booked in it;
  - an entry without a PV input has none of the sensors;
  - the published list carries the four kinds.
- Given the README, then a section describes: the sensors and their IDs; the PV
  delta and the formula; what the derivation writes and when it runs; the
  formula signature and that every existing entry recalculates once after the
  upgrade; that a period's delta is the `change` of the statistic and is shifted
  by one slot; that today can go below 0 after a correction; the contract table
  with the four new entries; and the ADR table lists ADR-027 with its title.
- Given `tasks/adr-summary.md`, then the ADR-027 entry says implemented, with
  the state of decision 9; `GLOSSARY.md` agrees with the built code.
- Given the translations en and de, then every new text is there.

## Decisions

- ADR-027 (human, 2026-11-07). Decision 9 is closed by the human before this
  task.

## Estimated File / Module Footprint (hint, not a commitment)

`tests/test_pv_delta_capability.py` (new), `README.md`, `tasks/adr-summary.md`,
`GLOSSARY.md` if the code differs, and the translations if a text is missing.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (translations en and de for sensor
  names) · no open ADR conflicts (ADR-027, decision 9, is the only open point)
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-07). -->

- The tests and the harness of TASK-0039 to TASK-0044
  (`tests/test_eps_capability.py` is the model for this module),
  `tests/test_history_recent_equivalence.py`.
- Everything the other CAP-13 tasks deliver (→ task: TASK-0045a to TASK-0045f).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
