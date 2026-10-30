# Task: Find and fix why the standby power reads far below the drain the SoC shows

- **Status:** todo — two stages: **Stage 1 evaluation** (no code change), then
  **Stage 2 fix** (human request 2026-10-30)
- **Capability:** CAP-2 (standby power), CAP-8 and CAP-9 (consumers of the idea
  of drain)
- **Related ADRs:** \[ADR-018 (Option A, the SoC-based estimate it set aside),
  ADR-023 decisions 3, 5 and 6, ADR-001, ADR-005\]
- **Dependencies:** — (independent of TASK-0033 and TASK-0034)
- **Gate:** a **defect** found in Stage 1 is fixed in Stage 2 without a stop. If
  the finding is instead that the sensor is right for what it measures, then
  what it *should* measure is a product decision: the worker states the options
  and a recommendation, and the **human chooses** before Stage 2 code is
  written. An ADR-018 or ADR-023 amendment is part of Stage 2.

## Goal

Stage 1: find out whether `sensor.effy_standby_power` is wrong, or right for
what it measures and merely not the number the human expects. Stage 2: make the
sensors say and do the right thing, so that the standby the human sees, the
drain the SoC shows and what the efficiencies subtract are consistent, or are
explained where they cannot be.

## Context (the human's report, 2026-10-30)

- The standby sensor reads **1.8** (the human wrote "1.8 %"; the sensor's unit
  is W, so W is assumed here and to be confirmed by the human).
- The battery capacity is 2.1 kWh. In the screenshot of the SoC (Solakon ONE, 27
  Oct 13:30 to 28 Oct 09:20) the SoC falls one point in about 3 hours while
  nothing is happening. One point of 2.1 kWh is 21 Wh, so about **7 W**.
- The same window of the discharge counter (second screenshot) rises 10 Wh in
  about 6 hours, so about **1.7 W**.

Rough readings from the screenshots, to be redone from recorder data (the
screenshots are 1-point and 10 Wh resolution, so every figure is +/- one step):

| Window | SoC | Discharge counter | Implied by SoC (2.1 kWh) | Implied by the counter |
| -- | -- | -- | -- | -- |
| 15:15 to 15:35, a load | 18 % to 15 % (3 points, about 63 Wh) | 198.19 to 198.25 kWh (60 Wh) | — | — |
| 15:35 to 06:20, quiet | 15 % to 11 % (4 points, about 84 Wh) | 198.25 to 198.28 kWh (30 Wh) | about 5.7 W | about 2.0 W |
| Steps late in the night | one point per 2 h 20 min | no counter step | about 9 W | — |

Two things stand out. During the load the SoC and the counter agree, so the 2.1
kWh is plausible at 15 to 18 %. During the quiet stretch the SoC falls about
three times faster than the counter rises, and the standby sensor (1.8 W) agrees
with the counter, not with the SoC. That is what ADR-018's machine is built to
do (the time between two peaks of one counter step), so the first suspicion is
the meaning of the number, not a bug in the machine.

## Candidate causes to confirm or refute

1. **Energy the discharge counter never sees.** The battery or its BMS powers
   itself (BMS, balancing, the inverter's idle supply taken on the battery side
   of the counter), so that drain shows in the SoC and not in the counter.
1. **The SoC is not linear at a low charge.** It may be a voltage estimate or
   rescaled, so one point at 10 to 15 % is not 21 Wh, and the usable capacity
   differs from the nominal 2.1 kWh there.
1. **SoC resolution and rounding.** With one-point steps the estimate over a few
   hours is +/- 1 point, that is +/- 21 Wh.
1. **Self-discharge or a measurement offset** that no counter and no model of
   Effy accounts for.
1. **A defect in Effy's reading of the counter** (the machine's peak heights,
   the learned resolution, the 5 % weight, the choice of source). Unlikely: 10
   Wh per 6 h is 1.7 W and the sensor shows 1.8 W.

## Acceptance Criteria

### Stage 1 — evaluation (no file under `custom_components/` changes)

- Given recorder data of the window of the screenshots, then the report
  **tabulates per SoC step**: start, the time to the next step, the energy
  implied at the stated capacity, the power it gives, the discharge counter's
  power over the same stretch, and the standby sensor's value then. The figures
  above are replaced by figures from data.
- Given the load at about 15:15 to 15:35 (and any other load in the data), then
  the report says whether 2.1 kWh is consistent with the counter in every SoC
  band the data covers, and at which SoC the two stop agreeing.
- Given the exported 5-minute statistics of the discharge counter (or its
  derived power series) for the window, then the pure functions of
  `calculation.py` (`drain_feed` and the peak adapters) are replayed offline
  with a scratch script outside the repository's `custom_components/`, and the
  report states whether they reproduce the 1.8 W (the machine is right for its
  input) or not (a defect, described with the data that shows it).
- Given the candidate causes above, then the report marks each as **confirmed**,
  **refuted** or **cannot be decided from the data**, with the evidence. At
  least one clean quiet stretch of 12 hours or more with no PV, no import and no
  discharge is compared in SoC and in the counter; the human's vendor
  documentation or integration description is consulted for what the discharge
  counter includes (battery side or AC side, and whether the system's own
  consumption is in it).
