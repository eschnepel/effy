"""Sensor platform for Effy – one effective sensor per input sensor.

Sensors hold no listeners of their own; they subscribe to the shared
EffyCoordinator and receive computed results via push (ADR-006 Option C).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .calculation import LossDistribution, effective_in_original_unit
from .const import CONF_INPUT_SENSORS, DOMAIN
from .coordinator import EffyCoordinator
from .sensor_utils import effective_unit_for


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Effy sensor entities."""
    coordinator: EffyCoordinator = hass.data[DOMAIN][entry.entry_id]
    input_ids: list[str] = entry.options.get(CONF_INPUT_SENSORS, [])

    entities = [EffySensor(hass, entry, coordinator, entity_id) for entity_id in input_ids]
    async_add_entities(entities, update_before_add=False)

    # Trigger one immediate recalculation so sensors have values on first load
    coordinator.force_refresh()


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
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Effy",
            manufacturer="Effy",
            model="PV Loss Distributor",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates (ADR-006 Option C)."""
        self._unsub_coordinator = self._coordinator.subscribe(
            self._source_entity_id, self._on_distribution
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from coordinator."""
        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None

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
