"""
End-to-end equivalence test: slot-timer path vs. full history recalc
(ADR-014 Amendment 2026-10-05, item 7; capability CAP-1).

Runs the *real* ``history.async_recalculate_recent`` cycle by cycle (the way
EffyCoordinator's slot timer does, every 5 minutes at HH:MM:05) against a
faked recorder, then runs the real ``history.async_recalculate_history`` over
the same raw data, and asserts the two leave identical statistics for every
closed slot — in particular that a low-resolution counter jump spread over
more than RECENT_RECALC_WINDOW is no longer left half-written (the BMS
battery-charge bug: only ~half of the energy survived, with 0-gaps between
ticks).

history.py needs ``homeassistant.components.recorder`` & friends, which are
impractical to run without a real HA instance. Like
tests/test_coordinator_slot.py, HA is stubbed via ``sys.modules`` (no HA
install needed); unlike it, the *real* history.py is loaded, and only its
recorder-facing helpers (raw-state fetch, offline-anchor lookback, statistics
fetch/write, unit and state-class lookups) are replaced by in-memory fakes.
The stubs are registered only while the modules load and removed again
afterwards, and the loaded package has a private name, so this file neither
clobbers nor leaks into the other test modules' own stubs.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_BASE = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_PKG = "effy_history_under_test"
_UTC = timezone.utc


def _ts(h: int, m: int, s: int = 0, day: int = 1) -> datetime:
    return datetime(2024, 1, day, h, m, s, tzinfo=_UTC)


# ---------------------------------------------------------------------------
# Loading the real history.py with HA stubbed (temporarily)
# ---------------------------------------------------------------------------


class _SensorStateClass:
    TOTAL_INCREASING = "total_increasing"
    TOTAL = "total"
    MEASUREMENT = "measurement"


def _load_history() -> tuple[ModuleType, ModuleType]:
    """Return (history, const) loaded from the real files under a private
    package name, with HA stubbed only for the duration of the load."""
    stub_names = [
        "homeassistant",
        "homeassistant.core",
        "homeassistant.util",
        "homeassistant.components",
        "homeassistant.components.sensor",
        "homeassistant.components.recorder",
        "homeassistant.components.recorder.db_schema",
        "homeassistant.components.recorder.models",
    ]
    module_names = [f"{_PKG}.{n}" for n in ("const", "calculation", "sensor_utils", "history")]
    touched = [*stub_names, _PKG, *module_names]
    saved: dict[str, ModuleType | None] = {name: sys.modules.get(name) for name in touched}

    def stub(name: str, **attrs: Any) -> None:
        mod = ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod

    try:
        stub("homeassistant")
        stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
        stub("homeassistant.util", slugify=lambda s: s.lower().replace(".", "_"))
        stub("homeassistant.components")
        stub("homeassistant.components.sensor", SensorStateClass=_SensorStateClass)
        stub("homeassistant.components.recorder", get_instance=lambda hass: None)
        stub(
            "homeassistant.components.recorder.db_schema",
            Statistics=object,
            StatisticsShortTerm=object,
        )
        # No StatisticMeanType here on purpose: history.py's defensive
        # ImportError branch handles it (older-HA code path).
        stub("homeassistant.components.recorder.models", StatisticData=dict, StatisticMetaData=dict)

        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(_BASE)]
        pkg.__package__ = _PKG
        sys.modules[_PKG] = pkg

        loaded: dict[str, ModuleType] = {}
        for name in ("const", "calculation", "sensor_utils", "history"):
            spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _BASE / f"{name}.py")
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            mod.__package__ = _PKG
            sys.modules[f"{_PKG}.{name}"] = mod
            setattr(pkg, name, mod)
            spec.loader.exec_module(mod)
            loaded[name] = mod
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return loaded["history"], loaded["const"]


_history, _const = _load_history()


# ---------------------------------------------------------------------------
# In-memory recorder world
# ---------------------------------------------------------------------------


@dataclass
class _World:
    """What the faked recorder knows, plus a log of how history.py used it."""

    energy: dict[str, list[tuple[datetime, str]]] = field(default_factory=dict)
    power_means: dict[str, dict[datetime, float]] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    state_classes: dict[str, str] = field(default_factory=dict)
    # entity_id -> {slot_start: value}; whatever was last written (ADR-004: overwrite)
    store: dict[str, dict[datetime, float]] = field(default_factory=dict)
    raw_calls: list[tuple[str, datetime, datetime]] = field(default_factory=list)
    anchor_calls: list[str] = field(default_factory=list)
    stats_calls: list[tuple[datetime, datetime]] = field(default_factory=list)
    # include_long_term flag of every statistics write, in call order (ADR-011 D2)
    write_flags: list[bool] = field(default_factory=list)
    # one record per timer cycle run by _run_timer_cycles
    cycles: list[_Cycle] = field(default_factory=list)


@dataclass
class _Cycle:
    """What one slot-timer cycle did, as the coordinator would see it."""

    now: datetime
    earliest: datetime | None
    touched: set[str]
    last_values: dict[str, tuple[float, str]]
    anchor_queries: int
    write_flags: list[bool]


class _FakeRecorder:
    async def async_add_executor_job(self, fn: Callable[..., Any], *args: Any) -> Any:
        return fn(*args)


class _FrozenDatetime(datetime):
    frozen: datetime = _ts(0, 0)

    @classmethod
    def now(cls, tz: Any = None) -> _FrozenDatetime:
        return cls.frozen  # type: ignore[return-value]


def _install(monkeypatch: pytest.MonkeyPatch, world: _World) -> None:
    h = _history

    def fetch_raw(
        _hass: Any, eid: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, str]]:
        world.raw_calls.append((eid, start, end))
        readings = world.energy[eid]
        before = [r for r in readings if r[0] < start]
        # include_start_time_state=True: the state in effect at `start` comes
        # back *stamped with `start`*, not with when it really changed (HA's
        # recorder behaviour as far as known). Deliberately the conservative
        # model: a jump whose real t1 predates the fetch is then only seen
        # correctly by a fetch that starts early enough.
        head = [(start, before[-1][1])] if before else []
        return head + [r for r in readings if start <= r[0] <= end]

    def fetch_anchor(
        _hass: Any, eid: str, before: datetime, _max_days: int
    ) -> tuple[datetime, str] | None:
        world.anchor_calls.append(eid)
        valid = [
            r
            for r in world.energy[eid]
            if r[0] < before and h._parse_energy_state(r[1]) is not None
        ]
        return valid[-1] if valid else None

    def fetch_stats(
        _hass: Any, eids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        world.stats_calls.append((start, end))
        slot = timedelta(minutes=5)
        return {
            eid: [
                {"start": s, "mean": v}
                for s, v in sorted(world.power_means.get(eid, {}).items())
                if start <= s and s + slot <= end  # the recorder only compiled closed slots
            ]
            for eid in eids
        }

    async def write(
        _hass: Any,
        per_sensor: dict[str, dict[str, Any]],
        short_term_cutoff: datetime | None = None,
        include_long_term: bool = True,
    ) -> int:
        world.write_flags.append(include_long_term)
        written = 0
        for eid, info in per_sensor.items():
            for slot, value in info["slot_values"]:
                world.store.setdefault(eid, {})[slot] = value
                written += 1
        return written

    monkeypatch.setattr(h, "get_recorder", lambda _hass: _FakeRecorder())
    monkeypatch.setattr(h, "_get_unit", lambda _hass, eid: world.units[eid])
    monkeypatch.setattr(h, "_get_state_class", lambda _hass, eid: world.state_classes[eid])
    monkeypatch.setattr(h, "_get_statistics_units", lambda _hass, _ids: dict(world.units))
    monkeypatch.setattr(h, "_get_short_term_retention_days", lambda _hass: 10)
    monkeypatch.setattr(h, "_fetch_raw_energy_states", fetch_raw)
    monkeypatch.setattr(h, "_fetch_last_valid_state_before", fetch_anchor)
    monkeypatch.setattr(h, "_fetch_statistics", fetch_stats)
    monkeypatch.setattr(h, "_write_recorder_statistics", write)
    monkeypatch.setattr(h, "datetime", _FrozenDatetime)


def _options(inputs: list[str], outputs: list[str] | None = None) -> dict[str, Any]:
    return {
        _const.CONF_INPUT_SENSORS: inputs,
        _const.CONF_OUTPUT_SENSORS: outputs or [],
        _const.CONF_MAX_HISTORY_DAYS: 5,
    }


def _energy_world(**sensors: list[tuple[datetime, str]]) -> _World:
    world = _World()
    for eid, readings in sensors.items():
        world.energy[eid] = readings
        world.units[eid] = "kWh"
        world.state_classes[eid] = _SensorStateClass.TOTAL_INCREASING
    return world


async def _run_timer_cycles(
    world: _World,
    options: dict[str, Any],
    first: datetime,
    last: datetime,
    *,
    pause: tuple[datetime, datetime] | None = None,
    restart_after_pause: bool = False,
    catch_up: bool = True,
) -> dict[str, dict[datetime, float]]:
    """Run async_recalculate_recent every 5 minutes, the way
    EffyCoordinator's slot timer does: one reading cache shared across
    cycles (ADR-015) and the end time of the last completed cycle turned
    into ``trigger_since`` by the real ``history.recent_trigger_since``
    (ADR-014 Amendment 2026-10-06).

    ``pause=(from, to)``: no cycle runs for ``from <= now < to`` (HA blocked,
    host suspended). ``restart_after_pause``: the pause was a Home Assistant
    restart — cache and last-run marker are gone afterwards.
    ``catch_up=False`` passes no ``trigger_since`` at all: the pre-2026-10-06
    behaviour, used as a negative control.
    """
    cache: dict[str, tuple[datetime, str]] = {}
    previous_run: datetime | None = None
    now = first
    while now <= last:
        if pause is not None and pause[0] <= now < pause[1]:
            now += timedelta(minutes=5)
            if restart_after_pause and now >= pause[1]:
                cache, previous_run = {}, None
            continue
        anchors_before = len(world.anchor_calls)
        flags_before = len(world.write_flags)
        _written, earliest, touched, last_values = await _history.async_recalculate_recent(
            object(),
            options,
            now,
            energy_reading_cache=cache,
            trigger_since=_history.recent_trigger_since(previous_run, now) if catch_up else None,
        )
        previous_run = now
        world.cycles.append(
            _Cycle(
                now,
                earliest,
                touched,
                last_values,
                len(world.anchor_calls) - anchors_before,
                world.write_flags[flags_before:],
            )
        )
        now += timedelta(minutes=5)
    result = {eid: dict(slots) for eid, slots in world.store.items()}
    world.store.clear()
    return result


async def _run_full_recalc(
    world: _World, options: dict[str, Any], at: datetime
) -> dict[str, dict[datetime, float]]:
    _FrozenDatetime.frozen = at
    await _history.async_recalculate_history(object(), options)
    result = {eid: dict(slots) for eid, slots in world.store.items()}
    world.store.clear()
    return result


def _equivalent(
    timer: dict[str, dict[datetime, float]],
    full: dict[str, dict[datetime, float]],
    lo: datetime,
    hi: datetime,
) -> bool:
    """True when both stores hold the same slots and values in [lo, hi)."""
    if set(timer) != set(full):
        return False
    for eid in timer:
        a = {s: v for s, v in timer[eid].items() if lo <= s < hi}
        b = {s: v for s, v in full[eid].items() if lo <= s < hi}
        if a.keys() != b.keys() or a != pytest.approx(b):
            return False
    return True


def _fixed_window_rule(
    _jump_windows: list[tuple[datetime, datetime]],
    now: datetime,
    window: timedelta,
    _max_lookback: timedelta,
    _slot_minutes: int = 5,
    trigger_since: datetime | None = None,
) -> datetime:
    """Negative control: the pre-amendment behaviour (always exactly the window)."""
    return now - window


# The BMS battery-charge screenshot: 0.01 kWh ticks every 19-35 min.
_TICKS = [(7, 30), (10, 50), (11, 25), (11, 52), (12, 14), (12, 34), (12, 53)]


def _screenshot_readings(jitter_seconds: int = 0) -> list[tuple[datetime, str]]:
    return [(_ts(h, m, jitter_seconds), f"{5.0 + 0.01 * i:.2f}") for i, (h, m) in enumerate(_TICKS)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

SENSOR = "sensor.bms_charge"


class TestSlotTimerEqualsFullRecalc:
    @pytest.mark.parametrize("jitter_seconds", [0, 37])
    async def test_screenshot_scenario_matches_and_conserves_energy(
        self, monkeypatch: pytest.MonkeyPatch, jitter_seconds: int
    ) -> None:
        world = _energy_world(**{SENSOR: _screenshot_readings(jitter_seconds)})
        _install(monkeypatch, world)
        options = _options([SENSOR])
        last_cycle = _ts(13, 5, 5)

        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), last_cycle)
        full = await _run_full_recalc(world, options, last_cycle)

        assert _equivalent(timer, full, _ts(10, 0), _ts(13, 5))  # slot 13:05 still in progress
        # derived power is in kW-equivalent per 5-min slot: kW * (5/60) h = kWh.
        power = timer[_history._effy_power_entity_id(SENSOR)]
        energy_kwh = sum(v for s, v in power.items() if s < _ts(13, 5)) / 12
        assert energy_kwh == pytest.approx(0.06)  # 6 ticks of 0.01 kWh, none lost

    async def test_negative_control_fixed_window_is_not_equivalent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the test can fail: with the pre-amendment rule the same
        scenario loses about half the energy (the production bug)."""
        world = _energy_world(**{SENSOR: _screenshot_readings()})
        _install(monkeypatch, world)
        monkeypatch.setattr(_history, "recent_rewrite_start", _fixed_window_rule)
        options = _options([SENSOR])

        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(13, 5, 5))
        full = await _run_full_recalc(world, options, _ts(13, 5, 5))

        assert not _equivalent(timer, full, _ts(10, 0), _ts(13, 5))
        power = timer[_history._effy_power_entity_id(SENSOR)]
        assert sum(v for s, v in power.items() if s < _ts(13, 5)) / 12 < 0.04

    async def test_offline_recovery_rewrites_the_whole_outage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readings = [(_ts(8, 0), "5.00"), (_ts(8, 30), "unavailable"), (_ts(11, 0), "5.05")]
        world = _energy_world(**{SENSOR: readings})
        _install(monkeypatch, world)
        options = _options([SENSOR])

        timer = await _run_timer_cycles(world, options, _ts(8, 0, 5), _ts(11, 35, 5))
        full = await _run_full_recalc(world, options, _ts(11, 35, 5))

        assert _equivalent(timer, full, _ts(8, 0), _ts(11, 35))
        power = timer[_history._effy_power_entity_id(SENSOR)]
        assert sum(v for s, v in power.items() if s < _ts(11, 35)) / 12 == pytest.approx(0.05)

    async def test_zero_delta_offline_recovery_zero_fills_the_outage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AUDIT-0001: the counter is the same before and after the outage.
        A full recalc writes explicit 0s over the outage; the timer path must
        too (item 7), not leave "no data" until the next full recalc."""
        readings = [(_ts(8, 0), "5.00"), (_ts(8, 30), "unavailable"), (_ts(11, 0), "5.00")]
        world = _energy_world(**{SENSOR: readings})
        _install(monkeypatch, world)
        options = _options([SENSOR])

        timer = await _run_timer_cycles(world, options, _ts(8, 0, 5), _ts(11, 35, 5))
        full = await _run_full_recalc(world, options, _ts(11, 35, 5))

        assert _equivalent(timer, full, _ts(8, 0), _ts(11, 35))
        power = timer[_history._effy_power_entity_id(SENSOR)]
        outage = {s: v for s, v in power.items() if _ts(8, 30) <= s < _ts(11, 0)}
        assert len(outage) == 30  # every outage slot exists...
        assert set(outage.values()) == {0.0}  # ...and is an explicit 0

    async def test_waterfall_stays_consistent_across_sensors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Input A (jumpy kWh counter) + input C (power, mean per slot) +
        output B (kWh counter): the effective (post-waterfall) series of *all*
        sensors must match, which only holds if the extended range is global
        (amendment item 3), not per sensor."""
        a, b, c = "sensor.a_in", "sensor.b_out", "sensor.c_in"
        world = _energy_world(
            **{
                a: _screenshot_readings(),
                # sparse jump at 09:40 (window 07:40-09:40, i.e. its real start
                # lies before the slot timer's *first* raw fetch at ~10:30), then
                # regular ticks: only a second fetch from the extended start
                # sees the 07:00 reading correctly.
                b: [(_ts(7, 0), "1.00"), (_ts(9, 40), "1.01")]
                + [
                    (_ts(10, 0) + timedelta(minutes=8 * i), f"{1.01 + 0.01 * (i + 1):.2f}")
                    for i in range(22)
                ],
            }
        )
        world.units[c] = "W"
        world.state_classes[c] = _SensorStateClass.MEASUREMENT
        slot = _ts(9, 0)
        while slot < _ts(13, 30):
            world.power_means[c] = {**world.power_means.get(c, {}), slot: 90.0}
            slot += timedelta(minutes=5)
        _install(monkeypatch, world)
        options = _options([a, c], [b])

        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(13, 5, 5))
        full = await _run_full_recalc(world, options, _ts(13, 5, 5))

        assert _equivalent(timer, full, _ts(10, 0), _ts(13, 5))
        assert (
            len(timer) >= 3
        )  # effective for both inputs + derived power etc. were really compared

        # ...and the same scenario with the old fixed window is NOT equivalent.
        world2 = _energy_world(**{a: world.energy[a], b: world.energy[b]})
        world2.units[c] = "W"
        world2.state_classes[c] = _SensorStateClass.MEASUREMENT
        world2.power_means = world.power_means
        _install(monkeypatch, world2)
        monkeypatch.setattr(_history, "recent_rewrite_start", _fixed_window_rule)
        timer2 = await _run_timer_cycles(world2, options, _ts(10, 0, 5), _ts(13, 5, 5))
        full2 = await _run_full_recalc(world2, options, _ts(13, 5, 5))
        assert not _equivalent(timer2, full2, _ts(10, 0), _ts(13, 5))


