# ADR-025 – A Cache for What Effy Reads From the Recorder

**Date:** 2026-10-31 **Status:** Accepted 2026-10-31 (human: "Proceed" on the
draft, read by the Lead Agent as confirmation with the recommendation of every
open point; see "Answers to the open points" at the end, where each can be
changed). Drafted by the Lead Agent in TASK-0036 Stage 1; the storage interface
is the human's design. Terms follow `GLOSSARY.md`.

---

## Context

Every recalculation reads its source data from the recorder again: the slot job
every five minutes for a window of about twenty minutes (a day after a counter
jump, ADR-014), the history recalculation for the whole configured range, and
the standby, efficiency and battery machines for the slots they have not seen.
ADR-015 keeps one answer in memory (the last valid reading of an energy-family
sensor) and nothing else is kept.

The human asked (2026-10-31) for a cache like Shady's, and for a debug export
that writes down what a recalculation read (TASK-0037). Shady's `cache.py`
(ADR-007, ADR-007a, ADR-008, TASK-0042, TASK-0043) is a pure module that owns
what was read from the recorder, is handed the function that reads it, and is
tested without mocks. TASK-0036's evaluation of Effy's reads is in that task's
Findings. The facts this ADR rests on:

- **What a cache buys in time is small.** On the Raspberry Pi 5 (2026-10-27) a
  repeated history run takes 3.92 s, of which the fetch phase is 2.96 s, and
  1.37 s of that are the reads of Effy's *own* stored rows, which must stay
  fresh (ADR-024). The reads of source data (units, four raw-state reads, one
  power-family read) are about 1.6 s. A slot job cycle takes 0.19 s in total.
  The case for the cache is **replay and equal results**, not speed (open point
  1).
- **A raw-state read depends on where it starts.** In Home Assistant 2026.10.4
  (`recorder/history/__init__.py`, `_state_changed_during_period_stmt`,
  `models/state.py`) `state_changes_during_period(start, end, entity)` returns
  (1) the latest state whose `last_updated` is **strictly before** `start`,
  stamped **`start`**, if the recorder holds a state that old, then (2) every
  state change (`last_changed == last_updated`) with
  `start < last_updated < end`, **both ends exclusive**. Two reads cannot be
  concatenated, and a state written at exactly `start` is in neither part of a
  read.
