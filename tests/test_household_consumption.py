"""
The household consumption series and the sensor "Household consumption now" (TASK-0049, CAP-15,
ADR-029 decision 3).

Per slot, in W: ``max(0, hi - he + system export + EPS output - system import)``, from the series
before the loss. It is a member of ``per_sensor`` of ``history._compute_effective_slots`` written
like the PV sum, so the writer, the stored-rows check (ADR-024), the hourly rows and the live push
treat it like any series. The pure formula has its own tests in ``tests/test_calculation.py``;
here the real ``history.py`` runs against the in-memory recorder world of
``test_history_recent_equivalence`` (and, for stored rows and the hourly row, the real writer
against the stateful recorder of ``test_history_write_only_changed``). The sensor tests load the
real ``sensor.py`` the way ``test_standby_sensor`` does.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.test_additional_sensors_series import _compute, _slots
from tests.test_history_recent_equivalence import (
    _const,
    _equivalent,
    _FrozenDatetime,
    _history,
    _install,
    _options,
    _run_full_recalc,
    _run_timer_cycles,
    _SensorStateClass,
    _ts,
    _World,
)
from tests.test_history_write_only_changed import (
    REAL_UNITS,
    REAL_WRITE,
    StoredRecorder,
    _install_ha_api,
    _snapshot,
)
from tests.test_published_sensors import _Coordinator, _Entry, _last
from tests.test_sensor_setup import ENERGY, _Platform, _SetupEntry, _state
from tests.test_standby_sensor import _Registry, _registry, _sensor

HI_KEY = _const.CONF_HOUSEHOLD_IMPORT_SENSOR
HE_KEY = _const.CONF_HOUSEHOLD_EXPORT_SENSOR
CONSUMPTION = _const.HOUSEHOLD_CONSUMPTION_ENTITY_ID

PV = "sensor.pv_w"  # PV input
SYS_IMPORT = "sensor.bms_grid_in"  # the system's grid import counter (a BMS input)
DISCHARGE = "sensor.bms_discharge"  # the other BMS input
CHARGE = "sensor.bms_charge"  # an output
SYS_EXPORT = "sensor.bms_grid_out"  # the system's grid export counter (an output)
EPS = "sensor.eps_w"  # the signed EPS sensor (an input, ADR-026)
HI = "sensor.house_import"
HE = "sensor.house_export"

ALL_SLOTS = _slots(0, 48)  # 09:00 .. 12:55, the window of ``_compute``


def _options_for(
    *, household: dict[str, str] | None = None, eps: bool = False, outputs: bool = True
) -> dict[str, Any]:
    options = _options([PV], [CHARGE, SYS_EXPORT] if outputs else [], [SYS_IMPORT, DISCHARGE])
    if eps:
        options[_const.CONF_EPS_SENSOR] = EPS
    options.update(household or {})
    return options


def _power_world(**sensors: Any) -> _World:
    """Power-family sensors: a constant, ``(constant, unit)`` or an explicit ``{slot: value}``."""
    world = _World()
    for eid, spec in sensors.items():
        if isinstance(spec, dict):
            means, unit = spec, "W"
        elif isinstance(spec, tuple):
            means, unit = {slot: spec[0] for slot in ALL_SLOTS}, spec[1]
        else:
            means, unit = {slot: spec for slot in ALL_SLOTS}, "W"
        world.power_means[eid] = means
        world.units[eid] = unit
        world.state_classes[eid] = _SensorStateClass.MEASUREMENT
    return world


def _consumption(per_sensor: dict[str, Any]) -> dict[datetime, float]:
    info = per_sensor.get(CONSUMPTION)
    return dict(info["slot_values"]) if info else {}


def _effective(per_sensor: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in per_sensor.items() if k != CONSUMPTION}


async def _consumption_of(
    monkeypatch: pytest.MonkeyPatch, world: _World, options: dict[str, Any]
) -> dict[datetime, float]:
    eff, _power, _smooth = await _compute(monkeypatch, world, options)
    return _consumption(eff)


# A system in a steady state: every slot gives the example of the task.
STEADY: dict[str, float] = {
    PV: 500.0,
    SYS_IMPORT: 100.0,
    DISCHARGE: 0.0,
    CHARGE: 600.0,
    SYS_EXPORT: 200.0,
    EPS: 50.0,
}


class TestTheFormula:
    async def test_the_example_of_the_task_is_450_w_in_every_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # hi 300 W, he 0, system export 200 W, EPS output 50 W, system import 100 W
        world = _power_world(**STEADY, **{HI: 300.0})
        options = _options_for(household={HI_KEY: HI}, eps=True)
        eff, _power, _smooth = await _compute(monkeypatch, world, options)
        assert eff[CONSUMPTION]["unit"] == "W"
        assert dict(eff[CONSUMPTION]["slot_values"]) == pytest.approx(
            dict.fromkeys(ALL_SLOTS, 450.0)
        )

    async def test_the_household_export_is_subtracted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _power_world(**STEADY, **{HI: 300.0, HE: 120.0})
        options = _options_for(household={HI_KEY: HI, HE_KEY: HE}, eps=True)
        values = await _consumption_of(monkeypatch, world, options)
        assert set(values.values()) == {330.0}  # 300 - 120 + 200 + 50 - 100

    async def test_readings_in_kw_and_in_w_add_up_after_normalisation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _power_world(**{**STEADY, SYS_IMPORT: (0.1, "kW")}, **{HI: (0.3, "kW"), HE: 20.0})
        options = _options_for(household={HI_KEY: HI, HE_KEY: HE}, eps=True)
        values = await _consumption_of(monkeypatch, world, options)
        # 300 - 20 + 200 + 50 - 100, the kW readings normalised to W
        assert values and all(v == pytest.approx(430.0) for v in values.values())

    async def test_the_eps_output_adds_and_the_eps_input_is_not_subtracted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = _options_for(household={HI_KEY: HI}, eps=True)
        without = await _consumption_of(
            monkeypatch,
            _power_world(**{**STEADY, EPS: 0.0}, **{HI: 300.0}),
            options,
        )
        output = await _consumption_of(
            monkeypatch, _power_world(**{**STEADY, EPS: 70.0}, **{HI: 300.0}), options
        )
        input_ = await _consumption_of(
            monkeypatch, _power_world(**{**STEADY, EPS: -70.0}, **{HI: 300.0}), options
        )
        assert set(without.values()) == {400.0}
        assert set(output.values()) == {470.0}  # the positive part leaves the system: household
        assert set(input_.values()) == {400.0}  # the negative part is not subtracted

    async def test_without_an_eps_sensor_or_a_grid_import_the_terms_are_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _power_world(**{PV: 500.0, SYS_EXPORT: 200.0, CHARGE: 600.0}, **{HI: 300.0})
        options = _options([PV], [CHARGE, SYS_EXPORT], []) | {HI_KEY: HI}
        values = await _consumption_of(monkeypatch, world, options)
        assert set(values.values()) == {500.0}  # 300 + 200

    async def test_only_the_grid_counters_and_the_eps_output_count_not_the_other_sensors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The PV input, the discharge counter and the charge output are in the loss balance
        and in no way in this one: the balance is at the system's AC port."""
        base = _power_world(**STEADY, **{HI: 300.0})
        shifted = _power_world(
            **{**STEADY, PV: 5000.0, DISCHARGE: 700.0, CHARGE: 9000.0}, **{HI: 300.0}
        )
        options = _options_for(household={HI_KEY: HI}, eps=True)
        assert await _consumption_of(monkeypatch, shifted, options) == await _consumption_of(
            monkeypatch, base, options
        )

    async def test_it_uses_the_series_before_the_loss_never_the_effective_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """1000 W come in through the grid counter and nothing goes out: the whole input is
        loss, so its effective value is 0 W. The consumption still takes the 1000 W."""
        world = _power_world(
            **{PV: 0.0, SYS_IMPORT: 1000.0, DISCHARGE: 0.0, CHARGE: 0.0, SYS_EXPORT: 0.0},
            **{HI: 1500.0},
        )
        options = _options_for(household={HI_KEY: HI})
        eff, _power, _smooth = await _compute(monkeypatch, world, options)
        effective_import = dict(eff[_history._effy_entity_id(SYS_IMPORT)]["slot_values"])
        assert set(effective_import.values()) == {0.0}  # the loss took all of it
        assert set(_consumption(eff).values()) == {500.0}  # 1500 - 1000, not 1500 - 0

    async def test_a_negative_sum_is_zero_not_negative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _power_world(**{**STEADY, SYS_IMPORT: 900.0}, **{HI: 300.0})
        options = _options_for(household={HI_KEY: HI}, eps=True)
        values = await _consumption_of(monkeypatch, world, options)
        assert set(values.values()) == {0.0}  # 300 + 200 + 50 - 900 < 0
        assert all(str(v) == "0.0" for v in values.values())  # no negative zero


