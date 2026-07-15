"""Sensor platform for Effy – one effective sensor per input sensor.

Sensors hold no listeners of their own; they subscribe to the shared
EffyCoordinator and receive computed results via push (ADR-006 Option C).
Live-path disabled (2026-07-09, disabled/README.md): no *numeric* push
currently happens for EffySensor/EffyDerivedPowerSensor/EffySmoothedSensor
— their statistics are populated directly by history.py instead (ADR-011,
ADR-012). They do each receive a lightweight "unknown" push right after
every recalculation that touches them (EffyCoordinator.notify_updated),
purely so a state_changed event fires for dashboard cards that only
refresh on that event — see EffySensor._on_updated.
EffyRecalculatedFromSensor is the one sensor with an actual live *value*
update even with the live path off: it subscribes to the coordinator's
separate recalculated-from channel, which carries a real timestamp.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import slugify

from .calculation import LossDistribution, effective_in_original_unit
from .const import CONF_INPUT_SENSORS, CONF_OUTPUT_SENSORS, DOMAIN
from .coordinator import EffyCoordinator
from .sensor_utils import effective_unit_for, get_sensor_meta, is_energy_family


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Shared device grouping for every Effy entity."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Effy",
        manufacturer="Effy",
        model="PV Loss Distributor",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Effy sensor entities."""
    coordinator: EffyCoordinator = hass.data[DOMAIN][entry.entry_id]
    input_ids: list[str] = entry.options.get(CONF_INPUT_SENSORS, [])
    output_ids: list[str] = entry.options.get(CONF_OUTPUT_SENSORS, [])

    entities: list[SensorEntity] = [
        EffySensor(hass, entry, coordinator, entity_id) for entity_id in input_ids
    ]

    # Derived-power sensors (ADR-012, energy-family, inputs AND outputs)
    # and smoothed sensors (gap-interpolation feature, power-family INPUT
    # only) both need the source entity's live state_class/unit to decide
    # which one (if any) applies — see _build_derived_entities below.
    # That state only exists once the *source's own* integration has
    # finished loading and reported a first state. Effy cannot declare a
    # static after_dependencies on that integration (manifest.json only
    # knows about `recorder` — the actual source is an arbitrary,
    # user-chosen entity picked at config time), so at HA startup this
    # platform's own setup frequently runs before that state exists.
    # Entities with no state yet are deferred to _add_late_derived_entities
    # below instead of being silently skipped — otherwise the derived
    # entity for a perfectly valid source sensor simply never gets created
    # for the rest of that HA session (the bug this fixes: the entity is
    # entirely missing from the integration page, not just lacking data).
    input_id_set = set(input_ids)
    pending_ids: list[str] = []
    for entity_id in input_ids + output_ids:
        state = hass.states.get(entity_id)
        if state is None:
            pending_ids.append(entity_id)
            continue
        entities.extend(
            _build_derived_entities(hass, entry, coordinator, entity_id, entity_id in input_id_set)
        )

    entities.append(EffyRecalculatedFromSensor(hass, entry, coordinator))

    async_add_entities(entities, update_before_add=False)

    if pending_ids:
        _add_late_derived_entities(
            hass, entry, coordinator, pending_ids, input_id_set, async_add_entities
        )

    # Trigger one immediate recalculation so sensors have values on first load
    coordinator.force_refresh()


def _build_derived_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: EffyCoordinator,
    entity_id: str,
    is_input: bool,
) -> list[SensorEntity]:
    """Return whichever derived entity applies to one source sensor, given
    its now-known state_class/unit.

    Energy-family (TOTAL_INCREASING / TOTAL-as-energy) sensors get an
    EffyDerivedPowerSensor, input or output alike (ADR-012). Power-family
    (MEASUREMENT / TOTAL-as-power) *input* sensors get an
    EffySmoothedSensor instead (gap-interpolation feature) — output
    sensors and non-input power-family sensors get neither. Shared by the
    initial setup pass and the late-binding listener below so the two
    paths can't silently diverge.
    """
    meta = get_sensor_meta(hass, entity_id)
    state_class = meta.get("state_class")
    unit = meta.get("unit", "W")
    if is_energy_family(state_class, unit):
        return [EffyDerivedPowerSensor(hass, entry, coordinator, entity_id)]
    if is_input:
        return [EffySmoothedSensor(hass, entry, coordinator, entity_id)]
    return []


