# Task: The battery efficiency and roundtrip efficiency sensors

- **Status:** todo
- **Capability:** CAP-9
- **Related ADRs:** \[ADR-023 (Draft) decisions 2 and 8, ADR-021 (Amendments
  2026-10-23)\]
- **Dependencies:** \[TASK-0021-charge-and-discharge-efficiency-sensors,
  TASK-0025-battery-slot-feed-and-restore\]
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

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

- the efficiency sensor base and restore pattern (→ TASK-0021) · the battery
  feed's restore/subscribe (→ TASK-0025) — **filled by the Lead before start**

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->
