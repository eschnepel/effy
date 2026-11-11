"""
The delta derivation: the history of the PV delta integrals, and the first has_sum writer
(TASK-0045c, CAP-13, ADR-027 decision 5).

Effy derives, once, the statistics history of the PV delta integrals from the stored effective
rows and writes it as running-total rows; it returns the delta base from which the live sensors
continue. Nothing calls it yet.

The pure functions run without a fake. The reader, the writer and the orchestration run the REAL
``history.py`` against the stateful recorder of ``test_history_write_only_changed``, which keeps
every column of a row, including ``sum`` and ``last_reset``, and the metadata flags of the last
import, so the tests read back what was written.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.test_history_recent_equivalence import _const, _history, _options
from tests.test_history_write_only_changed import (
    REAL_UNITS,
    StoredRecorder,
    _install_ha_api,
    _rig,
)

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
PV_A = "sensor.pv_a"  # effective series in W
PV_B = "sensor.pv_b"  # effective series in kW
OUT = "sensor.out"
EFF_A = "sensor.effy_pv_a"
EFF_B = "sensor.effy_pv_b"
TODAY_A = "sensor.effy_pv_a_delta_integral_today"
TODAY_B = "sensor.effy_pv_b_delta_integral_today"
COMBINED_TODAY = "sensor.effy_pv_deltasum_integral_today"
COMBINED_TOTAL = "sensor.effy_pv_deltasum_integral_total"
SLOT = timedelta(minutes=5)
HOUR = timedelta(hours=1)


def _u(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def _step(start: datetime, energy: float, short: bool = True) -> Any:
    return _history.IntegralStep(start, energy, short)


# ---------------------------------------------------------------------------
# The rows of one integral
# ---------------------------------------------------------------------------


class TestIntegralRows:
    def test_the_example_of_the_task(self) -> None:
        # slots of 25, 30 and 20 Wh after the local midnight, then the next day
        midnight = datetime(2026, 11, 10, 0, 0, tzinfo=BERLIN)
        steps = [
            _step(midnight + SLOT, 25.0),
            _step(midnight + 2 * SLOT, 30.0),
            _step(midnight + 3 * SLOT, 20.0),
            _step(midnight + timedelta(days=1) + SLOT, 7.0),
        ]
        rows = _history.integral_rows(steps, BERLIN, today=True)
        assert [r.state for r in rows] == pytest.approx([25.0, 55.0, 75.0, 7.0])
        assert [r.total for r in rows] == pytest.approx([25.0, 55.0, 75.0, 82.0])
        assert [r.last_reset for r in rows[:3]] == [midnight] * 3
        assert rows[3].last_reset == midnight + timedelta(days=1)
        assert [r.start for r in rows] == [s.start for s in steps]

    def test_the_local_midnight_is_the_one_of_the_rows_day_not_utc(self) -> None:
        # 23:30 UTC on the 9th is 00:30 on the 10th in Berlin (UTC+1 in November)
        rows = _history.integral_rows([_step(_u(9, 23, 30), 10.0)], BERLIN, today=True)
        assert rows[0].last_reset == datetime(2026, 11, 10, 0, 0, tzinfo=BERLIN)
        # and 22:55 UTC is still the 9th
        rows = _history.integral_rows([_step(_u(9, 22, 55), 10.0)], BERLIN, today=True)
        assert rows[0].last_reset == datetime(2026, 11, 9, 0, 0, tzinfo=BERLIN)

    @pytest.mark.parametrize(
        ("day", "hours"),
        [(datetime(2026, 3, 29, tzinfo=BERLIN), 23), (datetime(2026, 10, 25, tzinfo=BERLIN), 25)],
    )
    def test_a_day_of_23_or_25_hours_is_one_day(self, day: datetime, hours: int) -> None:
        start = day.astimezone(UTC)
        following = datetime.combine(
            (day + timedelta(days=1)).date(), datetime.min.time(), tzinfo=BERLIN
        ).astimezone(UTC)
        assert (following - start) == timedelta(hours=hours)
        steps = [_step(start + HOUR * i, 1.0, short=False) for i in range(hours)]
        steps.append(_step(following, 1.0, short=False))  # the first hour of the next day
        rows = _history.integral_rows(steps, BERLIN, today=True)
        assert rows[hours - 1].state == pytest.approx(float(hours))  # the whole day, not 24 h
        assert {r.last_reset for r in rows[:hours]} == {start}
        assert rows[hours].state == pytest.approx(1.0)  # the next day restarts
        assert rows[hours].last_reset == following
        assert rows[hours].total == pytest.approx(hours + 1.0)

    def test_the_total_has_no_last_reset_and_its_state_is_its_running_total(self) -> None:
        steps = [_step(_u(10, 0, 5), 25.0), _step(_u(11, 12, 0), 5.0)]
        rows = _history.integral_rows(steps, BERLIN, today=False)
        assert [r.state for r in rows] == pytest.approx([25.0, 30.0])
        assert [r.total for r in rows] == pytest.approx([25.0, 30.0])
        assert [r.last_reset for r in rows] == [None, None]

    def test_no_steps_no_rows(self) -> None:
        assert _history.integral_rows([], BERLIN, today=True) == []

    def test_the_rows_keep_whether_a_5_minute_row_exists(self) -> None:
        rows = _history.integral_rows(
            [_step(_u(10, 8), 1.0, short=False), _step(_u(10, 9), 1.0)], BERLIN, today=True
        )
        assert [r.short for r in rows] == [False, True]


# ---------------------------------------------------------------------------
# The energy of a slot, from the stored rows
# ---------------------------------------------------------------------------


class TestSlotEnergySteps:
    def test_a_5_minute_row_is_its_power_times_5_over_60(self) -> None:
        steps = _history.slot_energy_steps({_u(10, 10): 120.0, _u(10, 10, 5): 60.0}, {})
        assert [(s.start, s.energy, s.short) for s in steps] == [
            (_u(10, 10), pytest.approx(10.0), True),
            (_u(10, 10, 5), pytest.approx(5.0), True),
        ]

    def test_an_hour_older_than_the_5_minute_rows_is_its_mean_times_one_hour(self) -> None:
        steps = _history.slot_energy_steps(
            {_u(10, 10): 120.0}, {_u(10, 8): 600.0, _u(10, 9): 300.0}
        )
        assert [(s.start, s.energy, s.short) for s in steps] == [
            (_u(10, 8), pytest.approx(600.0), False),
            (_u(10, 9), pytest.approx(300.0), False),
            (_u(10, 10), pytest.approx(10.0), True),
        ]

    def test_the_hour_of_the_oldest_5_minute_row_keeps_the_energy_of_its_missing_slots(
        self,
    ) -> None:
        """The recorder purges 5-minute rows by time, so the oldest hour is often half there.
        Its hourly row holds the whole hour: what the 5-minute rows lack is carried, the hour
        is not counted twice, and the energy of the hour is conserved."""
        five = {_u(10, 10, 30 + 5 * i): 120.0 for i in range(6)}  # the second half hour: 60 Wh
        steps = _history.slot_energy_steps(five, {_u(10, 10): 120.0})  # the hour: 120 Wh
        assert [(s.start, s.short) for s in steps[:2]] == [
            (_u(10, 10), False),
            (_u(10, 10, 30), True),
        ]
        assert steps[0].energy == pytest.approx(60.0)  # 120 Wh of the hour minus the 60 Wh seen
        assert sum(s.energy for s in steps) == pytest.approx(120.0)

    def test_a_hour_with_all_its_5_minute_rows_is_not_counted_again(self) -> None:
        five = {_u(10, 10, 5 * i): 120.0 for i in range(12)}
        steps = _history.slot_energy_steps(five, {_u(10, 10): 120.0})
        assert len(steps) == 12 and all(s.short for s in steps)
        assert sum(s.energy for s in steps) == pytest.approx(120.0)

    def test_a_hour_after_the_oldest_5_minute_row_without_one_is_not_made_up(self) -> None:
        steps = _history.slot_energy_steps({_u(10, 10): 120.0}, {_u(10, 12): 500.0})
        assert [s.start for s in steps] == [_u(10, 10)]

    def test_without_5_minute_rows_every_hourly_row_counts(self) -> None:
        steps = _history.slot_energy_steps({}, {_u(10, 8): 60.0, _u(10, 7): 30.0})
        assert [(s.start, s.energy) for s in steps] == [
            (_u(10, 7), pytest.approx(30.0)),
            (_u(10, 8), pytest.approx(60.0)),
        ]

    def test_a_carry_is_never_negative(self) -> None:
        five = {_u(10, 10, 30 + 5 * i): 1000.0 for i in range(6)}  # 500 Wh seen
        steps = _history.slot_energy_steps(five, {_u(10, 10): 100.0})  # the hour says 100 Wh
        assert all(s.energy >= 0 for s in steps)

    def test_no_rows_no_steps(self) -> None:
        assert _history.slot_energy_steps({}, {}) == []


class TestCombinedRows:
    def test_the_inputs_add_up_in_kwh_and_a_missing_slot_adds_zero(self) -> None:
        a = [_step(_u(10, 10), 10.0), _step(_u(10, 10, 5), 20.0)]  # Wh
        b = [_step(_u(10, 10, 5), 0.05)]  # kWh, no row in the first slot
        today, total = _history.combined_integral_rows([(a, "Wh"), (b, "kWh")], BERLIN)
        assert [r.start for r in today] == [_u(10, 10), _u(10, 10, 5)]
        assert [r.state for r in today] == pytest.approx([0.010, 0.010 + 0.020 + 0.050])
        assert [r.total for r in total] == pytest.approx([0.010, 0.080])
        assert [r.state for r in total] == pytest.approx([0.010, 0.080])
        assert all(r.last_reset is None for r in total)
        assert all(r.last_reset is not None for r in today)

    def test_the_today_rows_restart_at_the_local_midnight_and_the_total_goes_on(self) -> None:
        a = [_step(_u(9, 22, 0), 1000.0), _step(_u(9, 23, 30), 1000.0)]  # Wh: 22:00Z, 00:30 local
        today, total = _history.combined_integral_rows([(a, "Wh")], BERLIN)
        assert [r.state for r in today] == pytest.approx([1.0, 1.0])
        assert [r.state for r in total] == pytest.approx([1.0, 2.0])

    def test_no_inputs_no_rows(self) -> None:
        assert _history.combined_integral_rows([], BERLIN) == ([], [])


# ---------------------------------------------------------------------------
# The reader, the writer and the orchestration
# ---------------------------------------------------------------------------


class _Recorder(StoredRecorder):
    """The stateful recorder, and the metadata of every import."""

    def __init__(self) -> None:
        super().__init__()
        self.metadata: list[dict[str, Any]] = []

    def async_import_statistics(self, metadata: Any, stats: Any, table: Any) -> None:
        self.metadata.append(dict(metadata))
        super().async_import_statistics(metadata, stats, table)

    def has_sum_rows(self, sid: str, table: str) -> dict[datetime, dict[str, Any]]:
        return {ts: row for (t, s, ts), row in self.rows.items() if t == table and s == sid}


def _hass() -> Any:
    return SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Berlin"))


def _store(
    recorder: _Recorder,
    statistic_id: str,
    unit: str,
    five: dict[datetime, float] | None = None,
    hourly: dict[datetime, float] | None = None,
) -> None:
    """Rows of an effective series, as an earlier run of Effy wrote them."""
    recorder.units[statistic_id] = unit
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
    # A (W): an hour of the day before, two older hours, two 5-minute rows
    _store(
        recorder,
        EFF_A,
        "W",
        five={_u(10, 10): 120.0, _u(10, 10, 5): 120.0, _u(10, 10, 10): 240.0},
        hourly={_u(9, 20): 60.0, _u(10, 8): 600.0, _u(10, 9): 300.0},
    )
    # B (kW): two 5-minute rows
    _store(recorder, EFF_B, "kW", five={_u(10, 10): 0.6, _u(10, 10, 5): 0.6, _u(10, 10, 10): 0.6})
    return recorder


NOW = _u(
    10, 10, 12
)  # settled_before: 10:10, so the slots at 10:00 and 10:05 are the last completed
OPTIONS = _options([PV_A, PV_B], [OUT])


async def _derive(**kwargs: Any) -> dict[str, Any]:
    result: dict[str, Any] = await _history.async_derive_pv_delta_integrals(
        _hass(), OPTIONS, now=NOW, **kwargs
    )
    return result


MIDNIGHT = datetime(2026, 11, 10, 0, 0, tzinfo=BERLIN)
PREVIOUS = datetime(2026, 11, 9, 0, 0, tzinfo=BERLIN)


class TestTheDerivationWritesTheHistory:
    async def test_the_per_input_integral_of_a_w_input_is_written_in_wh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        short = recorder.has_sum_rows(TODAY_A, "5minute")
        # only up to the last completed slot (10:05): the 10:10 slot is the recorder's
        assert set(short) == {_u(10, 10), _u(10, 10, 5)}
        assert short[_u(10, 10)]["state"] == pytest.approx(910.0)
        assert short[_u(10, 10)]["sum"] == pytest.approx(970.0)
        assert short[_u(10, 10)]["last_reset"] == MIDNIGHT
        assert short[_u(10, 10, 5)]["state"] == pytest.approx(920.0)
        assert short[_u(10, 10, 5)]["sum"] == pytest.approx(980.0)
        assert short[_u(10, 10)]["mean"] is None  # a running total has no mean
        assert recorder.units[TODAY_A] == "Wh"

    async def test_the_hourly_rows_are_the_closed_hours_with_the_values_of_their_last_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        hourly = recorder.has_sum_rows(TODAY_A, "hour")
        # the hour of 10:00 is not closed (the slot of 10:10 is the recorder's): not written
        assert set(hourly) == {_u(9, 20), _u(10, 8), _u(10, 9)}
        assert hourly[_u(9, 20)]["state"] == pytest.approx(60.0)
        assert hourly[_u(9, 20)]["last_reset"] == PREVIOUS
        assert hourly[_u(10, 8)]["state"] == pytest.approx(600.0)  # the day restarts
        assert hourly[_u(10, 8)]["sum"] == pytest.approx(660.0)
        assert hourly[_u(10, 8)]["last_reset"] == MIDNIGHT
        assert hourly[_u(10, 9)]["state"] == pytest.approx(900.0)
        assert hourly[_u(10, 9)]["sum"] == pytest.approx(960.0)

    async def test_the_hourly_row_of_a_closed_hour_takes_the_last_5_minute_row_of_its_hour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        # the same data, a quarter of an hour later: the hour of 10:00 is closed at 11:00
        _store(recorder, EFF_A, "W", five={_u(10, 10, 5 * i): 120.0 for i in range(12)})
        await _history.async_derive_pv_delta_integrals(_hass(), OPTIONS, now=_u(10, 11, 1))
        hourly = recorder.has_sum_rows(TODAY_A, "hour")
        short = recorder.has_sum_rows(TODAY_A, "5minute")
        last = short[_u(10, 10, 55)]
        assert hourly[_u(10, 10)]["state"] == last["state"]
        assert hourly[_u(10, 10)]["sum"] == last["sum"]
        assert hourly[_u(10, 10)]["last_reset"] == last["last_reset"]

    async def test_the_per_input_integral_of_a_kw_input_is_written_in_kwh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        short = recorder.has_sum_rows(TODAY_B, "5minute")
        assert recorder.units[TODAY_B] == "kWh"
        assert short[_u(10, 10)]["state"] == pytest.approx(0.05)
        assert short[_u(10, 10, 5)]["state"] == pytest.approx(0.10)
        assert short[_u(10, 10, 5)]["sum"] == pytest.approx(0.10)
        assert recorder.has_sum_rows(TODAY_B, "hour") == {}  # no hourly row, no closed hour

    async def test_the_combined_integrals_add_up_the_inputs_in_kwh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        today = recorder.has_sum_rows(COMBINED_TODAY, "5minute")
        total = recorder.has_sum_rows(COMBINED_TOTAL, "5minute")
        assert recorder.units[COMBINED_TODAY] == recorder.units[COMBINED_TOTAL] == "kWh"
        assert today[_u(10, 10)]["state"] == pytest.approx(0.96)  # 0.9 + 0.01 + 0.05
        assert today[_u(10, 10, 5)]["state"] == pytest.approx(1.02)
        assert today[_u(10, 10, 5)]["sum"] == pytest.approx(1.08)
        assert today[_u(10, 10, 5)]["last_reset"] == MIDNIGHT
        assert total[_u(10, 10)]["state"] == total[_u(10, 10)]["sum"] == pytest.approx(1.02)
        assert total[_u(10, 10, 5)]["state"] == total[_u(10, 10, 5)]["sum"] == pytest.approx(1.08)
        assert total[_u(10, 10, 5)]["last_reset"] is None
        hourly_total = recorder.has_sum_rows(COMBINED_TOTAL, "hour")
        assert set(hourly_total) == {_u(9, 20), _u(10, 8), _u(10, 9)}
        assert hourly_total[_u(10, 9)]["state"] == pytest.approx(0.96)

    async def test_the_metadata_is_has_sum_not_has_mean_with_the_unit_and_the_recorder_as_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        by_id = {
            m["statistic_id"]: m for m in recorder.metadata if m["statistic_id"] in recorder.flags
        }
        for sid, unit in (
            (TODAY_A, "Wh"),
            (TODAY_B, "kWh"),
            (COMBINED_TODAY, "kWh"),
            (COMBINED_TOTAL, "kWh"),
        ):
            meta = by_id[sid]
            assert (meta["has_sum"], meta["has_mean"]) == (True, False), sid
            assert meta["unit_of_measurement"] == unit and meta["source"] == "recorder", sid
        assert {m["statistic_id"] for m in recorder.metadata if "delta" in m["statistic_id"]} == {
            TODAY_A,
            TODAY_B,
            COMBINED_TODAY,
            COMBINED_TOTAL,
        }
        # what the existing writer does is untouched: the effective series stay has_mean
        assert recorder.flags.get(EFF_A) is None


class TestTheDeltaBase:
    async def test_it_is_the_last_written_row_per_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world(monkeypatch)
        bases = await _derive()
        assert set(bases) == {TODAY_A, TODAY_B, COMBINED_TODAY, COMBINED_TOTAL}
        a = bases[TODAY_A]
        assert (a.state, a.total) == (pytest.approx(920.0), pytest.approx(980.0))
        assert a.last_reset == MIDNIGHT
        assert bases[TODAY_B].state == pytest.approx(0.10)
        assert (bases[COMBINED_TODAY].state, bases[COMBINED_TODAY].total) == (
            pytest.approx(1.02),
            pytest.approx(1.08),
        )
        total = bases[COMBINED_TOTAL]
        assert (total.state, total.total, total.last_reset) == (
            pytest.approx(1.08),
            pytest.approx(1.08),
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
        assert set(bases) == {TODAY_A, TODAY_B, COMBINED_TODAY, COMBINED_TOTAL}
        for base in bases.values():
            assert (base.state, base.total, base.last_reset) == (0.0, 0.0, None)
        assert recorder.imports == []


class TestARunOnlyDerivesWhatLacksStatistics:
    async def test_running_it_twice_writes_nothing_the_second_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        await _derive()
        written = len(recorder.imports)
        assert written > 0
        second = await _derive()
        assert second == {}  # nothing derived, so no base to hand over
        assert len(recorder.imports) == written

    async def test_a_series_that_already_has_statistics_is_skipped_but_still_in_the_combined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        recorder.units[TODAY_A] = "Wh"  # the recorder compiled it before: it has metadata
        bases = await _derive()
        assert TODAY_A not in bases
        assert recorder.has_sum_rows(TODAY_A, "5minute") == {}
        assert recorder.has_sum_rows(TODAY_A, "hour") == {}
        assert set(bases) == {TODAY_B, COMBINED_TODAY, COMBINED_TOTAL}
        # the combined still adds up input A
        assert bases[COMBINED_TODAY].state == pytest.approx(1.02)

    async def test_without_a_pv_input_there_is_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _world(monkeypatch)
        no_pv = _options([], [OUT], bms=[PV_A])
        result = await _history.async_derive_pv_delta_integrals(_hass(), no_pv, now=NOW)
        assert result == {} and recorder.imports == []


class TestTheButtonDoesNotDerive:
    async def test_a_full_history_recalculation_never_calls_the_derivation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _rig(monkeypatch)

        async def boom(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the button must not derive (ADR-027 decision 4)")

        monkeypatch.setattr(_history, "async_derive_pv_delta_integrals", boom)
        result = await _history.async_recalculate_history(
            object(), _options(["sensor.pv"], ["sensor.out"])
        )
        assert len(result) == 5


class TestGuards:
    async def test_a_source_named_pv_is_derived_like_any_other_beside_the_combined_ones(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ADR-027 Amendment 2026-11-10: the combined integrals read ``deltasum``, so the
        per-input integral of ``sensor.pv`` (``sensor.effy_pv_delta_integral_today``) is its own
        series: derived, no warning, and the combined one is still the addition of both."""
        recorder = _world(monkeypatch)
        _store(recorder, "sensor.effy_pv", "W", five={_u(10, 10): 120.0, _u(10, 10, 5): 120.0})
        options = _options([PV_A, "sensor.pv"], [OUT])
        with caplog.at_level("WARNING"):
            bases = await _history.async_derive_pv_delta_integrals(_hass(), options, now=NOW)
        assert caplog.text == ""
        per_input_pv = "sensor.effy_pv_delta_integral_today"
        assert set(bases) == {TODAY_A, per_input_pv, COMBINED_TODAY, COMBINED_TOTAL}
        assert len({TODAY_A, per_input_pv, COMBINED_TODAY, COMBINED_TOTAL}) == 4
        assert bases[per_input_pv].total == pytest.approx(20.0)  # 2 x 120 W x 5/60 h, in Wh
        # the combined series is the addition of both inputs, not overwritten by one of them
        assert bases[COMBINED_TOTAL].total == pytest.approx(0.98 + 0.02)

    def test_the_constants_name_the_combined_integrals_deltasum(self) -> None:
        assert _const.PV_DELTA_TODAY_ENTITY_ID == "sensor.effy_pv_deltasum_integral_today"
        assert _const.PV_DELTA_TOTAL_ENTITY_ID == "sensor.effy_pv_deltasum_integral_total"
        assert _const.PV_DELTA_INTEGRAL_TODAY_SUFFIX == "_delta_integral_today"

    async def test_an_unknown_time_zone_falls_back_to_utc_days_with_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorder = _world(monkeypatch)
        hass = SimpleNamespace(config=SimpleNamespace(time_zone="Mars/Olympus"))
        with caplog.at_level("WARNING"):
            await _history.async_derive_pv_delta_integrals(hass, OPTIONS, now=NOW)
        assert "unknown time zone" in caplog.text
        reset = recorder.has_sum_rows(TODAY_A, "5minute")[_u(10, 10)]["last_reset"]
        assert reset == _u(10, 0)  # the UTC midnight
