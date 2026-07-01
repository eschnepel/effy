# ADR-006 – Live Sensor Update Strategy

**Date:** 2026-06-30
**Status:** Accepted


---

## Context

The `effy_*` output sensors must reflect the current loss distribution as
input and output sensors change. Four strategies were evaluated.

---

## Options considered

### Option A – Immediate event-driven update

Each `EffySensor` registers `async_track_state_change_event` on all watched
sensors. Any state change triggers `async_schedule_update_ha_state(True)`
immediately.

| | |
|---|---|
| **Pro** | Output updates within one event loop cycle of any source change. |
| **Pro** | HA-idiomatic; no custom scheduling logic. |
| **Con** | N output sensors × (M inputs + K outputs) listeners → O(N·(M+K)) listeners. For 4 inputs, 2 outputs, 4 effy sensors: 24 listeners. Acceptable for residential scale. |
| **Con** | If multiple source sensors update in the same burst (e.g. a Modbus poll returning 5 registers at once), each event triggers a full recalculation immediately, before the others have landed. The intermediate results are overwritten quickly, but the CPU work is done N times instead of once. |
| **Con** | No deduplication: two source sensors changing 1 ms apart fire two full recalculations. |

---

### Option B – Debounced update with scheduling delay

On each state-change event, schedule a recalculation with a short delay
(e.g. 0.5 s) using `async_call_later`. If a recalculation is already
pending, skip scheduling a new one (flag or cancel-and-reschedule).

```python
if not self._refresh_pending:
    self._refresh_pending = True
    self._unsub_refresh = async_call_later(hass, 0.5, self._do_refresh)
```

| | |
|---|---|
| **Pro** | Allows HA to drain a burst of pending state-change events before recalculating – one recalculation per burst instead of one per event. |
| **Pro** | Reduces CPU load in multi-sensor update scenarios. |
| **Con** | Introduces up to 0.5 s of display lag on dashboards. |
| **Con** | Additional state to manage (`_refresh_pending`, `_unsub_refresh`); must be cleaned up on `async_will_remove_from_hass`. |
| **Con** | The 0.5 s value is a heuristic – too short and bursts still slip through; too long and the display feels sluggish. |
| **Neutral** | Still O(N·(M+K)) listeners; the benefit is only in recalculation count, not listener count. |

---

### Option C – Shared coordinator with debouncing *(current implementation)*-

A single `EffyCoordinator` (one per config entry) registers all listeners
and holds the current cached state of every watched sensor. Each state-change
event updates the cache. Recalculation is scheduled with debouncing (Option B
logic) at the coordinator level. All `EffySensor` instances subscribe to the
coordinator result.

```
state_change → coordinator updates cache → debounce timer → one recalculation
                                                           → push to all N sensors
```

| | |
|---|---|
| **Pro** | (M+K) listeners total instead of N·(M+K) – reduces listener count from O(N·(M+K)) to O(M+K). |
| **Pro** | Exactly one recalculation per burst regardless of N. |
| **Pro** | The coordinator cache is the single source of truth; sensors only read from it. |
| **Con** | Significantly more code: coordinator class, subscription mechanism, push notification to child sensors. |
| **Con** | Over-engineering for the typical residential case (< 10 sensors, < 30 listeners). |

---

### Option D – Cache + update-on-change only

Each `EffySensor` caches the last effective value it computed. On each
state-change event it re-reads all states, recomputes, and only calls
`async_write_ha_state` if the result has changed (within a tolerance).

| | |
|---|---|
| **Pro** | Avoids spurious HA state writes when the effective value is unchanged. |
| **Con** | Still fires a full recalculation on every event (same CPU cost as Option A). |
| **Con** | Adds complexity without reducing the listener count or recalculation count. |
| **Verdict** | Dominated by Option C; not worth implementing on its own. |

---

## Decision

**Option C is implemented.** A single `EffyCoordinator` per config entry
owns O(M+K) listeners (inputs + outputs), caches the latest reading per
entity, debounces with a 0.3 s timer (``DEBOUNCE_SECONDS``), runs one
``distribute_loss`` call per burst, and pushes results to all N subscriber
sensors via registered callbacks.

Option B was considered as a stepping stone but skipped in favour of the
clean coordinator design. Option D is not worth implementing independently.

---

## Consequences

- **Pro:** Current implementation is minimal and correct.
- **Pro:** Clear upgrade path to Option C documented here.
- **Con:** Burst recalculations are wasteful in multi-sensor poll scenarios,
  but invisible to users at residential scale.
- **Neutral:** On HA restart, all states are replayed, triggering an initial
  recalculation per sensor – correct behaviour for first-load initialisation.

---

## Amendment – 2026-07-01: LiveReading accumulator replaces the raw-value cache

The original ADR described the coordinator cache as `dict[str, SensorReading | None]`
holding the latest raw state value per entity.  This has been replaced by a
`dict[str, LiveReading]` accumulator cache.  See ADR-007 for the full
algorithm; the coordinator-level consequences are summarised here.

### Why the raw-value cache was insufficient

1. **Energy sensors (TOTAL_INCREASING / TOTAL-as-energy):** passing the
   absolute counter total to `distribute_loss` produced numerically meaningless
   results (effective value ≈ lifetime total).
2. **No Wh→W conversion:** even after reducing to a slot delta, Wh and W
   values were fed to `distribute_loss` without normalisation, producing
   systematically wrong loss shares (ADR-008).
3. **No time-weighted averaging for power sensors:** the last raw value
   before a recalculation was used, ignoring all intermediate events.

### `LiveReading` cache structure

Each watched entity has one `LiveReading` instance, mutated in-place on
every event.  Two families exist (see ADR-007 and ADR-008):

- **ENERGY** (`TOTAL_INCREASING`, `TOTAL` with Wh/kWh): accumulates
  `raw_start` and `raw_last`; `to_sensor_reading` computes
  `delta_Wh / elapsed_h → W`.
- **POWER** (`MEASUREMENT`, `TOTAL` with W/kW): accumulates a
  time-weighted average; `to_sensor_reading` returns `avg` as-is.

### `_on_state_change` dispatching

```python
if live.family == _FAMILY_POWER:
    live.update_power(value, event_ts)
else:
    live.update_energy(value, event_ts)
```

No separate `_slot_anchor` dict or `_delta_reading` method exists.

### `_do_refresh` cache reset

After every recalculation `_reset_all_cache(now)` rolls all `LiveReading`
accumulators forward:
- ENERGY: `raw_start = raw_last`, `reset_ts = updated_ts`
- POWER: `avg = 0`, `reset_ts = updated_ts`

The next window begins exactly at the end of the previous one — no gap,
no overlap.

### `force_refresh` semantics

`force_refresh` calls `_do_refresh` directly without re-reading any sensor
states.  The accumulated `LiveReading` values are used as-is, then reset.
Re-reading absolute counter states here would corrupt the energy delta
accumulated in the current window.

### `async_setup` seeding

`async_setup` creates one `LiveReading` per watched entity from the current
HA state attributes (unit, state_class) but does *not* seed any values.
The first real `_on_state_change` event seeds the accumulator.  Until then
`to_sensor_reading` returns `None` and the entity is excluded from the
distribution — the correct conservative behaviour at startup.
