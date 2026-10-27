"""Effy – Effective PV Loss Distribution integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import DOMAIN
from .coordinator import EffyCoordinator
from .inputs import legacy_inputs_were_moved, migrate_legacy_input_options, output_ids

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button"]


def _async_migrate_input_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Silently move the old ``input_sensors`` list into ``pv_input_sensors`` (ADR-019 D4).

    No ``VERSION`` bump and no ``async_migrate_entry``: the entry, its ID and
    therefore every unique ID stay as they are. Called at the very start of
    ``async_setup_entry``; Effy has no update listener, so the write cannot
    trigger a reload. After the migration every former input is a PV input; the
    four BMS counters are assigned in the options flow, and until then the entry
    refuses to set up (ADR-023 decision 1).
    """
    migrated = migrate_legacy_input_options(entry.options)
    if migrated is None:
        return
    moved = legacy_inputs_were_moved(entry.options)
    hass.config_entries.async_update_entry(entry, options=migrated)
    if moved:
        _LOGGER.warning(
            "Effy: the input sensors were moved to the PV input list (the inputs are now "
            "configured by role). Open Effy's options and assign the BMS counters (grid "
            "import, discharge, charge, grid export); until then Effy does not set up."
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Effy from a config entry.

    Creates one EffyCoordinator per entry and stores it in hass.data so the
    sensor platform can subscribe to it instead of each sensor managing its
    own listeners — see ADR-006 (Option C) for the rationale.

    An entry without a charge or a grid export counter refuses to set up
    (ADR-023 decision 1): with no output a recalculation would write the whole
    input as loss. It creates nothing, schedules nothing and writes nothing; the
    options flow stays reachable and reloads the entry when the human saves the
    counters (``OptionsFlowWithReload``).
    """
    _async_migrate_input_options(hass, entry)
    if not output_ids(entry.options):
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="outputs_not_assigned")

    hass.data.setdefault(DOMAIN, {})

    coordinator = EffyCoordinator(hass, entry)
    coordinator.async_setup()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
