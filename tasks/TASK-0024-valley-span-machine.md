# Task: The pure valley and span machine for battery efficiency

- **Status:** done — 2026-10-29, reviewer PASS (inline)
- **Capability:** CAP-9
- **Related ADRs:** \[ADR-023 decisions 6, 7, 13 and Open decisions 1 and 2,
  ADR-000 §3/§6\]
- **Dependencies:** [TASK-0019-efficiency-pure-run-machine]
- **Gate:** ADR-023 accepted 2026-10-27. Open decision 1 settled by the human
  2026-10-29 (amendment in ADR-023): **option A**, a battery efficiency above
  100 % is published as it is.

## Goal

A pure machine in `calculation.py` that turns SoC slots, capacity and the charge
and discharge energies into the battery efficiency: the valley zigzag, the
valley spans, Δstored, the capacity check and the decayed ratio shared with
TASK-0019. It also settles the boundary convention by simulation.

## Acceptance Criteria

- Given a SoC series, then a valley is real when SoC has risen to its lowest
  point plus the hysteresis, it ends at the last slot at or below the lowest
  point plus 10 before that rise, and the next valley begins at the first slot
  after the peak at or below its own lowest point plus 10; a dip smaller than
  the hysteresis widens the valley.
- Given a missing SoC slot, then the detection resets and no span contains it.
- Given a closed span, then the battery efficiency measurement is Σ discharge ÷
  (Σ charge − Δstored) with the middle-of-boundary-slot convention, Δstored =
  (SoC_end · capacity_end − SoC_begin · capacity_begin) ÷ 100, and the decayed
  ratio of TASK-0019 with weight Σ charge − Δstored.
- Given a capacity that changes by more than 1 % relative to the span's start
  anywhere inside it, or is unavailable, or a missing charge or discharge
  statistic, or Σ charge − Δstored not above 0, then the span is dropped and
  only the span (no reset of the estimate).
- Given a span, then it closes only when the next valley is real and its
  beginning is 125 min old.
- Given a simulator of a battery of known efficiency over several days with
  wobbles and one capacity change, then the estimate converges to the known
  value and the bias of the boundary convention is below 0.5 points; if not, the
  convention is changed and the change recorded in ADR-023 (Open decision 2).
- Given the Open decision 1 (an estimate above 100 %) as the human decided, then
  the machine does exactly that and a test pins it.
- Given a state dumped and restored between two chunks of slots, then the result
  equals one go; `calculation.py` stays free of Home Assistant.

## Estimated File / Module Footprint (hint, not a commitment)

`calculation.py` (new section: `ValleyState`, `valley_feed`, span accounting,
restore functions), `tests/test_valley_spans.py`, simulator helper shared with
TASK-0019.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- From TASK-0019 (`calculation.py`, no Home Assistant import):
  `DecayedRatio(num, den)`, `decay_add(ratio, gross_kwh, out, inp)`,
  `ratio_value(ratio)`, `EFF_VALIDATION_DELAY` (125 min), `EFF_DECAY_PER_KWH`
  (0.95), `EffEstimate` (the shape of an estimate's attributes), `run_feed` (the
  pattern: time-ordered slots, only those 125 min old enter, `last_slot` guards
  a replay), `run_restore_data` / `run_state_from_restore` (plain-JSON dump,
  malformed data gives `None`), the private helpers `_restore_time`,
  `_restore_number` and `_DRAIN_SLOT` (5 min).
- From TASK-0023 (`const.py`, `inputs.py`): `MIN_VALLEY_HYSTERESIS` (15),
  `DEFAULT_VALLEY_HYSTERESIS` (30), `valley_hysteresis(options) -> float`. The
  machine receives the hysteresis as an argument and never reads options.
- Tests: `tests/test_efficiency_runs.py` (the `_System` simulator pattern: a
  physical model with cumulative counters that tick in 10 Wh; this task builds
  its own battery simulator in the same style, the helper is not importable from
  there because test modules load `calculation.py` by path).
