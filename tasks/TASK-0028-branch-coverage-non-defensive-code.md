# Task: Branch Coverage of the Non-Defensive Code

- **Status:** todo
- **Capability:** none (cross-cutting quality task; human request 2026-10-24)
- **Related ADRs:** \[ADR-000 §3 and §6 (module order, tests), ADR-018, ADR-021,
  ADR-001\]
- **Dependencies:** []
- **Gate:** none inside the task. The worker goes through the file slices below
  one after the other without stopping; the normal review (Phase 4b) follows
  after the last slice. Not blocked by Draft ADR-023.

## Goal

Find out which branches of `custom_components/effy` no test reaches, separate
the ones that carry behaviour from the ones that only guard, and cover the first
kind with tests that assert that behaviour. Nothing about the branches is known
yet: this task investigates them (the request deliberately kept it out of the
chat session where the task was created).

## How the work is cut: file slices

The worker takes **one slice at a time** and finishes it completely before the
next: measure → classify → write tests → measure again → record. A slice is one
module of `custom_components/effy`; a module of more than about 400 lines is cut
at function boundaries into several slices (by its existing sections, so that
each slice's context stays small). For a slice the worker reads only that
module's source, the tests that load it and the ADRs its docstrings name, and
puts the previous slice's source out of its working context.

Proposed order, leaf modules first (ADR-000 §3), so that a module's callees are
already covered when it is reached:

| # | Slice | Note |
| -- | -- | -- |
| 0 | tooling | coverage configuration in `pyproject.toml`, the first run, `tasks/DEPENDENCIES.md`; no test yet |
| 1 | `effy_exports.py` | already pinned by `tests/test_effy_exports.py`; expected to be quick |
| 2 | `const.py`, `inputs.py` |  |
| 3 | `catalog.py`, `sensor_utils.py` |  |
| 4 | `calculation.py` | cut into sub-slices by section (slot helpers and trapezoid, gap handling, the standby machine) |
| 5 | `history.py` | cut into sub-slices by section (reading, the recalculation paths, the standby glue, writing) |
| 6 | `config_flow.py` |  |
| 7 | `coordinator.py` |  |
| 8 | `sensor.py` | cut by sensor class if over the limit |
| 9 | `button.py`, `__init__.py` |  |

The worker may reorder or re-cut slices when the source shows a better cut, and
records the final slice list at the top of the report. Modules that earlier
tasks add later (TASK-0017 to TASK-0027) are not in this list; each of those
tasks answers for the coverage of its own new code.

## Definitions (the worker applies them; there is no gate to change them)

Every uncovered branch gets exactly one class:

- **A, non-defensive:** reachable with legal input or a legal state of the
  system, and its behaviour is specified by an ADR, the glossary, the README or
  the docstring of its function — including a specified failure behaviour
  ("logged, state unchanged"). Worth a test.
- **B, defensive:** a guard against a state the callers or the ADRs rule out, or
  against a malformed value from outside (a `None` or a wrong type where Home
  Assistant or the recorder promises better), and no document specifies what it
  does. Left alone.
- **C, dead:** cannot be reached at all, with any input. Reported, never removed
  here.
- **D, needs real Home Assistant:** reachable, but only with behaviour the stubs
  in `tests/` cannot show (Home Assistant is not installed in the sandbox).
  Listed; not covered here.

When a branch could be A or B, it is A if it changes what the user, a downstream
integration or a recorder row sees; otherwise B. The worker applies this rule
itself and says why in one line. If a branch still cannot be classified, it goes
to the report's list of open questions as **B for now** and the slice goes on;
the human decides afterwards.

## Acceptance Criteria

**Per slice:**

- Given the slice's module, when the suite runs with branch coverage limited to
  it (coverage configured in `pyproject.toml` under `[tool.coverage.*]`, source
  given as the directory because the tests load modules by file path), then the
  report gets a section for the slice with its line and branch coverage before
  and after, and every uncovered branch as file, line, condition and the
  direction not taken.
- Given each uncovered branch of the slice, then the section classifies it A, B,
  C or D with a one-line reason and the ADR or document it relies on.
- Given every class A branch of the slice, then a test reaches it and asserts
  the behaviour that makes it class A (a returned value, a published attribute,
  a logged failure with an unchanged state, a recorder row), not merely that the
  line ran.
- Given each new test, then breaking the branch's behaviour on purpose (once, by
  hand, then undone) makes it fail; the section records that this was done.
- Given the end of the slice, then the whole suite is green and `ruff format`,
  `ruff check` and `mypy --strict` show nothing the repository did not already
  have, before the next slice starts.
- Given a class A branch that cannot be covered without changing production
  code, then it is listed in the report as a finding (a bug, or code that needs
  a seam) with a proposed follow-up, and the slice goes on.

**Over the whole task:**

- Given the production code, then no file under `custom_components/` changes: a
  bug or a dead branch is reported, not fixed inline.
- Given classes B, C and D, then they are left as they are and listed; whether
  to mark any `# pragma: no cover`, remove dead code or add a coverage threshold
  is for the human and is listed at the end of the report as questions, not
  decided here.
- Given the last slice, then the report ends with a table of branch coverage per
  module before and after, the findings, the open questions, and the A branches
  left uncovered (if any) with the reason.
- Given all tests that existed before, then they pass unchanged.
- Given `tasks/DEPENDENCIES.md`, then the coverage tool is recorded as a
  development-only tool (name, version, install command, import) and is not
  added to `manifest.json`.

## Estimated File / Module Footprint (hint, not a commitment)

`pyproject.toml` (coverage configuration), `tasks/coverage-report.md` (new, one
section appended per slice), `tasks/DEPENDENCIES.md`, new or existing files
under `tests/` (one test module per source module, named after it where a new
file is needed). No file under `custom_components/`.

## Definition of Done

- Tests green · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- The report has a section for every slice, lists every uncovered branch with a
  class, and no class A branch is left without a test or a finding
- Any new external dependency recorded in `tasks/DEPENDENCIES.md`

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. -->

- None: the task reads the existing source and tests and consumes no symbol.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

## Notes for the Lead

- Independent of every other open task and touches only `tests/`, the coverage
  configuration and documents, so it can run at any time. Run it **before
  TASK-0017** if possible: it then measures the code the ADR-023 tasks start
  from.
- If the worker fails the review three times (Phase 4c), escalate with the
  slices already finished: their tests and report sections stand on their own.
- Whether the ADR-023 tasks should carry a coverage threshold is for the human
  to decide from the report.
