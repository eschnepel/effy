"""
The history of the household consumption energy sensors: the delta derivation on the consumption
power series (TASK-0050, CAP-15, ADR-029 decision 5, ADR-027 decision 5).

The stored consumption rows are the PV tests' effective rows of ``test_delta_derivation`` (an
hour of the day before, two older hours, three 5-minute rows), so the numbers are those of the PV
derivation divided by 1000: the mechanism is one, only its source differs. The REAL ``history.py``
runs against the stateful recorder of ``test_history_write_only_changed``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.test_delta_integrals import _di
from tests.test_history_recent_equivalence import _const, _history, _options
from tests.test_history_write_only_changed import REAL_UNITS, StoredRecorder, _install_ha_api

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
HI_KEY = _const.CONF_HOUSEHOLD_IMPORT_SENSOR
HE_KEY = _const.CONF_HOUSEHOLD_EXPORT_SENSOR

PV = "sensor.pv_a"
OUT = "sensor.out"
HI = "sensor.house_import"
CONSUMPTION = _const.HOUSEHOLD_CONSUMPTION_ENTITY_ID
TODAY = _const.HOUSEHOLD_CONSUMPTION_TODAY_ENTITY_ID
TOTAL = _const.HOUSEHOLD_CONSUMPTION_TOTAL_ENTITY_ID
PV_TODAY = "sensor.effy_pv_a_delta_integral_today"
PV_COMBINED = "sensor.effy_pv_deltasum_integral_today"

MIDNIGHT = datetime(2026, 11, 10, 0, 0, tzinfo=BERLIN)
PREVIOUS = datetime(2026, 11, 9, 0, 0, tzinfo=BERLIN)
# settled_before: 10:10, so the slots at 10:00 and 10:05 are the last completed
NOW = datetime(2026, 11, 10, 10, 12, tzinfo=UTC)
OPTIONS = {**_options([PV], [OUT]), HI_KEY: HI}


def _u(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


class _Recorder(StoredRecorder):
    """The stateful recorder, and the metadata of every import."""

    def __init__(self) -> None:
        super().__init__()
        self.metadata: list[dict[str, Any]] = []

    def async_import_statistics(self, metadata: Any, stats: Any, table: Any) -> None:
        self.metadata.append(dict(metadata))
        super().async_import_statistics(metadata, stats, table)

    def integral_rows(self, sid: str, table: str) -> dict[datetime, dict[str, Any]]:
        return {ts: row for (t, s, ts), row in self.rows.items() if t == table and s == sid}

    def imported_ids(self) -> set[str]:
        return {m["statistic_id"] for m in self.metadata}


def _hass() -> Any:
    return SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Berlin"))


def _store(
    recorder: _Recorder,
    five: dict[datetime, float] | None = None,
    hourly: dict[datetime, float] | None = None,
    statistic_id: str = CONSUMPTION,
) -> None:
    """Rows of the consumption power series, as an earlier run of Effy wrote them (W)."""
    recorder.units[statistic_id] = "W"
    for table, rows in (("5minute", five or {}), ("hour", hourly or {})):
        for ts, mean in rows.items():
            recorder.rows[(table, statistic_id, ts)] = {
                "mean": mean,
                "state": mean,
                "min": None,
                "max": None,
                "sum": None,
                "last_reset": None,
            }


def _world(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    _install_ha_api(monkeypatch, recorder)
    monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
    monkeypatch.setattr(_history, "_get_statistics_units", REAL_UNITS)
    _store(
        recorder,
        five={_u(10, 10): 120.0, _u(10, 10, 5): 120.0, _u(10, 10, 10): 240.0},
        hourly={_u(9, 20): 60.0, _u(10, 8): 600.0, _u(10, 9): 300.0},
    )
    return recorder


async def _derive(options: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    result: dict[str, Any] = await _history.async_derive_household_integrals(
        _hass(), OPTIONS if options is None else options, now=NOW, **kwargs
    )
    return result


class TestTheDerivationWritesTheHistory:
    async def test_the_today_integral_is_written_in_kwh_up_to_the_last_completed_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        short = recorder.integral_rows(TODAY, "5minute")
        # only up to the last completed slot (10:05): the 10:10 slot is the recorder's
        assert set(short) == {_u(10, 10), _u(10, 10, 5)}
        assert short[_u(10, 10)]["state"] == pytest.approx(0.91)
        assert short[_u(10, 10)]["sum"] == pytest.approx(0.97)
        assert short[_u(10, 10)]["last_reset"] == MIDNIGHT
        assert short[_u(10, 10, 5)]["state"] == pytest.approx(0.92)
        assert short[_u(10, 10, 5)]["sum"] == pytest.approx(0.98)
        assert short[_u(10, 10)]["mean"] is None  # a running total has no mean
        assert recorder.units[TODAY] == "kWh"

    async def test_the_total_integral_has_no_last_reset_and_its_state_is_its_running_total(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        short = recorder.integral_rows(TOTAL, "5minute")
        assert short[_u(10, 10, 5)]["state"] == pytest.approx(0.98)
        assert short[_u(10, 10, 5)]["sum"] == pytest.approx(0.98)
        assert {row["last_reset"] for row in short.values()} == {None}
        assert recorder.units[TOTAL] == "kWh"

    async def test_hourly_rows_are_the_closed_hours_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        hourly = recorder.integral_rows(TODAY, "hour")
        # the hour of 10:00 is not closed (the slot of 10:10 is the recorder's): not written
        assert set(hourly) == {_u(9, 20), _u(10, 8), _u(10, 9)}
        assert hourly[_u(9, 20)]["state"] == pytest.approx(0.06)
        assert hourly[_u(9, 20)]["last_reset"] == PREVIOUS
        assert hourly[_u(10, 8)]["state"] == pytest.approx(0.6)  # the day restarts
        assert hourly[_u(10, 8)]["sum"] == pytest.approx(0.66)
        assert hourly[_u(10, 9)]["sum"] == pytest.approx(0.96)

    async def test_the_change_of_an_hour_is_the_energy_of_that_hour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Home Assistant's ``change`` of an hour is the difference of its running totals: it
        must be the energy the household used in that hour (the sum is a running total)."""
        recorder = _world(monkeypatch)
        await _derive()
        hourly = recorder.integral_rows(TOTAL, "hour")
        # the stored hours: 600 W in the hour of 08:00 and 300 W in the hour of 09:00 (UTC)
        assert hourly[_u(10, 9)]["sum"] - hourly[_u(10, 8)]["sum"] == pytest.approx(0.3)
        assert hourly[_u(10, 8)]["sum"] - hourly[_u(9, 20)]["sum"] == pytest.approx(0.6)
        today = recorder.integral_rows(TODAY, "hour")
        assert today[_u(10, 9)]["sum"] - today[_u(10, 8)]["sum"] == pytest.approx(0.3)

    async def test_the_metadata_says_running_total_not_mean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        mine = [m for m in recorder.metadata if m["statistic_id"] in (TODAY, TOTAL)]
        assert mine and all(m["has_sum"] is True and m["has_mean"] is False for m in mine)
        assert {m["unit_of_measurement"] for m in mine} == {"kWh"}

    async def test_it_is_the_energy_of_the_power_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A constant 600 W for an hour is 0.6 kWh: the energy is the power times the time."""
        recorder = _world(monkeypatch)
        for key in [k for k in recorder.rows if k[1] == CONSUMPTION]:
            del recorder.rows[key]
        _store(recorder, five={_u(10, 8, 5 * i): 600.0 for i in range(12)})
        await _history.async_derive_household_integrals(_hass(), OPTIONS, now=_u(10, 9, 1))
        total = recorder.integral_rows(TOTAL, "5minute")
        assert total[_u(10, 8, 55)]["sum"] == pytest.approx(0.6)

    async def test_the_other_integrals_are_not_touched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        assert recorder.imported_ids() == {TODAY, TOTAL}  # no PV integral, no other series


class TestTheDeltaBase:
    async def test_it_is_the_last_written_row_of_each_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world(monkeypatch)
        bases = await _derive()
        assert set(bases) == {TODAY, TOTAL}
        today = bases[TODAY]
        assert (today.state, today.total) == (pytest.approx(0.92), pytest.approx(0.98))
        assert today.last_reset == MIDNIGHT
        total = bases[TOTAL]
        assert (total.state, total.total, total.last_reset) == (
            pytest.approx(0.98),
            pytest.approx(0.98),
            None,
        )

    async def test_with_nothing_to_derive_the_base_is_zero_and_nothing_is_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        _install_ha_api(monkeypatch, recorder)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
        monkeypatch.setattr(_history, "_get_statistics_units", REAL_UNITS)
        bases = await _derive()
        assert set(bases) == {TODAY, TOTAL}
        for base in bases.values():
            assert (base.state, base.total, base.last_reset) == (0.0, 0.0, None)
        assert recorder.imports == []

    async def test_the_base_becomes_the_state_of_the_live_integrals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What ``history.py`` returns is what ``DeltaIntegrals.hand_over`` takes."""
        _world(monkeypatch)
        bases = await _derive()
        integrals = _di.DeltaIntegrals()
        for entity_id, kind in ((TODAY, "today"), (TOTAL, "total")):
            integrals.restore(
                entity_id=entity_id,
                kind=kind,
                source=None,
                series=CONSUMPTION,
                unit="kWh",
                value=None,
                last_reset=None,
                now=NOW,
                tz=BERLIN,
            )
        integrals.hand_over(bases, NOW, BERLIN, units={TODAY: "kWh", TOTAL: "kWh"})
        assert integrals.get(TODAY).state == pytest.approx(0.92)
        assert integrals.get(TOTAL).state == pytest.approx(0.98)


