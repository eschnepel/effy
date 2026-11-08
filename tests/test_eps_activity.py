"""
An active EPS/UPS sensor, in either direction, disturbs the standby and efficiency machines
(TASK-0042, ADR-026 decision 5, ADR-018 decisions 2 and 3, ADR-023 decisions 3 and 4).

The EPS sensor is a signed power sensor and a member of the input list. Its activity is read
like that of any power-family input: from its own ``effy_*_smoothed`` statistic, active when the
value is not zero, so a negative value (energy entering through the port) counts exactly as a
positive one. The sensor is not one of the four counters: it makes a slot impure like a PV
input, is exempt from the 5 % rule, breaks a drain interval, is never a drain source and is
never read as a counter.

The pure rules are tested directly; the glue end to end on the simulations of
``test_standby_glue`` and ``test_efficiency_feed`` with an EPS sensor added to them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_efficiency_feed import (
    ETA_C,
    ETA_D,
    GI,
)
from tests.test_efficiency_feed import FIRST as EFF_FIRST
from tests.test_efficiency_feed import LAST as EFF_LAST
from tests.test_efficiency_feed import _cycles as _eff_cycles
from tests.test_efficiency_feed import _opts as _eff_opts
from tests.test_efficiency_feed import _world as _eff_world
from tests.test_history_recent_equivalence import (
    _const,
    _history,
    _install,
    _options,
    _SensorStateClass,
    _World,
)
from tests.test_standby import T0
from tests.test_standby_glue import BMS, OUT, PV
from tests.test_standby_glue import FIRST as SB_FIRST
from tests.test_standby_glue import LAST as SB_LAST
from tests.test_standby_glue import _cycles as _sb_cycles
from tests.test_standby_glue import _opts as _sb_opts
from tests.test_standby_glue import _world as _sb_world

SLOT = timedelta(minutes=5)
EPS = "sensor.eps_w"
_calc = _history.run_feed.__globals__  # the private calculation module history.py loaded
EffRoles, EffSlot = _calc["EffRoles"], _calc["EffSlot"]
classify_slot, significant_flow = _calc["classify_slot"], _calc["_significant_flow"]
CHARGING, DISCHARGING = _calc["EFF_KIND_CHARGE"], _calc["EFF_KIND_DISCHARGE"]


def _hours(a: float, b: float) -> tuple[datetime, datetime]:
    return T0 + timedelta(hours=a), T0 + timedelta(hours=b)


def _add_eps(world: _World, value_at: Callable[[datetime], float], hours: float = 10.0) -> None:
    """The EPS sensor's recorder statistic: one ``mean`` (W) per slot, from ``value_at``."""
    world.units[EPS] = "W"
    world.state_classes[EPS] = _SensorStateClass.MEASUREMENT
    rows: dict[datetime, float] = {}
    slot = T0
    while slot < T0 + timedelta(hours=hours):
        rows[slot] = value_at(slot)
        slot += SLOT
    world.power_means[EPS] = rows


