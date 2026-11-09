# Effy

A Home Assistant integration that takes the energy a PV and battery system loses
(the part its outputs do not account for), distributes that loss evenly across
the inputs that were active, and exposes the corrected values as sensors and
5-minute statistics.

## Language

### System and entities

**Input**: A source sensor for energy or power entering the system: a PV string,
the battery's discharge counter, the grid-import counter, or the EPS input.
_Avoid_: Producer

**PV input**: An input that is a PV string. It can never be a drain source: PV
active alone is loss and drain mixed.

**BMS input**: An input that is a BMS reading: the battery's discharge or the
grid import. Only a BMS input can be a drain source.

**Output**: A source sensor for energy or power leaving the system: the
battery's charge counter, the grid-export counter, or the EPS output. Every
output except the EPS output is a BMS reading. _Avoid_: Sink, consumer

**EPS port**: The inverter's emergency power supply (UPS) port. Energy leaves
the system through it to the backup loads and, when another inverter feeds it,
enters the system through it. _Avoid_: Backup port

**EPS sensor**: The one signed power sensor of the EPS port: positive when
energy leaves, negative when it enters. Neither a PV input nor a BMS reading,
and never a drain source. _Avoid_: EPS counter

**EPS output**: The positive part of the EPS sensor's value in a slot, an
output. **EPS input**: its negative part as a positive number, an input that
takes a loss share like any other. At most one of them is not zero in a slot.

**Source sensor**: The user's original entity that an input or output is read
from. _Avoid_: Source (unqualified), raw sensor

**Effy sensor**: Any `sensor.effy_*` entity Effy creates. _Avoid_: Derived
sensor

**Exports file**: The standalone file `effy_exports.py` that holds the symbols
of what Effy publishes: entity IDs, attribute keys and their values. Other
projects copy it; Effy imports it. _Avoid_: Contract module, shared constants

### Sensor families

**Energy-family sensor**: A source sensor that reports cumulative energy: a
counter in Wh or kWh. _Avoid_: Counter sensor

**Power-family sensor**: A source sensor that reports momentary power in W or
kW.

**Power equivalent**: An energy delta divided by the time it covers (a slot, or
the span between two jumps), as an average power. A power-family reading already
is one, so readings of both families can be compared and summed. _Avoid_:
W-equivalent

### Loss

**Loss**: For a slot, the inputs' total minus the outputs' total, never below
zero: everything the outputs do not account for, namely conversion, wiring,
measurement noise and standby power. Effy cannot tell these apart. _Avoid_:
Conversion loss

**Loss share**: The part of a slot's loss assigned to one input.

**Active sensor**: An input or output whose value in a slot is not zero.

**Active input**: An active sensor that is an input.

**Waterfall distribution**: The rule that gives every active input an equal loss
share and passes any share an input cannot carry on to the others.

**Effective value**: An input's value minus its loss share, never below zero.
_Avoid_: Net, corrected value

**Standby power**: The power the battery system itself draws to keep running, in
every mode and even when the battery is at its lowest allowed charge. Part of
the loss. _Avoid_: Standby drain, idle (for the battery system)

### Battery system counters

**Charge counter**: The BMS output that counts energy going into the battery,
measured on the battery side. _Avoid_: Battery in, battery export

**Discharge counter**: The BMS input that counts energy coming out of the
battery, measured on the battery side. _Avoid_: Battery out, battery import

**Grid import counter**: The BMS input that counts energy the battery system
takes from the grid. _Avoid_: Grid in

**Grid export counter**: The BMS output that counts energy the battery system
gives to the grid. _Avoid_: Grid out, to grid

### Efficiency

**Standby energy**: Standby power over a span of time.

**Pure charge slot**: A slot in which the grid import counter and the charge
counter are active and no other sensor is, PV inputs included.

**Pure discharge slot**: A slot in which the discharge counter and the grid
export counter are active and no other sensor is, PV inputs included.

**Pure run**: Consecutive pure charge slots, or consecutive pure discharge
slots, without the first and the last of them, which may be incomplete. Only
slots of a pure run are counted.

**Charge efficiency**: The charge counter's energy divided by the grid import
counter's energy less standby energy, measured in pure runs of charge slots.
_Avoid_: Grid-to-battery efficiency

**State of charge**: The battery's charge level in percent, as the BMS reports
it. _Avoid_: SoC level

**Battery capacity**: The energy the BMS's 100 % state of charge stands for, as
a configured entity's value.

**Stored energy**: State of charge times battery capacity. Only differences of
it are used.

**Valley**: The stretch of slots in which the state of charge stays within 10
points of its lowest point. A valley is real once the state of charge has risen
to the valley hysteresis above that lowest point; a smaller dip and recovery
belongs to the valley and widens it.

**Valley hysteresis**: The rise above its lowest point that makes a valley real,
and the fall below its peak that makes the next one. Configurable, 30 points by
default.

