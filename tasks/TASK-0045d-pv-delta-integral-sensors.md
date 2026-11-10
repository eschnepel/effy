# Task: The PV delta integral sensors, their booking, the midnight reset and the base handover

- **Status:** todo
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027, ADR-016, ADR-011]
- **Dependencies:** [TASK-0045b-pv-delta-values, TASK-0045c-delta-derivation]
- **Gate:** Gate 2 approved by the human (2026-11-09, with Q27 = A); re-scoped
  in a design interview on 2026-11-07, split from the held TASK-0045.

## Goal

The PV delta integral sensors exist, hand over their base, book every run's
deltas at the time of the write, and reset at local midnight; Home Assistant
compiles their statistics.

## Acceptance Criteria

- Given an entry with at least one PV input, then these sensors exist, device
  class energy, state class `total`, unique IDs `<entry_id>_…`:
  - per PV input `sensor.effy_<slug>_delta_integral_today`, "<source name> delta
    integral today", in Wh (W input) or kWh (kW input), with `last_reset`;
  - `sensor.effy_pv_delta_integral_today` ("PV delta integral today", kWh, with
    `last_reset`);
  - `sensor.effy_pv_delta_integral_total` ("PV delta integral total", kWh, no
    `last_reset`).
- Given an entry without a PV input, or a PV input removed, then the sensors are
  not created or are removed from the entity registry (the per-input one with
  its input); their statistics stay.
- Given no base yet and nothing to restore, then each sensor is `unavailable`;
  given the coordinator hands over the base of TASK-0045c, then it pushes
  exactly the base `state` (and `last_reset`) and is available.
- Given a restart, then each sensor restores its last state like the other Effy
  sensors (an `unavailable` or unknown state restores nothing).
- Given a run's deltas (TASK-0045b), then after the write has succeeded the
  coordinator books them: each per-input state grows by that input's delta, the
  combined *today* and *total* by the addition of all deltas in kWh, whatever
  slot the rows belong to. Negative deltas lower the states; nothing is clamped,
  and *today* may go below 0. Nothing is booked for a sensor that is
  `unavailable` or for an input without a base, and a run without a delta books
  nothing.
- Given a booking, then the state is kept at full precision in memory and a
  `state_changed` event reaches the recorder.
- Given local midnight (wall clock, daylight saving included), then both *today*
  kinds are set to 0 with `last_reset` at that midnight; given a restored state
  whose `last_reset` is of an earlier day, then it is reset the same way at
  startup. *Total* is never reset.
- Given the sensor names, then the translations en and de name them.
- Given the published list, then they are **not** yet in it (TASK-0045f).

## Decisions

- ADR-027 decisions 3, 4 and 5 (human, 2026-11-07): attribution by write time,
  today may go negative, statistics belong to Home Assistant, restart restores
  like the other sensors, a crash loss is accepted.

## Estimated File / Module Footprint (hint, not a commitment)

`sensor.py`, `coordinator.py` (booking, base handover, midnight timer, restore
and subscribe in the style of `restore_standby` / `subscribe_standby`),
`const.py`, `translations/en.json`, `translations/de.json`, and tests for each.

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
- `coordinator.restore_standby`, `coordinator.subscribe_standby`,
  `coordinator.async_setup`, `coordinator.async_shutdown`,
  `coordinator._schedule_next_slot_timer` (the pattern for timers),
  `sensor.EffyStandbyPowerSensor`, `sensor._standby_entities`,
  `sensor._async_restore_measurement`.
- The deltas the recalculations return (→ task: TASK-0045b-pv-delta-values,
  done: `history.async_recalculate_history` and `async_recalculate_recent`
  return `(written, from, touched, last_values, deltas)`,
  `deltas: dict[source PV entity ID, (delta, "Wh"|"kWh")] | None`, keyword
  `delta_skip`; `calculation.convert_unit`, `calculation.pv_delta`;
  `history._unchanged_slots` -> `(starts, text, stored)`;
  `Storage.unchanged_rows(..., values=False)`).
- The delta base per series and the derivation (→ task:
  TASK-0045c-delta-derivation).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
