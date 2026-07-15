"""Button platform for Effy – diagnostic history recalculation trigger."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EffyCoordinator
from .history import async_recalculate_history

_LOGGER = logging.getLogger(__name__)

RECALCULATE_BUTTON = ButtonEntityDescription(
    key="recalculate_history",
    name="Re-calculate History",
    icon="mdi:history",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Effy diagnostic button."""
    async_add_entities(
        [EffyRecalculateButton(hass, entry, RECALCULATE_BUTTON)],
        update_before_add=False,
    )


class EffyRecalculateButton(ButtonEntity):  # type: ignore[misc]
    """Button that triggers a full history recalculation.

    Each press overwrites existing statistics for the configured history
    window (ADR-004) — intentional, e.g. after a sensor list change.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: ButtonEntityDescription,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_recalculate_history"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Effy",
            manufacturer="Effy",
            model="PV Loss Distributor",
        )

    async def async_press(self) -> None:
        """Trigger history recalculation.

        Also reports the recalculated range's start to the coordinator's
        "recalculated from" tracking (ADR-012) — a manual rewrite always
        recomputes every slot in the full configured window
        unconditionally, so that window's own start is, by definition,
        the earliest touched slot. Additionally pushes a fresh "unknown"
        state to every touched entity (EffyCoordinator.notify_updated)
        so dashboard cards that only refresh on state_changed pick up the
        rewritten statistics, since these entities otherwise never get a
        live push while the live path is disabled (ADR-011).
        """
        _LOGGER.info("Effy: starting history recalculation (triggered by button)")
        try:
            coordinator: EffyCoordinator = self.hass.data[DOMAIN][self._entry.entry_id]
            written, recalculated_from, touched = await async_recalculate_history(
                self.hass,
                self._entry.options,
                energy_reading_cache=coordinator.last_valid_energy_readings,
            )
            _LOGGER.info("Effy: history recalculation complete – %d slots written", written)
            if recalculated_from is not None:
                coordinator.set_recalculated_from(recalculated_from)
            if touched:
                coordinator.notify_updated(touched)
        except Exception:
            # Broad catch is intentional: this runs outside a request/response
            # cycle (button press has no caller to propagate to) — log and
            # swallow rather than crash the entity (ADR-000 §8).
            _LOGGER.exception("Effy: history recalculation failed")
