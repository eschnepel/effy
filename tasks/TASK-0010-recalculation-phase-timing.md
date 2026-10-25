# Task: Time the Three Phases of a Recalculation

- **Status:** done (2026-10-19) — Gate 2 for this task approved by the human
  ("Proceed with instrumentalisation"); the other CAP-6 tasks stay gated by
  G-perf
- **Related ADRs:** [ADR-022, ADR-011, ADR-012, ADR-014, ADR-015]
- **Dependencies:** []

## Goal

Every recalculation logs how long its fetch, compute and write phases took and
how much data went through them, so the human can see where the 5 minutes of a
history recalculation on the Raspberry Pi 5 go. No behaviour changes: the same
statistics are written, the same values returned. The numbers feed gate G-perf
of ADR-022. At the human's request (2026-10-19) the cost of parsing the raw
states is measured separately, as far as that is possible without changing the
calculation: by a probe, not inline (see below).

## Acceptance Criteria

**Phases.** Test with an injected clock (a fake that advances by known amounts)
and the existing fake-recorder world of
`tests/test_history_recent_equivalence.py`.

- Given a history recalculation, when it finishes, then exactly one INFO record
  from `custom_components.effy.history` is logged, and it names `total`,
  `fetch`, `compute` and `write` with seconds, and `other` for the rest.
- Given the fake clock, then the time spent awaiting a recorder executor job in
  the fetch path (unit lookup, raw states, offline-anchor lookbacks,
  power-family statistics) is counted as `fetch`; the synchronous segments of
  `_compute_effective_slots` (jump windows, the trapezoid loop, the
  interpolation, the slot loop) as `compute`; `_write_recorder_statistics` as
  `write`; and the three plus `other` equal `total`.
- Given a recent recalculation with a jump-extended refetch, then the base fetch
  and the refetch both count as raw queries and towards `fetch`.

**Sizes.** The same record carries:

- `queries`: the number of recorder executor jobs the recalculation issued;
- `raw_states`: raw state entries held after the last fetch, over all
  energy-family sources;
- `slots`: distinct slots in `[start, end)` that have at least one value;
- `rows`: the short-term rows written (the existing return value).

Test: the fake world has known numbers of sources, states and slots; the record
shows exactly those.

**Parse probe (history recalculation only).** Parsing happens inside
`trapezoidal_slot_contributions` (in `calculation.py`, which must not change)
and once more in the fetch helper, so it cannot be timed inline. Instead:

- Given a history recalculation, then after the trapezoid loop one extra pass
  runs over every raw state held, once with `_parse_energy_state` (`values`) and
  once converting each timestamp to epoch seconds (`timestamps`), each timed on
  its own, and the record shows both.
- Given the record, then the probe time is its own term (`parse probe`) in the
  sum, never part of `compute`, and the record says that it is one extra pass.
- Given a failure inside the probe (a timestamp out of range), then the
  recalculation still completes with unchanged results and still logs.
- Given a recent recalculation, then no probe runs.
- Given the probe, then what is written is identical to a run without it.

**Recent recalculation.** Given a recent recalculation, then the same record is
logged at DEBUG, not INFO (it runs every 5 minutes), without the probe.

**No behaviour change.**

- Given the whole existing test suite, then it passes unmodified.
- Given `_compute_effective_slots` called without the new optional parameter
  (every existing call), then it behaves as before, records nothing and logs
  nothing, and the shared disabled instance keeps no state.
- Given the TASK-0004 harness, then timer-path and history-recalculation results
  are equal exactly as before.

