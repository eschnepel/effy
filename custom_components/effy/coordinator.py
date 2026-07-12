"""
=====================================================================
 LIVE-PATH DISABLED — 2026-07-09. See disabled/README.md.

 EffyCoordinator partially remains: the slot-aligned timer drives a
 history-path computation of recent slots (async_recalculate_recent,
 ADR-011, ADR-012) instead of live event accumulation.

 Everything event-driven (LiveReading, state-change listeners, the
 debounce timer, live Wh/kWh->W accumulation) has been moved,
 unmodified, to disabled/coordinator_live.py. Do not reimplement any
 of it here from memory — restore that file instead if live-path
 support is turned back on.
=====================================================================

EffyCoordinator – history-driven slot computation.
(Origin: ADR-006 Option C. Slot timer: ADR-010. Slot→history wiring:
ADR-011. Trapezoidal energy redistribution + "recalculated from"
tracking: ADR-012. Live event path disabled — see banner above.)

Architecture (current)
-----------------------
One coordinator is created per config entry and stored in ``hass.data``.
It owns:
  - A push registry for effective-value distributions: child EffySensor
    instances subscribe and would receive a computed effective value
    after each recalculation — not currently used, since
    async_recalculate_recent writes statistics directly and does not
    (yet) push a live value. See ADR-011 for why a live push cannot
    substitute for this write in the first place (no API to backdate a
    live state into an already-closed slot).
  - A separate, single-value push for the "recalculated from" timestamp
    (ADR-012): both the slot timer below and a manual history rewrite
    (button.py) report the earliest slot they touched, via
    set_recalculated_from — used by EffyRecalculatedFromSensor.
  - A slot-aligned timer that fires SLOT_TIMER_LAG_SECONDS *after* every
    SLOT_MINUTES wall-clock boundary and calls
    history.async_recalculate_recent (ADR-011, ADR-012). Firing after
    (not before) the boundary trades off some of the recorder-compile
    safety margin ADR-010 originally used for fresher data — see ADR-011
    for the reasoning and its limits. The recomputed range is dynamic,
    not a fixed single slot — see async_recalculate_recent's docstring
    (a trapezoidal-redistributed energy jump, ADR-012, can touch more
    than one slot).

No state-change listeners are registered and no LiveReading cache
exists while the live path is disabled — see disabled/coordinator_live.py
for that implementation, including the full accumulator formulas.

Thread safety
-------------
HA dispatches async_call_later callbacks on the event loop thread.  No
concurrent access is possible, so no locks are needed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .calculation import LossDistribution
from .const import CONF_INPUT_SENSORS, CONF_OUTPUT_SENSORS
from .history import async_recalculate_recent
from .sensor_utils import SLOT_MINUTES

_LOGGER = logging.getLogger(__name__)

# How long after every SLOT_MINUTES wall-clock boundary the slot-aligned
# timer fires (see EffyCoordinator._schedule_next_slot_timer, ADR-010,
# ADR-011). Deliberately short: it trades off recorder-compile safety
# margin for fresher data — see ADR-011 for why 5s was chosen and what
# happens if the recorder hasn't compiled the relevant slot(s) yet
# (async_recalculate_recent finds nothing and simply logs a debug
# message; nothing is written that cycle).
SLOT_TIMER_LAG_SECONDS = 5

# Type alias for a subscriber callback.
SubscriberCallback = Callable[[LossDistribution], None]
RecalculatedFromCallback = Callable[[datetime], None]


def _next_slot_trigger_delay(
    now: datetime,
    slot_minutes: int = SLOT_MINUTES,
    lag_seconds: float = SLOT_TIMER_LAG_SECONDS,
) -> float:
    """Seconds from ``now`` until the next slot-aligned trigger.

    Slot boundaries are wall-clock aligned multiples of ``slot_minutes``
    (e.g. :00, :05, :10, ... for slot_minutes=5), matching the HA recorder
    statistics slots used by the history path (ADR-003, sensor_utils.SLOT_MINUTES)
    — the same grid, not a separately-invented interval. The trigger itself
    fires ``lag_seconds`` after each boundary (ADR-011). If that point has
    already passed within the current slot (i.e. ``now`` is past
    ``lag_seconds`` into the slot), the delay skips forward to the
    following slot's trigger instead of returning a stale/negative delay.
    """
    slot_seconds = slot_minutes * 60
    epoch = now.timestamp()
    current_slot_start = epoch - (epoch % slot_seconds)
    trigger_at = current_slot_start + lag_seconds
    if trigger_at <= epoch:
        trigger_at += slot_seconds
    return trigger_at - epoch


class EffyCoordinator:
    """
    Central coordinator for one Effy config entry.

    Reduced shell while the live path is disabled — see module banner.

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

        # Subscriber registry: entity_id → callback
        self._subscribers: dict[str, SubscriberCallback] = {}

        # "Recalculated from" tracking (ADR-012): the earliest slot touched
        # by the most recent recalculation, from either this coordinator's
        # own slot timer or a manual history rewrite (button.py, via
        # set_recalculated_from). At most one sensor (EffyRecalculatedFromSensor)
        # is expected to subscribe, but this uses the same list-of-callbacks
        # shape as _subscribers for consistency.
        self.recalculated_from: datetime | None = None
        self._recalculated_from_subscribers: list[RecalculatedFromCallback] = []

        # Slot-aligned timer state. Drives the history-driven slot
        # computation (ADR-011). Self-reschedules after every firing.
        self._unsub_slot_timer: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    @callback  # type: ignore[untyped-decorator]
    def async_setup(self) -> None:
        """Start the slot-aligned timer.

        Live-path disabled: no state-change listeners are registered and no
        LiveReading cache is seeded — see module banner and
        disabled/coordinator_live.py.
        """
        self._schedule_next_slot_timer()

    @callback  # type: ignore[untyped-decorator]
    def async_shutdown(self) -> None:
        """Cancel the slot timer."""
        if self._unsub_slot_timer is not None:
            self._unsub_slot_timer()
            self._unsub_slot_timer = None

    # ------------------------------------------------------------------
    # Subscriber management
    # ------------------------------------------------------------------

    def subscribe(self, entity_id: str, cb: SubscriberCallback) -> Callable[[], None]:
        """Register a child sensor callback.

        Returns an unsubscribe callable (call it from async_will_remove_from_hass).
        Kept working while the live path is disabled so EffySensor doesn't
        need changes: it just won't receive any pushes yet (see
        _on_slot_timer).
        """
        self._subscribers[entity_id] = cb

        def _unsubscribe() -> None:
            self._subscribers.pop(entity_id, None)

        return _unsubscribe

    def subscribe_recalculated_from(self, cb: RecalculatedFromCallback) -> Callable[[], None]:
        """Register EffyRecalculatedFromSensor's callback (ADR-012).

        Returns an unsubscribe callable. Unlike subscribe(), this isn't
        keyed by entity_id — there is exactly one "recalculated from"
        sensor per config entry.
        """
        self._recalculated_from_subscribers.append(cb)

        def _unsubscribe() -> None:
            if cb in self._recalculated_from_subscribers:
                self._recalculated_from_subscribers.remove(cb)

        return _unsubscribe

    def set_recalculated_from(self, ts: datetime) -> None:
        """Report the earliest slot touched by a recalculation (ADR-012).

        Called by this coordinator's own slot timer (_on_slot_timer) and,
        externally, by button.py after a manual history rewrite — both
        recalculation entry points funnel through this single method so
        EffyRecalculatedFromSensor doesn't need to know which one fired.
        Always overwrites (not a running minimum): the sensor reflects
        what the *most recent* recalculation touched, not the oldest ever
        seen.
        """
        self.recalculated_from = ts
        for cb in list(self._recalculated_from_subscribers):
            cb(ts)

    # ------------------------------------------------------------------
    # Slot-aligned timer — computes the just-finished slot via the
    # history-path logic (ADR-011)
    # ------------------------------------------------------------------

    @callback  # type: ignore[untyped-decorator]
    def _on_slot_timer(self, _now: Any) -> None:
        """Slot-aligned timer fired: recalculate recent slots via the history path.

        Fires SLOT_TIMER_LAG_SECONDS after a slot boundary (ADR-011).
        Delegates the actual range calculation to
        history.async_recalculate_recent (ADR-012) — it now dynamically
        determines how far back to look (a trapezoidal-redistributed
        energy jump can touch more than one slot), rather than always
        recomputing exactly one fixed slot.

        Schedules the recalculation as a background task rather than
        awaiting it directly — this callback itself must stay synchronous
        (HA's @callback contract), and the timer must still reschedule
        itself promptly regardless of how long the recalculation takes.
        """
        self._hass.async_create_task(self._async_recalculate_recent_and_report())
        self._schedule_next_slot_timer()

    async def _async_recalculate_recent_and_report(self) -> None:
        """Run async_recalculate_recent and forward its result (ADR-012).

        Split out from _on_slot_timer so the recalculation can be awaited
        (needed to get its return value) while _on_slot_timer itself stays
        a synchronous @callback, per HA's contract.
        """
        now = datetime.now(tz=timezone.utc)
        _written, earliest = await async_recalculate_recent(self._hass, self._entry.options, now)
        if earliest is not None:
            self.set_recalculated_from(earliest)

    def _schedule_next_slot_timer(self) -> None:
        """(Re)schedule the slot-aligned timer for its next trigger point."""
        delay = _next_slot_trigger_delay(datetime.now(tz=timezone.utc))
        self._unsub_slot_timer = async_call_later(self._hass, delay, self._on_slot_timer)

    # ------------------------------------------------------------------
    # Manual trigger (called once by the sensor platform on first load)
    # ------------------------------------------------------------------

    def force_refresh(self) -> None:
        """Placeholder while the live path is disabled — see module banner.

        Previously triggered an immediate live recalculation. No
        recalculation source currently exists (state-change-driven live
        accumulation is disabled; history-driven slot computation is not
        yet wired to this coordinator), so this intentionally does nothing
        beyond logging. sensor.py still calls this unconditionally on
        setup — kept as a harmless no-op rather than removed, so that call
        site doesn't need touching for this temporary state.
        """
        _LOGGER.debug(
            "Effy coordinator: force_refresh() called, but recalculation is "
            "currently disabled (see module banner) — no-op"
        )
