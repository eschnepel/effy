# Task: Find and fix why charge and discharge efficiency stay unavailable after a planned night charge

- **Status:** todo — two stages: **Stage 1 evaluation** (no code change), then
  **Stage 2 fix** (human request 2026-10-30)
- **Capability:** CAP-8
- **Related ADRs:** [ADR-023 decisions 3, 4, 5 and 7, ADR-018 D3, ADR-020]
- **Dependencies:** [TASK-0033-standby-power-history-missing] (`done`: a
  leftover recorder exclusion hid the standby history and the human removed it;
  the slot count it would have delivered is counted in Stage 1 here, from the
  export, and TASK-0035 may still affect the hourly rows)
- **Gate:** Stage 2 follows Stage 1 without a stop. The exception is a finding
  that the design itself (a rule of ADR-023 such as the 3-slot minimum, the
  125-minute delay or the strict purity) is what keeps the sensor empty on this
  system: changing a rule of an accepted ADR is the human's decision, so the
  worker states the options and the human picks before that code is written.

## Goal

Stage 1: find out why `sensor.effy_charge_efficiency` and
`sensor.effy_discharge_efficiency` are `unavailable`, in particular why a charge
the human planned at night to provoke a measurement did not produce one, and
name the first condition of ADR-023 that failed. Stage 2: fix what Stage 1
found, so that a charge like the human's produces a value, or, where the system
is simply too small or too noisy for the rule, so that the sensor says why it
has none.

## Context (the human's report, 2026-10-30)

- Both sensors are "not available".
- The human charged the battery on purpose at night, and "it does not show up".
  The battery is small (2.1 kWh), so the charge may have been short.
- The standby sensor has a value (1.8) but had no history (TASK-0033: a leftover
  recorder exclusion, removed by the human since).

What makes a charge efficiency measurement appear (ADR-023, README), all of it
at once:

1. the grid import and the charge counter are configured, and Effy's feed has
   run (`efficiency_enabled`, a first slot job after a restore);
1. a **pure charge run**: consecutive 5-minute slots in which only the grid
   import and the charge counter are active (not a PV input, not the discharge
   or the grid export counter), at least **3** of them, because the first and
   the last slot of a run never count;
1. every counted slot is **at least 125 minutes old**;
1. every counted slot has a **recorded `sensor.effy_standby_power` statistic**,
   and its import energy is larger than its standby energy;
1. the sensor is `available` only when its own estimate exists **and** the
   standby estimate on the coordinator exists.

README: because a counter that does not tick is redistributed backwards up to
two hours when it next ticks, the idle counters of a short charge look active in
every slot of it, and only runs longer than about two hours leave pure slots.
While the sensor is unavailable it shows **no attributes**
(`last_processed_slot`, `counted_runs`), so the human cannot see how far the
machine got.

## Candidate causes to confirm or refute

1. **No standby statistic for the slots** (the recorder does not hold
   `sensor.effy_standby_power`; TASK-0033). Every slot is then without standby
   energy and none counts, however pure the charge was.
1. **The charge was too short or too slow** to leave 3 pure slots, given the 10
   Wh resolution of the counters and the backward redistribution of the idle
   counters (the discharge and grid export counter tick later and look active in
   the slots of the charge).
1. **The slots were not pure for another reason**: a PV input with a small
   non-zero night reading (ADR-023 has no tolerance for it, deliberately), the
   discharge counter ticking during the charge, a load drawing from the battery.
1. **Not enough time had passed**: fewer than 125 minutes between the last slot
   of the run and the moment the human looked, or the run's last slot (which
   never counts) was the one that mattered.
1. **The feed did not run or failed**: no slot job since the restart, an
   exception logged as
   `Effy: updating the charge and discharge efficiency failed`, a restore that
   started empty, the counters not configured by function as ADR-023 decision 1
   requires.
1. **The counters configured are not the ones that tick** during a grid charge
   (for example a household grid import counter instead of the battery system's
   AC input), so no slot is ever "pure charge".
1. **Discharge efficiency** has its own conditions (the discharge and the grid
   export counter active alone, a run of 3 or more slots) and was probably never
   provoked: no test was done for it.

## Acceptance Criteria

### Stage 1 — evaluation (no file under `custom_components/` changes)

- Given the human's description of the night charge (start, end, power, SoC
  before and after) and the recorder statistics of the four counters, of every
  PV input and of the standby sensor from two hours before the charge to two
  hours after the time the human looked, then the report lists **for each
  5-minute slot**: the energy of each counter (kWh), which sensors count as
  active, whether a standby statistic exists, and the slot's class (pure charge,
  or impure with the reason). The class comes from replaying the pure functions
  of `calculation.py` (`run_feed` and its slot classification) offline with a
  scratch script outside `custom_components/`.
- Given that replay, then the report says **how many slots counted and how many
  runs formed**, and names the **first condition that failed** from the list in
  the context (1 to 5), with the slots that show it. If the replay counts a run
  while the live sensor stays unavailable, the difference (restore, feed not
  running, an exception, the 3-day catch-up window, the standby estimate) is
  found and named.
- Given the candidate causes above, then each is marked **confirmed**,
  **refuted** or **cannot be decided from the data**, with the evidence, and the
  logs of the slot jobs (`custom_components.effy.coordinator`,
  `custom_components.effy.history`) for the period are read for errors.
- Given the finding, then the report states **what charge the human would have
  to make** (minimum duration and power for a 2.1 kWh battery, with the 10 Wh
  counters) for a measurement to appear, and how long after it the sensor shows
  a value, so that the next test is designed from facts. It also states what the
  equivalent test for the discharge efficiency is, and whether the human's
  system can provide it (a discharge to the grid or the household counted by the
  grid export counter, with no other counter active).
