# ADR-026 – The EPS/UPS Port as a Signed Power Sensor

**Date:** 2026-11-06 **Status:** Draft — decisions were taken by the human in
chat on 2026-11-06 (one signed power sensor; an EPS flow disqualifies a pure
slot; the EPS input takes a loss share like any input; the EPS series is
smoothed, option A, at Gate 1). ◇ marks my formalization of a decision, to be
confirmed at Gate 2. Terms follow `GLOSSARY.md`.

---

## Context

A hybrid inverter has an EPS (emergency power supply) or UPS port that feeds
backup loads. Energy leaves the system through it. Another inverter can also
feed that port; then energy enters the system through it. The human's inverter
reports the port as **one signed power sensor**: positive when energy leaves
through the port, negative when it enters.

**What Effy does today.**

- It knows no EPS port. Energy that leaves through it is in no output, so Effy
  reads it as loss (ADR-001) and distributes it over the inputs. Energy that
  enters through it is in no input, so the loss is understated, and where it
  exceeds the loss the cap at zero fires (ADR-005).
- A power-family reading goes into the waterfall as it is
  (`history._readings_for_slot`). A negative reading in the output list would be
  subtracted from the outputs' sum: it would raise the loss, and it would never
  take a loss share. Adding the sensor to either list unchanged is therefore
  wrong; it has to be split first.
- Outputs are always BMS readings (glossary, ADR-019). An EPS sensor is neither
  a BMS reading nor a PV string.

**Assumptions:**

- **A1** The sensor is a power-family sensor of state class `measurement`; its
  5-minute statistic `mean` is the slot's net power (what the recorder holds;
  Effy reads no raw states of a power-family sensor).
- **A2** Energy that enters through the port is conserved like any input: it
  leaves through an output or becomes loss.

## Options considered

- **Two counters (EPS output, EPS input) or one signed sensor.** The human's
  inverter has one signed power sensor. Chosen: one signed power sensor (human).
  Two counters are a possible later addition and are out of scope.
- **EPS flow in a pure slot: subtract it from the BMS side, or disqualify the
  slot.** Subtracting leaves the conversion loss of the EPS path in the
  denominator (the EPS energy is measured on the AC side), so the efficiency
  would carry a bias. Chosen: disqualify the slot (human): no bias, fewer
  counted slots.
- **The EPS input in the waterfall: equal loss share, or pass through.** Chosen:
  an equal share like any input (human); ADR-001 stays as it is.
- **Smoothing the EPS series or not.** Chosen: smooth it (human, option A): the
  sensor is a power-family input half the time, and a gap in it would shift loss
  shares onto the other inputs, as for any power-family input (ADR-013 decision
  2).

## Decision

### 1. Configuration: one optional picker ◇

One optional single sensor picker, key ◇ `eps_sensor` (`CONF_EPS_SENSOR`), the
**EPS sensor**. It is neither a PV input nor one of the four BMS counters, and a
sensor cannot have two roles (ADR-023 decision 1).

- **Picker:** domain `sensor`, device class `power`. An energy counter cannot be
  signed, so it is not offered.
- **Validation ◇:** at submit time the sensor must have state class
  `measurement` (as the SoC sensor does) and, if it reports a unit, W or kW; the
  device class is re-checked like the outputs' (ADR-002/008).
- **The EPS sensor counts for neither minimum ◇:** "at least one input in total"
  and "at least one output (charge or grid export)" are satisfied by the other
  roles only. An entry with only an EPS sensor refuses to set up as today (a
  recalculation without a BMS output would write the whole input as loss).
- An entry without an EPS sensor changes nothing.

### 2. The signed split, and the formula

Per slot, the EPS sensor's value `v` (the `mean`, in W or kW) gives two
readings:

```
eps_out = max(v, 0)     → joins the outputs
eps_in  = max(−v, 0)    → joins the inputs, and the waterfall pool
```

At most one is not zero. The formula of the README and ADR-005 becomes

```
total_loss = max(0, Σ inputs + eps_in − Σ outputs − eps_out)
```

and the waterfall (ADR-001) is unchanged: the EPS input is an active input with
an equal loss share like every other (human). The invariant Σ effective inputs =
Σ outputs holds with both halves included, with the exception ADR-005 already
names (a clamped slot). `distribute_loss` keeps its signature; the split is a
pure function in `calculation.py` ◇ that the slot loop applies to the EPS
sensor's reading. The EPS sensor is a member of the input list (`all_input_ids`,
after the PV and BMS inputs ◇) and **not** of the output list: it is fetched,
smoothed and shown once, and only the slot loop turns its signed value into the
two readings, so the output half never needs a list of its own.

### 3. Smoothing: the signed series, before the split (human, option A)

The EPS sensor is smoothed like a power-family input (ADR-013 decision 2): gaps
of up to `INTERPOLATION_MAX_GAP_SLOTS` (2) slots are interpolated linearly **on
the signed series**, and the interpolated values are merged into the per-slot
series before the split, so both halves see the same filled value. (A gap that
spans a change of sign passes through zero.) A longer gap stays a gap, no
extrapolation. This is the one exception to "outputs are left untouched"
(ADR-012): the output half is smoothed because it is the same reading as the
input half. The interpolated series is published as its own Effy sensor (5). It
is also what the standby and efficiency machines read as the EPS sensor's
activity (4).

### 4. What the slot mean cannot show

