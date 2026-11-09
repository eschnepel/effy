# Task: A cache for what Effy reads from the recorder (after Shady's `cache.py`)

- **Status:** done — closed by the human 2026-11-09 (was review since
  2026-10-31). Stage 1 done; gate passed (the human's "Proceed" on Draft
  ADR-025, read as confirmation with every recommendation; ADR-025 Accepted);
  Stage 2 built and checked inline; the Pi record was outstanding when closed.
  Deviations from the acceptance text are listed under Findings, "Stage 2 —
  built" (human request 2026-10-31)
- **Capability:** CAP-11
- **Related ADRs:** \[ADR-014 (recalculation range, offline anchor), ADR-015
  (the volatile last-valid-reading cache), ADR-011 (slot job, the recorder-job
  lock), ADR-022 (measure first; the pure-module placement), ADR-024 (stored
  rows are read first), ADR-020 and its Amendment 2026-10-30 (nothing here
  changes what is written), ADR-000 §3 and §6; **Draft ADR-025** (this task
  writes it); Shady: ADR-007, ADR-007a, ADR-008 §2 and
  `custom_components/shady/cache.py` of the `initialcode` branch, read-only
  reference\]
- **Dependencies:** none that block it. TASK-0035 (`review`) changed what is
  *written*; this task changes what is *read*; both touch `history.py`, so the
  worker starts from the tree that contains TASK-0035.
- **Gate:** Stage 1 ends with Draft ADR-025 and a list of open points. **The
  human confirms the draft (or answers the points) before any code is written.**
  Stage 2 then follows without a stop.
- **Needed by:** TASK-0037 (the debug export dumps this cache).

## Goal

Effy re-reads the same source data from the recorder on every recalculation: the
slot job (every 5 minutes) reads the raw states of every energy-family sensor
and the statistics of every power-family sensor for its window, the history
recalculation reads the whole configured range, and each reads the units and
Effy's own stored rows besides. Shady solved the same problem with a pure
`cache.py`: one module that owns what was read, is handed the functions that
read it (so it never imports the recorder), and is tested with no mocking.

