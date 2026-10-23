# ADR-022 – Measure the History Recalculation, Then an Array-Native Compute Path

**Date:** 2026-10-19 **Status:** Draft — not yet reviewed by the human. Nothing
below is a decision until the human confirms it; open points are listed at the
end (Q1–Q5). Terms follow `GLOSSARY.md`.

---

## Context

The human reports that a history recalculation with the default 28 history days
takes about **5 minutes on a Raspberry Pi 5** (2026-10-19). Effy cannot say
where those minutes go: `history.py` contains no timing at all.

A history recalculation (`async_recalculate_history`) has three phases:

1. **Fetch.** Raw state history of every energy-family source, with the staged
   offline-anchor lookback of ADR-014/015, plus `statistics_during_period` for
   power-family sources. All of it runs through the recorder's executor
   (`recorder.async_add_executor_job`), that is, database reads.
1. **Compute.** `_compute_effective_slots`: `trapezoidal_slot_contributions` per
   energy-family source (ADR-012), gap interpolation of power-family inputs
   (ADR-013), then a loop over every slot that builds `SensorReading` objects
   and runs `distribute_loss` (ADR-001). This code is plain synchronous Python
   inside a coroutine, so it runs **on the event loop**, with no await between
   the first and the last slot.
1. **Write.** `_write_recorder_statistics` three times, then
   `async_block_till_done` (ADR-003/004), that is, database writes.

### Evidence from a benchmark (synthetic data, not the Pi)

`bench_numpy.py` (an evaluation script, not part of the repo) imports Effy's
unmodified `calculation.py`, `sensor_utils.py` and `history.py` with Home
Assistant stubbed, and compares them with numpy versions of the same algorithms.
Data: 4 inputs and 2 outputs shaped like a PV + battery + grid system; 28 days
are 8,064 slots and 182,741 raw counter states (about 1.3k states per sensor and
day). Machine: one x86 sandbox core, noisy (one stage measured 108 ms in one run
and 210 ms in another); speed-ups are therefore ranges, not constants.

| Range | Pure Python | Drop-in numpy | Native numpy (arrays out) | Native + lists out |
| -- | -- | -- | -- | -- |
| 2.4 h (the recent recalculation) | 5.1 ms | 2.0 ms | 1.6 ms | 1.7 ms |
| 28 days (default history days) | 803 ms | 173 ms (4.6×) | 99 ms (8.1×) | 108 ms (7.4×) |
| 365 days (maximum) | 11.6 s | 3.0 s (3.9×) | 1.4 s (8.5×) | 1.5 s (7.6×) |

- *Drop-in* swaps two stages for numpy and keeps today's dict-of-dicts and list
  interfaces between stages; *native* keeps arrays on one shared slot grid from
  the trapezoid to the end.
- In pure Python the trapezoid is about 86 % of the compute phase, the per-slot
  loop about 13 %, the interpolation under 1 %. In the native version the string
  parsing of the raw states is about 75 % of the remaining time at 7 days and
  more; the waterfall arithmetic is 2–3 %.
- First reading of the parse probe (TASK-0010; the same synthetic 28 days
  through the real `async_recalculate_history`, real clock, same sandbox, two
  runs, figures rounded to 0.01 s): one pass costs about 0.01–0.02 s for the
  state strings and 0.02–0.03 s for the timestamps, against 0.53–0.66 s of
  compute, that is about 2–3 % and 4 % of today's pure-Python compute phase. The
  75 % above is parsing's share of the *native* path, where everything else got
  faster, not of today's code.
- `distribute_loss` called once per slot through numpy is 16–25 times *slower*;
  the gain exists only batched across slots.
- `import numpy`: 72 ms and +16 MB resident (bare interpreter 9 MB).
- Equivalence: the waterfall kernel matches `distribute_loss` on 60,000 fuzzed
  rows (ties, negatives, zeros, loss above the sum of the active inputs); the
  trapezoid returns identical slot keys, values within 1.4e-10 kWh; the native
  pipeline returns identical slot lists, worst difference 1.6e-6 W (a `pv_west`
  value in W: the trapezoid's rounding multiplied by 12 × 1000). **2 of 18,136
  compared values lay outside `pytest.approx`'s defaults** (rel 1e-6, abs
  1e-12), all near zero.

