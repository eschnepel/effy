"""
The PV delta of every run, from the stored values (TASK-0045b, CAP-13, ADR-027 decision 2).

Each recalculation reports, per PV input, how much energy it changed in the stored rows
of that input's effective series: the added-up ``(new − stored) × 5/60`` over the
5-minute rows it wrote, in Wh for an input whose effective unit is W and in kWh for kW.
The stored values come to the event loop together with the unchanged starts (ADR-024
Amendment 2026-11-09, a human decision); only for the PV inputs' effective series and
without a further recorder read. Nothing books the deltas yet (TASK-0045d).

The pure function and the unit conversion run without a fake. The read and the run run
the REAL ``history.py`` against the stateful recorder of ``test_history_write_only_changed``.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_history_phase_timing import NOW, OUT, PV, PW, _opts
from tests.test_history_recent_equivalence import _const, _history, _options, _ts
from tests.test_history_write_only_changed import (
    StoredRecorder,
    _install_ha_api,
    _recalculate,
    _rig,
)

SLOT = timedelta(minutes=5)
EFF_PV = _history._effy_entity_id(PV)  # energy family: effective series in kW
EFF_PW = _history._effy_entity_id(PW)  # power family: effective series in W
T0 = _ts(10, 0, day=10)


# ---------------------------------------------------------------------------
# The pure function and the unit conversion
# ---------------------------------------------------------------------------


class TestThePureDelta:
    def test_the_example_of_the_task(self) -> None:
        # stored 300 W is rewritten to 290 W and a new slot gets 310 W: (-10 + 310) * 5/60
        new = [(T0, 290.0), (T0 + SLOT, 310.0)]
        assert _history.pv_delta(new, {T0: 300.0}) == pytest.approx(25.0)

    def test_unchanged_rows_add_zero_and_no_rows_are_zero(self) -> None:
        assert (
            _history.pv_delta([(T0, 300.0), (T0 + SLOT, 310.0)], {T0: 300.0, T0 + SLOT: 310.0})
            == 0.0
        )
        assert _history.pv_delta([], {T0: 300.0}) == 0.0

    def test_a_slot_without_a_stored_row_counts_with_its_new_value_alone(self) -> None:
        assert _history.pv_delta([(T0, 120.0)], {}) == pytest.approx(10.0)  # 120 * 5/60

    def test_a_fall_is_a_negative_delta(self) -> None:
        assert _history.pv_delta([(T0, 100.0)], {T0: 112.0}) == pytest.approx(-1.0)

    def test_a_stored_row_that_is_not_among_the_new_rows_is_not_counted(self) -> None:
        assert _history.pv_delta([(T0, 100.0)], {T0: 100.0, T0 - SLOT: 999.0}) == 0.0


class TestTheUnitConversion:
    @pytest.mark.parametrize(
        ("value", "stored", "new", "expected"),
        [
            (0.3, "kW", "W", 300.0),
            (300.0, "W", "kW", 0.3),
            (0.3, "kW", "kW", 0.3),
            (300.0, "W", "W", 300.0),
        ],
    )
    def test_a_stored_value_is_converted_by_its_unit(
        self, value: float, stored: str, new: str, expected: float
    ) -> None:
        assert _history.convert_unit(value, stored, new) == pytest.approx(expected)

    def test_the_same_unit_is_returned_as_it_is(self) -> None:
        value = 0.1 + 0.2  # a value a multiplication and a division would change
        assert _history.convert_unit(value, "kW", "kW") == value


# ---------------------------------------------------------------------------
# The read: the stored values, converted, with no further recorder read
# ---------------------------------------------------------------------------


class TestTheStoredValuesOfARead:
    SID = "sensor.effy_x"
    T = NOW - timedelta(hours=3)

    def _recorder(self, monkeypatch: pytest.MonkeyPatch, unit: str | None) -> StoredRecorder:
        recorder = StoredRecorder()
        _install_ha_api(monkeypatch, recorder)
        monkeypatch.setattr(_history, "_get_statistics_units", _history._get_statistics_units)
        if unit is not None:
            recorder.units[self.SID] = unit
        return recorder

    def _store(self, recorder: StoredRecorder, ts: datetime, mean: float) -> None:
        recorder.rows[("5minute", self.SID, ts)] = {
            "mean": mean,
            "state": mean,
            "min": None,
            "max": None,
            "sum": None,
            "last_reset": None,
        }

    def _read(self, unit: str, rows: list[tuple[datetime, float]]) -> dict[str, Any]:
        result: dict[str, Any] = _history._unchanged_rows(
            object(), self.SID, unit, rows, {}, values=True
        )
        return result

    def test_the_stored_value_of_every_row_to_write_comes_back_with_the_unchanged_starts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._recorder(monkeypatch, "kW")
        self._store(recorder, self.T, 1.5)
        self._store(recorder, self.T + SLOT, 2.5)
        result = self._read("kW", [(self.T, 1.5), (self.T + SLOT, 9.9), (self.T + 2 * SLOT, 3.0)])
        assert result["short"] == frozenset({self.T})  # unchanged: as before
        assert result["stored"] == {self.T: 1.5, self.T + SLOT: 2.5}  # no row: no value
        assert len(recorder.reads) == 1  # one read, as without the values

    def test_a_stored_value_in_another_unit_is_converted_and_nothing_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._recorder(monkeypatch, "kW")
        self._store(recorder, self.T, 0.3)
        result = self._read("W", [(self.T, 300.0)])
        assert result["stored"] == {self.T: pytest.approx(300.0)}
        assert result["short"] == frozenset()  # the import is what updates the metadata
        assert len(recorder.reads) == 1

    def test_without_metadata_nothing_is_read_and_every_stored_value_counts_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._recorder(monkeypatch, None)
        self._store(recorder, self.T, 1.5)  # rows without a unit are not to be trusted
        result = self._read("kW", [(self.T, 1.5)])
        assert result["stored"] == {}
        assert recorder.reads == []

    def test_the_values_are_only_returned_when_asked_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._recorder(monkeypatch, "kW")
        self._store(recorder, self.T, 1.5)
        result = _history._unchanged_rows(object(), self.SID, "kW", [(self.T, 1.5)], {})
        assert set(result) == {"short", "hourly"}  # every other series: unchanged starts only


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _deltas(result: Any) -> dict[str, tuple[float, str]] | None:
    assert len(result) == 5  # written, recalculated_from, touched, last_values, deltas
    deltas: dict[str, tuple[float, str]] | None = result[4]
    return deltas


def _rows(recorder: StoredRecorder, statistic_id: str) -> dict[datetime, float]:
    return {ts: row["mean"] for ts, row in recorder.short(statistic_id).items()}


def _energy(new: dict[datetime, float], stored: dict[datetime, float]) -> float:
    return sum((value - stored.get(ts, 0.0)) * 5 / 60 for ts, value in new.items())


class TestTheRunsDeltas:
    async def test_a_full_recalculation_over_an_empty_recorder_reports_the_energy_of_all_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        deltas = _deltas(await _recalculate())
        assert deltas is not None
        assert set(deltas) == {PV, PW}  # the PV inputs, by their source entity ID
        assert deltas[PV][1] == "kWh" and deltas[PW][1] == "Wh"  # effective kW, effective W
        assert deltas[PV][0] == pytest.approx(_energy(_rows(recorder, EFF_PV), {}))
        assert deltas[PW][0] == pytest.approx(_energy(_rows(recorder, EFF_PW), {}))
        assert deltas[PV][0] > 0  # 1.8 kW over the written slots; PW's rows are all 0 here

    async def test_a_run_that_writes_nothing_reports_zero_not_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _rig(monkeypatch)
        await _recalculate()
        again = _deltas(await _recalculate())
        assert again == {PV: (0.0, "kWh"), PW: (0.0, "Wh")}

    async def test_rewritten_rows_add_new_minus_stored_and_unchanged_rows_add_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        # What an earlier run stored differs from what the world gives now: three PV rows
        # were 0.1 kW lower, two PV rows were not there, four rows of the power input were
        # 5 W. Every other row is what is stored.
        for i in (3, 4, 5):
            row = recorder.rows[("5minute", EFF_PV, T0 + SLOT * i)]
            recorder.rows[("5minute", EFF_PV, T0 + SLOT * i)] = {**row, "mean": row["mean"] - 0.1}
        for i in (8, 9):
            del recorder.rows[("5minute", EFF_PV, T0 + SLOT * i)]
        for i in (0, 1, 2, 3):
            row = recorder.rows[("5minute", EFF_PW, T0 + SLOT * i)]
            recorder.rows[("5minute", EFF_PW, T0 + SLOT * i)] = {**row, "mean": 5.0}
        before = {sid: _rows(recorder, sid) for sid in (EFF_PV, EFF_PW)}

        deltas = _deltas(await _recalculate())

        assert deltas is not None
        # 3 rows * 0.1 kW + 2 new rows * 1.8 kW, each for 5/60 h
        assert deltas[PV][0] == pytest.approx((3 * 0.1 + 2 * 1.8) * 5 / 60)
        assert deltas[PW][0] == pytest.approx(-4 * 5.0 * 5 / 60)  # back from 5 W to 0 W
        for source, sid in ((PV, EFF_PV), (PW, EFF_PW)):
            assert deltas[source][0] == pytest.approx(_energy(_rows(recorder, sid), before[sid]))

    async def test_a_stored_value_in_another_unit_is_converted_by_its_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        # the power input's rows were stored in kW (as if the unit had changed): same energy
        recorder.units[EFF_PW] = "kW"
        for ts, row in recorder.short(EFF_PW).items():
            recorder.rows[("5minute", EFF_PW, ts)] = {**row, "mean": row["mean"] / 1000.0}
        deltas = _deltas(await _recalculate())
        assert deltas is not None
        assert deltas[PW][0] == pytest.approx(0.0, abs=1e-9)

    async def test_stored_rows_without_metadata_count_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        expected = _energy(_rows(recorder, EFF_PW), {})
        del recorder.units[EFF_PW]
        recorder.rows = {k: v for k, v in recorder.rows.items() if k[1] != EFF_PW}
        deltas = _deltas(await _recalculate())
        assert deltas is not None
        assert deltas[PW][0] == pytest.approx(expected)

    async def test_when_the_stored_rows_cannot_be_read_there_is_no_delta_and_every_row_is_written(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()  # the first run stores rows; the read fails in the second
        recorder.imports.clear()
        recorder.read_error = RuntimeError("database is locked")
        with caplog.at_level(logging.WARNING):
            result = await _recalculate()
        assert _deltas(result) is None  # nothing is not zero: no input reports a delta
        assert "could not read the stored statistics" in caplog.text
        assert recorder.imports, "every row is written as before"

    async def test_an_input_the_caller_marks_as_having_no_base_is_left_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _rig(monkeypatch)
        deltas = _deltas(await _recalculate(delta_skip=frozenset({PW})))
        assert deltas is not None and set(deltas) == {PV}

    async def test_a_bms_input_is_not_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _rig(monkeypatch)
        options = _options([PV], [OUT], bms=[PW])
        result = await _history.async_recalculate_history(object(), options)
        deltas = _deltas(result)
        assert deltas is not None and set(deltas) == {PV}

    async def test_the_eps_sensor_is_not_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _rig(monkeypatch)
        options = _options([PV], [OUT])
        options[_const.CONF_EPS_SENSOR] = PW  # the signed EPS/UPS sensor, an input but not PV
        deltas = _deltas(await _history.async_recalculate_history(object(), options))
        assert deltas is not None and set(deltas) == {PV}

    async def test_the_deltas_need_no_additional_recorder_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        recorder.reads.clear()
        await _recalculate()
        per_read = Counter((period, tuple(sorted(ids))) for period, ids, _s, _e in recorder.reads)
        assert per_read and max(per_read.values()) == 1  # one read per series and table
        assert {period for period, *_ in per_read} == {"5minute"}  # the hourly rows do not count

    async def test_a_slot_left_to_the_recorder_is_not_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The newest slots are not written (the recorder compiles them, ADR-024), so they are
        no delta either: the delta is the energy of the rows that were written."""
        _world_, recorder = _rig(monkeypatch)
        deltas = _deltas(await _recalculate())
        assert deltas is not None
        written = _rows(recorder, EFF_PW)
        assert max(written) < NOW  # something is left out at the end of the world
        assert deltas[PW][0] == pytest.approx(_energy(written, {}))

    async def test_the_recent_recalculation_returns_the_deltas_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world_, recorder = _rig(monkeypatch)
        await _recalculate()
        key = ("5minute", EFF_PV, T0 + SLOT * 20)  # 11:40, inside the recent window
        recorder.rows[key] = {**recorder.rows[key], "mean": 1.0}
        before = _rows(recorder, EFF_PV)
        result = await _history.async_recalculate_recent(object(), _opts(), NOW)
        deltas = _deltas(result)
        assert deltas is not None and set(deltas) == {PV, PW}
        after = _rows(recorder, EFF_PV)
        assert after[T0 + SLOT * 20] == pytest.approx(1.8)  # the row was rewritten
        assert deltas[PV][0] == pytest.approx(0.8 * 5 / 60)
        assert deltas[PV][0] == pytest.approx(_energy(after, before))