class TestRewriteRangeBehaviour:
    async def test_idle_cycle_does_one_fetch_per_sensor_over_todays_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No jump in the last RECENT_RECALC_WINDOW: range, queries and
        writes are exactly what they were before the amendment."""
        world = _energy_world(**{SENSOR: [(_ts(7, 30), "5.00"), (_ts(10, 50), "5.01")]})
        _install(monkeypatch, world)
        now = _ts(12, 0, 5)

        written, earliest, _touched, _last = await _history.async_recalculate_recent(
            object(), _options([SENSOR]), now
        )

        base = now - _history.RECENT_RECALC_WINDOW
        assert world.raw_calls == [(SENSOR, base - _history._RAW_HISTORY_BOUNDARY_MARGIN, now)]
        assert earliest == _ts(11, 45)  # unaligned base 11:40:05 -> first whole slot (unchanged)
        assert written > 0

    async def test_jump_cycle_extends_globally_and_refetches_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = "sensor.c_in"
        world = _energy_world(**{SENSOR: [(_ts(7, 30), "5.00"), (_ts(10, 50), "5.01")]})
        world.units[c] = "W"
        world.state_classes[c] = _SensorStateClass.MEASUREMENT
        world.power_means[c] = {_ts(8, 0) + timedelta(minutes=5 * i): 50.0 for i in range(60)}
        _install(monkeypatch, world)
        now = _ts(10, 55, 5)

        _written, earliest, _touched, _last = await _history.async_recalculate_recent(
            object(), _options([SENSOR, c]), now
        )

        margin = _history._RAW_HISTORY_BOUNDARY_MARGIN
        base = now - _history.RECENT_RECALC_WINDOW
        extended = _ts(8, 50)  # 10:50 jump, 120 min cap -> window starts 08:50
        assert world.raw_calls == [(SENSOR, base - margin, now), (SENSOR, extended - margin, now)]
        # the power-family sensor is fetched from the same, extended start
        assert world.stats_calls == [(extended, now)]
        assert earliest == extended
        # a sensor that did not jump itself got rewritten over the extended range too
        assert min(world.store[_history._effy_entity_id(c)]) == extended

    async def test_extension_is_bounded_by_max_lookback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Offline for 3 days, recovery inside the window: the anchor lookback
        finds the pre-outage reading, but the rewrite stops at the 1-day bound."""
        readings = [
            (_ts(8, 0, day=1), "5.00"),
            (_ts(9, 0, day=1), "unavailable"),
            (_ts(11, 58, day=4), "5.05"),
        ]
        world = _energy_world(**{SENSOR: readings})
        _install(monkeypatch, world)
        now = _ts(12, 0, 5, day=4)

        _written, earliest, _touched, _last = await _history.async_recalculate_recent(
            object(), _options([SENSOR]), now
        )

        assert earliest == _ts(12, 0, day=3)  # slot-aligned (now - 1 day)
        assert earliest is not None
        assert earliest - now == pytest.approx(
            -_history.RECENT_REWRITE_MAX_LOOKBACK, abs=timedelta(minutes=5)
        )
        # the anchor was needed for both the base fetch and the extended one
        assert world.anchor_calls == [SENSOR, SENSOR]


