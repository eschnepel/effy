# Task: Add three Effective PV Sum sensors

- **Status:** todo
- **Capability:** none yet (to be assigned by the Lead)
- **Related ADRs:** [ADR-019, ADR-007, ADR-013]
- **Dependencies:** none
- **Gate:** Gate 2 pending

## Goal

Effy publishes the sum of the effective values of all PV input sensors as three
sensors: the instantaneous power, the energy of the current day, and the
lifetime energy.

## Acceptance Criteria

- Given an entry with at least one PV input sensor, then three sensors exist,
  named and unique-ID'd like the other Effy sensors:
  - `Effective PV Sum now`: power, the sum of the effective values of all PV
    inputs in the current slot (state class measurement).
  - `Effective PV Sum today (integral)`: energy, the integral of the sum since
    local midnight, resetting at the day change (state class total, with
    `last_reset` at local midnight).
  - `Effective PV Sum total (integral)`: energy, the integral of the sum since
    the first processed slot, never reset (state class total increasing).
- Given PV inputs in mixed units (W, kW, Wh, kWh), then the sum is normalised as
  by ADR-002 and ADR-008, and the sensors use W and kWh.
- Given a PV input that is unavailable or a slot without data, then it adds 0 to
  the sum; the sensors do not become unavailable while another PV input is
  valid.
- Given the recalculate-history button, then the three sensors' history is
  rebuilt with it, the total starting at the first slot of the rebuilt history.
- Given a history recalculation or a restart, then both integral sensors are
  restored and rebuilt consistently with the per-input effective sensors (the
  sum of the per-input integrals equals the integral of the sum).
- Given an entry without a PV input sensor, then the three sensors are not
  created.
- Given the published `sensors` list, then the three sensors appear with the
  `role` and `function` of a PV sum (to be fixed in the implementation; if a new
  contract literal is needed it goes into `effy_exports.py` and
  `EFFY_PUBLISHED_SCHEMA_VERSION` stays 1).
- Given the README and translations (en, de), then the sensors are described.

## Decisions (human, 2026-11-06)

- "Today" is the Home Assistant local day.
- The total starts at the first slot of the rebuilt history.
- The three sensors are recalculated by the recalculate-history button, like the
  other Effy sensors; the acceptance criterion on recalculation covers it.

## Estimated File / Module Footprint (hint, not a commitment)

`sensor.py`, `coordinator.py`, `calculation.py` or `history.py` (the sum and
integrals), `catalog.py`, `effy_exports.py`, `const.py`, `README.md`,
`translations/en.json`, `translations/de.json`, and tests for each.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de) · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies; `tasks/DEPENDENCIES.md` unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. -->

- _(open)_

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->

- _(open)_
