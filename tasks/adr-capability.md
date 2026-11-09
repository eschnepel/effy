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

**Extension 2026-10-23 (human request, TASK-0016):** the contract's symbols are
one standalone file, `effy_exports.py`, that other projects copy and Effy
imports; every symbol is prefixed `EFFY_`.

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

## CAP-7 — The battery system's counters configured by function *(proposed 2026-10-23; Gate 1 pending; Draft ADR-023, amends ADR-019 and ADR-021)*

**What the user sees:** the options show four sensor pickers (grid import,
discharge, charge, grid export) where they showed a BMS-input list and an output
list, and nothing that Effy calculates changes. An entry that still has the old
lists does not set up and says in the log and UI to assign the four counters; no
statistics are written meanwhile. Every source series in the published `sensors`
list carries a `function` (`grid_import`, `discharge`, `charge`, `grid_export`,
or null for a PV input).

**End-to-end demonstration:** config and options flow tests with the validation
of ADR-023 decision 1 (an output is required, no sensor in two roles, a sensor
in no list twice); the CAP-1 equivalence harness gives identical results with
the same sources assigned by function; an entry with the old keys refuses setup
and writes nothing; the published entries carry `function`.

**Scope (in):** the four pickers and their validation, translations (en, de),
README, the roles inside the integration (the BMS inputs and outputs the
waterfall and the standby sensor use), the refusal of an unconfigured entry, the
`function` key and its symbols in `effy_exports.py`.

**Scope (out):** any efficiency; SoC, capacity and valley hysteresis (CAP-9);
any migration of the old lists (decided: none).

## CAP-8 — Charge and discharge efficiency *(proposed 2026-10-23; Gate 1 pending; Draft ADR-023; needs CAP-7 and the standby sensor of CAP-2)*

**What the user sees:** `sensor.effy_charge_efficiency` (grid import to charge)
and `sensor.effy_discharge_efficiency` (discharge to grid export) in %, each the
battery system's own measured value less the standby energy, adapting at 5 % per
kWh. Both are unavailable until a first pure run was measured and the standby
sensor has a value; both survive a restart; the history recalculation fills them
from the recorder's 5-minute statistics.

**End-to-end demonstration:** a simulator drives the pure machine with counters
from a battery of known efficiencies and a known standby and the estimates
converge to them; replay and live operation agree; a pure run of two slots
counts nothing; a slot whose import is not larger than its standby energy does
not count; a restart is caught up from the last processed slot; the published
`sensors` list shows both sensors.

**Scope (in):** the pure-slot classification and pure runs, the decayed sums,
the two sensors, restore and catch-up, the history rebuild, the published series
and their symbols.

**Scope (out):** battery and roundtrip efficiency, SoC and capacity (CAP-9);
mixed flows with PV; any change to the loss model.

## CAP-9 — Battery and roundtrip efficiency *(proposed 2026-10-23; Gate 1 pending; Draft ADR-023; battery efficiency needs CAP-7, roundtrip also CAP-8)*

**What the user sees:** two more pickers (state of charge, battery capacity) and
a valley hysteresis number in the options, and two sensors:
`sensor.effy_battery_efficiency` (discharge over charge less the change in
stored energy between two valleys of the state of charge, no standby) and
`sensor.effy_roundtrip_efficiency` (the product of the three, unavailable until
all three have a value).

**End-to-end demonstration:** a simulator of a battery of known efficiency over
several days converges; a dip below the hysteresis widens a valley instead of
splitting it; a capacity change inside a span drops that span only; a missing
SoC slot resets the detection; the roundtrip equals the product; replay and live
operation agree; the published `sensors` list shows both sensors.

**Scope (in):** the SoC and capacity options and their validation, the valley
machine and spans, the battery sensor, the roundtrip sensor, restore and
catch-up, the history rebuild, the published series and their symbols.

