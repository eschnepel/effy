# Task: Delta derivation: the history of the PV delta integrals and the first has_sum writer

- **Status:** done
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
  TASK-0045b-pv-delta-values, done: `history.async_recalculate_history` and
  `async_recalculate_recent` return
  `(written, from, touched, last_values, deltas)`,
  `deltas: dict[source PV entity ID, (delta, "Wh"|"kWh")] | None`, keyword
  `delta_skip`; `calculation.convert_unit`, `calculation.pv_delta`;
  `history._unchanged_slots` -> `(starts, text, stored)`;
  `Storage.unchanged_rows(..., values=False)`).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- `custom_components/effy/calculation.py` →
  `IntegralStep(start, energy, short=True)`,
  `IntegralRow(start, state, total, last_reset, short=True)` (frozen
  dataclasses); `local_midnight(ts, tz)`;
  `slot_energy_steps(rows_5min, rows_hourly, slot_minutes=5)` (5-minute row =
  power x 5/60; hourly row = mean x 1 h for hours ending at or before the oldest
  5-minute row; the hour that contains it carries what its 5-minute rows lack as
  an hourly-only step, never negative); `integral_rows(steps, tz, *, today)`;
  `combined_integral_rows(per_input, tz) -> (today_rows, total_rows)` with
  `per_input` an iterable of `(steps, "Wh" | "kWh")`, summed in kWh.
- `custom_components/effy/const.py` → `PV_DELTA_INTEGRAL_TODAY_SUFFIX`
  (`"_delta_integral_today"`), `PV_DELTA_TODAY_ENTITY_ID`
  (`"sensor.effy_pv_delta_integral_today"`), `PV_DELTA_TOTAL_ENTITY_ID`
  (`"sensor.effy_pv_delta_integral_total"`). TASK-0045f moves them into
  `effy_exports.py`.
- `custom_components/effy/history.py` →
  - `async_derive_pv_delta_integrals(hass, entry_options, *, now=None, storage=None) -> dict[str, DeltaBase]`,
    keyed by statistic ID (`_pv_delta_integral_id(source)`, the two combined
    IDs); only the series it derived have an entry; a series that already has
    metadata is skipped; nothing to derive gives `DeltaBase(0.0, None, 0.0)` and
    writes nothing. Not called by anything yet (the button never does).
  - `DeltaBase(state, last_reset, total)`; `_pv_delta_integral_id(entity_id)`;
    `_integral_statistics(rows, settled)`; `_fetch_integral_source(...)` (a
    reader); `_local_tz(hass)` (`hass.config.time_zone`, UTC with a warning if
    unknown).
  - `_build_statistic_metadata(statistic_id, unit, *, has_sum=False)`: with
    `has_sum` the flags are `has_mean=False, has_sum=True`, and on newer cores
    `mean_type` NONE and `unit_class` `"energy"`.
  - Writes both tables via `instance.async_import_statistics`; 5-minute rows
    only where a 5-minute row exists and `start < settled_before(now)`; hourly
    rows only for closed hours (`hour + 1 h <= settled`) with the values of the
    hour's last row.
- `storage.py`, `recorder_storage.py`, `cache.py` →
  `Storage.stored_means(statistic_id, start, end) -> (rows_5min, rows_hourly)`
  of `mean`; the cache passes it through (observed, never reused; the derivation
  is no part of a recorded run). The derivation reads only through the storage
  (ADR-025); `_fetch_integral_source` is registered as a reader in the guard
  test.
- Tests: new `tests/test_delta_derivation.py` (32) and 1 in
  `tests/test_history_cache.py`; `tests/support.py` fakes got `stored_means`.
  The stateful recorder of `test_history_write_only_changed` already kept `sum`,
  `last_reset` and the metadata flags; the new test only adds a subclass that
  records the imported metadata.
- External dependencies added: none (`zoneinfo` is standard library).
- **Open points for the human** (not decided here):
  1. *ID collision (ADR-027 decision 3).* A PV source whose slug is `pv` (for
     example `sensor.pv`) gets the per-input ID
     `sensor.effy_pv_delta_integral_today`, which is the combined *today* ID.
     The derivation leaves such a per-input series out and logs a warning;
     TASK-0045d's sensors need the same rule, and the ADR needs a decision on
     naming.
  1. *Unit class.* The has_sum metadata sets `unit_class` `"energy"` on newer
     cores so that it matches what the recorder gives the live sensor. Written
     from reading the code, not run against Home Assistant: check once on the
     Pi.
- Reviewer (inline, no sub-agent available): PASS.