class TestTheSignedImport:
    """ADR-029 decision 2: only an import with no export sensor is split by its sign."""

    BASE = {**STEADY, EPS: 0.0, SYS_IMPORT: 0.0}  # system import 0, export 200, EPS 0

    def _world(self, hi: float, **more: float) -> _World:
        return _power_world(**{**self.BASE, **more}, **{HI: hi})

    async def test_a_negative_import_without_an_export_sensor_is_the_household_export(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = _options_for(household={HI_KEY: HI}, eps=True)
        values = await _consumption_of(monkeypatch, self._world(-120.0), options)
        assert set(values.values()) == {80.0}  # hi 0, he 120: 0 - 120 + 200 - 0

    async def test_a_positive_import_is_the_household_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = _options_for(household={HI_KEY: HI}, eps=True)
        values = await _consumption_of(monkeypatch, self._world(300.0), options)
        assert set(values.values()) == {500.0}  # 300 - 0 + 200 - 0

    async def test_the_sign_changes_inside_one_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        swing = {slot: (-100.0 if i % 2 else 100.0) for i, slot in enumerate(ALL_SLOTS)}
        options = _options_for(household={HI_KEY: HI}, eps=True)
        world = _power_world(**{**self.BASE}, **{HI: swing})
        values = await _consumption_of(monkeypatch, world, options)
        assert {values[s] for s in ALL_SLOTS[0::2]} == {300.0}  # +100: 100 - 0 + 200
        assert {values[s] for s in ALL_SLOTS[1::2]} == {100.0}  # -100: 0 - 100 + 200

    async def test_a_negative_import_makes_no_negative_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = _options_for(household={HI_KEY: HI}, eps=True)
        world = self._world(-500.0, **{SYS_EXPORT: 0.0})  # 0 - 500 + 0 - 0 < 0
        assert set((await _consumption_of(monkeypatch, world, options)).values()) == {0.0}

    async def test_with_an_export_sensor_a_negative_import_counts_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = _options_for(household={HI_KEY: HI, HE_KEY: HE}, eps=True)
        world = _power_world(**self.BASE, **{HI: -120.0, HE: 30.0})
        values = await _consumption_of(monkeypatch, world, options)
        assert set(values.values()) == {170.0}  # hi 0 (not split), he 30: 0 - 30 + 200

    async def test_with_an_export_sensor_a_negative_export_counts_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = _options_for(household={HI_KEY: HI, HE_KEY: HE}, eps=True)
        world = _power_world(**self.BASE, **{HI: 300.0, HE: -50.0})
        values = await _consumption_of(monkeypatch, world, options)
        assert set(values.values()) == {500.0}  # 300 - 0 + 200

    async def test_a_counter_without_an_export_sensor_gives_an_export_of_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An energy counter is never split by a sign: a counter step that is not an increase
        is no household export. Its derived power is 0 or more, so the result is the import."""
        world = self._world(0.0)
        world.power_means.pop(HI)
        world.energy[HI] = [
            (_ts(8, 55) + timedelta(minutes=5 * i), f"{1000 + 25 * i}.0") for i in range(60)
        ]
        world.units[HI] = "Wh"
        world.state_classes[HI] = _SensorStateClass.TOTAL_INCREASING
        options = _options_for(household={HI_KEY: HI}, eps=True)
        eff, _power, _smooth = await _compute(monkeypatch, world, options)
        inside = [v for s, v in _consumption(eff).items() if _ts(10, 0) <= s < _ts(12, 0)]
        assert inside and inside == pytest.approx([500.0] * len(inside))  # 25 Wh/slot = 300 W


class TestTheSlotsOfTheSeries:
    async def test_a_slot_without_a_household_import_value_has_no_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A gap of up to two slots is bridged (ADR-013 decision 2), a longer one stays a gap:
        no row, whatever the other terms are."""
        means = {slot: 300.0 for slot in ALL_SLOTS}
        for slot in _slots(6, 2) + _slots(20, 3):  # a gap of 2 slots and one of 3 slots
            del means[slot]
        world = _power_world(**STEADY, **{HI: means})
        options = _options_for(household={HI_KEY: HI}, eps=True)
        values = await _consumption_of(monkeypatch, world, options)
        assert all(slot in values for slot in _slots(6, 2))  # bridged: a row
        assert not any(slot in values for slot in _slots(20, 3))  # a gap: no row
        assert values[_slots(6, 1)[0]] == 450.0

    async def test_any_other_missing_term_counts_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The system's grid export counter has no value in four slots (a gap longer than the
        bridge, the output is not smoothed at all): the slot keeps a row without that term."""
        export = {slot: 200.0 for slot in ALL_SLOTS if slot not in _slots(10, 4)}
        world = _power_world(**{**STEADY, EPS: 0.0, SYS_EXPORT: export}, **{HI: 300.0})
        options = _options_for(household={HI_KEY: HI}, eps=True)
        values = await _consumption_of(monkeypatch, world, options)
        assert {values[s] for s in _slots(10, 4)} == {200.0}  # 300 - 100, no export term
        assert values[_slots(9, 1)[0]] == 400.0  # 300 + 200 - 100

    async def test_a_slot_that_only_the_household_import_has_still_has_a_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The system's sensors have data in the morning only; the household meter all day. The
        series is not limited to the waterfall's slots: later slots have ``hi`` and no other
        term, while the effective rows stop where the inputs' data stops."""
        morning = {slot: 100.0 for slot in _slots(0, 6)}
        world = _power_world(
            **{PV: morning, SYS_IMPORT: morning, DISCHARGE: morning, CHARGE: morning},
            **{SYS_EXPORT: morning, HI: 300.0},
        )
        options = _options_for(household={HI_KEY: HI})
        eff, _power, _smooth = await _compute(monkeypatch, world, options)
        values = _consumption(eff)
        assert set(values) == set(ALL_SLOTS)
        assert {values[s] for s in _slots(6, 42)} == {300.0}  # hi alone
        assert {values[s] for s in _slots(0, 6)} == {300.0}  # 300 + 100 (export) - 100 (import)
        effective_slots = {
            slot for info in _effective(eff).values() for slot, _v in info["slot_values"]
        }
        assert max(effective_slots) == _slots(5, 1)[0]

    async def test_with_no_input_or_output_row_at_all_the_series_still_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _power_world(**{HI: 300.0})
        for sensor in (PV, SYS_IMPORT, DISCHARGE, CHARGE, SYS_EXPORT):
            world.units[sensor] = "W"
            world.state_classes[sensor] = _SensorStateClass.MEASUREMENT
        options = _options_for(household={HI_KEY: HI})
        eff, _power, _smooth = await _compute(monkeypatch, world, options)
        assert set(eff) == {CONSUMPTION}  # nothing effective, the consumption all the same
        assert set(_consumption(eff).values()) == {300.0}

    async def test_no_household_import_means_no_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _power_world(**STEADY, **{HI: 300.0, HE: 20.0})
        for options in (
            _options_for(eps=True),  # none configured
            _options_for(household={HE_KEY: HE}, eps=True),  # an export alone: no household sensor
            _options_for(household={HI_KEY: PV}, eps=True),  # a role sensor: no household sensor
        ):
            eff, _power, _smooth = await _compute(monkeypatch, world, options)
            assert CONSUMPTION not in eff

    async def test_the_household_export_without_a_value_counts_as_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        export = {slot: 50.0 for slot in ALL_SLOTS if slot not in _slots(30, 5)}
        world = _power_world(**{**STEADY, EPS: 0.0}, **{HI: 300.0, HE: export})
        options = _options_for(household={HI_KEY: HI, HE_KEY: HE}, eps=True)
        values = await _consumption_of(monkeypatch, world, options)
        assert values[_slots(29, 1)[0]] == 350.0  # 300 - 50 + 200 - 100
        assert {values[s] for s in _slots(30, 5)} == {400.0}  # no export term


