# Task: A debug export for the history recalculation button

- **Status:** done — closed by the human 2026-11-09 (was review since
  2026-11-03: Stage 1 done with the human's three choices, Stage 2 built; the
  run on the Pi was outstanding) (human request 2026-10-31)
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

- every read of source data goes through the cache (structural once `history.py`
  takes a `Storage`, TASK-0036; a read around it would be missing from the
  file);
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
  - And how it records: *clear the cache, run, write its snapshot* (the human's
    idea, the default), or a recording decorator over any `Storage` that logs
    every call and its result (works without a clean cache, and also records the
    stored rows and metadata uniformly, but keeps duplicates of overlapping
    reads).
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
- Given **the replay**, then the report says that its natural form is
  `Cache.from_snapshot(data, inner=<a storage that raises on every read>)`
  handed to the real history code (a read the file does not contain fails, which
  shows a changed read pattern), and where the rest lives (a test helper
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
- the `Storage` protocol and `Cache.from_snapshot` (TASK-0036); `_World`,
  `_install`, `_FrozenDatetime` in `tests/test_history_recent_equivalence.py`
  (replaced by a fake storage as TASK-0036 moves the tests);
  `StrictRecorderMixin` in `tests/support.py` (TASK-0035)

## Open points for the human (answered in Stage 1 with the facts)

1. **How it is switched on:** A (option, default off, recommended), B (second
   button), C (service parameter).
1. **Pseudonymized entity ids:** an option, or never.
1. **Does the slot job get one too** ("export the next recent recalculation")?

## Findings

### Stage 1 — the report (2026-11-03)

**Where it hooks in.** `EffyCoordinator.async_recalculate_history_and_report`
(the button's and the service's one path) already takes `_recorder_job_lock()`
and calls `self._storage.clear()` under it. With the option on it also hands
`async_recalculate_history` an `export` hook. The hook is called by the run
itself, after its last write and before its timing line is logged, so: lock held
→ `clear()` → run (every read through the cache) → `snapshot()` on the event
loop with **no `await` between the end of the run and the copy** → the encoding,
the write and the pruning in an executor job → the lock is released. The slot
job's body and the other recalculations wait for the lock for the whole of it.
The standby, efficiency and battery rebuilds that follow the run in the
coordinator are **not** in the file (they read Effy's own statistics, not the
sources); the file is the history recalculation.

**One reader is outside the lock.** `async_probe_last_slot_ready` (the probe at
the start of a slot job) reads through the cache without the lock. It can fill
the cache between the clear and the dump. It reads two slots of the power
sources, which the history run reads over the whole window anyway, and the cache
merges both into one span, so the snapshot is the same; a test shows it
(`TestTheSlotJobsProbeDoesNotChangeWhatTheFileHolds`). It was considered to give
the probe a storage without memory during an export; not done: more code for a
read that cannot change the file.

**A failing run, a failing export.** The hook is called only when the run has
written. A recalculation that raises produces no file (nothing partial: the file
is written to `name.tmp` and renamed). A hook that raises is caught in
`history._run_export`, logged once with the reason (`WARNING`), shown in the
timing line (`debug export failed after … s (reason)`), and changes nothing of
the run's result; `snapshot()` only reads, so the cache is as usable as after
any run. A cancellation is not swallowed.

**How it is switched on** (human decision 2026-11-03: **A**).

| Option | Cost |  |
| -- | -- | -- |
| A. Entry option "write a debug export with every history recalculation", default off | one field in the config and options flow (en, de); the entry reloads when it changes, which empties the cache (harmless: the next press empties it anyway) | **chosen** |
| B. A second button | a second entity for a diagnostic; two buttons to explain | not chosen |
| C. A service parameter | no entity, but the human presses a button, not a service; the file name cannot be asked for | not chosen |
| D. Always | a file per press, about 4 MB (below) each; rejected on its face | rejected |

How it records: **clear, run, write the snapshot** (the human's idea). The
recording decorator was not built: it needs no clean cache, but keeps every
overlapping read twice, and the clean cache is exact (TASK-0036's test shows the
snapshot equals the set of reads).

**Content.** `format_version` 1, then:

| Section | Needed to replay because | Default | Size on the Pi (estimate) |
| -- | -- | -- | -- |
| `meta` | Effy and Home Assistant versions, time zone, written_at, `omitted`, `max_bytes`, `section_bytes` | in | under 1 KB |
| `config` | the entry's options: the roles, `max_history_days`, SoC and capacity, the hysteresis | in | under 1 KB |
| `inputs` | `ran_at` (what the run took for "now": the clock), `window_start`, and the volatile last-valid-reading cache of ADR-015 as it was **before** the run (it decides whether an anchor is read from the recorder) | in | under 1 KB |
| `result` | rows written and `recalculated_from`, what the replay must return | in | under 1 KB |
| `cache` | `Cache.snapshot()`: raw states, statistics rows, anchors, capacity states, and the *observed* kinds: the units, state classes, retention, statistics units, and the **stored rows that decided what was written** (ADR-024) | in | the 4,804 raw states are about 0.5 MB as JSON; 28 days of one power source about 0.7 MB |
| `written` | the rows the writer scheduled, per statistic and table (`5minute`, `hour`), the expected result of the replay | in | the first press after a change of settings: all rows, about 2.6 MB for 5 series; a repeated press writes only the changed rows (ADR-024), so almost nothing |

Estimates from row counts and 60 to 100 bytes per compact JSON row, **not
measured on the Pi**; the indentation added on 2026-11-04 (human request: line
breaks and indentation, so the file is readable) makes the file a few times
larger. The bound is 50 MiB of JSON as written; a section that would exceed it
is left out whole (the cache first kept, then `written`), named in
`meta.omitted` and in the log, never cut. A file without `cache` cannot be
replayed and says so.

**Format.** Plain JSON (`.json`) with line breaks and two-space indentation
(changed 2026-11-04 from gzip with compact separators; `loads` still reads a
gzip file), written by the pure `debug_export.py` (no `hass`). Datetimes are ISO
8601 in UTC with the offset, microseconds kept; states are the strings the
recorder returned (`unavailable`, `unknown` too), never parsed. A sample
(shortened):

```json
{
  "format_version": 1,
  "meta": {
    "effy_version": "1.0.0",
    "ha_version": "2026.10.4",
    "time_zone": "Europe/Berlin",
    "written_at": "2026-11-03T12:00:09.100000+00:00",
    "omitted": [],
    "max_bytes": 52428800,
    "section_bytes": {
      "config": 310,
      "inputs": 120,
      "result": 60,
      "cache": 812345,
      "written": 2650000
    }
  },
  "config": {
    "pv_input_sensors": [
      "sensor.pv"
    ],
    "charge_sensor": "sensor.charge",
    "max_history_days": 28
  },
  "inputs": {
    "ran_at": "2026-11-03T12:00:07.123456+00:00",
    "window_start": "2026-10-06T12:00:07.123456+00:00",
    "energy_reading_cache": {
      "sensor.pv": [
        "2026-11-03T11:55:00+00:00",
        "100.5"
      ]
    }
  },
  "result": {
    "rows_written": 24192,
    "recalculated_from": "2026-10-06T12:00:00+00:00"
  },
  "cache": {
    "version": 1,
    "raw": {
      "sensor.pv": {
        "lo": "\u2026",
        "obtained": "\u2026",
        "base": [
          "100.0"
        ],
        "events": [
          [
            "2026-11-03T11:50:12+00:00",
            "100.4"
          ]
        ]
      }
    },
    "capacity": {},
    "stats": {},
    "anchors": [],
    "observed": {
      "units": {
        "sensor.pv": "kWh"
      },
      "retention_days": 10,
      "\u2026": "\u2026"
    }
  },
  "written": [
    {
      "statistic_id": "sensor.effy_pv",
      "unit": "W",
      "table": "5minute",
      "rows": [
        {
          "start": "2026-11-03T11:50:00+00:00",
          "mean": 412.5,
          "state": 412.5
        }
      ]
    }
  ]
}
```

**Where.**
`<config>/effy_debug/effy-export-<UTC time of the run>-<8 characters of the entry id>.json`,
written to `name.tmp` and renamed. The newest 5 per entry are kept
(`KEEP_FILES`); only names of exactly this shape are ever removed, other files
in the directory and other entries' exports are left alone; a file that cannot
be removed is a warning. The log line (part of the timing record) names the
path, the size and the time. Nothing is served over HTTP: the human fetches the
file with the file editor, Samba or SSH. The write runs in an executor job (the
snapshot is a copy made on the loop, so the job shares nothing with it).

**Privacy.** The file contains entity ids, the energy and power readings of the
configured sources with their times, the configuration and Effy's own statistics
ids; no secrets or credentials (it never touches the config entry's `data`). The
human reads it before sharing it. **Pseudonymized entity ids: not built** (human
decision 2026-11-03: never). Whoever shares a file can replace the ids by hand
in the JSON, consistently, and the replay still works, since it never looks at
the names.

**The replay.** `tests/replay.py`: `debug_export.loads(bytes)` (refuses a
foreign format version, a damaged file, a missing section; the message names the
version), then `replay(document, monkeypatch)`: the clock frozen at
`inputs.ran_at`, a fresh in-memory recorder,
`Cache.from_snapshot(document["cache"], RaisingStorage())` so that a read the
file does not contain fails (`UnrecordedRead`), the reading cache from `inputs`,
and the **real** `async_recalculate_history` and the real writer over it;
`assert_same` compares the rows written with `written` (the same keys exactly,
values within ADR-022's `rel=1e-9, abs=1e-9`) and the result. The in-memory
world of the existing tests (`_World`) is not needed: the file is the world. To
change the way rows are looked up, a datetime's `repr` in the key of the
stored-rows record had to go: `Cache._rows_key` now uses instants
(`timestamp()`), because the `tzinfo` of a recorded run need not be the one a
replay builds.

**The slot job** gets no export (human decision 2026-11-03: button only).

**Decisions made here** (beyond the human's three): file format JSON+gzip, bound
50 MiB, 5 files per entry, the directory `effy_debug`, the key `format_version`
(the literal `schema_version` belongs to `effy_exports.py`), the export taken
after the history run and before the rebuilds, the amendment to ADR-025 rather
than a new ADR.

### Stage 2 — built (2026-11-03)

- `custom_components/effy/debug_export.py` (new, pure): `ExportRecord`,
  `encode`, `loads`, `export_to_file`, `file_name`, `files_to_remove`,
  `ExportError`, `ExportOutcome`.
- `history.py`: the `export` keyword of `async_recalculate_history`,
  `_run_export`, the `_active_export` record the writer fills, `note_export` in
  the timing line.
- `cache.py`: the stored-rows key by instants.
- `coordinator.py`: `_debug_export_hook`, `_export_meta`, the option check under
  the lock. `const.py`, `config_flow.py`, `translations/en.json`, `de.json`: the
  option `debug_export`.
- `tests/replay.py`, `tests/test_debug_export.py` (new), additions to
  `tests/test_coordinator_slot.py` and `tests/test_config_flow.py`.
- README "Debug export", GLOSSARY (*Debug export*, *Replay*), ADR-025 amendment.

**Deviations from the acceptance text, and why.**

1. *The capacity entity is not in the round trip of a history run.* The history
   recalculation does not read it (the battery rebuild, which follows it, does).
   Its states are proven to survive the file (snapshot, file, `from_snapshot`,
   equal reads); a replay of the battery rebuild is a follow-up if the human
   wants the export to cover the rebuilds.
1. *The tests were written together with the code, not shown failing first*; the
   mutants below stand in, as in TASK-0036.
1. *No measurement on the Pi*: the sizes are estimates.

**Mutants tried and killed:** a section emptied (`inputs`: 10 tests failed);
datetimes written without their offset (10); a raw read around the cache (7, in
this task's and TASK-0036's tests); the reading cache not recorded, the hourly
rows not noted by the writer, and the `try` of the export hook narrowed so a
failure propagates (tried together: 7 tests failed, each of the three named by
its own test); the pruning loosened to any name with the prefix (2); the option
ignored by the coordinator (4). The whole suite has 1582 tests; `ruff`, `mypy`
pass; `mdformat` was run over the changed Markdown.

**Open for the human.** Switch the option on, press the button once, send the
log line (path, size, time) and the file's size; replay it on another machine
(README "Debug export"). Confirm the amendment of ADR-025 and the deviations.

## Delivered Artifacts

- `custom_components/effy/debug_export.py` → `ExportRecord`, `ExportOutcome`,
  `ExportError`, `encode`, `loads`, `export_to_file`, `file_name`,
  `files_to_remove`, `entry_tag`, `SCHEMA_VERSION` (the value of
  `format_version`), `DIRECTORY`, `KEEP_FILES`, `MAX_BYTES`
- `custom_components/effy/history.py` → the `export` keyword of
  `async_recalculate_history`, `ExportHook`, `_run_export`, `_active_export`,
  `_PhaseTimings.note_export`
- `custom_components/effy/cache.py` → `Cache._rows_key` by instants
- `custom_components/effy/coordinator.py` →
  `EffyCoordinator._debug_export_hook`, `_export_meta`; `const.py` →
  `CONF_DEBUG_EXPORT`; `config_flow.py`, `translations/en.json`, `de.json` → the
  option
- `tests/replay.py` → `replay`, `assert_same`, `written_rows`, `debug_export`
  (the module loaded by path); `tests/test_debug_export.py`;
  `TestTheDebugExportOfTheButton` in `tests/test_coordinator_slot.py`;
  `TestDebugExportOption` in `tests/test_config_flow.py`
- `README.md` (Debug export), `GLOSSARY.md` (Debug export, Replay), `adr/025`
  (Amendment 2026-11-03)
- External dependencies added: none (`json`, `gzip`)
