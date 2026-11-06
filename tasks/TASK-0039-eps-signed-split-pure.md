# Task: Pure split of the signed EPS reading into an EPS output and an EPS input

- **Status:** todo
- **Capability:** CAP-12
- **Related ADRs:** [ADR-026 decisions 2 and 8, ADR-001, ADR-005, ADR-002]
- **Dependencies:** []
- **Gate:** Gate 2 pending (2026-11-06); ADR-026 is a Draft until the human
  confirms it.

## Goal

A pure function in `calculation.py` that turns the EPS sensor's signed reading
of one slot into its two halves (the EPS input for the inputs, the EPS output
for the outputs), and tests that show what that does to the loss formula and the
waterfall. No Home Assistant, no I/O, no wiring.

## Acceptance Criteria

- Given a reading with a positive value, then the input half is 0 and the output
  half carries the value; given a negative value, then the input half carries
  the absolute value and the output half is 0; given 0, then both are 0. At most
  one half is not zero.
- Given a reading in kW (or W), then both halves keep the entity ID and the unit
  of the reading (the split does not convert; ADR-002 keeps one normalisation
  point inside `distribute_loss`).
- Given an EPS output of 200 W, the other readings unchanged, then
  `distribute_loss` gives a total loss 200 W lower than without it (never below
  0, ADR-005).
- Given an EPS input of 150 W, then the input total is 150 W higher, the EPS
  input is an active input with an equal share (a share it cannot carry is
  passed on, ADR-001), and Σ effective inputs = Σ outputs.
- Given an EPS input of 0 (the port exports), then the reading is still returned
  as an input reading of 0, so the sensor keeps a row in the effective series,
  and it takes no share.
- Given `distribute_loss` and the existing functions, then their signatures and
  results do not change (the whole existing calculation test file stays green).
- Given the module, then it imports nothing from Home Assistant and is tested
  with no mocking, loaded by file path (ADR-000 §6).

## Estimated File / Module Footprint (hint, not a commitment)

`custom_components/effy/calculation.py` (one new function, e.g.
`split_signed_reading(reading) -> tuple[SensorReading, SensorReading]`, input
half first ◇), `tests/test_calculation.py`.

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

- Pre-existing, `calculation.py`:
  `SensorReading(entity_id, raw_value, original_unit)`,
  `distribute_loss(inputs, outputs) -> LossDistribution`, `LossDistribution`
  (`total_loss_w`, `shares`, `effective_values_w`),
  `effective_in_original_unit`.
- Pre-existing, `tests/test_calculation.py`: the file-path import of
  `calculation` used by the existing classes.
- No dependency tasks.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- _(open)_
