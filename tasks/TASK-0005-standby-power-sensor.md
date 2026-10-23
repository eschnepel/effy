# Task: Investigate and expose the battery system's standby power as an Effy sensor

- **Status:** review
- **Related ADRs:** \[ADR-000, ADR-001, ADR-002, ADR-003, ADR-005, ADR-008,
  ADR-012, ADR-013, ADR-017, ADR-018 (Draft), ADR-019 (Draft)\]
- **Dependencies:** Stage 1: none. Stage 2:
  [TASK-0007b-retire-recorder-exclusion, TASK-0008-split-input-sensors-by-role]

## Goal

The sibling project **Chargy** (battery grid-charge planner) needs the battery
system's **standby power**: the power the battery system itself draws to keep
running, in every mode and even when the battery is at its minimum SoC. Chargy
currently estimates it itself, from the SoC drop while the BMS is idle (Chargy
ADR-0012). That is an energy-accounting question, and energy accounting is
Effy's job: Effy already compares the energy flowing into and out of the PV +
BMS system slot by slot.

This task has two stages with a human decision between them:

1. **Investigate** whether and how Effy can determine standby power, separate
   from the conversion/wiring loss it already distributes, and propose the
   design as a **Draft ADR** — no production code.
1. **Address** it: after a human has confirmed the ADR, implement the sensor.

## Cross-project context (the worker has no access to the Chargy repo)

- **Terms (Chargy glossary):** *Standby power* — the power the battery system
  itself draws to keep running, in every mode and even below Min SoC. *Idle run*
  — consecutive slots in which the BMS neither draws from nor feeds the grid on
  any phase and the SoC never rises. Do not confuse with Effy's "idle"
  (ADR-010/013), which means a *sensor* that is not changing; use "standby
  power" for the new concept.
- **Chargy's current estimator (to be replaced or demoted if Effy delivers):**
  standby power = Σ energy drop over idle runs ÷ Σ run duration, over a history
  window of 28 days; an idle run lasts ≥ 3 h and spans ≥ 2 SoC percentage points
  (proposed, unvalidated); until one qualifies a 10 W placeholder is used.
  Energy drop = ΔSoC/100 × capacity.
- **How Chargy will consume the result:** through an entity picker for standby
  power in its config flow, read as a live state in **W** (≥ 0). So the sensor
  must be a normal `sensor.effy_*` entity (recorder-excluded by the glob of
  ADR-017), unit W, state class measurement, with a stable entity ID, and must
  report `unavailable` — not an invented number — while there is not enough
  data.
- **Double-counting risk (key question, see below):** Chargy's round-trip
  efficiency (Chargy ADR-0005 §4) subtracts the energy standby power consumes
  from the energy delivered out of the battery. A Chargy user may point Chargy's
  energy-counter pickers at Effy's *effective* `sensor.effy_*` series. If Effy's
  loss already contains standby energy, that standby energy would be counted
  twice.

## Questions to answer (Stage 1)

1. **Visibility.** Effy's inputs are PV strings, BMS import-from-battery and BMS
   import-from-grid; its outputs are BMS export-to-battery and BMS
   export-to-grid; loss = `max(0, Σ inputs − Σ outputs)` (ADR-001, ADR-005). It
   has **no SoC and no capacity input**. Can standby power be seen in these
   flows at all? Which hardware counters actually include the standby energy (a
   discharge counter that counts it, a grid import that feeds it, neither)?
   Document per counter, with evidence (README definitions, code, a worked
   scenario) — mark anything that depends on the Solakon hardware as an
   assumption to be confirmed against real data.
1. **Overlap with the existing loss.** In a slot with standby but no other flow,
   what do `total_loss`, `effy_*` and `effy_*_power` show today? Does standby
   energy end up inside the distributed loss (and so inside the effective
   series), or is it invisible? Demonstrate with a small pure scenario
   (zero-mock), not by argument alone.
