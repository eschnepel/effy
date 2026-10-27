# ADR-024 – The Write Phase of the History Recalculation

**Date:** 2026-10-25 **Status:** Accepted 2026-10-25 (human). The human chose
option C and widened it (Decisions 1 to 5), and confirmed the details with Q1 =
A (exact equality) and Q2 = A (live states for every computed entity). Terms
follow `GLOSSARY.md`.

---

## Context

ADR-022's measurement on the Raspberry Pi 5 (28 history days, 2026-10-25): of
106.63 s, the write phase took 104.34 s (97.9 %), fetch 1.82 s, compute 0.41 s.
A run with about 60 days took 228 s for 236,112 rows. Both give about 0.96 ms
per short-term row, so the time is linear in the rows written, and a fixed cost
per recalculation is negligible. The hourly rows add to that figure; they are
not counted separately.

**Why a row costs a millisecond.** Effy hands the recorder its rows through
`async_import_statistics` (ADR-003/004), a public call that queues an
`ImportStatisticsTask`. For every row that task runs one `SELECT` for an
existing row at the same start time and then one `UPDATE` or `INSERT` as its own
statement, whether or not the value changed (Home Assistant core, `dev` branch,
`_import_statistics_with_session`; not checked against the Pi's release). Effy
calls it twice per series (short-term and hourly), 14 series, so 28 tasks,
queued at once; `_write_recorder_statistics` then awaits
`async_block_till_done`.

**What is not known.** Whether the recorder spends these 104 s computing or
waiting on storage (the detail block of TASK-0010 patch 1 shows CPU against wall
seconds for the write phase); whether the other work the recorder has to do
meanwhile (the states of the whole instance, queued behind the 28 tasks) is
noticeably delayed; and how many of the rewritten rows differ from what is
already stored.

**Measured on the Pi, 2026-10-26** (Home Assistant 2026.10.4, Python 3.14,
SQLite, 28 days, two runs): the write phase took 106.10 s and 106.43 s for
109,242 rows, and the process used **106.14 s and 106.47 s of CPU in it**. The
recorder is therefore CPU-bound (one `SELECT` and one `UPDATE` per row through
the ORM), not waiting for storage: faster storage, bigger or smaller batches
would not shorten it, and only writing fewer rows does. Preparing and scheduling
the imports on the event loop took 0.1 to 0.46 s per series group; the longest
stretch the event loop was held was 0.46 s. Reading one series of about 8,000
stored rows took 0.09 to 0.13 s (about 1.6 s for 14 series), so the read the
decision relies on is cheap. That makes option D pointless for speed and leaves
C as the one lever.

## Options considered

| Option | Effect | Cost / risk |
| -- | -- | -- |
| A. Leave it | none; about 1 ms per short-term row, once per button press | a minute and a half to four minutes of recorder time per run |
| B. Say what is happening | an INFO line per finished series during the write, and the README says the button takes about 1 ms per row | no speed-up; the wait stops being mysterious |
| C. Write only rows that differ | one bulk read of the stored rows replaces one `SELECT` per row, and unchanged rows are skipped; near zero for a repeated run with unchanged settings | no gain when the settings changed (every value is new, which is the usual reason to press the button); an exact-equality rule for floats; a read of what is stored |
| D. Smaller batches | the recorder's other work is interleaved with the import instead of waiting behind it | no speed-up; more tasks; only worth it if the delay is shown to matter |
| E. Fewer rows | skip a series or a range | changes what Effy publishes (ADR-003/012/021): out of scope here |

## Decision

1. **Write only rows that differ, in both recalculations.** The history
   recalculation and the slot job (and the hour's long-term rewrite) read the
   stored rows first and hand the recorder only the rows that are new or
   different. The human's reason: the read is not the cost, the comparison is
   cheap, and the same rule serves the 5-minute path (about 230 rows a cycle
   that mostly equal what is stored).
1. **What "unchanged" means.** A stored row is unchanged only if it is already
   exactly what the writer would write: a row with the same `start` whose `mean`
   and `state` equal the new values (exact float equality) and whose `min`,
   `max`, `sum` and `last_reset` are empty (the writer clears them, TASK-0007a;
   a native row that still carries them is rewritten). For an hourly row the
   expected values are the ones the writer derives from the hour's slots. A
   series whose stored unit differs from the new one is written in full (the
   import is what updates the metadata).
1. **Fail safe.** If the stored rows cannot be read, everything is written, as
   before, and a warning says so. A read never makes a recalculation fail.
1. **`recalculated_from` is the earliest change.** The sensor reports the
   earliest slot (or hour, for a long-term row) whose stored value actually
   changed, not the start of the range that was recomputed (ADR-012 Amendment
   2026-10-25). A run that changes nothing reports `None`, which leaves the
   sensor alone (the rule of ADR-012 since 2026-10-06).
1. **What does not change.** `touched_entity_ids` and `last_values` stay "every
   entity computed this run", so the live states of ADR-016 are still pushed for
   all of them; ADR-004's end state is the same (after a run the stored rows
   equal the computed ones); the values, the range and the recompute are
   untouched.
1. **Not planned:** progress lines per series (option B), smaller batches
   (option D), numpy and an executor job (ADR-022's outcome).

**What to expect, to be measured and not promised.** A repeated recalculation
with unchanged settings writes (almost) nothing; the cost becomes one read of
the stored rows (the detail block's jobs labelled `stored rows` show it). After
a change of settings every value is new, and the run costs what it did, about 1
ms per row.

**The race that is not one.** The read may miss a row the recorder has not
committed yet (the slot's native row, TASK-0006): a missing row counts as
"changed" and is written, as today. The read cannot make a row differ from what
is stored, so the failure direction is an extra write, never a missed one.

## Answered points

**Q1 — Exact float equality, or a tolerance?** *Decided A:* exact. A noise
difference of the last bit counts as a change and is written, costing a write
and moving `recalculated_from` earlier than it needs to. *B:* equal within a
relative 1e-9: no spurious change, but a stored value may stay a hair off the
computed one.

**Q2 — Live states stay pushed for every computed entity (Decision 5).**
*Decided A:* yes, so an entity does not stay `unknown` after a restart because
its rows happened to equal the stored ones. *B:* push only entities with a
changed row.

## Amendment — 2026-10-26

**Reason:** Decision 2 compared `mean` and `state` of the stored rows. Home
Assistant 2026.10.4 silently discards the `state` and `sum` types in
`statistics_during_period` when the series' metadata has `has_sum` false (the
function that extracts the metadata ids in `recorder/statistics.py`, verified at
tag `2026.10.4`), and Effy's metadata always has. The first Pi record showed it:
`mean differs 0`, `state stored None` in every row, `rows unchanged 0`. The
comparison could never succeed through this public call.

**Decision:** (option A, chosen by the human)

1. A stored row is unchanged if its `mean` equals the new value exactly and its
   `min`, `max` and `last_reset` are empty. `state` and `sum` are not compared:
   the API does not return them, and `state` is the row's own value by the
   writer's rule (5-minute: the mean; hourly: the last slot, which is in the
   mean).
1. A history run does not read the hourly rows. An hour is rewritten only if a
   change was written in it, i.e. at least one of its 5-minute rows is written;
   the hourly row itself is not compared (the human: "rewrite every hour
   affected by a change, no compare for the hour itself"). An hour in which no
   5-minute row was written is not touched. Exception (the Lead Agent's,
   awaiting the human): a slot older than the short-term cutoff has no stored
   5-minute row, so no change can be detected there and its hour is rewritten.
1. The slot job's hour rewrite (`async_rewrite_hourly`) is unchanged: it has no
   5-minute comparison to go by, so it still compares the stored hourly rows,
   now by the rule of point 1.
1. `recalculated_from` follows: a written hourly row counts from the first slot
   of its hour, so one changed slot reports the start of its hour at the
   earliest (never later than the slot); a run that changes nothing still
   reports `None`.

**Known limit (inferred, not verified):** a native recorder row with the right
`mean`, a null `state` and no `min`/`max` would count as unchanged. Native rows
of a `has_mean` series carry `min` and `max`, which the check catches.

**Decided by:** human; the exception in point 2 is the Lead Agent's, not yet
confirmed by the human