def _rig(monkeypatch: pytest.MonkeyPatch, world: _World) -> None:
    """The faked recorder, serving a power-family *source* from ``power_means`` and every
    statistic Effy wrote (and the standby) from ``store``."""
    _install(monkeypatch, world)

    def fetch(
        _hass: Any, eids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        world.stats_calls.append((start, end))
        served: dict[str, list[dict[str, Any]]] = {}
        for eid in eids:
            source = world.power_means.get(eid)
            rows = source if source is not None else world.store.get(eid, {})
            served[eid] = [
                {"start": s, "mean": v}
                for s, v in sorted(rows.items())
                if start <= s and s + SLOT <= end
            ]
        return served

    monkeypatch.setattr(_history, "_fetch_statistics", fetch)


# ---------------------------------------------------------------------------
# The pure rules
# ---------------------------------------------------------------------------


class TestPureRules:
    ROLES = EffRoles(grid_import="gi", discharge="dis", charge="ch", grid_export="ge")

    def test_a_charging_slot_is_pure_only_without_the_eps_sensor(self) -> None:
        assert classify_slot({"gi", "ch"}, self.ROLES) == CHARGING
        assert classify_slot({"gi", "ch", EPS}, self.ROLES) is None

    def test_a_discharging_slot_is_pure_only_without_the_eps_sensor(self) -> None:
        assert classify_slot({"dis", "ge"}, self.ROLES) == DISCHARGING
        assert classify_slot({"dis", "ge", EPS}, self.ROLES) is None

    def test_the_eps_sensor_alone_is_no_kind(self) -> None:
        assert classify_slot({EPS}, self.ROLES) is None

    def test_the_five_percent_rule_drops_a_late_counter_tick_but_never_the_eps_sensor(
        self,
    ) -> None:
        slot = EffSlot(
            start=T0,
            active=frozenset({"gi", "ch", "dis", EPS}),
            grid_import_kwh=1.0,
            charge_kwh=0.9,
            discharge_kwh=0.01,  # 1 % of the largest counter: a late tick, not a flow
        )
        flow = significant_flow(slot, self.ROLES)
        assert "dis" not in flow  # the control: the rule works
        assert EPS in flow  # a sensor that is no counter always stays
        assert classify_slot(flow, self.ROLES) is None  # so the slot is still not pure

    def test_without_the_eps_sensor_the_same_slot_is_pure(self) -> None:
        slot = EffSlot(
            start=T0,
            active=frozenset({"gi", "ch", "dis"}),
            grid_import_kwh=1.0,
            charge_kwh=0.9,
            discharge_kwh=0.01,
        )
        assert classify_slot(significant_flow(slot, self.ROLES), self.ROLES) == CHARGING


# ---------------------------------------------------------------------------
# The activity reading
# ---------------------------------------------------------------------------


class TestActivityReading:
    START = T0
    END = T0 + timedelta(minutes=30)

    def _options(self) -> dict[str, Any]:
        return {**_options([PV], [OUT], [BMS]), _const.CONF_EPS_SENSOR: EPS}

    async def test_the_eps_sensor_is_active_in_either_direction_and_quiet_at_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _sb_world(discharge=[(T0, "100.00")])
        _add_eps(world, lambda _slot: 0.0, hours=0.5)
        _install(monkeypatch, world)
        s1, s2, s3 = T0 + SLOT, T0 + 2 * SLOT, T0 + 3 * SLOT
        smoothed = _history._effy_smoothed_entity_id(EPS)
        asked: list[list[str]] = []
        rows = {
            smoothed: [
                {"start": s1, "mean": 120.0},  # energy leaves through the port
                {"start": s2, "mean": -80.0},  # energy enters through the port
                {"start": s3, "mean": 0.0},  # idle: known, and quiet
            ]
        }

        def fetch(_h: Any, eids: list[str], _s: datetime, _e: datetime) -> Any:
            asked.append(list(eids))
            return {eid: list(rows.get(eid, [])) for eid in eids}

        monkeypatch.setattr(_history, "_fetch_statistics", fetch)

        activity = await _history.async_read_drain_activity(
            object(), self._options(), self.START, self.END
        )

        assert activity == {s1: frozenset({EPS}), s2: frozenset({EPS}), s3: frozenset()}
        assert smoothed in asked[0] and EPS not in asked[0]  # read from its smoothed series

    async def test_the_eps_sensor_is_not_read_as_a_counter_for_the_efficiency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _eff_world()
        _add_eps(world, lambda _slot: 50.0)
        _install(monkeypatch, world)
        asked: list[list[str]] = []

        def fetch(_h: Any, eids: list[str], _s: datetime, _e: datetime) -> Any:
            asked.append(list(eids))
            return {eid: [] for eid in eids}

        monkeypatch.setattr(_history, "_fetch_statistics", fetch)
        options = {**_eff_opts(), _const.CONF_EPS_SENSOR: EPS}

        roles = _history.efficiency_roles(options)
        assert EPS not in (roles.grid_import, roles.discharge, roles.charge, roles.grid_export)
        await _history.async_read_efficiency_energy(object(), options, T0, T0 + timedelta(hours=1))
        read = {eid for call in asked for eid in call}
        assert EPS not in read and _history._effy_smoothed_entity_id(EPS) not in read
        assert _history._effy_power_entity_id(GI) in read  # the control: the counters are read


# ---------------------------------------------------------------------------
# The standby machine, end to end
# ---------------------------------------------------------------------------


class TestStandbyMachine:
    @staticmethod
    def _quiet_night() -> _World:
        from tests.test_standby import _simulate_counter

        end = T0 + timedelta(hours=8)
        return _sb_world(discharge=_simulate_counter([(T0, end, 12)], T0, end))

    @staticmethod
    def _options() -> dict[str, Any]:
        return {**_sb_opts(), _const.CONF_EPS_SENSOR: EPS}

    async def _state(
        self, monkeypatch: pytest.MonkeyPatch, value_at: Callable[[datetime], float]
    ) -> Any:
        world = self._quiet_night()
        _add_eps(world, value_at)
        _rig(monkeypatch, world)
        return await _sb_cycles(world, self._options(), SB_FIRST, SB_LAST)

    async def test_an_idle_eps_sensor_leaves_the_standby_power_as_it_is(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = await self._state(monkeypatch, lambda _slot: 0.0)
        assert state.estimate == pytest.approx(12.0)
        assert state.count >= 4
        assert state.last_source == BMS  # the drain source is the BMS input, never the EPS sensor

    @pytest.mark.parametrize("watts", [5.0, -5.0])
    async def test_an_active_eps_sensor_breaks_every_drain_interval(
        self, monkeypatch: pytest.MonkeyPatch, watts: float
    ) -> None:
        state = await self._state(monkeypatch, lambda _slot: watts)
        assert state.estimate is None and state.count == 0

    @pytest.mark.parametrize("watts", [5.0, -5.0])
    async def test_an_eps_stretch_costs_only_the_intervals_it_touches(
        self, monkeypatch: pytest.MonkeyPatch, watts: float
    ) -> None:
        control = await self._state(monkeypatch, lambda _slot: 0.0)
        first, last = _hours(3.0, 4.0)
        state = await self._state(monkeypatch, lambda s: watts if first <= s < last else 0.0)
        assert 0 < state.count < control.count
        assert state.estimate == pytest.approx(12.0)

    @pytest.mark.parametrize("watts", [12.0, -12.0])
    async def test_the_eps_sensor_is_never_a_drain_source(
        self, monkeypatch: pytest.MonkeyPatch, watts: float
    ) -> None:
        world = _sb_world(discharge=[(T0, "12.30")])  # the BMS input never moves
        _add_eps(world, lambda _slot: watts)
        _rig(monkeypatch, world)
        state = await _sb_cycles(world, self._options(), SB_FIRST, SB_LAST)
        assert state.estimate is None and state.count == 0


class TestEpsSensorIsNoBmsInput:
    async def test_an_eps_sensor_without_a_bms_input_does_not_enable_the_standby_machine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        end = T0 + timedelta(hours=2)
        world = _sb_world(discharge=[(T0, "12.30")])
        _add_eps(world, lambda _slot: 30.0, hours=2)
        _rig(monkeypatch, world)
        options = {**_sb_opts(bms=False), _const.CONF_EPS_SENSOR: EPS}
        assert _history.bms_input_ids(options) == []  # the roles: the EPS sensor is no BMS input
        raw_before, stats_before = len(world.raw_calls), len(world.stats_calls)

        state = await _history.async_update_standby(
            object(), options, _history.DrainState(), end + timedelta(seconds=5)
        )

        assert state.estimate is None and state.count == 0
        assert len(world.raw_calls) == raw_before  # no raw state is read for an EPS sensor
        assert len(world.stats_calls) == stats_before  # nothing at all is read


# ---------------------------------------------------------------------------
# The efficiency machine, end to end
# ---------------------------------------------------------------------------


class TestEfficiencyMachine:
    @staticmethod
    def _options(with_eps: bool = True) -> dict[str, Any]:
        options = _eff_opts()
        if with_eps:
            options[_const.CONF_EPS_SENSOR] = EPS
        return options

    async def _states(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value_at: Callable[[datetime], float] | None,
    ) -> Any:
        world = _eff_world()
        if value_at is not None:
            _add_eps(world, value_at)
        _rig(monkeypatch, world)
        efficiency, _standby = await _eff_cycles(
            world, self._options(with_eps=value_at is not None), EFF_FIRST, EFF_LAST
        )
        return efficiency

    async def test_an_idle_eps_sensor_changes_neither_estimate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plain = await self._states(monkeypatch, None)
        idle = await self._states(monkeypatch, lambda _slot: 0.0)
        assert plain.charge.counted_runs == idle.charge.counted_runs == 1
        assert plain.discharge.counted_runs == idle.discharge.counted_runs == 1
        assert idle.charge.counted_kwh == pytest.approx(plain.charge.counted_kwh)
        assert idle.discharge.counted_kwh == pytest.approx(plain.discharge.counted_kwh)
        assert idle.charge.value == pytest.approx(ETA_C, abs=0.02)
        assert idle.discharge.value == pytest.approx(ETA_D, abs=0.02)

    @pytest.mark.parametrize("watts", [40.0, -40.0])
    async def test_an_active_eps_sensor_leaves_no_pure_slot_during_its_stretch(
        self, monkeypatch: pytest.MonkeyPatch, watts: float
    ) -> None:
        # the charge run is hours 0.5 to 3.5, the discharge run hours 4 to 7
        first, last = _hours(0.0, 3.75)
        state = await self._states(monkeypatch, lambda s: watts if first <= s < last else 0.0)
        assert state.charge.counted_runs == 0 and state.charge.counted_kwh == 0.0
        # the other run is untouched: nothing of the EPS stretch reaches it
        assert state.discharge.counted_runs == 1
        assert state.discharge.value == pytest.approx(ETA_D, abs=0.02)