1. **Estimation options.** Evaluate at least:
   - **A — SoC-based:** idle-run estimation like Chargy's, which needs new
     optional SoC and capacity inputs in Effy's config flow.
   - **B — flow-balance-based** using only the existing inputs and outputs, if
     question 1 shows it is feasible (for example slots where only the battery
     discharge input is non-zero and no output is).
   - **C — pass-through** of a standby sensor the BMS integration may already
     expose (little added value; state it plainly if so). For each: data needed,
     config impact, resolution (5-minute slots; SoC is coarse, idle runs are
     long), failure modes, effect on existing series.
1. **Shape of the output.** A single rolling estimate sensor (W) versus a
   per-slot statistic series; the window length; behavior with too little data
   (`unavailable`); attributes that expose provenance (for example sample count,
   total idle duration).
1. **Interaction with ADR-001/005.** Must the existing invariant
   `Σ effective inputs = Σ outputs` and the loss capping stay untouched? If
   standby is carved out of the loss, say exactly how, and prove no existing
   `effy_*` series changes (regression equality) or state the intended change.
1. **Consequence for Chargy.** If standby energy is inside Effy's loss: state
   which Chargy-side change is needed (for example the efficiency subtracts
   standby only when raw counters are used) so it can be passed to the Chargy
   Lead Agent. Effy must not depend on Chargy.

## Acceptance Criteria

### Stage 1 — investigation (no production code)

- Given the six questions above Then a **Findings** section in this file answers
  each one with evidence; assumptions about hardware are listed separately.
- Given the options of question 3 Then a Draft `adr/018-standby-power-sensor.md`
  (Status **Draft**, drafted per ADR-000's ADR rules, with at least the options
  A/B/C, a recommendation, consequences, and the config/entity changes) exists,
  and `adr/INDEX`-style listings (if Effy keeps one) are updated.
- Given question 2 Then a zero-mock test or throwaway script result demonstrates
  how a standby-only slot is treated today; if it is kept, it lives in `tests/`
  and passes.
- Given Stage 1 is done Then no file under `custom_components/` is changed, the
  Lead Agent is told the status is `review` with the open decisions listed, and
  work **stops at the gate**: the human confirms, amends or rejects ADR-018.

### Stage 2 — implementation (provisional; the Lead Agent refines it from the confirmed ADR-018)

- Given a synthetic world with zero flows and a SoC falling 2 points over 8 h
  (capacity C) Then the standby sensor reads `ΔSoC/100 × C ÷ 8 h` in W within a
  stated tolerance.
- Given slots with any flow or with a rising SoC Then they are excluded from the
  estimate.
- Given less data than the confirmed ADR requires Then the sensor is
  `unavailable` (no default value invented).
- Given the existing scenarios of `tests/test_calculation.py` and
  `tests/test_history_recent_equivalence.py` Then they pass unchanged, and a new
  regression test proves the existing `effy_*` and `effy_*_power` series are
  identical with and without the new inputs, unless ADR-018 states otherwise.
- Given the sensor entity Then it is `sensor.effy_*`, unit W, state class
  measurement, stable entity ID, and covered by the recorder exclusion of
  ADR-017.

## Estimated File / Module Footprint (hint, not a commitment)

- Stage 1: this file, `adr/018-standby-power-sensor.md`, `tests/` (a small
  scenario test, optional).
- Stage 2 (depends on the chosen option): a pure function in
  `custom_components/effy/calculation.py` (or a new pure module), wiring in
  `coordinator.py`, a new entity in `sensor.py`, optional new inputs in
  `config_flow.py` / `const.py` / `translations/`, README (config table and
  output entities), tests.

## Definition of Done

- Stage 1: findings and Draft ADR-018 delivered, work stopped at the gate.
- Stage 2: tests green · `ruff`/`mypy --strict` clean · docs updated · no open
  ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none
  expected)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. No upstream task delivers
     anything for this one; Stage 1 reads the existing code only. -->

