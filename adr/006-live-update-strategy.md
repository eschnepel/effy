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