- Given that an unavailable efficiency sensor shows no attributes, then the
  report states whether that made the diagnosis harder here, and which of these
  Stage 2 builds: attributes while unavailable (slots seen, slots pure, slots
  dropped by reason), a debug log per slot class, or neither.
- Given the finding, then the report states whether it is **how the design
  works** (a short charge on a small battery cannot form a run, ADR-023
  Consequences), a **configuration** problem (TASK-0033 or the counters), or a
  **defect**; for a defect it describes the failing behaviour and the test that
  would pin it.
- Given the report is written into this file, then Stage 2 starts.

### Stage 2 — fix (tests first, each shown failing against the code as it was)

- Given the first failed condition is a **defect** (a slot classified wrongly, a
  statistic read from the wrong series or unit, a restore or catch-up that loses
  the run, an exception that stops the feed, an availability rule that stays
  false when it should not), then a test built from the human's slots (trimmed
  to what shows it, in `tests/`) fails first, and the fix makes the same slots
  produce a measurement and an available sensor.
- Given the cause is the **standby statistic missing** (TASK-0033), then this
  task adds no second fix for it; it adds a test that the efficiency feed over a
  window with the standby statistic present counts the run the human made, and
  the human's repeated charge after TASK-0033's fix is the verification.
- Given the cause is a **configuration** (counters that do not tick during a
  grid charge, roles that do not match), then Effy says so: the log or the
  attributes name the counter that never showed flow in a charge, and the README
  states which counters the roles need.
- Given the cause is the **design** on this system (too short a charge, a PV
  input with a night reading, idle counters that look active), then the human
  chooses between keeping the rule and amending ADR-023 (for example a tolerance
  for tiny PV readings, or a different treatment of the redistribution); the
  chosen change is built test first with the amendment recorded in ADR-023, and
  the README states the minimum charge (duration and power) that produces a
  value on a battery of this size.
- Given an efficiency sensor is `unavailable`, then (unless Stage 1 shows it
  unneeded) its attributes still say how far the machine got:
  `last_processed_slot`, the number of slots read, of pure charge or discharge
  slots, the longest run so far, and the reason the last candidate slot was
  dropped (impure, no standby statistic, too young, run too short). The values
  are plain and the state stays `unavailable`, not unknown. Tests pin each
  reason, and the published contract (`effy_exports.py`) is untouched because
  these are attributes of the sensor itself.
- Given the fix, then the whole suite, `ruff`, `mypy` and `mdformat` pass, no
  existing assertion is weakened, and a mutation of the new rule is caught.
- Given the fix is released, then the **human repeats the night charge** as the
  README now describes it, and the result (a value, or the reason shown in the
  attributes) is written into the findings. Until then the task stays in
  `review`, not `done`.

## Evidence the human has to supply

- When the night charge started and ended, at what power, the SoC and the four
  counters' readings before and after, and when the human looked at the sensors.
- The configured entity IDs and their roles from the options flow (grid import,
  discharge, charge, grid export, every PV input, SoC, capacity), or the
  `sensors` attribute of `sensor.effy_recalculated_from`.
- An export of the 5-minute statistics from two hours before the charge until
  the time the human looked, for those entities, their `sensor.effy_*_power`
  series and `sensor.effy_standby_power` (TASK-0033 says how).
- The Home Assistant log for the period, filtered for `effy`.
- The state of `sensor.effy_charge_efficiency` and
  `sensor.effy_discharge_efficiency` (unavailable or unknown) and whether they
  existed before the last restart.

## Estimated File / Module Footprint (hint, not a commitment)

Stage 1: none in production; the report is written into this file and a scratch
script (not committed) replays `run_feed` on the exported data. Stage 2,
depending on the cause: `calculation.py` (classification, run rules),
`history.py` (reading and the feed), `coordinator.py`, `sensor.py` (the
availability and the attributes while unavailable), `README.md`, `adr/023-*.md`
(an amendment, if a rule changes), `tasks/adr-summary.md`, and new tests
(`test_efficiency_runs.py`, `test_efficiency_feed.py`,
`test_efficiency_sensor.py`).

## Definition of Done

- The findings section below is complete: the slot table, the first failed
  condition, the classification, the design of the next test, what Stage 2 built
- Stage 2 tests were shown failing first; `pytest`, `ruff format --check`,
  `ruff check`, `mypy` and `mdformat --check` pass
- Docs updated (README, an ADR-023 amendment where a rule changed) · no open ADR
  conflicts
- The human's repeated charge is recorded
- `Delivered Artifacts` block completed and accurate
- No new external dependencies

## Consumed Interfaces

- `run_feed`, `RunState`, `EffSlot`, the pure-slot classification in
  `custom_components/effy/calculation.py` (→ TASK-0019); read in Stage 1,
  changed in Stage 2 where the finding requires.
- `async_update_efficiency`, `async_read_efficiency_energy` and
  `async_read_drain_activity` in `custom_components/effy/history.py` (→
  TASK-0020); read, not changed.
- `EffyEfficiencySensor.available` in `custom_components/effy/sensor.py` (→
  TASK-0021); read, not changed.
- From TASK-0033: nothing; the number of slots with a standby statistic in the
  window is counted here (the sensor is recorded since the exclusion was
  removed, so older slots have none).

## Findings

<!-- Filled by the worker after the evidence is in. -->

## Delivered Artifacts

<!-- Filled by the worker: what Stage 2 changed, exactly. -->
