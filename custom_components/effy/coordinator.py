"""
EffyCoordinator – shared coordinator for live loss distribution (ADR-006 Option C).

Architecture
------------
One coordinator is created per config entry and stored in ``hass.data``.
It owns:
  - O(M+K) state-change listeners (inputs + outputs), not O(N·(M+K)).
  - A cache of the latest raw reading for every watched entity.
  - A debounce timer (DEBOUNCE_SECONDS) that fires one recalculation after
    a burst of state-change events has settled.
  - A push registry: child EffySensor instances subscribe and receive their
    computed effective value after each recalculation.

Flow
----
state_change event
  → update cache entry
  → if no refresh pending: schedule _do_refresh(DEBOUNCE_SECONDS)
  → ... (further events only update cache, no new timer)
  → timer fires → _do_refresh
      → distribute_loss over cached readings
      → push result to each subscriber via their callback
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .calculation import LossDistribution, SensorReading, distribute_loss
from .const import CONF_INPUT_SENSORS, CONF_OUTPUT_SENSORS
from .sensor_utils import get_current_value

_LOGGER = logging.getLogger(__name__)

# How long to wait after the last state-change event before recalculating.
# Chosen to be long enough to absorb a typical Modbus/SolarEdge poll burst
# (all registers arrive within ~100 ms) while keeping display lag imperceptible.
# See ADR-006 (Option C) for the full evaluation of this debounce strategy
# against immediate updates (Option A) and a cache-only approach (Option D).
# With a value of 0.3 a maximum of roughly 3 recalculation cycles occour in one second.
DEBOUNCE_SECONDS = 0.3

# Type alias for a subscriber callback: receives the full distribution result
# plus the source entity id so each sensor can extract its own slice.
SubscriberCallback = Callable[[LossDistribution], None]


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

        # Cache: entity_id → latest SensorReading (or None if unavailable)
        self._cache: dict[str, SensorReading | None] = {}

        # Subscriber registry: entity_id → callback
        self._subscribers: dict[str, SubscriberCallback] = {}

        # Debounce state
        self._refresh_pending: bool = False
        self._unsub_refresh: Callable[[], None] | None = None

        # Listener unsubscribe handle
        self._unsub_listeners: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    @callback
    def async_setup(self) -> None:
        """Register state-change listeners and seed the cache from current states."""
        all_watched = self._input_ids + self._output_ids

        # Seed cache from whatever HA already knows
        for eid in all_watched:
            self._cache[eid] = get_current_value(self._hass, eid)

        self._unsub_listeners = async_track_state_change_event(
            self._hass,
            all_watched,
            self._on_state_change,
        )

    @callback
    def async_shutdown(self) -> None:
        """Cancel listeners and any pending debounce timer."""
        if self._unsub_listeners is not None:
            self._unsub_listeners()
            self._unsub_listeners = None
        self._cancel_pending_refresh()

    # ------------------------------------------------------------------
    # Subscriber management
    # ------------------------------------------------------------------

    def subscribe(self, entity_id: str, cb: SubscriberCallback) -> Callable[[], None]:
        """
        Register a child sensor callback.

        Returns an unsubscribe callable (call it from async_will_remove_from_hass).
        """
        self._subscribers[entity_id] = cb

        def _unsubscribe() -> None:
            self._subscribers.pop(entity_id, None)

        return _unsubscribe

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    @callback
    def _on_state_change(self, event: Event) -> None:
        """Handle a state-change event for any watched entity.

        Only the cache is updated synchronously; the actual recalculation is
        debounced (ADR-006 Option C) so that a burst of near-simultaneous
        events — e.g. several registers from one Modbus poll — triggers
        exactly one ``distribute_loss`` call instead of one per event.
        """
        entity_id: str = event.data.get("entity_id", "")
        self._cache[entity_id] = get_current_value(self._hass, entity_id)

        if not self._refresh_pending:
            self._refresh_pending = True
            self._unsub_refresh = async_call_later(self._hass, DEBOUNCE_SECONDS, self._do_refresh)

    # ------------------------------------------------------------------
    # Recalculation
    # ------------------------------------------------------------------

    @callback
    def _do_refresh(self, _now: Any) -> None:
        """Debounce timer fired – recalculate and push to all subscribers."""
        self._refresh_pending = False
        self._unsub_refresh = None

        inputs: list[SensorReading] = []
        outputs: list[SensorReading] = []

        for eid in self._input_ids:
            reading = self._cache.get(eid)
            if reading is not None:
                inputs.append(reading)

        for eid in self._output_ids:
            reading = self._cache.get(eid)
            if reading is not None:
                outputs.append(reading)

        if not inputs:
            _LOGGER.debug("Effy coordinator: no valid input readings, skipping refresh")
            return

        distribution = distribute_loss(inputs, outputs)

        _LOGGER.debug(
            "Effy coordinator: total_loss=%.1f W, pushing to %d subscribers",
            distribution.total_loss_w,
            len(self._subscribers),
        )

        for cb in self._subscribers.values():
            cb(distribution)

    def force_refresh(self) -> None:
        """Trigger an immediate recalculation (e.g. on first load)."""
        self._cancel_pending_refresh()
        # Re-read all states into cache before firing
        for eid in list(self._cache):
            self._cache[eid] = get_current_value(self._hass, eid)
        self._do_refresh(None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cancel_pending_refresh(self) -> None:
        if self._unsub_refresh is not None:
            self._unsub_refresh()
            self._unsub_refresh = None
        self._refresh_pending = False
