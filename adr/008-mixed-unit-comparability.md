# ADR-008 – Comparability of W and Wh in Mixed-Unit Configurations

**Date:** 2026-07-01
**Status:** Accepted

---

## Context

ADR-002 established that W and Wh are **numerically treated as identical**
within a 5-minute interval, because the conversion factor Δt = 5/60 h
cancels in the loss *ratio*:

```
ratio = loss / Σ inputs
      = (Δt × loss_W) / (Δt × Σ inputs_W)
      = loss_W / Σ inputs_W
```

This derivation holds **only when all terms share the same unit family**
(all W/kW, or all Wh/kWh).  If a configuration mixes unit families —
e.g. one PV string reports as a MEASUREMENT sensor in W and another as a
TOTAL_INCREASING sensor in Wh — the cancellation breaks:

| Sensor | State class | Unit | Value at 1 000 W for 5 min |
|---|---|---|---|
| PV-String A | MEASUREMENT | W | 1 000 |
| PV-String B | TOTAL_INCREASING | Wh | 83.3 (= 1 000 W × 5/60) |

`distribute_loss` would receive `Σ inputs = 1 000 + 83.3 = 1 083.3` instead
of `1 000 + 1 000`.  Sensor B would be assigned ~12× less loss than Sensor A
despite contributing the same physical energy.

---

## Scope: History path vs. Live path

### History path (`history.py`)

`_readings_for_slot` reads:
- `change` for TOTAL_INCREASING and TOTAL-as-energy (unit: Wh/kWh, a
  5-minute energy delta)
- `mean` for MEASUREMENT / TOTAL-as-power (unit: W/kW, average power)

Both the unit mismatch and its fix apply equally to the history path.

### Live path (`coordinator.py`)

`LiveReading.to_sensor_reading()` converts energy-family sensors by dividing
the accumulated delta by the actual elapsed time within the window:

```python
delta / elapsed_h    # Wh / h = W,  kWh / h = kW
```

This uses the real elapsed time rather than a fixed 5-minute constant,
which is more accurate when the coordinator fires slightly early or late.
The resulting `original_unit` is set to `W` or `kW` so that
`effective_in_original_unit` converts back to the same power unit.

---

## Options considered

### Option A – Reject mixed-unit configurations

Validate at config-flow time and raise a user-facing error if any input
sensor has a different unit family from the others.

| | |
|---|---|
| **Pro** | Mismatch caught before it produces wrong results. |
| **Con** | Blocks users with heterogeneous sensor setups (e.g. a SolarEdge total-energy counter alongside a Fronius real-time power sensor). |
| **Con** | Forces an artificial constraint on sensor selection. |

### Option B – Warn only

Log a warning when mixed unit families are detected; proceed with the
mixed values as-is.

| | |
|---|---|
| **Pro** | No code change to calculation path; warning is better than silence. |
| **Con** | Results remain wrong — factor ~12 error on loss shares. A warning that does not fix the problem is not sufficient. |

### Option C – Convert Wh/kWh to W-equivalent at the reader layer *(implemented)*

Before constructing a `SensorReading`, convert energy-delta values to their
W-equivalent average power.  The conversion happens at the **reader layer**
(the point where raw statistics or live counter deltas are turned into
`SensorReading` objects), not inside `distribute_loss`.  This preserves the
architectural boundary from ADR-000 §3 (`calculation.py` remains pure and
time-agnostic).

**History path** (`_readings_for_slot` via `to_power_equivalent`):

```python
# sensor_utils.py
def to_power_equivalent(value, unit, slot_minutes=SLOT_MINUTES):
    if unit == "Wh":
        return value * (60.0 / slot_minutes), "W"
    if unit == "kWh":
        return value * (60.0 / slot_minutes), "kW"
    return value, unit
```

For a 5-minute slot: `83.3 Wh × 12 = 1 000 W` — now numerically equal to
the W-family sensor reporting the same physical power.

**Live path** (`LiveReading.to_sensor_reading`):

```python
elapsed_h = (self.updated_ts - self.reset_ts).total_seconds() / 3600.0
delta = max(0.0, self.raw_last - self.raw_start)
power_unit = "W" if self.unit == "Wh" else "kW"
return SensorReading(raw_value=delta / elapsed_h, original_unit=power_unit)
```

Uses the actual elapsed window rather than the fixed 5-minute constant,
which is more accurate when the debounce timer fires slightly off-schedule.

**`original_unit` rewrite**: in both paths the `original_unit` field is
changed from Wh → W (or kWh → kW).  `effective_in_original_unit` therefore
returns a W/kW value — the natural output unit for an average-power sensor.

| | |
|---|---|
| **Pro** | Physically correct for all unit combinations. |
| **Pro** | `calculation.py` unchanged — pure, time-agnostic (ADR-000 §3). |
| **Pro** | Both paths use the same concept (Wh→W); the live path additionally uses real elapsed time for higher accuracy. |
| **Con** | `original_unit` is silently changed at the reader boundary; callers that inspect `SensorReading.original_unit` after conversion will see W/kW even if the underlying sensor is Wh/kWh. This is intentional and documented here. |

---

## Decision

**Option C is implemented.**

`to_power_equivalent` lives in `sensor_utils.py` (shared between history and
coordinator) and is imported by `history._readings_for_slot`.  The live path
performs an equivalent conversion directly inside
`LiveReading.to_sensor_reading` using the actual elapsed window duration.

`_warn_mixed_units` and `_unit_family` that were introduced as a temporary
interim measure have been removed; the conversion makes them unnecessary.

---

## Consequences

- **Pro:** Mixed-unit configurations now produce correct loss ratios.  A
  W-based and a Wh-based sensor with identical physical power contribution
  receive identical loss shares.
- **Pro:** `calculation.py` and `distribute_loss` remain time-agnostic.
- **Pro:** The `to_power_equivalent` helper is unit-tested independently and
  reused across both read paths.
- **Neutral:** This ADR supersedes the implicit assumption in ADR-002 that
  all sensors share one unit family.  The W/Wh identity claim in ADR-002
  now applies to the *output* of the reader layer (always W/kW after
  conversion), not to raw sensor values.
- **Note:** `SensorReading.original_unit` after conversion reflects the
  *effective* unit passed to `distribute_loss` (W or kW), not the sensor's
  native HA unit.  Code that needs the native HA unit must read it from
  `hass.states.get(entity_id).attributes["unit_of_measurement"]` directly.
