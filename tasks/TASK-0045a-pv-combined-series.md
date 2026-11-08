# Task: PV combined series and the sensor Effective PV Sum now

- **Status:** todo
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-002, ADR-008, ADR-024]
- **Dependencies:** []
- **Gate:** Gate 2 pending (re-scoped by the human in a design interview on
  2026-11-07; split from the held TASK-0045). No code before approval.

## Goal

Effy adds up the effective values of all PV inputs per slot, writes the result
as a series like every effective series, and shows it as the sensor "Effective
PV Sum now".

## Acceptance Criteria

- Given an entry with at least one PV input, then in every slot the series is
  the addition of the PV inputs' effective values in W; kW inputs are normalised
  (ADR-002, ADR-008). Example: effective 300 W and 0.2 kW give 500 W.
- Given a PV input without a value in a slot, then it adds 0; given a slot where
  no PV input has a value, then the series has no row. The EPS sensor and the
  BMS inputs are not in it.
- Given the slot job, the recent recalculation and the button, then all three
  produce the series, and a slot whose effective values changed is rewritten
  (unchanged rows are not, ADR-024). A full recalculation and the recent
  recalculation over the same slots give identical rows (the CAP-1 equivalence
  test covers the new series).
- Given the hourly row, then it is the mean of its slots like every effective
  series.
- Given an entry with a PV input, then the sensor
  `sensor.effy_effective_pv_sum_now` ("Effective PV Sum now", unique ID
  `<entry_id>_effective_pv_sum_now`) exists: power in W, state class
  `measurement`, its live value pushed with the other effective sensors,
  restored like them after a restart.
- Given an entry without a PV input, or a PV input removed, then the sensor is
  not created or is removed from the entity registry; its statistics stay.
- Given the sensor names, then the translations en and de name it.
- Given the published list, then it is **not** yet in it (TASK-0045f).

## Decisions

- ADR-027 decision 1 (human, 2026-11-07). The name keeps the word "Sum".

## Estimated File / Module Footprint (hint, not a commitment)

`history.py`, `sensor.py`, `coordinator.py`, `const.py`, `translations/en.json`,
`translations/de.json`, and tests for each.

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

- `history._compute_effective_slots(hass, options, start, end, ...)`,
  `history.async_recalculate_history`, `history.async_recalculate_recent` in
  `custom_components/effy/history.py`; `history._effy_entity_id`,
  `history._effy_smoothed_entity_id`.
- `inputs.pv_input_ids(options)` in `custom_components/effy/inputs.py`.
- `sensor.EffySensor`, `sensor.EffyStandbyPowerSensor` and
  `sensor._standby_entities` (a sensor that exists only under a condition and is
  removed from the registry otherwise), `sensor._async_restore_measurement`.
- `coordinator.subscribe`, `coordinator.notify_updated`, the `last_values` the
  recalculations return.
- `tests/test_history_recent_equivalence.py` (`_World`, `_install`,
  `_run_full_recalc`, `_equivalent`), `tests/test_eps_waterfall._add_power`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
