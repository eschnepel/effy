# Task: The PV delta integral sensors, their booking, the midnight reset and the base handover

- **Status:** done
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027 (Amendment 2026-11-10), ADR-016, ADR-011]
- **Dependencies:** \[TASK-0045b-pv-delta-values, TASK-0045c-delta-derivation,
  TASK-0045c-delta-derivation-patch-1\]
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
  - `sensor.effy_pv_deltasum_integral_today` ("PV delta integral today", kWh,
    with `last_reset`);
  - `sensor.effy_pv_deltasum_integral_total` ("PV delta integral total", kWh, no
    `last_reset`) (the combined IDs read `deltasum`: ADR-027 Amendment
    2026-11-10; so do their unique IDs `<entry_id>_pv_deltasum_integral_…`).
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
  TASK-0045c-delta-derivation, done:
  `history.async_derive_pv_delta_integrals(hass, options, *, now=None, storage=None) -> dict[statistic ID, history.DeltaBase(state, last_reset, total)]`
  (only derived series); `history._pv_delta_integral_id(source)`,
  `const.PV_DELTA_TODAY_ENTITY_ID`, `const.PV_DELTA_TOTAL_ENTITY_ID`;
  `calculation.slot_energy_steps`, `integral_rows`, `combined_integral_rows`;
  `Storage.stored_means`).

- `const.PV_DELTA_TODAY_ENTITY_ID` / `PV_DELTA_TOTAL_ENTITY_ID` with their new
  `deltasum` values (→ task: TASK-0045c-delta-derivation-patch-1, done).

## Delivered Artifacts

- `custom_components/effy/delta_integrals.py` (new, pure, no Home Assistant):
  `Kind` (`Literal["today", "total"]`), `TODAY`, `TOTAL`, `COMBINED_UNIT`
  (`"kWh"`), protocol `Base` (`state`, `last_reset`; `history.DeltaBase` fits),
  dataclass
  `DeltaIntegral(entity_id, kind, source, unit, state=None, last_reset=None)`
  with `.available`, and class `DeltaIntegrals` with `get`, `all`,
  `restore(*, entity_id, kind, source, unit, value, last_reset, now, tz)`,
  `has_base(source)`, `skip_sources(sources)`, `book(deltas)`,
  `reset_today(midnight)`, `hand_over(bases, now, tz, units=None)`; each of
  `book`, `reset_today` and `hand_over` returns the integrals that changed.
- `custom_components/effy/calculation.py` → `next_local_midnight(ts, tz)`.
- `custom_components/effy/coordinator.py` → `EffyCoordinator.pv_delta_enabled`,
  `restore_delta_integral(entity_id, kind, source, unit, value, last_reset)`,
  `subscribe_delta_integral(entity_id, cb)` (callback gets the `DeltaIntegral`),
  `hand_over_delta_bases(bases, units=None)` (**the entry point TASK-0045e calls
  with the result of `history.async_derive_pv_delta_integrals`; `units`
  optionally names the unit of each derived series**), private
  `_delta_integrals`, `_delta_skip`, `_book_deltas`, `_schedule_midnight`,
  `_on_midnight`; type alias `DeltaIntegralCallback`. The slot job and
  `async_recalculate_history_and_report` pass `delta_skip=` (the PV inputs whose
  integral has no base) and book the returned deltas as soon as the call
  returns. `async_setup` starts the midnight timer (only with a PV input),
  `async_shutdown` cancels it. New imports:
  `homeassistant.helpers.event.async_track_point_in_utc_time`,
  `history._local_tz`.
- `custom_components/effy/sensor.py` → class
  `EffyPvDeltaIntegralSensor(hass, entry, coordinator, kind, source_entity_id=None)`
  (energy, `total`, `last_reset` for *today*, restore, translation names),
  builder `_pv_delta_entities` (creates the sensors, removes stale ones from the
  entity registry), called from `async_setup_entry` after `_pv_sum_entities`.
  Not registered as published.
- `custom_components/effy/const.py` → `PV_DELTA_TODAY_UNIQUE_ID_SUFFIX`
  (`"pv_deltasum_integral_today"`), `PV_DELTA_TOTAL_UNIQUE_ID_SUFFIX`
  (`"pv_deltasum_integral_total"`), `PV_DELTA_INPUT_TRANSLATION_KEY`
  (`"pv_input_delta_integral_today"`), `PV_DELTA_TODAY_TRANSLATION_KEY`
  (`"pv_delta_integral_today"`), `PV_DELTA_TOTAL_TRANSLATION_KEY`
  (`"pv_delta_integral_total"`).
- `custom_components/effy/translations/en.json`, `de.json` → entity names for
  the three translation keys (per-input one with the placeholder
  `{source_name}`).
- Identities: per input entity ID `sensor.effy_<slug>_delta_integral_today`,
  unique ID `<entry_id>_<slug>_delta_integral_today`; combined entity IDs
  `sensor.effy_pv_deltasum_integral_today` / `_total`, unique IDs
  `<entry_id>_pv_deltasum_integral_today` / `_total`.
- Tests: `tests/test_delta_integrals.py` (pure, 40),
  `tests/test_coordinator_delta_integrals.py` (30),
  `tests/test_pv_delta_sensors.py` (45); shared fakes extended in
  `tests/test_coordinator_slot.py` (`delta_skip`, `_deltas_return`, `_local_tz`,
  the timer stub), `tests/test_standby_sensor.py` and
  `tests/test_efficiency_sensor.py` (energy device class, registry listing,
  `delta_integrals` loaded); the entity lists of `tests/test_sensor_setup.py`.
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged).
- Behaviour to know (decisions of the Lead Agent within the task, flagged for
  the human): a base of an earlier local day (the midnight passed while the
  sensor was unavailable) is reset like a restored state; a *today* state
  restored without a readable `last_reset` is reset; a base for a sensor that
  does not exist yet waits for it; a delta in another unit than the sensor's is
  converted; without a restored unit and a source state the per-input unit is Wh
  until a hand-over names it.
- **Nothing in the integration calls `hand_over_delta_bases` yet** (TASK-0045e).
  Until then a fresh sensor stays `unavailable` and its input is skipped by
  `delta_skip`; a restored one books normally.
- Reviewer (inline, no sub-agent available): PASS. Every acceptance criterion
  has a test; the code stays within ADR-027 (decisions 3 to 5, Amendment
  2026-11-10); full suite 1958 passed (1842 before this change set);
  `ruff check` and `mypy --strict` clean; `ruff format` flags only
  `tests/test_cache.py`, as before. Mutation checks (booking removed, midnight
  using the callback time) make the coordinator tests fail.
