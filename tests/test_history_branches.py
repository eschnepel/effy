"""
The realistic branches of ``history.py`` the other history tests leave open.

Each test runs the real ``history.py`` (loaded with Home Assistant stubbed, see
``test_history_recent_equivalence``) against the in-memory recorder world or the stateful recorder
of ``test_history_write_only_changed``: a disabled stopwatch, an export hook that declines, a write
of nothing, a power-family counter of the efficiency machine, a stored row the writer did not
expect, a series with no recent slot, the PV delta derivation when only some of its series exist.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_delta_derivation import (
    COMBINED_TODAY,
    COMBINED_TOTAL,
    TODAY_A,
    TODAY_B,
)
from tests.test_delta_derivation import NOW as DERIVE_NOW
from tests.test_delta_derivation import OPTIONS as DERIVE_OPTIONS
from tests.test_delta_derivation import _hass as _derive_hass
from tests.test_delta_derivation import _world as _derive_world
from tests.test_history_phase_timing import NOW as RECALC_NOW
from tests.test_history_phase_timing import PW as POWER_INPUT
from tests.test_history_phase_timing import _opts as _recalc_options
from tests.test_history_phase_timing import _world as _recalc_world
from tests.test_history_recent_equivalence import (
    _energy_world,
    _FrozenDatetime,
    _history,
    _install,
    _options,
    _SensorStateClass,
    _ts,
    _World,
)
from tests.test_history_write_only_changed import (
    REAL_UNITS,
    StoredRecorder,
    _install_ha_api,
)

UTC = timezone.utc
SLOT = timedelta(minutes=5)
T0 = _ts(10, 0, day=10)
SID = "sensor.effy_x"


# ---------------------------------------------------------------------------
# The stopwatch and the export hook
# ---------------------------------------------------------------------------


class TestADisabledStopwatch:
    def test_it_keeps_no_cache_figures_and_no_export_note(self) -> None:
        timings = _history._PhaseTimings("history", logging.INFO, enabled=False)
        timings.note_cache(3, 4)
        timings.note_export(1.5, None, "OSError: disk full")
        assert timings.cache_reads is None
        assert timings.export_note is None

    def test_an_enabled_one_keeps_both(self) -> None:
        timings = _history._PhaseTimings("history", logging.INFO)
        timings.note_cache(3, 4)
        timings.note_export(1.5, None, "OSError: disk full")
        assert timings.cache_reads == (3, 4)
        assert timings.export_note is not None and "disk full" in timings.export_note


class TestAnExportHookThatDeclines:
    async def test_nothing_is_noted_and_nothing_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        seen: list[Any] = []

        async def decline(record: Any) -> None:
            seen.append(record)

        record = object()
        timings = _history._PhaseTimings("history", logging.INFO)
        with caplog.at_level(logging.WARNING):
            await _history._run_export(decline, record, timings)
        assert seen == [record]
        assert timings.export_note is None
        assert caplog.records == []


# ---------------------------------------------------------------------------
# The storage a run reads through, and what a write tells it
# ---------------------------------------------------------------------------


class TestCreateStorage:
    def test_it_is_a_cache_that_reads_through_the_recorder_functions_of_the_module(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_history, "_get_unit", lambda _hass, eid: f"unit of {eid}")
        storage = _history.create_storage(object(), clock=lambda: T0)
        assert isinstance(storage, _history.Cache)
        assert storage.unit("sensor.a") == "unit of sensor.a"
        assert storage.counters() == {"hits": 0, "misses": 0}


class _Told:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], datetime, datetime]] = []

    def written(self, statistic_ids: list[str], start: datetime, end: datetime) -> None:
        self.calls.append((statistic_ids, start, end))


class TestWriteAndForget:
    @staticmethod
    def _writer(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
        async def write(_hass: Any, _series: Any, **_options: Any) -> int:
            return count

        monkeypatch.setattr(_history, "_write_recorder_statistics", write)

    async def test_the_storage_is_told_the_span_of_the_slots_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._writer(monkeypatch, 3)
        told = _Told()
        series = {
            SID: {"unit": "kW", "slot_values": [(T0 + SLOT, 1.0), (T0 + 2 * SLOT, 2.0)]},
            "sensor.effy_y": {"unit": "kW", "slot_values": [(T0, 1.0)]},
        }
        written = await _history._write_and_forget(object(), told, series)
        assert written == 3
        assert told.calls == [([SID, "sensor.effy_y"], T0, T0 + 3 * SLOT)]

    async def test_a_write_of_no_slot_tells_the_storage_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._writer(monkeypatch, 0)
        told = _Told()
        written = await _history._write_and_forget(
            object(), told, {SID: {"unit": "kW", "slot_values": []}}
        )
        assert written == 0
        assert told.calls == []


class TestLastSlotValues:
    def test_a_series_without_slots_neither_appears_nor_replaces_an_earlier_value(self) -> None:
        first = {
            "a": {"unit": "W", "slot_values": [(T0, 1.0), (T0 + SLOT, 2.0)]},
            "b": {"unit": "W", "slot_values": []},
        }
        second = {"a": {"unit": "kW", "slot_values": []}}
        assert _history._last_slot_values(first, second) == {"a": (2.0, "W")}


# ---------------------------------------------------------------------------
# The compute of a recalculation
# ---------------------------------------------------------------------------


class TestTheSmoothedSeriesOfAPowerInput:
    @pytest.mark.parametrize(
        ("offset", "written"), [(timedelta(hours=-1), True), (timedelta(days=-6), False)]
    )
    async def test_it_is_written_only_for_the_slots_inside_the_window(
        self, monkeypatch: pytest.MonkeyPatch, offset: timedelta, written: bool
    ) -> None:
        world = _recalc_world()
        _install(monkeypatch, world)

        def fetch(
            _hass: Any, eids: list[str], _start: datetime, _end: datetime
        ) -> dict[str, list[dict[str, Any]]]:
            return {eid: [{"start": RECALC_NOW + offset, "mean": 300.0}] for eid in eids}

        monkeypatch.setattr(_history, "_fetch_statistics", fetch)
        _FrozenDatetime.frozen = RECALC_NOW
        await _history.async_recalculate_history(object(), _recalc_options())
        smoothed = _history._effy_smoothed_entity_id(POWER_INPUT)
        stored = {sid for _long, _short, series in world.write_calls for sid in series}
        assert (smoothed in stored) is written


# ---------------------------------------------------------------------------
# The stored rows the writer compares against
# ---------------------------------------------------------------------------


def _stored_rig(monkeypatch: pytest.MonkeyPatch) -> StoredRecorder:
    recorder = StoredRecorder()
    _install_ha_api(monkeypatch, recorder)
    monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
    monkeypatch.setattr(_history, "_get_statistics_units", REAL_UNITS)
    recorder.units[SID] = "kW"
    return recorder


def _put_row(recorder: StoredRecorder, ts: datetime, mean: float | None, sid: str = SID) -> None:
    recorder.rows[("5minute", sid, ts)] = {
        "mean": mean,
        "state": mean,
        "min": None,
        "max": None,
        "sum": None,
        "last_reset": None,
    }


class TestUnchangedRows:
    def test_a_stored_row_between_the_rows_to_write_is_counted_and_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _stored_rig(monkeypatch)
        stray = T0 + SLOT
        for ts, mean in ((T0, 1.5), (stray, 9.0), (T0 + 2 * SLOT, 1.5)):
            _put_row(recorder, ts, mean)
        notes: dict[str, str] = {}
        result = _history._unchanged_rows(
            object(), SID, "kW", [(T0, 1.5), (T0 + 2 * SLOT, 1.5)], {}, notes
        )
        assert result["short"] == frozenset({T0, T0 + 2 * SLOT})
        assert notes[SID] == (
            "5minute: read 3, equal 2, missing 0, start unexpected 1, mean differs 0,"
            f" columns set 0; first: start {stray!r} is not one of the new rows"
        )

    def test_a_stored_row_without_a_mean_is_a_change_and_has_no_stored_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _stored_rig(monkeypatch)
        _put_row(recorder, T0, None)
        _put_row(recorder, T0 + SLOT, 1.5)
        notes: dict[str, str] = {}
        result = _history._unchanged_rows(
            object(), SID, "kW", [(T0, 1.5), (T0 + SLOT, 1.5)], {}, notes, values=True
        )
        assert result["short"] == frozenset({T0 + SLOT})
        assert result["stored"] == {T0 + SLOT: 1.5}
        assert "mean differs 1" in notes[SID] and "mean stored None, new 1.5" in notes[SID]


class TestMarkUnchanged:
    async def test_a_series_without_a_slot_to_compare_is_not_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _stored_rig(monkeypatch)
        old, recent = "sensor.effy_old", SID
        recorder.units[old] = "kW"
        cutoff = T0 + timedelta(hours=1)
        old_series: dict[str, Any] = {"unit": "kW", "slot_values": [(T0, 1.0), (T0 + SLOT, 1.0)]}
        recent_series: dict[str, Any] = {"unit": "kW", "slot_values": [(cutoff, 1.0)]}
        _put_row(recorder, cutoff, 1.0)
        run = {old: old_series, recent: recent_series}
        await _history._mark_unchanged(
            object(),
            (run,),
            short_term_cutoff=cutoff,  # the old series lies wholly below it
            include_long_term=True,
            phases=_history._NO_TIMINGS,
        )
        assert [sids for _period, sids, _start, _end in recorder.reads] == [{recent}]
        assert "unchanged_short" not in old_series
        assert recent_series["unchanged_short"] == frozenset({cutoff})


# ---------------------------------------------------------------------------
# The stored rows of the PV delta integrals
# ---------------------------------------------------------------------------


class TestTheIntegralSourceRead:
    def test_start_timestamps_become_datetimes_and_a_row_without_a_mean_is_left_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _stored_rig(monkeypatch)
        recorder.start_as_float = True  # some Home Assistant versions do this
        _put_row(recorder, T0, 120.0)
        _put_row(recorder, T0 + SLOT, None)
        recorder.rows[("hour", SID, T0)] = {"mean": 90.0}
        five, hourly = _history._fetch_integral_source(object(), SID, T0, T0 + timedelta(hours=1))
        assert five == {T0: 120.0}
        assert hourly == {T0: 90.0}


class TestTheIntegralStoredRead:
    def test_timestamps_are_converted_and_a_row_without_a_sum_or_state_is_left_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _stored_rig(monkeypatch)
        recorder.start_as_float = True
        recorder.flags[SID] = {"has_mean": False, "has_sum": True}
        reset = _ts(0, 0, day=10)
        columns: dict[str, Any] = {"mean": None, "min": None, "max": None}
        rows: list[tuple[datetime, float | None, float | None]] = [
            (T0, 10.0, 40.0),
            (T0 + SLOT, 11.0, None),
            (T0 + 2 * SLOT, None, 42.0),
        ]
        for ts, state, total in rows:
            recorder.rows[("5minute", SID, ts)] = {
                **columns,
                "state": state,
                "sum": total,
                "last_reset": reset,
            }
        five, hourly = _history._fetch_integral_stored(object(), SID, T0, T0 + timedelta(hours=1))
        assert five == {T0: _history.IntegralStat(10.0, 40.0, reset)}
        assert hourly == {}


class TestTheLastIntegralRow:
    def test_the_later_end_wins_whatever_the_order_of_the_rows(self) -> None:
        early = _history.IntegralStat(1.0, 1.0, None)
        late = _history.IntegralStat(2.0, 2.0, None)
        assert _history._last_integral_row({T0 + SLOT: late, T0: early}, {}) is late
        assert _history._last_integral_row({T0: early, T0 + SLOT: late}, {}) is late


class TestTheTimeZone:
    @pytest.mark.parametrize(
        "hass", [object(), SimpleNamespace(config=SimpleNamespace(time_zone=""))]
    )
    def test_a_home_assistant_without_a_time_zone_gives_utc_days(self, hass: Any) -> None:
        assert _history._local_tz(hass) is timezone.utc


class TestDeriveOnlyWhatLacksStatistics:
    async def test_without_the_combined_integrals_missing_only_the_per_input_ones_are_derived(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _derive_world(monkeypatch)
        recorder.units[COMBINED_TODAY] = recorder.units[COMBINED_TOTAL] = "kWh"
        bases = await _history.async_derive_pv_delta_integrals(
            _derive_hass(), DERIVE_OPTIONS, now=DERIVE_NOW
        )
        assert set(bases) == {TODAY_A, TODAY_B}
        assert recorder.short(COMBINED_TODAY) == recorder.short(COMBINED_TOTAL) == {}
        assert recorder.short(TODAY_A) and recorder.short(TODAY_B)

    async def test_with_one_combined_integral_missing_only_that_one_is_derived(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _derive_world(monkeypatch)
        recorder.units[COMBINED_TODAY] = "kWh"
        bases = await _history.async_derive_pv_delta_integrals(
            _derive_hass(), DERIVE_OPTIONS, now=DERIVE_NOW
        )
        assert set(bases) == {TODAY_A, TODAY_B, COMBINED_TOTAL}
        assert recorder.short(COMBINED_TODAY) == {}
        assert recorder.short(COMBINED_TOTAL)


# ---------------------------------------------------------------------------
# The readers of the standby and the efficiency machines
# ---------------------------------------------------------------------------

GI, DIS, CH, GE = "sensor.grid_in", "sensor.discharge", "sensor.charge", "sensor.grid_out"
STANDBY = "sensor.effy_standby_power"


def _counters_world(**power: str) -> _World:
    """Four counters; those named in ``power`` (id -> unit) are power-family, the rest kWh."""
    world = _energy_world(**{eid: [(T0, "1.000")] for eid in (GI, DIS, CH, GE) if eid not in power})
    for eid, unit in power.items():
        world.units[eid] = unit
        world.state_classes[eid] = _SensorStateClass.MEASUREMENT
    return world


def _counter_options() -> dict[str, Any]:
    return _options([], [CH, GE], [GI, DIS])


def _serve(
    monkeypatch: pytest.MonkeyPatch, rows: dict[str, list[dict[str, Any]]]
) -> list[list[str]]:
    asked: list[list[str]] = []

    def fetch(
        _hass: Any, eids: list[str], _start: datetime, _end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        asked.append(list(eids))
        return {eid: [] for eid in eids} | {eid: list(found) for eid, found in rows.items()}

    monkeypatch.setattr(_history, "_fetch_statistics", fetch)
    return asked


class TestEfficiencyEnergy:
    START = T0
    END = T0 + timedelta(hours=1)

    async def test_without_counters_there_is_nothing_to_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, _counters_world())
        asked = _serve(monkeypatch, {})
        assert await _history.async_read_efficiency_energy(
            object(), _options(["sensor.pv"]), self.START, self.END
        ) == ({}, {})
        only_others = await _history.async_read_efficiency_energy(
            object(), _counter_options(), self.START, self.END, only={"sensor.other"}
        )
        assert only_others == ({}, {})
        assert asked == []

    async def test_power_family_counters_are_read_from_their_smoothed_series_or_themselves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # GI and DIS are inputs of the entry (smoothed series), CH and GE outputs (their own)
        _install(monkeypatch, _counters_world(**{GI: "W", DIS: "W", CH: "W", GE: "kW"}))
        s1, s2, s3 = T0, T0 + SLOT, T0 + 2 * SLOT
        asked = _serve(
            monkeypatch,
            {
                _history._effy_smoothed_entity_id(GI): [
                    {"start": s1, "mean": 600.0},
                    {"start": s2, "mean": None},  # a slot without a mean is unknown
                    {"start": self.END, "mean": 600.0},  # outside [start, end)
                ],
                _history._effy_smoothed_entity_id(DIS): [{"start": s2, "mean": 1200.0}],
                CH: [{"start": s3, "mean": 300.0}],
                GE: [{"start": s1, "mean": 2.4}],
                STANDBY: [
                    {"start": s1, "mean": 20.0},
                    {"start": s2, "mean": None},
                    {"start": self.END, "mean": 20.0},
                ],
            },
        )
        energy, standby = await _history.async_read_efficiency_energy(
            object(), _counter_options(), self.START, self.END
        )
        assert energy == {
            s1: {GI: pytest.approx(0.05), GE: pytest.approx(0.2)},
            s2: {DIS: pytest.approx(0.1)},
            s3: {CH: pytest.approx(0.025)},
        }
        assert standby == {s1: pytest.approx(20.0 / 1000.0 * 5 / 60)}
        assert set(asked[0]) == {
            _history._effy_smoothed_entity_id(GI),
            _history._effy_smoothed_entity_id(DIS),
            CH,
            GE,
            STANDBY,
        }

    async def test_a_statistic_that_was_not_asked_for_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, _counters_world())
        _serve(monkeypatch, {"sensor.effy_unrelated": [{"start": T0, "mean": 5.0}]})
        assert await _history.async_read_efficiency_energy(
            object(), _counter_options(), self.START, self.END
        ) == ({}, {})


class TestCounterResolution:
    async def test_without_counters_there_is_nothing_to_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _counters_world()
        _install(monkeypatch, world)
        options = _options(["sensor.pv"])
        assert (
            await _history.async_read_counter_resolution(
                object(), options, T0, T0 + timedelta(hours=1)
            )
            == {}
        )
        assert world.raw_calls == []

    async def test_a_power_family_counter_is_not_read_for_its_resolution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _counters_world(**{GI: "W", CH: "W"})
        world.energy[DIS] = world.energy[GE] = [(T0, "1.000"), (T0 + SLOT, "1.000")]
        _install(monkeypatch, world)
        resolution = await _history.async_read_counter_resolution(
            object(), _counter_options(), T0, T0 + timedelta(hours=1)
        )
        assert resolution == {}  # the energy counters never jumped
        assert {eid for eid, _start, _end in world.raw_calls} == {DIS, GE}


class TestDrainActivity:
    async def test_a_statistic_that_was_not_asked_for_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, _counters_world())
        _serve(monkeypatch, {"sensor.effy_unrelated": [{"start": T0, "mean": 5.0}]})
        activity = await _history.async_read_drain_activity(
            object(), _options([], [CH, GE], [GI, DIS]), T0, T0 + timedelta(hours=1)
        )
        assert activity == {}
