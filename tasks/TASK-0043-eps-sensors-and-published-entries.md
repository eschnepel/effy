# Task: The two EPS Effy sensors exist and are published with role and function `eps`

- **Status:** done
- **Capability:** CAP-12
- **Related ADRs:** \[ADR-026 decision 6, ADR-021 and Amendment 2026-11-06,
  ADR-023 decision 11, ADR-013 decision 2\]
- **Dependencies:** [TASK-0041-eps-split-in-slot-loop]
- **Gate:** Gate 2 approved by the human (2026-11-06); ADR-026 accepted.

## Goal

The EPS sensor's two Effy sensors (`sensor.effy_<slug>`, the effective value of
the input half, and `sensor.effy_<slug>_smoothed`, the signed series) are
created by the code that builds them for a power-family input, restored and
pushed like the others, and appear in the published `sensors` list with the new
additive values.

## Acceptance Criteria

- Given an EPS sensor, then `EffySensor` and `EffySmoothedSensor` exist for it,
  also when its source has no state at setup (the late-binding path), with the
  naming of the other sensors; the smoothed one is device class power, in the
  source's unit, and its restore and live push work as for any smoothed sensor.
- Given the published `sensors` list, then the EPS sensor has two entries:
  `series` `effective`, `direction` `input`, and `series` `smoothed`,
  `direction` `both`; both have `role` `eps`, `function` `eps`,
  `source_entity_id` the EPS sensor and the aggregations of the other source
  series.
- Given `effy_exports.py`, then it defines `EFFY_ROLE_EPS` (`"eps"`),
  `EFFY_FUNCTION_EPS` (`"eps"`) and `EFFY_DIRECTION_BOTH` (`"both"`), the
  literals `EffyRole`, `EffyFunction`, `EffyDirection` carry them, the sign
  convention of the signed series is stated in the file's comments, and
  `EFFY_PUBLISHED_SCHEMA_VERSION` stays 1; the file keeps its rules (standard
  library only, no import from Effy, prefixed symbols).
- Given the test that pins every contract value and fails on a contract literal
  spelled out in another module, then it is extended and green.
- Given the coordinator, then it exposes `eps_id` for the catalog like the four
  counter IDs.
- Given the README section "For other integrations", then the new role, function
  and direction and the sign convention are described.
- Given an entry without an EPS sensor, then the published list is unchanged.

## Estimated File / Module Footprint (hint, not a commitment)

`effy_exports.py`, `catalog.py` (`describe_source_series` gains an EPS branch),
`coordinator.py` (`eps_id`), `sensor.py` (`_source_descriptor`, verification of
the entity building), `README.md`, `tests/test_effy_exports.py`,
`tests/test_catalog.py`, `tests/test_published_sensors.py`,
`tests/test_sensor_setup.py`.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- Pre-existing, `catalog.py`:
  `describe_source_series(entity_id, series, source_entity_id, *, pv_input_ids, grid_import_id, discharge_id, charge_id, grid_export_id)`,
  `published_attributes`.
- Pre-existing, `sensor.py`: `_source_descriptor`, `_build_derived_entities`,
  `_add_late_derived_entities`, `EffySensor`, `EffySmoothedSensor`.
- Pre-existing, `effy_exports.py`: the `EFFY_ROLE_*`, `EFFY_FUNCTION_*`,
  `EFFY_DIRECTION_*` symbols and the `Effy*` literals.
- From TASK-0041-eps-split-in-slot-loop: `inputs.all_input_ids(options)` ends
  with the EPS sensor (`inputs.eps_id`), so `sensor.async_setup_entry` builds
  `EffySensor` and, through `_build_derived_entities` (late binding included),
  `EffySmoothedSensor` for it; `history._effy_entity_id(eps)` is the effective
  series (the input half, >= 0), `history._effy_smoothed_entity_id(eps)` the
  signed one.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- `custom_components/effy/effy_exports.py` → new `EFFY_DIRECTION_BOTH = "both"`,
  `EFFY_ROLE_EPS = "eps"`, `EFFY_FUNCTION_EPS = "eps"`; the literals
  `EffyDirection` (`input`, `output`, `both`), `EffyRole` (`pv`, `bms`, `eps`)
  and `EffyFunction` (`grid_import`, `discharge`, `charge`, `grid_export`,
  `eps`) carry them; the sign convention of `both` and the meaning of the two
  `eps` series are in the file's comments. `EFFY_PUBLISHED_SCHEMA_VERSION` stays
  1; the file is still standard library only and imports nothing from Effy.
- `custom_components/effy/catalog.py` →
  `describe_source_series(..., grid_export_id, eps_id: str | None = None)`: a
  source equal to `eps_id` has role and function `eps`, direction `input` for
  series `effective` and `both` for every other series; everything else is
  unchanged; an unconfigured source is still a `ValueError`.
- `custom_components/effy/coordinator.py` → property `eps_id -> str | None`
  (`self._eps_id`, from `inputs.eps_id(entry.options)`).
- `custom_components/effy/sensor.py` → `_source_descriptor` passes
  `eps_id=coordinator.eps_id`. Without it every EPS entity would have raised
  `ValueError` at setup after TASK-0041. No other code change: the two entities
  come from the existing code for a power-family input.
- `README.md` → "For other integrations": EPS example entries, the three new
  values in the key table, a paragraph on the EPS entries.
- Tests: `tests/test_effy_exports.py` (the pin table, the literal types and the
  second-copy list extended with `eps` and `both`, one test that the new values
  are additive); `tests/test_catalog.py` (`TestDescribeEps`, the `_describe`
  helper gets `eps`); `tests/test_published_sensors.py`
  (`TestEpsSensorsArePublished`; the `_Coordinator` double gets `eps_id`);
  `tests/test_sensor_setup.py` (`TestEpsSensorSetup`; `_SetupCoordinator` gets
  `eps_id`); `tests/test_coordinator_slot.py`
  (`test_exposes_the_eps_sensor_and_it_is_the_last_input_only`).
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged).
- Verification: 1742 tests pass (1725 before, 17 new); `ruff check` and
  `mypy --strict` clean; `ruff format --check` flags only `tests/test_cache.py`
  (already flagged before CAP-12, not touched).