**Scope (out):** a reset of the estimates on a stack upgrade; correcting the
BMS's state of charge; more than one battery system per entry.

## CAP-10 — Recalculations write only what changed *(Gate 1 approved 2026-10-25, human; ADR-024 accepted, amends ADR-012)*

**What the user sees:** pressing the history recalculation button a second time,
with the settings unchanged, takes seconds instead of a minute and a half (28
days on a Raspberry Pi 5), and the log says how many rows were already up to
date. The slot job does the same for its 5-minute window. After a change of
settings every value is new, and the run takes what it takes today.
`sensor.effy_recalculated_from` shows the earliest slot whose stored value
actually changed (not the start of the range that was recomputed), and stays as
it is when nothing changed, so Shady and Chargy re-read only what moved. Every
stored `effy_*` row ends up exactly as before; nothing is configured
differently.

**End-to-end demonstration:** the slot-timer equivalence harness (CAP-1,
TASK-0004) green; a history recalculation run twice on the in-memory recorder
writes nothing the second time and reports `None`; a changed raw value moves
`recalculated_from` to exactly that slot; the Pi's own record of a repeated
recalculation (the detail block's `stored rows` jobs show what the read costs).

**Scope (in):** reading the stored rows, the rule of "unchanged", writing only
the rest (short-term and hourly), the earliest change as `recalculated_from`,
the fail-safe.

**Scope (out):** what Effy computes or writes (values, range, series), the live
states, progress lines, smaller batches, numpy (ADR-022's outcome), any change
to the sensor's attributes (ADR-021).

## CAP-11 — A recalculation's inputs are held once and can be replayed *(proposed 2026-10-31 by the human's request; Gate 1: confirmation of this text pending; Draft ADR-025)*

**What the user sees:** nothing changes in the sensors. With the debug export
switched on, pressing **Re-calculate History** also writes one file with
everything the recalculation read from the recorder and the configuration it ran
with, and anyone with that file can reproduce the recalculation offline, in a
test, without access to the instance. The logs say how many reads were served
from memory.

**End-to-end demonstration:** a history recalculation on the in-memory recorder
with and without the cache writes identical rows (the CAP-1 equivalence harness
and the TASK-0035 recorder rig green); after a clear and one recalculation the
cache holds exactly what that recalculation read; the export of such a run,
replayed from the file alone, writes the same rows and reports the same
`recalculated_from`.

**Scope (in):** a pure cache for the source data Effy reads (raw states,
statistics, anchors, capacity states, metadata), `clear()` and `snapshot()`, the
export file, its format and its switch, the replay.

**Scope (out):** caching what Effy calculates or writes, persisting the cache
across a restart (unless the human asks), a speed target (the history
recalculation takes 3.92 s when repeated; ADR-022's measurement), any change to
what Effy writes or publishes.

## CAP-12 — The EPS/UPS port in the loss *(proposed 2026-11-06 by the human's request; Gate 1 approved 2026-11-06, smoothing option A; ADR-026 accepted, amends ADR-013, ADR-019, ADR-021, ADR-023; TASK-0039 to TASK-0044, Gate 2 approved 2026-11-06)*

**What the user sees:** one more optional picker in the options, the EPS/UPS
sensor (a signed power sensor: positive when energy leaves through the port,
negative when another inverter feeds it). What the backup loads draw is no
longer counted as loss, and what the other inverter feeds in is counted as an
input that carries an equal loss share. `sensor.effy_<eps sensor>` shows that
input half's effective value (0 while the port exports), and
`sensor.effy_<eps sensor>_smoothed` the gap-interpolated signed series; both are
published with role and function `eps`. Without the picker nothing changes.

**End-to-end demonstration:** a slot with 200 W out through the port lowers the
loss by 200 W; a slot with 150 W in raises the input total by 150 W and gives
the EPS input an equal share like any input; Σ effective inputs = Σ outputs; the
same sources without an EPS sensor give identical results (the CAP-1 equivalence
harness, full recalculation and slot timer); an active EPS sensor makes a slot
impure for the efficiency sensors and breaks a drain interval; the published
`sensors` list carries the entry.

**Scope (in):** the picker and its validation, the signed split, the formula
(README, glossary), the waterfall and the slot timer and history path seeing
both halves, the activity reading of standby and efficiency including the EPS
sensor, smoothing of its signed series, the effective and the smoothed sensor
and their published entries (`effy_exports.py`), translations (en, de), tests.

**Scope (out):** two separate EPS counters or an energy counter; gross in-slot
flows; an EPS efficiency or standby sensor; more than one EPS port; a tolerance
for small idle readings; any change in Shady or Chargy.

## CAP-13 — Effective PV sum and PV delta integrals *(assigned by the Lead 2026-11-06 for the human's TASK-0045; re-scoped by the human in a design interview 2026-11-07, agreed in chat; ADR-027)*

**What the user sees:** one sensor that adds up the effective values of all PV
inputs, `Effective PV Sum now` (power, rewritten for changed slots like every
effective series), and the energy those inputs delivered, as PV delta integrals:
per PV input `<name> delta integral today`, and combined
`PV delta integral today` and `PV delta integral total` (energy, Home Assistant
compiles their statistics; the delta of any period is the `change` query). They
are derived once from the history, follow every correction Effy makes, and are
published with role `pv`. A change of the configured sensors that influence the
formula runs a full history recalculation by itself. The EPS sensor of CAP-12 is
not a PV input and is not in any of them.

**End-to-end demonstration:** with two PV inputs in mixed units, a full
recalculation and the derivation give both inputs and the combined integrals a
history; the next slot books its energy; a formula-relevant option change
recalculates by itself; a button run after a correction books it in today and
total, and the combined integrals equal the per-input ones added up; an entry
without a PV input has none of them.

**Scope (in):** the tasks `TASK-0045a` to `TASK-0045g` and `TASK-0046` (added
2026-11-09: `PV share now`, the percentage of the effective inputs that came
from PV and the EPS input in the last completed slot; the EPS input is on the PV
side there only). **Scope (out):** any addition over anything but the PV inputs,
except that share; any change of the per-input effective sensors.

**Order:** after CAP-12; both touch `sensor.py`, `catalog.py`,
`effy_exports.py`, `coordinator.py`, `README.md` and the translations, so they
run strictly one after the other. Inside CAP-13 the tasks also share
`history.py`, so they run in the order of their dependencies, one at a time.

## CAP-14 — Additional sensors with derived power and smoothed series *(proposed 2026-11-09 by the human's request; Gate 1 pending; Draft ADR-028; independent of CAP-13)*

**What the user sees:** an optional **Additional sensors** list in the options.
For each sensor in it Effy writes the series an input gets before the loss is
distributed, and nothing else: `sensor.effy_<slug>_power` (redistributed power
of an energy counter, idle stretches as 0) or `sensor.effy_<slug>_smoothed` (a
power sensor with short gaps bridged). They are in no loss, effective value,
standby, efficiency or battery calculation. Use case: Effy's counter and gap
logic on the house grid import sensor. They are published with role `other` for
dependent projects.

**End-to-end demonstration:** add a low-resolution grid import counter and a
power sensor with gaps to the list, press the history button; both series have a
history, the counter's idle days read 0, the gaps of up to two slots are filled,
the effective rows of the entry equal those of the entry without the list;
remove a sensor and its Effy sensor leaves the registry; a sensor that is
already an input is refused.

**Scope (in):** `TASK-0047`. **Scope (out):** an effective, loss or share series
for these sensors; any influence on the loss; a role other than `other`.

**Order:** after CAP-13 (shared files `sensor.py`, `catalog.py`, `history.py`,
`effy_exports.py`, `coordinator.py`, `config_flow.py`, `README.md`, the
translations), one task at a time.
