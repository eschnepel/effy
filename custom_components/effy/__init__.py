"""Effy – Effective PV Loss Distribution integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EffyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Effy from a config entry.

    Creates one EffyCoordinator per entry and stores it in hass.data so the
    sensor platform can subscribe to it instead of each sensor managing its
    own listeners — see ADR-006 (Option C) for the rationale.
    """
    hass.data.setdefault(DOMAIN, {})

    coordinator = EffyCoordinator(hass, entry)
    coordinator.async_setup()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: EffyCoordinator | None = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        coordinator.async_shutdown()

    unloaded: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    _LOGGER.debug("Effy: options changed – reloading entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
