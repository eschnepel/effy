# ADR-018 – Standby Power Estimate from Drain Intervals

**Date:** 2026-10-13 **Status:** Draft — every open decision was taken by the
human on 2026-10-13 and is wired into the text; awaiting the human's
confirmation before Stage 2 of TASK-0005 starts. Terms follow `GLOSSARY.md`.

---

## Context

Chargy (sibling project) needs the system's **standby power**: the power the
battery system itself draws to keep running. It currently estimates it from
state-of-charge idle runs. Effy sees every flow and the raw readings, so the
question is whether Effy can provide it as a sensor instead.

**What Effy does with standby power today.** Standby power leaves the system and
reaches no output, so it is never measured separately: it is part of the
**loss** (ADR-001, ADR-005). `tests/test_standby_scenario.py` pins this:

- In a slot with only 12 W of standby draw, the whole 12 W is loss and the
  discharge input's effective value is 0.
- With PV also active, the same 12 W is split 6 W / 6 W between PV and the
  discharge input (ADR-001): standby cannot be attributed to the battery.
- 12 W of pure standby and a 112 W discharge delivering 100 W give the same loss
  (12 W): per slot, standby and conversion loss are indistinguishable.

So standby power is **visible only where nothing else is going on**: when no
output and no other input is active, the loss equals the standby power exactly.
For a PV input this does not hold: PV active alone with no output is conversion
loss and drain mixed, with no evidence which is which (human). Only **BMS
inputs** can therefore be a drain source.

**What the human's BMS energy sensors show.** They are `total_increasing`
counters with a resolution of 0.01 kWh (10 Wh). Their first derivation gives
**peaks** of exactly one step (10 Wh) at standby times and a multiple of it at
load times. High standby is therefore just a shorter time between two peaks of
one step each.

**Assumptions:**

- **A1** *(confirmed by the human from the live system)* The BMS input that
  feeds the standby draw shows that energy: the raw discharge counter jumps with
  no consumption.
- **A2** *(corrected by the human)* At the lowest allowed charge the BMS stops
  feeding the grid but keeps drawing its own standby power from the battery. Now
  and then (for example 5 % below the lowest charge), when no PV is available, a
  small recharge from the grid brings the charge back up. So standby stays
  visible at the lowest charge; the recharge is flow in other sensors, and drain
  intervals that touch it are excluded.
- **A3** Between two peaks of one step the standby power is what the step energy
  divided by the time says it is; a jump time is the moment the counter crossed
  a step, within the source's polling delay.
- **A4** PV, grid import and both outputs together record every non-standby
  flow. A flow below a counter's resolution stays hidden in that counter until
  its next jump.
- **A5** Standby power is the same whichever BMS input feeds it (battery or
  grid), and the same in quiet periods as in operation (Chargy makes the same
  assumption).

## Options considered

| Option | Idea | Needs | Resolution | Main weakness |
| -- | -- | -- | -- | -- |
| **A** SoC-based | Quiet stretches where SoC falls: energy drop ÷ duration | SoC sensor, battery capacity | 1 SoC point of the capacity, e.g. 100 Wh for 2 points of a 5 kWh battery: 8+ h of quiet at 12 W | Capacity unknown to Effy; duplicates Chargy's estimator; coarse |
| **B1** Drain intervals (state machine) | Step energy ÷ time between two consecutive peaks of one BMS input while nothing else is active | BMS inputs configured apart from PV (ADR-019) | One counter step (e.g. 10 Wh): 50 min between peaks at 12 W | Needs quiet periods |
| B2 Fixed window | Counter energy in a fixed quiet window ÷ window | One selected input | ± one counter step per window: 17 % error for 3 h at 12 W | Quantization |
| B3 Regression | Fit loss against throughput; intercept = standby | None | Noisy | Needs many mixed slots; unstable |
| **C** Pass-through | Re-expose a standby sensor the BMS integration may already offer | That sensor | Whatever the BMS gives | No Effy value: Chargy can pick that sensor directly |

B1 is B2 with the window boundaries chosen at the peaks themselves, which
removes the quantization error: between two peaks of one step the energy is
exactly one step, so power = step ÷ time.

## Decision (proposed)

### 1. Peaks have a height and a power equivalent

A **peak** is a single reading whose power equivalent is above 0. Its **height**
is its first derivation (human): for an energy-family sensor the energy of the
jump, for a power-family sensor the reading.

