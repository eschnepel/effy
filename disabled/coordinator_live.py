"""
=====================================================================
 DISABLED — 2026-07-09. Live-path support is temporarily switched off
 until the history path (history.py) works correctly on its own. This
 is a verbatim, self-contained snapshot of the last working live-path
 implementation — not imported or executed anywhere while disabled.
 See disabled/README.md. Further instructions on the replacement
 design (history-driven "current slot" computation via the slot timer
 still in coordinator.py) to follow — do not delete this file until
 that migration is confirmed complete.
=====================================================================

EffyCoordinator – shared coordinator for live loss distribution (ADR-006 Option C, ADR-010).

Architecture
------------
One coordinator is created per config entry and stored in ``hass.data``.
It owns:
  - O(M+K) state-change listeners (inputs + outputs).
  - A LiveReading cache: one entry per watched entity, updated on every
    state-change event, reset after each recalculation.
  - A debounce timer (DEBOUNCE_SECONDS) that fires one recalculation after
    a burst of state-change events has settled.
  - A second, independent slot-aligned timer that fires SLOT_TIMER_LEAD_SECONDS
    before every SLOT_MINUTES wall-clock boundary, regardless of whether any
    state-change event occurred. It does not replace or cancel the debounce
    timer — both coexist and either may trigger a recalculation. Its purpose
    is to give ENERGY-family entities that update less often than the debounce
    cadence a real, honestly-computed reading close to every slot boundary,
    instead of only being recalculated whenever some other entity happens to
    report (see LiveReading.reset).
  - A push registry: child EffySensor instances subscribe and receive their
    computed effective value after each recalculation.

Flow
----
state_change event  (always on the HA event loop)
  → _on_state_change
      → update LiveReading in _cache (time-weighted average or raw delta)
      → if no refresh pending: schedule _do_refresh(DEBOUNCE_SECONDS)
      → further events only update cache, no new timer
  → debounce timer fires → _do_refresh → _recalculate_and_reset
slot-aligned timer fires (independent of the above)
  → _on_slot_timer → _recalculate_and_reset → reschedules itself
_recalculate_and_reset:
      → convert each LiveReading to a W-equivalent SensorReading
      → distribute_loss
      → push result to subscribers
      → reset every LiveReading. For an entity that reported a real event
        since its last reset, the anchor (last raw value / last average)
        and timestamps are carried/rolled forward together, since a reset
        happens on every cycle regardless of which entity actually
        triggered it. For an ENERGY entity that did NOT report since its
        last reset, only updated_ts advances to the real current time —
        reset_ts stays put — so the next reading reflects a real,
        honestly-idle delta-over-elapsed-time rather than a stale carried
        rate (see LiveReading.reset).

Thread safety
-------------
HA dispatches all state-change events and async_call_later callbacks on the
same event loop thread.  No concurrent access is possible, so no locks are
needed.

LiveReading cache structure (per entity)
-----------------------------------------
All state classes share:
  reset_ts   – wall-clock time of the last real anchor point (start of the
               current accumulation window; only moves when this entity
               itself reported, or advances to `now` for the very first
               ever entity).
  updated_ts – wall-clock time used as the end of the current accumulation
               window: the most recent state-change event, OR — for an
               ENERGY entity idle since its last reset — the real time of
               the most recent recalculation cycle.

MEASUREMENT / TOTAL  (unit: W or kW, already instantaneous power)
  avg_w      – time-weighted running average over [reset_ts, updated_ts].
               Computed incrementally on each event:
               new_avg = (old_avg * old_Δt + new_value * new_Δt) / total_Δt
               Unit is kept as W/kW; distribute_loss normalizes to W.

TOTAL_INCREASING / TOTAL  (unit: Wh or kWh, energy counter)
  raw_start  – absolute counter value at reset_ts.
  raw_last   – absolute counter value at updated_ts (unchanged while idle).
  delta      – raw_last - raw_start (≥ 0, clamped on counter reset).
  Conversion to W-equivalent at recalculation time:
    W_equiv = delta_Wh / ((updated_ts - reset_ts).total_seconds() / 3600)
  This mirrors the history path's ``change / slot_duration_h`` formula
  but uses the actual elapsed time instead of the fixed 5-minute window.
  While idle, delta stays 0 and updated_ts keeps advancing, so this
  honestly converges to 0 W rather than freezing at the last active rate.

Note on TOTAL vs TOTAL_INCREASING:
  Both use ``change`` in the history path and are treated identically here
  (energy delta → W conversion).  If a TOTAL sensor turns out to carry
  instantaneous W values in practice, only _state_class_family() needs to
  change for that sensor.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .calculation import LossDistribution, SensorReading, distribute_loss
from .const import CONF_INPUT_SENSORS, CONF_OUTPUT_SENSORS
from .sensor_utils import SLOT_MINUTES

_LOGGER = logging.getLogger(__name__)

# How long to wait after the last state-change event before recalculating.
# See ADR-006 (Option C).
DEBOUNCE_SECONDS = 0.3

# How long before every SLOT_MINUTES wall-clock boundary the independent
# slot-aligned timer fires (see EffyCoordinator._schedule_next_slot_timer,
# ADR-010). A few seconds of lead time keeps this comfortably clear of the
# boundary itself without materially shrinking the window it's meant to cover.
SLOT_TIMER_LEAD_SECONDS = 5

# Sentinel: used as reset_ts / updated_ts before the first event arrives.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Type alias for a subscriber callback.
SubscriberCallback = Callable[[LossDistribution], None]

# State-class families
_FAMILY_POWER = "power"  # W / kW  — instantaneous, MEASUREMENT or TOTAL-as-power
_FAMILY_ENERGY = "energy"  # Wh / kWh — counter delta, TOTAL_INCREASING or TOTAL-as-energy


@dataclass
class LiveReading:
    """Per-sensor accumulator held in the coordinator cache.

    One instance exists per watched entity for the lifetime of the coordinator;
    it is *mutated* on every state-change event and *reset* after each
    recalculation.  No new objects are allocated in the hot path.
    """

    entity_id: str
    unit: str
    family: str  # _FAMILY_POWER or _FAMILY_ENERGY

    # --- shared timestamps ---
    reset_ts: datetime = field(default_factory=lambda: _EPOCH)
    updated_ts: datetime = field(default_factory=lambda: _EPOCH)

    # --- POWER family (MEASUREMENT / TOTAL-as-power) ---
    # Time-weighted running average in the original unit (W or kW).
    avg: float = 0.0

    # --- ENERGY family (TOTAL_INCREASING / TOTAL-as-energy) ---
    # Absolute counter value at the last reset.
    raw_start: float = 0.0
    # Absolute counter value from the most recent event.
    raw_last: float = 0.0

    # True if update_power/update_energy has been called since the last
    # reset() call. Used by reset() to distinguish "this entity's window
    # closed because it actually reported" from "this entity was reset only
    # because some other entity's event triggered a recalculation cycle" —
    # see reset() for why that distinction matters.
    _touched_since_reset: bool = False

    def is_seeded(self) -> bool:
        """True once at least one real state value has been received."""
        return self.updated_ts is not _EPOCH and self.updated_ts != _EPOCH

    def to_sensor_reading(self) -> SensorReading | None:
        """Convert the accumulated state to a W-equivalent SensorReading.

        Returns None if no data has been received yet or the window is empty.
        The conversion uses only the internally accumulated timestamps
        (reset_ts, updated_ts) — no external ``now`` is needed or used.
        """
        if not self.is_seeded():
            return None

        if self.family == _FAMILY_POWER:
            # avg is already in W/kW; distribute_loss normalises to W.
            return SensorReading(
                entity_id=self.entity_id,
                raw_value=self.avg,
                original_unit=self.unit,
            )

        # ENERGY family: convert delta [Wh/kWh] → W-equivalent
        elapsed_h = (self.updated_ts - self.reset_ts).total_seconds() / 3600.0
        if elapsed_h <= 0:
            # reset_ts == updated_ts means only one event has been seen
            # (no elapsed time yet).  Use the raw delta as-is — the first
            # recalculation after setup will have a very short window; a
            # zero-duration window would produce ±inf, so we fall back to 0.
            return SensorReading(
                entity_id=self.entity_id,
                raw_value=0.0,
                original_unit="W"
                if self.unit in ("W", "kW")
                else ("W" if self.unit == "Wh" else "kW"),
            )

        delta = max(0.0, self.raw_last - self.raw_start)
        # Wh / h = W,  kWh / h = kW
        power_unit = "W" if self.unit == "Wh" else "kW"
        return SensorReading(
            entity_id=self.entity_id,
            raw_value=delta / elapsed_h,
            original_unit=power_unit,
        )

    def reset(self, now: datetime) -> None:
        """Roll the accumulation window forward after a recalculation (ADR-010).

        ``_do_refresh`` (and the slot-aligned timer, see EffyCoordinator)
        resets every watched entity on every cycle, including entities that
        received no event since the last reset. What "reset" should mean for
        such an untouched entity differs by family:

        POWER: *carry the last computed average forward* as the seed for the
        new window; move timestamps forward together (no gap). `avg` is a
        time-weighted average already folded in at read time in
        `update_power` — `to_sensor_reading` just returns it as-is, so
        advancing `reset_ts`/`updated_ts` together here doesn't change what
        gets reported; it only affects how much weight `update_power` gives
        the carried-forward `avg` once a real event finally arrives (see
        `update_power`).

        ENERGY, touched since the last reset: same "carry forward, roll
        together" treatment, moving raw_start to raw_last. This mirrors
        POWER and is safe because `update_energy` re-anchors cleanly on the
        first real event of a new window anyway.

        ENERGY, NOT touched since the last reset (genuinely idle): reset_ts
        must NOT move — it still marks the last real event. But updated_ts
        *does* advance to the real `now`. This makes the next
        `to_sensor_reading()` compute an honest
        ``0 (unchanged raw_start/raw_last) / real_elapsed_h`` = 0, instead of
        reporting a stale rate that was only true while the entity was still
        actually reporting. Collapsing updated_ts to the old value here (as
        for the "touched" case) would instead keep elapsed_h pinned at 0
        indefinitely — an energy sensor that updates less often than
        whatever triggers recalculation would then flatline at whatever its
        last real rate happened to be, for as long as it stays quiet, which
        is the bug this branch exists to prevent.
        """
        if not self.is_seeded():
            new_reset = now
            self.reset_ts = new_reset
            self.updated_ts = new_reset
            if self.family != _FAMILY_POWER:
                self.raw_start = self.raw_last
            self._touched_since_reset = False
            return

        if self.family == _FAMILY_POWER or self._touched_since_reset:
            new_reset = self.updated_ts
            self.reset_ts = new_reset
            self.updated_ts = new_reset
            if self.family != _FAMILY_POWER:
                self.raw_start = self.raw_last
        else:
            # ENERGY family, genuinely idle since the last reset.
            self.updated_ts = now

        self._touched_since_reset = False

    def update_power(self, new_value: float, event_ts: datetime) -> None:
        """Incorporate a new instantaneous power reading (W/kW) via time-weighted average.

        The weight of each sample is the duration from the previous update to
        this event.  The very first event within a window sets avg = new_value
        with zero elapsed time (the sample covers no past interval yet); the
        running average becomes meaningful from the second event onward.
        """
        if not self.is_seeded():
            # True first-ever event for this entity: reset_ts still holds the
            # _EPOCH sentinel (never seeded by async_setup - see module
            # docstring amendment). Without this branch, old_elapsed would be
            # computed against 1970-01-01 (decades of "elapsed" seconds),
            # swamping new_elapsed (0 s) to a near-zero weight and making
            # avg come out as ~0 regardless of new_value. Anchor reset_ts
            # here instead, mirroring update_energy's first-event handling.
            self.reset_ts = event_ts
            self.avg = new_value
            self.updated_ts = event_ts
            self._touched_since_reset = True
            return

        prev_ts = self.updated_ts
        old_elapsed = (prev_ts - self.reset_ts).total_seconds()
        new_elapsed = max(0.0, (event_ts - prev_ts).total_seconds())
        total_elapsed = old_elapsed + new_elapsed

        if total_elapsed > 0:
            self.avg = (self.avg * old_elapsed + new_value * new_elapsed) / total_elapsed
        else:
            self.avg = new_value

        self.updated_ts = event_ts
        self._touched_since_reset = True

    def update_energy(self, absolute: float, event_ts: datetime) -> None:
        """Incorporate a new absolute counter reading (Wh/kWh).

        On the very first call (not seeded yet) the anchor is set and no delta
        is accumulated yet.  On subsequent calls within the same window the
        delta grows monotonically.  A decrease (counter reset mid-window) is
        clamped: raw_start is moved to the new value so the remainder of the
        window starts cleanly from the reset point.
        """
        if not self.is_seeded():
            # First event: initialise anchor; delta starts at 0.
            self.raw_start = absolute
            self.raw_last = absolute
            self.reset_ts = event_ts
            self.updated_ts = event_ts
            self._touched_since_reset = True
            return

        if absolute < self.raw_last:
            # Counter reset: clamp — treat current reading as new baseline.
            _LOGGER.debug(
                "Effy: counter reset for %s (%.3f → %.3f), clamping anchor",
                self.entity_id,
                self.raw_last,
                absolute,
            )
            self.raw_start = absolute

        self.raw_last = absolute
        self.updated_ts = event_ts
        self._touched_since_reset = True


def _state_class_family(state_class: str | None, unit: str) -> str:
    """Map (state_class, unit) to _FAMILY_POWER or _FAMILY_ENERGY.

    TOTAL_INCREASING always → energy (it is always a counter).
    TOTAL → energy if the unit is Wh/kWh, power otherwise (best-effort
            assumption; no real TOTAL sensors are available for testing).
    MEASUREMENT / None → power (instantaneous W/kW reading).
    """
    if state_class == SensorStateClass.TOTAL_INCREASING:
        return _FAMILY_ENERGY
    if state_class == SensorStateClass.TOTAL:
        return _FAMILY_ENERGY if unit in ("Wh", "kWh") else _FAMILY_POWER
    return _FAMILY_POWER


def _next_slot_trigger_delay(
    now: datetime,
    slot_minutes: int = SLOT_MINUTES,
    lead_seconds: float = SLOT_TIMER_LEAD_SECONDS,
) -> float:
    """Seconds from ``now`` until the next slot-aligned trigger.

    Slot boundaries are wall-clock aligned multiples of ``slot_minutes``
    (e.g. :00, :05, :10, ... for slot_minutes=5), matching the HA recorder
    statistics slots used by the history path (ADR-003, sensor_utils.SLOT_MINUTES)
    — the same grid, not a separately-invented interval. The trigger itself
    fires ``lead_seconds`` before each boundary. If that point has already
    passed within the current slot (i.e. ``now`` is inside the last
    ``lead_seconds`` before/at a boundary), the delay skips forward to the
    following slot's trigger instead of returning a zero/negative delay.
    """
    slot_seconds = slot_minutes * 60
    epoch = now.timestamp()
    current_slot_start = epoch - (epoch % slot_seconds)
    trigger_at = current_slot_start + slot_seconds - lead_seconds
    if trigger_at <= epoch:
        trigger_at += slot_seconds
    return trigger_at - epoch


class EffyCoordinator:
    """
    Central coordinator for one Effy config entry.

    Lifecycle
    ---------
    Created in async_setup_entry, torn down via async_shutdown (called from
    async_unload_entry).  Child sensors subscribe in async_added_to_hass and
    unsubscribe in async_will_remove_from_hass.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._input_ids: list[str] = entry.options.get(CONF_INPUT_SENSORS, [])
        self._output_ids: list[str] = entry.options.get(CONF_OUTPUT_SENSORS, [])

        # Cache: entity_id → LiveReading accumulator
        self._cache: dict[str, LiveReading] = {}

        # Subscriber registry: entity_id → callback
        self._subscribers: dict[str, SubscriberCallback] = {}

        # Debounce state
        self._refresh_pending: bool = False
        self._unsub_refresh: Callable[[], None] | None = None

        # Slot-aligned timer state (independent of the debounce timer above —
        # see module docstring). Self-reschedules after every firing.
        self._unsub_slot_timer: Callable[[], None] | None = None

        # Listener unsubscribe handle
        self._unsub_listeners: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    @callback  # type: ignore[untyped-decorator]
    def async_setup(self) -> None:
        """Register state-change listeners and seed the cache from current states."""
        all_watched = self._input_ids + self._output_ids

        for eid in all_watched:
            self._cache[eid] = self._make_live_reading(eid)

        self._unsub_listeners = async_track_state_change_event(
            self._hass,
            all_watched,
            self._on_state_change,
        )

        self._schedule_next_slot_timer()

    @callback  # type: ignore[untyped-decorator]
    def async_shutdown(self) -> None:
        """Cancel listeners and any pending debounce/slot timer."""
        if self._unsub_listeners is not None:
            self._unsub_listeners()
            self._unsub_listeners = None
        self._cancel_pending_refresh()
        if self._unsub_slot_timer is not None:
            self._unsub_slot_timer()
            self._unsub_slot_timer = None

    # ------------------------------------------------------------------
    # Subscriber management
    # ------------------------------------------------------------------

    def subscribe(self, entity_id: str, cb: SubscriberCallback) -> Callable[[], None]:
        """Register a child sensor callback.

        Returns an unsubscribe callable (call it from async_will_remove_from_hass).
        """
        self._subscribers[entity_id] = cb

        def _unsubscribe() -> None:
            self._subscribers.pop(entity_id, None)

        return _unsubscribe

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    @callback  # type: ignore[untyped-decorator]
    def _on_state_change(self, event: Event) -> None:
        """Handle a state-change event for any watched entity.

        Updates the LiveReading accumulator for the entity; the actual
        recalculation is deferred by DEBOUNCE_SECONDS (ADR-006 Option C).
        """
        entity_id: str = event.data.get("entity_id", "")
        live = self._cache.get(entity_id)
        if live is None:
            # Entity appeared after setup (shouldn't normally happen) — create on the fly.
            live = self._make_live_reading(entity_id)
            self._cache[entity_id] = live

        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown", ""):
            return

        try:
            value = float(state.state)
        except ValueError:
            _LOGGER.warning("Effy: cannot parse state '%s' for %s", state.state, entity_id)
            return

        # Use the timestamp HA recorded when the event fired — more accurate
        # than a fresh datetime.now() call which would include any scheduling
        # delay between the event being enqueued and this handler running.
        event_ts: datetime = event.time_fired

        if live.family == _FAMILY_POWER:
            live.update_power(value, event_ts)
        else:
            live.update_energy(value, event_ts)

        if not self._refresh_pending:
            self._refresh_pending = True
            self._unsub_refresh = async_call_later(self._hass, DEBOUNCE_SECONDS, self._do_refresh)

    # ------------------------------------------------------------------
    # Recalculation
    # ------------------------------------------------------------------

    @callback  # type: ignore[untyped-decorator]
    def _do_refresh(self, _now: Any) -> None:
        """Debounce timer fired – clear debounce state, then recalculate."""
        self._refresh_pending = False
        self._unsub_refresh = None
        self._recalculate_and_reset(datetime.now(tz=timezone.utc))

    @callback  # type: ignore[untyped-decorator]
    def _on_slot_timer(self, _now: Any) -> None:
        """Slot-aligned timer fired – recalculate, then reschedule for the next slot.

        Deliberately does NOT touch `_refresh_pending` / `_unsub_refresh`:
        this timer is fully independent of the debounce timer (see module
        docstring) and must not cancel or interfere with a debounce cycle
        that may currently be pending.
        """
        try:
            self._recalculate_and_reset(datetime.now(tz=timezone.utc))
        finally:
            # Always reschedule, even if recalculation raised, so a single
            # bad cycle doesn't permanently stop the slot timer.
            self._schedule_next_slot_timer()

    def _schedule_next_slot_timer(self) -> None:
        """(Re)schedule the slot-aligned timer for its next trigger point."""
        delay = _next_slot_trigger_delay(datetime.now(tz=timezone.utc))
        self._unsub_slot_timer = async_call_later(self._hass, delay, self._on_slot_timer)

    def _recalculate_and_reset(self, now: datetime) -> None:
        """Convert accumulators, recalculate, push, then reset every LiveReading.

        Shared by both the debounce timer (_do_refresh) and the slot-aligned
        timer (_on_slot_timer) — the two triggers differ only in what timer
        bookkeeping happens around this call, not in the recalculation itself.
        """
        inputs: list[SensorReading] = []
        outputs: list[SensorReading] = []

        for eid in self._input_ids:
            live = self._cache.get(eid)
            if live is None:
                continue
            reading = live.to_sensor_reading()
            if reading is not None:
                inputs.append(reading)

        for eid in self._output_ids:
            live = self._cache.get(eid)
            if live is None:
                continue
            reading = live.to_sensor_reading()
            if reading is not None:
                outputs.append(reading)

        if not inputs:
            _LOGGER.debug("Effy coordinator: no valid input readings, skipping refresh")
            # Reset even on skip so idle accumulators don't carry over indefinitely.
            self._reset_all_cache(now)
            return

        distribution = distribute_loss(inputs, outputs)

        _LOGGER.debug(
            "Effy coordinator: total_loss=%.1f W, pushing to %d subscribers",
            distribution.total_loss_w,
            len(self._subscribers),
        )

        for cb in self._subscribers.values():
            cb(distribution)

        # Reset all accumulators: the next window starts from the end of this one.
        self._reset_all_cache(now)

    def force_refresh(self) -> None:
        """Trigger an immediate recalculation (e.g. on first load).

        Does not re-read sensor states — the cache already holds whatever
        accumulated since setup / the last recalculation.  Calling
        get_current_value() here would inject a raw absolute reading into an
        accumulator that expects only deltas, corrupting the energy sensors.
        """
        self._cancel_pending_refresh()
        self._recalculate_and_reset(datetime.now(tz=timezone.utc))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_live_reading(self, entity_id: str) -> LiveReading:
        """Create a fresh LiveReading for an entity from its current HA state."""
        state = self._hass.states.get(entity_id)
        unit = "W"
        sc = None
        if state is not None:
            unit = state.attributes.get("unit_of_measurement", "W")
            sc = state.attributes.get("state_class")
        family = _state_class_family(sc, unit)
        return LiveReading(entity_id=entity_id, unit=unit, family=family)

    def _reset_all_cache(self, now: datetime) -> None:
        """Reset all LiveReading accumulators after a recalculation."""
        for live in self._cache.values():
            live.reset(now)

    def _cancel_pending_refresh(self) -> None:
        if self._unsub_refresh is not None:
            self._unsub_refresh()
            self._unsub_refresh = None
        self._refresh_pending = False