class TestInNoLossCalculation:
    async def test_the_effective_rows_are_identical_with_and_without_the_household_sensors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plain = _options_for(eps=True)
        world = _power_world(**STEADY, **{HI: 300.0, HE: 20.0})
        eff0, power0, smooth0 = await _compute(monkeypatch, world, plain)
        for household in ({HI_KEY: HI}, {HI_KEY: HI, HE_KEY: HE}):
            eff, power, smooth = await _compute(
                monkeypatch, world, _options_for(household=household, eps=True)
            )
            assert eff0 and _effective(eff) == eff0  # every effective row, the PV sum and share
            assert set(eff) - set(eff0) == {CONSUMPTION}  # the only series that comes with them
            assert {k: v for k, v in power.items() if k in power0} == power0
            assert {k: v for k, v in smooth.items() if k in smooth0} == smooth0

    async def test_the_consumption_series_is_not_a_source_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _power_world(**STEADY, **{HI: 300.0})
        eff, power, smooth = await _compute(
            monkeypatch, world, _options_for(household={HI_KEY: HI}, eps=True)
        )
        assert CONSUMPTION not in power and CONSUMPTION not in smooth
        assert CONSUMPTION != _history._effy_entity_id(HI)


def _counter(wh_per_slot: float) -> list[tuple[datetime, str]]:
    """An energy counter in Wh that rises by ``wh_per_slot`` every 5 minutes (08:55 to 14:00)."""
    return [
        (_ts(8, 55) + timedelta(minutes=5 * i), f"{1000.0 + wh_per_slot * i:.1f}")
        for i in range(62)
    ]


