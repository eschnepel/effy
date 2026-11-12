"""
The base of an integral that has statistics, and the combined integrals derived again
(TASK-0045e, CAP-13, ADR-027 decisions 6 and 9, Q27 = A).

Two things the implicit full recalculation needs besides the derivation of TASK-0045c:

- ``async_read_delta_bases``: a PV input that is removed and added again still has statistics;
  nothing is derived and its base is its last stored row (``state``, ``last_reset``);
- ``async_rederive_pv_combined``: when the PV inputs change, the combined integrals are the
  per-slot addition of the per-input statistics of the configured inputs, written as a one-time
  rewrite; an input that is removed leaves the combined history.

Both run the REAL ``history.py`` against the stateful recorder of
``test_history_write_only_changed``, which keeps every column of a row and the metadata flags.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.test_history_recent_equivalence import _history, _options
from tests.test_history_write_only_changed import (
    REAL_UNITS,
    StoredRecorder,
    _install_ha_api,
)

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
PV_A = "sensor.pv_a"
PV_B = "sensor.pv_b"
PV_C = "sensor.pv_c"
OUT = "sensor.out"
TODAY_A = "sensor.effy_pv_a_delta_integral_today"
TODAY_B = "sensor.effy_pv_b_delta_integral_today"
TODAY_C = "sensor.effy_pv_c_delta_integral_today"
COMBINED_TODAY = "sensor.effy_pv_deltasum_integral_today"
COMBINED_TOTAL = "sensor.effy_pv_deltasum_integral_total"
MIDNIGHT = datetime(2026, 11, 10, 0, 0, tzinfo=BERLIN)
NOW = datetime(2026, 11, 10, 10, 32, tzinfo=UTC)


def _u(hour: int, minute: int = 0, day: int = 10) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def _hass() -> Any:
    return SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Berlin"))


def _world(monkeypatch: pytest.MonkeyPatch) -> StoredRecorder:
    recorder = StoredRecorder()
    _install_ha_api(monkeypatch, recorder)
    monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
    monkeypatch.setattr(_history, "_get_statistics_units", REAL_UNITS)
    return recorder


def _put(
    recorder: StoredRecorder,
    statistic_id: str,
    unit: str,
    five: dict[datetime, float] | None = None,
    hourly: dict[datetime, float] | None = None,
    *,
    today: bool = True,
) -> None:
    """Rows of a running-total series (``sum`` given; ``state`` the day's energy, or ``sum``)."""
    recorder.units[statistic_id] = unit
    recorder.flags[statistic_id] = {"has_mean": False, "has_sum": True}
    for table, rows in (("5minute", five or {}), ("hour", hourly or {})):
        for ts, total in rows.items():
            recorder.rows[(table, statistic_id, ts)] = {
                "mean": None,
                "state": total - 1000.0 if today else total,
                "min": None,
                "max": None,
                "sum": total,
                "last_reset": MIDNIGHT if today else None,
            }


def _combined(recorder: StoredRecorder, statistic_id: str) -> dict[datetime, dict[str, Any]]:
    return recorder.short(statistic_id)


# ---------------------------------------------------------------------------
# The base of a series that has statistics
# ---------------------------------------------------------------------------


class TestReadDeltaBases:
    async def test_the_base_is_the_last_stored_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, TODAY_A, "Wh", five={_u(10, 0): 100.0, _u(10, 5): 130.0, _u(10, 10): 170.0})
        bases, units = await _history.async_read_delta_bases(
            _hass(), _options([PV_A], [OUT]), now=NOW
        )
        assert bases[TODAY_A].state == 170.0 - 1000.0
        assert bases[TODAY_A].total == 170.0
        assert bases[TODAY_A].last_reset == MIDNIGHT
        assert units == {TODAY_A: "Wh"}

    async def test_the_total_has_no_last_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, COMBINED_TOTAL, "kWh", five={_u(10, 0): 7.0, _u(10, 5): 7.5}, today=False)
        bases, _ = await _history.async_read_delta_bases(_hass(), _options([PV_A], [OUT]), now=NOW)
        assert (bases[COMBINED_TOTAL].state, bases[COMBINED_TOTAL].last_reset) == (7.5, None)
        assert bases[COMBINED_TOTAL].total == 7.5

    async def test_a_series_with_only_hourly_rows_takes_the_last_hour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, TODAY_A, "Wh", hourly={_u(7): 100.0, _u(8): 150.0})
        bases, _ = await _history.async_read_delta_bases(_hass(), _options([PV_A], [OUT]), now=NOW)
        assert bases[TODAY_A].total == 150.0

    async def test_the_later_of_the_two_tables_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _world(monkeypatch)
        # the hour 09:00 ends at 10:00; the 5-minute row at 10:05 is later
        _put(recorder, TODAY_A, "Wh", five={_u(10, 5): 130.0}, hourly={_u(9): 120.0})
        bases, _ = await _history.async_read_delta_bases(_hass(), _options([PV_A], [OUT]), now=NOW)
        assert bases[TODAY_A].total == 130.0

    async def test_every_series_that_has_statistics_has_a_base_and_the_others_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, TODAY_A, "Wh", five={_u(10, 0): 1.0})
        _put(recorder, TODAY_B, "kWh", five={_u(10, 0): 2.0})
        _put(recorder, COMBINED_TODAY, "kWh", five={_u(10, 0): 3.0})
        bases, units = await _history.async_read_delta_bases(
            _hass(), _options([PV_A, PV_B, PV_C], [OUT]), now=NOW
        )
        assert set(bases) == {TODAY_A, TODAY_B, COMBINED_TODAY}  # not C, not the total
        assert units == {TODAY_A: "Wh", TODAY_B: "kWh", COMBINED_TODAY: "kWh"}

    async def test_a_removed_input_is_not_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, TODAY_A, "Wh", five={_u(10, 0): 1.0})
        _put(recorder, TODAY_C, "Wh", five={_u(10, 0): 1.0})
        bases, _ = await _history.async_read_delta_bases(_hass(), _options([PV_A], [OUT]), now=NOW)
        assert set(bases) == {TODAY_A}

    async def test_rows_of_old_days_are_read_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An input removed for weeks: its last row is old, and it is still its base."""
        recorder = _world(monkeypatch)
        _put(recorder, TODAY_A, "Wh", hourly={_u(8, day=1): 55.0})
        bases, _ = await _history.async_read_delta_bases(_hass(), _options([PV_A], [OUT]), now=NOW)
        assert bases[TODAY_A].total == 55.0

    async def test_without_a_pv_input_there_is_nothing_to_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, COMBINED_TOTAL, "kWh", five={_u(10, 0): 7.0}, today=False)
        assert await _history.async_read_delta_bases(_hass(), _options([], [OUT]), now=NOW) == (
            {},
            {},
        )

    async def test_a_series_with_metadata_but_no_rows_has_no_base(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        recorder.units[TODAY_A] = "Wh"
        bases, units = await _history.async_read_delta_bases(
            _hass(), _options([PV_A], [OUT]), now=NOW
        )
        assert bases == {} and units == {}


# ---------------------------------------------------------------------------
# The combined integrals derived again
# ---------------------------------------------------------------------------


def _inputs(recorder: StoredRecorder) -> None:
    """A (Wh) and B (kWh) with 5-minute totals; the steps after the first row are 30 and 40 Wh
    for A, 0.5 and 0.5 kWh for B."""
    _put(recorder, TODAY_A, "Wh", five={_u(10, 0): 100.0, _u(10, 5): 130.0, _u(10, 10): 170.0})
    _put(recorder, TODAY_B, "kWh", five={_u(10, 0): 1.0, _u(10, 5): 1.5, _u(10, 10): 2.0})


class TestRederiveCombined:
    async def test_the_settled_rule_of_the_derivation_applies(self) -> None:
        # NOW (10:32) settles the slots up to 10:25: every row of the tests below is written
        assert _history.settled_before(NOW) >= _u(10, 15)

    async def test_the_combined_total_is_the_addition_of_the_per_input_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _inputs(recorder)
        bases, units = await _history.async_rederive_pv_combined(
            _hass(), _options([PV_A, PV_B], [OUT]), now=NOW
        )
        total = _combined(recorder, COMBINED_TOTAL)
        # 10:05: 30 Wh + 0.5 kWh; 10:10: 40 Wh + 0.5 kWh; in kWh
        assert total[_u(10, 5)]["sum"] == pytest.approx(0.53)
        assert total[_u(10, 10)]["sum"] == pytest.approx(0.53 + 0.54)
        assert total[_u(10, 10)]["state"] == pytest.approx(0.53 + 0.54)  # state = running total
        assert total[_u(10, 10)]["last_reset"] is None
        assert units == {COMBINED_TODAY: "kWh", COMBINED_TOTAL: "kWh"}
        assert bases[COMBINED_TOTAL].state == pytest.approx(1.07)
        assert bases[COMBINED_TOTAL].last_reset is None

    async def test_the_combined_today_is_the_energy_since_the_local_midnight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _inputs(recorder)
        bases, _ = await _history.async_rederive_pv_combined(
            _hass(), _options([PV_A, PV_B], [OUT]), now=NOW
        )
        today = _combined(recorder, COMBINED_TODAY)
        assert today[_u(10, 5)]["state"] == pytest.approx(0.53)
        assert today[_u(10, 10)]["state"] == pytest.approx(1.07)
        assert today[_u(10, 10)]["sum"] == pytest.approx(1.07)
        assert today[_u(10, 10)]["last_reset"] == MIDNIGHT
        assert bases[COMBINED_TODAY].last_reset == MIDNIGHT
        assert bases[COMBINED_TODAY].state == pytest.approx(1.07)

    async def test_the_old_combined_history_is_replaced_not_added_to(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Q27 = A: an input that was removed leaves the combined history. The rows written for
        the earlier composition, whatever their time, are gone."""
        recorder = _world(monkeypatch)
        _inputs(recorder)
        # the old composition had a third input: its totals are far above
        _put(
            recorder, COMBINED_TOTAL, "kWh", five={_u(9, 55): 900.0, _u(10, 5): 950.0}, today=False
        )
        _put(recorder, COMBINED_TODAY, "kWh", five={_u(9, 55): 40.0, _u(10, 5): 45.0})
        await _history.async_rederive_pv_combined(_hass(), _options([PV_A, PV_B], [OUT]), now=NOW)
        assert recorder.cleared == [[COMBINED_TODAY, COMBINED_TOTAL]]
        total = _combined(recorder, COMBINED_TOTAL)
        assert _u(9, 55) not in total
        assert total[_u(10, 5)]["sum"] == pytest.approx(0.53)  # not 950

    async def test_a_removed_input_leaves_the_combined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _inputs(recorder)  # B stays in the statistics, but is no longer configured
        await _history.async_rederive_pv_combined(_hass(), _options([PV_A], [OUT]), now=NOW)
        total = _combined(recorder, COMBINED_TOTAL)
        assert total[_u(10, 10)]["sum"] == pytest.approx((30.0 + 40.0) / 1000)

    async def test_an_input_without_statistics_adds_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _inputs(recorder)
        await _history.async_rederive_pv_combined(
            _hass(), _options([PV_A, PV_B, PV_C], [OUT]), now=NOW
        )
        total = _combined(recorder, COMBINED_TOTAL)
        assert total[_u(10, 10)]["sum"] == pytest.approx(1.07)

    async def test_the_metadata_is_a_running_total_in_kwh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _inputs(recorder)
        await _history.async_rederive_pv_combined(_hass(), _options([PV_A, PV_B], [OUT]), now=NOW)
        for statistic_id in (COMBINED_TODAY, COMBINED_TOTAL):
            assert recorder.units[statistic_id] == "kWh"
            assert recorder.flags[statistic_id] == {"has_mean": False, "has_sum": True}

    async def test_the_hourly_table_is_written_for_closed_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, TODAY_A, "Wh", hourly={_u(6): 10.0, _u(7): 40.0, _u(8): 100.0})
        await _history.async_rederive_pv_combined(_hass(), _options([PV_A], [OUT]), now=NOW)
        hourly = recorder.hourly(COMBINED_TOTAL)
        assert hourly[_u(7)]["sum"] == pytest.approx(0.03)
        assert hourly[_u(8)]["sum"] == pytest.approx(0.09)
        assert _u(6) not in hourly  # the first row only fixed the starting point

    async def test_rows_that_are_not_settled_yet_are_not_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _put(
            recorder,
            TODAY_A,
            "Wh",
            five={_u(10, 20): 100.0, _u(10, 25): 110.0, _u(10, 30): 120.0},
        )
        early = _u(10, 12)  # settled_before: 10:10
        await _history.async_rederive_pv_combined(_hass(), _options([PV_A], [OUT]), now=early)
        assert _combined(recorder, COMBINED_TOTAL) == {}

    async def test_the_two_series_are_written_in_both_tables_in_one_go(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _inputs(recorder)
        await _history.async_rederive_pv_combined(_hass(), _options([PV_A, PV_B], [OUT]), now=NOW)
        written = {(table, sid) for table, sid, _rows in recorder.imports}
        assert ("5minute", COMBINED_TODAY) in written and ("5minute", COMBINED_TOTAL) in written

    async def test_without_a_pv_input_nothing_is_cleared_or_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, COMBINED_TOTAL, "kWh", five={_u(10, 0): 7.0}, today=False)
        assert await _history.async_rederive_pv_combined(_hass(), _options([], [OUT]), now=NOW) == (
            {},
            {},
        )
        assert recorder.cleared == [] and recorder.imports == []

    async def test_nothing_to_derive_starts_at_zero_and_clears_the_old_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _put(recorder, COMBINED_TOTAL, "kWh", five={_u(10, 0): 7.0}, today=False)
        bases, _ = await _history.async_rederive_pv_combined(
            _hass(), _options([PV_A], [OUT]), now=NOW
        )
        assert recorder.cleared == [[COMBINED_TODAY, COMBINED_TOTAL]]
        assert (bases[COMBINED_TOTAL].state, bases[COMBINED_TOTAL].total) == (0.0, 0.0)
        assert bases[COMBINED_TOTAL].last_reset is None

    async def test_the_rewrite_is_read_through_the_storage_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-025: a read of the recorder goes through ``Storage``. A storage that cannot say
        raises, and so does the rewrite: it never reaches past it."""
        recorder = _world(monkeypatch)
        _inputs(recorder)

        class _Blind:
            async def statistics_units(self, ids: list[str]) -> dict[str, str]:
                return {statistic_id: "Wh" for statistic_id in ids}

            async def stored_integral(self, *args: Any) -> Any:
                raise RuntimeError("cannot read")

        with pytest.raises(RuntimeError):
            await _history.async_rederive_pv_combined(
                _hass(), _options([PV_A], [OUT]), now=NOW, storage=_Blind()
            )
        assert recorder.cleared == []  # nothing was cleared before the read succeeded


class TestTheBasesFitTheLiveIntegrals:
    """What ``history.py`` returns is what ``DeltaIntegrals.hand_over`` takes (no fake between)."""

    async def test_a_rewritten_combined_base_becomes_the_state_of_the_sensors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.test_delta_integrals import _di

        recorder = _world(monkeypatch)
        _inputs(recorder)
        bases, units = await _history.async_rederive_pv_combined(
            _hass(), _options([PV_A, PV_B], [OUT]), now=NOW
        )
        integrals = _di.DeltaIntegrals()
        for entity_id, kind in ((COMBINED_TODAY, "today"), (COMBINED_TOTAL, "total")):
            integrals.restore(
                entity_id=entity_id,
                kind=kind,
                source=None,
                unit="kWh",
                value=None,
                last_reset=None,
                now=NOW,
                tz=BERLIN,
            )
        integrals.hand_over(bases, NOW, BERLIN, units=units)
        total = integrals.get(COMBINED_TOTAL)
        today = integrals.get(COMBINED_TODAY)
        assert total is not None and today is not None
        assert total.state == pytest.approx(1.07) and total.last_reset is None
        assert today.state == pytest.approx(1.07) and today.last_reset == MIDNIGHT

    async def test_the_last_stored_row_becomes_the_state_of_a_waiting_sensor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.test_delta_integrals import _di

        recorder = _world(monkeypatch)
        _put(recorder, TODAY_A, "Wh", five={_u(10, 0): 100.0, _u(10, 5): 130.0})
        bases, units = await _history.async_read_delta_bases(
            _hass(), _options([PV_A], [OUT]), now=NOW
        )
        integrals = _di.DeltaIntegrals()
        integrals.restore(
            entity_id=TODAY_A,
            kind="today",
            source=PV_A,
            unit="kWh",
            value=None,
            last_reset=None,
            now=NOW,
            tz=BERLIN,
        )
        integrals.hand_over(bases, NOW, BERLIN, units=units)
        integral = integrals.get(TODAY_A)
        assert integral is not None
        assert (integral.state, integral.unit, integral.last_reset) == (
            130.0 - 1000.0,
            "Wh",
            MIDNIGHT,
        )
        integrals.book({PV_A: (20.0, "Wh")})  # the gap the recalculation books
        assert integral.state == pytest.approx(130.0 - 1000.0 + 20.0)