**Valley span**: The slots from the end of one valley to the beginning of the
next. _Avoid_: Cycle (a cycle is a job run, ADR-014)

**Battery efficiency**: The discharge counter's energy divided by the charge
counter's energy less the change in stored energy, measured between two valleys.
Standby power plays no part in it.

**Discharge efficiency**: The grid export counter's energy divided by the
discharge counter's energy less standby energy, measured in pure runs of
discharge slots. _Avoid_: Battery-to-grid efficiency

**Roundtrip efficiency**: The product of charge, battery and discharge
efficiency. _Avoid_: BMS efficiency

### Series Effy writes

**Effective series**: The effective values of an input, one Effy sensor per
input (`effy_*`).

**Derived power series**: The power equivalent of an energy-family sensor before
the loss is distributed (`effy_*_power`).

**Smoothed series**: A power-family input with short gaps bridged by
interpolation (`effy_*_smoothed`). _Avoid_: Smoothing (for redistribution)

**Redistribution**: Spreading one jump's energy over the time it took to
accumulate, so a low-resolution counter yields a smooth power instead of spikes.

**Running total**: The value a `total` sensor's statistic accumulates over all
its cycles; Home Assistant's `sum` column. _Avoid_: sum (for this), cumulative
sum

**Change**: The difference of a running total between two points in time (Home
Assistant's statistic type `change`): the amount booked in a period. The change
of an hour is the change of its slots added up.

**PV combined series**: The effective values of all PV inputs added up per slot,
in W. Effy writes it like any effective series (the sensor "Effective PV Sum
now", published as `pv_sum`). The EPS sensor and the BMS inputs are not in it.
_Avoid_: PV total

**PV share**: For a slot, 100 times the effective PV values plus the effective
EPS input, divided by all effective inputs; the part of the outputs that PV and
the EPS input fed. 0 when no input is active. An hour's share is
energy-weighted. The sensor "PV share now", in %.

**PV delta**: For one PV input and one run, the energy by which the run changed
the stored rows of its effective series: the added-up `(new − stored) × 5/60 h`
over the rows the run wrote, the new value alone where there was no row. In Wh
or kWh by the input's effective unit; positive or negative; booked at the time
of the write, not at the time of the slot. _Avoid_: undistributed series,
correction series

**PV delta integral**: A `total` sensor that accumulates PV deltas; Home
Assistant compiles its statistics. There is one per PV input and one combined
for all PV inputs, both with a `last_reset` at local midnight that Effy resets
at wall-clock midnight (_today_), and one combined without `last_reset`
(_total_). The delta of any period is its change. _Avoid_: PV delta sum

**Delta derivation**: The one-time computation of the history of the PV delta
integrals from the stored effective rows, after a full history recalculation
(ADR-027). It writes running-total rows, the only ones Effy writes. _Avoid_:
clone, migration, derived power series

**Delta base**: The last derived row of a PV delta integral: its `state`,
`last_reset` and running total, from which the live bookings continue.

**Formula signature**: The set of configured sensors that influence the
effective values (PV inputs, grid import, discharge, charge, grid export, EPS),
kept in the entry's data; a difference at setup triggers a full history
recalculation (ADR-027).

**Statistic**: A 5-minute value Effy writes into the recorder for an Effy
sensor. _Avoid_: History value

**Aggregation**: The way a statistic summarises the readings of its slot, as the
recorder names it: `mean`, `min`, `max`, `sum` or `state`. Which aggregations a
sensor's short-term statistics offer differs per sensor. _Avoid_: Statistic type

**Live state**: An Effy sensor's current Home Assistant state, which is
best-effort and not a statistic. _Avoid_: Value (unqualified)

### Counter movement

**Jump**: A step between two consecutive valid readings of an energy-family
sensor, with a positive difference. _Avoid_: Tick, transition, step, event

**Jump interval**: The time between two consecutive jumps of one energy-family
sensor.

**Peak**: A single reading whose power equivalent is above 0: a jump of an
energy-family sensor, or a reading above 0 of a power-family sensor with no
reading above 0 directly before or after it. Two consecutive readings above 0
are no peak. _Avoid_: Spike

**Peak height**: A peak's first derivation: for an energy-family sensor the
energy of the jump (one resolution step at standby, a multiple at load), for a
power-family sensor the reading. Neighbouring peaks have the same height when
they differ by less than the resolution.

**Resolution**: The smallest step a sensor reports, learned from its readings
with no starting value: for an energy-family sensor the smallest positive peak
height seen (10 Wh for a 0.01 kWh counter), for a power-family sensor the
smallest step between two distinct non-zero readings seen. Neighbouring peaks
have the same height when they differ by less than it.

**Drain interval**: The span between two consecutive peaks of the same BMS input
that have the same height, during which no other sensor is active; its power
equivalent is one value of the standby power. A high standby is a shorter drain
interval, not a different kind. _Avoid_: Quiet interval, idle run

**Idle sensor**: An energy-family sensor that shows no jump over a stretch of
time. _Avoid_: Idle (for the battery system)

**Zero-fill**: An explicit 0 for a slot in an idle sensor's stretch, instead of
leaving the slot without a statistic.

**Distribution window**: The span over which one jump's energy is redistributed.
_Avoid_: Window (unqualified)

### Offline

**Offline**: The state of a source sensor that reports `unavailable`, `unknown`
or a non-numeric state. _Avoid_: Outage

**Offline gap**: The stretch between a source sensor's last valid reading before
going offline and its first valid reading after.

**Recovery**: The first valid reading of a source sensor after an offline gap.

**Anchor**: The last valid reading before a span of history being processed,
which gives the first jump inside it its start; after an offline gap, the
reading from before the gap. _Avoid_: Baseline

### Slots and recalculation

**Slot**: A five-minute interval of the recorder's statistics grid, identified
by its start time. _Avoid_: Period, bucket

**Current slot**: The slot the present moment falls in. It has not finished yet.
_Avoid_: Open slot

**Last completed slot**: The most recently finished slot. It is never the
current slot. _Avoid_: Closed slot, last complete slot

**Slot timer**: The trigger that starts a recent recalculation after each slot
ends: the recorder's own 5-minute statistics event starts a slot job, which
waits until the last completed slot is readable and then runs the recent
recalculation. A wall-clock timer remains only as a watchdog. _Avoid_: Timer
path

**Open slot**: The slot that contains the current time. Effy writes no statistic
for it, because the recorder inserts that row itself when the slot ends; the
live state carries the last completed slot's value meanwhile. _Avoid_:
Provisional slot, current slot

**Recent recalculation**: The recalculation the slot timer starts, which
rewrites only the slots near now. _Avoid_: Slot recalc

**History recalculation**: The recalculation over the configured number of
history days, started by the button or the service; the only writer of hourly
(long-term) statistics. _Avoid_: Full recalc

**Storage**: The interface to everything Effy reads from the recorder (raw
states, 5-minute statistics, units, Effy's own stored rows). The recorder-backed
storage and the cache both implement it, so the history code cannot tell which
one it is reading from. _Avoid_: Reader, source

**Cache**: A storage that remembers what it has read from the storage underneath
it and asks that one only for what it is missing. It forgets what Effy writes,
never reuses Effy's own stored rows, and is emptied by the history button. Not
the last-valid-reading cache of ADR-015. _Avoid_: Buffer

**Clean cache**: A cache that was emptied before a recalculation, so that what
it holds afterwards is exactly what that recalculation read. _Avoid_: Fresh
cache

**Debug export**: A file written after the button's history recalculation, when
the option is on, holding Effy's configuration, the instant the run took for
"now", everything it read (the clean cache's snapshot) and the rows it wrote.
Enough to run the same recalculation again offline. _Avoid_: Dump, log

**Replay**: Running the real history recalculation again from a debug export
alone, over a cache rebuilt from the file and a storage that fails on any read
the file does not contain, and comparing the rows written with those in the
file. _Avoid_: Re-run

**First processed slot**: The first slot a standby, efficiency or battery
machine took since its state began (a first start or a reset such as the history
button's rebuild); kept across restarts and shown as `first_processed_slot`.
With `last_processed_slot` it tells what the estimate can contain. _Avoid_:
Start slot

**Recalculation phase**: One of the three parts of a recalculation: *fetch*
(reading raw states and statistics from the recorder), *compute* (the slot
values, on the event loop unless ADR-022 moves it), *write* (importing the
statistics). The time of each is logged. _Avoid_: Stage, step

**Parse probe**: One extra pass over all raw states during a history
recalculation, timed on its own, so the cost of parsing (state strings to
numbers, timestamps to epoch seconds) is visible apart from the compute phase. A
diagnostic, not part of the calculation. _Avoid_: Parsing phase

**Recalculation detail**: The lines after the first line of a history
recalculation's timing record: the environment, CPU against wall seconds, the
longest stretch without an `await`, every recorder job with its run and its
wait, the compute stages and the three statistics writes. It splits the phases
into sub-processes; it adds nothing to them. _Avoid_: Profile

**Unchanged row**: A stored statistic that already is exactly what Effy would
write for that slot or hour: the same `mean` and `state`, and no `min`, `max`,
`sum` or `last_reset`. A recalculation does not write an unchanged row, and
`sensor.effy_recalculated_from` is the earliest slot of a row that was written.
_Avoid_: Identical row, skipped row

**Closing step**: The last action of a recalculation, the slot job or the
history recalculation: the update of the recalculated-from sensor. It tells a
listener that the recalculation's statistics and states are final.

**History days**: The configured number of days, ending now, that the history
recalculation covers. _Avoid_: History window (Chargy's own term for the days
its measurements span)

**Trigger window**: The span since the previous recent recalculation in which an
arriving jump or recovery makes the rewrite range reach further back. _Avoid_:
Window (unqualified)

**Rewrite range**: The slots a recent recalculation actually rewrites.
