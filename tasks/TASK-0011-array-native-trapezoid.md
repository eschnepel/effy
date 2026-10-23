# Task: Array-Native Trapezoid and the numpy Dependency

- **Status:** todo — draft, blocked: gate G-perf (ADR-022 Decision 2) and Gate 2
- **Related ADRs:** [ADR-022, ADR-012, ADR-013, ADR-014, ADR-000 (§3, §6)]
- **Dependencies:** [TASK-0010-recalculation-phase-timing]

## Goal

A pure function that computes the per-slot contributions of one energy-family
counter as an array, with the same results as `trapezoidal_slot_contributions`,
and the numpy dependency that makes it possible. Nothing is wired into
`history.py` here.

## Acceptance Criteria

**Step 1 — investigate before any code (result goes into ADR-022 as an amendment
in the text, for the human to confirm).**

- Which numpy version Home Assistant 2026.3 (the minimum) constrains, where that
  is stated, and whether a `requirements` entry in a custom integration's
  `manifest.json` must equal it. Whether the wheel for the Pi's platform (Home
  Assistant OS on aarch64) installs from a custom integration's requirements.
  Anything not found in a source is reported as not verified; the human checks
  the install on the Pi.

**Step 2 — spike, then choose (amendment to ADR-022 Decision 7).** Both
formulations, on the same seeded histories:

- cumulative difference: contribution = F(slot end) − F(slot start) of the
  piece-wise linear cumulative counter (the form benchmarked in ADR-022);
- per-transition overlap: each transition's window is split at slot boundaries,
  overlap × rate is summed per slot (local arithmetic, no monotonic-time
  assumption).

Report for each: worst absolute and relative difference to the reference on the
seeded histories, behaviour on a history whose timestamp goes backwards, and
time on a 28-day synthetic history. The one with the larger equivalence margin
that is still at least 3× faster than the reference is kept; if neither, stop
and ask.

**Step 3 — implement the kept formulation as `trapezoid_slot_array(...)` in a
new `calculation_grid.py`.** The oracle is
`calculation.trapezoidal_slot_contributions`; tests use no mocking (ADR-000 §6).

- Given seeded random chronological histories (ticks of 0.01 kWh at varying
  rates, idle stretches, `unavailable`/`unknown` stretches, recovery after an
  outage, equal timestamps, a last reading before `now`), when both functions
  run, then the slot keys are identical and every value is within `abs=1e-9`.
- Given hand-made edge cases (empty list; only invalid states; one valid state
  with and without `now`; first reading exactly on a slot boundary; a transition
  longer than `TRAPEZOID_MAX_MINUTES`; an outage recovery of any delta; a
  timestamp that goes backwards), then the result equals the reference's.
- Given the 28-day synthetic history, then the speed-up against the reference is
  reported in the task note (measured, not asserted in a test).
- Given the module, then it imports no Home Assistant module, performs no I/O,
  and imports only numpy outside the standard library and `calculation`.

**Dependency.**

- Given `manifest.json`, then `requirements` lists numpy as Step 1 found;
  `tasks/DEPENDENCIES.md` has the entry (name, version, install command,
  canonical `import numpy as np`); the dev group in `pyproject.toml` and
  `uv.lock` have it; `mypy --strict` passes.
- Given ADR-000 §3, then its diagram lists `calculation_grid.py` at the layer of
  `calculation.py` (amendment, human confirms).

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/calculation_grid.py` (new)
- `tests/test_calculation_grid_trapezoid.py` (new; the history generator may
  live in a shared `tests/_grid_data.py` for TASK-0012/0013)
- `custom_components/effy/manifest.json`, `pyproject.toml`, `uv.lock`
- `tasks/DEPENDENCIES.md`, `adr/000-coding-standards.md` (§3), `adr/022-*.md`

## Definition of Done

- Tests green · `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`,
  `mdformat --check` clean · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md`

## Consumed Interfaces

<!-- Pre-existing symbols (verified 2026-10-19). From TASK-0010 only the log
     line matters here: the record `Effy recalculation timing (history): ...`
     (logger `custom_components.effy.history`) is how the before/after wall
     times in this task's note are read. -->

- `trapezoidal_slot_contributions`, `TRAPEZOID_MAX_MINUTES`,
  `_parse_energy_state`, `_slot_aligned` from
  `custom_components/effy/calculation.py` (the first two are the reference
  contract; the last two only to read their exact semantics)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->