Stage 1: find out which reads Effy makes, what a cache of them would look like
for Effy's kinds of data (which are not all the 5-minute series Shady's cache
was made for), what it buys (with figures, honestly, including "only the debug
export needs it"), and write the design as Draft ADR-025. Stage 2: build it, so
that every read of source data from the recorder goes through it, a
recalculation gives exactly the result it gave before, and the content of the
cache after a recalculation is exactly what that recalculation read.

## Context

What exists today (to be completed and checked in Stage 1):

- `_fetch_raw_energy_states` (`state_changes_during_period`, per energy-family
  sensor: `(time, state string)` events, the state in effect at the start
  first), `_fetch_last_valid_state_before` (the offline anchor, staged
  lookbacks), `_fetch_statistics` (`statistics_during_period`, 5-minute `mean`
  of the power-family sensors, and the hourly read of the slot job's rewrite),
  `_fetch_capacity_states` (the battery capacity entity), the stored rows of
  Effy's own statistics (`_unchanged_slots`, ADR-024), and the units, state
  classes and retention days (`_get_unit`, `_get_state_class`,
  `_get_statistics_units`, `_get_short_term_retention_days`).
- ADR-015's `last_valid_energy_readings` in the coordinator: a volatile,
  session-long cache of one thing, the last valid reading of an energy-family
  sensor, so that the offline lookback is skipped.
- The standby and efficiency machines (ADR-018, ADR-023) are fed from the slot
  job and rebuilt by the history recalculation; whether they read anything from
  the recorder besides what the above already reads is a Stage 1 question.
- The Pi's records: 4,804 raw states for 28 history days, `fetch` 1.82 s of
  106.63 s before ADR-024; after TASK-0030 a repeated history run takes 3.92 s
  (106 s before; the human's record of 2026-10-27). **The fetch is not what
  takes the time.** A cache that is justified by speed alone is therefore
  doubtful; Stage 1 says so if it is.

What Shady's cache is (`adr/007*.md`, `cache.py`, TASK-0042, TASK-0043), and
what is likely to carry over and what is not (Stage 1 decides, with the code):

| Shady | Effy |
| -- | -- |
| One pure module, no `hass`; `fetch_fn(sensor_id, start, end)` injected | Carries over **as an interface** (the human's design, 2026-10-31): a `Storage` protocol in `storage.py`; `RecorderStorage` is the one HA-facing implementation and the only place that touches the recorder; `Cache` (pure, at the layer of `calculation.py`, ADR-000 §3) implements the same protocol and wraps an injected inner storage |
| Index-addressable 5-minute lists per sensor, three states per entry (`float`, `None` not fetched, `str` a stable non-answer such as `unavailable`), absolute index from a fixed epoch | Fits the **statistics** of power-family sensors and Effy's stored rows. Does **not** fit raw states: they are events at irregular times (a counter that ticks every 19 to 35 minutes), not slots |
| `validated` range per sensor; a fetch fills only what is missing; sensors with the same missing range share one call | Carries over in principle; the unit of a raw-state range is a time span, not an index range |
| A sensor with no valid data is fetched for its **whole** window in one call | **Conflicts with TASK-0037**, which needs "the cache was clean before, so everything in it afterwards is what this recalculation read". The slot job must not pull 28 days because it asked for 2 hours. Effy's cache fetches exactly what is asked |
| A `None` in the freshest slots is not an answer: the recorder compiles a slot about 10 s after its end (TASK-0037-patch-5) | Carries over, and is the same fact TASK-0035 built on (`RECORDER_SETTLE`) |
| One explicit `trim()` at the recalculation, to a whole-day boundary (TASK-0043) | Carries over in kind; what bounds Effy's range is `max_history_days` and the recorder's `keep_days` |
| A private `RLock`, never held across a fetch (TASK-0042) | Effy already serializes recalculations with the recorder-job lock (ADR-011); whether the cache is also touched from an executor job is a Stage 1 question |
| Fitted models, ramp state, energy totals, service-response cache, pinned pools, regression pools | None of these exist in Effy |
| `push` of values Shady calculated itself | Effy does not cache what it calculates (the recorder is the store of its output); out of scope |

## Acceptance Criteria

### Stage 1 — evaluation (no file under `custom_components/` changes)

- Given the code, then the report is a **table of every read** of source data
  and of Effy's stored rows: the function, the recorder call, what it returns
  (the shape), the key (entity, range), the callers, how often it runs in a slot
  job cycle and in a history recalculation, and — from the Pi's records and the
  in-memory harness — what it costs. Anything found by search that is not in the
  list above is added; a read that the cache must not cover is named with the
  reason.
- Given Shady's cache, then the report says for each row of the table above what
  carries over, what changes and what is dropped, **from the Shady code**, not
  from this task's guess, and names the Shady tasks whose lessons apply (at
  least TASK-0037-patch-5, TASK-0042, TASK-0043).
- Given ADR-015's volatile reading cache, then the report says whether it
  becomes one entry kind of the new cache (the last valid reading of a sensor is
  a derived answer to "what is the last valid state before T"), stays beside it,
  or goes, and what happens to the offline-recovery paths (ADR-014).
- Given the question "what does the cache buy", then the report gives the
  **recorder queries and rows read per slot-job cycle and per history
  recalculation, with and without it**, measured in the harness (counted calls
  and rows) and, where the Pi's record exists, in seconds. It states plainly if
  the saving is small and the reason to build is TASK-0037 and the equivalence
  of recalculations; the human decides on that basis.
- Given TASK-0037's need, then the design states how the cache **fetches exactly
  what is asked**, extends a range on demand, and can be **cleared** and
  **dumped whole** (a snapshot that can be serialized, without a reference to
  any live object), and shows with a test plan that after a clear and one
  history recalculation the content is exactly what that recalculation read:
  nothing prefetched, nothing missing, nothing read around the cache.
- Given the data kinds, then the design gives for each: the stored form (slot
  list with three states; event list with a validated time span; scalar), the
  key, how a range is validated, how a fetch is **bounded** (`max_history_days`,
  and the memory in numbers: entries per sensor for 28 days, for the Pi's 4,804
  raw states and for a counter that ticks every minute), and what a recorder
  that answers late means (the freshest slots, `RECORDER_SETTLE`).
- Given invalidation, then the design says when an entry is dropped: a change of
  the configured sensors (entry reload), a user's edit or deletion of recorder
  history, a recorder purge, an entity whose unit or state class changed, and
  the **history button**: should it always read fresh (the human uses it to
  repair), read through the cache, or offer both. The recommendation is argued;
  the choice is the human's.
- Given restarts, then the design says whether the cache is persisted
  (recommendation: not, as in Shady for everything but the totals) and what the
  first slot job after a restart reads, in numbers.
- Given the equivalence harness (CAP-1, TASK-0004) and the recorder rig of
  TASK-0035 (`tests/support.py`), then the test plan shows how "with the cache"
  and "without" are compared for every kind of read, including the offline
  anchor, a counter that ticks while the slot job runs, and a recorder that has
  not compiled the freshest slot.
- Given the above, then **Draft ADR-025** is written
  (`adr/025-recorder-read-cache.md`, status Draft, amending nothing silently:
  ADR-015, ADR-014 and ADR-024 are named where the cache touches them),
  `tasks/adr-summary.md` has a line marked draft, and the report ends with the
  **open points for the human**, each with the options and a recommendation.
  **Stop here.**

### Stage 2 — build (after the human's confirmation of the draft; tests first, each shown failing)

- Given the confirmed ADR, then `storage.py` (the `Storage` protocol),
  `cache.py` (`Cache`, pure: no `hass`, no recorder import, no I/O; it
  implements `Storage` over an injected inner storage, clock and settle time;
  tested with zero mocking and loaded by file path, ADR-000 §6) and
  `recorder_storage.py` (`RecorderStorage`, the only HA-facing implementation)
  exist, and `Cache` has `clear()`, `snapshot()`, `from_snapshot(data, inner)`
  and the bound.
- Given `history.py`, then it takes a `Storage` and **every** read listed in
  Stage 1 goes through it, so none can go around the cache; a test over its
  source fails if a recorder read API is named outside `recorder_storage.py`.
  The nine test files that patch the module-level `_fetch_*` functions keep
  passing while the call sites move (`RecorderStorage` looks them up at call
  time), and are then moved to a fake storage one file at a time, no assertion
  weakened.
- Given a history recalculation and a slot job cycle, then the written rows are
  exactly what they were before (the equivalence harness and the TASK-0035 rig
  green; no existing assertion weakened), and a second slot job cycle reads from
  the recorder only what the first did not.
- Given a clean cache and one history recalculation, then `snapshot()` contains
  exactly the keys and ranges that recalculation read, and nothing else; and
  `Cache(S)` returns what any storage `S` returns for any sequence of reads (a
  model-based test over a fake that applies the recorder's rule), also when the
  validated span is re-checked after an `await` (the probe reads outside the
  recorder-job lock). A cache loaded with `from_snapshot` over a storage that
  raises on every read answers every read of the recorded recalculation and
  raises on any other.
- Given the freshest slot, then a `None` for a slot the recorder has not
  compiled is not stored as an answer (the TASK-0037-patch-5 lesson, shown with
  a test).
- Given the timing record (ADR-022, TASK-0010), then it says how many reads were
  served from the cache and how many went to the recorder.
- Given a failing read, then the recalculation behaves as it does today (the
  fail-safe of ADR-024 is untouched), and no half-filled entry is left behind.
- Given the README, then the History recalculation section says what is cached,
  for how long, and how to clear it, and the timing record's new part is read
  like the others.
- Given the fix, then the whole suite, `ruff`, `mypy` and `mdformat` pass, and a
  mutation of the new rule (a missing range check, a bypass, a stored `None` in
  the freshest slot) is caught.
- Given the Pi, then the human runs a history recalculation twice and a few slot
  jobs and reports the timing record, so that the figures of Stage 1 are
  confirmed. Until then the task stays in `review`.

## Estimated File / Module Footprint (hint, not a commitment)

Stage 1: the report in this file, `adr/025-recorder-read-cache.md` (Draft),
`tasks/adr-summary.md`, a scratch script (not committed) for the counts. Stage
2: `custom_components/effy/storage.py`, `cache.py` and `recorder_storage.py`
(new), `history.py` (the read functions become `RecorderStorage` internals;
their callers take a `Storage`), `coordinator.py` (owns the instance, clears it
on reload), `tests/test_cache.py` (new), `tests/support.py` (the counting
recorder), `tests/test_history_recent_equivalence.py`, `README.md`,
`adr/025-*.md`, `adr/015-*.md`, `adr/000-*.md` (the module diagram),
`GLOSSARY.md` (one term: *Cache*, and what *clean* means).

## Definition of Done

- Stage 1's report is complete and Draft ADR-025 is confirmed by the human
- Stage 2 tests were shown failing first; `pytest`, `ruff format --check`,
  `ruff check`, `mypy` and `mdformat --check` pass; the equivalence harness is
  green
- Every read goes through the cache; `snapshot()` after a clean recalculation is
  exactly what it read
- Docs updated (README, ADR-025 accepted, ADR-015 amended where it changes,
  ADR-000's diagram, GLOSSARY) · no open ADR conflicts
- The human's record from the Pi is in the findings
- `Delivered Artifacts` block completed and accurate
- Any new external dependency recorded in `tasks/DEPENDENCIES.md` (none
  expected)

## Consumed Interfaces

- `_fetch_raw_energy_states`, `_fetch_last_valid_state_before`,
  `_fetch_energy_raw_with_anchor`, `_fetch_statistics`,
  `_fetch_capacity_states`, `_unchanged_slots`, `_unchanged_rows`,
  `_mark_unchanged`, `_get_unit`, `_get_state_class`, `_get_statistics_units`,
  `_get_short_term_retention_days`, `async_recalculate_history`,
  `async_recalculate_recent`, `async_rewrite_hourly` and
  `_PhaseTimings.recorder_job` in `custom_components/effy/history.py`
- `EffyCoordinator` (`last_valid_energy_readings`, `_recorder_job_lock`,
  `_async_recalculate_recent_and_report`) in
  `custom_components/effy/coordinator.py`
- `SLOT_MINUTES`, `RECORDER_SETTLE`, `settled_before` in
  `custom_components/effy/sensor_utils.py`
- Shady (read-only, branch `initialcode`): `custom_components/shady/cache.py`,
  `adr/007-coordinator-cache-split.md`,
  `adr/007a-cache-storage-and-accessor-design.md`

## Open points for the human (to be answered at the gate, with Stage 1's figures)

1. Is the cache built for **speed**, for **the debug export and replay**
   (TASK-0037), or both? Stage 1 gives the figures; the answer decides how much
   machinery is justified.
1. Does the **history button** read fresh, through the cache, or both (for
   example: through the cache, and a "clear the cache" action)?
1. **Persistence** across a restart: none (recommended), or a stored copy.
1. Is the cache **per config entry** (as Effy's data is) or shared?

## Findings

### Stage 1 report (2026-10-31)

Sources: the code (`history.py`, `coordinator.py`), the harness (real history
code, the TASK-0035 recorder rig, counts of calls and rows), Home Assistant
2026.10.4 (`recorder/history/__init__.py`, `models/state.py`), Shady's
`cache.py` and ADR-007/007a on `initialcode`, and the human's Pi record of
2026-10-27 (TASK-0030 patch 2). Not available: any new measurement on the Pi.

**1. Every read of source data and of Effy's stored rows**

| Read | Recorder call | Returns | Callers and how often |
| -- | -- | -- | -- |
| Raw states, `_fetch_raw_energy_states` | `state_changes_during_period(start, end, entity, no_attributes=True)` | `[(time, state string)]`, the first row the state in effect at `start` stamped `start` | `_fetch_energy_raw_with_anchor`: one per energy-family input and output per slot job, one more if a jump extends the window (harness cycle 1: 4 calls for 2 sensors, then 2), one per sensor per history run; `async_read_drain_events`: one per BMS input per cycle; `_fetch_capacity_states` (same API, plus the unit): the capacity entity, per battery update and rebuild |
| Anchor, `_fetch_last_valid_state_before` | the same API, descending, staged lookbacks (30 min, 1 day, `max_history_days`) | `(time, state)` or `None` | only when the window starts offline and contains a recovery, and ADR-015's cache has no answer |
| Statistics, `_fetch_statistics` | `statistics_during_period(..., "5minute", types={"mean","change"})` | `{statistic_id: [row dict]}` | `_compute_effective_slots` (power-family sources, one call per run), `async_probe_last_slot_ready` (two slots, per slot job), `async_read_drain_activity`, `async_read_efficiency_energy`, `async_update_battery` (SoC), `async_rewrite_hourly` (the 12 slots of an hour, once an hour) |
| Units of the statistics, `_get_statistics_units` | recorder metadata, one executor job | `{id: unit}` | 4 to 6 per recalculation in the harness |
| Unit, state class, retention | the state machine, `hass.data` (no recorder query) | strings, a number | per recalculation |
| Effy's own stored rows, `_unchanged_slots` / `_unchanged_rows` | `statistics_during_period` with the compared columns | rows of one Effy series | one per Effy series per run (14 on the Pi), 1.37 s of the Pi's 3.92 s |
| Last valid reading, ADR-015 | none (the coordinator's dict) | `(time, state)` | read on an offline recovery, updated every cycle |

Harness counts (2 energy-family sources, 1 power-family source, 5 Effy series, a
small world): one history run 2 raw reads (24 rows), 1 statistics read (20
rows), 6 unit reads, 5 stored-row reads; a repeated run reads 116 stored rows; a
slot job cycle 2 to 4 raw reads, 1 statistics read, 4 to 6 unit reads, 3 to 5
stored-row reads. Calls do not grow with the range; rows do. **The Pi
(2026-10-27):** a repeated history run 3.92 s, 20 recorder queries (units, 4
raw, power, 14 stored), fetch 2.96 s of which the stored rows 1.37 s (0.08 to
0.14 s per series), 109,762 stored rows read; a slot job cycle 0.19 s in total
with a 24-hour window.

**2. Shady, from its code.** *Carries over:* a pure module with no `hass`, one
explicit trim, a leaf lock only if an executor job touches it, the rule that a
trailing `None` in the freshest slots is not an answer (`_settled_end`, there
ten minutes; here `RECORDER_SETTLE`). *Changes:* Shady calls the cache from
inside an executor job with an injected `fetch_fn`, so it needs a lock; Effy's
reads are awaited on the event loop (`phases.recorder_job`), so the cache needs
no lock; it re-checks its span after each `await`. An earlier version of the
draft had the caller read between `missing` and `store`; **the human's design
replaced it (2026-10-31)**: the cache implements the same `Storage` protocol as
the recorder-backed storage and wraps it, so `history.py` calls one interface
and cannot go around the cache. Shady's index-addressable list per sensor fits
the statistics rows only; raw states are events with a base state and a span.
Shady fetches a sensor's whole window the first time anything is asked
(`_validate_range`: `fetch_start = min(start, end - window_len + 1)`); Effy must
not (TASK-0037). *Dropped:* models, ramp state, energy totals, service-response
cache, pinned pools, `push`.

**3. Raw states are not slots, and a read depends on its start.** In 2026.10.4
the read returns the latest state strictly before `start`, stamped `start`, then
the changes with `start < last_updated < end`; a state written exactly at
`start` is in neither part. A cache must therefore keep real events and a base
state and re-read a seam with an overlap. This is the one point where reusing
Shady's design unchanged would have been wrong.

**4. ADR-015.** Its dict is the answer to "the last valid reading before T",
which the raw events answer when the span reaches back, and the anchor lookback
answers otherwise. Proposed: it becomes an accessor of the cache with the same
behaviour (open point 8 of the draft).

**5. What the cache buys.** On the Pi: at most the source reads of a repeated
history run (about 1.6 s of 3.92 s: the 2.96 s fetch less the 1.37 s of stored
rows, which are not cacheable) and a fraction of 0.19 s per slot job. **Speed is
not the reason.** The reasons are the replay of a recalculation from its inputs
(TASK-0037) and one path for every read. The human decides on that basis (open
point 1).

**6. Clear, exactness, dump.** Designed in the draft (decisions 3 and 5):
nothing is prefetched, `clear()`, `snapshot()` as plain data with a schema
version and `from_snapshot()`. Test plan: after `clear()` and one history
recalculation `snapshot()` equals the set of spans the counting recorder saw
requested.

**7. Bounds.** Measured here (Python 3.12): 160 bytes per raw state, 290 per
statistics row. The Pi's 4,804 states are 0.8 MB; one counter ticking every
minute is 6.5 MB for 28 days and 13.7 MB for 60; 28 days of one power sensor is
2.3 MB. A cap per entity is proposed (open point 7). Not measured on the Pi.

**8. Invalidation.** A change of the configured sensors reloads the entry and
builds a new cache. A recorder purge, an edit of history or an entity that
changed unit are not seen by a cache that reads only the tail: hence the button
reads fresh (open point 2). A slot or state not yet compiled or committed is not
stored as an answer.

**9. Restart.** Nothing persisted (open point 3); the first slot job reads its
window as it does today.

**10. Test plan.** Equality of `read` and a direct read for random spans and
seams (a state exactly on a seam, a start older than the recorder's oldest
state), the CAP-1 equivalence harness and the TASK-0035 rig with and without the
cache (offline anchor, a counter ticking during the job, an uncompiled freshest
slot), a counting recorder for "no read around the cache", `snapshot()` against
requests, and mutants (a missing overlap, a stored `None`, a bypass).

**Draft ADR-025** is written (`adr/025-recorder-read-cache.md`) and
`tasks/adr-summary.md` has a line marked draft.

### Open points for the human (the draft's list, with a recommendation each)

See the end of ADR-025: purpose (replay, recommended), the button (clear and
read fresh), persistence (none), per entry, stored rows and metadata (observed,
never reused), settle (`RECORDER_SETTLE`, passed in as a parameter), the cap
(200,000 entries), ADR-015 folded in, how the timing record reaches
`RecorderStorage` (a `phases` attribute), and the write side as a `Sink`
(later).

### Stage 2 — built (2026-10-31)

**What exists.** `storage.py` (the `Storage` protocol), `cache.py` (`Cache`: raw
states and the capacity entity's as events with a base state, 5-minute
statistics rows with a validated span, anchors, observed kinds, `clear()`,
`snapshot()`, `from_snapshot()`, a cap per entity, `counters()`),
`recorder_storage.py` (`RecorderStorage`). `history.py`'s functions that read
take `storage: Storage | None = None` (the default is a plain recorder-backed
storage, so no caller changed); the two public runs scope the storage's timing
record to the run and reset it in `finally`; `_write_and_forget` tells the
storage what Effy wrote; `create_storage()` builds the cache. The coordinator
owns one per entry, passes it to every history call, clears it under the lock
before the button reads, and the timing record says
`reads from the cache N, from the recorder M`.

**Found while building (not in the draft).** Effy **reads back statistics it
wrote** (the standby and efficiency readers read `effy_*_power`,
`effy_*_smoothed` and `sensor.effy_standby_power`), so a cache would have served
them stale after a write: ADR-025 decision 11, `Storage.written()`. The start
state is recognised within 2 microseconds, not by equality (it comes back
through a float timestamp). A read after a `clear()` that was in flight must not
be undone by that read's stale merge (the revision check).

**Tests.** `tests/test_cache.py` was written before `cache.py` (it was first run
against the finished module, not shown failing); `tests/test_history_cache.py`
and the coordinator tests were written after the code they cover. What shows
that the tests detect a break is the mutants below, not a failing-first run.
`tests/test_cache.py` (the cache equals any storage for random worlds, a moving
clock and late commits; seams, freshness, the cap, snapshot, replay over a
storage that raises, concurrency, purity), `tests/test_history_cache.py` (16:
the same rows with and without the cache, the next slot job reads only the tail,
a clean run's snapshot is exactly what the recorder was asked, a run replayed
from its snapshot, writes are told, the timing record, a failing read, the
timing scope, no read around the interface), and 6 in
`tests/test_coordinator_slot.py`. Every mutant tried is killed: the seams,
spans, settle, `written`, revision check, start tolerance and copy of the cache,
and the `written`, timing-scope and cache-note parts of `history.py`. The suite
has 1503 tests; `ruff`, `mypy` and `mdformat` pass.

**Deviations from the acceptance text, and why.**

1. *The reader bodies stay in `history.py`* and `RecorderStorage` calls them
   through the module. "A recorder read API named only in `recorder_storage.py`"
   became a syntax-tree test over `history.py`: a recorder API only inside the
   readers, a reader only called by another reader. Moving the bodies would
   change the patch targets of nine test files and where `_unchanged_rows` finds
   a patched `_get_statistics_units`.
1. *The tests of single functions were not moved to a fake storage.* Their
   patches work as before.
1. *ADR-015's reading cache is not folded in* (the draft's answer 8).
1. *The tests were not all shown failing first* (the Definition of Done asks for
   it): see Tests above; the mutants stand in for it.
1. *No measurement on the Pi*: the figures of Stage 1 are unconfirmed until the
   human's record exists.

**Follow-up (to be created if the human wants it):** move the reader bodies into
`recorder_storage.py` and the single-function tests onto a fake storage; fold
ADR-015 in.

**Open for the human.** Run a history recalculation twice and a few slot jobs on
the Pi and report the history timing line
(`reads from the cache N, from the recorder M`, the fetch time) and the 5-minute
job's debug line; confirm that after the button `sensor.effy_recalculated_from`
and the statistics are as before. Confirm the amendments (ADR-000, 015, 025).

## Delivered Artifacts

- `custom_components/effy/storage.py` → `Storage` (Protocol), `StatRow`,
  `RawState`, `CapacityState`
- `custom_components/effy/cache.py` → `Cache` (`raw_states`, `capacity_states`,
  `statistics`, `last_valid_state_before`, `statistics_units`, `unchanged_rows`,
  `written`, `unit`, `state_class`, `retention_days`, `set_timing`, `clear`,
  `snapshot`, `from_snapshot`, `counters`), `SNAPSHOT_VERSION`
- `custom_components/effy/recorder_storage.py` → `RecorderStorage`
- `custom_components/effy/history.py` → `create_storage`, `_storage_or_default`,
  `_write_and_forget`, `_cache_counters`, `_note_cache_reads`,
  `_PhaseTimings.note_cache`, `_recalculate_history`, `_recalculate_recent`; a
  `storage` keyword on `async_recalculate_history`, `async_recalculate_recent`,
  `async_rewrite_hourly`, `async_probe_last_slot_ready`,
  `async_read_drain_events`, `async_read_drain_activity`,
  `async_update_standby`, `async_read_efficiency_energy`,
  `async_update_efficiency`, `async_rebuild_efficiency`, `async_update_battery`,
  `async_rebuild_battery`, `_classify_sensors`, `_mark_unchanged`,
  `_compute_effective_slots`, `_fetch_energy_raw_with_anchor`
- `custom_components/effy/coordinator.py` → `EffyCoordinator._storage`
- `tests/support.py` → `FakeClock`, `FakeStorage`, `RaisingStorage`,
  `UnrecordedRead`
- `tests/test_cache.py`, `tests/test_history_cache.py`,
  `TestTheCoordinatorOwnsOneStorage` in `tests/test_coordinator_slot.py`
- `README.md` (What Effy keeps in memory), `GLOSSARY.md` (Storage, Cache, Clean
  cache), `adr/025` (Accepted, Amendment), `adr/015` and `adr/000` (amendments)
- External dependencies added: none