def _add_late_derived_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: EffyCoordinator,
    pending_ids: list[str],
    input_id_set: set[str],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create derived entities whose classification couldn't be resolved
    at initial setup because the source entity had no state yet (see
    async_setup_entry above for why this race is unavoidable via
    manifest.json alone).

    Listens for each pending entity's first state report and calls
    _build_derived_entities() at that point instead — one-shot per
    entity_id; once every pending id has been resolved, the listener
    removes itself.
    """
    remaining: set[str] = set(pending_ids)

    @callback  # type: ignore[untyped-decorator]
    def _on_state_changed(event: Any) -> None:
        entity_id: str = event.data["entity_id"]
        new_state = event.data.get("new_state")
        if new_state is None or entity_id not in remaining:
            # Still no state (e.g. entity_id went unavailable->unavailable)
            # or already resolved — nothing to do yet.
            return
        remaining.discard(entity_id)

        new_entities = _build_derived_entities(
            hass, entry, coordinator, entity_id, entity_id in input_id_set
        )
        if new_entities:
            async_add_entities(new_entities)

        if not remaining:
            unsub()

    unsub = async_track_state_change_event(hass, pending_ids, _on_state_changed)
    entry.async_on_unload(unsub)


class EffySensor(SensorEntity):  # type: ignore[misc]
    """Effective-power sensor for one input source."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: EffyCoordinator,
        source_entity_id: str,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._source_entity_id = source_entity_id

        self._slug = slugify(source_entity_id.split(".")[-1])
        self._attr_unique_id = f"{entry.entry_id}_{self._slug}"
        self._attr_native_value: float | None = None
        self._attr_native_unit_of_measurement: str | None = None
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._source_unit: str = "W"

        self._unsub_coordinator: Callable[[], None] | None = None
        self._unsub_updates: Callable[[], None] | None = None

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id or ""

    @property
    def name(self) -> str:
        state = self._hass.states.get(self._source_entity_id)
        friendly = state.attributes.get("friendly_name", self._slug) if state else self._slug
        return f"{friendly} (effective)"

    @property
    def entity_id(self) -> str:
        return f"sensor.effy_{self._slug}"

    @entity_id.setter
    def entity_id(self, value: str) -> None:
        pass

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates (ADR-006 Option C), plus the
        "push unknown after recalculation" channel (see _on_updated)."""
        self._unsub_coordinator = self._coordinator.subscribe(
            self._source_entity_id, self._on_distribution
        )
        self._unsub_updates = self._coordinator.subscribe_updates(self.entity_id, self._on_updated)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from coordinator."""
        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None
        if self._unsub_updates is not None:
            self._unsub_updates()
            self._unsub_updates = None

    @callback  # type: ignore[untyped-decorator]
    def _on_updated(self) -> None:
        """Push a fresh "unknown" state (EffyCoordinator.notify_updated).

        Fires after a recalculation has written new statistics for this
        entity. There is no live numeric value to push here (ADR-011 — no
        API to backdate a live state into an already-closed slot) — this
        exists purely so a state_changed event fires at all, letting
        dashboard cards that only refresh on that event (e.g.
        history/statistics graph cards) know to refetch.
        """
        self._attr_native_value = None
        self.async_write_ha_state()

    @callback  # type: ignore[untyped-decorator]
    def _on_distribution(self, distribution: LossDistribution) -> None:
        """Receive a new distribution result from the coordinator and update state.

        ``distribute_loss`` (and, upstream of it, ``LiveReading.to_sensor_reading``
        for TOTAL_INCREASING/TOTAL-as-energy sources) always works in Watts —
        an energy-family source's raw Wh/kWh delta is converted to a
        W-equivalent *before* it ever reaches the coordinator's distribution
        call (ADR-008), and never converted back to an energy unit, because
        ``_from_w`` only strips a kilo- prefix and has no notion of "per
        hour". So the value reported here for an energy-family source is
        always itself a power reading, and must be labeled and converted
        as such (``effective_unit_for``) — not with the source entity's own
        raw Wh/kWh unit, which would be a category error (energy vs. power),
        not just a scale error. See the bug this fixed for the long version.
        """
        # Effective value may not be present if this sensor had no reading
        if self._source_entity_id not in distribution.effective_values_w:
            return

        src_state = self._hass.states.get(self._source_entity_id)
        if src_state:
            raw_unit = src_state.attributes.get("unit_of_measurement", "W")
            self._source_unit = effective_unit_for(raw_unit)
            self._attr_native_unit_of_measurement = self._source_unit

        self._attr_native_value = round(
            effective_in_original_unit(self._source_entity_id, distribution, self._source_unit),
            3,
        )

        self._attr_extra_state_attributes: dict[str, Any] = {
            "source_entity": self._source_entity_id,
            "total_loss_w": round(distribution.total_loss_w, 3),
            "loss_share_w": round(distribution.shares.get(self._source_entity_id, 0.0), 3),
        }

        self.async_write_ha_state()


