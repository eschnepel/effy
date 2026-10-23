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
  - A third push registry, keyed by each derived entity's own entity_id
    (subscribe_updates / notify_updated): after either recalculation path
    writes new statistics for an entity, that entity gets its live state
    pushed as the ``(value, unit)`` of the last (most recent) slot just
    written for it (ADR-016 — revisits the "unknown"-only push this used
    to do; see notify_updated's own docstring for the "right value,
    provisionally wrong slot" tradeoff this accepts) — used by
    EffySensor/EffyDerivedPowerSensor/EffySmoothedSensor so dashboard
    cards show something other than "unknown" between recalculations,
    and so a state_changed event fires either way for cards that only
    refresh on that event (e.g. history/statistics graph cards).
  - The recorder-synchronized slot trigger (TASK-0006, as Shady's
    TASK-0041; supersedes the wall-clock trigger of ADR-010/ADR-011
    Decision 3): the recorder's own EVENT_RECORDER_5MIN_STATISTICS_GENERATED,
    fired from inside its 5-minute compile, starts a *slot job*. The job
    probes (history.async_probe_last_slot_ready, one await-and-retry loop on
    the event loop, at most MAX_PROBES retries, PROBE_INTERVAL apart) until
    the last completed slot's power-family statistics are readable — the
    event fires *before* the recorder commits — and then runs
    history.async_recalculate_recent (ADR-011, ADR-012) once. The former
    wall-clock timer (SLOT_TIMER_LAG_SECONDS after every SLOT_MINUTES
    boundary) stays only as a watchdog: it starts a job when none was
    started for WATCHDOG, so a silently missing event cannot stop the
    recalculation (it also starts the first job after a restart). The
    recomputed range is dynamic, not a fixed single slot — see
    async_recalculate_recent's docstring (a trapezoidal-redistributed
    energy jump, ADR-012, can touch more than one slot).
  - TASK-0007a: the first job after a restart is held until the first
    recorder event (the watchdog counts from setup before any job), and when
    the last completed slot is the last of its hour the slot job rewrites that
    hour's long-term row after its recent recalculation, under the lock.
  - TASK-0005 Stage 2 (ADR-018): the standby power machine. When at least one
    BMS input is configured, every slot job feeds it after its recent
    recalculation, still under the lock (history.async_update_standby reads the
    statistics that recalculation just wrote). The state lives here; the
    standby sensor restores it at start (restore_standby) and receives it after
    every job (subscribe_standby). Nothing is fed before the sensor has
    restored, and the first feed after a start is the replay of ADR-018 §5 —
    inside the slot job, so it never blocks Home Assistant's startup.
  - One lock that serializes the recent recalculation and the history
    recalculation (button), see "Thread safety".
  - A small, volatile, in-memory cache of each energy-family entity's
    last known valid reading (last_valid_energy_readings, ADR-015) — not
    the disabled live path's LiveReading cache below, a much lighter
    weight one used purely to let history.py skip a recorder query when
    it already knows the answer from an earlier cycle this session.

No state-change listeners are registered and no *live-path* LiveReading
cache exists while the live path is disabled — see disabled/coordinator_live.py
for that implementation, including the full accumulator formulas.