# ---------------------------------------------------------------------------
# AUDIT-0002, Issue 1 — jumps that arrive while no cycle is running
# (ADR-014 Amendment 2026-10-06)
# ---------------------------------------------------------------------------

_PAUSE = (_ts(11, 55, 5), _ts(12, 40, 5))  # no cycle from 11:55:05 to 12:35:05


class TestCatchUpAfterPause:
    async def _run(
        self, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
    ) -> tuple[bool, float, _World]:
        world = _energy_world(**{SENSOR: _screenshot_readings()})
        _install(monkeypatch, world)
        options = _options([SENSOR])
        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(13, 5, 5), **kwargs)
        full = await _run_full_recalc(world, options, _ts(13, 5, 5))
        power = timer[_history._effy_power_entity_id(SENSOR)]
        energy_kwh = sum(v for s, v in power.items() if s < _ts(13, 5)) / 12
        return _equivalent(timer, full, _ts(10, 0), _ts(13, 5)), energy_kwh, world

    async def test_pause_longer_than_the_window_is_caught_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The audit's reproduction: the 12:14 tick arrived during the pause,
        so it was older than RECENT_RECALC_WINDOW at the first cycle after
        it and used to stay half-written (0.0418 instead of 0.0600 kWh)."""
        equivalent, energy_kwh, _ = await self._run(monkeypatch, pause=_PAUSE)
        assert equivalent
        assert energy_kwh == pytest.approx(0.06)

    async def test_home_assistant_restart_catches_up_on_the_first_cycle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Restart = reading cache and last-run marker are gone: the first
        cycle assumes a one-off RECENT_REWRITE_MAX_LOOKBACK catch-up."""
        equivalent, energy_kwh, _ = await self._run(
            monkeypatch, pause=_PAUSE, restart_after_pause=True
        )
        assert equivalent
        assert energy_kwh == pytest.approx(0.06)

    @pytest.mark.parametrize("restart", [False, True])
    async def test_negative_control_without_trigger_since_loses_energy(
        self, monkeypatch: pytest.MonkeyPatch, restart: bool
    ) -> None:
        """Proves the tests above can fail: the pre-2026-10-06 behaviour."""
        equivalent, energy_kwh, _ = await self._run(
            monkeypatch, pause=_PAUSE, restart_after_pause=restart, catch_up=False
        )
        assert not equivalent
        assert energy_kwh < 0.045

    async def test_normal_cadence_is_unchanged_by_trigger_since(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """previous_run 5 minutes ago lies inside the window: same range,
        same single fetch per sensor as without trigger_since."""
        world = _energy_world(**{SENSOR: [(_ts(7, 30), "5.00"), (_ts(10, 50), "5.01")]})
        _install(monkeypatch, world)
        now = _ts(12, 0, 5)

        _w, earliest, _t, _l = await _history.async_recalculate_recent(
            object(),
            _options([SENSOR]),
            now,
            trigger_since=_history.recent_trigger_since(now - timedelta(minutes=5), now),
        )

        base = now - _history.RECENT_RECALC_WINDOW
        assert world.raw_calls == [(SENSOR, base - _history._RAW_HISTORY_BOUNDARY_MARGIN, now)]
        assert earliest == _ts(11, 45)

    async def test_first_cycle_reads_one_day_of_raw_history_and_rewrites_only_what_changed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Catch-up cost: the first fetch reaches back RECENT_REWRITE_MAX_LOOKBACK
        (plus margin) — but without a trigger in that day nothing extra is
        rewritten, and no second fetch happens."""
        world = _energy_world(**{SENSOR: [(_ts(7, 30, day=1), "5.00")]})
        _install(monkeypatch, world)
        now = _ts(12, 0, 5, day=2)

        _w, earliest, _t, _l = await _history.async_recalculate_recent(
            object(),
            _options([SENSOR]),
            now,
            trigger_since=_history.recent_trigger_since(None, now),
        )

        expected_start = now - _history.RECENT_REWRITE_MAX_LOOKBACK
        assert world.raw_calls == [
            (SENSOR, expected_start - _history._RAW_HISTORY_BOUNDARY_MARGIN, now)
        ]
        assert earliest == _ts(11, 45, day=2)

    async def test_catch_up_does_not_rewrite_a_day_for_a_jump_older_than_the_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A long pause (3 days) must not turn an ancient jump into a full-day
        rewrite: it can no longer change anything inside the bounded range."""
        readings = [(_ts(8, 0, day=1), "5.00"), (_ts(9, 0, day=1), "5.01")]
        world = _energy_world(**{SENSOR: readings})
        _install(monkeypatch, world)
        now = _ts(12, 0, 5, day=4)

        _w, earliest, _t, _l = await _history.async_recalculate_recent(
            object(), _options([SENSOR]), now, trigger_since=_ts(9, 5, day=1)
        )

        assert earliest == _ts(11, 45, day=4)
        assert len(world.raw_calls) == 1  # no extended second fetch

    def test_trigger_since_policy(self) -> None:
        now = _ts(12, 0, 5)
        assert (
            _history.recent_trigger_since(None, now) == now - _history.RECENT_REWRITE_MAX_LOOKBACK
        )
        last = _ts(11, 30, 5)
        assert _history.recent_trigger_since(last, now) == last


# ---------------------------------------------------------------------------
# AUDIT-0002, Issue 2 — the open slot gets the previous slot's value
# (ADR-016 Amendment 2026-10-06)
# ---------------------------------------------------------------------------


def _steady_then_faster_readings(step: float = 0.002) -> list[tuple[datetime, str]]:
    """A counter ticking every minute: +``step`` kWh until 12:00, then +2*step."""
    readings: list[tuple[datetime, str]] = []
    value = 5.0
    minute = _ts(11, 0)
    while minute <= _ts(12, 10):
        value += step if minute <= _ts(12, 0) else 2 * step
        readings.append((minute, f"{value:.3f}"))
        minute += timedelta(minutes=1)
    return readings


OUT = "sensor.bms_out"


class TestOpenSlotProvisionalValue:
    def _world(self, monkeypatch: pytest.MonkeyPatch) -> _World:
        """Input 0.12 kW (0.24 kW from 12:00) and an output counter at half
        that, so the *effective* value (the output's share) is non-zero too."""
        world = _energy_world(
            **{
                SENSOR: _steady_then_faster_readings(0.002),
                OUT: _steady_then_faster_readings(0.001),
            }
        )
        _install(monkeypatch, world)
        return world

    async def test_open_slot_carries_the_previous_slots_value_and_is_pushed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._world(monkeypatch)
        power_id = _history._effy_power_entity_id(SENSOR)
        effective_id = _history._effy_entity_id(SENSOR)

        timer = await _run_timer_cycles(
            world, _options([SENSOR], [OUT]), _ts(12, 0, 5), _ts(12, 0, 5)
        )

        power, effective = timer[power_id], timer[effective_id]
        assert power[_ts(11, 55)] == pytest.approx(0.12)  # 0.002 kWh/min = 0.12 kW
        assert power[_ts(12, 0)] == power[_ts(11, 55)]  # the open slot: carried, not ~0
        assert effective[_ts(11, 55)] == pytest.approx(0.06)
        assert effective[_ts(12, 0)] == effective[_ts(11, 55)]
        cycle = world.cycles[0]
        assert cycle.last_values[power_id] == (cycle.last_values[power_id][0], "kW")
        assert cycle.last_values[power_id][0] == pytest.approx(0.12)
        assert cycle.last_values[effective_id] == (cycle.last_values[effective_id][0], "kW")
        assert cycle.last_values[effective_id][0] == pytest.approx(0.06)
        assert {power_id, effective_id} <= cycle.touched

    async def test_the_next_cycle_replaces_the_guess_with_the_real_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = self._world(monkeypatch)
        power_id = _history._effy_power_entity_id(SENSOR)

        timer = await _run_timer_cycles(
            world, _options([SENSOR], [OUT]), _ts(12, 0, 5), _ts(12, 5, 5)
        )

        # slot 12:00 was 0.12 (guess) after the first cycle; the second one
        # saw the real 0.004 kWh/min = 0.24 kW, and the new open slot 12:05
        # carries *that*.
        assert timer[power_id][_ts(12, 0)] == pytest.approx(0.24)
        assert timer[power_id][_ts(12, 5)] == pytest.approx(0.24)

    async def test_full_history_recalc_is_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Scope: only the slot-timer path carries a guess forward."""
        world = self._world(monkeypatch)
        power_id = _history._effy_power_entity_id(SENSOR)

        full = await _run_full_recalc(world, _options([SENSOR], [OUT]), _ts(12, 0, 5))

        assert full[power_id][_ts(12, 0)] < 0.01  # still the 5-second sliver, ~0

    async def test_sensor_that_is_offline_right_now_gets_no_guess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readings = [(_ts(11, 0), "5.00"), (_ts(11, 30), "5.01"), (_ts(11, 58), "unavailable")]
        world = _energy_world(**{SENSOR: readings})
        _install(monkeypatch, world)
        power_id = _history._effy_power_entity_id(SENSOR)

        timer = await _run_timer_cycles(world, _options([SENSOR]), _ts(12, 0, 5), _ts(12, 0, 5))

        assert _ts(12, 0) not in timer.get(power_id, {})

    async def test_smoothed_series_is_not_extended_into_the_open_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Power-family inputs only ever have compiled (closed) slots; there
        is no drop to 0 to fix, and nothing is invented for them."""
        c = "sensor.c_in"
        world = _energy_world(**{SENSOR: _steady_then_faster_readings()})
        world.units[c] = "W"
        world.state_classes[c] = _SensorStateClass.MEASUREMENT
        world.power_means[c] = {_ts(11, 0) + timedelta(minutes=5 * i): 80.0 for i in range(12)}
        _install(monkeypatch, world)

        timer = await _run_timer_cycles(world, _options([SENSOR, c]), _ts(12, 0, 5), _ts(12, 0, 5))

        smoothed = timer[_history._effy_smoothed_entity_id(c)]
        assert max(smoothed) == _ts(11, 55)


# ---------------------------------------------------------------------------
# AUDIT-0002 — coverage gaps found by the audit (no behaviour change)
# ---------------------------------------------------------------------------


class TestAuditCoverageGaps:
    async def test_gap_interpolation_of_power_inputs_over_the_extended_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """effy_*_smoothed (ADR-012/013 gap smoothing) next to a jumpy counter:
        the jump extends the range back over slots with missing recorder
        rows; the interpolated values must equal a full recalc's."""
        c = "sensor.c_in"
        world = _energy_world(**{SENSOR: _screenshot_readings()})
        world.units[c] = "W"
        world.state_classes[c] = _SensorStateClass.MEASUREMENT
        missing = {_ts(9, 45), _ts(10, 20), _ts(10, 25)}  # a 1-slot and a 2-slot gap
        slot = _ts(9, 0)
        while slot < _ts(13, 30):
            if slot not in missing:
                # a ramp, so interpolated values differ from both neighbours
                world.power_means.setdefault(c, {})[slot] = 50.0 + (slot.minute // 5) * 3
            slot += timedelta(minutes=5)
        _install(monkeypatch, world)
        options = _options([SENSOR, c])

        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(13, 5, 5))
        full = await _run_full_recalc(world, options, _ts(13, 5, 5))

        assert _equivalent(timer, full, _ts(10, 0), _ts(13, 5))
        smoothed = timer[_history._effy_smoothed_entity_id(c)]
        assert {_ts(10, 20), _ts(10, 25)} <= smoothed.keys()  # gap filled on the timer path
        assert smoothed[_ts(10, 20)] != smoothed[_ts(10, 15)]

    async def test_shared_cache_keeps_recorder_anchor_queries_at_the_pre_change_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-015 with the extended (second) fetch and the catch-up first
        fetch: an offline recovery arriving at 10:50, cycles 10:45-11:05.
        Recorder anchor queries per cycle; [0, 0, 1, 1, 1] is what the code
        before the ADR-014 Amendment (commit 3ad37b5) produced for this
        exact scenario."""
        readings = [(_ts(8, 0), "5.00"), (_ts(8, 30), "unavailable"), (_ts(10, 50), "5.05")]
        world = _energy_world(**{SENSOR: readings})
        _install(monkeypatch, world)

        await _run_timer_cycles(world, _options([SENSOR]), _ts(10, 45, 5), _ts(11, 5, 5))

        assert [c.anchor_queries for c in world.cycles] == [0, 0, 1, 1, 1]

    async def test_timer_path_writes_short_term_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ADR-011 Decision 2, on idle and on jump cycles alike."""
        world = _energy_world(**{SENSOR: _screenshot_readings()})
        _install(monkeypatch, world)
        options = _options([SENSOR])

        await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(13, 5, 5))
        assert all(c.write_flags for c in world.cycles)  # every cycle wrote something
        assert [f for c in world.cycles for f in c.write_flags if f] == []  # never long-term

        world.write_flags.clear()
        await _run_full_recalc(world, options, _ts(13, 5, 5))
        assert world.write_flags and all(world.write_flags)  # control: full recalc does

    async def test_live_push_on_a_jump_cycle_comes_from_the_newest_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-016 with an extended range: last_values must be the *newest*
        slot's value, not that of the extended start."""
        world = _energy_world(**{SENSOR: _screenshot_readings()})
        _install(monkeypatch, world)
        power_id = _history._effy_power_entity_id(SENSOR)
        effective_id = _history._effy_entity_id(SENSOR)

        timer = await _run_timer_cycles(world, _options([SENSOR]), _ts(12, 15, 5), _ts(12, 15, 5))

        cycle = world.cycles[0]  # the 12:14 tick just arrived: the range is extended
        assert cycle.earliest is not None
        assert cycle.earliest < _ts(12, 15, 5) - _history.RECENT_RECALC_WINDOW
        power = timer[power_id]
        newest = power[max(power)]
        assert newest > 0
        assert newest != power[cycle.earliest]  # distinguishes "newest" from "extended start"
        assert cycle.last_values[power_id] == (cycle.last_values[power_id][0], "kW")
        assert cycle.last_values[power_id][0] == pytest.approx(newest)
        effective = timer[effective_id]  # single input, no output: all loss, i.e. 0
        assert cycle.last_values[effective_id] == (cycle.last_values[effective_id][0], "kW")
        assert cycle.last_values[effective_id][0] == pytest.approx(effective[max(effective)])
        assert cycle.touched == {power_id, effective_id}
