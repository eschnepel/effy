# ADR-020 – Effy Entities Are Recorded; the Exclusion Requirement of ADR-017 Is Retired

**Date:** 2026-10-16 **Status:** Draft — awaiting the human's confirmation.
Supersedes ADR-017. Terms follow `GLOSSARY.md`.

---

## Context

ADR-017 required users to exclude `sensor.effy_*` from the recorder, because the
recorder's own statistics compile inserted rows for the same slots Effy had
already written, and its plain insert failed with
`UNIQUE constraint failed: statistics_short_term.metadata_id, ...start_ts`. The
human decided (2026-10-13) that the exclusion is no longer needed once Effy's
trigger is synchronized with the recorder, and that the planned standby power
sensor (ADR-018) is born recorded.

What makes the race impossible is now built:

- **TASK-0006 / ADR-011 Amendment 2026-10-16:** the recorder's own 5-minute
  event starts the work, which waits until the slot is readable, and the history
  recalculation and the slot job never overlap.
- **TASK-0007a / ADR-016 and ADR-011 Amendments 2026-10-16:** Effy writes no
  statistic for the open slot, holds its first job after a restart until the
  first recorder event, and rewrites an hour's long-term row after the
  recorder's roll-up. Effy only ever *updates* a row the recorder inserted.

## Decision

1. The recorder exclusion of ADR-017 is no longer required. `sensor.effy_*`
   entities are recorded like any other sensor. The check, its three constants,
   the `recorder_not_excluded` repair issue and its strings (en, de) and the
   README requirement are removed. An upgrade needs no explicit cleanup: the
   issue was created without `is_persistent`, so Home Assistant does not keep it
   across the restart that installs this version.
1. The README tells users who added the exclusion for an earlier version that
   they may remove it, and states the outage behaviour of the decisions below.
1. A slot for which Effy finds no data keeps the recorder's native row for the
   first slot of that outage (it is built from the live state, which holds the
   last value Effy reported); the following slots stay empty. No delete path
   (human, 2026-10-16).

## What was verified, and what was not

Verified by reading the source of Home Assistant **2025.1.4** (the newest
version the sandbox's package mirror offers; the pinned version is 2026.3):

- The recorder's compile inserts each slot's row with a plain `session.add`,
  guarded only by the `StatisticsRuns` check, against a unique index on
  `(metadata_id, start_ts)`.
- `async_import_statistics` does update-or-insert, and the update replaces every
  column, so an Effy update of a native row leaves `min`/`max` NULL.
- The 5-minute event is fired inside the compile before the session commits, and
  the hourly roll-up for the hour just ended runs earlier in the same call, when
  the compiled slot starts at minute 55.
- The catch-up compile of missed slots is queued at recorder start and re-queues
  itself; imports run on the recorder's queue after the compile task that is
  running when they are queued.

**Not verified:** Home Assistant 2026.3 itself, and any live instance. Shady,
the sibling project whose TASK-0041 this follows, writes no statistics through
the recorder's import and has no exclusion, so nothing it validated carries over
to this ADR; the repository holds no record of what was validated there. The
first live check is the human's: remove the exclusion from `configuration.yaml`,
restart, and watch the Home Assistant log across a few slot boundaries and one
full hour for `UNIQUE constraint failed: statistics_short_term`.

## Consequences

- `sensor.effy_*` entities have a state history and native statistics; the
  standby sensor of ADR-018 can be recorded (TASK-0005 Stage 2).
- Residual race, accepted for now and not part of the decisions above: a history
  recalculation started by the button in the seconds between a slot boundary and
  the recorder's compile (boundary + 10 s) still writes the last completed slot
  before the recorder has inserted it. A job whose probe ran out of retries can
  do the same. Either would make the recorder's insert for that one slot fail.
- ADR-017's check and repair are gone; the recorder-exclusion section of the
  README is replaced by a short "Recorder" section.
