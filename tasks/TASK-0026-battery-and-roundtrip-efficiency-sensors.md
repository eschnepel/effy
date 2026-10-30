# Task: The battery efficiency and roundtrip efficiency sensors

- **Status:** done — 2026-10-30 (inline)
- **Capability:** CAP-9
- **Related ADRs:** \[ADR-023 decisions 2 and 8, ADR-021 (Amendments
  2026-10-23)\]
- **Dependencies:** \[TASK-0021-charge-and-discharge-efficiency-sensors,
  TASK-0025-battery-slot-feed-and-restore\]
- **Gate:** ADR-023 accepted 2026-10-27.

## Goal

`sensor.effy_battery_efficiency` and `sensor.effy_roundtrip_efficiency`, with
the availability rules of ADR-023, restored and published.

## Acceptance Criteria

- Given charge, discharge, SoC and capacity configured, then
  `sensor.effy_battery_efficiency` exists; it does not wait for the standby
  value and is `unavailable` until its first span.
- Given all three efficiencies configured, then
  `sensor.effy_roundtrip_efficiency` exists, equals the product of the three
  published values, and is `unavailable` whenever any of them is.
- Given both sensors, then unit, display, state class, attributes and restore
  are those of TASK-0021 (for the battery: `counted_runs` counts spans, plus
  `last_valley_span_begin` and `last_valley_span_end`).
- Given the published configuration, then both appear with series
  `battery_efficiency` / `roundtrip_efficiency`, direction `output`, role `bms`,
  `source_entity_id` null, aggregations `[mean, min, max]`, no `function`; the
  symbols join `effy_exports.py` and its pinned test.
- Given the ADR-023 known limit (roundtrip runs slightly high), then the README
  says so in one sentence.
- Given the translations (en, de), then both sensors are described.

## Estimated File / Module Footprint (hint, not a commitment)

`sensor.py`, `catalog.py`, `effy_exports.py`, `const.py`, `translations/*.json`,
README, `tests/test_efficiency_sensor.py`, `tests/test_effy_exports.py`.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- From TASK-0025 (done 2026-10-29, filled by the Lead): `EffyCoordinator`:
  `battery_enabled`, `battery_hysteresis`, `battery_state`
  (`ValleyState | None`), `restore_battery(data) -> ValleyState`,
  `subscribe_battery(cb) -> unsubscribe`
  (`BatteryCallback = Callable[[ValleyState], None]`, called after every slot
  job and after a history rebuild). The dump to store is
  `calculation.valley_restore_data(state, coordinator.battery_hysteresis)`;
  `ValleyState.estimate` (`ValleyEstimate`: `value`, `counted_kwh`,
  `counted_spans`, `last_value`, `last_span_begin`, `last_span_end`),
  `ValleyState.last_slot`, `dropped_spans`, `last_drop`. The battery machine
  needs the charge and discharge counters, the SoC and the capacity entity only.
- the efficiency sensor base and restore pattern (→ TASK-0021) — **filled by the
  Lead before start**

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- `effy_exports.py`: fixed entity IDs and series symbols for battery and
  roundtrip efficiency; both values added to `EffySeries` and the pinned,
  copyability-tested contract. Schema version remains 1 (additive).
- `catalog.py`: `describe_efficiency` now covers all four efficiency series;
  both new descriptors are output/BMS, have no source or `function`, and use
  `[mean, min, max]`.
- `sensor.py`: `EffyBatteryEfficiencySensor` restores and subscribes to the
  valley machine, publishes span attributes, and does not depend on standby.
  `EffyRoundtripEfficiencySensor` restores both machines and publishes the
  product of the three displayed percentage values; its availability follows all
  three components, including standby availability for charge/discharge. Battery
  entity creation requires charge, discharge, SoC and capacity; roundtrip
  additionally requires grid import and grid export.
- `translations/en.json` and `translations/de.json`: names for both entities.
- README: measurement, availability, restore and the accepted roundtrip limit.
- Tests: `test_efficiency_sensor.py`, `test_catalog.py`, `test_effy_exports.py`,
  and `test_sensor_setup.py` cover the new contract and lifecycle.
- External dependencies added: none.
