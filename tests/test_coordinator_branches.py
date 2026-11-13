"""Realistic branches of ``coordinator.py`` the other coordinator tests leave out.

Runs the real ``coordinator.py`` against the fakes of ``test_coordinator_slot`` (Home Assistant
stubbed, the history functions replaced by recorders).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_coordinator_slot import (
    EffyCoordinator,
    _coord_mod,
    _FakeEntry,
    _FakeHass,
    _history_full_return_value,
    _reset_slot_job_fakes,
)

PV = "sensor.pv"
TODAY = "sensor.effy_pv_delta_integral_today"
MIDNIGHT = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
RECALCULATED_FROM = datetime(2026, 11, 10, 11, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Base:
    state: float
    last_reset: datetime | None
    total: float = 0.0


def _coordinator() -> EffyCoordinator:
    _reset_slot_job_fakes()
    coord = EffyCoordinator(_FakeHass(), _FakeEntry([PV], ["sensor.export"]))
    coord._now = lambda: MIDNIGHT  # type: ignore[method-assign]
    return coord


class TestAReplacedSubscriptionIsNotUnsubscribedByTheOldOne:
    def test_the_old_unsubscribe_leaves_the_new_subscriber_in_place(self) -> None:
        coord = _coordinator()
        old: list[float | None] = []
        new: list[float | None] = []
        unsubscribe_old = coord.subscribe_delta_integral(TODAY, lambda i: old.append(i.state))
        unsubscribe_new = coord.subscribe_delta_integral(TODAY, lambda i: new.append(i.state))
        coord.restore_delta_integral(TODAY, "today", PV, "Wh", None, None)

        unsubscribe_old()  # the sensor that was replaced goes away late
        coord.hand_over_delta_bases({TODAY: _Base(4.0, MIDNIGHT)})
        assert new == [4.0] and old == []

        unsubscribe_old()  # and again: still no effect
        unsubscribe_new()
        coord.hand_over_delta_bases({TODAY: _Base(5.0, MIDNIGHT)})
        assert new == [4.0]


class _Integration:
    version = "1.2.3"


class TestTheExportMeta:
    @pytest.fixture
    def home_assistant(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """``homeassistant.const.__version__`` and ``homeassistant.loader`` as at run time."""
        looked_up: list[str] = []

        async def async_get_integration(hass: Any, domain: str) -> _Integration:
            looked_up.append(domain)
            return _Integration()

        loader = ModuleType("homeassistant.loader")
        loader.async_get_integration = async_get_integration  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "homeassistant.loader", loader)
        monkeypatch.setattr(
            sys.modules["homeassistant.const"], "__version__", "2026.11.0", raising=False
        )
        return looked_up

    @pytest.mark.asyncio
    async def test_it_names_both_versions_when_home_assistant_can_be_asked(
        self, home_assistant: list[str]
    ) -> None:
        meta = await _coordinator()._export_meta()
        assert meta["ha_version"] == "2026.11.0"
        assert meta["effy_version"] == "1.2.3"
        assert meta["time_zone"] == "Europe/Berlin"
        assert meta["written_at"] == MIDNIGHT.isoformat()
        assert home_assistant == [_coord_mod.DOMAIN]

    @pytest.mark.asyncio
    async def test_an_integration_that_cannot_be_found_leaves_only_its_own_version_unknown(
        self, monkeypatch: pytest.MonkeyPatch, home_assistant: list[str]
    ) -> None:
        async def missing(hass: Any, domain: str) -> Any:
            raise LookupError(domain)

        monkeypatch.setattr(sys.modules["homeassistant.loader"], "async_get_integration", missing)
        meta = await _coordinator()._export_meta()
        assert meta["ha_version"] == "2026.11.0"
        assert meta["effy_version"] == "unknown"

    @pytest.mark.asyncio
    async def test_the_file_of_a_run_carries_the_versions(
        self, home_assistant: list[str], tmp_path: Path
    ) -> None:
        import effy.debug_export as debug_export

        coord = _coordinator()
        coord._entry.options["debug_export"] = True
        coord._hass.config.directory = str(tmp_path)
        await coord.async_recalculate_history_and_report()
        [path] = (tmp_path / "effy_debug").glob("effy-export-*.json")
        meta = debug_export.loads(path.read_bytes())["meta"]
        assert (meta["ha_version"], meta["effy_version"]) == ("2026.11.0", "1.2.3")


class TestAFailingDebugExportDoesNotAffectTheRecalculation:
    @pytest.mark.asyncio
    async def test_it_is_logged_and_the_run_still_reports_and_closes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        coord = _coordinator()
        coord._entry.options["debug_export"] = True
        coord._hass.config.directory = str(tmp_path)
        _history_full_return_value[0] = (7, RECALCULATED_FROM, set(), {})

        def broken(*args: Any, **kwargs: Any) -> Any:
            raise OSError("disk full")

        monkeypatch.setattr(_coord_mod, "export_to_file", broken)
        with caplog.at_level(logging.INFO, logger=_coord_mod.__name__):
            written = await coord.async_recalculate_history_and_report()

        assert written == 7
        assert coord.recalculated_from == RECALCULATED_FROM  # the closing step still ran
        assert coord._pending_export is None
        assert not list(tmp_path.rglob("effy-export-*.json"))
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "debug export failed" in message and "OSError: disk full" in message
        assert "recalculation is not affected" in message
        assert not [r for r in caplog.records if "KiB" in r.getMessage()]

    @pytest.mark.asyncio
    async def test_a_cancellation_is_not_swallowed_as_an_export_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        import effy.debug_export as debug_export

        coord = _coordinator()
        coord._hass.config.directory = str(tmp_path)
        coord._pending_export = debug_export.ExportRecord()

        def cancelled(*args: Any, **kwargs: Any) -> Any:
            raise asyncio.CancelledError

        monkeypatch.setattr(_coord_mod, "export_to_file", cancelled)
        with caplog.at_level(logging.INFO, logger=_coord_mod.__name__):
            with pytest.raises(asyncio.CancelledError):
                await coord._async_write_pending_export()
        assert coord._pending_export is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