def _mixed_world() -> _World:
    """The household import and the system's grid export are counters (300 W and 240 W), the rest
    power sensors: PV 500 W, system import 100 W, EPS output 50 W. Consumption: 490 W."""
    world = _power_world(**{PV: 500.0, SYS_IMPORT: 100.0, DISCHARGE: 0.0, CHARGE: 600.0, EPS: 50.0})
    for sensor, means in world.power_means.items():  # the counters run to 14:00: so do these
        value = next(iter(means.values()))
        world.power_means[sensor] = {slot: value for slot in _slots(0, 64)}
    for sensor, wh in ((HI, 25.0), (SYS_EXPORT, 20.0)):
        world.energy[sensor] = _counter(wh)
        world.units[sensor] = "Wh"
        world.state_classes[sensor] = _SensorStateClass.TOTAL_INCREASING
    return world


MIXED = _options_for(household={HI_KEY: HI}, eps=True)


class TestAllPathsProduceTheSeries:
    async def test_the_values_of_a_counter_and_power_sensor_mix_are_in_w(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        values = await _consumption_of(monkeypatch, _mixed_world(), MIXED)
        inside = [v for slot, v in values.items() if _ts(10, 0) <= slot < _ts(12, 0)]
        assert inside and inside == pytest.approx([490.0] * len(inside))

    async def test_the_slot_timer_and_the_full_recalculation_give_identical_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CAP-1's invariant covers the new series: slot by slot, as a timer would run, and in
        one go over the same slots."""
        world = _mixed_world()
        _install(monkeypatch, world)
        last_cycle = _ts(13, 5, 5)

        timer = await _run_timer_cycles(world, MIXED, _ts(10, 0, 5), last_cycle)
        cycles = list(world.cycles)
        full = await _run_full_recalc(world, MIXED, last_cycle)

        assert timer.get(CONSUMPTION), "the slot timer wrote no row of the series"
        assert full.get(CONSUMPTION)
        assert _equivalent(timer, full, _ts(10, 0), _ts(13, 5))
        assert any(CONSUMPTION in c.touched for c in cycles)
        assert any(c.last_values.get(CONSUMPTION, (0, ""))[1] == "W" for c in cycles)

    async def test_the_slot_job_pushes_the_last_completed_slot_as_the_live_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _mixed_world()
        _install(monkeypatch, world)
        timer = await _run_timer_cycles(world, MIXED, _ts(12, 0, 5), _ts(12, 30, 5))
        last = world.cycles[-1]
        assert CONSUMPTION in last.touched
        assert last.last_values[CONSUMPTION] == (timer[CONSUMPTION][_ts(12, 25)], "W")

    async def test_the_button_reports_the_series_as_touched_with_its_last_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, _mixed_world())
        _FrozenDatetime.frozen = _ts(13, 5, 5)
        result = await _history.async_recalculate_history(object(), MIXED)
        _written, _earliest, touched, last_values, _deltas = result
        assert CONSUMPTION in touched
        value, unit = last_values[CONSUMPTION]
        assert unit == "W" and value == pytest.approx(490.0)

    async def test_a_change_of_the_system_sensors_changes_the_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The recent recalculation and the button see the new series like any other: the same
        options over a world with a bigger grid import give a smaller consumption."""
        bigger = _mixed_world()
        bigger.power_means[SYS_IMPORT] = dict.fromkeys(ALL_SLOTS, 400.0)
        values = await _consumption_of(monkeypatch, bigger, MIXED)
        inside = [v for slot, v in values.items() if _ts(10, 0) <= slot < _ts(12, 0)]
        assert inside == pytest.approx([190.0] * len(inside))


class _Rig:
    """The real writer against the stateful recorder of ``test_history_write_only_changed``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, world: _World) -> None:
        self.world = world
        _install(monkeypatch, world)
        self.recorder = StoredRecorder()
        _install_ha_api(monkeypatch, self.recorder)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: self.recorder)
        monkeypatch.setattr(_history, "_write_recorder_statistics", REAL_WRITE)
        sources = dict(world.units)
        monkeypatch.setattr(
            _history,
            "_get_statistics_units",
            lambda hass, ids: {**sources, **REAL_UNITS(hass, list(ids))},
        )
        _FrozenDatetime.frozen = _ts(13, 5, 5)

    async def run(self) -> Any:
        return await _history.async_recalculate_history(object(), MIXED)


class TestStoredRowsAndTheHourlyRow:
    """ADR-024: a row that is already what Effy would write is not written again."""

    async def test_a_repeated_run_writes_no_row_of_the_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch, _mixed_world())
        await rig.run()
        assert rig.recorder.short(CONSUMPTION), "the first run wrote no row of the series"
        stored, imports = _snapshot(rig.recorder), len(rig.recorder.imports)

        written, earliest, touched, _last, _deltas = await rig.run()

        assert (written, earliest) == (0, None)
        assert len(rig.recorder.imports) == imports and _snapshot(rig.recorder) == stored
        assert CONSUMPTION in touched  # the live state is pushed all the same

    async def test_a_slot_whose_value_changed_is_rewritten_and_the_others_are_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _mixed_world()
        rig = _Rig(monkeypatch, world)
        await rig.run()
        before = {ts: dict(row) for ts, row in rig.recorder.short(CONSUMPTION).items()}
        first_imports = len(rig.recorder.imports)

        # the grid import is 150 W higher in one slot only
        slot = _ts(11, 30)
        world.power_means[SYS_IMPORT] = {s: (250.0 if s == slot else 100.0) for s in _slots(0, 64)}
        await rig.run()

        after = rig.recorder.short(CONSUMPTION)
        changed = {ts for ts in after if after[ts] != before.get(ts)}
        assert slot in changed and after[slot]["mean"] == pytest.approx(340.0)
        assert changed <= {slot, _ts(11, 0)}  # the slot, and the hour of it (long-term row)
        rewritten = [i for i in rig.recorder.imports[first_imports:] if i[1] == CONSUMPTION]
        assert rewritten  # the changed slot was written

    async def test_the_hourly_row_is_the_mean_of_its_slots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch, _mixed_world())
        await rig.run()
        hour = _ts(11, 0)
        slots = [
            rig.recorder.short(CONSUMPTION)[hour + timedelta(minutes=5 * i)]["mean"]
            for i in range(12)
        ]
        row = rig.recorder.hourly(CONSUMPTION)[hour]
        assert row["mean"] == pytest.approx(sum(slots) / 12)
        assert row["sum"] is None  # no running total: ADR-003, a power series has no sum
        assert rig.recorder.units[CONSUMPTION] == "W"

    async def test_a_changed_household_reading_leaves_every_effective_row_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _mixed_world()
        rig = _Rig(monkeypatch, world)
        await rig.run()
        effective = {
            sid: dict(rig.recorder.short(sid))
            for sid in {k[1] for k in rig.recorder.rows}
            if sid.startswith("sensor.effy_")
            and not sid.endswith(("_power", "_smoothed"))
            and sid != CONSUMPTION
        }
        assert effective  # the effective series and the PV sum exist
        world.energy[HI] = _counter(40.0)  # a faster household counter

        await rig.run()

        for sid, before in effective.items():
            assert dict(rig.recorder.short(sid)) == before


# ---------------------------------------------------------------------------
# The sensor
# ---------------------------------------------------------------------------

IMPORT_ID = "sensor.house_import"


class _HouseholdCoordinator(_Coordinator):
    def __init__(self, household_import: str | None = IMPORT_ID) -> None:
        super().__init__()
        self.household_import_id = household_import
        self.household_ids = [household_import] if household_import else []
        self.unsubscribed = 0

    def subscribe_updates(self, entity_id: str, cb: Callable[..., None]) -> Callable[[], None]:
        super().subscribe_updates(entity_id, cb)

        def unsubscribe() -> None:
            self.unsubscribed += 1

        return unsubscribe


def _consumption_sensor(coordinator: _Coordinator | None = None) -> Any:
    return _sensor.EffyHouseholdConsumptionSensor(
        object(), _Entry(), coordinator or _HouseholdCoordinator()
    )


class TestTheSensorsIdentity:
    def test_name_ids_class_and_unit(self) -> None:
        entity = _consumption_sensor()
        assert entity.entity_id == "sensor.effy_household_consumption_now"
        assert entity.unique_id == "entry1_household_consumption_now"
        assert entity._attr_translation_key == "household_consumption_now"
        assert entity._attr_device_class == "power"
        assert entity._attr_state_class == "measurement"
        assert entity._attr_native_unit_of_measurement == "W"
        assert entity._attr_has_entity_name is True
        assert entity.device_info["name"] == "Effy"

    def test_the_statistic_id_is_the_entity_id(self) -> None:
        assert CONSUMPTION == "sensor.effy_household_consumption_now"

    @pytest.mark.parametrize(("lang", "name"), [("en", "Household consumption now")])
    def test_the_translations_name_it(self, lang: str, name: str) -> None:
        base = Path(__file__).resolve().parents[1] / "custom_components" / "effy" / "translations"
        data = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        assert data["entity"]["sensor"]["household_consumption_now"]["name"] == name

    def test_german_has_a_name_too(self) -> None:
        base = Path(__file__).resolve().parents[1] / "custom_components" / "effy" / "translations"
        data = json.loads((base / "de.json").read_text(encoding="utf-8"))
        assert data["entity"]["sensor"]["household_consumption_now"]["name"]


class TestExistence:
    def _created(self, coordinator: _Coordinator) -> list[str]:
        entities = _sensor._household_consumption_entities(object(), _Entry(), coordinator)
        return [e.entity_id for e in entities]

    def test_it_exists_while_a_household_import_is_configured(self) -> None:
        _registry[0] = _Registry({})
        assert self._created(_HouseholdCoordinator()) == [CONSUMPTION]
        assert _registry[0].removed == []

    def test_without_one_it_does_not_and_the_registered_entity_is_removed(self) -> None:
        _registry[0] = _Registry(
            {("sensor", "effy", "entry1_household_consumption_now"): CONSUMPTION}
        )
        assert self._created(_HouseholdCoordinator(None)) == []
        assert _registry[0].removed == [CONSUMPTION]

    def test_nothing_is_removed_that_was_never_registered(self) -> None:
        _registry[0] = _Registry({})
        assert self._created(_HouseholdCoordinator(None)) == []
        assert _registry[0].removed == []

    async def test_the_platform_adds_it_with_the_other_entities(self) -> None:
        _registry[0] = _Registry({})
        entry = _SetupEntry(["sensor.pv_energy"], [])
        platform = _Platform(entry, {"sensor.pv_energy": _state(ENERGY), IMPORT_ID: _state(ENERGY)})
        platform.coordinator.household_import_id = IMPORT_ID
        platform.coordinator.household_ids = [IMPORT_ID]
        await platform.setup()
        names = [type(e).__name__ for batch in platform.added for e in batch]
        assert names.count("EffyHouseholdConsumptionSensor") == 1
        assert names.count("EffyDerivedPowerSensor") >= 1  # the meter's own series entity

    async def test_the_platform_adds_none_without_a_household_import(self) -> None:
        _registry[0] = _Registry({})
        entry = _SetupEntry(["sensor.pv_energy"], [])
        platform = _Platform(entry, {"sensor.pv_energy": _state(ENERGY)})
        await platform.setup()
        names = [type(e).__name__ for batch in platform.added for e in batch]
        assert "EffyHouseholdConsumptionSensor" not in names


class TestLiveStateAndRestore:
    async def test_the_last_slots_value_is_pushed_in_w(self) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _consumption_sensor(coordinator)
        entity.async_write_ha_state = lambda: None
        await entity.async_added_to_hass()
        coordinator.updates[CONSUMPTION](412.3456, "W")
        assert entity._attr_native_value == 412.346
        assert entity._attr_native_unit_of_measurement == "W"

    async def test_a_missing_value_is_unknown_not_a_number(self) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _consumption_sensor(coordinator)
        entity.async_write_ha_state = lambda: None
        await entity.async_added_to_hass()
        coordinator.updates[CONSUMPTION](None, None)
        assert entity._attr_native_value is None

    async def test_it_restores_its_last_state_after_a_restart(self) -> None:
        entity = _consumption_sensor()
        entity.last_state = _last("123.4", {"unit_of_measurement": "W"})
        await entity.async_added_to_hass()
        assert entity._attr_native_value == 123.4

    @pytest.mark.parametrize("state", ["unknown", "unavailable", "not-a-number"])
    async def test_a_last_state_that_is_not_a_number_restores_nothing(self, state: str) -> None:
        entity = _consumption_sensor()
        entity.last_state = _last(state, {"unit_of_measurement": "W"})
        await entity.async_added_to_hass()
        assert entity._attr_native_value is None

    async def test_it_is_not_published_yet_and_unsubscribes_on_removal(self) -> None:
        coordinator = _HouseholdCoordinator()
        entity = _consumption_sensor(coordinator)
        await entity.async_added_to_hass()
        assert coordinator.registry == {}  # TASK-0045f publishes it, not this task
        assert CONSUMPTION in coordinator.updates
        assert coordinator.unsubscribed == 0
        await entity.async_will_remove_from_hass()
        assert coordinator.unsubscribed == 1
        await entity.async_will_remove_from_hass()  # a second removal does nothing
        assert coordinator.unsubscribed == 1
