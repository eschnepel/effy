# Task: The battery system's four counters configured by function

- **Status:** done — 2026-10-27, reviewer PASS (inline)
- **Capability:** CAP-7
- **Related ADRs:** \[ADR-023 (Draft) decision 1, ADR-019 (Amendment
  2026-10-23), ADR-018\]
- **Dependencies:** []
- **Gate:** ADR-023 accepted 2026-10-27 (the human's instruction to implement
  tasks 17 to 27).

## Goal

Replace the BMS-input list and the output list by four optional single sensor
pickers (grid import, discharge, charge, grid export), so that every later task
knows which counter is which. An entry with the old keys refuses to set up until
the human assigns the pickers. Nothing that Effy calculates changes.

## Acceptance Criteria

- Given the options flow, when the human assigns grid import `a`, discharge `b`,
  charge `c`, grid export `d` and PV `[p]`, then the saved options hold
  `grid_import_sensor`, `discharge_sensor`, `charge_sensor`,
  `grid_export_sensor` and `pv_input_sensors`, and neither `bms_input_sensors`
  nor `output_sensors`.
- Given those options, then `bms_input_ids == [a, b]` (grid import first; unset
  roles skipped), `output_ids == [c, d]` (charge first),
  `all_input_ids == [p, a, b]`, and one accessor per role returns its entity ID
  or `None`.
- Given the form, then validation rejects: an entity in two roles (PV list
  included), no output at all, no input at all, and a charge or grid export
  sensor without a device class (the existing error keys are kept where they
  exist); each picker alone is optional.
- Given an entry whose options hold the old keys and none of the new, when it is
  set up, then `async_setup_entry` raises `ConfigEntryError` with a translated
  message, creates no entity, schedules no recalculation and writes no
  statistics.
- Given such an entry, when the human saves valid pickers in the options flow,
  then the old keys are gone, the entry reloads and sets up; no `VERSION` bump,
  no `async_migrate_entry`, no repair issue.
- Given the old `input_sensors` key (ADR-019 decision 4), then its silent
  migration still runs as before, and the entry then refuses setup like
  criterion 4.
- Given the CAP-1 equivalence harness with the same sources assigned by role,
  then every effective and derived series equals the result before this task;
  the standby sensor exists exactly when at least one BMS picker is set.
- Given the translations, then the pickers, their descriptions, the new refusal
  message and the new error texts exist in `en.json` and `de.json`.

## Estimated File / Module Footprint (hint, not a commitment)

`const.py` (four keys, old keys read nowhere), `inputs.py` (role accessors),
`config_flow.py`, `__init__.py` (refusal; reload on save), `coordinator.py` (id
accessors only), `translations/en.json`, `translations/de.json`, README,
`tests/test_inputs.py`, config-flow and setup tests, equivalence harness.

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

- `inputs.pv_input_ids`, `bms_input_ids`, `all_input_ids`,
  `migrate_legacy_input_options` from `custom_components/effy/inputs.py` (→
  task: TASK-0008-split-input-sensors-by-role)
- `EffyConfigFlow`, `EffyOptionsFlow`, `_build_schema`, `_validate_user_input`,
  `_normalised` from `custom_components/effy/config_flow.py` (→ TASK-0008)
- `async_setup_entry`, `_async_reload_entry`, `_async_migrate_input_options`
  from `custom_components/effy/__init__.py` (→ TASK-0008)
- `EffyCoordinator.pv_input_ids`, `bms_input_ids`, `standby_enabled` from
  `custom_components/effy/coordinator.py` (→ TASK-0005-standby-power-sensor,
  TASK-0008)

## Delivered Artifacts

- `const.py`: `CONF_GRID_IMPORT_SENSOR` (`grid_import_sensor`),
  `CONF_DISCHARGE_SENSOR`, `CONF_CHARGE_SENSOR`, `CONF_GRID_EXPORT_SENSOR`; the
  constants `CONF_BMS_INPUT_SENSORS` and `CONF_OUTPUT_SENSORS` are removed.
