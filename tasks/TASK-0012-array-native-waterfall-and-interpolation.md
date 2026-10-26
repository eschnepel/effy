# Task: Array-Native Waterfall, Unit Conversion and Gap Interpolation

- **Status:** **skipped** (2026-10-25, human: not released by gate G-perf —
  compute is 0.4 % of a history recalculation, ADR-022 outcome). Kept as an
  audit trail; do not build.
- **Related ADRs:** \[ADR-022, ADR-001, ADR-002, ADR-005, ADR-008, ADR-013,
  ADR-000 (§6)\]
- **Dependencies:** [TASK-0011-array-native-trapezoid] (same new module and the
  numpy dependency → sequential)

## Goal

Pure array functions for the second and third compute stage on one slot grid:
gap interpolation of a power-family column, unit conversion to and from watts,
and the waterfall across all slots at once. Same algorithm as `distribute_loss`:
ascending sort, equal-share rule, residual to the largest active input. Nothing
is wired into `history.py` here.

## Acceptance Criteria

Oracles: `calculation.distribute_loss`, `effective_in_original_unit`,
`interpolate_slot_gaps`. No mocking.

**Waterfall.**

- Given fuzzed rows (1 to 8 inputs, 1 to 4 outputs; per-column unit W or kW;
  zeros, negative readings, ties between active inputs, loss above the sum of
  the active inputs, rows with no active input), when both run, then total loss,
  shares and effective values are within `abs=1e-9` of the reference per row.
- Given two active inputs with equal values and a loss above their sum, then the
  residual lands on the same input as in the reference (stable order by input
  position).
- Given a missing reading (NaN in the grid), then it is not active, yields no
  result, and a missing output counts as 0; a slot with no input reading yields
  no result — exactly as `_readings_for_slot` plus `distribute_loss` do.

**Unit conversion.** Given energy-family columns (kWh/Wh per slot) and
power-family columns (W/kW), then the conversion to watts and back reproduces
`to_power_equivalent` and `effective_in_original_unit` for every unit the
current code supports.

**Interpolation.** Given a power-family column with gaps of 1, 2 and 3 slots,
leading and trailing gaps, then the filled slots equal `interpolate_slot_gaps`
(`INTERPOLATION_MAX_GAP_SLOTS` = 2), and a recorder row is never overwritten.

**Module.** Given `calculation_grid.py`, then the TASK-0011 import rules still
hold.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/calculation_grid.py` (extended)
- `tests/test_calculation_grid_waterfall.py`,
  `tests/test_calculation_grid_interpolation.py` (new)

## Definition of Done

- Tests green · `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`,
  `mdformat --check` clean · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none
  expected)

## Consumed Interfaces

<!-- Pre-existing (verified 2026-10-19); the Lead Agent copies TASK-0011's
     Delivered Artifacts here before the start. -->

- `distribute_loss`, `SensorReading`, `LossDistribution`,
  `effective_in_original_unit`, `interpolate_slot_gaps`,
  `INTERPOLATION_MAX_GAP_SLOTS`, `_to_w`, `_from_w` from
  `custom_components/effy/calculation.py`
- `to_power_equivalent`, `effective_unit_for`, `is_energy_family` from
  `custom_components/effy/sensor_utils.py`
- `_readings_for_slot` from `custom_components/effy/history.py` (to read the
  missing-reading semantics; not imported)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->
