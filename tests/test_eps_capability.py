"""
CAP-12 acceptance: the EPS/UPS port as a signed power sensor (TASK-0044, ADR-026).

One module that runs the demonstration of the capability on the *real* history recalculation
(``history.async_recalculate_history``) against the in-memory recorder world of
``test_history_recent_equivalence``. The pieces are tested one by one in
``test_calculation`` (the split), ``test_eps_waterfall`` (smoothing, the waterfall, slot job =
full recalculation), ``test_eps_activity`` (standby and efficiency), ``test_catalog``,
``test_published_sensors`` and ``test_effy_exports`` (the published entries). This module
checks that they add up to what the capability promises, with one scenario:

    PV input 1000 W every slot; the charge counter (the only output) and the EPS sensor change
    in three phases of eight slots each:

        phase      charge output   EPS sensor   what it shows
        ---------  --------------  -----------  -------------------------------------------
        exporting  700 W           +200 W       the loss falls from 300 W to 100 W
        importing  900 W           -150 W       the loss rises from 100 W to 250 W, shared
        idle       700 W           0 W          nothing changes against a run without EPS

The expected numbers are written out from the README's formula, never taken from the code:

    total_loss = max(0, Σ inputs + eps_in − Σ outputs − eps_out)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_calculation import SensorReading, distribute_loss, split_eps_readings
from tests.test_catalog import catalog, exports
from tests.test_eps_activity import CHARGING, _rig, classify_slot
from tests.test_eps_waterfall import _add_power
from tests.test_history_recent_equivalence import (
    _const,
    _FrozenDatetime,
    _history,
    _install,
    _options,
    _run_full_recalc,
    _ts,
    _World,
)

PV = "sensor.pv_w"  # power-family PV input
OUT = "sensor.out_w"  # power-family output: the charge counter
EPS = "sensor.eps_w"  # the signed EPS/UPS sensor
GI = "sensor.grid_import_w"  # a BMS input, for the pure-slot scenario

SLOT = timedelta(minutes=5)
SLOTS = [_ts(10, 0) + SLOT * i for i in range(24)]
EXPORTING, IMPORTING, IDLE = SLOTS[:8], SLOTS[8:16], SLOTS[16:]
NOW = _ts(13, 5, 5)
PV_W = 1000.0

# (slots, charge output W, EPS sensor W)
PHASES: list[tuple[list[datetime], float, float]] = [
    (EXPORTING, 700.0, 200.0),
    (IMPORTING, 900.0, -150.0),
    (IDLE, 700.0, 0.0),
]
OUT_W = {slot: out for slots, out, _eps in PHASES for slot in slots}
EPS_W = {slot: eps for slots, _out, eps in PHASES for slot in slots}

PV_EFF = _history._effy_entity_id(PV)
EPS_EFF = _history._effy_entity_id(EPS)
EPS_SMOOTHED = _history._effy_smoothed_entity_id(EPS)

Store = dict[str, dict[datetime, float]]


def _world(*, with_eps_data: bool = True) -> _World:
    world = _World()
    _add_power(world, PV, {slot: PV_W for slot in SLOTS})
    _add_power(world, OUT, dict(OUT_W))
    if with_eps_data:  # the recorder knows the sensor whether or not the entry uses it
        _add_power(world, EPS, dict(EPS_W))
    return world


def _plain_options() -> dict[str, Any]:
    return _options([PV], [OUT])


def _eps_options() -> dict[str, Any]:
    return {**_plain_options(), _const.CONF_EPS_SENSOR: EPS}


async def _recalculate(
    monkeypatch: pytest.MonkeyPatch, world: _World, options: dict[str, Any]
) -> Store:
    """The button: the real history recalculation, and what it left in the recorder."""
    _install(monkeypatch, world)
    return await _run_full_recalc(world, options, NOW)


def _expected_loss(slot: datetime, *, with_eps: bool) -> float:
    """The README formula, written out."""
    eps = EPS_W[slot] if with_eps else 0.0
    eps_in, eps_out = max(-eps, 0.0), max(eps, 0.0)
    return max(0.0, PV_W + eps_in - OUT_W[slot] - eps_out)


def _loss_seen(store: Store, slot: datetime) -> float:
    """The loss as the written series show it: everything that went in and did not come out
    as an effective value."""
    inputs = PV_W + (max(-EPS_W[slot], 0.0) if EPS_EFF in store else 0.0)
    effective = store[PV_EFF][slot] + store.get(EPS_EFF, {}).get(slot, 0.0)
    return inputs - effective


class TestTheDemonstration:
    async def test_an_eps_output_of_200_w_lowers_the_loss_by_200_w(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world()
        with_eps = await _recalculate(monkeypatch, world, _eps_options())
        without = await _recalculate(monkeypatch, world, _plain_options())
        for slot in EXPORTING:
            assert _loss_seen(without, slot) == pytest.approx(300.0)
            assert _loss_seen(with_eps, slot) == pytest.approx(100.0)
            assert _loss_seen(without, slot) - _loss_seen(with_eps, slot) == pytest.approx(200.0)
            assert _loss_seen(with_eps, slot) == pytest.approx(_expected_loss(slot, with_eps=True))
            # the port is exporting: its input half is 0, so it keeps a row and takes no share
            assert with_eps[EPS_EFF][slot] == 0.0
            # the PV input keeps what the backup loads no longer cost it
            assert with_eps[PV_EFF][slot] == pytest.approx(900.0)
            assert without[PV_EFF][slot] == pytest.approx(700.0)

    async def test_an_eps_input_of_150_w_raises_the_input_total_and_takes_an_equal_share(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world()
        with_eps = await _recalculate(monkeypatch, world, _eps_options())
        without = await _recalculate(monkeypatch, world, _plain_options())
        for slot in IMPORTING:
            # the input total: 1000 W of PV plus the 150 W that enter through the port
            assert PV_W + max(-EPS_W[slot], 0.0) == pytest.approx(1150.0)
            assert _loss_seen(without, slot) == pytest.approx(100.0)
            assert _loss_seen(with_eps, slot) == pytest.approx(250.0)
            assert _loss_seen(with_eps, slot) == pytest.approx(_expected_loss(slot, with_eps=True))
            # an equal share of 125 W each: both inputs can bear it
            assert PV_W - with_eps[PV_EFF][slot] == pytest.approx(125.0)
            assert 150.0 - with_eps[EPS_EFF][slot] == pytest.approx(125.0)
            assert with_eps[EPS_EFF][slot] == pytest.approx(25.0)
            assert with_eps[PV_EFF][slot] == pytest.approx(875.0)

    async def test_the_effective_inputs_add_up_to_the_outputs_in_every_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = await _recalculate(monkeypatch, _world(), _eps_options())
        for slot in SLOTS:
            outputs = OUT_W[slot] + max(EPS_W[slot], 0.0)  # the charge counter and the EPS output
            assert store[PV_EFF][slot] + store[EPS_EFF][slot] == pytest.approx(outputs), slot

    async def test_an_idle_port_changes_nothing_against_a_run_without_the_sensor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world()
        with_eps = await _recalculate(monkeypatch, world, _eps_options())
        without = await _recalculate(monkeypatch, world, _plain_options())
        for slot in IDLE:
            assert with_eps[PV_EFF][slot] == pytest.approx(without[PV_EFF][slot])
            assert with_eps[EPS_EFF][slot] == 0.0
            assert with_eps[EPS_SMOOTHED][slot] == 0.0

    async def test_the_signed_series_is_kept_with_its_sign(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = await _recalculate(monkeypatch, _world(), _eps_options())
        assert {slot: store[EPS_SMOOTHED][slot] for slot in SLOTS} == EPS_W


class TestWithoutAnEpsSensorNothingChanges:
    async def test_the_same_sources_give_the_results_they_gave_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # the README's formula before CAP-12: total_loss = max(0, Σ inputs − Σ outputs); one input
        # carries all of it, so its effective value is the smaller of input and output
        plain = await _recalculate(monkeypatch, _world(), _plain_options())
        for slot in SLOTS:
            assert plain[PV_EFF][slot] == pytest.approx(min(PV_W, OUT_W[slot]))
        assert EPS_EFF not in plain and EPS_SMOOTHED not in plain

    async def test_the_sensor_in_the_recorder_does_not_matter_when_the_entry_ignores_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plain = await _recalculate(monkeypatch, _world(), _plain_options())
        bare = await _recalculate(monkeypatch, _world(with_eps_data=False), _plain_options())
        assert plain == bare

    async def test_the_sensor_adds_exactly_its_two_series_and_changes_no_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world()
        with_eps = await _recalculate(monkeypatch, world, _eps_options())
        plain = await _recalculate(monkeypatch, world, _plain_options())
        assert set(with_eps) - set(plain) == {EPS_EFF, EPS_SMOOTHED}
        assert set(plain) <= set(with_eps)


class TestAnActiveEpsSensorMakesASlotImpure:
    """A charging night: the grid import and the charge counter carry the slot, as in a pure
    charge slot. From slot 12 on the EPS sensor reads +40 W. The recalculation writes the
    signed series, the activity reading takes it from there, and the slot is no longer pure
    (ADR-026 decision 5)."""

    SPAN_END = SLOTS[-1] + SLOT

    @staticmethod
    def _options() -> dict[str, Any]:
        return {**_options([], [OUT], [GI]), _const.CONF_EPS_SENSOR: EPS}

    @staticmethod
    def _night() -> _World:
        world = _World()
        _add_power(world, GI, {slot: 1000.0 for slot in SLOTS})
        _add_power(world, OUT, {slot: 900.0 for slot in SLOTS})
        _add_power(world, EPS, {slot: (0.0 if i < 12 else 40.0) for i, slot in enumerate(SLOTS)})
        return world

    async def test_the_slot_with_a_flowing_port_is_not_pure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._night()
        _rig(monkeypatch, world)
        _FrozenDatetime.frozen = NOW
        await _history.async_recalculate_history(object(), self._options())
        assert world.store[EPS_SMOOTHED][SLOTS[12]] == pytest.approx(40.0)

        activity = await _history.async_read_drain_activity(
            object(), self._options(), SLOTS[0], self.SPAN_END
        )
        roles = _history.efficiency_roles(self._options())
        for slot in SLOTS[:12]:  # the port is idle: the control, a pure charge slot
            assert activity[slot] == frozenset({GI, OUT}), slot
            assert classify_slot(activity[slot], roles) == CHARGING, slot
        for slot in SLOTS[12:]:
            assert activity[slot] == frozenset({GI, OUT, EPS}), slot
            assert classify_slot(activity[slot], roles) is None, slot


class TestThePublishedListCarriesBothEntries:
    """What the recalculation writes and what Effy publishes are the same two series."""

    async def test_both_eps_series_are_published_with_role_and_function_eps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = await _recalculate(monkeypatch, _world(), _eps_options())
        written = {PV_EFF: ("effective", PV), EPS_EFF: ("effective", EPS)}
        written[EPS_SMOOTHED] = ("smoothed", EPS)
        assert set(written) <= set(store)  # every published series is one the recalculation wrote

        options = _eps_options()
        descriptors = [
            catalog.describe_source_series(
                entity_id,
                series,
                source,
                pv_input_ids=[PV],
                grid_import_id=None,
                discharge_id=None,
                charge_id=options[_const.CONF_CHARGE_SENSOR],
                grid_export_id=None,
                eps_id=options[_const.CONF_EPS_SENSOR],
            )
            for entity_id, (series, source) in written.items()
        ]
        attributes = catalog.published_attributes(descriptors, 5)
        by_id = {entry["entity_id"]: entry for entry in attributes["sensors"]}

        assert attributes["schema_version"] == exports.EFFY_PUBLISHED_SCHEMA_VERSION == 1
        assert [entry["entity_id"] for entry in attributes["sensors"]] == sorted(by_id)
        effective, smoothed = by_id[EPS_EFF], by_id[EPS_SMOOTHED]
        assert (effective["role"], effective["function"]) == ("eps", "eps")
        assert (smoothed["role"], smoothed["function"]) == ("eps", "eps")
        assert effective["direction"] == "input" and smoothed["direction"] == "both"
        assert effective["source_entity_id"] == smoothed["source_entity_id"] == EPS
        assert effective["series"] == "effective" and smoothed["series"] == "smoothed"
        assert by_id[PV_EFF]["role"] == "pv" and by_id[PV_EFF]["function"] is None  # unchanged


class TestTheReadmeExample:
    """The EPS lines the README adds to its worked example, run through the real functions."""

    @staticmethod
    def _run(eps_w: float) -> Any:
        def reading(entity_id: str, value: float, unit: str) -> Any:
            return SensorReading(entity_id, value, unit)

        inputs = [
            reading("pv_south", 153.0, "W"),
            reading("pv_east", 60.0, "W"),
            reading("pv_north", 7.0, "W"),
            reading("pv_garage", 0.0, "W"),
            reading("bms_bat_import", 5.0, "Wh"),
            reading("bms_grid_import", 0.0, "Wh"),
            reading("eps", eps_w, "W"),
        ]
        outputs = [reading("bms_bat_export", 95.0, "Wh"), reading("bms_grid_export", 100.0, "Wh")]
        split_inputs, split_outputs = split_eps_readings("eps", inputs, outputs)
        return distribute_loss(split_inputs, split_outputs)

    def test_without_the_eps_line_the_loss_is_30_as_in_the_readme(self) -> None:
        assert self._run(0.0).total_loss_w == pytest.approx(30.0)

    def test_an_eps_output_of_20_w_leaves_a_loss_of_10_w(self) -> None:
        result = self._run(20.0)
        assert result.total_loss_w == pytest.approx(10.0)  # max(0, 225 − 195 − 20)
        assert sum(result.effective_values_w.values()) == pytest.approx(215.0)  # 195 + 20

    def test_an_eps_input_of_15_w_raises_the_loss_to_45_w_and_keeps_the_invariant(self) -> None:
        result = self._run(-15.0)
        assert result.total_loss_w == pytest.approx(45.0)  # max(0, 225 + 15 − 195)
        assert result.effective_values_w["eps"] == pytest.approx(4.0)  # it takes 11 W of 15 W
        assert sum(result.effective_values_w.values()) == pytest.approx(195.0)  # = Σ outputs