The `mean` is net. If the flow reverses inside one slot only the net appears,
and the conversion loss of the larger gross flows stays inside the loss. This is
accepted: it is what the statistics hold, and a raw-state path for a
power-family sensor is out of scope.

### 5. Standby and efficiency are unchanged, and the EPS flow disturbs them

"Active" is the glossary's: a value that is not zero, here in either direction.
The activity reading of ADR-018 §3 and ADR-023 decision 3 takes a power-family
input from its smoothed series and tests `mean != 0`, so the signed smoothed
series of the EPS sensor shows activity in both directions with no change to
that reading ◇ (a test pins it, for a negative value in particular). The EPS
sensor is not one of the four counters, so, like a PV input, an active EPS
sensor makes a slot impure (ADR-023 decision 4, `classify_slot`), is exempt from
the 5 % significance rule (ADR-023 Amendment 2026-11-04) and breaks a drain
interval (ADR-018 decision 3). The efficiency formulas and the standby machine
are not touched (human). The EPS sensor is never a drain source: it is not a BMS
input (ADR-018 decision 2).

Consequence, stated for the user: with an EPS load that never pauses, the
standby and efficiency sensors stop updating, because no slot is quiet and none
is pure. There is no tolerance for small idle readings, as for PV inputs
(ADR-023 decision 3).

### 6. What Effy publishes ◇

- Two Effy sensors per EPS sensor, both built by the code that already builds
  them for a power-family input:
  - `sensor.effy_<slug>`, the **effective value of the input half** (≥ 0 in the
    source's unit; 0 while the port is exporting), like the effective series of
    any input;
  - `sensor.effy_<slug>_smoothed`, the **signed** gap-interpolated series
    (positive: energy leaves through the port, negative: it enters). There is no
    derived-power sensor (power-family) and no series for the output half (no
    output has one).
- They exist whenever the EPS sensor is configured, whether or not the port has
  ever imported ◇, so that a reader does not have to wait for a first import to
  find them.
- `sensors` entries, `source_entity_id` the EPS sensor, `role` `eps` ◇ and
  `function` `eps` ◇ in both:
  - `series` `effective`, `direction` `input`;
  - `series` `smoothed`, `direction` `both` ◇ (a signed series is neither an
    input nor an output; the sign convention is documented in
    `effy_exports.py`). The values `eps` (role, function) and `both` (direction)
    are new and additive (ADR-021 decision 1: an additive key or value does not
    raise the schema version). `EFFY_PUBLISHED_SCHEMA_VERSION` stays at 1; a
    reader ignores a value it does not know. `effy_exports.py` gets
    `EFFY_ROLE_EPS`, `EFFY_FUNCTION_EPS` and `EFFY_DIRECTION_BOTH`, and the
    literals `EffyRole`, `EffyFunction` and `EffyDirection` the new values.

### 7. Structure (ADR-000 §3/§6)

The split is pure and tested with no mocking (`calculation.py`). `inputs.py`
gets `eps_id(options)`; `config_flow.py` the picker and its validation;
`history.py` the use of the split in the slot loop (one function serves the full
recalculation and the slot job); `catalog.py` the two published entries
(`sensor.py` builds the sensors as for any power-family input, to be verified,
not assumed); `effy_exports.py` the three symbols. README (configuration table,
the formula of "How the algorithm works"), the translations (en, de) and the
glossary change with it.

### 8. Tests

- The split: positive, negative, zero, unit kW, exactly one half is not zero.
- Smoothing: a gap of one or two slots is filled on the signed series (also
  across a change of sign), a longer gap is not; both halves see the filled
  value; the smoothed series is signed.
- Formula: an EPS output lowers the loss by its power; an EPS input raises the
  total and takes an equal share (a small one is capped at its value, as in the
  waterfall); Σ effective inputs = Σ outputs.
- Regression: an entry without an EPS sensor gives results identical to before,
  full recalculation and slot timer (the TASK-0004 harness).
- Equivalence: with an EPS sensor, the slot job after each cycle equals a full
  recalculation at the same time (the CAP-1 invariant).
- Standby and efficiency: an active EPS sensor makes a slot impure and breaks a
  drain interval, in either direction of the sign (the activity reading takes
  the signed smoothed series).
- Config flow: the picker, no role twice, the two minimums not satisfied by it,
  state class and unit.
- Contract: the two published entries, and the pinned values of
  `effy_exports.py`.

### 9. Out of scope

Two separate EPS counters; an EPS energy counter; the gross in-slot flows (4);
an EPS efficiency or an EPS standby sensor; more than one EPS port per entry; a
tolerance for small idle readings (5); any change in Shady or Chargy.

## Consequences

- The loss shown by Effy shrinks by what the backup loads draw, and the foreign
  inverter's energy that enters through the port stops being invisible. For an
  existing installation that adds the sensor the loss series, the effective
  values and the history change from that day on; a history recalculation
  rewrites them (ADR-004).
- With a continuous EPS load the standby and efficiency sensors may stay
  unavailable or stop updating (5); this is the price of "no bias".
- A signed power sensor needs no new fetch path: it is read like any
  power-family sensor.
- The README's formula changes, ADR-001 and ADR-005 stay as written.

## Open decisions

None. Smoothing was decided by the human at Gate 1 (option A, 2026-11-06). The
points marked ◇ are mine and are confirmed or changed at Gate 2.
