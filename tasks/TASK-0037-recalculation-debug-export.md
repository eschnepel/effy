# Task: A debug export for the history recalculation button

- **Status:** todo — two stages: **Stage 1 evaluation** (the choices below, no
  file under `custom_components/` changes), then **Stage 2 build** (human
  request 2026-10-31)
- **Capability:** CAP-11
- **Related ADRs:** \[ADR-025 (Draft, the recorder-read cache of TASK-0036),
  ADR-012 (the button and `sensor.effy_recalculated_from`), ADR-021 (what Effy
  publishes), ADR-022 and ADR-024 (the timing record that already logs a
  recalculation), ADR-000 §6\]
- **Dependencies:** [TASK-0036-recorder-read-cache] — this task dumps that cache
  and relies on its two properties: it fetches exactly what a recalculation asks
  for, and every read of source data goes through it. Not startable before
  TASK-0036's Stage 2 is `done`.
- **Gate:** Stage 2 follows Stage 1 without a stop. The exception is how the
  export is switched on (the first open point below): it changes what the button
  or the options do, so the human picks.

## Goal

When something is wrong with Effy's numbers, the human today sends a log line, a
screenshot and some SQL, and the cause is found by reading code and guessing
(TASK-0033, TASK-0034 and TASK-0035 each began like that). The history
recalculation's input is all there is to it: Effy's configuration, the clock,
and what the recorder returned. If that input is written to one file, the
recalculation can be **replayed offline**, in a test, on the machine of whoever
looks at it, with no Home Assistant and no access to the instance.

Stage 1: decide the open points (activation, content, format, place, size,
privacy) with the facts. Stage 2: build the export on the history recalculation
button, and a replay that loads an export into the test harness and runs the
real history code over it.

## Context

The human's idea (2026-10-31): *every read of sensor statistics or states is
serialized into a debug output file. If the cache is clean before the
recalculation, everything in the cache after it is relevant and should be
written.* So the export is not a second set of reads or a logging hook in every
fetch function: it is **clear the cache, run the recalculation, write down the
cache.** What that requires of TASK-0036 (and is checked here, not assumed):

- every read of source data goes through the cache (a read around it is missing
  from the file);
- the cache fetches exactly what is asked, never more (a prefetch is irrelevant
  data in the file, and a 28-day read for a 2-hour question is a large one);
- the snapshot is serializable and holds no live object;
- "clean" is exact: a cache cleared at the start of this recalculation, with no
  other recalculation (slot job) filling it meanwhile. The recalculations are
  serialized by the recorder-job lock (ADR-011), so the export takes it for the
  whole run.

What is *not* in the cache, and may still be needed to replay a run (Stage 1
decides which go in the file): the configuration of the entry (the sensors by
role, `max_history_days`, SoC and capacity options, the valley hysteresis), the
clock the run used, Effy's and Home Assistant's versions, the units and state
classes of the sources (`_get_unit`, `_get_state_class`,
`_get_statistics_units`; metadata, not recorder rows), the stored rows of Effy's
own statistics that decided which rows were written (ADR-024), and, as an
expected result, the rows the run wrote.

The history recalculation is the one that reads everything; the slot job's
recent recalculation reads a window. Whether the slot job can be exported too
(for example: "the next slot job") is a Stage 1 question, but the button comes
first.

## Acceptance Criteria

### Stage 1 — evaluation (no file under `custom_components/` changes)

- Given the button (`button.py`), the service and `async_recalculate_history`,
  then the report names where the export hooks in and how the lock, the clearing
  of the cache and the dump are ordered, so that a slot job cannot fill the
  cache between the clear and the dump, and a failing recalculation still leaves
  a usable (partial) file or none, never a corrupt one.
- Given the choice **how it is switched on**, then the report gives the options
  with their cost and recommends one. The human decides:
  - (A) an option in the entry's options, "write a debug export with every
    history recalculation", default off;
  - (B) a second button, "Re-calculate History (with debug export)", beside the
    first;
  - (C) a service parameter on the existing service, no entity;
  - (D) the button always exports (rejected on its face: a file per press;
    stated for the record with the size it would have).
- Given the content, then the report lists each section and why it is needed to
  replay: metadata (versions, the clock, the time zone, the schema version of
  the file), the configuration, the source metadata, **the cache's snapshot**
  (states, statistics, anchors, capacity states), the stored rows read for
  ADR-024, and the rows written. For each: whether it is included by default,
  and its size on the human's instance (the Pi's 4,804 raw states for 28 days is
  the starting figure).
- Given the **format**, then the report chooses one and shows a sample: JSON
  with an explicit schema version, optionally gzip (and which sections are lists
  of rows), written by a pure function in a module with no `hass`. The datetimes
  are written in a form that round-trips exactly (UTC, with offset); the states
  are kept as the strings the recorder returned (`unavailable`, `unknown`
  included), not parsed.
- Given **where** the file goes, then the report says: a directory under the
  configuration directory (`hass.config.path("effy_debug")`), the file name (the
  time of the run and the entry), how many files are kept (older ones removed,
  the number is an option or a constant), how the human gets it (nothing is
  served over HTTP; the log line names the path; the usual file editor or share
  of the host), and that the write runs **off the event loop** (an executor job)
  with a bound on its size.
