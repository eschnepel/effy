"""
The EPS/UPS sensor in the waterfall path (TASK-0041, ADR-026, capability CAP-12).

The real ``history.py`` runs against the in-memory recorder world of
``test_history_recent_equivalence``. Constant power-family sources make the expected
numbers easy to follow: PV 1000 W in, 800 W out, so without an EPS sensor the loss is 200 W
and the PV input's effective value is 800 W.

The EPS sensor is one signed power sensor: positive while energy leaves through the port,
negative while another inverter feeds it. Per slot it is split into an EPS output and an EPS
input (``calculation.split_eps_readings``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_history_recent_equivalence import (
    _const,
    _energy_world,
    _equivalent,
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

PV = "sensor.pv_w"  # power-family input, 1000 W
OUT = "sensor.out_w"  # power-family output (charge counter), 800 W
EPS = "sensor.eps_w"  # the signed EPS/UPS sensor

START = _ts(10, 0, day=10)
END = _ts(12, 0, day=10)
SLOT = timedelta(minutes=5)
SLOTS = [START + SLOT * i for i in range(24)]  # every 5-minute slot of [START, END)

PV_EFF = _history._effy_entity_id(PV)
EPS_EFF = _history._effy_entity_id(EPS)
EPS_SMOOTHED = _history._effy_smoothed_entity_id(EPS)


def _add_power(world: _World, eid: str, rows: dict[datetime, float]) -> None:
    world.units[eid] = "W"
    world.state_classes[eid] = _SensorStateClass.MEASUREMENT
    world.power_means[eid] = dict(rows)


def _world(eps_rows: dict[datetime, float], pv: float = 1000.0, out: float = 800.0) -> _World:
    world = _World()
    _add_power(world, PV, {s: pv for s in SLOTS})
    _add_power(world, OUT, {s: out for s in SLOTS})
    _add_power(world, EPS, eps_rows)
    return world


def _constant(value: float) -> dict[datetime, float]:
    return {s: value for s in SLOTS}


def _options_with_eps(with_eps: bool = True) -> dict[str, Any]:
    options = _options([PV], [OUT])
    if with_eps:
        options[_const.CONF_EPS_SENSOR] = EPS
    return options


async def _compute(
    monkeypatch: pytest.MonkeyPatch, world: _World, options: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    _install(monkeypatch, world)
    result: tuple[
        dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]
    ] = await _history._compute_effective_slots(object(), options, START, END)
    return result


def _series(per_sensor: dict[str, dict[str, Any]], statistic_id: str) -> dict[datetime, float]:
    return {slot: value for slot, value in per_sensor[statistic_id]["slot_values"]}


def _assert_all(series: dict[datetime, float], expected: float) -> None:
    """Every slot of a non-empty series has the expected value."""
    assert series
    assert all(v == pytest.approx(expected) for v in series.values()), series


class TestTheEpsSensorInTheWaterfall:
    async def test_without_the_option_the_sensor_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(_constant(200.0))  # the recorder knows the sensor; the entry does not
        per_sensor, _power, smoothed = await _compute(
            monkeypatch, world, _options_with_eps(with_eps=False)
        )
        assert EPS_EFF not in per_sensor and EPS_SMOOTHED not in smoothed
        _assert_all(_series(per_sensor, PV_EFF), 800.0)

    async def test_an_eps_output_lowers_the_loss_by_its_power(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(_constant(200.0))  # 200 W leave through the port
        per_sensor, _power, _smoothed = await _compute(monkeypatch, world, _options_with_eps())
        # 1000 W in, 800 W + 200 W out: no loss left, the PV input keeps all of it
        _assert_all(_series(per_sensor, PV_EFF), 1000.0)
        # an exporting port is an input of 0: it keeps a row and takes no share
        assert _series(per_sensor, EPS_EFF) == {s: 0.0 for s in SLOTS}

    async def test_an_eps_input_takes_an_equal_share_of_the_loss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(_constant(-300.0))  # another inverter feeds 300 W in
        per_sensor, _power, _smoothed = await _compute(monkeypatch, world, _options_with_eps())
        # 1300 W in, 800 W out: loss 500 W, 250 W each (300 W can bear it)
        _assert_all(_series(per_sensor, EPS_EFF), 50.0)
        _assert_all(_series(per_sensor, PV_EFF), 750.0)

    async def test_an_eps_input_too_small_for_its_share_passes_the_rest_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(_constant(-100.0))
        per_sensor, _power, _smoothed = await _compute(monkeypatch, world, _options_with_eps())
        # 1100 W in, 800 W out: loss 300 W; 100 W absorbs only its own value
        _assert_all(_series(per_sensor, EPS_EFF), 0.0)
        _assert_all(_series(per_sensor, PV_EFF), 800.0)

    async def test_effective_inputs_equal_outputs_in_every_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pattern = [200.0, -300.0, 0.0, 120.0, -100.0, -450.0]
        eps = {s: pattern[i % len(pattern)] for i, s in enumerate(SLOTS)}
        world = _world(eps)
        per_sensor, _power, _smoothed = await _compute(monkeypatch, world, _options_with_eps())
        pv, eps_eff = _series(per_sensor, PV_EFF), _series(per_sensor, EPS_EFF)
        for slot, value in eps.items():
            outputs = 800.0 + max(value, 0.0)
            assert pv[slot] + eps_eff[slot] == pytest.approx(outputs), (slot, value)

    async def test_the_smoothed_series_is_the_signed_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pattern = [200.0, -300.0, 0.0]
        eps = {s: pattern[i % len(pattern)] for i, s in enumerate(SLOTS)}
        world = _world(eps)
        _per_sensor, _power, smoothed = await _compute(monkeypatch, world, _options_with_eps())
        assert _series(smoothed, EPS_SMOOTHED) == eps  # negative values are kept, W
        assert smoothed[EPS_SMOOTHED]["unit"] == "W"

    async def test_a_slot_without_an_eps_reading_has_neither_half(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eps = {s: -300.0 for s in SLOTS if s != SLOTS[10]}
        # a lone missing slot is bridged; a longer hole is not: see TestSmoothingTheSignedSeries
        eps = {s: v for s, v in eps.items() if s not in SLOTS[10:14]}
        world = _world(eps)
        per_sensor, _power, _smoothed = await _compute(monkeypatch, world, _options_with_eps())
        hole = SLOTS[10:14]
        assert not set(hole) & _series(per_sensor, EPS_EFF).keys()
        for slot in hole:  # the loss is the plain 200 W there
            assert _series(per_sensor, PV_EFF)[slot] == pytest.approx(800.0)

    async def test_the_sensor_is_fetched_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = _world(_constant(-300.0))
        _install(monkeypatch, world)
        requested: list[str] = []
        original = _history._fetch_statistics

        def spy(hass: Any, eids: list[str], start: datetime, end: datetime) -> Any:
            requested.extend(eids)
            return original(hass, eids, start, end)

        monkeypatch.setattr(_history, "_fetch_statistics", spy)
        await _history._compute_effective_slots(object(), _options_with_eps(), START, END)
        assert requested.count(EPS) == 1


class TestSmoothingTheSignedSeries:
    """ADR-026 decision 3: gaps of up to two slots are interpolated on the signed series,
    before the split; a longer gap stays a gap."""

    def _rows(
        self, missing: set[int], first: float = 100.0, last: float = -200.0
    ) -> dict[Any, float]:
        # slot 0 is `first`; every later known slot is `last`
        return {s: (first if i == 0 else last) for i, s in enumerate(SLOTS) if i not in missing}

    async def test_a_two_slot_gap_across_a_change_of_sign_is_filled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(self._rows({1, 2}))
        per_sensor, _power, smoothed = await _compute(monkeypatch, world, _options_with_eps())
        filled = _series(smoothed, EPS_SMOOTHED)
        assert filled[SLOTS[0]] == pytest.approx(100.0)
        assert filled[SLOTS[1]] == pytest.approx(0.0)  # +100 -> -200 in three steps
        assert filled[SLOTS[2]] == pytest.approx(-100.0)
        assert filled[SLOTS[3]] == pytest.approx(-200.0)
        pv, eps_eff = _series(per_sensor, PV_EFF), _series(per_sensor, EPS_EFF)
        # both halves see the filled values:
        assert pv[SLOTS[0]] == pytest.approx(900.0)  # EPS output 100 W: loss 100 W
        assert pv[SLOTS[1]] == pytest.approx(800.0)  # 0 W: the plain loss
        assert eps_eff[SLOTS[1]] == 0.0
        assert pv[SLOTS[2]] == pytest.approx(800.0)  # EPS input 100 W absorbs only itself
        assert eps_eff[SLOTS[2]] == pytest.approx(0.0)

    async def test_a_one_slot_gap_is_filled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = _world(self._rows({5}, first=-100.0, last=-100.0) | {SLOTS[0]: -100.0})
        _per_sensor, _power, smoothed = await _compute(monkeypatch, world, _options_with_eps())
        assert _series(smoothed, EPS_SMOOTHED)[SLOTS[5]] == pytest.approx(-100.0)

    async def test_a_three_slot_gap_stays_a_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = _world(self._rows({1, 2, 3}))
        per_sensor, _power, smoothed = await _compute(monkeypatch, world, _options_with_eps())
        hole = {SLOTS[1], SLOTS[2], SLOTS[3]}
        assert not hole & _series(smoothed, EPS_SMOOTHED).keys()
        assert not hole & _series(per_sensor, EPS_EFF).keys()
        for slot in hole:
            assert _series(per_sensor, PV_EFF)[slot] == pytest.approx(800.0)


def _equivalence_world() -> tuple[_World, str, str, str]:
    """An energy-family input and output (the BMS counter screenshot) plus a signed EPS
    sensor that changes sign and has gaps."""
    a, b, c = "sensor.a_in", "sensor.b_out", "sensor.eps_signed"
    world = _energy_world(
        **{
            a: _screenshot_readings(),
            b: [(_ts(7, 0), "1.00"), (_ts(9, 40), "1.01")]
            + [
                (_ts(10, 0) + timedelta(minutes=8 * i), f"{1.01 + 0.01 * (i + 1):.2f}")
                for i in range(22)
            ],
        }
    )
    pattern = [120.0, -80.0, 0.0, 40.0, -200.0]
    missing = {_ts(10, 20), _ts(10, 25), _ts(11, 0)}  # a 2-slot and a 1-slot gap
    rows: dict[datetime, float] = {}
    slot, i = _ts(9, 0), 0
    while slot < _ts(13, 30):
        if slot not in missing:
            rows[slot] = pattern[i % len(pattern)]
        slot += SLOT
        i += 1
    _add_power(world, c, rows)
    return world, a, b, c


class TestSlotTimerEqualsFullRecalcWithTheEpsSensor:
    """The CAP-1 invariant with the EPS sensor: the slot job after each cycle equals a full
    recalculation at the same time (one function serves both paths)."""

    def _opts(self, a: str, b: str, c: str) -> dict[str, Any]:
        options = _options([a], [b])
        options[_const.CONF_EPS_SENSOR] = c
        return options

    async def test_every_series_is_identical_on_both_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world, a, b, c = _equivalence_world()
        _install(monkeypatch, world)
        options = self._opts(a, b, c)
        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(13, 5, 5))
        full = await _run_full_recalc(world, options, _ts(13, 5, 5))

        assert _equivalent(timer, full, _ts(10, 0), _ts(13, 5))
        # the sensor really takes part: without it the input's effective values differ
        plain = await _run_full_recalc(world, _options([a], [b]), _ts(13, 5, 5))
        a_effective = _history._effy_entity_id(a)
        assert any(
            full[a_effective][slot] != pytest.approx(plain[a_effective][slot])
            for slot in plain[a_effective]
            if _ts(10, 0) <= slot < _ts(13, 5)
        )
        effective, smoothed = _history._effy_entity_id(c), _history._effy_smoothed_entity_id(c)
        assert effective in timer and smoothed in timer
        assert any(v < 0 for v in timer[smoothed].values())  # the signed series
        assert {_ts(10, 20), _ts(10, 25), _ts(11, 0)} <= timer[smoothed].keys()  # gaps filled
        assert all(v >= 0 for v in timer[effective].values())  # an effective value is >= 0

    async def test_a_cycle_pushes_both_eps_entities_like_any_power_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world, a, b, c = _equivalence_world()
        _install(monkeypatch, world)
        await _run_timer_cycles(world, self._opts(a, b, c), _ts(12, 15, 5), _ts(12, 15, 5))
        pushed = world.cycles[0].last_values
        effective, smoothed = _history._effy_entity_id(c), _history._effy_smoothed_entity_id(c)
        assert pushed[effective][1] == "W" and pushed[smoothed][1] == "W"
        assert {effective, smoothed} <= world.cycles[0].touched

    async def test_without_the_option_nothing_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world, a, b, c = _equivalence_world()
        _install(monkeypatch, world)
        plain = _options([a], [b])
        baseline = await _run_full_recalc(world, plain, _ts(13, 5, 5))
        # the same recorder with the sensor's data removed gives the same statistics
        without_data = {k: v for k, v in world.power_means.items() if k != c}
        world.power_means = without_data
        world.units.pop(c), world.state_classes.pop(c)
        again = await _run_full_recalc(world, plain, _ts(13, 5, 5))
        assert again == baseline
        assert _history._effy_entity_id(c) not in baseline
