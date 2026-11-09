# Task: Delta derivation: the history of the PV delta integrals and the first has_sum writer

- **Status:** todo
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-024, ADR-011]
- **Dependencies:** [TASK-0045b-pv-delta-values]
- **Gate:** Gate 2 approved by the human (2026-11-09, with Q27 = A); re-scoped
  in a design interview on 2026-11-07, split from the held TASK-0045.

## Goal

Effy can derive, once, the statistics history of the PV delta integrals from the
stored effective rows and write it as running-total rows, and returns the delta
base from which the live sensors continue. Nothing calls it yet.

## Acceptance Criteria

- Given per-slot energies of one input, then a pure function returns the rows of
  its integral: `state` is the energy accumulated since the local midnight of
  the row's day, `last_reset` that midnight, and the running total accumulates
  from the oldest row. A day of 23 or 25 hours (daylight saving) is handled.
  Example: slots of 25 Wh, 30 Wh, 20 Wh after midnight give `state` 25, 55, 75;
  the next day restarts at its first slot and the running total goes on.
- Given the combined integrals, then a pure function adds up the per-slot
  energies of the inputs (an input without a row adds 0, Wh and kWh are
  converted to kWh) and returns the *today* rows like the above and the *total*
  rows (`state` equal to the running total, no `last_reset`).
- Given the stored effective rows, then the energy of a slot is the row's power
  times 5/60 h from the 5-minute rows where they exist, and the hourly row's
  mean times 1 h for hours older than the 5-minute rows.
- Given the writer, then it writes both tables with metadata `has_sum=True`,
  `has_mean=False`, the sensor's unit, source `recorder`; 5-minute rows only up
  to the last completed slot, hourly rows only for closed hours (the settled
  rule of ADR-024), so a slot or an hour the recorder will compile is never
  written. An hourly row takes `state`, `last_reset` and running total of the
  last 5-minute row of its hour.
- Given a series that already has statistics, then the writer skips it and
  writes nothing; running the derivation twice writes nothing the second time.
- Given a derivation, then it returns the delta base per series: the last row's
  `state`, `last_reset` and running total (0 when there is nothing to derive).
- Given the button, then it does not call the derivation.
- The in-memory recorder world gets the means to record `has_sum` rows so that
  the tests read back what was written.

## Decisions

- ADR-027 decision 5 (human, 2026-11-07). How the combined integrals are derived
  again when the PV inputs change is TASK-0045e (decision 9, decided: A); this
  task only provides the pure functions and the writer.

## Estimated File / Module Footprint (hint, not a commitment)

`calculation.py` (pure derivation), `history.py` (reader of the effective rows
and the writer), `tests/test_history_recent_equivalence.py` or a helper beside
it, and tests.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (translations en and de for sensor
  names) · no open ADR conflicts (ADR-027 decision 9 decided: Q27 = A)
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-07). -->

- `history._compute_effective_slots(hass, options, start, end, ...)`,
  `history.async_recalculate_history`, `history.async_recalculate_recent` in
  `custom_components/effy/history.py`; `history._effy_entity_id`,
  `history._effy_smoothed_entity_id`.
- `inputs.pv_input_ids(options)` in `custom_components/effy/inputs.py`.
- `history._build_statistic_metadata`, `history._write_recorder_statistics`,
  `history._hourly_rows`, the settled rule for hourly rows and the
  last-completed-slot logic (ADR-024).
- The unit conversion and the delta helper of TASK-0045b (→ task:
  TASK-0045b-pv-delta-values).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
