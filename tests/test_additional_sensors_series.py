"""
The series of the additional sensors in the history computation (TASK-0047, CAP-14, ADR-028).

An additional sensor gets the series an input gets before the loss is distributed, a derived
power series (energy family) or a smoothed series (power family), and takes part in no
formula. The real ``history.py`` runs against the in-memory recorder world of the CAP-1
equivalence test (``tests/test_history_recent_equivalence.py``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tests.test_history_recent_equivalence import (
    _equivalent,
    _energy_world,
    _history,
    _install,
    _options,
    _run_full_recalc,
    _run_timer_cycles,
    _screenshot_readings,
    _SensorStateClass,
    _ts,
    _World,
)

ADDITIONAL = "additional_sensors"
PV = "sensor.pv_a"
OUT = "sensor.bms_charge"
GRID = "sensor.house_grid_import"  # an energy counter of 10 Wh resolution, idle for hours
POWER = "sensor.house_power"  # a power sensor with gaps

GRID_POWER = _history._effy_power_entity_id(GRID)
POWER_SMOOTHED = _history._effy_smoothed_entity_id(POWER)
START, END = _ts(9, 0), _ts(13, 0)

# 10 Wh steps (the counter reads Wh), then nothing for the rest of the day: the idle stretch
# is an explicit 0 (ADR-013)
_GRID_READINGS = [
    (_ts(9, 0), "100.0"),
    (_ts(9, 10), "110.0"),
    (_ts(9, 52), "120.0"),
    (_ts(10, 20), "130.0"),
]
_OUT_READINGS = [(_ts(7, 0), "1.00"), (_ts(9, 40), "1.01")] + [
    (_ts(10, 0) + timedelta(minutes=8 * i), f"{1.01 + 0.01 * (i + 1):.2f}") for i in range(22)
]


def _slots(first: int, count: int) -> list[Any]:
    return [_ts(9, 0) + timedelta(minutes=5 * (first + i)) for i in range(count)]


def _power_means() -> dict[Any, float]:
    """Means for every slot from 09:00 to 13:00 except a gap of 2 slots and one of 3 slots."""
    gap_two, gap_three = set(_slots(6, 2)), set(_slots(20, 3))
    return {
        slot: 100.0 + 5.0 * index
        for index, slot in enumerate(_slots(0, 48))
        if slot not in gap_two | gap_three
    }


def _world(*, with_inputs: bool = True) -> _World:
    world = _energy_world(**{GRID: _GRID_READINGS})
    if with_inputs:
        world.energy[PV] = _screenshot_readings()
        world.energy[OUT] = _OUT_READINGS
        for eid in (PV, OUT):
            world.units[eid] = "kWh"
            world.state_classes[eid] = _SensorStateClass.TOTAL_INCREASING
    world.units[GRID] = "Wh"
    world.units[POWER] = "W"
    world.state_classes[POWER] = _SensorStateClass.MEASUREMENT
    world.power_means[POWER] = _power_means()
    return world


def _with(options: dict[str, Any], *extra: str) -> dict[str, Any]:
    return {**options, ADDITIONAL: list(extra)} if extra else dict(options)


async def _compute(
    monkeypatch: pytest.MonkeyPatch, world: _World, options: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _install(monkeypatch, world)
    return await _history._compute_effective_slots(object(), options, START, END)  # type: ignore[no-any-return]


class TestTheSeriesOfAnAdditionalSensor:
    async def test_an_energy_counter_gets_a_derived_power_series_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = _options([PV], [OUT])
        eff0, power0, smooth0 = await _compute(monkeypatch, _world(), base)
        eff, power, smooth = await _compute(monkeypatch, _world(), _with(base, GRID))

        assert set(power) - set(power0) == {GRID_POWER}
        assert eff == eff0  # no effective series, no loss share
        assert smooth == smooth0
        assert _history._effy_entity_id(GRID) not in eff

    async def test_a_power_sensor_gets_a_smoothed_series_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = _options([PV], [OUT])
        eff0, power0, smooth0 = await _compute(monkeypatch, _world(), base)
        eff, power, smooth = await _compute(monkeypatch, _world(), _with(base, POWER))

        assert set(smooth) - set(smooth0) == {POWER_SMOOTHED}
        assert eff == eff0 and power == power0

    async def test_the_derived_power_is_the_series_an_input_gets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same machinery (ADR-028 decision 2): as an input or as an additional sensor, one
        and the same sensor has the same derived power series."""
        as_input = await _compute(monkeypatch, _world(), _options([PV, GRID], [OUT]))
        as_extra = await _compute(monkeypatch, _world(), _with(_options([PV], [OUT]), GRID))
        assert as_extra[1][GRID_POWER] == as_input[1][GRID_POWER]

    async def test_the_smoothed_series_is_the_series_an_input_gets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_input = await _compute(monkeypatch, _world(), _options([PV, POWER], [OUT]))
        as_extra = await _compute(monkeypatch, _world(), _with(_options([PV], [OUT]), POWER))
        assert as_extra[2][POWER_SMOOTHED] == as_input[2][POWER_SMOOTHED]

    async def test_an_idle_counter_reads_an_explicit_zero_not_a_missing_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _eff, power, _smooth = await _compute(
            monkeypatch, _world(), _with(_options([PV], [OUT]), GRID)
        )
        values = dict(power[GRID_POWER]["slot_values"])
        idle = [_ts(11, 0) + timedelta(minutes=5 * i) for i in range(12)]  # 11:00 .. 11:55
        assert [values[slot] for slot in idle] == [0.0] * 12
        assert max(values) == _ts(12, 55)  # up to the last closed slot before ``END``
        assert any(value > 0 for value in values.values())  # and the ticks are spread out

    async def test_a_low_resolution_counter_is_redistributed_with_energy_conserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _eff, power, _smooth = await _compute(
            monkeypatch, _world(), _with(_options([PV], [OUT]), GRID)
        )
        info = power[GRID_POWER]
        # three 10 Wh steps inside the window (09:10, 09:52, 10:20): 30 Wh = the area in W x h
        energy_wh = sum(value * 5 / 60 for _slot, value in info["slot_values"])
        assert energy_wh == pytest.approx(30.0)

    async def test_a_gap_of_two_slots_is_bridged_and_a_longer_one_stays_a_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _eff, _power, smooth = await _compute(
            monkeypatch, _world(), _with(_options([PV], [OUT]), POWER)
        )
        values = dict(smooth[POWER_SMOOTHED]["slot_values"])
        assert all(slot in values for slot in _slots(6, 2))  # 2 slots: interpolated
        assert not any(slot in values for slot in _slots(20, 3))  # 3 slots: a gap
        assert smooth[POWER_SMOOTHED]["unit"] == "W"

    async def test_the_effective_rows_are_identical_with_and_without_additional_sensors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = _options([PV], [OUT])
        eff0, power0, smooth0 = await _compute(monkeypatch, _world(), base)
        eff, power, smooth = await _compute(monkeypatch, _world(), _with(base, GRID, POWER))
        assert eff and eff == eff0  # every effective row, the PV sum included
        assert {k: v for k, v in power.items() if k != GRID_POWER} == power0
        assert {k: v for k, v in smooth.items() if k != POWER_SMOOTHED} == smooth0

    async def test_the_slots_of_an_additional_sensor_start_no_waterfall_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The PV input (a power sensor: no zero-fill) has data only in the morning; the
        additional sensors have it all day. The effective rows still stop where the input's
        data stops, so no slot of an additional sensor enters the waterfall."""
        base = _options([PV], [OUT])
        world = _world()
        world.energy.pop(PV)
        world.units[PV] = "W"
        world.state_classes[PV] = _SensorStateClass.MEASUREMENT
        world.power_means[PV] = {slot: 80.0 for slot in _slots(0, 6)}  # 09:00 .. 09:25
        eff0, _p, _s = await _compute(monkeypatch, world, base)
        eff, _p, _s = await _compute(monkeypatch, world, _with(base, POWER, GRID))
        assert eff == eff0
        slots = {slot for info in eff.values() for slot, _v in info["slot_values"]}
        assert slots and max(slots) == _ts(9, 25)

    async def test_additional_sensors_alone_still_have_their_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No input or output has a row at all: nothing effective, the series of the additional
        sensors all the same."""
        world = _world(with_inputs=False)
        world.units[PV] = world.units[OUT] = "W"
        world.state_classes[PV] = world.state_classes[OUT] = _SensorStateClass.MEASUREMENT
        options = _with(_options([PV], [OUT]), GRID, POWER)
        eff, power, smooth = await _compute(monkeypatch, world, options)
        assert eff == {}
        assert set(power) == {GRID_POWER} and set(smooth) == {POWER_SMOOTHED}

    async def test_a_sensor_that_is_also_a_role_is_not_an_additional_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hand-edited entry listing a PV input as additional changes nothing."""
        base = _options([PV], [OUT])
        plain = await _compute(monkeypatch, _world(), base)
        listed = await _compute(monkeypatch, _world(), _with(base, PV))
        assert listed == plain

    async def test_no_additional_sensor_changes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = _options([PV], [OUT])
        assert await _compute(monkeypatch, _world(), {**base, ADDITIONAL: []}) == await _compute(
            monkeypatch, _world(), base
        )


class TestSlotJobAndFullRecalculation:
    async def test_the_timer_and_the_full_recalculation_leave_identical_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CAP-1's invariant covers the new series: a counter whose steps are spread over more
        than the window, an idle stretch, and a power sensor with gaps."""
        world = _world()
        world.energy[GRID] = [(_ts(7, 30), "100.0")] + [
            (_ts(9, 35) + timedelta(minutes=23 * i), f"{110.0 + 10.0 * i:.1f}") for i in range(5)
        ]
        _install(monkeypatch, world)
        options = _with(_options([PV], [OUT]), GRID, POWER)

        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(13, 5, 5))
        full = await _run_full_recalc(world, options, _ts(13, 5, 5))

        assert _equivalent(timer, full, _ts(10, 0), _ts(13, 5))
        assert GRID_POWER in timer and POWER_SMOOTHED in timer
        assert any(v == 0.0 for v in timer[GRID_POWER].values())  # the idle zero-fill

    async def test_the_live_value_is_the_last_completed_slot_and_is_pushed_every_cycle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world()
        _install(monkeypatch, world)
        options = _with(_options([PV], [OUT]), GRID, POWER)

        timer = await _run_timer_cycles(world, options, _ts(12, 0, 5), _ts(12, 30, 5))
        last = world.cycles[-1]

        assert {GRID_POWER, POWER_SMOOTHED} <= last.touched
        assert last.last_values[GRID_POWER][0] == timer[GRID_POWER][_ts(12, 25)]
        assert last.last_values[POWER_SMOOTHED][0] == timer[POWER_SMOOTHED][_ts(12, 25)]
        assert last.last_values[POWER_SMOOTHED][1] == "W"

    async def test_the_extra_series_are_written_by_the_same_writer_as_the_inputs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world()
        _install(monkeypatch, world)
        options = _with(_options([PV], [OUT]), GRID, POWER)
        await _run_full_recalc(world, options, _ts(13, 5, 5))
        written = {sid for _lt, _st, series in world.write_calls for sid in series}
        assert {GRID_POWER, POWER_SMOOTHED} <= written
        assert not written & {_history._effy_entity_id(GRID), _history._effy_entity_id(POWER)}
