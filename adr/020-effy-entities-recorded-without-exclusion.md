# ADR-020 – Effy Entities Are Recorded; the Exclusion Requirement of ADR-017 Is Retired

**Date:** 2026-10-16 **Status:** Accepted 2026-10-17 (human). Supersedes
ADR-017. Terms follow `GLOSSARY.md`.

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

## Amendment — 2026-10-30

**Reason:** The first live check (the human, 2026-10-30, after removing the
exclusion) found a failed compile, not a one-slot race: the recorder's compile
of the period 13:55 UTC failed at 15:02:33 local with
`UNIQUE constraint failed: statistics.metadata_id, statistics.start_ts` on the
hourly row of one Effy entity for 13:00 UTC (TASK-0035). The "residual race" of
the Consequences was believed to last seconds. It is not: a history
recalculation started **anywhere in an hour** inserted the hourly row of that
open hour, and the recorder's roll-up of the hour then failed. The same is true
for the 5-minute row of a slot that ended less than ten seconds ago. What the
recorder does on such a failure (read in `homeassistant/components/recorder` of
2026.10.4, the human's version, and reproduced in the strict recorder fake of
`tests/support.py`):

- The compile of a period inserts its 5-minute rows and, for the period starting
  at minute 55, the hour's roll-up, all with plain inserts and in one session. A
  duplicate fails the flush, and the **whole period is dropped for every entity
  of every integration**: no 5-minute row, no roll-up, no run marker.
- The failure is logged as a warning
  (`Blocked attempt to insert duplicated statistic rows`) and swallowed. Later
  periods compile normally (each tick is its own task). The catch-up at start
  continues from the newest run marker, so the dropped period is not compiled
  again at the next start; it is lost.
- `async_import_statistics` updates a row that exists (every column replaced)
  and inserts one that does not, on the recorder's queue, in order with the
  compile tasks. The catch-up is queued once, at recorder start, before any
  integration is set up.

**Decision:**

1. Effy inserts no row the recorder has yet to insert. A 5-minute slot is
   **settled** when it ended at least 30 s ago (`sensor_utils.RECORDER_SETTLE`;
   the recorder queues the compile at boundary + 10 s, the rest is margin for a
   late timer and an inexact clock); an hour is settled when its last slot is.
   `sensor_utils.settled_before(now)` is the start of the first slot that is
   not.
1. The history recalculation computes every slot (the live push of ADR-016 is
   unchanged) but writes only the 5-minute rows of settled slots and the hourly
   rows of settled hours. The rest, normally the open hour, is left to the slot
   job, which updates it after the recorder's compile and rewrites the hour
   after the roll-up (ADR-011 Amendment 2026-10-16), so the stored hourly row
   equals what a full recalculation writes. Hours and slots the recorder has
   finished with are written as before, whether or not the recorder ever
   compiled them natively, so older history is not lost. The timing record says
   how many hourly rows were left (`hourly rows left for the slot job N`), and
   what was left is neither a change nor skipped for `recalculated_from`.
1. A job the recorder's event started is behind the compile by construction (the
   event is fired from inside it, and Effy's imports queue after the event). A
   job the watchdog started has no such proof and works as of 30 s earlier, and
   an event job that waited for the lock serves only the slot of its own event.
1. Why the time rule is sound after a restart: the recorder's catch-up is one
   task queued at recorder start, ahead of anything Effy queues, and the queue
   is first in, first out. This is read from the source, not run on a live
   instance.

**Evaluated and not chosen:** an hourly row of a native statistic as proof of
the roll-up (a read per run, absent if no source had data in the hour, and an
Effy row from an older version looks the same); the recorder's run table
(`StatisticsRuns`, a private internal, exact; the choice if the time rule ever
proves insufficient, with the human's go); writing only hours older than a fixed
age (does not say when the recorder is done after a restart).

**Known limits (the first two are the recorder's, the third is the human's
decision of ADR-024 and is open):** a live schema migration at startup can let
an Effy import precede the catch-up; a catch-up that the recorder re-queues
after a database error goes to the end of the queue; and a history run decides
an hour is unchanged from its 5-minute rows (ADR-024 Amendment 2026-10-26, point
2), so an hour left by one run and not rewritten by the slot job (Home Assistant
stopped in the hour's last minutes) keeps the recorder's roll-up until a
5-minute row of it changes. Reading the stored hourly rows of the last two
settled hours would close that; awaiting the human.

**Decided by:** Lead Agent (not yet confirmed by the human)