### The evidence does not explain 5 minutes

The same compute phase costs about 0.8 s in the benchmark. Three explanations
remain, and they call for different remedies:

| Explanation | What would be true | Remedy it points to |
| -- | -- | -- |
| Compute dominates | many more raw states than the synthetic data (a sensor that updates every few seconds) on a CPU several times slower than the sandbox's | this ADR's array-native path |
| Fetch dominates | recorder reads (SQLite, flash storage, staged lookbacks up to the full history) | query shape, fewer or smaller reads — a different ADR |
| Write dominates | three `async_import_statistics` batches and `async_block_till_done` | write batching — a different ADR |

An illustration, not a finding: 0.8 s × a 5× slower CPU × 50× more states would
already be 200 s. Nothing here measures any of the three.

### Constraints the change touches

- ADR-000 §3: `calculation.py` is pure, imports no other Effy module and no Home
  Assistant module; §6: it is tested with zero mocking and `pytest` only, loaded
  by file path. Today the manifest has no `requirements` and
  `tasks/DEPENDENCIES.md` lists no runtime library.
- ADR-011/014: after every slot job, the completed slots written equal a history
  recalculation at the same time. The harness that enforces this
  (`tests/test_history_recent_equivalence.py`, `_equivalent`) compares with
  `pytest.approx` at its **defaults**.
- ADR-012/013/014: the semantics of jumps, windows, zero-fill and the 120-minute
  cap stay as they are.

---

## Decision (proposed)

1. **Measure first (TASK-0010).** Each recalculation logs one INFO line with the
   wall time of its three phases and the sizes behind them: raw states fetched,
   recorder queries issued, slots computed, rows written. No behaviour change.
   It covers the history recalculation (INFO) and the recent recalculation
   (DEBUG). The history recalculation also runs a **parse probe**: one extra
   pass over all raw states, timed on its own and kept out of the compute
   figure, once converting the state strings and once the timestamps to epoch
   seconds. Effy parses each state in the trapezoid and, because the coordinator
   passes its reading cache, once more in the fetch helper, so the probe is the
   cost of one such pass. The probe is a diagnostic and can be removed once gate
   G-perf has been decided. The human runs it on the Pi and reports the line.

1. **Decision gate G-perf.** The numpy work (TASK-0011 to TASK-0013) is released
   only if that line shows the compute phase at or above a threshold (Q1). If
   fetch or write dominates, no numpy work is released and a new Draft ADR
   addresses the dominating phase; this ADR then records the measurement and
   ends.

1. **Off the event loop (TASK-0014), independent of numpy.** The compute phase
   is split from the fetch and write phases and runs as one executor job on
   plain data (no `hass`, no recorder). It is released if the compute phase
   blocks the event loop for longer than a threshold (Q2). The lock of ADR-011
   Amendment 2026-10-16 is unchanged.

1. **If released: an array-native compute path.** One slot grid per
   recalculation; the trapezoid writes into array columns; interpolation, unit
   conversion and the waterfall work column-wise on that grid; the result is
   converted to the `(datetime, value)` lists the writer consumes once, at the
   end (about 10 % of the native time). The input stays what the recorder hands
   over today (raw state strings, statistics rows). The waterfall is the same
   algorithm: same ascending sort, same equal-share rule, same residual rule,
   vectorized across slots and looped across the few inputs.

1. **Placement.** A new module `custom_components/effy/calculation_grid.py` at
   the layer of `calculation.py`: pure, no Home Assistant import, no I/O, the
   only third-party import is numpy. It may import `calculation` (constants);
   `calculation` never imports it. ADR-000 §3's diagram gains this module; §6
   applies to it unchanged (zero mocking, file-path load; numpy joins the dev
   dependency group).

1. **The pure-Python functions stay the reference.**
   `trapezoidal_slot_contributions`, `interpolate_slot_gaps` and
   `distribute_loss` are the oracle the grid module is tested against (fuzz and
   scenario tests). What happens to them in production code after the cut-over
   is Q3.

