# Task: The battery system's four counters configured by function

- **Status:** todo
- **Capability:** CAP-7
- **Related ADRs:** \[ADR-023 (Draft) decision 1, ADR-019 (Amendment
  2026-10-23), ADR-018\]
- **Dependencies:** []
- **Gate:** Blocked until Draft ADR-023 is **Accepted** by the human (Gate 2);
  until then no code.

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

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

## Stage 0 (before any code): verify against Home Assistant 2026.3

Home Assistant is not installed in the sandbox. Read its source (`git clone` of
`home-assistant/core` at the 2026.3 tag; `github.com` is reachable) and settle
ADR-023 Open decision 3: (a) an entry in the setup-error state still offers the
options flow; (b) saving options on it reloads the entry — via
`OptionsFlowWithReload` or Effy's own update listener, whichever the source
shows works when setup failed before the listener was added. Record the finding
in this file; if neither holds, stop and report (propose a draft ADR amendment).
