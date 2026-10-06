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
