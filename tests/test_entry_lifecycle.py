"""
Unloading and reloading the config entry (TASK-0028, slice 7).

``__init__.py`` is the module ``test_migration`` loads (Home Assistant and the
coordinator stubbed); only its setup was tested. Here the other half of the
entry's life: the unload that stops the coordinator and the reload that an
options change triggers.
"""

from __future__ import annotations

import asyncio
from typing import Any

from tests.test_migration import _CALLS, _FakeConfigEntries, _FakeEntry, _FakeHass, _init

DOMAIN = "effy"
ENTRY_ID = "entry-1"


class _Coordinator:
    def __init__(self) -> None:
        self.shutdowns = 0

    def async_shutdown(self) -> None:
        _CALLS.append("coordinator.async_shutdown")
        self.shutdowns += 1


class _UnloadingConfigEntries(_FakeConfigEntries):
    def __init__(self, unloads: bool = True) -> None:
        super().__init__()
        self.unloads = unloads
        self.unloaded_platforms: list[list[str]] = []

    async def async_unload_platforms(self, entry: Any, platforms: list[str]) -> bool:
        _CALLS.append("async_unload_platforms")
        self.unloaded_platforms.append(list(platforms))
        return self.unloads


def _hass(coordinator: _Coordinator | None, *, unloads: bool = True) -> Any:
    hass = _FakeHass()
    hass.config_entries = _UnloadingConfigEntries(unloads)
    hass.data[DOMAIN] = {ENTRY_ID: coordinator} if coordinator is not None else {}
    return hass


def _unload(hass: Any) -> bool:
    entry = _FakeEntry({})
    unloaded: bool = asyncio.run(_init.async_unload_entry(hass, entry))
    return unloaded


class TestUnload:
    def test_the_coordinator_is_stopped_then_the_platforms_are_unloaded_then_it_is_forgotten(
        self,
    ) -> None:
        coordinator = _Coordinator()
        hass = _hass(coordinator)
        _CALLS.clear()

        assert _unload(hass) is True

        assert _CALLS == ["coordinator.async_shutdown", "async_unload_platforms"]
        assert coordinator.shutdowns == 1
        assert hass.config_entries.unloaded_platforms == [["sensor", "button"]]
        assert ENTRY_ID not in hass.data[DOMAIN]

    def test_an_entry_without_a_coordinator_still_unloads_its_platforms(self) -> None:
        """Setup may have failed before the coordinator was stored."""
        hass = _hass(None)
        _CALLS.clear()

        assert _unload(hass) is True

        assert _CALLS == ["async_unload_platforms"]
        assert hass.data[DOMAIN] == {}

    def test_a_platform_that_will_not_unload_is_reported_as_not_unloaded(self) -> None:
        hass = _hass(_Coordinator(), unloads=False)
        assert _unload(hass) is False
        assert hass.config_entries.unloaded_platforms == [["sensor", "button"]]


class TestReloadOnOptionsChange:
    def test_the_entry_registers_the_reload_as_its_update_listener(self) -> None:
        listeners: list[Any] = []

        class _Entry(_FakeEntry):
            def add_update_listener(self, listener: Any) -> Any:
                listeners.append(listener)
                return lambda: None

        asyncio.run(_init.async_setup_entry(_FakeHass(), _Entry({"output_sensors": []})))

        assert listeners == [_init._async_reload_entry]

    def test_the_listener_reloads_the_entry_that_changed(self) -> None:
        hass = _FakeHass()
        asyncio.run(_init._async_reload_entry(hass, _FakeEntry({})))
        assert hass.config_entries.reloads == [ENTRY_ID]