- Given the finding, then the report says in one sentence **what
  `sensor.effy_standby_power` measures** ("the energy the discharge counter
  registers while nothing else flows") and whether that is what its consumers
  need. The consumers are the charge and discharge efficiency (which subtract
  it, ADR-023 decision 5) and the outside projects that read the sensor. It also
  says in which direction an underestimated standby moves the charge and the
  discharge efficiency (to be derived, not assumed), and that the battery
  efficiency, which uses SoC and capacity, sees the real drain.
- Given the options the finding leaves, then the report lays them side by side
  with their cost and **recommends one**, for the human to confirm or change:
  - **A** keep the sensor as it is and say in the README what it measures;
  - **B** add a SoC-based drain estimate (ADR-018's Option A, now possible
    because SoC and capacity are configured for the battery efficiency) as a
    second sensor or as a cross-check beside the first;
  - **C** change what the standby machine counts or what the efficiencies
    subtract.
- Given the report is written into this file, then, for a defect, Stage 2 starts
  at once, and otherwise after the human's choice.

### Stage 2 — fix (tests first, each shown failing against the code as it was)

- Given Stage 1 found a **defect** in how the machine reads the counter (a wrong
  peak height, resolution, source or weight), then a test built from the human's
  exported counter data (trimmed to what shows it, in `tests/`) fails first, and
  the fix makes the machine give the value the data supports.
- Given the human chose **A**, then the README and the sensor's own description
  say what `sensor.effy_standby_power` measures and what it does not (the drain
  the SoC shows beyond it), ADR-018 gets an amendment, and a test pins the
  documented attributes. No behaviour of the sensor changes.
- Given the human chose **B**, then a second sensor (or an attribute on the
  first) reports the SoC-based quiet drain in W, from quiet stretches of the SoC
  and capacity series, built as a pure machine in `calculation.py` with the
  human's data as a fixture, fed and restored like the other machines, published
  in the contract (`effy_exports.py` and its pinned test, `catalog.py`),
  translated (en, de) and described in the README. ADR-018 or a new ADR records
  the decision.
- Given the human chose **C**, then the change is built test first, ADR-018 and
  ADR-023 are amended, the history rebuilds (TASK-0022, TASK-0027) still equal a
  fresh replay, and the README says what the efficiencies subtract.
- Given any option, then the effect on the charge and discharge efficiency (the
  direction Stage 1 derived) is stated in the README, and a test shows the
  efficiencies for the human's data with the chosen standby.
- Given the fix, then the whole suite, `ruff`, `mypy` and `mdformat` pass and no
  existing assertion is weakened.
- Given the fix is released, then the **human compares the sensors with the SoC
  again** over a quiet night, and the result is written into the findings. Until
  then the task stays in `review`, not `done`.

## Evidence the human has to supply

- The date and time range of the screenshots, or a longer quiet stretch, with
  the entity IDs of the SoC, the discharge counter and the standby sensor.
- An export of the 5-minute statistics for those three entities and for
  `sensor.effy_*_power` of the discharge counter over at least three days (the
  statistics table, `statistics_short_term`, or the history download as CSV).
- Confirmation of the standby sensor's unit and of the battery's nameplate and
  usable capacity.
- Optionally one deliberate long quiet stretch (no load, no charge, no PV), with
  the SoC and the counter noted at its start and its end.

## Estimated File / Module Footprint (hint, not a commitment)

Stage 1: none in production; the report is written into this file and a scratch
script (not committed) replays `drain_feed` on the exported data. Stage 2,
depending on the option: `README.md`, `adr/018-*.md` and `adr/023-*.md`
(amendments), `tasks/adr-summary.md`, `GLOSSARY.md`; for B also
`calculation.py`, `history.py`, `coordinator.py`, `sensor.py`, `catalog.py`,
`effy_exports.py`, `translations/*.json` and new tests; for C `calculation.py`,
`history.py` and their tests.

## Definition of Done

- The findings section below is complete and states the measured drain, the
  confirmed cause or causes, the meaning of the sensor, the human's choice and
  what Stage 2 built
- Stage 2 tests were shown failing first; `pytest`, `ruff format --check`,
  `ruff check`, `mypy` and `mdformat --check` pass
- Docs updated (README, the ADR amendments, translations en and de where text
  changes) · no open ADR conflicts
- The human's comparison over a quiet night is recorded
- `Delivered Artifacts` block completed and accurate
- No new external dependencies

## Consumed Interfaces

- `drain_feed`, `DrainState` and the peak adapters in
  `custom_components/effy/calculation.py` (→ TASK-0005); read in Stage 1,
  changed in Stage 2 only for a defect or option C.
- `sensor.effy_standby_power` and its attributes `last_counted_value`,
  `last_drain_interval_end`, `drain_source`, `counted_intervals` (→ TASK-0005).

## Findings

<!-- Filled by the worker after the evidence is in. -->

## Delivered Artifacts

<!-- Filled by the worker: what Stage 2 changed, exactly. -->