- `distribute_loss`, `SensorReading`, `LossDistribution`,
  `trapezoidal_slot_contributions`, `interpolate_slot_gaps`,
  `effective_in_original_unit` from `custom_components/effy/calculation.py`
  (existing code, read in Stage 1)
- `EffySensor`, `EffyDerivedPowerSensor` from `custom_components/effy/sensor.py`
  (existing entity patterns)
- `history.py` recorder read/write helpers (`_fetch_statistics`,
  `_write_recorder_statistics`, …) for any statistic-based option

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->

- Stage 1 (2026-10-13, no file under `custom_components/` changed):
  - Findings in this file; Draft `adr/018-standby-power-sensor.md` (amended
    after the human's four rounds the same day; no open decisions left) and
    Draft `adr/019-input-sensors-split-by-role.md` (new, from the human's
    decision to split the input list).
  - `tests/test_standby_scenario.py` → `TestStandbyOnlySlotToday` (4 tests: how
    today's model treats standby) and `TestDrainMachinePrototype` (25 test
    cases: throwaway prototype `_DrainMachine`, `_energy_events`,
    `_power_events`, `_activity_at`, `_run`, `_simulate_counter` — defined in
    the test file, **not** production code, **not** imported by anything).
  - Related, same session: `GLOSSARY.md` (new), deferred
    `TASK-0006-recorder-synchronized-slot-trigger.md`, deferred
    `TASK-0007b-retire-recorder-exclusion.md`,
    `TASK-0008-split-input-sensors-by-role.md`, CAP-3 and CAP-4 in
    `tasks/adr-capability.md`, `tasks/INDEX.md`.
  - Verification: `pytest` 198 passed (167 existing + 31 new); `ruff check`,
    `ruff format --check`, `mypy` clean with the locked toolchain (ruff 0.15.20,
    mypy 2.1.0); `mdformat --check` clean on the edited `.md` files.
  - README ADR table not touched (it stops at ADR-008); it is updated in Stage 2
    with the loss wording.
- Stage 2: (to be filled after the gate)

## Findings

### Human input during Stage 1 (2026-10-13)

- Hint: standby drain is easy to detect from the time between two peaks of the
  raw battery discharge sensor where nothing else is active in between or near
  (5 minutes before the first and after the last). Read as a **drain interval**.
- It also works with peaks in a power raw source: a jump becomes a peak.
- **Round 3, the human's BMS energy sensors** are `total_increasing` with a
  resolution of 0.01 kWh. Their first derivation gives peaks of exactly one step
  (10 Wh) at standby times and a multiple at load times. **High standby is just
  a shorter time between peaks.** The same-value rule is therefore about **peak
  heights**: the start and the end peak of an interval must have the same height
  within the sensor's resolution (10 Wh then 20 Wh disqualifies). It is not
  about the derived power: my earlier power-agreement rule with its 10 %
  tolerance is dropped. For a power sensor the peak is the reading; two
  consecutive readings above 0 disqualify.
- Design points wired into ADR-018: A2 corrected (standby stays visible at the
  lowest charge, with occasional grid recharges); no config option for the drain
  source; the sensor is not excluded from the recorder; a state machine driven
  by peaks and short-term statistics; the live value takes each counted interval
  with 5 % weight; a burst of 60 W without consumption is a high standby (no
  plausibility cap).
- **PV is not a drain source**: loss and drain are mixed there, with no evidence
  which is which. Hence the input list must be split into PV and BMS inputs
  (ADR-019, TASK-0008). The waterfall keeps treating PV and BMS inputs as one
  mixed pool as before the split. **No migration path and no version bump**: the
  human has the only installation.
- **A1 is confirmed by the human from the live system**; no data export is
  needed.
- Decisions taken as suggested: validation delay 125 min; the arming and re-read
  rules (◇); one estimate for all drain sources; restored state with a 24 h
  replay lookback; standby stays inside the loss; TASK-0006 split from
  TASK-0007b; README housekeeping in Stage 2; TASK-0006 parameters as Shady.
- Round 4, all agreed: the resolution is learned (smallest positive peak height
  seen per sensor; for power sensors the smallest step between distinct peak
  readings); a drain peak is exactly one step; the 125 min validation delay
  stays; the sensor exists only if at least one BMS input is configured; a
  sensor in both input lists is an error and no BMS input is required.
- Round 5: no starting value for the resolution (a fixed 0.01 W would not work
  for coarser resolutions). The 0.01 was meant as the maximum absolute
  difference between start and end peak; with the learned resolution that
  maximum is the resolution itself (peaks agree when they differ by less than
  it). A power sensor learns it from the smallest step between distinct non-zero
  readings; without a learned resolution nothing is validated.
- Terms settled in `GLOSSARY.md`.

### Q1 — Visibility

Standby power is visible in the flows only as **loss**, and measurable only
where nothing else flows. Per counter:

| Counter | Shows standby energy? | Basis |
| -- | -- | -- |
| BMS import-from-battery (BMS input) | Yes, also at the lowest charge (A1 confirmed, A2 corrected) | Human, live system |
| BMS import-from-grid (BMS input) | While the grid feeds the BMS, e.g. a recharge; found like any source | A2 |
| PV strings (PV inputs) | Not usable: PV active alone is loss and drain mixed | Human; waterfall (ADR-001) |
| BMS export-to-battery (output) | No: energy into the battery | README definitions |
| BMS export-to-grid (output) | No: standby is consumed inside, not exported | README definitions |

### Q2 — Overlap with the existing loss (zero-mock, `TestStandbyOnlySlotToday`)

- Standby-only slot (12 W, 1 Wh): `total_loss_w` = 12 W, effective value of the
  discharge input = 0, derived power (`effy_*_power`, before the waterfall) = 12
  W. The standby energy is **entirely inside the loss and absent from the
  effective series**.
- Through the real slot pipeline for a quiet 6 h night: the effective series
  sums to 0 and the loss carries the discharged energy (less than one slot's
  energy at the open tail).
- With 100 W PV also active: loss 12 W is split 6 W / 6 W; effective PV 94 W,
  effective discharge 6 W. Standby cannot be attributed to the battery.
- 12 W standby and a 112 W discharge delivering 100 W give the same
  `total_loss_w`: not separable per slot.

### Q3 — Estimation options

Full table in ADR-018. **A (SoC)** needs SoC and capacity inputs, resolution 1
SoC point of the capacity (8+ h of quiet at 12 W for 2 points of an assumed 5
kWh battery). **B1 (drain intervals)** needs the BMS inputs apart from PV
(ADR-019), resolution one counter step (50 min between peaks at 12 W and 10 Wh).
**B2 (fixed window)** is quantization-limited (10 W instead of 12 W). **B3
(regression)** is noisy. **C (pass-through)** adds nothing Effy-side.
Recommendation: **B1**.

### Prototype results (`TestDrainMachinePrototype`, synthetic, η = 1)

What the validation delay buys (12 W standby, estimate in W):

| Scenario | no delay | delay 125 min |
| -- | -- | -- |
| Load 60 W for 10 min, output counter jumps at the end | 12.00 | 12.00 |
| Load 20 W for 30 min, output counter jumps late | 12.90 | 12.00 |
| Load 5 W for 60 min, below every counter | 12.37 | 12.25 |
| BMS-internal burst 60 W for 10 min, no load | 14.06 | 14.40 |
| Load 4 W for 4 h, output counter jumps every 150 min | 12.74 | 12.33 |

Under the same-height rule all of these peaks are one step, so the rule does not
touch them: the delay is the only protection against loads no sensor shows, and
a burst is by design a high standby, bounded by the 5 % weight.

Further cases: 10 Wh then 20 Wh disqualifies the interval; two equal double
steps count unless single steps are required; a constant 60 W is accepted as the
standby (no cap); 24 W standby is simply a shorter time between peaks; standby
carried by the grid-import input alone gives 12.0 W; two BMS inputs sharing it
give no estimate; PV alone gives none (it would give 12.0 W if PV were
eligible); isolated 12 W power peaks give 12.0 W like jumps, power peaks of 12.0
and 12.5 W disqualify each other, two consecutive readings above 0 give none; a
single drain interval counts; the margins before the first and after the last
peak hold at exactly 5 min; offline gaps and resets break the chain; a polling
delay of 30–60 s needs no tolerance because peak heights stay exact; a 12 → 14 W
step is followed at 5 % per counted interval.

### Q4 — Shape of the output

One live sensor, `sensor.effy_standby_power` (recorded), estimate = 5 % average
of counted intervals, `unavailable` until the first interval counts, provenance
attributes; exists only if a BMS input is configured (ADR-018 D4).

### Q5 — Interaction with ADR-001/005

**Untouched** (human: yes). Standby stays inside the loss; the machine only
reads. A regression test asserts every existing series is identical with and
without the new sensor.

### Q6 — Consequence for Chargy (text for the Chargy Lead Agent)

> Effy's loss contains standby power, so Effy's **effective** `sensor.effy_*`
> series already exclude it (with PV active the waterfall also takes part of it
> out of the PV series). Chargy's round-trip efficiency must subtract standby
> energy **only when it uses raw counters**; with Effy's effective series it
> would count standby twice. Effy does not depend on Chargy.

### Assumptions

A1–A5 as in ADR-018 §Context. A1 is confirmed by the human; A2 was corrected by
the human; A3–A5 remain assumptions to check on the live system after Stage 2.

### Limits of this investigation

- Every number comes from synthetic scenarios (constant power, η = 1, one 10 Wh
  resolution for all sensors); A1 rests on the human's observation.
- A sensor whose smallest step never shows up in its history has no resolution
  to learn and gives no estimate (no starting value, human).
- The prototype models the statistics as the redistribution known at each slot
  completion; the real recorder timing (ADR-011, TASK-0006) is not simulated.
- Replay after a restart and the cost of reading histories are specified, not
  prototyped or measured.
- ADR-019 is untested.

### Open decisions for the gate

None. ADR-018 and ADR-019 are Draft only until the human confirms them.

### Proposed Stage 2 acceptance criteria (if ADR-018 is confirmed as drafted)

The provisional Stage 2 criteria above are shaped for Option A (SoC) and are
replaced by these if ADR-018 is confirmed (after TASK-0007b and TASK-0008):

- Given raw readings of an energy-family BMS input, then a pure adapter yields
  its peaks with their heights and power equivalents; given a power-family BMS
  input, then isolated readings above 0 are peaks and two consecutive readings
  above 0 are not; offline gaps and resets yield breaks.
- Given the transition function, then the table of ADR-018 §3 holds event by
  event, with boundary tests for the margin before the first and after the last
  peak, and the cases C1–C9.
- Given the same-height rule, then 10 Wh then 20 Wh does not count and 10 Wh
  then 10 Wh does; given the validation delay, then the delay table of the
  prototype holds with the production code.
- Given a power-family BMS input that never shows two distinct non-zero
  readings, then no interval is validated and the sensor stays `unavailable`.
- Given a sequence of counted intervals, then the live state equals the 5 %
  average, the first value seeds it, and before it the sensor is `unavailable`.
- Given a restart, then restoring the state and replaying from the last
  processed slot (24 h lookback) gives the same result as uninterrupted
  operation.
- Given the new sensor, then every existing `effy_*` and `effy_*_power` series
  is identical (regression), and the entity is `sensor.effy_standby_power`, W,
  measurement, recorded, with the provenance attributes; it does not exist
  without a BMS input.
- Given Home Assistant startup, then the first replay does not block it.
- Given the README, then its ADR table is complete and its loss wording says
  *loss*.
