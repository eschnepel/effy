# Task: A cache for what Effy reads from the recorder (after Shady's `cache.py`)

- **Status:** todo — two stages: **Stage 1 evaluation and Draft ADR-025** (no
  file under `custom_components/` changes), a **gate**, then **Stage 2 build**
  (human request 2026-10-31)
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
| One pure module, no `hass`; `fetch_fn(sensor_id, start, end)` injected | Carries over. Effy's `history.py` is the one place that touches the recorder; `calculation.py` is pure; `cache.py` joins `calculation.py` at that layer (ADR-000 §3) |
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

- Given the confirmed ADR, then `custom_components/effy/cache.py` exists: pure
  (no `hass`, no recorder import, no I/O), tested with zero mocking and loaded
  by file path (ADR-000 §6), with the accessors the ADR names, the injected read
  functions, `clear()`, `snapshot()` and the bound.
- Given `history.py`, then **every** read listed in Stage 1 goes through the
  cache, and a test fails if a new read bypasses it (a recorder fake that counts
  calls not made through the cache).
- Given a history recalculation and a slot job cycle, then the written rows are
  exactly what they were before (the equivalence harness and the TASK-0035 rig
  green; no existing assertion weakened), and a second slot job cycle reads from
  the recorder only what the first did not.
- Given a clean cache and one history recalculation, then `snapshot()` contains
  exactly the keys and ranges that recalculation read, and nothing else.
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
2: `custom_components/effy/cache.py` (new), `history.py` (the read functions and
their callers), `coordinator.py` (owns the instance, clears it on reload),
`tests/test_cache.py` (new), `tests/support.py` (the counting recorder),
`tests/test_history_recent_equivalence.py`, `README.md`, `adr/025-*.md`,
`adr/015-*.md`, `adr/000-*.md` (the module diagram), `GLOSSARY.md` (one term:
*Cache*, and what *clean* means).

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

<!-- Filled by the worker: Stage 1's report. -->

## Delivered Artifacts

<!-- Filled by the worker after Stage 2. -->
