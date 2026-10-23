# Capabilities (MVP delta for ADR-014 amendment, 2026-10-05)

## CAP-1 — Energy-conserving derived power from the slot timer

**What the user sees:** for a low-resolution energy meter (e.g. BMS kWh counter,
0.01 kWh ticks every 19–35 min), `effy_*_power` / `effy_*` statistics written by
the 5-minute slot timer form the same smooth plateaus as a manual full recalc —
no zero gaps between ticks, and the area under the curve over each jump equals
the raw tick (10 Wh ⇒ 10 Wh).

**End-to-end demonstration:** replay the screenshot scenario (7 raw readings,
slot-timer cycles every 5 min from 10:00:05 to 13:05:05). Final stored derived
power equals a full recalc at the same time for every closed slot; total area =
60 Wh (currently 30.2 Wh). `repro_derived_power.py` is the scenario seed.

**Scope (in):** jump-triggered, global, bounded extension of the slot-timer
rewrite range per ADR-014 Amendment items 1–7 (incl. offline-recovery jumps).

**Scope (out, not decided):** 15-vs-20-min unaligned window start; in-progress
slot written / pushed as live value (ADR-016); hourly long-term statistics
(still full-recalc only).

## CAP-2 — Battery-system standby power sensor *(proposed 2026-10-13, pending Gate 1)*

**What the user sees:** a `sensor.effy_*` entity reporting the power the battery
system itself draws to keep running (W), usable by other integrations — first
the sibling project Chargy, which would pick it as its standby power instead of
estimating it itself.

**End-to-end demonstration:** not yet defined — it depends on the design chosen
in the Draft ADR that TASK-0005 produces (SoC-based, flow-balance-based or
pass-through). TASK-0005 is the investigation; implementation follows only after
a human has confirmed that ADR.

**Scope (in):** investigate whether standby power can be separated from the
conversion/wiring loss Effy already distributes (ADR-001, ADR-005); expose the
result as a sensor; state the consequence for consumers that use Effy's
effective series (double counting).

**Scope (out, not decided):** any Chargy-side change (owned by the Chargy
project); changing the existing loss model or the existing `effy_*` series
unless the confirmed ADR says so.

## CAP-3 — Slot trigger synchronized with the recorder *(Gate 1 approved 2026-10-16 for TASK-0006 only; TASK-0007a/b pending Gate 2)*

**What the user sees:** nothing new on a healthy system. The `effy_*` statistics
of the last completed slot are never missing because the recent recalculation
started a few seconds too early, and users no longer have to add a recorder
exclusion for `sensor.effy_*` (ADR-017).

**End-to-end demonstration:** a simulated recorder that compiles each slot 10 s
after its boundary, with the slot timer at boundary + 5 s; after the change the
last completed slot's statistics are present in the same cycle.

**Scope (in):** how the recent recalculation is triggered (the recorder's
5-minute statistics event, the wall-clock timer kept as a watchdog), as Shady
did in its TASK-0041, with the Home Assistant minimum raised to Shady's
(TASK-0006), with the recent and the history recalculation serialized by one
lock; then retiring the recorder exclusion requirement (TASK-0007a and
TASK-0007b, decided 2026-10-16, pending Gate 2).

**Scope (out, not decided):** anything in the loss model or the rewrite range
(ADR-012/014).

## CAP-4 — Inputs configured by role *(Gate 1 approved 2026-10-16)*

**What the user sees:** two input lists when setting Effy up, PV inputs and BMS
inputs, instead of one mixed list. An existing installation keeps working and
shows its former inputs as PV inputs. Nothing else changes: every `effy_*` value
is as before.

**End-to-end demonstration:** the config and options flow accept the two lists
with the validation of ADR-019, and the CAP-1 equivalence harness gives the same
results with the same sources split by role (TASK-0008).

**Scope (in):** the configuration split, its translations and README, exposing
the two roles inside the integration; a silent migration puts an existing input
list into the PV list (human, 2026-10-16; the BMS sensors are reassigned in the
options flow).

**Scope (out):** any use of the role; it serves the standby power sensor
(CAP-2).
