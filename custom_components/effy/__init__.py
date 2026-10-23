"""Effy – Effective PV Loss Distribution integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EffyCoordinator
from .inputs import legacy_inputs_were_moved, migrate_legacy_input_options

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button"]


def _async_migrate_input_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Silently move the old ``input_sensors`` list into ``pv_input_sensors`` (ADR-019 D4).

    No ``VERSION`` bump and no ``async_migrate_entry``: the entry, its ID and
    therefore every unique ID stay as they are. Called at the very start of
    ``async_setup_entry``, *before* the update listener is registered, so the
    write cannot trigger a reload. After the migration every former input is a
    PV input and the results do not change; the BMS sensors are reassigned in
    the options flow.
    """
    migrated = migrate_legacy_input_options(entry.options)
    if migrated is None:
        return
    moved = legacy_inputs_were_moved(entry.options)
    hass.config_entries.async_update_entry(entry, options=migrated)
    if moved:
        _LOGGER.warning(
            "Effy: the input sensors were moved to the PV input list (the inputs are now "
            "configured by role). Open Effy's options and move the BMS sensors to the BMS "
            "input list; until then no BMS input exists and calculated values are unchanged."
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Effy from a config entry.

    Creates one EffyCoordinator per entry and stores it in hass.data so the
    sensor platform can subscribe to it instead of each sensor managing its
    own listeners — see ADR-006 (Option C) for the rationale.
    """
    _async_migrate_input_options(hass, entry)

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
