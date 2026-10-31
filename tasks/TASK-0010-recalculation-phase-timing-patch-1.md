# Task: Break the Three Phases Down into Sub-Processes (Detail Block)

- **Status:** done (2026-10-31; closed by the human, no further Pi run needed:
  gate G-perf was decided on 2026-10-25 and TASK-0030 met its target on the Pi
  on 2026-10-27). Implemented inline, reviewer pass PASS; Gate 2 given by the
  human's request of 2026-10-25 ("if you need more data, add an additional
  instrumentalisation").
- **Related ADRs:** \[ADR-022, ADR-011, ADR-012, ADR-014, ADR-015, ADR-003,
  ADR-004\]
- **Dependencies:** [TASK-0010-recalculation-phase-timing]

## Goal

The human's first run on the Raspberry Pi 5 (2026-10-24, recorded in TASK-0010's
"Test results") took 228 s for a history recalculation but carried no timing
record, so gate G-perf cannot be decided. This patch makes the next run answer
more than "which of the three phases": the record also says where inside each
phase the time goes, whether it is spent computing or waiting, and which build
and machine produced it. Only measurement is added: the same statistics are
written, the same values returned, and the first line of the TASK-0010 record
keeps its form.

## Acceptance Criteria

Test with the injected clocks of `tests/test_history_phase_timing.py` (wall and
CPU are separate fakes) and the same fake recorder world.

**Compatibility.**

- Given the whole existing test suite, then it passes unmodified. In particular:
  the constructor of `_PhaseTimings` takes no new required or keyword argument,
  and `_write_recorder_statistics` keeps its signature (the fake world replaces
  both).
- Given a history recalculation, then there is still exactly one INFO record
  from `custom_components.effy.history` that contains
  `recalculation timing (history)`. Its first line is the TASK-0010 line; the
  detail block follows on further lines of the same record.
- Given a recent recalculation, then its record is the TASK-0010 one-liner at
  DEBUG, with no detail block.

**Detail block (history recalculation only).**

- *Environment.* Given the record, then its first detail line names the record
  version (`detail v2`), `history days`, `energy sources`, `power sources`, the
  Home Assistant version, Python version and machine, the number of CPUs, the
  recorder's database dialect and `keep_days`. Anything that cannot be read is
  `unknown`; reading it never raises.
- *Fetch jobs.* Given a history recalculation, then every recorder executor job
  is listed per kind (`units`, `raw states <entity>`,
  `anchor lookback <entity>`, `power statistics`) with the number of calls,
  `wall` (what the coroutine waited), `run` (the time inside the executor job)
  and `wait` (wall minus run: queueing for the executor), and the rows returned.
  The walls add up to `fetch`.
- *Compute stages.* Given a history recalculation, then the record lists, per
  energy-family source, `trapezoid` (seconds, states in, slots out); per
  power-family input, `interpolation` (seconds, slots in); per source, the
  `cache pass` of the fetch helper (seconds, states) when a reading cache is
  passed; and the `slot loop` (seconds, slots) split into `readings`,
  `waterfall`, `conversion` and `rest`. The stages are carved out of `compute`;
  none is added to the total, and `compute` equals what a run without the detail
  reports.
- *Write calls.* Given the three statistics writes, then each is listed by name
  (`effective`, `derived power`, `smoothed`) with its short-term rows, its
  hourly rows, `build` (preparing and scheduling the imports on the event loop)
  and `recorder` (awaiting `async_block_till_done`). `build` plus `recorder` of
  the three add up to `write`.
- *CPU against wall.* Given the record, then for `fetch`, `compute` and `write`
  it shows the CPU seconds the process used next to the wall seconds, labelled
  as process-wide (it includes anything else Home Assistant did meanwhile). CPU
  far below wall means waiting (storage, queues), CPU near wall means computing.
