# Task: Formula signature and the implicit full recalculation with the delta derivation

- **Status:** todo
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-019, ADR-024, ADR-012]
- **Dependencies:** \[TASK-0045c-delta-derivation,
  TASK-0045d-pv-delta-integral-sensors\]
- **Gate:** Gate 2 pending (re-scoped by the human in a design interview on
  2026-11-07; split from the held TASK-0045). No code before approval.

## Goal

A change of the configured sensors that influence the formula, and the first
start after the upgrade, runs the full history recalculation and the delta
derivation by itself, then hands the bases to the sensors.

## Acceptance Criteria

- Given the options, then a pure function returns the formula signature: the PV
  inputs (order does not matter), grid import, discharge, charge, grid export
  and EPS sensor. A change of history days, SoC, capacity, valley hysteresis or
  debug export does not change it.
- Given setup of an entry whose data holds no signature or a different one, then
  a background task (setup does not wait for it) runs under the recalculation
  lock: the full history recalculation, the delta derivation for every PV input
  that has no statistics, the combined integrals (see decision below), the
  hand-over of all bases to the sensors, and finally the signature is stored in
  the entry's **data**. Storing it reloads nothing and no update listener is
  added.
- Given an equal signature, then nothing runs at setup.
- Given an input without a base during that recalculation, then its deltas are
  not booked (the derivation gives its base); inputs that have a base book
  normally. Which inputs have no base is decided by whether their integral has
  statistics, not by the signature.
- Given a PV input that is added, then it is derived after the recalculation;
  given an input that is removed and added again, then its statistics exist,
  nothing is derived and its base is its last statistic row (`state`,
  `last_reset`), and the recalculation books the gap as deltas.
- Given any failing step, then the signature is not stored, a warning is logged,
  the sensors stay `unavailable`, the slot jobs keep working, and the next setup
  tries again.
- Given an entry without a PV input, then a signature change still runs the full
  recalculation and derives nothing.
- Given the button after the bases exist, then its deltas are booked normally.
- Given the options flow, then a formula-relevant change reloads the entry and
  triggers the above, and any other change reloads and triggers nothing.

## Decisions

- ADR-027 decision 6 (human, 2026-11-07).
- **Decision 9 / Q27 is open and recommended A; this task builds A until the
  human decides:** when the PV inputs change after the integrals exist, the
  combined integrals are derived again as the per-slot addition of the per-input
  statistics of the configured inputs (the per-slot energy of an input is the
  change of its running total; a new input's comes from its derivation), written
  as a one-time rewrite while the sensors are `unavailable`, and a removed input
  leaves the combined history. If the human chooses B, the combined series are
  left untouched and this paragraph and its tests change; nothing else does.

## Estimated File / Module Footprint (hint, not a commitment)

`__init__.py`, `coordinator.py`, `inputs.py` (the signature), `history.py` (the
orchestration and the reader of per-input statistics), and tests for each.

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
- `__init__.async_setup_entry` (entry data, `async_update_entry`; the options
  flow is `OptionsFlowWithReload`), `coordinator._recorder_job_lock`,
  `coordinator.async_recalculate_history_and_report`.
- The derivation, its writer and the delta base (→ task:
  TASK-0045c-delta-derivation).
- The sensors, booking and base handover (→ task:
  TASK-0045d-pv-delta-integral-sensors).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
