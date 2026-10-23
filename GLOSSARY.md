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
battery's charge counter, the grid-export counter. _Avoid_: Sink, consumer

**Source sensor**: The user's original entity that an input or output is read
from. _Avoid_: Source (unqualified), raw sensor

**Effy sensor**: Any `sensor.effy_*` entity Effy creates. _Avoid_: Derived
sensor

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

**Slot timer**: The wall-clock trigger that starts a recent recalculation
shortly after each slot ends. _Avoid_: Timer path

**Recent recalculation**: The recalculation the slot timer starts, which
rewrites only the slots near now. _Avoid_: Slot recalc

**History recalculation**: The recalculation over the configured number of
history days, started by the button or the service; the only writer of hourly
(long-term) statistics. _Avoid_: Full recalc

**Trigger window**: The span since the previous recent recalculation in which an
arriving jump or recovery makes the rewrite range reach further back. _Avoid_:
Window (unqualified)

**Rewrite range**: The slots a recent recalculation actually rewrites.