- Filled by the Lead (2026-10-29) from the Delivered Artifacts of TASK-0019 and
  TASK-0023.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

All in `custom_components/effy/calculation.py` (no Home Assistant import), after
`run_state_from_restore`:

- Constants: `VALLEY_BAND` (10.0), `CAPACITY_TOLERANCE` (0.01),
  `SPAN_DROP_MISSING_ENERGY`, `SPAN_DROP_CAPACITY_UNAVAILABLE`,
  `SPAN_DROP_CAPACITY_CHANGED`, `SPAN_DROP_NO_NET_INPUT`, the type `SpanDrop`
  (the four reasons, as the strings `"missing_energy"`,
  `"capacity_unavailable"`, `"capacity_changed"`, `"no_net_input"`).
- Frozen dataclasses:
  `BatSlot(start, soc, charge_kwh, discharge_kwh, capacity_kwh)` (the input;
  every field but `start` may be `None`),
  `SpanSums(start, soc, capacity, charge, discharge, problem)`,
  `SpanPoint(start, soc, capacity, charge, discharge, problem)`,
  `ValleyEstimate(ratio, counted_kwh, counted_spans, last_value, last_span_begin, last_span_end)`
  with property `value` (`float | None`),
  `ValleyState(estimate, low, band, span, peak, records, last_slot, dropped_spans, last_drop)`.
- Functions: `valley_step(state, slot, hysteresis) -> ValleyState` (raises
  `ValueError` for a hysteresis of 10 or less),
  `valley_feed(state, slots, now, hysteresis, delay=EFF_VALIDATION_DELAY) -> ValleyState`,
  `valley_restore_data(state, hysteresis) -> dict` (plain JSON, keys
  `hysteresis`, `estimate`, `low`, `band`, `span`, `peak`, `records`,
  `last_slot`, `dropped_spans`, `last_drop`; a sums object has `start`, `soc`,
  `capacity`, `charge_kwh`, `discharge_kwh`, `problem`),
  `valley_state_from_restore(data, hysteresis) -> ValleyState | None` (`None`
  for unusable data, for another hysteresis, and for a state that processed
  nothing). Private: `_seek_step`, `_span_step`, `_measure_span`,
  `_slot_problem`, `_sums_start`, `_sums_add`, `_point`, `_valid_capacity`,
  `_sums_data`, `_sums_fields`, `_sums_from_data`, `_point_from_data`,
  `_valley_estimate_from_data`.
- Reused, unchanged: `DecayedRatio`, `decay_add`, `ratio_value`,
  `EFF_VALIDATION_DELAY`, `_DRAIN_SLOT`, `_restore_time`, `_restore_number`.
- Tests: `tests/test_valley_spans.py` (99 tests, with the battery simulator
  `_Battery` and the helpers `series`, `feed`, `step_by_step`).
- Not touched: `const.py`, `inputs.py`, translations, README (nothing is visible
  to the user before TASK-0026), `effy_exports.py`.
- External dependencies added: none.

For TASK-0025 (the glue): the machine wants **kWh** for the capacity (convert
Wh), a hysteresis above 10 (the options flow guarantees 15,
`inputs.valley_hysteresis` does not clamp a hand-edited value: the glue must not
pass 10 or less), and slots that are consecutive 5-minute slots in time order; a
slot that is not directly after the last one resets the detection. The state is
the whole of `ValleyState`; dump it with the hysteresis in force.

## Decisions taken inside the task (◇), for the human's confirmation

1. **Slots enter at 125 minutes old, like the pure runs**, so the span "closes
   when the next valley is real and its beginning is 125 min old" holds without
   a clock in the machine; the cost is that a closing trails the rise by up to
   those 125 minutes more than a clock-based rule would. The estimate trails by
   hours anyway (ADR-023 Consequences).