- `inputs.py`: `grid_import_id`, `discharge_id`, `charge_id`, `grid_export_id`
  (`-> str | None`), `bms_input_ids` (grid import, discharge), `output_ids`
  (charge, grid export), `all_input_ids` (PV, grid import, discharge),
  `legacy_inputs_were_moved` / `migrate_legacy_input_options` (the PV list or
  any of the four keys counts as a new key; no `bms_input_sensors` is written).
- `config_flow.py`:
  `_build_schema(pv_input_sensors, grid_import_sensor, discharge_sensor, charge_sensor, grid_export_sensor, max_history_days)`;
  `_validate_user_input` (error keys `at_least_one_input`, `sensor_in_two_roles`
  (on the later field), `at_least_one_output`, `output_needs_device_class`);
  `_normalised`; `EffyOptionsFlow(config_entries.OptionsFlowWithReload)`.
- `__init__.py`: `async_setup_entry` raises
  `ConfigEntryError(translation_domain ="effy", translation_key="outputs_not_assigned")`
  when `output_ids(options)` is empty, before anything is created; no update
  listener; `_async_reload_entry` is removed.
- `coordinator.py`: properties `grid_import_id`, `discharge_id`, `charge_id`,
  `grid_export_id`; `_output_ids` from `output_ids`. `history.py`, `sensor.py`:
  `output_ids` (imported as `configured_output_ids`) instead of the old key.
- `translations/en.json`, `de.json`: the four pickers, the new error texts,
  `exceptions.outputs_not_assigned`; README (Configuration).
- `tests/support.py`: `role_options(pv, bms, outputs, **extra)` for tests that
  describe a system as lists (more than two BMS inputs or outputs raise).
- External dependencies added: none.

## Review

**PASS** (inline; 672 tests, ruff, mypy on source and tests). The acceptance
criteria map to: `tests/test_config_flow.py` (criteria 1, 3, 8),
`tests/test_inputs.py` (2, 6), `tests/test_migration.py` and
`tests/test_entry_lifecycle.py` (4, 5, 6), the unchanged equivalence harness
through `role_options` (7), `tests/test_coordinator_slot.py` (accessors, standby
by role).

Not verifiable in the sandbox: that the frontend shows "Configure" for an entry
in the setup-error state, and the reload of a failed entry in a real Home
Assistant (the source says it works; see Stage 0). The first start of the
upgraded integration on the Pi is the test: Effy will show a setup error until
the four counters are assigned in its options.

## Stage 0 (before any code): verify against Home Assistant 2026.3

Home Assistant is not installed in the sandbox. Read its source (`git clone` of
`home-assistant/core` at the 2026.3 tag; `github.com` is reachable) and settle
ADR-023 Open decision 3: (a) an entry in the setup-error state still offers the
options flow; (b) saving options on it reloads the entry — via
`OptionsFlowWithReload` or Effy's own update listener, whichever the source
shows works when setup failed before the listener was added. Record the finding
in this file; if neither holds, stop and report (propose a draft ADR amendment).

### Finding (2026-10-27, Home Assistant 2026.10.4 and 2026.3.0)

- (a) `OptionsFlowManager.async_create_flow` has no state check and
  `ConfigEntry.supports_options` depends only on the handler (it overrides
  `async_get_options_flow`), so an entry in `setup_error` (a recoverable state)
  offers the options flow. The frontend was not read.
- (b) A listener of Effy's own cannot reload a failed entry: after a failed
  setup Home Assistant runs the entry's unload callbacks (`finally:` of the
  setup, `_async_process_on_unload`), which removes it. `OptionsFlowWithReload`
  (present in 2026.3.0 and 2026.10.4) schedules the reload itself, and
  `ConfigEntries.async_reload` unloads a `setup_error` entry and sets it up
  again. It raises `ValueError` if the entry has update listeners, so Effy
  registers none. Decision: `EffyOptionsFlow` extends `OptionsFlowWithReload`;
  the update listener and `_async_reload_entry` are gone. ADR-023 Open decision
  3 is settled.
