"""
CAP-13 acceptance: the PV combined series and the PV delta integrals (TASK-0045g, ADR-027, ADR-029).

One module that demonstrates the capability on the *real* code, from the setup of the entry to
the published list: the real ``__init__.async_setup_entry``, the real coordinator, the real
history recalculation with its real ``has_sum`` writer against the stateful recorder of
``test_history_write_only_changed`` (the in-memory world of ``test_history_recent_equivalence``),
and the real sensor platform with real sensors attached to that coordinator. Only Home Assistant
itself is stubbed, as in the other test modules. The pieces are tested one by one in
``test_pv_combined_series``, ``test_pv_delta_values``, ``test_delta_derivation``,
``test_delta_integrals``, ``test_coordinator_delta_integrals``, ``test_pv_delta_sensors``,
``test_formula_*``, ``test_pv_share_*`` and ``test_catalog``; this module checks that they add up.

The scenario. Two PV inputs, A in W and B in kW, and one output, the charge counter. The data
covers three hours across a local midnight (the zone is UTC, so local midnight is 00:00):

    hour    A (W)   B (kW)   output (W)   loss   A effective   B effective   combined
    ------  ------  -------  -----------  -----  ------------  ------------  --------
    23:00   600     0.4      900          100    550 W         0.35 kW       900 W
    00:00   600     0.4      700          300    450 W         0.25 kW       700 W
    01:00   600     0.4      900          100    550 W         0.35 kW       900 W

The loss is Σ inputs − Σ outputs (1000 W against 900 W and 700 W), shared equally by the two
inputs (ADR-001): 50 W each in the first and last hour, 150 W each in the middle one. Every slot
is 5 minutes, so a power of P watts is P × 5/60 Wh in a slot and P Wh in an hour. The energies
written out below follow from this table and from nothing in the code:

    A: 550 + 450 + 550 = 1550 Wh in all, 450 + 550 = 1000 Wh since midnight (01-02)
    B: 0.35 + 0.25 + 0.35 = 0.95 kWh in all, 0.25 + 0.35 = 0.6 kWh since midnight
    combined: 0.9 + 0.7 + 0.9 = 2.5 kWh in all, 0.7 + 0.9 = 1.6 kWh since midnight

What Home Assistant does with a ``total`` sensor (the running total grows by the new state minus
the last state, ADR-027 Context) is modelled by ``_Compile`` below, for the one test that needs it:
the ``change`` of an hour.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType, ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.support import GROUP_KEYS
from tests.test_catalog import exports
from tests.test_eps_waterfall import _add_power
from tests.test_history_recent_equivalence import (
    _BASE,
    _MODULES,
    _PKG,
    _const,
    _FrozenDatetime,
    _history,
    _install,
    _options,
    _World,
)
from tests.test_history_write_only_changed import (
    REAL_UNITS,
    REAL_WRITE,
    StoredRecorder,
    _install_ha_api,
)
from tests.test_standby_sensor import _registry, _Registry, _sensor

UTC = timezone.utc
SLOT = timedelta(minutes=5)
_ROOT = Path(__file__).resolve().parent.parent
_TIMERS: list[tuple[str, Any, Any]] = []  # ("later" | "at", when, callback) of the timer stubs


# ---------------------------------------------------------------------------
# The real coordinator and __init__ over the history of the in-memory world
# ---------------------------------------------------------------------------


def _load_integration() -> dict[str, ModuleType]:
    """``delta_integrals``, ``coordinator`` and ``__init__`` from the real files, registered in
    the private package of ``test_history_recent_equivalence`` next to the modules that harness
    loaded, so that the coordinator calls *that* history, whose recorder functions the harness
    replaces. Home Assistant is stubbed only for the time of the load."""
    stubs: dict[str, ModuleType] = {}

    def stub(name: str, **attrs: Any) -> None:
        module = ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        stubs[name] = module

    def call_later(_hass: Any, delay: Any, callback: Any) -> Any:
        _TIMERS.append(("later", delay, callback))
        return lambda: None

    def track_point(_hass: Any, callback: Any, when: Any) -> Any:
        _TIMERS.append(("at", when, callback))
        return lambda: None

    stub("homeassistant")
    stub("homeassistant.config_entries", ConfigEntry=object)
    stub(
        "homeassistant.const",
        EVENT_RECORDER_5MIN_STATISTICS_GENERATED="recorder_5min_statistics_generated",
    )
    stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    stub("homeassistant.exceptions", ConfigEntryError=Exception)
    stub("homeassistant.helpers")
    stub(
        "homeassistant.helpers.event",
        async_call_later=call_later,
        async_track_point_in_utc_time=track_point,
    )
    names = ("delta_integrals", "coordinator", "__init__")
    touched = [*stubs, _PKG, *(f"{_PKG}.{n}" for n in (*_MODULES, *names))]
    saved = {name: sys.modules.get(name) for name in touched}
    loaded: dict[str, ModuleType] = {}
    try:
        sys.modules.update(stubs)
        package = ModuleType(_PKG)
        package.__path__ = [str(_BASE)]
        package.__package__ = _PKG
        sys.modules[_PKG] = package
        for name, module in _MODULES.items():
            sys.modules[f"{_PKG}.{name}"] = module
            setattr(package, name, module)
        for name in names:
            spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _BASE / f"{name}.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            module.__package__ = _PKG
            sys.modules[f"{_PKG}.{name}"] = module
            setattr(package, name, module)
            spec.loader.exec_module(module)
            loaded[name] = module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return loaded


_LOADED = _load_integration()
_coordinator_module, _init = _LOADED["coordinator"], _LOADED["__init__"]


# ---------------------------------------------------------------------------
# The scenario
# ---------------------------------------------------------------------------

PV_A, PV_B, PV_C, OUT, GRID = (
    "sensor.pv_a",
    "sensor.pv_b",
    "sensor.pv_c",
    "sensor.out",
    "sensor.grid_import",
)
A_W, B_KW = 600.0, 0.4
OUT_W = [900.0, 700.0, 900.0, 900.0, 900.0]  # per hour from 23:00; the last two are for the jobs
FIRST = datetime(2024, 1, 1, 23, 0, tzinfo=UTC)
SETUP_AT = datetime(2024, 1, 2, 2, 3, 5, tzinfo=UTC)  # the slots up to 01:55 are closed
MIDNIGHT = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
NEXT_MIDNIGHT = datetime(2024, 1, 3, 0, 0, tzinfo=UTC)

# the statistic IDs are the entity IDs (ADR-027); written out, they are part of the README
A_EFFECTIVE, B_EFFECTIVE = "sensor.effy_pv_a", "sensor.effy_pv_b"
PV_SUM = "sensor.effy_effective_pv_sum_now"
PV_SHARE = "sensor.effy_pv_share_now"
A_TODAY = "sensor.effy_pv_a_delta_integral_today"
B_TODAY = "sensor.effy_pv_b_delta_integral_today"
C_TODAY = "sensor.effy_pv_c_delta_integral_today"
ALL_TODAY = "sensor.effy_pv_deltasum_integral_today"
ALL_TOTAL = "sensor.effy_pv_deltasum_integral_total"
INTEGRALS = [A_TODAY, B_TODAY, ALL_TODAY, ALL_TOTAL]
CAP13_SERIES = [PV_SUM, PV_SHARE, *INTEGRALS]
RECALCULATED_FROM = "sensor.effy_recalculated_from"


def _slots(hours: int) -> list[datetime]:
    return [FIRST + SLOT * i for i in range(12 * hours)]


def _world() -> _World:
    """The three hours of the table and two more, constant, for the slot jobs of the tests."""
    world = _World()
    slots = _slots(len(OUT_W))
    _add_power(world, PV_A, {slot: A_W for slot in slots})
    _add_power(world, PV_B, {slot: B_KW for slot in slots})
    world.units[PV_B] = "kW"
    _add_power(world, PV_C, {slot: 0.0 for slot in slots})  # a string that gives nothing yet
    # the grid import counter of the no-PV entry: an energy counter, 1000 W = 1/12 kWh a slot
    world.energy[GRID] = [(FIRST + SLOT * i, f"{i / 12:.6f}") for i in range(len(slots) + 1)]
    world.units[GRID] = "kWh"
    world.state_classes[GRID] = "total_increasing"
    _add_power(world, OUT, {slot: OUT_W[(slot - FIRST) // timedelta(hours=1)] for slot in slots})
    return world


def _pv_options(*pv: str) -> dict[str, Any]:
    return _options(list(pv), [OUT])


# ---------------------------------------------------------------------------
# The rig: hass, the entry, the real platform and the stateful recorder
# ---------------------------------------------------------------------------


class _AnyDatetime(type(datetime)):  # type: ignore[misc]
    """A class is an instance check away from every real ``datetime``."""

    def __instancecheck__(cls, instance: Any) -> bool:
        return isinstance(instance, datetime)


class _Clock(datetime, metaclass=_AnyDatetime):
    """``history.datetime`` for this module: ``now()`` is the frozen moment of the harness, and
    a plain ``datetime`` still is an instance of it. The harness's own ``_FrozenDatetime`` is a
    plain subclass, so ``isinstance(row_start, datetime)`` in the readers of stored integral
    rows fails for every row Home Assistant's API returns; nothing read such rows through it
    before. In production ``datetime`` is the real class."""

    @classmethod
    def now(cls, tz: Any = None) -> _Clock:
        return _FrozenDatetime.frozen  # type: ignore[return-value]


class _Entry:
    entry_id = "entry1"

    def __init__(self, rig: _Rig, options: dict[str, Any], data: dict[str, Any]) -> None:
        self.rig = rig
        self.options = MappingProxyType(dict(options))
        self.data = dict(data)
        self.unloads: list[Any] = []

    def async_create_background_task(self, _hass: Any, target: Any, name: str) -> Any:
        task = asyncio.ensure_future(target)
        self.rig.tasks[name] = task
        return task

    def async_on_unload(self, callback: Any) -> None:
        self.unloads.append(callback)


class _ConfigEntries:
    def __init__(self, rig: _Rig) -> None:
        self.rig = rig

    def async_update_entry(
        self,
        entry: _Entry,
        *,
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> bool:
        if data is not None:
            entry.data = dict(data)
        if options is not None:
            entry.options = MappingProxyType(dict(options))
        return True

    async def async_forward_entry_setups(self, entry: _Entry, platforms: list[str]) -> None:
        """The sensor platform of the real ``sensor.py``; every entity is added to Home Assistant
        (it restores nothing: a fresh entity, as after a reload of a world without a state)."""
        assert "sensor" in platforms
        added: list[Any] = []

        def add_entities(entities: list[Any], **_kwargs: Any) -> None:
            added.extend(entities)

        await _sensor.async_setup_entry(self.rig.hass, entry, add_entities)
        for entity in added:
            await entity.async_added_to_hass()
        self.rig.entities = added


class _Rig:
    """The entry as Home Assistant would set it up, on the in-memory world."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, world: _World | None = None) -> None:
        self.world = world or _world()
        self.recorder = StoredRecorder()
        self.tasks: dict[str, asyncio.Future[Any]] = {}
        self.entities: list[Any] = []
        self.now = SETUP_AT
        _TIMERS.clear()
        _install(monkeypatch, self.world)
        _install_ha_api(monkeypatch, self.recorder)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: self.recorder)
        monkeypatch.setattr(_history, "_write_recorder_statistics", REAL_WRITE)
        sources = dict(self.world.units)
        monkeypatch.setattr(
            _history,
            "_get_statistics_units",
            lambda hass, ids: {**sources, **REAL_UNITS(hass, list(ids))},
        )
        monkeypatch.setattr(_history, "datetime", _Clock)
        monkeypatch.setattr(_coordinator_module.EffyCoordinator, "_now", lambda _c: self.now)
        _registry[0] = _Registry({})
        states = {
            eid: SimpleNamespace(
                state="1.0",
                attributes={"state_class": "measurement", "unit_of_measurement": unit},
            )
            for eid, unit in self.world.units.items()
        }
        self.hass = SimpleNamespace(
            data={},
            config=SimpleNamespace(time_zone="UTC"),
            states=SimpleNamespace(get=states.get),
            bus=SimpleNamespace(async_listen=lambda *_a: lambda: None),
            config_entries=_ConfigEntries(self),
        )
        self.set_now(SETUP_AT)

    def set_now(self, now: datetime) -> None:
        self.now = now
        _FrozenDatetime.frozen = now

    async def setup(self, options: dict[str, Any], data: dict[str, Any] | None = None) -> _Entry:
        """``async_setup_entry`` of the real ``__init__``; ``data`` is what the entry stored."""
        self.entry = _Entry(self, options, data or {})
        _TIMERS.clear()
        assert await _init.async_setup_entry(self.hass, self.entry) is True
        self.coordinator = self.hass.data["effy"][self.entry.entry_id]
        return self.entry

    async def finish(self) -> None:
        """Wait for the background tasks of the entry (the recalculation started by itself)."""
        for task in list(self.tasks.values()):
            assert await task is True

    async def ready(self, options: dict[str, Any] | None = None) -> _Rig:
        """The entry set up with nothing stored, and its first recalculation done."""
        await self.setup(options or _pv_options(PV_A, PV_B))
        await self.finish()
        return self

    def entity(self, entity_id: str) -> Any:
        (found,) = [e for e in self.entities if e.entity_id == entity_id]
        return found

    def has(self, entity_id: str) -> bool:
        return any(e.entity_id == entity_id for e in self.entities)

    def value(self, entity_id: str) -> float:
        value: float = self.entity(entity_id).native_value
        return value

    async def slot_job(self, slot: datetime) -> None:
        """The body of the slot job that serves ``slot``. In production the recorder's 5-minute
        event starts it just after the slot closed; the moment chosen here, 35 seconds after,
        is inside the 5-minute window that follows, and any moment in it gives the same result."""
        self.set_now(slot + SLOT + timedelta(seconds=35))
        await self.coordinator._async_recalculate_recent_and_report(slot)

    def short(self, statistic_id: str) -> dict[datetime, dict[str, Any]]:
        return self.recorder.short(statistic_id)

    def hourly(self, statistic_id: str) -> dict[datetime, dict[str, Any]]:
        return self.recorder.hourly(statistic_id)

    def imported(self, *statistic_ids: str) -> list[tuple[str, str, int]]:
        return [i for i in self.recorder.imports if i[1] in statistic_ids]