**Reporting (human step, not a worker step).** The task's final note tells the
human how to read the record (README subsection) and what to send back: the INFO
line of one history recalculation on the Pi, `max_history_days`, the number of
configured sources, and the Home Assistant version.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/history.py`: a private timing record (injectable
  clock), a small wrapper that counts and times recorder executor jobs, the
  wiring in `_compute_effective_slots`, `_fetch_energy_raw_with_anchor`,
  `_write_recorder_statistics`, `async_recalculate_history` and
  `async_recalculate_recent`.
- `tests/test_history_phase_timing.py` (new).
- `README.md`: one subsection, "How long did a recalculation take?".
- `GLOSSARY.md`: **Recalculation phase**, **Parse probe**.

## Definition of Done

- Tests green · `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`,
  `mdformat --check` clean · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependency (`tasks/DEPENDENCIES.md` untouched)

## Consumed Interfaces

<!-- Filled by the Lead Agent before implementation. No dependency task; the
     symbols below exist today (verified 2026-10-19). -->

- `_compute_effective_slots`, `_fetch_energy_raw_with_anchor`,
  `_write_recorder_statistics`, `async_recalculate_history`,
  `async_recalculate_recent` from `custom_components/effy/history.py`
- `_World`, `_FrozenDatetime`, `_run_full_recalc` from
  `tests/test_history_recent_equivalence.py` (fake recorder world)

## Delivered Artifacts

- `custom_components/effy/history.py`
  - `_clock` — module attribute (default `time.perf_counter`), read at every use
    so tests can replace it.
  - `class _PhaseTimings(kind: str, level: int, *, parse_probe: bool = False, enabled: bool = True)`
    — a "current phase" stopwatch. Methods: `enter(phase: str | None)`,
    `async recorder_job(recorder, func, *args)`, `note_raw_states(energy_raw)`,
    `probe_parse(energy_raw)`, `total()`, `other()`, `finish(rows: int)`.
    Attributes: `seconds` (`fetch`, `compute`, `write`, `probe`), `queries`,
    `raw_states`, `slots`, `rows`, `parse_values`, `parse_timestamps`.
  - `_NO_TIMINGS` — the disabled instance and default of the new parameter.
  - `_fetch_energy_raw_with_anchor(..., energy_reading_cache, phases=_NO_TIMINGS)`
    and `_compute_effective_slots(..., trigger_since, phases=_NO_TIMINGS)` — one
    new trailing optional parameter each; behaviour without it unchanged.
  - `async_recalculate_history` creates
    `_PhaseTimings("history", logging.INFO, parse_probe=True)`, times the three
    statistics writes as `write`, logs once (also on the early return when
    nothing was found).
  - `async_recalculate_recent` creates `_PhaseTimings("recent", logging.DEBUG)`
    and logs once.
  - Log line (one record, logger `custom_components.effy.history`):
    `Effy recalculation timing (<history|recent>): total X s = fetch X s + compute X s + write X s [+ parse probe X s] + other X s; recorder queries N, raw states N, slots N, rows written N[; parse probe (one extra pass over all raw states, not part of compute): values X s, timestamps X s]`.
- `tests/test_history_phase_timing.py` — 13 tests (stopwatch, record and sizes,
  partition and `other`, anchor lookback as a query, nothing found, parse probe
  and its separation from `compute`, a failing probe, probe changes nothing
  written, recent at DEBUG without probe, jump-extended refetch, no behaviour
  change).
- `README.md` — subsection "How long did a recalculation take?".
- `GLOSSARY.md` — **Recalculation phase**, **Parse probe**.
- External dependencies added: none (`tasks/DEPENDENCIES.md` untouched).

## Review

Inline, by the Lead Agent against the criteria above, after the Worker pass.
**PASS.** Gates: `pytest` 440 passed (427 before), `ruff format --check` and
`ruff check` clean, `mypy` reports exactly the 3 errors that exist on the
untouched tree (`tests/test_catalog.py` ×2, `tests/test_published_sensors.py`
×1) and none new. Mutation checks: seven deliberate faults in the implementation
(probe booked as compute, query not counted, recent logging at INFO, probe
running on the recent path, no record on the early return, writes not timed,
refetch not counted) each make at least one test fail.

Findings during the pass, all fixed: the probe could have raised from
`datetime.timestamp()` and broken the recalculation it measures (now guarded and
tested); one acceptance criterion (jump-extended refetch) had no dedicated test
(added); one criterion was worded confusingly (corrected above).

Known limits, by design: no record is logged if the recalculation raises; the
probe measures the cost of one parse pass, not the inline time; the timestamp
pass measures what an array-based compute path would have to convert (today's
code does datetime arithmetic instead). The probe is a diagnostic that can be
removed once G-perf is decided (ADR-022 Decision 1).

## Gate G-perf (Lead Agent and human, after this task is done)

1. The human runs a history recalculation on the Pi with the `history` logger at
   INFO and sends the record.
1. The Lead Agent compares it with the thresholds the human fixed in ADR-022
   Q1/Q2 and writes the outcome into ADR-022 as an amendment in the text:
   *compute dominates* → TASK-0011 to TASK-0013 (and TASK-0014 per Q2) are
   released for Gate 2; *fetch or write dominates* → they stay `todo`, a new
   Draft ADR addresses that phase, and ADR-022 ends with the measurement.

## Test results

The following shows the debug output on the PI 5:

```
2026-10-24 22:50:18.906 INFO (MainThread) [custom_components.effy_.button] Effy: starting history recalculation (triggered by button)
2026-10-24 22:54:07.228 DEBUG (MainThread) [custom_components.effy_.history] Effy: history recalculation wrote 236112 short-term slots across 6 effective + 4 derived-power + 4 smoothed sensors
2026-10-24 22:54:07.266 INFO (MainThread) [custom_components.effy_.button] Effy: history recalculation complete – 236112 slots written
2026-10-24 22:55:05.028 DEBUG (MainThread) [custom_components.effy_.history] Effy recent recalc: counter jump(s) in the last 0:20:00 extend the rewrite range from 2026-10-24 20:35:05.000176+00:00 back to 2026-10-24 19:35:00+00:00
2026-10-24 22:55:05.265 DEBUG (MainThread) [custom_components.effy_.history] Effy: recent recalculation wrote 222 short-term rows across 6 effective + 4 derived-power + 4 smoothed sensors, earliest touched slot 2026-10-24 19:35:00+00:00
2026-10-24 23:00:05.023 DEBUG (MainThread) [custom_components.effy_.history] Effy recent recalc: counter jump(s) in the last 0:20:00 extend the rewrite range from 2026-10-24 20:40:05.000701+00:00 back to 2026-10-24 19:35:00+00:00
2026-10-24 23:00:05.270 DEBUG (MainThread) [custom_components.effy_.history] Effy: recent recalculation wrote 236 short-term rows across 6 effective + 4 derived-power + 4 smoothed sensors, earliest touched slot 2026-10-24 19:35:00+00:00
```

## Evaluation of the test results (Lead Agent, 2026-10-25)

**The measurement did not happen.** None of the pasted lines is the timing
record. In this branch `finish()` logs it at INFO immediately after the
`history recalculation wrote …` line, from the same logger, and the DEBUG lines
prove INFO was enabled. It is not there, and the logger is
`custom_components.effy_.history`, not `custom_components.effy.history`. Only
the `helper_sensors` branch contains the timing code (checked on all branches).
So the copy that ran on the Pi is another build, presumably from before commit
`f752c55`, and gate G-perf has no input yet.

**What the lines do say** (arithmetic on the timestamps, not instrumentation):

- The history recalculation took 228.3 s (22:50:18.906 to 22:54:07.228), about 3
  min 48 s, not 5 min.
- 236,112 rows across 14 series is 16,865 slots per series, about 58.6 days, not
  the default 28 (that would be about 113,000 rows). `max_history_days` must be
  near 60; please confirm.
- That is 0.97 ms per row written, or 13.5 ms per slot across all series.
- The two recent recalculations took 0.237 s and 0.247 s for 222 and 236 rows
  (1.07 and 1.05 ms per row). Their start line is logged after the first fetch,
  so this is refetch, compute and write of 16 slots.
- Per-row cost is nearly the same for 16 slots and for 16,865 slots, so a fixed
  per-recalculation cost (query count, lock, lookbacks) is at most 0.24 s of the
  228 s. Two points cannot show that the cost is linear, only that a constant
  part is small.

**What they cannot say:** which phase it is. A per-slot-linear cost fits
compute, fetch and write equally.

**Plausibility, calculated and not measured.** The pure-Python trapezoid costs
2.8 µs per raw state on the sandbox, linear from 83,000 to 1,000,000 states.
With the ADR's 86 % share that is about 3.3 µs per state for the compute phase;
at an assumed 3 to 5 times slower Pi, 10 to 16 µs. The series counts (6
effective, 4 derived power, 4 smoothed) suggest 4 energy-family sensors. For
compute to explain 228 s they would need 3.5 to 5.7 million raw states each, one
state per 0.9 to 1.5 s for 58 days. Even Q1's 25 % (57 s) needs one state per
3.6 to 5.9 s per sensor. A battery system's counters rarely tick that fast, so
compute is the less likely explanation. 236,112 short-term rows plus about
19,700 hourly rows at a per-row upsert in the order of 1 ms is what a recorder
import on a Pi can cost (from memory of Home Assistant's import code, not
checked against the 2026.x source), which would make the write phase the more
likely one. The fetch phase (rows turned into state objects) is the third
candidate. This is a ranking of hypotheses, not a finding.

**Decision.** More data is needed, and a second run without more detail would
risk a third. TASK-0010-recalculation-phase-timing-patch-1 adds the sub-process
breakdown, CPU against wall time, the longest stretch without an `await` and the
environment to the same record. Q1 and Q2 are still open points in the Draft
ADR-022 (the text says "nothing below is a decision"); they must be confirmed
before G-perf is decided.