class TestARunOnlyDerivesWhatLacksStatistics:
    async def test_running_it_twice_writes_nothing_the_second_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        imports = len(recorder.imports)
        assert await _derive() == {}
        assert len(recorder.imports) == imports

    async def test_a_series_that_has_statistics_is_skipped_and_the_other_derived(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        for key in [k for k in recorder.rows if k[1] == TOTAL]:
            del recorder.rows[key]
        del recorder.units[TOTAL]
        today_rows = dict(recorder.integral_rows(TODAY, "5minute"))
        bases = await _derive()
        assert set(bases) == {TOTAL}
        assert recorder.integral_rows(TODAY, "5minute") == today_rows

    async def test_a_household_import_is_enough_no_pv_input_is_needed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        options = {**_options([], [OUT], [OUT + "_bms"]), HI_KEY: HI}
        bases = await _derive(options)
        assert set(bases) == {TODAY, TOTAL}
        assert recorder.integral_rows(TODAY, "5minute")

    async def test_without_a_household_import_nothing_is_read_or_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        for options in (
            _options([PV], [OUT]),  # none configured
            {**_options([PV], [OUT]), HE_KEY: "sensor.house_export"},  # an export alone
        ):
            assert await _derive(options) == {}
        assert recorder.imports == [] and recorder.cleared == []

    async def test_the_pv_derivation_does_not_write_the_household_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The two derivations are separate entry points of one mechanism."""
        recorder = _world(monkeypatch)
        _store(recorder, five={_u(10, 10): 120.0}, statistic_id="sensor.effy_pv_a")
        await _history.async_derive_pv_delta_integrals(_hass(), OPTIONS, now=NOW)
        assert recorder.imported_ids() == {
            PV_TODAY,
            PV_COMBINED,
            PV_COMBINED.replace("today", "total"),
        }


class TestTheWindowOfTheDerivation:
    async def test_rows_older_than_the_history_window_are_not_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        old = NOW - timedelta(days=10)
        _store(recorder, hourly={old.replace(minute=0): 1000.0})
        options = {**OPTIONS, _const.CONF_MAX_HISTORY_DAYS: 5}
        bases = await _derive(options)
        # the 1000 W hour of ten days ago is outside five days: the total is what it was
        assert bases[TOTAL].total == pytest.approx(0.98)

    async def test_a_longer_window_reads_them(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _world(monkeypatch)
        old = NOW - timedelta(days=10)
        _store(recorder, hourly={old.replace(minute=0): 1000.0})
        options = {**OPTIONS, _const.CONF_MAX_HISTORY_DAYS: 30}
        bases = await _derive(options)
        assert bases[TOTAL].total == pytest.approx(0.98 + 1.0)  # 1000 W for an hour


class TestRewriteIsTheFormulaChangeCase:
    """Q27 = A for the household sensors: cleared, then derived again from the rows now stored."""

    async def test_both_series_are_cleared_and_written_again_from_the_current_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        before = recorder.integral_rows(TOTAL, "5minute")
        assert before[_u(10, 10, 5)]["sum"] == pytest.approx(0.98)
        # the full recalculation changed the consumption rows: the slots are twice as high
        _store(recorder, five={_u(10, 10): 240.0, _u(10, 10, 5): 240.0, _u(10, 10, 10): 480.0})

        bases = await _derive(rewrite=True)

        assert recorder.cleared == [[TODAY, TOTAL]]
        after = recorder.integral_rows(TOTAL, "5minute")
        assert after[_u(10, 10, 5)]["sum"] == pytest.approx(0.66 + 0.3 + 0.02 + 0.02)
        assert set(bases) == {TODAY, TOTAL}
        assert bases[TOTAL].total == pytest.approx(1.0)

    async def test_no_row_of_the_earlier_composition_is_left(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        # a row the new composition does not have (the slot 10:10 moved out of the history)
        recorder.rows[("5minute", TODAY, _u(10, 7))] = {
            "mean": None,
            "state": 9.0,
            "min": None,
            "max": None,
            "sum": 9.0,
            "last_reset": MIDNIGHT,
        }
        await _derive(rewrite=True)
        assert _u(10, 7) not in recorder.integral_rows(TODAY, "5minute")

    async def test_a_rewrite_with_nothing_stored_clears_and_writes_zero_bases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        _install_ha_api(monkeypatch, recorder)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
        monkeypatch.setattr(_history, "_get_statistics_units", REAL_UNITS)
        bases = await _derive(rewrite=True)
        assert recorder.cleared == [[TODAY, TOTAL]]
        assert recorder.imports == []
        assert {b.total for b in bases.values()} == {0.0}

    async def test_a_read_that_fails_clears_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _world(monkeypatch)

        class _Blind:
            async def statistics_units(self, ids: list[str]) -> dict[str, str]:
                return {statistic_id: "kWh" for statistic_id in ids}

            async def stored_means(self, *args: Any) -> Any:
                raise RuntimeError("cannot read")

        with pytest.raises(RuntimeError):
            await _derive(rewrite=True, storage=_Blind())
        assert recorder.cleared == []  # nothing was cleared before the read succeeded

    async def test_without_a_household_import_a_rewrite_clears_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        assert await _derive(_options([PV], [OUT]), rewrite=True) == {}
        assert recorder.cleared == []


class TestReadingTheBases:
    async def test_the_consumption_integrals_that_exist_have_a_base_and_a_unit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        bases, units = await _history.async_read_delta_bases(_hass(), OPTIONS, now=NOW)
        assert set(bases) == {TODAY, TOTAL}
        assert units == {TODAY: "kWh", TOTAL: "kWh"}
        assert bases[TODAY].state == pytest.approx(0.92)
        assert bases[TOTAL].total == pytest.approx(0.98)
        assert recorder.cleared == []  # reads only

    async def test_with_a_pv_input_too_both_users_are_in_the_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        _store(recorder, five={_u(10, 10): 120.0}, statistic_id="sensor.effy_pv_a")
        await _history.async_derive_pv_delta_integrals(_hass(), OPTIONS, now=NOW)
        await _derive()
        bases, _units = await _history.async_read_delta_bases(_hass(), OPTIONS, now=NOW)
        assert {PV_TODAY, PV_COMBINED, TODAY, TOTAL} <= set(bases)

    async def test_a_household_only_entry_reads_its_bases_without_any_pv_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world(monkeypatch)
        options = {**_options([], [OUT], [OUT + "_bms"]), HI_KEY: HI}
        await _derive(options)
        bases, _units = await _history.async_read_delta_bases(_hass(), options, now=NOW)
        assert set(bases) == {TODAY, TOTAL}

    async def test_without_a_household_import_the_consumption_bases_are_not_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world(monkeypatch)
        await _derive()
        bases, _units = await _history.async_read_delta_bases(
            _hass(), _options([PV], [OUT]), now=NOW
        )
        assert TODAY not in bases and TOTAL not in bases

    async def test_nothing_configured_at_all_reads_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world(monkeypatch)
        options = _options([], [OUT], [OUT + "_bms"])
        assert await _history.async_read_delta_bases(_hass(), options, now=NOW) == ({}, {})
