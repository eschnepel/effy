# Task: Retire the Recorder Exclusion Requirement (Deferred — ADR-017)

- **Status:** todo
- **Related ADRs:** [ADR-003, ADR-017, ADR-011]
- **Dependencies:** [TASK-0006-recorder-synchronized-slot-trigger]

## Goal

**DEFERRED — do not start until TASK-0006 is `done` and the human releases it.**
The human states (2026-10-13) that once the slot trigger is
recorder-synchronized the recorder exclusion of ADR-017 is no longer needed: it
was introduced only to mitigate the race with the recorder writer. Retire it, so
`sensor.effy_*` entities are recorded like any other. The standby power sensor
(ADR-018) is born recorded and depends on this (TASK-0005 Stage 2).

## Acceptance Criteria

- Given the README, then it no longer asks users to add
  `recorder: exclude: entity_globs: - sensor.effy_*`.
- Given `__init__.py` and `const.py`, then `_async_check_recorder_exclusion`,
  `RECORDER_EXCLUSION_PROBE_ENTITY_ID`, `RECORDER_EXCLUDE_ENTITY_GLOB` and the
  repair issue key are removed, and the `recorder_not_excluded` strings are
  removed from `translations/en.json` and `de.json`.
- Given an upgrade from a version that raised the `recorder_not_excluded`
  repair, then it is gone after a restart: the issue is created without
  `is_persistent` and is therefore not stored across restarts (verify against
  the pinned Home Assistant; no explicit delete unless that fails).
- Given ADR-017, then its Status becomes Superseded, with the superseding ADR
  (Draft until confirmed) stating that the exclusion is no longer required
  because Effy writes after the recorder's own compile; `tasks/adr-summary.md`
  and `GLOSSARY.md` are updated.
- Given the entities are now recorded, then the Lead Agent states what the human
  validated for Shady and confirms it covers Effy: Effy's imported short-term
  rows are written after the native compile of the same slot, and the native
  hourly roll-up reads them. Anything that differs is raised before the
  exclusion is removed.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/__init__.py`, `const.py`, `translations/en.json`,
  `translations/de.json`, `README.md`,
  `adr/017-recorder-exclusion-for-effy-entities.md`, `tasks/adr-summary.md`,
  `GLOSSARY.md`.

## Definition of Done

- Tests green; `mypy`, `ruff check`, `ruff format --check`, `mdformat --check`
  clean.
- ADR-017 superseded and consistent with what was built.
- No new external dependency; `Delivered Artifacts` completed.

## Consumed Interfaces

- `_async_check_recorder_exclusion`, `RECORDER_EXCLUSION_PROBE_ENTITY_ID`,
  `RECORDER_EXCLUDE_ENTITY_GLOB`, `ISSUE_RECORDER_NOT_EXCLUDED` from
  `custom_components/effy/__init__.py` and `const.py`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->
