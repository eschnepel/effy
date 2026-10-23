# Task: Retire the Recorder Exclusion Requirement (ADR-017)

- **Status:** done
- **Related ADRs:** [ADR-003, ADR-017, ADR-011]
- **Dependencies:** [TASK-0007a-effy-writes-after-the-recorder]

## Goal

The human states (2026-10-13) that once the slot trigger is
recorder-synchronized the recorder exclusion of ADR-017 is no longer needed: it
was introduced only to mitigate the race with the recorder's own compile. With
TASK-0006 (trigger) and TASK-0007a (Effy never inserts a row the recorder will
insert, and only updates after its compile) that holds. Retire the requirement,
so `sensor.effy_*` entities are recorded like any other. The standby power
sensor (ADR-018) is born recorded and depends on this (TASK-0005 Stage 2).

## Acceptance Criteria

- Given the README, then it no longer asks users to add
  `recorder: exclude: entity_globs: - sensor.effy_*`; it states that an existing
  exclusion may be removed, and that the first slot of an outage keeps the
  carried-forward value.
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
  because Effy never inserts a row the recorder will insert and updates only
  after the recorder's compile (TASK-0006, TASK-0007a); `tasks/adr-summary.md`
  and `GLOSSARY.md` are updated.
- Given the entities are now recorded, then the Lead Agent states what the human
  validated for Shady and confirms it covers Effy, and the verification against
  the pinned Home Assistant (event constant, compile order, plain insert,
  `_update_statistics`) is recorded in the superseding ADR. Anything that
  differs is raised before the exclusion is removed.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/__init__.py`, `const.py`, `translations/en.json`,
  `translations/de.json`, `README.md`,
  `adr/017-recorder-exclusion-for-effy-entities.md`, a new superseding ADR,
  `tasks/adr-summary.md`, `GLOSSARY.md`.

## Definition of Done

- Tests green; `mypy`, `ruff check`, `ruff format --check`, `mdformat --check`
  clean.
- ADR-017 superseded and consistent with what was built.
- No new external dependency; `Delivered Artifacts` completed.

## Consumed Interfaces

- `_async_check_recorder_exclusion`, `RECORDER_EXCLUSION_PROBE_ENTITY_ID`,
  `RECORDER_EXCLUDE_ENTITY_GLOB`, `ISSUE_RECORDER_NOT_EXCLUDED` from
  `custom_components/effy/__init__.py` and `const.py`.
- From TASK-0007a (→ task: TASK-0007a-effy-writes-after-the-recorder, `done`):
  `calculation.drop_open_slot`, `history._drop_open_slot`,
  `history.async_rewrite_hourly`,
  `history._write_recorder_statistics(..., include_short_term)`,
  `EffyCoordinator._setup_at`, `_async_rewrite_hour`; ADR-016 and ADR-011
  Amendments 2026-10-16.

## Delivered Artifacts

- `custom_components/effy/__init__.py` → `_async_check_recorder_exclusion`
  removed with its call, and the imports of `get_recorder`, `issue_registry` and
  the three constants.
- `custom_components/effy/const.py` → `RECORDER_EXCLUDE_ENTITY_GLOB`,
  `RECORDER_EXCLUSION_PROBE_ENTITY_ID`, `ISSUE_RECORDER_NOT_EXCLUDED` removed.
- `translations/en.json`, `de.json` → the `issues` block
  (`recorder_not_excluded`) removed. No explicit issue delete on upgrade: the
  issue was created without `is_persistent`, so it is not stored across the
  restart that installs this version (taken from Home Assistant's issue-registry
  semantics, not run here).
- `README.md` → the "Required: exclude Effy's own entities" section replaced by
  "Recorder": no exclusion needed, an old one may be removed, the outage
  behaviour, and the Home Assistant 2026.3 minimum.
- `adr/020-effy-entities-recorded-without-exclusion.md` (new, **Draft**);
  `adr/017-recorder-exclusion-for-effy-entities.md` → Superseded by ADR-020
  (status line and Amendment 2026-10-16); `tasks/adr-summary.md`.
- Tests: `tests/test_migration.py` → `TestRecorderExclusionRetired` (setup does
  not probe the recorder or touch the issue registry; the check, constants and
  strings are gone; the README no longer demands an exclusion), written first
  and failing against the pre-change code.
- Verification record (AC 5): in ADR-020. Shady has no statistics import and no
  exclusion, so nothing it validated carries over; the repository holds no
  record of what was validated there. Home Assistant 2026.3 and a live instance
  were not available: **the first live check is the human's** (see ADR-020).
- External dependencies added: none; `tasks/DEPENDENCIES.md` unchanged.