class EffyDerivedPowerSensor(SensorEntity):  # type: ignore[misc]
    """Raw, pre-loss-distribution derived-power sensor (ADR-012).

    One per energy-family (TOTAL_INCREASING / TOTAL-as-energy) sensor,
    input or output — the trapezoidal-redistributed power
    (calculation.trapezoidal_slot_contributions) is a per-sensor quantity
    independent of the input/output role that only matters for
    distribute_loss, unlike EffySensor's "effective" value.

    Exists mainly so history.py's async_recalculate_history /
    async_recalculate_recent have a stable entity_id to attach statistics
    to (sensor.effy_{slug}_power) — like EffySensor, it receives no live
    numeric push while the live path is disabled (see disabled/README.md);
    its statistics are populated directly by history.py. It does, however,
    push a state_changed("unknown") after each recalculation that touches
    it — see _on_updated / EffyCoordinator.notify_updated.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: EffyCoordinator,
        source_entity_id: str,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._source_entity_id = source_entity_id

        self._slug = slugify(source_entity_id.split(".")[-1])
        self._attr_unique_id = f"{entry.entry_id}_{self._slug}_power"
        self._attr_native_value: float | None = None
        self._attr_native_unit_of_measurement: str | None = None
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._unsub_updates: Callable[[], None] | None = None

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id or ""

    @property
    def name(self) -> str:
        state = self._hass.states.get(self._source_entity_id)
        friendly = state.attributes.get("friendly_name", self._slug) if state else self._slug
        return f"{friendly} (derived power)"

    @property
    def entity_id(self) -> str:
        return f"sensor.effy_{self._slug}_power"

    @entity_id.setter
    def entity_id(self, value: str) -> None:
        pass

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe to the "push unknown after recalculation" channel
        (see _on_updated)."""
        self._unsub_updates = self._coordinator.subscribe_updates(self.entity_id, self._on_updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_updates is not None:
            self._unsub_updates()
            self._unsub_updates = None

    @callback  # type: ignore[untyped-decorator]
    def _on_updated(self) -> None:
        """Push a fresh "unknown" state (EffyCoordinator.notify_updated).

        See EffySensor._on_updated for why "unknown" and not the actual
        computed value.
        """
        self._attr_native_value = None
        self.async_write_ha_state()


class EffySmoothedSensor(SensorEntity):  # type: ignore[misc]
    """Gap-interpolated power-family INPUT sensor.

    Home Assistant's compiled 5-minute statistics for a MEASUREMENT/
    TOTAL-as-power *input* sensor occasionally have short gaps — a slot
    the recorder simply never compiled a ``mean`` for (a transient
    connectivity blip, a slow-polling source, etc.). TOTAL_INCREASING/
    energy-family sensors don't have this problem in the same way — their
    raw counter history is instead redistributed via the trapezoidal rule
    (ADR-012, EffyDerivedPowerSensor above).

    history.py's ``_compute_effective_slots`` bridges gaps of up to
    ``calculation.INTERPOLATION_MAX_GAP_SLOTS`` (2) consecutive missing
    slots via linear interpolation between the nearest valid readings on
    either side, merges that smoothed series into distribute_loss's own
    input (so the waterfall calculation sees it too, not just this
    sensor), and also writes it out here as its own statistic
    (``sensor.effy_{slug}_smoothed``) so the smoothing is visible, not
    just an invisible internal correction. A gap longer than that is left
    as a genuine gap rather than extrapolated across.

    Exists mainly so history.py has a stable place to attach these
    statistics to — like EffySensor/EffyDerivedPowerSensor, it receives no
    live numeric push while the live path is disabled (see
    disabled/README.md); its statistics are populated directly by
    history.py. It does, however, push a state_changed("unknown") after
    each recalculation that touches it — see _on_updated /
    EffyCoordinator.notify_updated. Output sensors never get one of these
    (out of scope for this feature).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: EffyCoordinator,
        source_entity_id: str,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._source_entity_id = source_entity_id

        self._slug = slugify(source_entity_id.split(".")[-1])
        self._attr_unique_id = f"{entry.entry_id}_{self._slug}_smoothed"
        self._attr_native_value: float | None = None
        self._attr_native_unit_of_measurement: str | None = None
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._unsub_updates: Callable[[], None] | None = None

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id or ""

    @property
    def name(self) -> str:
        state = self._hass.states.get(self._source_entity_id)
        friendly = state.attributes.get("friendly_name", self._slug) if state else self._slug
        return f"{friendly} (smoothed)"

    @property
    def entity_id(self) -> str:
        return f"sensor.effy_{self._slug}_smoothed"

    @entity_id.setter
    def entity_id(self, value: str) -> None:
        pass

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe to the "push unknown after recalculation" channel
        (see _on_updated)."""
        self._unsub_updates = self._coordinator.subscribe_updates(self.entity_id, self._on_updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_updates is not None:
            self._unsub_updates()
            self._unsub_updates = None

    @callback  # type: ignore[untyped-decorator]
    def _on_updated(self) -> None:
        """Push a fresh "unknown" state (EffyCoordinator.notify_updated).

        See EffySensor._on_updated for why "unknown" and not the actual
        computed value.
        """
        self._attr_native_value = None
        self.async_write_ha_state()


class EffyRecalculatedFromSensor(SensorEntity):  # type: ignore[misc]
    """Global timestamp of the earliest slot touched by the most recent
    recalculation (ADR-012).

    Unlike EffySensor/EffyDerivedPowerSensor, this one *is* live-updated
    even with the live path disabled: it subscribes to the coordinator's
    ``subscribe_recalculated_from`` channel, which both the slot timer
    (EffyCoordinator._on_slot_timer) and a manual history rewrite
    (button.py, via ``coordinator.set_recalculated_from``) push to. One
    per config entry — not tied to any single source sensor.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "Recalculated from"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: EffyCoordinator,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_recalculated_from"
        self._attr_native_value: datetime | None = None
        self._unsub_coordinator: Callable[[], None] | None = None

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id or ""

    @property
    def entity_id(self) -> str:
        return "sensor.effy_recalculated_from"

    @entity_id.setter
    def entity_id(self, value: str) -> None:
        pass

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe to the coordinator's recalculated-from channel.

        Seeds from whatever the coordinator already knows in case a
        recalculation happened before this entity finished being added
        (e.g. shortly after an HA restart, while platform setup is still
        in progress).
        """
        self._unsub_coordinator = self._coordinator.subscribe_recalculated_from(
            self._on_recalculated_from
        )
        if self._coordinator.recalculated_from is not None:
            self._attr_native_value = self._coordinator.recalculated_from

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None

    @callback  # type: ignore[untyped-decorator]
    def _on_recalculated_from(self, ts: datetime) -> None:
        self._attr_native_value = ts
        self.async_write_ha_state()