Thread safety
-------------
HA dispatches async_call_later callbacks and bus listeners on the event loop
thread, so the coordinator's own state needs no thread lock. What can overlap
is *work*: a slot job's recent recalculation and the button's history
recalculation each await recorder reads and writes, and the older snapshot of
a long history job could overwrite a slot the recent job just wrote correctly.
Both therefore run under one asyncio.Lock (``_recorder_job_lock``); the second
waits, it is not skipped (human decision 2026-10-16, as Shady's
``async_run_cache_job``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_RECORDER_5MIN_STATISTICS_GENERATED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .calculation import DrainState, LossDistribution, drain_state_from_restore
from .const import CONF_OUTPUT_SENSORS
from .inputs import all_input_ids, bms_input_ids, pv_input_ids
from .history import (
    async_probe_last_slot_ready,
    async_recalculate_history,
    async_recalculate_recent,
    async_rewrite_hourly,
    async_update_standby,
    recent_trigger_since,
)
from .sensor_utils import SLOT_MINUTES

_LOGGER = logging.getLogger(__name__)

# How long after every SLOT_MINUTES wall-clock boundary the slot-aligned
# timer fires (see EffyCoordinator._schedule_next_slot_timer, ADR-010,
# ADR-011). Since TASK-0006 this timer is only the watchdog of the
# recorder-synchronized trigger (see the module banner): the job it starts
# probes until the recorder has compiled the slot, so the short lag no
# longer risks reading a slot that is not there yet.
SLOT_TIMER_LAG_SECONDS = 5

# Recorder-synchronized slot job (TASK-0006, as Shady's TASK-0041): the
# recorder fires EVENT_RECORDER_5MIN_STATISTICS_GENERATED from inside its
# compile, *before* the session commits, so a slot job first probes — one
# first read plus up to MAX_PROBES retries, PROBE_INTERVAL apart, with an
# ``await`` on the event loop — until the last completed slot is readable.
# The wall-clock timer starts a job only if none was started for WATCHDOG.
PROBE_INTERVAL = timedelta(seconds=1)
MAX_PROBES = 5
WATCHDOG = timedelta(minutes=6)

# Type alias for a subscriber callback.
SubscriberCallback = Callable[[LossDistribution], None]
RecalculatedFromCallback = Callable[[datetime], None]
# (value, unit) of the entity's last-written slot this run, or (None, None)
# if no value is known for it (ADR-016) — see notify_updated/subscribe_updates.
EntityUpdateCallback = Callable[[float | None, str | None], None]
# The standby power machine's state after every slot job (ADR-018 §5).
StandbyCallback = Callable[[DrainState], None]


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
        # Input sensors by role (ADR-019). ``_input_ids`` is the mixed pool the
        # waterfall works on; the two roles are exposed separately for the
        # standby power sensor (ADR-018, TASK-0005 Stage 2).
        self._pv_input_ids: list[str] = pv_input_ids(entry.options)
        self._bms_input_ids: list[str] = bms_input_ids(entry.options)
        self._input_ids: list[str] = all_input_ids(entry.options)
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

        # "Push unknown after recalculation" registry: derived entity_id →
        # callback. Keyed by the derived entity's OWN entity_id (e.g.
        # sensor.effy_pv_roof / _power / _smoothed) — unlike _subscribers
        # above, which is keyed by the *source* entity_id for the
        # (currently dormant) live distribution channel. See
        # subscribe_updates / notify_updated below.
        self._update_subscribers: dict[str, EntityUpdateCallback] = {}

        # Volatile (in-memory only, never persisted, lost on restart)
        # cache of the last known *valid* raw reading per energy-family
        # entity_id — (timestamp, raw_state_string), same shape as a
        # _fetch_raw_energy_states row (ADR-015). NOT the disabled live
        # path's LiveReading cache (module banner above) — this exists
        # purely to let history._compute_effective_slots skip
        # _fetch_last_valid_state_before's recorder query when it already
        # knows the answer from having processed a valid reading for this
        # entity in some earlier cycle this session. Read and written
        # exclusively by history.py's recalculation functions, passed
        # through by reference — this coordinator does not interpret its
        # contents itself, just owns and hands it out so it survives
        # across calls within the same HA session.
        self.last_valid_energy_readings: dict[str, tuple[datetime, str]] = {}

        # End time (the ``now`` it ran with) of the last slot-timer cycle that
        # *completed* without raising — volatile, in memory only, so ``None``
        # again after every Home Assistant start (ADR-014 Amendment
        # 2026-10-06). Lets the next cycle also catch a counter jump that
        # arrived while no cycle was running (restart, suspended host, failed
        # cycles): history.recent_trigger_since turns it into the
        # ``trigger_since`` of async_recalculate_recent, with a one-off
        # RECENT_REWRITE_MAX_LOOKBACK catch-up when it is still ``None``.
        self._last_recent_run: datetime | None = None

        # Slot-aligned timer state. Drives the history-driven slot
        # computation (ADR-011). Self-reschedules after every firing.
        self._unsub_slot_timer: Callable[[], None] | None = None

        # Recorder-synchronized slot job (TASK-0006). The three parameters
        # are attributes so tests can shorten them.
        self._probe_interval: timedelta = PROBE_INTERVAL
        self._max_probes: int = MAX_PROBES
        self._watchdog: timedelta = WATCHDOG
        self._unsub_recorder_event: Callable[[], None] | None = None
        self._slot_job_task: asyncio.Task[None] | None = None
        self._slot_job_slot: datetime | None = None  # start of the slot it serves
        self._slot_job_probing: bool = False
        self._last_slot_job_at: datetime | None = None
        self._setup_at: datetime | None = None  # the watchdog's reference before any job

        # See _recorder_job_lock.
        self._lock_state: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None

        # Standby power machine (ADR-018). ``_standby_state`` stays ``None`` until
        # the standby sensor has restored (restore_standby): feeding earlier
        # would replay without the restored aggregates and count twice.
        self._standby_state: DrainState | None = None
        self._standby_subscribers: list[StandbyCallback] = []

    @property
    def pv_input_ids(self) -> list[str]:
        """Source entity IDs of the PV inputs (ADR-019)."""
        return list(self._pv_input_ids)

    @property
    def bms_input_ids(self) -> list[str]:
        """Source entity IDs of the BMS inputs (ADR-019); the drain sources of ADR-018."""
        return list(self._bms_input_ids)

    @property
    def standby_enabled(self) -> bool:
        """The standby power sensor exists only with at least one BMS input (ADR-018 D4)."""
        return bool(self._bms_input_ids)

    @property
    def standby_state(self) -> DrainState | None:
        """The standby machine's state, ``None`` before the sensor restored (ADR-018)."""
        return self._standby_state

    def restore_standby(self, data: Mapping[str, Any] | None) -> DrainState:
        """Start the standby machine from the data the sensor restored (ADR-018 §5).

        ``data`` is what ``calculation.drain_restore_data`` produced before the
        restart, or ``None`` when nothing was restored. Without usable data the
        machine starts empty and its first feed replays the last 24 hours. Returns
        the state it starts from; from now on slot jobs feed it.
        """
        restored = drain_state_from_restore(data) if data else None
        self._standby_state = restored if restored is not None else DrainState()
        return self._standby_state

    def subscribe_standby(self, cb: StandbyCallback) -> Callable[[], None]:
        """Register the standby sensor's callback; it gets the state after every slot job."""
        self._standby_subscribers.append(cb)

        def _unsubscribe() -> None:
            if cb in self._standby_subscribers:
                self._standby_subscribers.remove(cb)

        return _unsubscribe

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    @callback  # type: ignore[untyped-decorator]
    def async_setup(self) -> None:
        """Subscribe to the recorder's statistics event and start the watchdog timer.

        Live-path disabled: no state-change listeners are registered and no
        LiveReading cache is seeded — see module banner and
        disabled/coordinator_live.py.
        """
        self._setup_at = self._now()
        self._unsub_recorder_event = self._hass.bus.async_listen(
            EVENT_RECORDER_5MIN_STATISTICS_GENERATED, self._on_recorder_statistics
        )
        self._schedule_next_slot_timer()

    @callback  # type: ignore[untyped-decorator]
    def async_shutdown(self) -> None:
        """Remove the event listener, cancel the watchdog timer and any in-flight slot job."""
        if self._unsub_recorder_event is not None:
            self._unsub_recorder_event()
            self._unsub_recorder_event = None
        if self._unsub_slot_timer is not None:
            self._unsub_slot_timer()
            self._unsub_slot_timer = None
        task = self._slot_job_task
        if task is not None and not task.done():
            task.cancel()
        self._slot_job_task = None

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

    def subscribe_updates(self, entity_id: str, cb: EntityUpdateCallback) -> Callable[[], None]:
        """Register a derived entity's "push latest value after recalculation" callback.

        Keyed by the derived entity's own entity_id (e.g.
        sensor.effy_pv_roof, sensor.effy_pv_roof_power,
        sensor.effy_pv_roof_smoothed) — not the source sensor. Used by
        EffySensor / EffyDerivedPowerSensor / EffySmoothedSensor so they
        know when a recalculation has written new statistics for them and
        what to push as their new live state; see notify_updated() below
        for the ``(value, unit)`` shape and ADR-016 for why a real value
        is pushed here rather than "unknown".

        Returns an unsubscribe callable (call it from async_will_remove_from_hass).
        """
        self._update_subscribers[entity_id] = cb

        def _unsubscribe() -> None:
            self._update_subscribers.pop(entity_id, None)

        return _unsubscribe

    def notify_updated(
        self,
        entity_ids: Iterable[str],
        last_values: dict[str, tuple[float, str]] | None = None,
    ) -> None:
        """Push each subscribed entity's latest recalculated value as its live state.

        Called after a recalculation (this coordinator's own slot timer,
        or a manual history rewrite via button.py) has written new
        statistics — entity_ids is exactly the set of effy_* entity_ids
        that got new data *this run*
        (history.async_recalculate_recent / async_recalculate_history's
        third return value), not every configured sensor, so an entity
        untouched this cycle is left alone.

        ``last_values`` (the fourth return value of those same history.py
        functions) maps a touched entity_id to the ``(value, unit)`` of
        the *last* (most recent) slot written for it this run
        (ADR-016). For every entity_id in entity_ids, the corresponding
        subscriber callback is called with that ``(value, unit)`` pair —
        or ``(None, None)`` if entity_ids contains an id ``last_values``
        doesn't (shouldn't normally happen, since both come from the same
        recalculation call, but kept defensive rather than a KeyError).

        This is a genuine, immediately-visible live state update, not a
        backdated one: it's timestamped "now" like any ordinary state
        change, landing in whatever 5-minute slot is currently open — the
        *statistics* write above is what actually corrects the
        already-closed slot the value truly belongs to (ADR-011 still
        applies there: no public API backdates a live state into a closed
        slot). ADR-016 covers why this "right value, provisionally wrong
        slot" tradeoff is preferable to showing "unknown" until the next
        cycle corrects it. It also, as before, guarantees a state_changed
        event fires for the benefit of dashboard cards that only refresh
        on that event (e.g. history/statistics graph cards).

        Silently ignores any entity_id with no current subscriber (e.g. a
        sensor.effy_*_power entity that hasn't finished async_added_to_hass
        yet, or was never created because its source isn't energy-family) —
        this is a best-effort UI nicety, not a correctness-critical path.
        """
        last_values = last_values or {}
        for entity_id in entity_ids:
            cb = self._update_subscribers.get(entity_id)
            if cb is not None:
                value, unit = last_values.get(entity_id, (None, None))
                cb(value, unit)

    # ------------------------------------------------------------------
    # Slot-aligned timer — computes the just-finished slot via the
    # history-path logic (ADR-011)
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """The current time (a method so tests can control it)."""
        return datetime.now(tz=timezone.utc)

    @callback  # type: ignore[untyped-decorator]
    def _on_recorder_statistics(self, _event: Any) -> None:
        """The recorder compiled (and is about to commit) a 5-minute slot.

        Only schedules the slot job: no recorder access, nothing blocking —
        this runs inside the recorder's compile and must stay a plain
        ``@callback`` (TASK-0006).
        """
        self._start_slot_job(self._now())

    @callback  # type: ignore[untyped-decorator]
    def _on_slot_timer(self, now: Any) -> None:
        """Watchdog: the wall-clock timer fired SLOT_TIMER_LAG_SECONDS after a boundary.

        Starts a slot job only if none was started for ``self._watchdog`` — and
        before the first job, only once that long has passed since setup
        (TASK-0007a): the first job after a restart is held until the first
        recorder event, because HA's catch-up compile of the slots missed
        during downtime may not have reached a slot yet, and a row Effy
        inserted first would make the recorder's insert fail. A silently
        missing event still cannot stop the recalculation. In normal operation
        the recorder event keeps arriving and this never starts one. Always
        reschedules itself, whatever the outcome.
        """
        if not isinstance(now, datetime):
            now = self._now()
        reference = self._last_slot_job_at or self._setup_at
        if reference is None or now - reference >= self._watchdog:
            self._start_slot_job(now)
        self._schedule_next_slot_timer()

    @staticmethod
    def _last_completed_slot(now: datetime) -> datetime:
        """Start of the last completed slot as of ``now`` (never the current one)."""
        slot_seconds = SLOT_MINUTES * 60
        epoch = now.timestamp()
        return datetime.fromtimestamp(
            epoch - (epoch % slot_seconds) - slot_seconds, tz=timezone.utc
        )

    def _start_slot_job(self, now: datetime) -> None:
        """Start one slot job for the last completed slot as of ``now``.

        Unless one was already started for that slot (a duplicate event, or
        the watchdog right after an event). A job still *probing* for an
        older slot is cancelled first and never runs its body; one that has
        already started its body is left to finish (a newer job queues
        behind it on the lock).
        """
        slot = self._last_completed_slot(now)
        if slot == self._slot_job_slot:
            return
        previous = self._slot_job_task
        if previous is not None and not previous.done() and self._slot_job_probing:
            previous.cancel()
        # All state before ``async_create_task``: the task may start eagerly.
        self._slot_job_slot = slot
        self._slot_job_probing = True
        self._last_slot_job_at = now
        self._slot_job_task = self._hass.async_create_task(self._async_slot_job(now, slot))

    async def _async_slot_job(self, now: datetime, slot: datetime) -> None:
        """One slot job: probe until the last completed slot is readable, then run the body once.

        Retries after ``self._probe_interval`` with an ``await`` on the loop,
        at most ``self._max_probes`` times. A sensor that never delivers
        does not block the job: after the bounded retries the body runs
        anyway (a failing probe likewise, ADR-000 §8).
        """
        try:
            try:
                ready = await async_probe_last_slot_ready(self._hass, self._entry.options, now)
                retries = 0
                while not ready and retries < self._max_probes:
                    await asyncio.sleep(self._probe_interval.total_seconds())
                    retries += 1
                    ready = await async_probe_last_slot_ready(self._hass, self._entry.options, now)
            except asyncio.CancelledError:
                if self._slot_job_slot != slot:
                    return  # superseded by a newer slot's job: end quietly
                raise  # shutdown: a real cancellation
            except Exception:
                _LOGGER.exception("Effy: slot probe failed — running the slot job without waiting")
            self._slot_job_probing = False
            await self._async_recalculate_recent_and_report(slot)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Effy: slot job failed (slot %s)", slot)

    def _recorder_job_lock(self) -> asyncio.Lock:
        """The one lock of the recent and the history recalculation.

        Created for the running loop: an ``asyncio.Lock`` binds to the loop
        it first has to wait on, and only tests reuse a coordinator across
        loops.
        """
        loop = asyncio.get_running_loop()
        state = self._lock_state
        if state is None or state[0] is not loop:
            state = (loop, asyncio.Lock())
            self._lock_state = state
        return state[1]

    async def _async_recalculate_recent_and_report(self, slot: datetime | None = None) -> None:
        """Run async_recalculate_recent and forward its results (ADR-012,
        gap-interpolation feature, and the "push unknown" UX fix).

        The body of a slot job. Holds the recalculation lock for the whole
        run — ``now`` is taken only once the lock is held, so a job that
        waited behind a history recalculation reads current data.

        ``slot`` is the last completed slot the job serves. When it is the last
        slot of its hour, the hour's long-term row is rewritten afterwards,
        still under the lock (TASK-0007a, ADR-011 Amendment 2026-10-16): the
        recorder rolls the hour up before Effy can update that last slot.
        """
        async with self._recorder_job_lock():
            now = self._now()
            _written, earliest, touched, last_values = await async_recalculate_recent(
                self._hass,
                self._entry.options,
                now,
                energy_reading_cache=self.last_valid_energy_readings,
                trigger_since=recent_trigger_since(self._last_recent_run, now),
            )
            # Only reached when the cycle completed (an exception above leaves
            # the marker alone, so the next cycle's trigger window covers this
            # one too). max(): cycles may overlap and finish out of order.
            if self._last_recent_run is None or now > self._last_recent_run:
                self._last_recent_run = now
            if earliest is not None:
                self.set_recalculated_from(earliest)
            if touched:
                self.notify_updated(touched, last_values)
            if slot is not None and (slot + timedelta(minutes=SLOT_MINUTES)).minute == 0:
                await self._async_rewrite_hour(slot, last_values)
            await self._async_update_standby(now)

    async def _async_update_standby(self, now: datetime) -> None:
        """Feed the standby machine and tell its sensor (ADR-018).

        Called with the recalculation lock held, after the recent recalculation
        wrote the statistics the machine reads. Does nothing without a BMS input or
        before the sensor restored. A failure is logged and never fails the slot
        job (ADR-000 §8): the state is left as it was, so the next job feeds the
        same slots again.
        """
        state = self._standby_state
        if state is None or not self._bms_input_ids:
            return
        try:
            new_state = await async_update_standby(self._hass, self._entry.options, state, now)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Effy: updating the standby power estimate failed")
            return
        self._standby_state = new_state
        for cb in list(self._standby_subscribers):
            cb(new_state)

    async def _async_rewrite_hour(
        self, slot: datetime, last_values: dict[str, tuple[float, str]]
    ) -> None:
        """Rewrite the long-term row of the hour that ``slot`` completes.

        Called with the recalculation lock held. A failure is logged and not
        retried: the next history recalculation corrects the hour, and it must
        never fail the slot job (ADR-000 §8).
        """
        units = {entity_id: unit for entity_id, (_value, unit) in last_values.items()}
        if not units:
            return
        hour_start = slot.replace(minute=0, second=0, microsecond=0)
        try:
            await async_rewrite_hourly(self._hass, units, hour_start)
        except Exception:
            _LOGGER.exception("Effy: rewriting the hourly statistics of %s failed", hour_start)

    async def async_recalculate_history_and_report(self) -> int:
        """Run the full history recalculation (button) under the same lock.

        Reports the earliest touched slot and pushes the last values exactly
        as the button did before TASK-0006 (ADR-012, ADR-016). Returns the
        number of slots written. Exceptions propagate to the caller.
        """
        async with self._recorder_job_lock():
            written, recalculated_from, touched, last_values = await async_recalculate_history(
                self._hass,
                self._entry.options,
                energy_reading_cache=self.last_valid_energy_readings,
            )
            if recalculated_from is not None:
                self.set_recalculated_from(recalculated_from)
            if touched:
                self.notify_updated(touched, last_values)
            return written

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
