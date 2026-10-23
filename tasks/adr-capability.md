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

## CAP-2 — Battery-system standby power sensor *(proposed 2026-10-13; design ADR-018 confirmed 2026-10-17, implemented in TASK-0005 Stage 2)*

**What the user sees:** a `sensor.effy_*` entity reporting the power the battery
system itself draws to keep running (W), usable by other integrations — first
the sibling project Chargy, which would pick it as its standby power instead of
estimating it itself.

**End-to-end demonstration:** a quiet night in which a BMS counter with 10 Wh
steps jumps every 50 minutes: `sensor.effy_standby_power` reads 12 W once the
first drain interval is confirmed (ADR-018; `tests/test_standby_glue.py`).

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

## CAP-5 — Configuration published for dependent projects *(approved 2026-10-18, Gate 1)*

**What the user sees:** `sensor.effy_recalculated_from` carries attributes that
say which Effy sensors exist (each with direction, role, series, source and the
aggregations its short-term statistics offer) and the configured
`max_history_days`, so Shady and Chargy can offer or pre-select the right Effy
sensors and a matching history setting. The sensor's update is the last step of
every recalculation, and after a reboot it comes back with its previous state
and attributes, marked as restored until the next recalculation.

**End-to-end demonstration:** an entry with PV and BMS inputs and an output:
after a recalculation the attributes list every `sensor.effy_*` with its
metadata; a late-created sensor appears at the next recalculation; after a
restart every Effy sensor is back with its previous state, and the
recalculated-from sensor with its attributes plus `restored_as_of` and
`restored_at`, gone after the next recalculation (TASK-0009, ADR-021).

**Scope (in):** the attribute contract on the existing sensor, the closing-step
order (ADR-012 Amendment 2026-10-18), the restore, README and glossary, tests.

**Scope (out):** any change in Shady or Chargy (owned by those projects); any
change to the loss model or to what Effy writes into statistics.

## CAP-6 — A history recalculation whose time is known, and short enough *(proposed 2026-10-19; Gate 1 pending, except that the human approved starting TASK-0010 on 2026-10-19; human request: a history recalculation takes about 5 minutes on a Raspberry Pi 5; design Draft ADR-022)*

**What the user sees:** after pressing the recalculation button, the log says
where the time went (reading the recorder, computing, writing statistics). If
the computing turns out to be the cause, the history recalculation with the
default 28 history days is several times faster, and the event loop is not held
while it computes. Every `effy_*` value stays as before (within the tolerance of
ADR-022); nothing is configured differently.

**End-to-end demonstration:** the Pi's own log line of a history recalculation
(TASK-0010), the human's reading of it against the thresholds of ADR-022 (gate
G-perf), and — only if the gate opens — the same line again after the change,
with the slot-timer equivalence harness (CAP-1, TASK-0004) green.

**Scope (in):** timing of the three phases of a recalculation; the gate; if
released, an executor job for the compute phase and an array-native compute path
with numpy.

**Scope (out, not decided):** a remedy for slow recorder reads or writes (a new
ADR if the measurement points there); cheaper parsing of the raw states without
numpy (untested); any change to the loss model, to what Effy writes into
statistics, to the rewrite range, or to anything Shady and Chargy see.
