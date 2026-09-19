"""Effy – Effective PV Loss Distribution integration."""

from __future__ import annotations

import logging

from homeassistant.components.recorder import get_instance as get_recorder
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, ISSUE_RECORDER_NOT_EXCLUDED, RECORDER_EXCLUSION_PROBE_ENTITY_ID
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

    _async_check_recorder_exclusion(hass)

    return True


def _async_check_recorder_exclusion(hass: HomeAssistant) -> None:
    """Warn (via a persistent Repair issue) if effy's own entities are not
    excluded from the recorder (ADR-017).

    history.py writes every ``effy_*`` statistic directly, bypassing the
    recorder's normal state-based compilation (ADR-003's internal
    ``async_import_statistics`` usage). If ``sensor.effy_*`` is *not* also
    excluded from the recorder itself (``recorder: exclude: entity_globs:
    [sensor.effy_*]``), these entities keep behaving like any other
    recorder-tracked sensor with a ``state_class`` — so HA's own native
    statistics compiler independently, redundantly tries to compile the
    very same 5-minute slots from their regular state history, racing
    history.py's direct writes. That race is exactly what produced the
    real-world traceback this ADR is based on: HA core's own
    ``compile_missing_statistics`` raising ``sqlite3.IntegrityError:
    UNIQUE constraint failed: statistics_short_term.metadata_id,
    statistics_short_term.start_ts`` (or the equivalent on other DB
    backends) — entirely outside effy's own code.

    ``entity_filter`` is a pure name-pattern test: it does not require the
    probed entity_id to actually exist, so a synthetic id matching the
    glob is enough — no need to wait for a real effy entity to be created
    first.

    Not a config_flow validation step: recorder exclusion is global HA
    config the user sets independently of (and possibly before) ever
    configuring effy, and effy has no supported way to set it on the
    user's behalf (recorder's include/exclude filters are built once from
    YAML at recorder startup — see ADR-017). A Repairs issue is the
    correct mechanism for "you must fix something outside this
    integration's own config" the same way HA core integrations use it
    for comparable cross-cutting requirements.
    """
    try:
        instance = get_recorder(hass)
        would_record = instance.entity_filter(RECORDER_EXCLUSION_PROBE_ENTITY_ID)
    except Exception:
        # Defensive: matches history.py's own defensive stance toward
        # recorder-internals calls (ADR-003) — entity_filter is public but
        # still an attribute of the internal Recorder instance, not a
        # documented, versioned contract. Never let this diagnostic check
        # itself break setup.
        _LOGGER.debug("Effy: could not check recorder entity_filter", exc_info=True)
        return

    if would_record:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_RECORDER_NOT_EXCLUDED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_RECORDER_NOT_EXCLUDED,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_RECORDER_NOT_EXCLUDED)


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
