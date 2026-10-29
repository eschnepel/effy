# ADR-023 – Efficiency Sensors for the Battery System

**Date:** 2026-10-23 **Status:** Accepted 2026-10-27 — by the human's
instruction in chat to implement TASK-0017 to TASK-0027 (the ADR's Gate 2).
Decisions 1 to 14 were taken by the human in a grill session on 2026-10-23. The
points under "Open decisions" stay open until their task settles them. Terms
follow `GLOSSARY.md`; ◇ marks my formalization of a decision.

---

## Context

Effy shows the battery system's losses as one number (ADR-001) and its standby
power as one estimate (ADR-018). It does not say how efficiently the system
moves energy: from the grid into the battery, in the battery, and from the
battery back out to the grid. The BMS counters that make standby visible also
make those three efficiencies measurable.

**What Effy knows today.**

- The BMS reports four counters: the **grid import counter** and the **discharge
  counter** (BMS inputs), the **charge counter** and the **grid export counter**
  (BMS outputs). The charge and the discharge counter are measured on the
  battery side (human).
- The configuration has two unnamed lists, BMS inputs (ADR-019) and outputs, so
  Effy cannot tell the charge counter from the grid export counter, or the
  discharge counter from the grid import counter.
- The effective series satisfy Σ inputs = Σ outputs by construction (ADR-001).
  An efficiency computed from them would be 100 %. The measurement therefore
  reads the **derived power series** (`effy_*_power`, ADR-012), as ADR-018's
  activity reading does.
- The BMS's own consumption (standby power, ADR-018) sits between the grid side
  and the battery side. In a pure charge the grid feeds it and the grid import
  counter shows it; in a pure discharge the battery feeds it and the discharge
  counter shows it (ADR-018 A1).
- Stored energy needs the state of charge and the battery capacity, which Effy
  does not read today.

**Assumptions:**

- **A1** ADR-018 A1 holds: the BMS input that feeds the standby draw shows that
  energy.
- **A2** The BMS's state of charge is accurate enough for differences of it; its
  own error enters Δstored and is not corrected.
- **A3** An efficiency depends on power, temperature and age. Each sensor is a
  running average over what was observed.

## Options considered

- **Average per-event ratios, or decay a numerator and a denominator.** Ratios
  give a 3 kWh event and a 0.1 kWh event the same weight unless a weight is
  added by hand. Decayed sums give "5 % per kWh" exactly. Chosen: decayed sums.
- **Detect which counter is which from the state of charge, or configure it.**
  Detection fails with PV charging and with swapped meters, and a wrong guess
  gives a plausible wrong efficiency. Chosen: configure (human).
- **Standby in the battery efficiency** (the human's first formula,
  `discharge / (charge − Δstored − standby)`). Rejected by the human: both
  counters are on the battery side, so standby is not part of the battery's own
  efficiency.
- **Roundtrip measured directly, or as the product.** Chosen: the product
  (human).

## Decision

### 1. Configuration: the counters by function (amends ADR-019)

The BMS-input list and the output list are **replaced** by four optional single
sensor pickers (human): grid import counter and discharge counter (BMS inputs),
charge counter and grid export counter (BMS outputs). The PV input list stays.
Keys ◇: `grid_import_sensor`, `discharge_sensor`, `charge_sensor`,
`grid_export_sensor`. The waterfall still sees one mixed pool of inputs and the
outputs (ADR-019 decision 2 is unchanged).

Three more options, all optional: `soc_sensor`, `capacity_entity` and
`valley_hysteresis` (a number, default 30, minimum 15 so that it stays above the
10-point valley band ◇).

- **Capacity entity:** sensor, number or input_number domain, in Wh or kWh; any
  other unit is an error. A typed number is not offered: the entity can follow
  battery ageing.
- **Validation:** at least one input in total (PV list or a BMS picker) and at
  least one output (charge or grid export), as today; no entity in two roles;
  SoC and capacity are set together or not at all; the SoC sensor needs state
  class `measurement`; the standby sensor needs grid import or discharge, as in
  ADR-018. One sensor per role: one battery system per entry.
- **No migration (human).** The old `bms_input_sensors` and `output_sensors`
  keys are ignored and disappear at the next options save; nothing rewrites them
  at setup and there is no `VERSION` bump. ADR-019's migration of the still
  older `input_sensors` key stays as it is. An entry without a charge or a grid
  export counter **refuses to set up** with a translated message ("assign the
  BMS sensors in the options"): no recalculation runs and no statistics are
  written, because a recalculation with no output would write the whole input as
  loss. No repair issue. The options flow stays reachable on such an entry and
  reloads it on save (to be verified, see Open decisions). The human
  reconfigures the only installation.

### 2. Four sensors

`sensor.effy_charge_efficiency`, `sensor.effy_battery_efficiency`,
`sensor.effy_discharge_efficiency`, `sensor.effy_roundtrip_efficiency`.

- Unit %, shown on a 0 to 100 scale, rounded to 0.1, state class measurement, no
  device class (Home Assistant has none for efficiency). Recorded like the
  standby sensor (ADR-020) with the recorder's own `mean`, `min`, `max`
  statistics; Effy writes no statistics for them. Every one restores its state
  after a reboot (ADR-021 decision 6).
- **Existence.** Each sensor exists only if its inputs are configured: charge
  efficiency needs the grid import and charge counters; discharge efficiency the
  discharge and grid export counters; battery efficiency the charge and
  discharge counters, SoC and capacity; roundtrip efficiency all three.
- **Availability.** **Unavailable** (not unknown) until the sensor has its first
  measurement. Charge, discharge and roundtrip efficiency are also unavailable
  until `sensor.effy_standby_power` has its first value: no invented default
  (ADR-018). Battery efficiency does not wait for standby (human). Roundtrip is
  unavailable whenever any of the three is.
- **Attributes:** `last_counted_value`, `last_counted_end`, `counted_kwh` (total
  since the estimate began), `counted_runs` (pure runs, or valley spans for the
  battery), `last_processed_slot`.

### 3. What is measured

Per 5-minute slot, the energy of a counter is its derived power series value × 5
min ◇. The standby energy of a slot is the **recorded `effy_standby_power`
statistic of that slot × 5 min** (human): a slot without a standby statistic
does not count, which also makes replay identical to live operation. A slot
counts only when it is at least **125 min** old (ADR-018 D3), so that late jumps
have been redistributed. "Active" is the glossary's (value not zero) and the
reading of the activity is the one ADR-018's machine already uses, PV inputs
included; there is no new tolerance for tiny PV readings at night.

### 4. Pure slots and pure runs

A **pure charge slot** has only the grid import and charge counters active; a
**pure discharge slot** only the discharge and grid export counters, PV inputs
included. A **pure run** is a series of consecutive pure slots of one kind
**without its first and its last slot**, which may be incomplete (human). So a
run needs at least 3 slots to count anything. Any impure slot, any slot without
statistics and any offline gap ends the run. A slot is counted as soon as it is
old enough and both its neighbours are pure ◇: Effy does not wait for the run to
end, so a long grid charge updates the estimate while it runs. Standby's own
rule (ADR-018, quiet neighbours) is unchanged.

### 5. Charge and discharge efficiency

Over the counted slots of pure runs:

- **Charge efficiency** = Σ charge ÷ Σ (grid import − standby energy).
- **Discharge efficiency** = Σ grid export ÷ Σ (discharge − standby energy).

A slot whose import (or discharge) energy is not larger than its standby energy
does not count. They need no valley and no SoC.

### 6. Battery efficiency

**Battery efficiency = Σ discharge ÷ (Σ charge − Δstored)** over a valley span
(human). Standby plays no part in it.

- **Valleys (human, formalized ◇).** On the SoC series (the recorder's 5-minute
  `mean` statistic, in points): the valley hysteresis H and the 10-point band
  are as in the glossary. A valley with lowest point L is real when SoC has
  risen to L + H, and it **ends** at the last slot at or below L + 10 before
  that rise. The **next valley begins** at the first slot after the peak at or
  below L₂ + 10, where L₂ is its lowest point; it is real when SoC has risen to
  L₂ + H afterwards, and the peak between must be at least H above both lowest
  points. A smaller dip and recovery is a wobble: it widens the valley (a
  standard zigzag with threshold H). The 10-point band is a constant. A missing
  SoC slot resets the detection; a span never contains one.
- **Valley span** = from the end of one valley to the beginning of the next. Its
  closing waits until the next valley is real **and** its beginning is 125 min
  old. The first span therefore needs two valleys.
- **Sums ◇.** Σ charge and Σ discharge run from the middle of the end slot to
  the middle of the beginning slot (half of each boundary slot counts), because
  the slot's SoC is a mean and so describes its middle.
- **Δstored** = (SoC_end · capacity_end − SoC_begin · capacity_begin) ÷ 100,
  with each capacity read at its boundary.
- **Capacity change (human).** If the capacity changes anywhere inside the span
  by more than **1 %** relative to its value at the start (a BMS-reported value
  can jitter), the span is dropped: a battery stack upgrade. Only the span is
  skipped; the accumulated battery and roundtrip estimates are not reset (they
  converge at 5 % per kWh). Capacity comes from the raw state history of the
  entity (a number or template often has no statistics): the state at each
  boundary and every change between. A span with an unavailable or non-numeric
  capacity, or a missing statistic of the charge or discharge counter, is
  dropped. A span with Σ charge − Δstored not larger than 0 is dropped.

### 7. Adaptation: 5 % per kWh

Each sensor keeps a numerator N and a denominator D. Every measurement (a
counted slot, or a valley span) with gross input energy x kWh does `f = 0.95^x`,
`N ← f·N + out`, `D ← f·D + in`, and the sensor is N ÷ D. In a steady state a
measurement of x kWh weighs `1 − 0.95^x`: 5 % for 1 kWh, 14.3 % for 3 kWh, as
for standby's 5 % per interval. The first measurement seeds the estimate. There
is no cap on a large measurement. Gross input energy is the slot's grid import
(charge efficiency) or discharge (discharge efficiency) ◇, and Σ charge −
Δstored for a span.

### 8. Roundtrip efficiency

The product of the three published values (human). **Known limit (human
accepted):** the battery efficiency counts battery-fed standby as discharge (A1)
while discharge efficiency removes standby only inside pure discharge runs, so
standby drawn from the battery in idle or mixed slots counts as delivered and
the product runs slightly high.

### 9. Restart and history

- **Restart.** The machine's whole state — the decayed sums, the classification
  of the last two slots, the open span's running sums, the zigzag state and the
  last capacity — is persisted and restored; the slots after the last processed
  slot are then processed, at most 3 days back, which gives the same result as
  uninterrupted operation. A longer downtime loses what lies beyond the 3 days.
  Unlike ADR-018, the state is made of slot aggregates, so nothing is rebuilt
  from earlier events. A state up to 15 minutes old after a crash (ADR-021) is
  safe: the catch-up processes those slots again. Starting must not block Home
  Assistant startup.
- **History recalculation (human: yes).** Every history recalculation resets the
  four efficiencies' state and replays it over the slots it has just rewritten,
  then the slot job continues. The window is
  `min(max_history_days, purge_keep_days)`, since only 5-minute statistics are
  readable (10 days unless changed). Slots without a standby statistic do not
  count; battery efficiency replays fully.

### 10. Structure (ADR-000 §3/§6)

The pure machines (slot classification, the pure-run step, the valley step, the
decayed sums) are in `calculation.py`, zero-mock tested. Reading the statistics
(derived power of the six series, SoC, the standby statistic) and the capacity
state history, feeding slots in time order, persistence and replay are glue in
`history.py` / the coordinator. The slot job runs them **after the standby
update and before the closing step** (ADR-021 decision 4), under the lock.

### 11. Published to other integrations (amends ADR-021)

Four new `series` values (`charge_efficiency`, `battery_efficiency`,
`discharge_efficiency`, `roundtrip_efficiency`), direction `output`, role `bms`,
`source_entity_id` null, aggregations `[mean, min, max]`; and a new key
`function` on every source series (`grid_import`, `discharge`, `charge`,
`grid_export`; null for PV). Both are additive: no `schema_version` bump. The
new symbols join `effy_exports.py` (prefix `EFFY_`) in the same task (ADR-021
Amendment 2026-10-23).

### 12. The loss model is not changed

The waterfall, the effective series and every `effy_*` series stay as they are
(ADR-001, ADR-005, ADR-018 decision 7). A regression test asserts they are
identical with and without the new sensors.

### 13. Tests

All machines are pure and zero-mock. A simulator drives them with counters from
a battery of known efficiencies and a known standby, and the estimates must
converge to those values; replay and live operation give identical results; and
every case of decisions 4, 5 and 6 has its own scenario: a run of 2 slots, a
wobble below the hysteresis, a capacity change, an import below the standby
energy.

### 14. Out of scope

More than one battery system per entry; efficiency of mixed flows (PV with
charge, charge with export); any reset on a stack upgrade; correcting the BMS's
state of charge; any change in Shady or Chargy; separating efficiency by power
level or temperature.

## Consequences

- A configuration change **without** migration: an existing entry stops setting
  up until the human assigns the four counters in the options flow; the old keys
  are ignored. Entry ID, unique IDs and entity IDs stay; recorder statistics
  continue. The README, translations (en, de) and the tests that name the old
  lists change.
- The standby sensor is unaffected, but it now reads its BMS inputs from the two
  named pickers.
- Efficiency becomes available after the first pure run (charge, discharge) or
  the first valley span (battery, after two valleys, often a day or more), and
  the history recalculation fills it from up to 10 days at once.
- The estimates trail real time by at least 125 min, and the battery efficiency
  by the length of the span plus the rise out of the next valley.
- Efficiency depends on power level (A3): a sensor is an average over the powers
  that occurred in pure runs; it is not a figure for any given power.
- Roundtrip runs slightly high (decision 8).
- Counter resolution (10 Wh) and SoC resolution (1 point) leave noise in a
  single measurement; the kWh weight averages it out, and a valley span's
  Δstored error is bounded by one SoC point times the capacity.
- The history recalculation gets one more pass over a few thousand slots; its
  cost is small next to ADR-022's measured phases, and independent of them.

## Points that were assumptions, not questions to the human

For the Gate, because nothing in the grill rejected or confirmed them
explicitly: the derived power series (not the effective series) as the
measurement basis; the gross energy as the decay weight (decision 7); the
minimum of 15 for the hysteresis; half-slot boundaries for the span sums
(decision 6); the Wh/kWh unit check of the capacity entity.

## Open decisions

1. **An estimate above 100 %.** Per-slot noise and an SoC error can push a
   charge, discharge or battery estimate above 100 %. Option A: publish it as it
   is (an input is wrong; hiding it hides that). Option B: cap the state at 100
   %. Option C: do not count a measurement above 100 %. Recommended: A for
   charge and discharge (sums average the noise); the human decides for a valley
   span.

   ## Amendment — 2026-10-29

   **Reason:** The ADR left the case of a valley span open (the recommendation
   for charge and discharge was A; the human decides for a valley span).
   **Decision:** Option A for the **valley span** too: a battery efficiency
   above 100 % is published as it is, never capped and never discarded; the span
   counts with its measured value. This holds for all three estimates. **Decided
   by:** human (2026-10-29), settled for TASK-0024.

1. **The boundary convention of decision 6** (half boundary slots) is the Lead
   Agent's; the simulator of decision 13 must show a bias below 0.5 points
   before the task is done, or the convention changes.

   ## Amendment — 2026-10-29

   **Reason:** Open decision 2 required the simulator of decision 13 to show a
   bias below 0.5 points for the half-boundary-slot convention of decision 6.
   **Decision:** The convention stays (TASK-0024). A simulated battery of known
   efficiency (5-minute slots, SoC a whole number of points, counters that tick
   in 10 Wh, clouds that make the SoC dip, a capacity that jitters by 0.3 %)
   over 150 days, for eight batteries of 85 % to 97 %: half boundary slots give
   a bias of −0.05 points on average (−0.13 to +0.05, the worst single estimate
   0.62 points off); no boundary slot gives +0.19 (up to +0.31); whole boundary
   slots give −0.28 (down to −0.45). Details decided with it (◇, TASK-0024): of
   several equal highest SoC values the **latest** is the peak, so a dip between
   two equal tops is not "after the peak"; the first valley found after a reset
   may have begun before it (its end is still a valid boundary: the balance
   holds for any two boundaries); the machine takes only slots that are 125 min
   old, so a span closes at the rise slot's own age, not at the age of the
   valley's beginning (later by the rise, never earlier than decision 6 says).
   **Decided by:** Lead Agent (confirmed by the simulator; the human confirms at
   the next Gate).

1. **Options flow on a failed entry — settled 2026-10-27 (TASK-0017 stage 0,
   from the source of Home Assistant 2026.10.4 and 2026.3.0).** (a) The options
   flow is created without a check of the entry's state and `supports_options`
   depends only on the handler, so a failed entry offers it; whether the
   frontend's "Configure" button is shown for such an entry is not verified. (b)
   Saving reloads a failed entry through `OptionsFlowWithReload`, which exists
   in both versions; an update listener of Effy's own cannot do it (Home
   Assistant runs the entry's unload callbacks when its setup fails, so the
   listener is gone) and Home Assistant raises `ValueError` if an entry has a
   listener next to `OptionsFlowWithReload`. Effy therefore has no update
   listener.

1. **Persistence mechanism** of decision 9 — settled 2026-10-27 (TASK-0020) for
   the charge and discharge machine: there is no store of its own. Every
   efficiency sensor keeps the **whole** machine dump as `ExtraStoredData`
   (`run_restore_data`), like the standby sensor does; the coordinator holds the
   live state, takes the dump with the later `last_slot` when two sensors
   restore, and the first slot job after that catches up at most 3 days. The
   valley machine decides for itself in TASK-0025.
