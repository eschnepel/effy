# Task: PV delta of every run, from the stored values

- **Status:** done
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-024, ADR-002]
- **Dependencies:** [TASK-0045a-pv-combined-series]
- **Gate:** Gate 2 approved by the human (2026-11-09, with Q27 = A); re-scoped
  in a design interview on 2026-11-07, split from the held TASK-0045.

## Goal

Every recalculation reports, per PV input, how much energy it changed in the
stored rows of that input's effective series (the PV delta), computed from the
values that were stored before. No sensor uses it yet.

## Acceptance Criteria

- Given a run that writes rows, then the PV delta of an input is the addition of
  `(new − stored) × 5/60` over the rows it wrote, in Wh for an input whose
  effective unit is W and in kWh for kW. Example: stored 300 W is rewritten to
  290 W and a new slot gets 310 W: `(−10 + 310) × 5/60 = 25 Wh`.
- Given unchanged rows, then they add 0; given a run that writes nothing, then
  every PV input reports 0. Hourly rows do not count.
- Given the stored rows, then `history._unchanged_slots` returns their values as
  well as the unchanged starts, with no additional recorder read (a test counts
  the reads).
- Given a stored row in another unit than the new one, then it is converted by
  the unit in its metadata; given no metadata, then its value counts as 0.
- Given the stored rows cannot be read, then the run reports no delta for any
  input (not 0), logs a warning and writes every row as before.
- Given an input the caller marks as having no base yet, then it is left out of
  the result.
- Given `async_recalculate_recent` and `async_recalculate_history`, then both
  return the deltas, and `coordinator` receives them and books nothing (booking
  is TASK-0045d); all existing tests pass unchanged apart from the result shape.
- Given the EPS sensor and the BMS inputs, then they are not reported.
- A full recalculation over an empty recorder reports, per input, the energy of
  all its rows (equivalence test).

## Decisions

- ADR-024 Amendment 2026-11-09 (human): the stored values of the PV inputs'
  effective series cross to the event loop and the delta is computed there;
  other series still return only their unchanged starts. The event loop is held
  at most 1 s.
- ADR-027 decision 2 (human, 2026-11-07): deltas are booked only after the write
  has succeeded; that is the coordinator's job in TASK-0045d.

## Estimated File / Module Footprint (hint, not a commitment)

`history.py`, `calculation.py` (a pure delta function), `coordinator.py`
(receives the result), `storage.py`, `recorder_storage.py` and `cache.py` (the
stored values travel through `unchanged_rows`; the cache records them as an
optional field, an older snapshot replays as "no delta"), and tests for each.

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
- `history._unchanged_slots`, `history._write_recorder_statistics` and the call
  site in `async_recalculate_recent` / `async_recalculate_history` that reads
  the stored rows (ADR-024).
- The PV combined series of TASK-0045a (→ task: TASK-0045a-pv-combined-series,
  done: `per_sensor[const.PV_SUM_ENTITY_ID]` =
  `{"unit": "W", "slot_values": [...]}` from `history._compute_effective_slots`;
  `const.PV_SUM_ENTITY_ID`, `const.PV_SUM_UNIQUE_ID_SUFFIX`,
  `const.PV_SUM_TRANSLATION_KEY`; `sensor.EffyPvSumSensor`,
  `sensor.pv_sum_unique_id`, `sensor._pv_sum_entities`), for the shape of the
  slot loop's result.
- `coordinator._async_recalculate_recent_and_report`,
  `coordinator.async_recalculate_history_and_report`,
  `coordinator._recorder_job_lock`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- `custom_components/effy/calculation.py` →
  `convert_unit(value, from_unit, to_unit)` (W/kW; the same unit returns the
  value untouched) and `pv_delta(new_rows, stored, slot_minutes=5)` =
  `sum((new - stored) * slot_minutes / 60)`, a slot without a stored row
  counting with its new value alone.
- `custom_components/effy/history.py` →
  - both
    `async_recalculate_history(hass, entry_options, energy_reading_cache=None, storage=None, export=None, delta_skip=frozenset())`
    and
    `async_recalculate_recent(hass, entry_options, now, energy_reading_cache=None, trigger_since=None, storage=None, delta_skip=frozenset())`
    return the 5-tuple `_RecalcResult` =
    `(rows_written, recalculated_from, touched, last_values, deltas)`; `deltas`
    is `dict[source PV entity ID, (delta, "Wh" | "kWh")]` or `None` if the
    stored values could not be read (no delta, not 0). `delta_skip` holds source
    entity IDs to leave out (the inputs without a delta base yet). An input
    without an effective series in the run is not in the dict; a run that writes
    nothing reports `0.0` for each input with a series. Early returns without
    data give `{}`.
  - `_unchanged_slots(...)` returns `(unchanged_starts, diagnosis, stored)`,
    `stored` being `{start: mean}` of every stored row whose start is in
    `expected`.
  - `_unchanged_rows(hass, statistic_id, unit, short, hourly, notes=None, values=False)`
    returns `{"short", "hourly"}` and, with `values`, `"stored"` converted to
    `unit` (`{}` with no metadata).
  - `_mark_unchanged(..., delta_series=frozenset())` returns `bool` (the stored
    values were read) and adds `info["stored_short"]` for the series in
    `delta_series`.
  - `_pv_delta_series(entry_options, delta_skip)`,
    `_pv_deltas(per_sensor, entry_options, delta_skip, *, short_term_cutoff, values_read)`,
    type aliases `_PvDeltas`, `_RecalcResult`. Imports `convert_unit`,
    `pv_delta`.
- `custom_components/effy/storage.py`, `recorder_storage.py`, `cache.py` →
  `Storage.unchanged_rows(statistic_id, unit, short, hourly, notes=None, values=False) -> dict[str, Any]`;
  the cache key of a read with `values` ends in `|values` (the key without it is
  what it was, so an older snapshot still hits) and the observed entry carries
  `"stored": {iso: value}`; an entry without it replays as no delta.
- `custom_components/effy/coordinator.py` → unpacks the fifth value as `_deltas`
  at both call sites and books nothing (TASK-0045d); the history call's optional
  `export` keyword now travels in a `dict[str, Any]`, `export_kwargs`.
- Tests: new `tests/test_pv_delta_values.py` (26) and 2 tests in
  `tests/test_history_cache.py`; 4-tuple unpacks and annotations became 5-tuples
  in `replay.py`,
  `test_history_{compute_edges,phase_timing,recent_equivalence,recorder_rollup, write_only_changed,write_only_changed_recent}.py`
  and `test_pv_combined_series.py`; the coordinator fakes in
  `test_coordinator_slot.py` append an empty fifth value. No assertion changed
  otherwise.
- Event loop: the deltas of 4 PV inputs x 8,000 rows cost 3.5 ms on the loop
  (limit 1 s).
- Known difference to say plainly: a PV series whose stored unit differs from
  the new one used to cause no read at all; it now reads once to convert the
  stored values (only those series, only in that case). The normal path has no
  additional read.
- External dependencies added: none; `tasks/DEPENDENCIES.md` unchanged.
- Reviewer (inline, no sub-agent available): PASS.