|  | Peak | Height | Power equivalent |
| -- | -- | -- | -- |
| Energy-family | a **jump** | the energy of the jump: one resolution step at standby, a multiple at load | height ÷ time since the previous jump (exact; a polling delay of 30–60 s moves one value by 0.2 % to 2 %) |
| Power-family | a reading above 0 with no reading above 0 directly before or after it | the reading | the reading itself (not a slot mean) |

Two consecutive readings above 0 on a power-family sensor are no peak: that is
sustained power, and it breaks the chain. An offline gap or a counter reset
breaks it too.

**Resolution (human: learned, no starting value).** The resolution is not
configured and has no default: for an energy-family sensor it is the smallest
positive peak height seen for that sensor (10 Wh for the human's BMS), for a
power-family sensor the smallest step between two distinct non-zero readings
seen (all readings, not only peaks). Until a resolution has been learned,
nothing is validated. A leading double step does not matter: the resolution is
the minimum seen so far.

### 2. The drain source is detected, not configured

A **drain interval** is the span between two consecutive peaks of the *same* BMS
input that have the same height, during which no other sensor, input or output
(PV included), is active. That input is the drain source. There is no option for
it: whichever BMS input carries the standby is found by the state machine, so a
BMS fed from the grid is covered too. If two BMS inputs share the standby at the
same time, no drain interval forms and the sensor stays without estimate.

### 3. State machine (human's design; ◇ marks my formalization)

Events are processed in time order: peaks of the BMS inputs, and completed slots
with the short-term statistics of every sensor as known at that moment.

| Event | Effect |
| -- | -- |
| Peak of source S, and S is armed with its previous peak as t1 | The span (t1, t2) becomes a **pending drain interval**, remembering the heights of its start and end peak and the end peak's power equivalent; a new turn is armed at t2 |
| Any other peak (first one, other source, after a break) | Clear the armed turn; arm (S, t) only if the slot before the peak's slot is known and no sensor other than S was active in it ◇ |
| A slot completes | If any sensor other than the source is active in **any** slot from the slot before t1 up to this slot ◇ (re-read from the current statistics), clear the armed turn / drop that pending interval; otherwise confirm every pending interval older than the validation delay |
| Offline, counter reset, sustained power | Clear everything |
| A drain interval is confirmed | It **counts** only if its start and end peak have the same height (below) |
| An interval counts | `live = 0.95 · live + 0.05 · power equivalent`; the first value seeds it |

The 5-minute margin before the first and after the last peak falls out of the
slot grid: the slot before t1 and the slot after the one containing t2 must be
quiet. Re-reading the whole span at confirmation matters: a counter that jumps
late redistributes its energy into earlier slots (ADR-012/014).

**The same-height rule (human).** The start and the end peak of an interval must
have the same height: their heights differ by less than the sensor's resolution
(the maximum difference is the resolution itself, not a fixed number, human),
which for multiples of a step means equal. For an energy-family sensor both must
also be **exactly one step** (human: yes): the crossing time of a multi-step
jump is not known, so the time between peaks would not be exact. 10 Wh followed
by 20 Wh disqualifies the interval, and so do two equal 20 Wh peaks. The rule is
about heights, not about the time between peaks: a short time between two
one-step peaks is a high standby, not an outlier.

**Validation delay (human accepted my recommendation).** An interval is
confirmed only 125 min (redistribution cap plus margin) after its last peak, so
a lagging counter has had time to redistribute.

What the delay buys in prototype runs (12 W standby, estimate in W):

| Scenario | no delay | delay 125 min |
| -- | -- | -- |
| Load 60 W for 10 min, output counter jumps at the end | 12.00 | 12.00 |
| Load 20 W for 30 min, output counter jumps late | 12.90 | 12.00 |
| Load 5 W for 60 min, below every counter | 12.37 | 12.25 |
| BMS-internal burst 60 W for 10 min, no load | 14.06 | 14.40 |
| Load 4 W for 4 h, output counter jumps every 150 min | 12.74 | 12.33 |

A load that no counter shows leaks a sample whose effect is bounded by the 5 %
weight (the residuals above). The BMS-internal burst is by this design a high
standby, not an error.

### 4. Cases

- **C1** *Given* a start peak of 10 Wh and an end peak of 20 Wh *then* the
  interval does not count.
- **C2** *Given* a lone 60 W interval (10 Wh in 10 min) between 12 W intervals
  with no consumption anywhere, because the BMS itself drew it, *then* it counts
  as a high standby and moves the estimate by at most 5 % of its excess.
- **C3** *Given* every interval shows 60 W *then* the estimate moves to 60 W at
  5 % per value; there is no plausibility cap (human: high standby is just a
  lower time delta).
- **C4** *Given* a power sensor with two consecutive readings above 0 *then*
  there is no peak and the chain breaks.
- **C5** *Given* a 20 W load whose output counter jumps late *then* the delay
  lets the counter redistribute before the interval is confirmed.
- **C6** *Given* standby fed by the grid import with a static discharge counter
  *then* the estimate comes from the grid-import input.
- **C7** *Given* only PV moves *then* there is no estimate.
- **C8** *Given* two BMS inputs sharing the standby *then* there is no estimate.
- **C9** *Given* two peaks of 20 Wh each (two steps, resolution 10 Wh) *then*
  the interval does not count: a drain peak is exactly one step.
- **C10** *Given* the first jump is a double step and one-step peaks follow
  *then* the interval from the double step to the first one-step peak does not
  count, and the intervals between one-step peaks do.

### 5. The live standby sensor

- One Effy sensor, device class power, W, state class measurement, rounded to
  0.1 W; **unavailable** (not unknown) until the first interval counts. It
  adapts to seasonal changes at 5 % per counted interval (half of the gap to a
  new level after about 14 intervals). It exists only if at least one BMS input
  is configured (ADR-019).
- Attributes: last counted value, end of the last drain interval, drain source
  of the last value, count, last processed slot.
- **Restart.** The previous estimate comes back from Home Assistant's restored
  state; the machine is rebuilt by replaying the peaks and slots since the last
  processed slot, starting from the last peak of every BMS input within 24 h
  before it (read from the recorder's state history). Replay and live operation
  must give identical results.
- **Recorder.** The sensor is **not** excluded from the recorder (human): it has
  a state history and, with state class measurement, long-term statistics. Its
  entity ID is `sensor.effy_standby_power`. ADR-017's exclusion glob
  `sensor.effy_*` is retired by TASK-0007a and TASK-0007b (the human: no longer
  needed once the slot trigger is recorder-synchronized, TASK-0006). TASK-0005
  Stage 2 therefore depends on TASK-0007b and TASK-0008, so the sensor is born
  recorded with no interim workaround.

### 6. Structure (ADR-000 §3/§6)

The adapters (readings → peaks) and one pure transition function
(`state, event → state`) are in `calculation.py`, zero-mock tested. Reading the
state history, feeding events in time order after each recent recalculation, and
the restore/replay are glue in `history.py` / the coordinator. Starting must not
block Home Assistant startup (the concern behind ADR-014's staged lookback).

### 7. The loss model is not changed

Standby power stays inside the loss; ADR-001/005 and every existing `effy_*`
series are unchanged (human: yes). The waterfall treats PV and BMS inputs as one
mixed pool exactly as before the split of the input list (ADR-019). A regression
test asserts the existing series are identical with and without the new sensor.

### 8. Consequence for consumers

Standby power is already out of Effy's **effective series**, as part of the
loss. A consumer that takes energy from the effective series must not subtract
standby power again (double counting); one that takes the raw counters must.
(With PV active the waterfall also takes part of the standby loss out of the PV
series.)

### 9. Out of scope

Measuring standby power during operation; any Chargy-side change; separating BMS
inputs that share the standby.

## Consequences

- Standby power becomes available at one counter step of resolution with no
  capacity or SoC input, including at the lowest charge (A2). Recharge events
  and any other flow interrupt it and are excluded.
- Two consecutive one-step peaks of one BMS input are enough for one value, and
  the estimate trails real time by at least 125 min.
- A load that no sensor shows leaks a value bounded by the 5 % weight; a
  constant extra draw is indistinguishable from standby.
- A sensor whose resolution cannot be learned (for a power-family sensor: never
  two distinct non-zero readings) validates nothing and gives no estimate.
- The estimate is one value for all drain sources; the source of the last value
  is an attribute.
- It depends on ADR-019 (BMS inputs apart from PV), TASK-0007b (recorded sensor)
  and A1, which the human confirms.
- The sensor is the first Effy sensor with a real live state and a state machine
  that survives restarts; the replay cost must be bounded in Stage 2.

## Open decisions

None. Decided by the human on 2026-10-13: the resolution is learned (D1), a
drain peak is exactly one step (D2), the 125 min validation delay stays (D3),
the sensor exists only if at least one BMS input is configured (D4).