1. **Of several equal highest values, the latest is the peak** (see the
   amendment of Open decision 2).
1. **A reset can leave the first valley partly seen**; its end is still a valid
   span boundary (the energy balance holds for any two boundaries), only the
   leverage of the SoC's error changes. No attempt is made to detect it.
1. **`ValueError` for a hysteresis of 10 or less**, in the pure code; the glue
   passes the configured one.
1. **A dump made with another hysteresis is refused** (`None`), and so is a dump
   that processed nothing: the caller starts from nothing and a replay of at
   most 3 days finds the valleys again.
1. **Diagnostics beyond the ADR's attribute list**: `ValleyState.dropped_spans`
   and `last_drop` (not published; for the log of TASK-0025 if it wants one).
1. **Open decision 1: option A (human, 2026-10-29)**: a battery efficiency above
   100 % is published as measured; a test pins it
   (`test_an_estimate_above_one_is_published_as_it_is`).

## Review

**PASS** (inline; 946 tests (847 before), `ruff check`, `ruff format`, `mypy` on
source and tests; the new code is covered by the tests to the last line and
branch). Tests were written first and failed at collection; on the first run 84
of 94 passed, and the failures were four wrong expectations of mine (a reset
restarts the detection, so a fresh valley after it opens a span; a closed cycle
before a missing slot has already counted; 45 is a rise of 30 from 12; a slot of
exactly 125 minutes enters), a world list that was too short, and a simulated
"stack upgrade" at night that no span contained: none was a defect of the
machine. One existing guard test (TASK-0018: the literals `"charge"` and
`"discharge"` belong to `effy_exports.py`) rejected my first names of the dump
keys; they are `charge_kwh` and `discharge_kwh`.

Acceptance criteria to tests: the valley (real at exactly the hysteresis, the
end at the last slot at or below the lowest plus 10, the next beginning at the
first slot in the band of its own final lowest point, the dip that widens a
valley, the wobble at a peak, the double top); the missing SoC slot and the gap
(the detection resets, the estimate stays); the sums with half of each boundary
slot, Δstored with each boundary's capacity, the decayed ratio and its weight;
every reason to drop a span (capacity out of 1 % at either boundary or in the
middle, unavailable or not positive, a missing charge or discharge statistic, a
net input of exactly 0 or less) and that a problem outside the span is none; the
age of 125 minutes to the minute; Open decision 1; the dump at every cut of a
57-slot series with a counted span, a dropped span, a missing SoC, a gap and a
wobble at a peak, through JSON, equal to one go; 40 kinds of unusable data
refused.

**Mutants.** 71 mutants of the new code (boundary comparisons, the half-slot
weight at each of four places in both directions, the capacity rules, the decay
weight, the sign of Δstored, the reset rules, the order and the content of the
closing, the age gate, the restore). The first round let 7 survive; the causes
were two gaps (the inclusive band edge of a *second* valley's end, a net input
of exactly zero), a restore check shadowed by another (the strict decrease of
the records), a restore series without a wobble before a higher peak, one mutant
that only an equal-top case could tell (which turned out to be a real design
question, now decided and pinned) and two mutation strings that the formatter
had changed. All 71 are caught now.

**Simulation (Open decision 2).** 120 days, a 92 % battery: 90 spans of 691 kWh,
none dropped, the estimate after 40 kWh within 0.53 points at worst (−0.15 on
average); a stack upgrade at 14:10 drops exactly the one span it is in
(`capacity_changed`) and the estimate stays within 0.96 points; a battery that
ages from 95 % to 88 % is followed (88.04 % after 50 more days); with one slot
in 150 missing a statistic 42 spans count and 47 are dropped and the bias is
−0.15. The convention comparison is in the amendment to ADR-023.

Not verifiable here: the behaviour on a real BMS (a SoC that is noisy, steps or
holds still for hours; a capacity that the BMS corrects at night).