- **The freshest data is not there yet.** The recorder compiles a slot about 10
  s after its end (TASK-0035) and writes states through its own queue (the
  commit interval was not read in this evaluation); a read at the moment of a
  slot job can miss the newest of both. Shady met this as its TASK-0037-patch-5
  (a `None` stored as an answer and never read again, for the rest of the
  process's life).
- **Shady's cache fetches a sensor's whole window the first time it is asked for
  anything** (`_validate_range`, "no valid data at all"). That is right for
  training and wrong here: TASK-0037 relies on the cache holding exactly what
  one recalculation read.
- The reads are `await`ed on the event loop through `phases.recorder_job`
  (`history.py`); Shady's cache is called from inside an executor job, which is
  why it needs a lock (TASK-0042).

## Decision (proposed)

1. **A storage interface, and the cache implements it** (the human's design,
   2026-10-31). `custom_components/effy/storage.py` (pure) defines `Storage`, a
   `Protocol` of the async reads `history.py` makes:
   `raw_states(entity, start, end)`,
   `last_valid_state_before(entity, before, max_days)`,
   `statistics(ids, start, end)`, `capacity_states(...)`,
   `statistics_units(ids)` and `stored_rows(...)` (Effy's own rows, ADR-024),
   each with the signature and the result of today's function of the same name.
   Three things implement it:

   - `RecorderStorage` (HA-facing, `recorder_storage.py`): the **only** place
     that calls the recorder's read APIs and `phases.recorder_job`. Today's
     `_fetch_*` functions become its internals and are looked up at call time,
     so the nine test files that patch them keep working while the call sites
     move.
   - `Cache` (`cache.py`, pure, no `hass`, no recorder import, no I/O): holds an
     injected inner `Storage`, a clock and the settle time, and **is** a
     `Storage`. A call it can answer from memory it answers; for the rest it
     calls the inner storage with exactly the missing span, stores the answer
     and answers.
   - in tests, a storage that applies the recorder's rule to a list of states or
     rows (the TASK-0035 rig is its write-side twin), and for a replay the cache
     itself, loaded from a snapshot over an inner storage that raises on any
     read (TASK-0037).

   `history.py` functions take a `storage: Storage` and do not know which one it
   is. The coordinator owns one `Cache` per config entry over one
   `RecorderStorage` and passes the cache, as it passes `energy_reading_cache`
   today.

1. **Substitutable, and tested as such.** For any `Storage` S and any sequence
   of reads, `Cache(S)` returns what S returns (a model-based test over a fake
   that implements the recorder's rule, with random spans, seams and a start
   older than the recorder's oldest state). The cache's code runs on the event
   loop between the `await`s of the inner storage, so it needs no lock. It
   **re-checks its validated span after every `await`** (Shady's
   `_fetch_and_store` rule): `async_probe_last_slot_ready` runs outside the
   recorder-job lock and can read while a recalculation does. If ADR-022's
   compute move is ever released and the cache is touched from an executor job,
   a leaf `RLock` as in Shady's TASK-0042 is added then.

1. **It fetches exactly what is asked.** A request for two hours reads two
   hours. A range is extended at its head or tail by the missing span only.
   There is no prefetch of the configured window. After a `clear()` and one
   recalculation the cache therefore holds what that recalculation read (open
   point 5 on what "read" includes).

1. **Entry kinds.**

   - *Raw states* of an entity (the energy-family inputs and outputs, the BMS
     inputs of the standby machine, the battery capacity entity): `base` (the
     state in effect at the start of the validated span, as the recorder returns
     it), the **real** events inside the span `(time, state[, unit])`, and the
     span `(from, to)`. `read(s, e)` applies the recorder's rule to the cached
     events: the latest event strictly before `s` (or `base`), stamped `s`, then
     the events with `s < time < e`. Where a read cannot be answered from the
     span, the missing head or tail is read so that it **overlaps** the span by
     a one-second seam (the tail starts at `to` minus the seam, and `to` already
     lags the read by `RECORDER_SETTLE`), and the fetch's first row (stamped
     with its own start) is dropped, so a state written exactly on a seam is not
     lost and none is counted twice. `to` is the end of the read minus
     `RECORDER_SETTLE` (a state not yet committed is not an answer; Shady's
     ADR-007a Amendment 2026-10-07).
   - *Statistics rows* of a statistic id (the power-family sources, the SoC, the
     standby ids, the efficiency sources; `types={"mean","change"}`): one entry
     per slot, three states as in Shady (`row`; `None` not read yet; `MISSING` a
     settled, stable absence), a validated span, and a trailing run of `None`
     for slots that ended less than `RECORDER_SETTLE` ago is **not** validated.
   - *Anchors*: the answer of `_fetch_last_valid_state_before` (a reading, or
     "none within the horizon"), keyed by entity and by the instant asked about.
     ADR-015's `last_valid_energy_readings` becomes the `last_valid_reading`
     accessor of the same cache with the same behaviour (open point 8).
   - *Observed* values: what Effy reads and must **not** reuse: the stored rows
     of its own statistics (ADR-024's comparison; the recorder may change a row
     the moment after Effy read it, and a wrongly skipped write is a wrong
     statistic) and the units, state classes and retention of the sources
     (cheap, from the state machine). They are recorded in the cache as they are
     read, so that `snapshot()` is complete, and every recalculation reads them
     fresh (open point 5).

1. **`clear()` and `snapshot()`** are methods of `Cache` beyond `Storage`.
   `clear()` empties every kind. `snapshot()` returns plain data (dicts, lists,
   strings, ISO datetimes with offset, no live object) with a schema version,
   that `Cache.from_snapshot(data, inner)` turns back into an equal cache. A
   replay (TASK-0037) is
   `Cache.from_snapshot(data, inner=<a storage that raises on every read>)`: a
   read the recording does not contain fails loudly instead of inventing data,
   which also shows when the code reads something different from what it read
   when the file was written.

1. **Bound.** The cache holds what recalculations asked and no more than
   `max_history_days` (the history recalculation drops everything older than its
   start when it begins). Measured in Python 3.12: a raw state costs 160 bytes,
   so the Pi's 4,804 states are 0.8 MB, one counter that ticks every minute is
   6.5 MB for 28 days and 13.7 MB for 60; a statistics row 290 bytes, 2.3 MB for
   28 days of one power sensor. A cap per entity (open point 7) keeps a
   pathological sensor from growing without end; over it the entity is read
   directly and not stored.

1. **What changes for a reader of the code.** `history.py` reads only through
   the `Storage` it is given, so no read can go around the cache; a test fails
   if a recorder read API is named outside `recorder_storage.py`. The order of
   the reads, what is computed and what is written do not change (ADR-020
   Amendment 2026-10-30 and ADR-024 are untouched; the writer is not part of
   `Storage`). A failing read leaves no half-filled entry. The timing record
   says how many reads were served from the cache. The cache takes the settle
   time as a parameter: `sensor_utils.RECORDER_SETTLE` lives in a module that
   imports Home Assistant, which a pure module cannot do.

1. **The history button.** Recommended: it calls `clear()` first and reads
   fresh, because the human uses it to repair, and it then fills the cache for
   the slot jobs that follow (they extend the tail only). This also makes
   TASK-0037's "the cache was clean before" true without a second mechanism
   (open point 2).

1. **Not persisted.** After a restart the first slot job reads its window as
   today (open point 3).

1. **Out of scope:** caching what Effy calculates or writes; any change to the
   loss model, the sensors, the options or what is published; a speed target.

1. **Effy reads back what it wrote, so a write invalidates.** The drain-activity
   and efficiency readers read `effy_*_power`, `effy_*_smoothed` and
   `sensor.effy_standby_power`, statistics that Effy itself writes. `Storage`
   has a `written(statistic_ids, start, end)` that `history.py` calls after
   every write; `RecorderStorage` ignores it, and `Cache` forgets everything it
   holds for those ids (not the rows only: the whole entry, the conservative
   choice). The recorder may also change a row after the write, so there is no
   write-through. Found while building Stage 2 (2026-10-31); a test shows a read
   after a write returns the new rows.

## Verification (what the build must show)

- *Equality.* For every kind, `read` equals a direct read for random spans,
  including a span assembled from several fetches, a state written exactly on a
  seam, and a start older than the recorder's oldest state; the CAP-1
  equivalence harness and the TASK-0035 recorder rig give the same rows with the
  cache as without, including the offline anchor and a counter that ticks while
  the job runs.
- *Exactness.* After `clear()` and one history recalculation, `snapshot()` holds
  exactly the keys and spans that recalculation asked for.
- *Freshness.* A slot the recorder has not compiled, and a state it has not
  committed, are not stored as answers.
- *No bypass.* Structural (decision 7), plus a test over `history.py`'s source
  that fails if a recorder read API is named outside `recorder_storage.py`.
- *Replay.* A cache loaded from a snapshot over a raising inner storage answers
  every read the recorded recalculation made, and raises on any other.

## What was verified, and what was not

Verified by reading Home Assistant 2026.10.4 (the human's version) and Shady's
`cache.py` on branch `initialcode`: the raw-state rule above, the start-time
stamping, Shady's whole-window fetch, its settle rule and its lock. Verified by
the in-memory harness: the number of reads per recalculation (TASK-0036
Findings). **Not verified:** any behaviour on the Raspberry Pi beyond the
human's record of 2026-10-27; memory on the Pi (the figures above are from this
machine); whether the recorder commits states within `RECORDER_SETTLE` under
load.

## Consequences

- **Pro:** a recalculation's inputs are in one place and can be written out and
  replayed (TASK-0037); the slot job stops re-reading the overlap of its own
  windows; two code paths that read the same data (slot job and history) read it
  the same way.
- **Pro:** nothing is prefetched, so the memory and the file of the export are
  what the work needed.
- **Con:** one more module and a second place where "what is current" is
  decided; a cache that is wrong gives wrong statistics. Hence the equality
  tests, the observed (never reused) stored rows and a button that reads fresh.
- **Con:** the saving is small. The justification is replay and equal results.

## Open points for the human

1. **Purpose.** Speed saves about 1.6 s of 3.92 s on a repeated history run and
   a fraction of 0.19 s per slot job. Is the cache built for the replay
   (TASK-0037) and consistency, with that saving as a side effect? *Recommended:
   yes.* If not, the cache is not worth its risk.
1. **The history button:** `clear()` and read fresh (recommended), read through
   the cache, or both.
1. **Persistence** across a restart: none (recommended) or a stored copy.
1. **Per config entry** (recommended) or shared.
1. **Stored rows of Effy's own statistics and the metadata:** *observed, never
   reused* (recommended: the snapshot is complete, the writes stay exact), or
   kept out of the cache and written to the export by a separate record.
1. **Settle:** `RECORDER_SETTLE` (30 s) for the freshest statistics and states
   (recommended, one constant for the whole project), or Shady's ten minutes.
1. **A cap per entity** for raw states, and its value (suggested: 200,000
   entries, about 32 MB, over which the entity is read directly and a warning is
   logged once).
1. **ADR-015's reading cache** folded into this one (recommended; ADR-015 is
   amended) or left beside it.
1. **How the timing record reaches the recorder-backed storage.** Today `phases`
   is passed down by hand and wraps each executor job. Recommended:
   `RecorderStorage` has a `phases` attribute that the run holding the
   recorder-job lock sets; a read outside the lock (the probe) is untimed, as it
   is today. Alternative: a context argument on every `Storage` method.
1. **The write side.** `Storage` here is read-only. A `Sink` interface for
   `_write_recorder_statistics`, with a recording implementation, would let the
   export also carry the rows a run wrote (the expected result of a replay) and
   let the TASK-0035 rig become a storage. Recommended: TASK-0037's Stage 2, not
   this task.

## Answers to the open points

Confirmed 2026-10-31 by the human's "Proceed", as the Lead Agent reads it:
**every recommendation of the draft was adopted.** If one was not meant, say
which and the change is made in TASK-0036 before it is `done`.

1. Purpose: replay and equal results; the saving in time is a side effect.
1. The history button calls `clear()` and reads fresh.
1. Not persisted.
1. One cache per config entry.
1. Stored rows (`unchanged_rows`) and the source metadata are *observed*: read
   fresh every time, recorded in the snapshot.
1. Settle: `RECORDER_SETTLE` (30 s), passed to the cache as a parameter.
1. A cap of 200,000 raw entries per entity; over it the entity is read directly,
   not stored, and a warning is logged once.
1. ADR-015's reading cache is folded into this one (ADR-015 is amended). **Not
   done in TASK-0036; see the Amendment 2026-10-31.**
1. `RecorderStorage` has a `set_timing` hook that the run holding the lock calls
   (the `phases` attribute of the draft, as a method so that the cache can
   forward it); a read outside the lock is untimed.
1. The write side as a `Sink` is not part of this task.

## Amendment — 2026-10-31

**Reason:** TASK-0036 Stage 2 built the decisions above. Where the build differs
from them, or found something the draft missed, it is recorded here.

**Decision (as built):**

1. `storage.py` (`Storage`), `cache.py` (`Cache`) and `recorder_storage.py`
   (`RecorderStorage`) exist; `history.create_storage()` builds the cache over
   the recorder-backed storage and the coordinator owns it, passes it to every
   history function and clears it before the history button reads (decision 8).
1. **The reader functions stay in `history.py`.** `RecorderStorage` receives the
   `history` module and looks `_fetch_raw_energy_states` and the other readers
   up on it at the moment of each call. Moving their bodies would change the
   patch targets of nine test files and the module in which `_unchanged_rows`
   finds a patched `_get_statistics_units`. The guarantee of decision 7 is kept
   by a test over `history.py`'s syntax tree instead: a recorder read API may be
   named only inside the readers, and a reader may be called only by another
   reader. Moving the bodies is a follow-up.
1. **ADR-015's reading cache is not folded in** (answer 8 above is not carried
   out here). `energy_reading_cache` is a parameter of the calls in many tests;
   it keeps its behaviour and sits beside the cache. A follow-up task folds it.
1. **Decision 11 was needed, as found:** `Storage.written()` is called by
   `history._write_and_forget` after every write; the cache forgets those
   statistic ids whole.
1. **The seam is 0.5 s**, not a second, and the first read of an entity starts
   one seam early, so that a state written exactly on the first instant asked
   about is not lost. A state written exactly one seam before that start is
   invisible to the recorder's own read as well and is not known to the cache;
   no read can ask for it. The start state is recognised within 2 microseconds
   of the start asked for (it comes back through a float timestamp).
1. **Timing.** The two public runs (`async_recalculate_history` and
   `async_recalculate_recent`) set the storage's timing record for exactly the
   run and reset it in `finally`; the history record's first line ends with
   `reads from the cache N, from the recorder M`.
1. **The tests of single functions** are not moved to a fake storage; they patch
   the readers by name as before and every function defaults to a
   recorder-backed storage. Moving them one file at a time is part of the
   follow-up.

**Decided by:** Lead Agent (not yet confirmed by the human)