1. **Tolerance policy.** Slot keys are compared exactly. Values are equal within
   `abs=1e-9` in the series' own unit (and `rel=1e-9`); a test that asserts a
   value near zero names its tolerance. The harness's `_equivalent` gets that
   explicit tolerance in the task that wires the path in. TASK-0011 first
   compares two formulations of the trapezoid and keeps the one with the larger
   margin that is still fast: the cumulative-difference form benchmarked above
   (its rounding depends on how long the window is) and a per-transition overlap
   form (local arithmetic; **not tried yet**).

1. **Dependency.** numpy is declared in `manifest.json` `requirements`, pinned
   as TASK-0011's investigation finds (Home Assistant's own constraint at the
   minimum version 2026.3), recorded in `tasks/DEPENDENCIES.md`, added to the
   dev group and `uv.lock`. What happens when the import fails is Q4. The human
   verifies the install on the Pi.

1. **Out of scope, unchanged:** the loss model (ADR-001/005), the statistics
   Effy writes (ADR-003/004), the rewrite range (ADR-014), the published
   attributes (ADR-021), anything Shady or Chargy see. The benchmark script is
   not shipped in the repo; its numbers are recorded above.

## Options considered

| Option | Result in the benchmark | Why not (yet) |
| -- | -- | -- |
| Do nothing | — | the 5 minutes are unexplained and a long blocking compute is possible |
| Measure only | — | it is Decision 1, and gates the rest |
| Executor only (Decision 3) | no speed-up; unblocks the loop | duration unchanged |
| Cheaper parsing in pure Python | **not measured** | parsing is 75 % of the native time, so it may matter more than numpy; a candidate for a later task |
| numpy, drop-in (two stages) | 3.8–5.0× from one day up | keeps both dict/list round trips; 1.6–1.7× slower than native at 28 days |
| numpy, native (Decision 4) | 5.9–9.5× from one day up | needs the interface change and the dependency; gated by G-perf |

## What was verified, and what was not

Verified (sandbox, synthetic data): the equivalence figures and timings above;
`history.py`'s compute phase is synchronous on the event loop (read in code);
the harness compares with default `approx` (read in code).

**Not verified:** any number from a Raspberry Pi; the real raw-state volume; how
the 5 minutes split into fetch, compute and write; the effect of a blocking
compute inside a running Home Assistant; whether Home Assistant's pin of numpy
and the wheel for the Pi's platform install from a custom integration's
`requirements`; the per-transition trapezoid form.

## Consequences

- Decision 1 costs one log line per recalculation and answers the question that
  decides everything else.
- If the numpy path is released: a runtime dependency (+16 MB, about 70 ms at
  load on the benchmark machine), a second module to keep equivalent to the
  reference, an interface change inside `history.py`, and a tolerance rule where
  the invariant tests today use the default one.
- If Q3 chooses one implementation, the recent recalculation also gets faster
  (5.1 → 1.7 ms) and both paths agree to the same rounding; the price is that
  the slot job loads numpy too.

## Open points for the human

**Q1 — Release threshold for the numpy work.** *A:* compute phase at least 30 s
on the Pi **or** at least 25 % of the history recalculation's wall time. *B:* a
different value. Recommendation: A; below it, numpy costs more than it saves.

**Q2 — Release threshold for the executor job.** *A:* compute phase blocks the
event loop for more than 1 s on the Pi. *B:* always, whatever the measurement
(it needs no dependency). Recommendation: A; the split changes the concurrency
model of the compute phase and should earn its place by a measurement.

**Q3 — One implementation or two?** *A:* the grid module replaces the
pure-Python stages in production, which move to `tests/` as the oracle; the slot
job and the history recalculation both use numpy. *B:* the history recalculation
uses numpy, the slot job keeps pure Python, the oracle stays in
`calculation.py`; the two paths then differ at rounding level and the harness
tolerance is what keeps them honest. Recommendation: A.

**Q4 — If `import numpy` fails at load.** *A:* the integration does not load and
says so clearly (Home Assistant installs `requirements` before loading, so this
is a broken install). *B:* fall back to the pure-Python path (requires Q3 = B).
Recommendation: A with Q3 = A.

**Q5 — Order if both gates open.** *A:* executor (TASK-0014) first, then numpy.
*B:* numpy first, then re-measure, then decide the executor from the new number.
Recommendation: B, because a 5× faster compute may make the executor
unnecessary.
