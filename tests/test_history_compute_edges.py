"""
Edges of the history computation and of what a history recalculation writes
(TASK-0028, slices 5c/5d).

The real ``history.py`` runs against the in-memory recorder world of
``test_history_recent_equivalence``. Each test names the series a sensor in an
unusual but legal state does or does not get: statistics stored in another unit
than the live entity reports, an input without statistics, a counter with no
history at all, a recalculation in which only some of the three series exist.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pytest

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

PV = "sensor.pv"  # energy-family input, kWh
OUT = "sensor.out"  # energy-family output, kWh
PW = "sensor.pw"  # power-family input, W
PW2 = "sensor.pw2"  # power-family input, W
NOTHING = "sensor.nothing"  # energy-family, no state ever recorded

START = _ts(10, 0, day=10)
END = _ts(12, 0, day=10)
SLOT = timedelta(minutes=5)


def _ticks(first: float, step: float) -> list[tuple[datetime, str]]:
    return [(START + timedelta(minutes=10 * i), f"{first + step * i:.2f}") for i in range(12)]


def _add_power(world: _World, eid: str, rows: dict[datetime, float] | None = None) -> None:
    world.units[eid] = "W"
    world.state_classes[eid] = _SensorStateClass.MEASUREMENT
    world.power_means[eid] = (
        rows if rows is not None else {START + SLOT * i: 300.0 + i for i in range(22)}
    )


def _logger() -> str:
    return str(_history._LOGGER.name)


class TestStatisticsUnitDiffersFromLiveUnit:
    """Statistics are read in the unit ``statistics_meta`` stores, not the live one
    (``_get_statistics_units``); a difference is logged."""

    async def _run(
        self, monkeypatch: pytest.MonkeyPatch, stats_unit: str | None
    ) -> dict[str, dict[str, Any]]:
        world = _energy_world(**{PV: _ticks(10.0, 0.5)})
        _install(monkeypatch, world)
        if stats_unit is not None:
            monkeypatch.setattr(_history, "_get_statistics_units", lambda _h, _i: {PV: stats_unit})
        per_sensor: dict[str, dict[str, Any]]
        per_sensor, _power, _smoothed = await _history._compute_effective_slots(
            object(), _options([PV]), START, END
        )
        return per_sensor

    async def test_the_statistics_unit_wins_and_the_difference_is_warned(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_logger()):
            per_sensor = await self._run(monkeypatch, "Wh")

        effy = _history._effy_entity_id(PV)
        assert per_sensor[effy]["unit"] == "W"  # Wh, not the live kWh (which would give kW)
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert PV in warnings[0] and "'Wh'" in warnings[0] and "'kWh'" in warnings[0]

    async def test_the_same_unit_in_both_places_is_not_a_difference(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_logger()):
            per_sensor = await self._run(monkeypatch, None)  # the world reports kWh for both
        assert per_sensor[_history._effy_entity_id(PV)]["unit"] == "kW"
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


class TestSensorsWithoutData:
    """A sensor with nothing in the range gets no statistic of its own; the others do."""

    async def _compute(
        self, monkeypatch: pytest.MonkeyPatch, world: _World, options: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        _install(monkeypatch, world)
        result: tuple[
            dict[str, Any], dict[str, Any], dict[str, Any]
        ] = await _history._compute_effective_slots(object(), options, START, END)
        return result

    async def test_a_power_input_without_statistics_has_no_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _energy_world(**{PV: _ticks(10.0, 0.5)})
        _add_power(world, PW, rows={})  # no statistic row at all
        _add_power(world, PW2)
        per_sensor, per_power, smoothed = await self._compute(
            monkeypatch, world, _options([PV, PW, PW2])
        )

        assert _history._effy_entity_id(PW) not in per_sensor
        assert _history._effy_smoothed_entity_id(PW) not in smoothed
        # the control: its sibling with rows has all of them
        assert _history._effy_entity_id(PW2) in per_sensor
        assert _history._effy_smoothed_entity_id(PW2) in smoothed
        assert _history._effy_power_entity_id(PV) in per_power

    async def test_a_power_input_whose_rows_have_no_mean_has_no_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _energy_world(**{PV: _ticks(10.0, 0.5)})
        _add_power(world, PW)
        _install(monkeypatch, world)
        empty_means = [{"start": START + SLOT * i, "mean": None} for i in range(6)]
        monkeypatch.setattr(
            _history,
            "_fetch_statistics",
            lambda _h, eids, _s, _e: {eid: list(empty_means) for eid in eids},
        )

        per_sensor, _power, smoothed = await _history._compute_effective_slots(
            object(), _options([PV, PW]), START, END
        )

        assert _history._effy_entity_id(PW) not in per_sensor
        assert smoothed == {}

    async def test_an_energy_sensor_with_no_history_has_no_effective_or_power_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _energy_world(**{PV: _ticks(10.0, 0.5), NOTHING: []})
        per_sensor, per_power, _smoothed = await self._compute(
            monkeypatch, world, _options([PV, NOTHING])
        )

        assert _history._effy_entity_id(NOTHING) not in per_sensor
        assert _history._effy_power_entity_id(NOTHING) not in per_power
        assert _history._effy_entity_id(PV) in per_sensor
        assert _history._effy_power_entity_id(PV) in per_power

    async def test_nothing_at_all_gives_three_empty_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _energy_world(**{NOTHING: []})
        assert await self._compute(monkeypatch, world, _options([NOTHING])) == ({}, {}, {})


class TestWhichSeriesAHistoryRecalculationWrites:
    """``async_recalculate_history`` writes each of the three series only if it exists."""

    async def _recalc(
        self, monkeypatch: pytest.MonkeyPatch, world: _World, options: dict[str, Any]
    ) -> tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]:
        _install(monkeypatch, world)
        _FrozenDatetime.frozen = END
        outcome: tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]
        outcome = await _history.async_recalculate_history(object(), options)
        return outcome

    async def test_only_a_derived_power_series_when_no_input_has_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every input is silent, the output counter is not: the output's derived
        power is the only series, and the effective one is not written empty."""
        world = _energy_world(**{NOTHING: [], OUT: _ticks(5.0, 0.2)})
        written, _start, touched, last = await self._recalc(
            monkeypatch, world, _options([NOTHING], [OUT])
        )

        power_id = _history._effy_power_entity_id(OUT)
        assert [set(series) for _lt, _st, series in world.write_calls] == [{power_id}]
        assert touched == {power_id}
        assert set(last) == {power_id}
        assert written > 0

    async def test_effective_and_smoothed_only_when_there_is_no_energy_sensor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Power-family sensors have no derived-power series (ADR-012)."""
        world = _World()
        _add_power(world, PW)
        written, _start, touched, _last = await self._recalc(monkeypatch, world, _options([PW]))

        assert [set(series) for _lt, _st, series in world.write_calls] == [
            {_history._effy_entity_id(PW)},
            {_history._effy_smoothed_entity_id(PW)},
        ]
        assert touched == {_history._effy_entity_id(PW), _history._effy_smoothed_entity_id(PW)}
        assert written > 0