- *Longest stretch.* Given the record, then it shows the longest time the
  recalculation held the event loop without awaiting (ADR-022 Q2's number), over
  the whole call including the first and the last stretch.

**Robustness.**

- Given a recorder job whose result cannot be counted (`None`, a number), then
  the recalculation still completes and the row count is 0 or 1.
- Given an environment lookup that fails, then the record says `unknown` and the
  recalculation completes.
- Given the detail, then what is written is identical to a run without it, and
  the shared disabled instance `_NO_TIMINGS` keeps no state through any of the
  new methods.

**Reporting (human step, not a worker step).** The README subsection says how to
read the block, that a missing `Effy recalculation timing` line means the
running copy does not contain TASK-0010 (check the logger name in the log:
`custom_components.<folder>.history`), and to send back the whole record.

## Estimated File / Module Footprint (hint, not a commitment)

- `custom_components/effy/history.py`: extend `_PhaseTimings` (job wrapper,
  `stage`, `wrap`, write notes, CPU clock, await bracketing, detail text); a
  context variable that lets the real `_write_recorder_statistics` find the
  active stopwatch without a new parameter; wiring in
  `_compute_effective_slots`, `_fetch_energy_raw_with_anchor`,
  `async_recalculate_history`.
- `tests/test_history_detail_timing.py` (new).
- `README.md`: extend "How long did a recalculation take?".
- `GLOSSARY.md`: **Recalculation detail**.
- `adr/022-measure-then-array-native-history-compute.md` (Draft): Decision 1
  names the detail block; `tasks/adr-summary.md` likewise.

## Definition of Done

- Tests green · `ruff format --check`, `ruff check`, `mypy`, `pytest`,
  `mdformat --check` clean (mypy: no error beyond those on the untouched tree) ·
  docs updated · no open ADR conflicts
- The instrumentation's own cost is measured on a large synthetic run and
  reported in the review
- `Delivered Artifacts` block completed and accurate
- No new external dependency (`tasks/DEPENDENCIES.md` untouched)

## Consumed Interfaces

<!-- From TASK-0010's Delivered Artifacts, verified 2026-10-25. -->

- `_clock`, `_PhaseTimings`, `_NO_TIMINGS`,
  `_fetch_energy_raw_with_anchor(..., phases=)`,
  `_compute_effective_slots(..., phases=)`, `async_recalculate_history`,
  `_write_recorder_statistics` from `custom_components/effy/history.py` (→ task:
  TASK-0010-recalculation-phase-timing)
- `_Clock`, `_rig`, `_world`, `_history_recalc`, `_timing_records`, `_figure`
  from `tests/test_history_phase_timing.py`; `_World`, `_install`, `_options`,
  `_energy_world` from `tests/test_history_recent_equivalence.py`

## Delivered Artifacts

- `custom_components/effy/history.py`
  - `_cpu_clock` (default `time.process_time`) and `_RECORD_VERSION = 2` —
    module attributes, read at every use.
  - `class _Figure`, `class _WriteFigure` — accumulators of the detail block.
  - `_rows_of(result) -> int`, `_environment_summary(hass, history_days) -> str`
    (never raises).
  - `_PhaseTimings`: new attributes `cpu`, `detail`, `environment`,
    `energy_sources`, `power_sources`, `jobs`, `stages`, `parts`, `writes`,
    `longest_block`; new methods `detail_on(environment)`,
    `note_sources(energy, power)`, `before_await()`, `after_await()`,
    `stage(name, source="", *, items=0)` (context manager yielding a figure with
    `.out`), `wrap(name, func)`, `write_call(label)` (context manager),
    `note_write(short, hourly, build, wait)`, `_detail_lines()`;
    `recorder_job(recorder, func, *args, label="", source="")` gained two
    keyword-only parameters. The constructor is unchanged; `seconds` keeps its
    four keys.
  - `_active_timings: ContextVar[_PhaseTimings]` (default `_NO_TIMINGS`), set
    only by `write_call`; the real `_write_recorder_statistics` reads it, so its
    signature is unchanged.
  - Wiring: labels on the four kinds of recorder job; stages `trapezoid`,
    `interpolation`, `cache pass`, `slot loop` (with parts `readings`,
    `waterfall`, `conversion`); `async_recalculate_history` calls
    `detail_on(_environment_summary(...))` and names its three writes
    `effective`, `derived power`, `smoothed`. `async_recalculate_recent` is
    untouched.
  - Record: first line as before; then `detail v2: …`, `cpu / wall …`,
    `longest stretch …`, `fetch jobs`, `compute stages`, `slot loop`,
    `write calls`, all in the same INFO record.
- `tests/test_history_detail_timing.py` — 35 tests.
- `README.md` — "How long did a recalculation take?" extended (detail block, how
  to read it, what a missing line means). `GLOSSARY.md` — **Recalculation
  detail**. Draft ADR-022 Decision 1 and `tasks/adr-summary.md` name the block.
- External dependencies added: none (`tasks/DEPENDENCIES.md` untouched).

## Review

Inline, by the Lead Agent against the criteria above, after the Worker pass.
**PASS.** Gates: `pytest` 591 passed (556 before; the 556 unmodified),
`ruff format --check` and `ruff check` clean, `mypy custom_components tests`
clean (34 files), `mdformat --check` clean on the edited files (run with the
`exclude` key of `.mdformat.toml` removed temporarily: it needs Python 3.13+,
the sandbox has 3.12).

Two compatibility traps found while planning and designed around, not patched in
the tests: an existing test replaces `_PhaseTimings` with a factory that accepts
only `parse_probe` and `enabled` (so detail is switched on by `detail_on`, not
by a constructor argument), and the fake world's writer has the old signature
(so the real writer finds the stopwatch through a context variable).

Mutation checks: twelve deliberate faults (executor run time not recorded,
`wrap` always wrapping, build including the recorder wait, the await not
bracketed, CPU never booked, last stretch dropped, a recent record with detail,
the context variable never reset, the cache pass without a cache, an environment
lookup that raises, stage time double-counted into `compute`, `detail_on`
changing the disabled instance) each make at least one test fail. Two survived
the first round (context variable reset; the environment's outer fallback); two
tests were added and all twelve are caught.

Instrumentation cost, measured: 4 energy sensors with 483,840 states and 8,064
slots, `_compute_effective_slots` with the real clock, best of five: 1.507 s
without detail, 1.513 s with it (+0.006 s, +0.4 %, inside the run-to-run noise).
The wrappers around the slot loop's three calls land in `rest`, not in the
parts.

Known limits, by design: no record is logged if the recalculation raises; CPU
seconds are process-wide and include whatever else Home Assistant does
meanwhile; `run` is the time inside the executor job, so the SQL query and the
conversion of rows to state objects inside `state_changes_during_period` are not
separated; the recorder thread's own time per import is not visible (only the
wait for it as a whole), because separating it would need a wait after every
import, which changes the pipelining; nothing was run against a real Home
Assistant or on the Pi.