@pytest.fixture(autouse=True)
def _fresh_registry() -> Any:
    _registry[0] = _Registry({})
    yield
    _registry[0] = _Registry({})


class _Compile:
    """What Home Assistant's recorder does with the live state of a ``total`` sensor, as far as
    ADR-027 (Context) states it: every 5 minutes it takes the sensor's state, adds *new state −
    last state* to the running total and stores both. A ``last_reset`` that changes would start a
    new cycle; the hours of this model stay within one day, so it never does. Nothing here is
    Effy's code: it is the world that Effy's pushes land in."""

    def __init__(self, state: float, total: float, last_reset: datetime | None) -> None:
        self.state, self.total, self.last_reset = state, total, last_reset
        self.totals: dict[datetime, float] = {}

    def compile(self, start: datetime, state: float, last_reset: datetime | None) -> None:
        assert last_reset == self.last_reset  # no new cycle within the modelled hours
        self.total += state - self.state
        self.state = state
        self.totals[start] = self.total

    def change(self, hour: datetime) -> float:
        """The change of the hour: its last running total minus the last one before it."""
        last = max(start for start in self.totals if hour <= start < hour + timedelta(hours=1))
        before = max(start for start in self.totals if start < hour)
        return self.totals[last] - self.totals[before]


def _u(hour: int, minute: int = 0, day: int = 2) -> datetime:
    return datetime(2024, 1, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. The PV combined series
# ---------------------------------------------------------------------------


class TestThePvCombinedSeries:
    async def test_each_input_loses_its_equal_share_of_the_loss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        # loss 100 W in 23:00 and 01:00, 300 W in 00:00: 50 W and 150 W per input
        for hour, a_w, b_kw in [
            (_u(23, day=1), 550.0, 0.35),
            (_u(0), 450.0, 0.25),
            (_u(1), 550.0, 0.35),
        ]:
            for i in range(12):
                slot = hour + SLOT * i
                assert rig.short(A_EFFECTIVE)[slot]["mean"] == pytest.approx(a_w)
                assert rig.short(B_EFFECTIVE)[slot]["mean"] == pytest.approx(b_kw)
        assert rig.recorder.units[A_EFFECTIVE] == "W"
        assert rig.recorder.units[B_EFFECTIVE] == "kW"  # the input's own unit

    async def test_the_combined_series_is_the_addition_of_the_effective_values_in_w(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        combined = rig.short(PV_SUM)
        assert len(combined) == 36  # a row for every slot of the three hours
        for hour, watts in [(_u(23, day=1), 900.0), (_u(0), 700.0), (_u(1), 900.0)]:
            for i in range(12):
                assert combined[hour + SLOT * i]["mean"] == pytest.approx(watts)
        # 550 W + 0.35 kW (= 350 W) = 900 W; 450 W + 0.25 kW (= 250 W) = 700 W
        assert rig.recorder.units[PV_SUM] == "W"

    async def test_the_combined_series_equals_the_outputs_when_the_inputs_are_all_pv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Σ effective inputs = Σ outputs (ADR-001): with only PV inputs the PV combined series
        is the output of the system."""
        rig = await _Rig(monkeypatch).ready()
        for hour, out in [(_u(23, day=1), 900.0), (_u(0), 700.0), (_u(1), 900.0)]:
            assert rig.short(PV_SUM)[hour]["mean"] == pytest.approx(out)

    async def test_the_hour_is_the_mean_of_its_slots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rig = await _Rig(monkeypatch).ready()
        hourly = {hour: row["mean"] for hour, row in rig.hourly(PV_SUM).items()}
        assert hourly == pytest.approx({_u(23, day=1): 900.0, _u(0): 700.0, _u(1): 900.0})

    async def test_the_share_of_pv_is_100_percent_when_every_input_is_pv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        assert {row["mean"] for row in rig.short(PV_SHARE).values()} == {100.0}
        assert rig.recorder.units[PV_SHARE] == "%"


# ---------------------------------------------------------------------------
# 2. The derivation gives the integrals a history
# ---------------------------------------------------------------------------

YESTERDAY = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
# (statistic ID, unit, energy of all rows, energy since the midnight of 01-02), by the table
DERIVED = [
    (A_TODAY, "Wh", 1550.0, 1000.0),
    (B_TODAY, "kWh", 0.95, 0.6),
    (ALL_TODAY, "kWh", 2.5, 1.6),
    (ALL_TOTAL, "kWh", 2.5, 2.5),
]


class TestTheDerivation:
    async def test_the_integrals_wait_unavailable_until_the_derivation_wrote_their_base(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch)
        await rig.setup(_pv_options(PV_A, PV_B))
        # the recalculation was started by itself, and has not run yet
        assert list(rig.tasks) == ["effy_formula_recalculation_entry1"]
        assert [rig.entity(eid).available for eid in INTEGRALS] == [False] * 4
        await rig.finish()
        assert [rig.entity(eid).available for eid in INTEGRALS] == [True] * 4

    @pytest.mark.parametrize(("statistic_id", "unit", "total", "since_midnight"), DERIVED)
    async def test_every_integral_has_a_history_of_the_whole_range(
        self,
        monkeypatch: pytest.MonkeyPatch,
        statistic_id: str,
        unit: str,
        total: float,
        since_midnight: float,
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        assert len(rig.short(statistic_id)) == 36  # every 5-minute slot of the three hours
        assert sorted(rig.hourly(statistic_id)) == [_u(23, day=1), _u(0), _u(1)]
        assert rig.recorder.units[statistic_id] == unit
        # a running total: Home Assistant's `sum`, not a mean
        assert rig.recorder.flags[statistic_id] == {"has_mean": False, "has_sum": True}
        assert all(row["mean"] is None for row in rig.short(statistic_id).values())

    @pytest.mark.parametrize(("statistic_id", "unit", "total", "since_midnight"), DERIVED)
    async def test_the_running_total_is_the_energy_of_all_rows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        statistic_id: str,
        unit: str,
        total: float,
        since_midnight: float,
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        last = rig.short(statistic_id)[_u(1, 55)]
        assert last["sum"] == pytest.approx(total)
        # the first row is the energy of its own slot: 550 W × 5/60 h = 45.83 Wh
        first = rig.short(statistic_id)[_u(23, day=1)]
        slot_energy = {A_TODAY: 550.0 * 5 / 60, B_TODAY: 0.35 * 5 / 60}
        slot_energy.update({ALL_TODAY: 0.9 * 5 / 60, ALL_TOTAL: 0.9 * 5 / 60})
        assert first["sum"] == pytest.approx(slot_energy[statistic_id])

    async def test_the_running_total_of_an_input_after_each_hour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        assert rig.short(A_TODAY)[_u(23, 55, day=1)]["sum"] == pytest.approx(550.0)
        assert rig.short(A_TODAY)[_u(0, 55)]["sum"] == pytest.approx(550.0 + 450.0)
        assert rig.short(A_TODAY)[_u(1, 55)]["sum"] == pytest.approx(550.0 + 450.0 + 550.0)
        # the hour rows carry the values of the hour's last 5-minute row
        assert {h: r["sum"] for h, r in rig.hourly(A_TODAY).items()} == pytest.approx(
            {_u(23, day=1): 550.0, _u(0): 1000.0, _u(1): 1550.0}
        )

    @pytest.mark.parametrize(("statistic_id", "unit", "total", "since_midnight"), DERIVED)
    async def test_the_state_is_the_energy_since_the_local_midnight_of_the_rows_day(
        self,
        monkeypatch: pytest.MonkeyPatch,
        statistic_id: str,
        unit: str,
        total: float,
        since_midnight: float,
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        rows = rig.short(statistic_id)
        assert rows[_u(1, 55)]["state"] == pytest.approx(since_midnight)
        today = statistic_id != ALL_TOTAL
        # the day before: 550 Wh, 0.35 kWh, 0.9 kWh; the midnight starts the new cycle
        before = {A_TODAY: 550.0, B_TODAY: 0.35, ALL_TODAY: 0.9, ALL_TOTAL: 0.9}[statistic_id]
        assert rows[_u(23, 55, day=1)]["state"] == pytest.approx(before)
        for start, row in rows.items():
            if today:
                assert row["last_reset"] == (MIDNIGHT if start >= MIDNIGHT else YESTERDAY)
            else:  # the total has no cycle
                assert row["last_reset"] is None

    async def test_the_midnight_starts_the_cycle_again_at_the_first_slot_of_the_day(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        # 00:00 slot of A: 450 W × 5/60 h = 37.5 Wh since midnight, 587.5 Wh in all
        assert rig.short(A_TODAY)[_u(0)]["state"] == pytest.approx(37.5)
        assert rig.short(A_TODAY)[_u(0)]["sum"] == pytest.approx(587.5)

    async def test_the_combined_integrals_are_the_per_input_ones_added_up_row_by_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        for start in rig.short(ALL_TODAY):
            a, b = rig.short(A_TODAY)[start], rig.short(B_TODAY)[start]
            combined = rig.short(ALL_TODAY)[start]
            total = rig.short(ALL_TOTAL)[start]
            # A is in Wh, B and the combined ones in kWh
            assert combined["sum"] == pytest.approx(a["sum"] / 1000 + b["sum"]), start
            assert combined["state"] == pytest.approx(a["state"] / 1000 + b["state"]), start
            assert total["sum"] == pytest.approx(a["sum"] / 1000 + b["sum"]), start

    async def test_the_sensors_show_exactly_the_last_derived_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        assert rig.value(A_TODAY) == pytest.approx(1000.0)
        assert rig.value(B_TODAY) == pytest.approx(0.6)
        assert rig.value(ALL_TODAY) == pytest.approx(1.6)
        assert rig.value(ALL_TOTAL) == pytest.approx(2.5)
        assert [rig.entity(eid).last_reset for eid in (A_TODAY, B_TODAY, ALL_TODAY)] == [
            MIDNIGHT
        ] * 3
        assert rig.entity(ALL_TOTAL).last_reset is None
        assert [rig.entity(eid).native_unit_of_measurement for eid in INTEGRALS] == [
            "Wh",
            "kWh",
            "kWh",
            "kWh",
        ]

    async def test_the_sensors_are_energy_totals(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rig = await _Rig(monkeypatch).ready()
        for entity_id in INTEGRALS:
            assert rig.entity(entity_id)._attr_device_class == "energy"
            assert rig.entity(entity_id)._attr_state_class == "total"

    async def test_the_signature_is_stored_when_the_run_is_done(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        # the signature is stored last: the next setup starts no recalculation
        assert rig.entry.data[_const.CONF_FORMULA_SIGNATURE] == _init.formula_signature(
            rig.entry.options
        )
        assert rig.entry.data[_const.CONF_FORMULA_SIGNATURE]["pv_input_sensors"] == [PV_A, PV_B]


# ---------------------------------------------------------------------------
# 3. The next slot books into the three integral kinds
# ---------------------------------------------------------------------------


class TestTheNextSlotBooksItsEnergy:
    """The slot 02:00 has A 600 W, B 0.4 kW and 900 W out: 550 W and 0.35 kW effective, 900 W in
    all. Its energy: A 550 × 5/60 = 45.83 Wh, B 0.35 × 5/60 = 0.02917 kWh, combined 900 × 5/60 =
    75 Wh = 0.075 kWh."""

    async def test_it_is_booked_into_every_kind_at_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        await rig.slot_job(_u(2))
        assert rig.value(A_TODAY) == pytest.approx(1000.0 + 550.0 * 5 / 60)  # per input today
        assert rig.value(B_TODAY) == pytest.approx(0.6 + 0.35 * 5 / 60)
        assert rig.value(ALL_TODAY) == pytest.approx(1.6 + 0.075)  # combined today
        assert rig.value(ALL_TOTAL) == pytest.approx(2.5 + 0.075)  # combined total

    async def test_the_cycle_and_the_units_are_unchanged_by_a_booking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        await rig.slot_job(_u(2))
        assert [rig.entity(e).last_reset for e in (A_TODAY, B_TODAY, ALL_TODAY)] == [MIDNIGHT] * 3
        assert rig.entity(ALL_TOTAL).last_reset is None

    async def test_effy_writes_no_statistic_of_an_integral_in_normal_operation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The statistics of the integrals belong to Home Assistant (ADR-027 decision 4)."""
        rig = await _Rig(monkeypatch).ready()
        before = list(rig.recorder.imports)
        await rig.slot_job(_u(2))
        await rig.slot_job(_u(2, 5))
        assert rig.imported(*INTEGRALS) == [i for i in before if i[1] in INTEGRALS]
        assert any(i[1] == A_EFFECTIVE for i in rig.recorder.imports[len(before) :])  # it wrote

    async def test_the_series_of_the_new_slot_are_written_with_the_slot_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        await rig.slot_job(_u(2))
        assert rig.short(PV_SUM)[_u(2)]["mean"] == pytest.approx(900.0)
        assert rig.short(A_EFFECTIVE)[_u(2)]["mean"] == pytest.approx(550.0)
        assert rig.short(B_EFFECTIVE)[_u(2)]["mean"] == pytest.approx(0.35)

    async def test_a_slot_that_changes_nothing_books_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        await rig.slot_job(_u(2))
        before = [rig.value(e) for e in INTEGRALS]
        await rig.slot_job(_u(2))  # the same slot again: rows equal what is stored
        assert [rig.value(e) for e in INTEGRALS] == pytest.approx(before)

    async def test_the_midnight_resets_today_and_keeps_the_total(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        ((_kind, when, callback),) = [t for t in _TIMERS if t[0] == "at"]
        assert when == NEXT_MIDNIGHT  # wall-clock local midnight, the next one
        callback(None)
        assert [rig.value(e) for e in (A_TODAY, B_TODAY, ALL_TODAY)] == [0.0, 0.0, 0.0]
        assert [rig.entity(e).last_reset for e in (A_TODAY, B_TODAY, ALL_TODAY)] == [
            NEXT_MIDNIGHT
        ] * 3
        assert rig.value(ALL_TOTAL) == pytest.approx(2.5)
        assert rig.entity(ALL_TOTAL).last_reset is None


# ---------------------------------------------------------------------------
# 4. A change of the formula runs the recalculation and the derivation by itself
# ---------------------------------------------------------------------------


def _snapshot(rig: _Rig, *statistic_ids: str) -> dict[str, dict[tuple[str, datetime], Any]]:
    return {
        sid: {
            **{("5minute", s): dict(r) for s, r in rig.short(sid).items()},
            **{("hour", s): dict(r) for s, r in rig.hourly(sid).items()},
        }
        for sid in statistic_ids
    }


class TestTheFormulaRecalculationRunsByItself:
    async def test_an_entry_that_stored_the_current_signature_starts_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        written = list(rig.recorder.imports)
        rig.tasks.clear()
        await rig.setup(_pv_options(PV_A, PV_B), data=dict(rig.entry.data))  # a restart
        assert rig.tasks == {}
        assert rig.recorder.imports == written

    @pytest.mark.parametrize(
        "change",
        [
            {_const.CONF_MAX_HISTORY_DAYS: 3},
            {_const.CONF_VALLEY_HYSTERESIS: 45},
            {_const.CONF_DEBUG_EXPORT: True},
        ],
    )
    async def test_an_option_that_does_not_influence_the_formula_starts_nothing(
        self, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        written = list(rig.recorder.imports)
        rig.tasks.clear()
        options = {**_pv_options(PV_A, PV_B), **change}
        assert _init.formula_signature(options) == rig.entry.data[_const.CONF_FORMULA_SIGNATURE]
        await rig.setup(options, data=dict(rig.entry.data))
        assert rig.tasks == {}  # no recalculation task is created at all
        assert rig.recorder.imports == written  # and nothing was written
        assert rig.recorder.cleared == []

    async def test_after_an_upgrade_every_entry_recalculates_once_and_derives_the_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The entry of the version before CAP-13: effective rows exist, no integral and no PV
        sum, and no signature is stored (ADR-027 decision 6)."""
        rig = await _Rig(monkeypatch).ready()
        derived = _snapshot(rig, *INTEGRALS)
        rig.recorder.async_clear_statistics(CAP13_SERIES)
        assert not any(sid in rig.recorder.units for sid in CAP13_SERIES)
        written = len(rig.recorder.imports)
        rig.tasks.clear()
        await rig.setup(_pv_options(PV_A, PV_B), data={})
        assert list(rig.tasks) == ["effy_formula_recalculation_entry1"]
        await rig.finish()
        # the history of the integrals is the one the table gives, as before
        assert _snapshot(rig, *INTEGRALS) == derived
        assert len(rig.short(PV_SUM)) == 36 and len(rig.short(PV_SHARE)) == 36
        assert rig.value(ALL_TOTAL) == pytest.approx(2.5)
        # the effective rows were already what they are: none of them is written again
        assert rig.imported(A_EFFECTIVE, B_EFFECTIVE) == [
            i for i in rig.recorder.imports[:written] if i[1] in (A_EFFECTIVE, B_EFFECTIVE)
        ]
        assert rig.entry.data[_const.CONF_FORMULA_SIGNATURE] == _init.formula_signature(
            rig.entry.options
        )

    async def test_a_new_pv_input_is_derived_and_the_combined_integrals_become_the_addition_of_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Q27 = A (ADR-027 decision 9). The string C gives nothing yet, so the effective values
        of A and B stay what they are and every number of the table stays true.

        The combined integrals are written again as the addition of the per-input *changes*:
        the first row of a per-input series only fixes its starting point (TASK-0045e, flagged
        for the human), so the first slot of the history, 23:00 on 01-01, is not counted. That
        is a known limit and the test pins it: the combined total is 0.075 kWh (900 W for one
        slot) short of the 2.5 kWh the table gives, the combined *today* is not affected (the
        slot is of the day before), and the 23:00 row is not rewritten."""
        rig = await _Rig(monkeypatch).ready()
        stored = dict(rig.entry.data)
        a_and_b = _snapshot(rig, A_TODAY, B_TODAY)
        rig.tasks.clear()
        written = len(rig.recorder.imports)
        await rig.setup(_pv_options(PV_A, PV_B, PV_C), data=stored)
        assert list(rig.tasks) == ["effy_formula_recalculation_entry1"]  # by itself
        await rig.finish()
        new = rig.recorder.imports[written:]
        assert rig.recorder.cleared == [[ALL_TODAY, ALL_TOTAL]]  # the combined ones, written again
        assert {sid for _t, sid, _n in new if sid in INTEGRALS + [C_TODAY]} == {
            C_TODAY,
            ALL_TODAY,
            ALL_TOTAL,
        }
        assert _snapshot(rig, A_TODAY, B_TODAY) == a_and_b  # untouched
        # the new input has a history of its own: 36 rows, no energy yet
        assert len(rig.short(C_TODAY)) == 36
        assert {row["sum"] for row in rig.short(C_TODAY).values()} == {0.0}
        # the combined integrals are the addition of the per-input changes of the three inputs
        first = _u(23, day=1)
        assert first not in rig.short(ALL_TODAY) and first not in rig.short(
            ALL_TOTAL
        )  # known limit
        assert len(rig.short(ALL_TODAY)) == len(rig.short(ALL_TOTAL)) == 35
        origin = {sid: rig.short(sid)[first]["sum"] for sid in (A_TODAY, B_TODAY, C_TODAY)}
        for start in rig.short(ALL_TOTAL):
            a, b, c = (rig.short(sid)[start] for sid in (A_TODAY, B_TODAY, C_TODAY))
            added = (
                (a["sum"] - origin[A_TODAY]) / 1000
                + (b["sum"] - origin[B_TODAY])
                + (c["sum"] - origin[C_TODAY])
            )
            assert rig.short(ALL_TOTAL)[start]["sum"] == pytest.approx(added)
            assert rig.short(ALL_TODAY)[start]["sum"] == pytest.approx(added)
            if start >= MIDNIGHT:  # the cycle of the day: the energy since local midnight
                assert rig.short(ALL_TODAY)[start]["state"] == pytest.approx(
                    a["state"] / 1000 + b["state"] + c["state"]
                )
        assert rig.short(ALL_TOTAL)[_u(1, 55)]["sum"] == pytest.approx(2.5 - 0.075)  # the limit
        assert rig.short(ALL_TODAY)[_u(1, 55)]["state"] == pytest.approx(1.6)
        # the sensors: A and B kept their state, C starts at 0, the combined ones are back
        assert [rig.value(e) for e in (A_TODAY, B_TODAY, C_TODAY)] == pytest.approx(
            [1000.0, 0.6, 0.0]
        )
        assert [rig.value(e) for e in (ALL_TODAY, ALL_TOTAL)] == pytest.approx([1.6, 2.5 - 0.075])
        assert rig.entry.data[_const.CONF_FORMULA_SIGNATURE]["pv_input_sensors"] == [
            PV_A,
            PV_B,
            PV_C,
        ]


# ---------------------------------------------------------------------------
# 5. A button run after a correction
# ---------------------------------------------------------------------------


class TestAButtonRunAfterACorrection:
    """The recorder's history of A is corrected: the string was disconnected, A was 0 W all along.
    Then A has nothing effective and B, whose 0.4 kW is below every output, loses nothing: 0.4 kW
    in every slot. The delta of the run, in the energy table's terms:

        A: 0 − (550 + 450 + 550) Wh = −1550 Wh
        B: (0.4 − 0.35) + (0.4 − 0.25) + (0.4 − 0.35) kWh = +0.25 kWh
        combined: −1.55 + 0.25 = −1.3 kWh, which is 3 × 0.4 − 2.5 kWh
    """

    @staticmethod
    async def _correct_and_press_the_button(rig: _Rig) -> None:
        rig.world.power_means[PV_A] = {slot: 0.0 for slot in _slots(len(OUT_W))}
        await rig.coordinator.async_recalculate_history_and_report()

    async def test_the_correction_is_booked_in_today_and_total_and_today_goes_negative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        await self._correct_and_press_the_button(rig)
        assert rig.value(A_TODAY) == pytest.approx(1000.0 - 1550.0)  # −550 Wh: not clamped
        assert rig.value(A_TODAY) < 0
        assert rig.value(B_TODAY) == pytest.approx(0.6 + 0.25)
        assert rig.value(ALL_TODAY) == pytest.approx(1.6 - 1.3)
        assert rig.value(ALL_TOTAL) == pytest.approx(2.5 - 1.3)
        # the corrected rows are the new truth: B alone, 0.4 kW = 400 W, all of the 3 hours
        assert [r["mean"] for r in rig.short(PV_SUM).values()] == pytest.approx([400.0] * 36)
        assert 3 * 0.4 == pytest.approx(2.5 - 1.3)  # the total is now the energy of 3 hours

    async def test_the_combined_integrals_still_equal_the_per_input_ones_added_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        assert rig.value(ALL_TODAY) == pytest.approx(rig.value(A_TODAY) / 1000 + rig.value(B_TODAY))
        await self._correct_and_press_the_button(rig)
        assert rig.value(ALL_TODAY) == pytest.approx(rig.value(A_TODAY) / 1000 + rig.value(B_TODAY))
        assert rig.value(ALL_TODAY) == pytest.approx(0.3)  # −0.55 kWh + 0.85 kWh

    async def test_the_cycle_is_unchanged_and_the_button_writes_none_of_their_statistics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        derived = _snapshot(rig, *INTEGRALS)
        await self._correct_and_press_the_button(rig)
        assert _snapshot(rig, *INTEGRALS) == derived  # Home Assistant compiles them
        assert [rig.entity(e).last_reset for e in (A_TODAY, B_TODAY, ALL_TODAY)] == [MIDNIGHT] * 3

    async def test_a_second_press_with_nothing_corrected_books_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        await self._correct_and_press_the_button(rig)
        booked = [rig.value(e) for e in INTEGRALS]
        await rig.coordinator.async_recalculate_history_and_report()
        assert [rig.value(e) for e in INTEGRALS] == pytest.approx(booked)


# ---------------------------------------------------------------------------
# 6. The change of an hour
# ---------------------------------------------------------------------------


class TestTheChangeOfAnHourIsWhatWasBookedInIt:
    """Every slot job books the energy of one slot (constant 900 W: A 45.83 Wh, B 0.02917 kWh,
    combined 0.075 kWh) when it runs, just after the slot closed; Home Assistant compiles the
    5-minute statistic that contains that moment. The job of the slot 02:00 runs in the window
    02:05 to 02:10 (here at 02:05:35) and shows in the statistic of 02:05, so the hour 02:00
    holds the bookings of 11 jobs (slots 02:00 to 02:50) and the hour 03:00 those of 12 (slots
    02:55 to 03:50): the statistic is one slot late against the physical slot (ADR-027
    decision 4)."""

    async def _run_the_jobs(self, rig: _Rig) -> dict[str, _Compile]:
        compiles: dict[str, _Compile] = {}
        for sid in INTEGRALS:
            entity = rig.entity(sid)
            compiles[sid] = _Compile(
                entity.native_value, rig.short(sid)[_u(1, 55)]["sum"], entity.last_reset
            )
            compiles[sid].totals[_u(1, 55)] = rig.short(sid)[_u(1, 55)]["sum"]
            compiles[sid].compile(_u(2), entity.native_value, entity.last_reset)
        for index in range(23):  # the slots 02:00 to 03:50
            slot = _u(2) + SLOT * index
            await rig.slot_job(slot)
            for sid in INTEGRALS:
                entity = rig.entity(sid)
                compiles[sid].compile(slot + SLOT, entity.native_value, entity.last_reset)
        return compiles

    async def test_the_change_of_a_full_hour_is_its_twelve_bookings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        compiles = await self._run_the_jobs(rig)
        # 12 × 550 × 5/60 = 550 Wh; 12 × 0.35 × 5/60 = 0.35 kWh; 12 × 0.075 = 0.9 kWh
        assert compiles[A_TODAY].change(_u(3)) == pytest.approx(550.0)
        assert compiles[B_TODAY].change(_u(3)) == pytest.approx(0.35)
        assert compiles[ALL_TODAY].change(_u(3)) == pytest.approx(0.9)
        assert compiles[ALL_TOTAL].change(_u(3)) == pytest.approx(0.9)

    async def test_the_hour_before_holds_one_booking_less_because_the_statistic_is_a_slot_late(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        compiles = await self._run_the_jobs(rig)
        # 11 × 550 × 5/60 = 504.17 Wh; 11 × 0.35 × 5/60 = 0.3208 kWh; 11 × 0.075 = 0.825 kWh
        assert compiles[A_TODAY].change(_u(2)) == pytest.approx(11 * 550.0 * 5 / 60)
        assert compiles[B_TODAY].change(_u(2)) == pytest.approx(11 * 0.35 * 5 / 60)
        assert compiles[ALL_TODAY].change(_u(2)) == pytest.approx(0.825)
        assert compiles[ALL_TOTAL].change(_u(2)) == pytest.approx(0.825)
        assert compiles[ALL_TOTAL].change(_u(2)) != pytest.approx(0.9)  # the physical hour

    async def test_the_changes_of_the_hours_add_up_to_what_the_sensor_gained(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        compiles = await self._run_the_jobs(rig)
        gained = rig.value(ALL_TOTAL) - 2.5
        assert compiles[ALL_TOTAL].change(_u(2)) + compiles[ALL_TOTAL].change(_u(3)) == (
            pytest.approx(gained)
        )
        assert gained == pytest.approx(23 * 0.075)  # 23 slots of 75 Wh


# ---------------------------------------------------------------------------
# 7. An entry without a PV input
# ---------------------------------------------------------------------------


class TestAnEntryWithoutAPvInputHasNoneOfTheSensors:
    """The battery system with a grid import counter as its only input and the charge counter:
    no PV string, so no PV sum, no PV energy and no PV share (the share needs a PV input or an
    EPS sensor, ADR-027 amendment 2026-11-09)."""

    @staticmethod
    def _options() -> dict[str, Any]:
        return _options([], [OUT], [GRID])

    async def test_no_sensor_of_the_capability_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready(self._options())
        for entity_id in [*CAP13_SERIES, C_TODAY]:
            assert not rig.has(entity_id), entity_id
        assert not [e for e in rig.entities if type(e).__name__.startswith("EffyPv")]
        assert rig.has("sensor.effy_grid_import")  # the entry itself works
        assert rig.coordinator.pv_delta_enabled is False

    async def test_the_sensors_of_an_earlier_configuration_leave_the_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _Rig(monkeypatch)
        entry = SimpleNamespace(entry_id="entry1")
        known = {
            ("sensor", "effy", _sensor.pv_sum_unique_id(entry)): PV_SUM,
            ("sensor", "effy", _sensor.pv_share_unique_id(entry)): PV_SHARE,
            ("sensor", "effy", "entry1_pv_a_delta_integral_today"): A_TODAY,
            ("sensor", "effy", "entry1_pv_deltasum_integral_today"): ALL_TODAY,
            ("sensor", "effy", "entry1_pv_deltasum_integral_total"): ALL_TOTAL,
            ("sensor", "effy", "entry1_recalculated_from"): RECALCULATED_FROM,
        }
        _registry[0] = _Registry(known)
        await rig.ready(self._options())
        assert sorted(_registry[0].removed) == sorted(
            [PV_SUM, PV_SHARE, A_TODAY, ALL_TODAY, ALL_TOTAL]  # not the recalculated-from sensor
        )

    async def test_the_recalculation_writes_none_of_their_statistics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready(self._options())
        assert not any(sid in rig.recorder.units for sid in [*CAP13_SERIES, C_TODAY])
        assert rig.short("sensor.effy_grid_import")  # but it ran, and wrote what the entry has
        assert rig.entry.data[_const.CONF_FORMULA_SIGNATURE]["pv_input_sensors"] == []

    async def test_nothing_of_it_is_published_and_no_midnight_is_scheduled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready(self._options())
        attributes = rig.entity(RECALCULATED_FROM).extra_state_attributes
        listed = {e for group in attributes["sensors"] for e in group["sensors"]}
        assert "sensor.effy_grid_import" in listed  # the entry's own sensors are published
        assert listed.isdisjoint([*CAP13_SERIES, A_TODAY, A_EFFECTIVE])
        assert all(group["role"] != "pv" for group in attributes["sensors"])
        assert all(not source.startswith("sensor.pv") for source in attributes["sources"])
        assert [t for t in _TIMERS if t[0] == "at"] == []  # nothing resets at midnight


# ---------------------------------------------------------------------------
# 8. The published list
# ---------------------------------------------------------------------------


def _group(
    series: str,
    quantity: str,
    scope: str,
    span: str,
    function: str,
    aggregations: list[str],
    sensors: list[str],
) -> dict[str, Any]:
    """A group of a PV sensor: role ``pv``, direction ``input`` in every one of them."""
    return {
        "series": series,
        "quantity": quantity,
        "scope": scope,
        "span": span,
        "role": "pv",
        "direction": "input",
        "function": function,
        "aggregations": aggregations,
        "sensors": sensors,
    }


# The groups of the ADR-029 table that this entry configures, sorted by the values of series,
# quantity, scope, span, role, direction and function (the README's rule). The inputs are power
# sensors, so each has an effective and a smoothed series and no derived one.
ENERGY = ["state", "sum", "change"]
SLOT_VALUES = ["mean", "state"]
EXPECTED_GROUPS = [
    _group("effective", "energy", "combined", "today", "pv", ENERGY, [ALL_TODAY]),
    _group("effective", "energy", "combined", "total", "pv", ENERGY, [ALL_TOTAL]),
    _group("effective", "energy", "source", "today", "pv", ENERGY, [A_TODAY, B_TODAY]),
    _group("effective", "power", "combined", "slot", "pv", SLOT_VALUES, [PV_SUM]),
    _group("effective", "power", "source", "slot", "pv", SLOT_VALUES, [A_EFFECTIVE, B_EFFECTIVE]),
    _group("pv", "share", "system", "slot", "share", SLOT_VALUES, [PV_SHARE]),
    _group(
        "smoothed",
        "power",
        "source",
        "slot",
        "pv",
        SLOT_VALUES,
        ["sensor.effy_pv_a_smoothed", "sensor.effy_pv_b_smoothed"],
    ),
]
EXPECTED_SOURCES = {
    PV_A: [A_EFFECTIVE, A_TODAY, "sensor.effy_pv_a_smoothed"],
    PV_B: [B_EFFECTIVE, B_TODAY, "sensor.effy_pv_b_smoothed"],
}

# ADR-029's table: which of its rows the entry configures, and with which of the row's series
CONFIGURED_ROWS = {
    "PV input": ["effective", "smoothed"],  # power sensors: no derived series
    "Effective PV Sum now": ["effective"],
    "PV energy today, per PV input": ["effective"],
    "PV energy today, all PV": ["effective"],
    "PV energy total, all PV": ["effective"],
    "PV share now": ["pv"],
}


def _adr_029_rows() -> dict[str, list[str]]:
    """The rows of the table 'Published entries' of ADR-029: name → the eight cells."""
    text = (_ROOT / "adr" / "029-household-consumption-and-contract-types.md").read_text(encoding="utf-8")
    table = text.split("### Published entries", 1)[1].split("## Alternatives considered", 1)[0]
    rows = {}
    for line in table.splitlines():
        if line.startswith("|") and not line.startswith("| --") and "series" not in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            rows[cells[0]] = cells[1:]
    return rows


class TestThePublishedList:
    async def test_the_real_recalculated_from_sensor_publishes_exactly_these_groups(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        attributes = rig.entity(RECALCULATED_FROM).extra_state_attributes
        assert attributes["schema_version"] == exports.EFFY_PUBLISHED_SCHEMA_VERSION == 2
        assert attributes["sensors"] == EXPECTED_GROUPS
        assert attributes["sources"] == EXPECTED_SOURCES
        assert attributes["max_history_days"] == 5

    async def test_every_group_has_every_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rig = await _Rig(monkeypatch).ready()
        for group in rig.entity(RECALCULATED_FROM).extra_state_attributes["sensors"]:
            assert set(group) >= set(GROUP_KEYS)

    async def test_every_row_of_the_adr_029_table_that_the_entry_configures_is_published(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        published = rig.entity(RECALCULATED_FROM).extra_state_attributes["sensors"]
        table = _adr_029_rows()
        assert set(CONFIGURED_ROWS) <= set(table)
        found = []
        for name, series_wanted in CONFIGURED_ROWS.items():
            series_cell, quantity, scope, span, role, direction, function = table[name]
            for series in series_cell.split(" / "):
                if series not in series_wanted:
                    continue
                matching = [
                    g
                    for g in published
                    if (g["series"], g["quantity"], g["scope"], g["span"])
                    == (series, quantity, scope, span)
                    and (g["role"], g["direction"], g["function"]) == (role, direction, function)
                ]
                assert len(matching) == 1, f"{name} / {series}"
                found.append(matching[0]["sensors"])
        # and nothing else is published: the rows found are all the groups there are
        assert len(found) == len(published) == 7
        assert sorted(e for sensors in found for e in sensors) == sorted(
            e for g in published for e in g["sensors"]
        )

    async def test_the_pv_sensors_are_found_by_their_fixed_entity_ids_too(self) -> None:
        assert exports.EFFY_PV_SUM_ENTITY_ID == PV_SUM
        assert exports.EFFY_PV_SHARE_ENTITY_ID == PV_SHARE
        assert exports.EFFY_PV_ENERGY_TODAY_ENTITY_ID == ALL_TODAY
        assert exports.EFFY_PV_ENERGY_TOTAL_ENTITY_ID == ALL_TOTAL
        for symbol, series in [
            (exports.EFFY_SERIES_EFFECTIVE, "effective"),
            (exports.EFFY_QUANTITY_ENERGY, "energy"),
            (exports.EFFY_SCOPE_COMBINED, "combined"),
            (exports.EFFY_SPAN_TODAY, "today"),
            (exports.EFFY_SPAN_TOTAL, "total"),
            (exports.EFFY_FUNCTION_PV, "pv"),
            (exports.EFFY_FUNCTION_SHARE, "share"),
        ]:
            assert symbol == series

    async def test_the_energy_groups_offer_state_sum_and_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        energy = [
            g
            for g in rig.entity(RECALCULATED_FROM).extra_state_attributes["sensors"]
            if g["quantity"] == "energy"
        ]
        assert [g["aggregations"] for g in energy] == [ENERGY] * 3
        assert list(exports.EFFY_ENERGY_AGGREGATIONS) == ENERGY


# ---------------------------------------------------------------------------
# 9. Translations and the README
# ---------------------------------------------------------------------------

_TRANSLATIONS = _ROOT / "custom_components" / "effy" / "translations"


class TestTheTranslations:
    @pytest.mark.parametrize("language", ["en", "de"])
    async def test_every_sensor_of_the_entry_has_its_name_in_both_languages(
        self, monkeypatch: pytest.MonkeyPatch, language: str
    ) -> None:
        rig = await _Rig(monkeypatch).ready()
        names = json.loads((_TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))[
            "entity"
        ]["sensor"]
        keys = {k for e in rig.entities if (k := getattr(e, "_attr_translation_key", None))}
        assert {
            _const.PV_SUM_TRANSLATION_KEY,
            _const.PV_SHARE_TRANSLATION_KEY,
            _const.PV_DELTA_INPUT_TRANSLATION_KEY,
            _const.PV_DELTA_TODAY_TRANSLATION_KEY,
            _const.PV_DELTA_TOTAL_TRANSLATION_KEY,
        } <= keys
        for key in keys:
            assert names[key]["name"].strip(), (language, key)

    @pytest.mark.parametrize("language", ["en", "de"])
    def test_the_name_of_a_per_input_integral_carries_the_name_of_its_source(
        self, language: str
    ) -> None:
        names = json.loads((_TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))[
            "entity"
        ]["sensor"]
        assert "{source_name}" in names[_const.PV_DELTA_INPUT_TRANSLATION_KEY]["name"]


_README = (_ROOT / "README.md").read_text(encoding="utf-8")


class TestTheReadme:
    """The README's table of ADR titles and its contract table follow the ADRs and the code."""

    @staticmethod
    def _adr_title(number: str) -> str:
        (path,) = (_ROOT / "adr").glob(f"{number}-*.md")
        first = path.read_text(encoding="utf-8").splitlines()[0]
        return first.split(" – ", 1)[1]

    @pytest.mark.parametrize("number", ["027", "029"])
    def test_the_adr_table_lists_the_adr_with_its_title(self, number: str) -> None:
        (row,) = [line for line in _README.splitlines() if line.startswith(f"| [{number}]")]
        assert row.split("|")[2].strip().lower() == self._adr_title(number).lower()

    def test_every_fixed_entity_id_of_the_capability_is_in_the_readme(self) -> None:
        for entity_id in [PV_SUM, PV_SHARE, ALL_TODAY, ALL_TOTAL, RECALCULATED_FROM]:
            assert f"`{entity_id}`" in _README, entity_id
        assert "sensor.effy_<slug>_delta_integral_today" in _README

    def test_the_contract_table_lists_every_published_entry_of_adr_029(self) -> None:
        table = _README.split("### The published entries", 1)[1].split("\n### ", 1)[0]
        for name, cells in _adr_029_rows().items():
            rows = [line for line in table.splitlines() if line.startswith(f"| {name} |")]
            assert len(rows) == 1, name
            assert [c.strip() for c in rows[0].strip("|").split("|")][1:] == cells, name