- Given **privacy**, then the report says what the file contains (entity ids,
  energy and power readings with their times, the configuration) and what it
  does not (no secrets, no credentials), that the human reads it before sharing
  it, and whether the entity ids can be replaced by stable pseudonyms on request
  (an option, off by default; the replay must work with either).
- Given **the replay**, then the report says where it lives (a test helper
  `tests/replay.py`, and/or a script under `utils/` that runs without Home
  Assistant) and how it loads an export into the in-memory recorder world of the
  existing history tests (`_World`, `_install`, the strict recorder of
  TASK-0035) so that the real `async_recalculate_history` runs over it; and what
  "the same" means (the rows written equal the rows in the file, exactly for
  slot keys and to the tolerance of ADR-022 for values).
- Given failure, then the export never fails the recalculation: an error while
  clearing, dumping or writing is logged once with the reason, the
  recalculation's own result is unchanged, and the cache is left in a state the
  next run can use.
- Given the above, then the report ends with the choices made (and the open
  points that remain for the human), and the sample file's section list is
  written into this task.

### Stage 2 — build (tests first, each shown failing)

- Given the switch chosen in Stage 1 and a history recalculation, then a file is
  written whose sections are the ones decided, and the log says where, how large
  it is and how long the export took (a part of the timing record, not a second
  line).
- Given a clean cache and one history recalculation, then the file's cache
  section is exactly `snapshot()` of the cache after that run: no entry that the
  run did not read, none missing (the rig of TASK-0035 and TASK-0036's counting
  recorder show the reads).
- Given a history recalculation started while a slot job runs, then the export
  waits for the lock and the file holds only what the recalculation read.
- Given **a round trip**, then a recalculation over the in-memory world,
  exported and replayed from the file alone (a fresh world built from the file,
  no reference to the original), writes rows equal to the original run's: the
  same keys, the same values within the stated tolerance, the same
  `recalculated_from`. This holds for an energy-family sensor with an offline
  gap and an anchor, a power-family sensor with a gap, the capacity entity, and
  an entity whose state is `unavailable`.
- Given a file written by a **different schema version** or a damaged file, then
  the replay refuses it with a message that names the version, and never
  half-runs.
- Given the size bound, then an export that would exceed it is written without
  the sections that exceed it and says so in its own metadata and in the log; it
  is not silently cut.
- Given the files kept, then the oldest are removed past the limit, only files
  of Effy's own naming, and a directory with other files in it is left alone.
- Given the README, then a section "Debug export" says how to switch it on,
  where the file is, what it contains, what to remove before sharing it, and how
  to replay it; and the glossary has the terms (*Debug export*, *Replay*).
- Given the fix, then the whole suite, `ruff`, `mypy` and `mdformat` pass, no
  existing assertion is weakened, and a mutation (a section dropped, a read
  bypassing the cache, a datetime written without its offset) is caught.
- Given the Pi, then the human switches it on, presses the button once, and
  reports the log line and the file's size; and replays it on another machine.
  Until then the task stays in `review`.

## Estimated File / Module Footprint (hint, not a commitment)

Stage 1: the report in this file. Stage 2:
`custom_components/effy/debug_export.py` (new, pure: building the document from
the cache snapshot and the context, serializing, reading it back; the writing of
the file is the only I/O and is injected), `button.py` and `coordinator.py` (the
switch and the lock), `history.py` (the hook after a recalculation, the section
of the timing record), `config_flow.py` and `translations/` if the switch is an
option, `tests/replay.py` (new), `tests/test_debug_export.py` (new),
`tests/support.py`, `README.md`, `GLOSSARY.md`, a new ADR or an amendment to
ADR-025 for the file format (Stage 1 says which).

## Definition of Done

- Stage 1's choices are made and written here; the format has a schema version
- Stage 2 tests were shown failing first; `pytest`, `ruff format --check`,
  `ruff check`, `mypy` and `mdformat --check` pass
- The round trip test is green for each case named above
- Docs updated (README, glossary, the ADR) · no open ADR conflicts
- The human's run on the Pi is recorded in the findings
- `Delivered Artifacts` block completed and accurate
- Any new external dependency recorded in `tasks/DEPENDENCIES.md` (none
  expected: `json` and `gzip` are in the standard library)

## Consumed Interfaces

<!-- Filled by the Lead Agent from TASK-0036's Delivered Artifacts before this
     task starts: the cache's `clear()`, `snapshot()` and the shape of its
     entries. Until then: -->

- `async_recalculate_history` and the timing record in
  `custom_components/effy/history.py`; `EffyCoordinator._recorder_job_lock`
- the button (`custom_components/effy/button.py`) and the service
- `_World`, `_install`, `_FrozenDatetime` in
  `tests/test_history_recent_equivalence.py`; `StrictRecorderMixin` in
  `tests/support.py` (TASK-0035)

## Open points for the human (answered in Stage 1 with the facts)

1. **How it is switched on:** A (option, default off, recommended), B (second
   button), C (service parameter).
1. **Pseudonymized entity ids:** an option, or never.
1. **Does the slot job get one too** ("export the next recent recalculation")?

## Findings

<!-- Filled by the worker: Stage 1's report and decisions. -->

## Delivered Artifacts

<!-- Filled by the worker after Stage 2. -->
