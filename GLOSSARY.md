# Effy

A Home Assistant integration that takes the energy a PV and battery system loses
(the part its outputs do not account for), distributes that loss evenly across
the inputs that were active, and exposes the corrected values as sensors and
5-minute statistics.

## Language

### System and entities

**Input**: A source sensor for energy or power entering the system: a PV string,
the battery's discharge counter, the grid-import counter. _Avoid_: Producer

**PV input**: An input that is a PV string. It can never be a drain source: PV
active alone is loss and drain mixed.

**BMS input**: An input that is a BMS reading: the battery's discharge or the
grid import. Only a BMS input can be a drain source.

**Output**: A source sensor for energy or power leaving the system: the
battery's charge counter, the grid-export counter. Every output is a BMS
reading. _Avoid_: Sink, consumer

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

**Recalculation phase**: One of the three parts of a recalculation: *fetch*
(reading raw states and statistics from the recorder), *compute* (the slot
values, on the event loop unless ADR-022 moves it), *write* (importing the
statistics). The time of each is logged. _Avoid_: Stage, step

**Parse probe**: One extra pass over all raw states during a history
recalculation, timed on its own, so the cost of parsing (state strings to
numbers, timestamps to epoch seconds) is visible apart from the compute phase. A
diagnostic, not part of the calculation. _Avoid_: Parsing phase

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
