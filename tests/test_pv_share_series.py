"""
The PV share series in the history computation (TASK-0046, CAP-13, ADR-027 amendment 2026-11-09).

Per slot ``100 x (sum of effective PV + the EPS input) / (sum of all effective inputs)``, in %,
with the slot's denominator as the weight of the hourly row. The real ``history.py`` runs against
the in-memory recorder world of the CAP-1 equivalence test.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_eps_waterfall import (
    END,
    SLOTS,
    START,
    _add_power,
    _const,
    _history,
    _install,
    _options,
    _World,
)
from tests.test_history_recent_equivalence import (
    _equivalent,
    _run_full_recalc,
    _run_timer_cycles,
    _ts,
)

PV_A = "sensor.pv_a_w"
PV_B = "sensor.pv_b_kw"
GRID = "sensor.grid_in_w"
DISCHARGE = "sensor.discharge_w"
OUT = "sensor.out_w"
EPS = "sensor.eps_w"
SHARE = _const.PV_SHARE_ENTITY_ID


def _flat(value: float) -> dict[datetime, float]:
    return {slot: value for slot in SLOTS}


def _world(**watts: float) -> _World:
    """A power-family world; ``pv_b`` is a kW sensor (the others are W)."""
    world = _World()
    names = {"pv_a": PV_A, "pv_b": PV_B, "grid": GRID, "discharge": DISCHARGE, "out": OUT}
    for key, value in watts.items():
        eid = names.get(key)
        if eid is not None:
            _add_power(world, eid, _flat(value))
    if "pv_b" in watts:
        world.units[PV_B] = "kW"
    return world


def _opts(
    pv: list[str], out: list[str], bms: list[str] | None = None, eps: str | None = None
) -> dict[str, Any]:
    options = _options(pv, out, bms)
    if eps is not None:
        options[_const.CONF_EPS_SENSOR] = eps
    return options


async def _compute(
    monkeypatch: pytest.MonkeyPatch, world: _World, options: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    _install(monkeypatch, world)
    per_sensor, _power, _smoothed = await _history._compute_effective_slots(
        object(), options, START, END
    )
    return per_sensor  # type: ignore[no-any-return]


def _values(info: dict[str, Any]) -> dict[datetime, float]:
    return {slot: value for slot, value in info["slot_values"]}


class TestTheShareOfASlot:
    async def test_pv_in_w_and_kw_over_all_inputs_is_fifty_percent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The task's example: PV 300 W and 0.2 kW, grid import 500 W: 50 %. The units are
        normalised before the addition (ADR-002, ADR-008)."""
        world = _world(pv_a=300.0, pv_b=0.2, grid=500.0, out=1000.0)
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A, PV_B], [OUT], [GRID]))
        share = per_sensor[SHARE]
        assert share["unit"] == "%"
        assert set(_values(share)) == set(SLOTS)
        assert all(v == pytest.approx(50.0) for v in _values(share).values())

    async def test_the_weight_of_a_slot_is_the_sum_of_all_effective_inputs_in_w(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(pv_a=300.0, pv_b=0.2, grid=500.0, out=1000.0)
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A, PV_B], [OUT], [GRID]))
        weights = per_sensor[SHARE]["weights"]
        assert set(weights) == set(SLOTS)
        assert all(w == pytest.approx(1000.0) for w in weights.values())

    async def test_battery_discharge_counts_on_the_other_side(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(pv_a=200.0, grid=100.0, discharge=300.0, out=600.0)
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A], [OUT], [GRID, DISCHARGE]))
        assert all(v == pytest.approx(100 * 200 / 600) for v in _values(per_sensor[SHARE]).values())

    async def test_the_loss_is_taken_from_the_inputs_before_the_share(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The effective values are used (the waterfall has run): with a loss the share is the
        share of the *effective* inputs, whose sum is what the outputs received."""
        world = _world(pv_a=500.0, grid=500.0, out=800.0)  # 200 W loss
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A], [OUT], [GRID]))
        eff = {
            sid: _values(per_sensor[sid])
            for sid in (_history._effy_entity_id(PV_A), _history._effy_entity_id(GRID))
        }
        slot = SLOTS[3]
        total = (
            eff[_history._effy_entity_id(PV_A)][slot] + eff[_history._effy_entity_id(GRID)][slot]
        )
        assert total == pytest.approx(800.0)  # = the outputs (ADR-001)
        assert _values(per_sensor[SHARE])[slot] == pytest.approx(
            100 * eff[_history._effy_entity_id(PV_A)][slot] / total
        )
        assert per_sensor[SHARE]["weights"][slot] == pytest.approx(800.0)

    async def test_a_slot_in_which_nothing_fed_is_zero_not_a_missing_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(pv_a=0.0, grid=0.0, out=0.0)
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A], [OUT], [GRID]))
        assert set(_values(per_sensor[SHARE])) == set(SLOTS)
        assert set(_values(per_sensor[SHARE]).values()) == {0.0}
        assert set(per_sensor[SHARE]["weights"].values()) == {0.0}

    async def test_the_value_always_lies_between_0_and_100(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World()
        _add_power(world, PV_A, {slot: 50.0 * i for i, slot in enumerate(SLOTS)})
        _add_power(world, GRID, {slot: 400.0 - 10.0 * i for i, slot in enumerate(SLOTS)})
        _add_power(world, OUT, {slot: 300.0 + 20.0 * i for i, slot in enumerate(SLOTS)})
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A], [OUT], [GRID]))
        values = list(_values(per_sensor[SHARE]).values())
        assert len(set(round(v, 6) for v in values)) > 3  # it really varies
        assert all(0.0 <= v <= 100.0 for v in values)


class TestTheEpsInput:
    async def test_the_eps_input_is_on_the_pv_side(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EPS -200 W: 200 W enter through the port (ADR-026). PV 100, grid 100, EPS in 200,
        outputs 400: (100 + 200) / 400 = 75 %."""
        world = _world(pv_a=100.0, grid=100.0, out=400.0)
        _add_power(world, EPS, _flat(-200.0))
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A], [OUT], [GRID], eps=EPS))
        assert all(v == pytest.approx(75.0) for v in _values(per_sensor[SHARE]).values())
        assert all(w == pytest.approx(400.0) for w in per_sensor[SHARE]["weights"].values())

    async def test_an_eps_output_is_not_an_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EPS +150 W leaves through the port: it is an output, so the inputs and the share
        are those of PV 300 W and grid 100 W: 75 %."""
        world = _world(pv_a=300.0, grid=100.0, out=250.0)
        _add_power(world, EPS, _flat(150.0))
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A], [OUT], [GRID], eps=EPS))
        assert all(v == pytest.approx(75.0) for v in _values(per_sensor[SHARE]).values())

    async def test_an_entry_with_only_an_eps_sensor_has_the_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(grid=200.0, out=400.0)
        _add_power(world, EPS, _flat(-200.0))
        per_sensor = await _compute(monkeypatch, world, _opts([], [OUT], [GRID], eps=EPS))
        assert all(v == pytest.approx(50.0) for v in _values(per_sensor[SHARE]).values())

    async def test_an_entry_without_a_pv_input_and_without_eps_has_no_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(grid=200.0, discharge=200.0, out=400.0)
        per_sensor = await _compute(monkeypatch, world, _opts([], [OUT], [GRID, DISCHARGE]))
        assert SHARE not in per_sensor

    async def test_the_pv_sum_series_is_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = _world(pv_a=300.0, pv_b=0.2, grid=500.0, out=1000.0)
        per_sensor = await _compute(monkeypatch, world, _opts([PV_A, PV_B], [OUT], [GRID]))
        assert all(
            v == pytest.approx(500.0) for v in _values(per_sensor[_const.PV_SUM_ENTITY_ID]).values()
        )


class TestTheHourlyRow:
    A, B = _ts(11, 0), _ts(11, 5)

    def test_it_is_energy_weighted_with_the_weights_of_the_slots(self) -> None:
        rows = _history._hourly_rows(
            [(self.A, 100.0), (self.B, 0.0)], {self.A: 100.0, self.B: 300.0}
        )
        assert rows == {_ts(11, 0): (pytest.approx(25.0), 0.0)}  # the state is the last slot's

    def test_without_weights_it_is_the_mean_as_for_every_other_series(self) -> None:
        rows = _history._hourly_rows([(self.A, 100.0), (self.B, 0.0)])
        assert rows == {_ts(11, 0): (50.0, 0.0)}

    def test_an_hour_in_which_nothing_fed_is_zero(self) -> None:
        rows = _history._hourly_rows([(self.A, 0.0), (self.B, 0.0)], {self.A: 0.0, self.B: 0.0})
        assert rows == {_ts(11, 0): (0.0, 0.0)}

    def test_every_hour_has_its_own_weights(self) -> None:
        values = [(_ts(11, 55), 100.0), (_ts(12, 0), 0.0), (_ts(12, 5), 50.0)]
        weights = {_ts(11, 55): 10.0, _ts(12, 0): 100.0, _ts(12, 5): 100.0}
        rows = _history._hourly_rows(values, weights)
        assert rows[_ts(11, 0)][0] == pytest.approx(100.0)
        assert rows[_ts(12, 0)][0] == pytest.approx(25.0)

    async def test_the_writer_writes_the_weighted_mean_of_a_closed_hour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[list[dict[str, Any]]] = []

        class _Instance:
            def async_import_statistics(
                self, _meta: Any, stats: list[dict[str, Any]], _t: Any
            ) -> None:
                recorded.append(stats)

            async def async_block_till_done(self) -> None:
                return None

        monkeypatch.setattr(_history, "get_recorder", lambda _hass: _Instance())
        series = {
            SHARE: {
                "unit": "%",
                "slot_values": [(self.A, 100.0), (self.B, 0.0)],
                "weights": {self.A: 100.0, self.B: 300.0},
            }
        }
        await _history._write_recorder_statistics(object(), series, include_long_term=True)
        short, long_term = recorded
        assert [row["mean"] for row in short] == [100.0, 0.0]  # the slot shares as they are
        assert [row["mean"] for row in long_term] == [pytest.approx(25.0)]


class TestFullAndRecentRecalculationAgree:
    @staticmethod
    def _world() -> _World:
        world = _World()
        slots = [_ts(9, 0) + timedelta(minutes=5 * i) for i in range(60)]
        _add_power(world, PV_A, {s: 100.0 + 40.0 * (i % 7) for i, s in enumerate(slots)})
        _add_power(world, GRID, {s: 300.0 - 15.0 * (i % 5) for i, s in enumerate(slots)})
        _add_power(world, OUT, {s: 350.0 + 5.0 * (i % 11) for i, s in enumerate(slots)})
        return world

    async def test_the_timer_and_the_full_recalculation_leave_identical_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._world()
        _install(monkeypatch, world)
        options = _opts([PV_A], [OUT], [GRID])
        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(12, 5, 5))
        full = await _run_full_recalc(world, options, _ts(12, 5, 5))
        assert SHARE in timer and SHARE in full
        assert _equivalent(timer, full, _ts(10, 0), _ts(12, 5))

    async def test_the_live_value_is_the_last_completed_slot_in_percent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._world()
        _install(monkeypatch, world)
        timer = await _run_timer_cycles(
            world, _opts([PV_A], [OUT], [GRID]), _ts(12, 0, 5), _ts(12, 30, 5)
        )
        last = world.cycles[-1]
        assert SHARE in last.touched
        assert last.last_values[SHARE] == (timer[SHARE][_ts(12, 25)], "%")


class TestTheHourRewriteOfTheSlotJob:
    """``async_rewrite_hourly`` reads the stored 5-minute rows, whose mean of a share would be the
    mean of the slot shares. The share's hour is recomputed over the whole hour instead, with the
    code of the full recalculation, so it is energy-weighted and the same as a full run's."""

    HOUR = _ts(11, 0)

    def _world(self) -> _World:
        world = _World()
        slots = [self.HOUR + timedelta(minutes=5 * i) for i in range(12)]
        # first half of the hour: PV 100 W + grid 100 W (share 50, weight 200);
        # second half: PV 0 + grid 400 W (share 0, weight 400)
        _add_power(world, PV_A, {s: 100.0 if i < 6 else 0.0 for i, s in enumerate(slots)})
        _add_power(world, GRID, {s: 100.0 if i < 6 else 400.0 for i, s in enumerate(slots)})
        _add_power(world, OUT, {s: 200.0 if i < 6 else 400.0 for i, s in enumerate(slots)})
        world.power_means[SHARE] = {
            s: 50.0 if i < 6 else 0.0 for i, s in enumerate(slots)
        }  # what the slot job stored
        world.units[SHARE] = "%"
        return world

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, dict[str, Any]]]:
        written: list[dict[str, dict[str, Any]]] = []

        async def write(_hass: Any, per_sensor: dict[str, dict[str, Any]], **_kw: Any) -> int:
            written.append({sid: dict(info) for sid, info in per_sensor.items()})
            return 0

        monkeypatch.setattr(_history, "_write_recorder_statistics", write)
        return written

    async def test_the_share_of_the_hour_is_weighted_by_the_energy_of_its_slots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._world()
        _install(monkeypatch, world)
        written = self._capture(monkeypatch)

        count = await _history.async_rewrite_hourly(
            object(),
            {SHARE: "%"},
            self.HOUR,
            entry_options=_opts([PV_A], [OUT], [GRID]),
        )

        assert count == 1
        info = written[0][SHARE]
        assert len(info["slot_values"]) == 12
        rows = _history._hourly_rows(info["slot_values"], info["weights"])
        # 6 slots of 50 % x 200 W and 6 of 0 % x 400 W: 600 of 3600 W = 16.67 %, not 25 %
        assert rows[self.HOUR][0] == pytest.approx(100 * 600 / 3600)

    async def test_the_other_statistics_of_the_hour_still_come_from_the_stored_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._world()
        world.power_means["sensor.effy_x"] = {self.HOUR: 2.0}
        world.units["sensor.effy_x"] = "kW"
        _install(monkeypatch, world)
        written = self._capture(monkeypatch)
        count = await _history.async_rewrite_hourly(
            object(),
            {SHARE: "%", "sensor.effy_x": "kW"},
            self.HOUR,
            entry_options=_opts([PV_A], [OUT], [GRID]),
        )
        assert count == 2
        assert set(written[0]) == {SHARE, "sensor.effy_x"}
        assert written[0]["sensor.effy_x"]["slot_values"] == [(self.HOUR, 2.0)]
        assert "weights" not in written[0]["sensor.effy_x"]

    async def test_without_the_options_the_share_is_not_written_rather_than_wrong(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._world()
        _install(monkeypatch, world)
        written = self._capture(monkeypatch)
        assert await _history.async_rewrite_hourly(object(), {SHARE: "%"}, self.HOUR) == 0
        assert written == []

    async def test_an_hour_the_inputs_have_no_data_for_writes_no_share(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._world()
        world.power_means[PV_A] = {}
        world.power_means[GRID] = {}
        world.power_means[OUT] = {}
        _install(monkeypatch, world)
        written = self._capture(monkeypatch)
        count = await _history.async_rewrite_hourly(
            object(), {SHARE: "%"}, self.HOUR, entry_options=_opts([PV_A], [OUT], [GRID])
        )
        assert count == 0 and written == []


class _StoredRows:
    """A storage that records what ``unchanged_rows`` is asked and answers: every 5-minute row
    unchanged except those named in ``changed``; the stored hourly rows as given."""

    def __init__(
        self,
        changed: dict[str, set[datetime]] | None = None,
        hourly_unchanged: frozenset[datetime] = frozenset(),
    ) -> None:
        self.changed = changed or {}
        self.hourly_unchanged = hourly_unchanged
        self.asked: dict[str, dict[datetime, tuple[float, float]]] = {}

    async def unchanged_rows(
        self,
        statistic_id: str,
        _unit: str,
        short: list[tuple[datetime, float]],
        hourly: dict[datetime, tuple[float, float]],
        _notes: Any = None,
        values: bool = False,
    ) -> dict[str, Any]:
        self.asked[statistic_id] = hourly
        gone = self.changed.get(statistic_id, set())
        return {
            "short": frozenset(slot for slot, _v in short if slot not in gone),
            "hourly": self.hourly_unchanged,
        }


class TestWhichHoursOfTheShareAreUnchangedInAHistoryRun:
    """ADR-024 Amendment 2026-10-26: a history run reads no hourly row; an hour is rewritten
    only if a change was written in it. The hourly share is energy-weighted, so a change in the
    rows of an input it is computed from counts as a change in the hour, exactly as a change in
    its own rows does; no hourly row is read for that (TASK-0046)."""

    HOUR = _ts(11, 0)
    OTHER_HOUR = _ts(12, 0)
    SOURCE = "sensor.effy_pv_a_w"
    SLOTS = [HOUR, HOUR + timedelta(minutes=5), OTHER_HOUR]

    def _series(self, share: list[float] | None = None) -> dict[str, dict[str, Any]]:
        values = share or [100.0, 0.0, 100.0]  # not uniform in hour 11
        return {
            SHARE: {
                "unit": "%",
                "slot_values": list(zip(self.SLOTS, values, strict=True)),
                "weights": {slot: 100.0 for slot in self.SLOTS},
                "sources": [self.SOURCE, "sensor.effy_not_in_this_run"],
            },
            self.SOURCE: {"unit": "W", "slot_values": [(s, 50.0) for s in self.SLOTS]},
            "sensor.effy_unrelated": {"unit": "W", "slot_values": [(s, 1.0) for s in self.SLOTS]},
        }

    async def _mark(
        self, series: dict[str, dict[str, Any]], storage: _StoredRows, **kwargs: Any
    ) -> None:
        options: dict[str, Any] = {
            "short_term_cutoff": None,
            "include_long_term": True,
            "include_short_term": True,
            "phases": _history._NO_TIMINGS,
            "storage": storage,
        } | kwargs
        await _history._mark_unchanged(object(), (series,), **options)

    async def test_no_hourly_row_is_read_for_the_share_in_a_history_run(self) -> None:
        series, storage = self._series(), _StoredRows()
        await self._mark(series, storage)
        assert storage.asked[SHARE] == {}
        assert all(hourly == {} for hourly in storage.asked.values())

    async def test_nothing_changed_nothing_is_written(self) -> None:
        series = self._series()
        await self._mark(series, _StoredRows())
        assert series[SHARE]["unchanged_hourly"] == frozenset({self.HOUR, self.OTHER_HOUR})

    async def test_a_changed_row_of_the_share_itself_changes_its_hour_only(self) -> None:
        series = self._series()
        await self._mark(series, _StoredRows({SHARE: {self.HOUR}}))
        assert series[SHARE]["unchanged_hourly"] == frozenset({self.OTHER_HOUR})

    async def test_a_changed_row_of_a_source_changes_the_hour_though_the_share_rows_are_equal(
        self,
    ) -> None:
        """The weights may have changed while a slot's share stayed the same (e.g. 0 -> 0)."""
        series = self._series()
        await self._mark(series, _StoredRows({self.SOURCE: {self.HOUR + timedelta(minutes=5)}}))
        assert series[SHARE]["unchanged_hourly"] == frozenset({self.OTHER_HOUR})
        # while the hour of an unrelated series is judged by its own rows, as before
        assert series["sensor.effy_unrelated"]["unchanged_hourly"] == frozenset(
            {self.HOUR, self.OTHER_HOUR}
        )

    async def test_an_hour_in_which_every_slot_has_the_same_share_ignores_the_weights(
        self,
    ) -> None:
        """A system with PV only is at 100 % all day: the weighted mean is 100 whatever the
        weights are, so a changed source row alone does not rewrite the hour."""
        series = self._series(share=[100.0, 100.0, 100.0])
        await self._mark(series, _StoredRows({self.SOURCE: {self.HOUR}}))
        assert series[SHARE]["unchanged_hourly"] == frozenset({self.HOUR, self.OTHER_HOUR})

    async def test_a_changed_row_of_the_share_counts_even_in_a_uniform_hour(self) -> None:
        series = self._series(share=[100.0, 100.0, 100.0])
        await self._mark(series, _StoredRows({SHARE: {self.HOUR}}))
        assert series[SHARE]["unchanged_hourly"] == frozenset({self.OTHER_HOUR})

    async def test_a_slot_older_than_the_short_term_cutoff_counts_as_changed(self) -> None:
        """No stored 5-minute row exists to compare: ``unchanged_short`` lacks it."""
        series = self._series()
        await self._mark(series, _StoredRows(), short_term_cutoff=self.OTHER_HOUR)
        assert series[SHARE]["unchanged_hourly"] == frozenset({self.OTHER_HOUR})

    async def test_the_other_series_keep_the_rule_of_the_5_minute_rows(self) -> None:
        series = self._series()
        await self._mark(series, _StoredRows({"sensor.effy_unrelated": {self.HOUR}}))
        assert series["sensor.effy_unrelated"]["unchanged_hourly"] == frozenset({self.OTHER_HOUR})
        assert series[SHARE]["unchanged_hourly"] == frozenset({self.HOUR, self.OTHER_HOUR})

    async def test_the_hour_rewrite_of_the_slot_job_compares_the_stored_hourly_row(self) -> None:
        """Without 5-minute rows to go by, the hour rewrite reads the stored hourly rows for
        every series; the share's expected row is the energy-weighted one."""
        series = {SHARE: self._series()[SHARE]}
        series[SHARE]["slot_values"] = [(self.HOUR, 100.0), (self.HOUR + timedelta(minutes=5), 0.0)]
        series[SHARE]["weights"] = {self.HOUR: 100.0, self.HOUR + timedelta(minutes=5): 300.0}
        storage = _StoredRows(hourly_unchanged=frozenset({self.HOUR}))
        await self._mark(series, storage, include_short_term=False)
        assert storage.asked[SHARE] == {self.HOUR: (pytest.approx(25.0), 0.0)}
        assert series[SHARE]["unchanged_hourly"] == frozenset({self.HOUR})
