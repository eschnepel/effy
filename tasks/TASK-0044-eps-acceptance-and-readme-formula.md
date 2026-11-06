# Task: CAP-12 acceptance test and the README's formula and ADR index

- **Status:** todo
- **Capability:** CAP-12
- **Related ADRs:** [ADR-026, ADR-001, ADR-005]
- **Dependencies:** [TASK-0042-eps-activity-in-standby-and-efficiency, TASK-0043-eps-sensors-and-published-entries]
- **Gate:** Gate 2 pending (2026-11-06); ADR-026 is a Draft until the human confirms it.

## Goal

Close CAP-12: one end-to-end test that runs the demonstration of the capability,
and the README's description of the changed formula, so that code, README and
glossary say the same.

## Acceptance Criteria

- Given the CAP-12 demonstration, then one test module drives the real history
  recalculation on the in-memory recorder with an EPS sensor: 200 W out lowers
  the loss by 200 W; 150 W in raises the input total and takes an equal share;
  Σ effective inputs = Σ outputs; the same sources without an EPS sensor give
  results identical to before; an active EPS sensor makes a slot impure; the
  published list carries both entries.
- Given the README "How the algorithm works", then `total_loss` shows the EPS
  terms (`max(0, Σ inputs + eps_in − Σ outputs − eps_out)`), says what the signed
  sensor is split into, mentions the smoothing and the net-per-slot limit, and
  the worked example is unchanged or extended by one EPS line.
- Given the README ADR table, then ADR-026 is listed.
- Given `GLOSSARY.md`, `tasks/adr-summary.md` and ADR-026, then they agree with
  what was built; any deviation is a stop-and-ask (Phase 0), not a silent edit.
- Given the whole repository, then the full gate (`ruff format --check`,
  `ruff check`, `mypy --strict`, `pytest`) shows nothing the repository did not
  already have.

## Estimated File / Module Footprint (hint, not a commitment)

`tests/test_eps_capability.py` (new), `README.md`, `GLOSSARY.md` and
`tasks/adr-summary.md` only if the built code differs from their text.

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

- To be filled by the Lead at Phase 3 from the Delivered Artifacts of
  TASK-0042-eps-activity-in-standby-and-efficiency and
  TASK-0043-eps-sensors-and-published-entries.
- Pre-existing: the in-memory recorder rig of `tests/support.py`
  (`FakeStorage`, `StrictRecorderMixin`) and the harness of
  `tests/test_history_recent_equivalence.py`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- _(open)_
