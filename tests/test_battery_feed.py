"""TASK-0025 — the valley machine fed from the recorder (ADR-023 decisions 6, 9).

``history.async_update_battery`` and ``async_rebuild_battery`` against the faked recorder
of ``test_history_recent_equivalence`` (statistics, raw states), on a simulated battery of
known efficiency (the ``_Battery`` of ``test_valley_spans``, whose slots become what the
recorder would hold: the derived charge and discharge power Effy wrote, the SoC's recorded
``mean``, and the capacity entity's raw states). The chain is proved against the pure
machine fed the same slots directly: the units, the missing statistics, the capacity's
history, a restart in the middle of a span, the 3-day catch-up and a span that is longer
than it, and what is read when.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left, bisect_right
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.support import role_options
from tests.test_history_recent_equivalence import _energy_world, _history, _install, _World
from tests.test_valley_spans import (
    S1,
    S4,
    T0,
    _Battery,
    bat,
    series,
    when,
)

SLOT = timedelta(minutes=5)
CH, DIS = "sensor.charge", "sensor.discharge"
SOC, CAP = "sensor.soc", "number.capacity"
CH_POWER, DIS_POWER = "sensor.effy_charge_power", "sensor.effy_discharge_power"
H = 30.0
DELAY = timedelta(minutes=125)

ValleyState = _history.ValleyState
BatSlot = _history.BatSlot
valley_feed = _history.valley_feed
_calc = _history.valley_feed.__globals__  # the calculation module history.py itself uses
valley_restore_data = _calc["valley_restore_data"]
valley_state_from_restore = _calc["valley_state_from_restore"]


def _opts(**extra: Any) -> dict[str, Any]:
    options: dict[str, Any] = role_options([], [], [CH], discharge_sensor=DIS)
    options.update(soc_sensor=SOC, capacity_entity=CAP, max_history_days=5)
    options.update(extra)
    return options


@dataclass
class _Rig:
    """The faked recorder and a log of what was asked of it."""

    world: _World
    capacity: list[tuple[datetime, str, str | None]]
    stat_calls: list[tuple[list[str], datetime, datetime]] = field(default_factory=list)
    capacity_calls: list[tuple[str, datetime, datetime]] = field(default_factory=list)

    def stats_of(self, statistic_id: str) -> list[tuple[list[str], datetime, datetime]]:
        return [call for call in self.stat_calls if statistic_id in call[0]]


def _install_rig(
    monkeypatch: pytest.MonkeyPatch,
    slots: list[Any],
    *,
    capacity_rows: Sequence[tuple[datetime, str, str | None]] | None = None,
    without_soc: Collection[int] = (),
    empty_soc: Collection[int] = (),
    without_charge: Collection[int] = (),
    without_discharge: Collection[int] = (),
) -> _Rig:
    """What the recorder would hold for ``slots`` (objects with ``start``, ``soc``,
    ``charge_kwh``, ``discharge_kwh`` and ``capacity_kwh``), minus the indexes left out."""
    world = _energy_world(**{CH: [], DIS: []})
    arrays: dict[str, tuple[list[datetime], list[float | None]]] = {
        CH_POWER: ([], []),
        DIS_POWER: ([], []),
        SOC: ([], []),
    }
    for i, slot in enumerate(slots):
        if slot.charge_kwh is not None and i not in without_charge:
            arrays[CH_POWER][0].append(slot.start)
            arrays[CH_POWER][1].append(
                slot.charge_kwh * 12.0
            )  # kWh in 5 min -> kW (a kWh counter's series is in kW)
        if slot.discharge_kwh is not None and i not in without_discharge:
            arrays[DIS_POWER][0].append(slot.start)
            arrays[DIS_POWER][1].append(slot.discharge_kwh * 12.0)
        if slot.soc is not None and i not in without_soc:
            arrays[SOC][0].append(slot.start)
            # a row the recorder compiled without a mean (a sensor that was unavailable)
            arrays[SOC][1].append(None if i in empty_soc else float(slot.soc))
    if capacity_rows is None:
        capacity_rows = [
            (s.start, repr(s.capacity_kwh), "kWh")
            if s.capacity_kwh is not None
            else (s.start, "unavailable", None)
            for s in slots
        ]
    rig = _Rig(world=world, capacity=sorted(capacity_rows, key=lambda row: row[0]))
    _install(monkeypatch, world)

    def fetch_stats(
        _hass: Any, eids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        rig.stat_calls.append((list(eids), start, end))
        result: dict[str, list[dict[str, Any]]] = {}
        for eid in eids:
            times, values = arrays.get(eid, ([], []))
            lo, hi = bisect_left(times, start), bisect_right(times, end - SLOT)
            result[eid] = [{"start": t, "mean": v} for t, v in zip(times[lo:hi], values[lo:hi])]
        return result

    def fetch_capacity(
        _hass: Any, eid: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, str, str | None]]:
        rig.capacity_calls.append((eid, start, end))
        times = [row[0] for row in rig.capacity]
        before = bisect_right(times, start - timedelta(microseconds=1))
        head = [(start, rig.capacity[before - 1][1], rig.capacity[before - 1][2])] if before else []
        return head + rig.capacity[bisect_left(times, start) : bisect_right(times, end)]

    monkeypatch.setattr(_history, "_fetch_statistics", fetch_stats)
    monkeypatch.setattr(_history, "_fetch_capacity_states", fetch_capacity)
    return rig


def _now_of(index: int) -> datetime:
    """The ``now`` at which slot ``index`` is the last one the machine may take."""
    return when(index) + DELAY


def _close(a: Any, b: Any, path: str = "") -> None:
    """Two dumps are equal; numbers within the rounding of kWh -> W -> kWh."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys(), path
        for key in a:
            _close(a[key], b[key], f"{path}.{key}")
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), path
        for n, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{n}]")
    elif isinstance(a, float) and isinstance(b, float):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-12), path
    else:
        assert a == b, path


def _direct(slots: list[Any], hysteresis: float = H) -> Any:
    """The pure machine fed the same slots directly: what the chain must give."""
    return valley_feed(
        ValleyState(),
        [BatSlot(s.start, s.soc, s.charge_kwh, s.discharge_kwh, s.capacity_kwh) for s in slots],
        when(len(slots) - 1) + DELAY,
        hysteresis,
    )


async def _update(state: Any, now: datetime, options: dict[str, Any] | None = None) -> Any:
    return await _history.async_update_battery(object(), options or _opts(), state, now)


# ---------------------------------------------------------------------------
# What is enabled
# ---------------------------------------------------------------------------


class TestEnabled:
    def test_the_machine_needs_charge_discharge_soc_and_capacity(self) -> None:
        assert _history.battery_enabled(_opts())
        for key in ("charge_sensor", "discharge_sensor", "soc_sensor", "capacity_entity"):
            options = _opts()
            del options[key]
            assert not _history.battery_enabled(options), key
            options[key] = ""  # an emptied picker is unset too
            assert not _history.battery_enabled(options), key

    def test_a_charge_discharge_pair_and_efficiency_do_not_make_a_battery(self) -> None:
        options = role_options([], [], [CH], discharge_sensor=DIS, grid_export_sensor="sensor.go")
        assert not _history.battery_enabled(options)  # no SoC, no capacity


# ---------------------------------------------------------------------------
# What is read, and when
# ---------------------------------------------------------------------------


class TestWhatIsRead:
    def _slots(self, n: int = 40) -> list[Any]:
        return series([10] * n)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "missing", ["soc_sensor", "capacity_entity", "charge_sensor", "discharge_sensor"]
    )
    async def test_nothing_is_read_when_the_machine_is_not_configured(
        self, monkeypatch: pytest.MonkeyPatch, missing: str
    ) -> None:
        rig = _install_rig(monkeypatch, self._slots())
        options = _opts()
        del options[missing]
        state = ValleyState()
        assert await _update(state, _now_of(39), options) is state
        assert rig.stat_calls == [] and rig.capacity_calls == []

    @pytest.mark.asyncio
    async def test_it_reads_the_soc_the_two_derived_series_and_the_capacity_states(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _install_rig(monkeypatch, self._slots())
        await _update(ValleyState(), _now_of(39))
        asked = {eid for ids, _s, _e in rig.stat_calls for eid in ids}
        assert asked == {SOC, CH_POWER, DIS_POWER}  # no grid counter, no standby, no source
        assert [call[0] for call in rig.capacity_calls] == [CAP]
        assert len(rig.capacity_calls) == 1  # one read of the capacity's history per update

    @pytest.mark.asyncio
    async def test_the_grid_counters_are_not_read_even_when_they_are_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # a full system: the efficiency machines read these, the battery machine does not
        rig = _install_rig(monkeypatch, self._slots())
        for eid in ("sensor.grid_in", "sensor.grid_out"):
            rig.world.units[eid] = "kWh"
        options = _opts(grid_import_sensor="sensor.grid_in", grid_export_sensor="sensor.grid_out")
        await _update(ValleyState(), _now_of(39), options)
        asked = {eid for ids, _s, _e in rig.stat_calls for eid in ids}
        assert asked == {SOC, CH_POWER, DIS_POWER}

    @pytest.mark.asyncio
    async def test_only_settled_slots_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rig = _install_rig(monkeypatch, self._slots())
        state = await _update(ValleyState(), _now_of(30))
        assert state.last_slot == when(30)
        assert max(end for _ids, _s, end in rig.stat_calls) == when(31)
        assert max(end for _e, _s, end in rig.capacity_calls) == when(31)

    @pytest.mark.asyncio
    async def test_a_slot_that_is_not_125_minutes_old_is_not_taken(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_rig(monkeypatch, self._slots())
        state = await _update(ValleyState(), _now_of(30) - timedelta(seconds=1))
        assert state.last_slot == when(29)

    @pytest.mark.asyncio
    async def test_nothing_is_read_when_there_is_no_new_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _install_rig(monkeypatch, self._slots())
        state = await _update(ValleyState(), _now_of(30))
        rig.stat_calls.clear()
        rig.capacity_calls.clear()
        again = await _update(state, _now_of(30) + timedelta(minutes=4))
        assert again is state
        assert rig.stat_calls == [] and rig.capacity_calls == []

    @pytest.mark.asyncio
    async def test_the_next_job_reads_only_the_new_slots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _install_rig(monkeypatch, self._slots())
        state = await _update(ValleyState(), _now_of(30))
        rig.stat_calls.clear()
        rig.capacity_calls.clear()
        state = await _update(state, _now_of(31))
        assert state.last_slot == when(31)
        assert {start for _ids, start, _end in rig.stat_calls} == {when(31)}
        assert {start for _eid, start, _end in rig.capacity_calls} == {when(31)}

    @pytest.mark.asyncio
    async def test_a_first_start_catches_up_three_days_at_most(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _install_rig(monkeypatch, self._slots())
        now = when(39) + DELAY + timedelta(days=10)
        await _update(ValleyState(), now)
        floor = now - DELAY - timedelta(days=3)
        assert all(start >= floor - SLOT for _ids, start, _end in rig.stat_calls)
        assert all(start >= floor - SLOT for _eid, start, _end in rig.capacity_calls)
        assert _history.EFFICIENCY_CATCHUP_MAX == timedelta(days=3)

    @pytest.mark.asyncio
    async def test_the_feed_only_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rig = _install_rig(monkeypatch, series(S4))
        await _update(ValleyState(), _now_of(len(S4) - 1))
        assert rig.world.write_calls == [] and rig.world.write_flags == []
        assert rig.world.store == {}

    @pytest.mark.asyncio
    async def test_a_failing_read_raises_and_the_callers_state_is_not_touched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_rig(monkeypatch, series(S4))

        def boom(*_a: Any) -> Any:
            raise RuntimeError("recorder gone")

        monkeypatch.setattr(_history, "_fetch_capacity_states", boom)
        state = ValleyState()
        with pytest.raises(RuntimeError):
            await _update(state, _now_of(len(S4) - 1))
        assert state == ValleyState()


# ---------------------------------------------------------------------------
# The slots the machine gets
# ---------------------------------------------------------------------------


class TestTheSlotsTheMachineGets:
    @pytest.mark.asyncio
    async def test_the_chain_gives_what_the_machine_gives_for_the_same_slots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots = series(S4)
        _install_rig(monkeypatch, slots)
        state = await _update(ValleyState(), _now_of(len(S4) - 1))
        assert state.estimate.counted_spans == 2
        _close(valley_restore_data(state, H), valley_restore_data(_direct(slots), H))

    @pytest.mark.asyncio
    async def test_the_energy_is_the_derived_power_times_five_minutes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 12 kW for 5 minutes is 1 kWh (the derived series of a kWh counter is in kW)
        slots = [bat(i, s, charge=1.0, discharge=0.5, capacity=1.0) for i, s in enumerate(S1)]
        _install_rig(monkeypatch, slots)
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.estimate.counted_spans == 1
        # the span from slot 5 to 13: 8 slots between the halves of its ends: 8 kWh charged,
        # 4 kWh discharged, the SoC 3 points lower (-0.03 kWh at 1 kWh)
        assert state.estimate.last_value == pytest.approx(4.0 / (8.0 + 0.03))

    @pytest.mark.asyncio
    async def test_a_missing_soc_slot_reaches_the_machine_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots = series(S1)
        _install_rig(monkeypatch, slots, without_soc={10})
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.estimate.counted_spans == 0  # the detection reset in the descent
        slots[10] = bat(10, None)
        _close(valley_restore_data(state, H), valley_restore_data(_direct(slots), H))

    @pytest.mark.asyncio
    async def test_a_soc_row_without_a_mean_is_a_missing_soc_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots = series(S1)
        _install_rig(monkeypatch, slots, empty_soc={10})
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.estimate.counted_spans == 0  # the descent was cut by a reset
        slots[10] = bat(10, None)
        _close(valley_restore_data(state, H), valley_restore_data(_direct(slots), H))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["charge", "discharge"])
    async def test_a_missing_energy_statistic_in_the_span_drops_it_never_counts_it_as_zero(
        self, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        _install_rig(
            monkeypatch,
            series(S1),
            without_charge={8} if kind == "charge" else (),
            without_discharge={8} if kind == "discharge" else (),
        )
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.estimate.counted_spans == 0
        assert state.dropped_spans == 1 and state.last_drop == "missing_energy"

    @pytest.mark.asyncio
    async def test_a_missing_energy_statistic_outside_the_span_does_not_matter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_rig(monkeypatch, series(S1), without_charge={2, 15}, without_discharge={3, 16})
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.estimate.counted_spans == 1 and state.dropped_spans == 0

    @pytest.mark.asyncio
    async def test_slots_that_have_nothing_at_all_are_gaps_that_reset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # the first slots of the window are before the recorder knew anything
        slots = series(S4)
        _install_rig(monkeypatch, slots)
        state = await _update(ValleyState(), _now_of(len(S4) - 1))
        assert state.last_slot == when(len(S4) - 1)
        assert state.estimate.counted_spans == 2  # the empty slots before did no harm


class TestTheCapacityTheMachineGets:
    def _rows(self, *rows: tuple[int, str, str | None]) -> list[tuple[datetime, str, str | None]]:
        return [(T0 + timedelta(minutes=m), state, unit) for m, state, unit in rows]

    @pytest.mark.asyncio
    async def test_a_capacity_in_wh_is_the_same_as_in_kwh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots = series(S4)
        _install_rig(monkeypatch, slots, capacity_rows=self._rows((0, "1.0", "kWh")))
        kwh = await _update(ValleyState(), _now_of(len(S4) - 1))
        _install_rig(monkeypatch, slots, capacity_rows=self._rows((0, "1000", "Wh")))
        wh = await _update(ValleyState(), _now_of(len(S4) - 1))
        assert kwh.estimate.counted_spans == 2
        _close(valley_restore_data(wh, H), valley_restore_data(kwh, H))

    @pytest.mark.asyncio
    async def test_a_capacity_without_a_unit_drops_every_span_it_is_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # the options flow accepts such an entity (it cannot judge it); the feed does not guess
        _install_rig(monkeypatch, series(S4), capacity_rows=self._rows((0, "1.0", None)))
        state = await _update(ValleyState(), _now_of(len(S4) - 1))
        assert state.estimate.counted_spans == 0
        assert state.dropped_spans == 2 and state.last_drop == "capacity_unavailable"

    @pytest.mark.asyncio
    async def test_a_unit_that_is_not_energy_drops_the_spans_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_rig(monkeypatch, series(S1), capacity_rows=self._rows((0, "1.0", "MWh")))
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.dropped_spans == 1 and state.last_drop == "capacity_unavailable"

    @pytest.mark.asyncio
    async def test_an_unavailable_capacity_inside_the_span_drops_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = self._rows((0, "1.0", "kWh"), (40, "unavailable", None), (45, "1.0", "kWh"))
        _install_rig(monkeypatch, series(S1), capacity_rows=rows)
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.dropped_spans == 1 and state.last_drop == "capacity_unavailable"

    @pytest.mark.asyncio
    async def test_a_capacity_that_changes_inside_the_span_drops_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # S1's span is slots 5 to 13 (25 to 65 minutes): the entity changes at minute 40
        rows = self._rows((0, "1.0", "kWh"), (40, "1.5", "kWh"))
        _install_rig(monkeypatch, series(S1), capacity_rows=rows)
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.dropped_spans == 1 and state.last_drop == "capacity_changed"

    @pytest.mark.asyncio
    async def test_a_capacity_that_changes_outside_the_span_does_not_matter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # in the first valley (before the span begins at slot 5, minute 25), as for a stack
        # upgrade that happens at night
        rows = self._rows((0, "1.0", "kWh"), (10, "1.5", "kWh"))
        _install_rig(monkeypatch, series(S1), capacity_rows=rows)
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.estimate.counted_spans == 1 and state.dropped_spans == 0

    @pytest.mark.asyncio
    async def test_the_capacity_in_effect_before_the_window_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # one state, long before the first slot read: the recorder returns it as the state at
        # the start of the window, and it is the capacity of every slot
        rows = [(T0 - timedelta(days=30), "1.0", "kWh")]
        _install_rig(monkeypatch, series(S1), capacity_rows=rows)
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.estimate.counted_spans == 1

    @pytest.mark.asyncio
    async def test_no_capacity_history_at_all_drops_the_spans(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # the only state the entity has is from the future: none is in effect in the window
        rows = [(T0 + timedelta(days=500), "1", "kWh")]
        _install_rig(monkeypatch, series(S1), capacity_rows=rows)
        state = await _update(ValleyState(), _now_of(len(S1) - 1))
        assert state.estimate.counted_spans == 0 and state.last_drop == "capacity_unavailable"


# ---------------------------------------------------------------------------
# A simulated battery: restart, catch-up, a span that is lost
# ---------------------------------------------------------------------------

ETA = 0.92
DAYS = 10
PER_DAY = 288
_SIM: list[Any] = []  # [(slots, baseline state)], computed once: the run is deterministic


def _sim_slots() -> list[Any]:
    battery = _Battery(eta=ETA, seed=3)
    return [slot for _ in range(DAYS) for slot in battery.day()]


async def _live(state: Any, first: int, last: int, options: dict[str, Any] | None = None) -> Any:
    """The slot job every 5 minutes: the update at the time slot ``n`` has just settled."""
    for n in range(first, last + 1):
        state = await _history.async_update_battery(object(), options or _opts(), state, _now_of(n))
    return state


async def _baseline(monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], Any]:
    if not _SIM:
        slots = _sim_slots()
        _install_rig(monkeypatch, slots)
        _SIM.append((slots, await _live(ValleyState(), 0, len(slots) - 1)))
    slots, state = _SIM[0]
    _install_rig(monkeypatch, slots)
    return slots, state


class TestTheSimulatedBattery:
    @pytest.mark.asyncio
    async def test_the_chain_measures_the_efficiency_in_the_right_units(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _slots, whole = await _baseline(monkeypatch)
        assert whole.estimate.counted_spans >= 8
        assert whole.estimate.value == pytest.approx(ETA, abs=0.02)
        assert whole.last_slot == when(DAYS * PER_DAY - 1)

    @pytest.mark.asyncio
    async def test_live_operation_equals_the_machine_fed_the_slots_directly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, whole = await _baseline(monkeypatch)
        # the live run began with an empty 3-day window of nothing before the data; the
        # direct feed gets the data only: the same machine, so the same state
        direct = _direct(slots)
        _close(valley_restore_data(whole, H), valley_restore_data(direct, H))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cut_slot, what",
        [
            (3 * PER_DAY + 150, "the afternoon of day 4, in the middle of a span"),
            (4 * PER_DAY + 40, "the night of day 5, in a valley"),
            (6 * PER_DAY + 100, "the morning of day 7"),
            (8 * PER_DAY + 200, "the evening of day 9, in the descent"),
        ],
    )
    async def test_a_restart_in_the_middle_equals_uninterrupted_operation(
        self, monkeypatch: pytest.MonkeyPatch, cut_slot: int, what: str
    ) -> None:
        slots, whole = await _baseline(monkeypatch)
        first_half = await _live(ValleyState(), 0, cut_slot)
        # the sensor stored it as JSON, Home Assistant restarted, the sensor restored it
        data = json.loads(json.dumps(valley_restore_data(first_half, H)))
        restored = valley_state_from_restore(data, H)
        assert restored == first_half, what
        resumed = await _live(restored, cut_slot + 1, len(slots) - 1)
        assert resumed == whole, what

    @pytest.mark.asyncio
    async def test_a_cut_is_in_the_middle_of_a_span_and_of_a_descent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # the cuts above are not accidents: at least one of them has an open span, and one
        # has the running minima of a descent in the state
        _slots, _whole = await _baseline(monkeypatch)
        afternoon = await _live(ValleyState(), 0, 3 * PER_DAY + 150)
        evening = await _live(afternoon, 3 * PER_DAY + 151, 8 * PER_DAY + 200)
        assert afternoon.span is not None
        assert evening.span is not None and evening.records

    @pytest.mark.asyncio
    async def test_a_restart_with_an_older_state_catches_up_to_the_same_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, whole = await _baseline(monkeypatch)
        older = await _live(ValleyState(), 0, 7 * PER_DAY + 30)  # two days and more behind
        caught_up = await _update(older, _now_of(len(slots) - 1))
        assert caught_up == whole

    @pytest.mark.asyncio
    async def test_a_first_start_catches_up_what_the_last_three_days_hold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, _whole = await _baseline(monkeypatch)
        fresh = await _update(ValleyState(), _now_of(len(slots) - 1))
        window = slots[len(slots) - 1 - 3 * PER_DAY :]
        expected = valley_feed(
            ValleyState(),
            [
                BatSlot(s.start, s.soc, s.charge_kwh, s.discharge_kwh, s.capacity_kwh)
                for s in window
            ],
            _now_of(len(slots) - 1),
            H,
        )
        _close(valley_restore_data(fresh, H), valley_restore_data(expected, H))
        assert fresh.estimate.counted_spans >= 2

    @pytest.mark.asyncio
    async def test_a_span_longer_than_the_catch_up_window_is_lost_and_nothing_is_corrupted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, _whole = await _baseline(monkeypatch)
        cut = 3 * PER_DAY + 150  # a span is open
        before = await _live(ValleyState(), 0, cut)
        assert before.span is not None and before.estimate.counted_spans >= 2
        # the machine was off for six days: the state is restored, the window reaches back
        # three days, and the span that was open then has no end left to find
        now = _now_of(len(slots) - 1)
        assert (now - DELAY) - before.last_slot > timedelta(days=3)
        lost = await _update(before, now)
        fresh = await _update(ValleyState(), now)
        # what it knows of the present is what a first start knows ...
        detection = ("low", "band", "span", "peak", "records", "last_slot")
        assert [getattr(lost, f) for f in detection] == [getattr(fresh, f) for f in detection]
        # ... plus what was measured before (the lost span is in neither)
        assert (
            lost.estimate.counted_spans
            == before.estimate.counted_spans + fresh.estimate.counted_spans
        )
        assert lost.estimate.counted_kwh == pytest.approx(
            before.estimate.counted_kwh + fresh.estimate.counted_kwh
        )
        assert lost.dropped_spans == before.dropped_spans + fresh.dropped_spans
        assert lost.estimate.value is not None
        assert lost.estimate.value == pytest.approx(ETA, abs=0.03)

    @pytest.mark.asyncio
    async def test_the_estimate_is_refused_when_the_hysteresis_changed_and_a_replay_finds_it_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, whole = await _baseline(monkeypatch)
        data = valley_restore_data(whole, H)
        assert valley_state_from_restore(data, 40.0) is None  # the options changed
        # the caller then starts from nothing and the first update replays three days
        replay = await _update(ValleyState(), _now_of(len(slots) - 1), _opts(valley_hysteresis=40))
        assert replay.estimate.counted_spans >= 1

    @pytest.mark.asyncio
    async def test_the_configured_hysteresis_is_the_one_the_machine_runs_with(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, _whole = await _baseline(monkeypatch)
        now = _now_of(len(slots) - 1)
        for hysteresis in (20, 30, 45):
            state = await _update(ValleyState(), now, _opts(valley_hysteresis=hysteresis))
            window = slots[len(slots) - 1 - 3 * PER_DAY :]
            expected = valley_feed(
                ValleyState(),
                [
                    BatSlot(s.start, s.soc, s.charge_kwh, s.discharge_kwh, s.capacity_kwh)
                    for s in window
                ],
                now,
                float(hysteresis),
            )
            _close(
                valley_restore_data(state, hysteresis), valley_restore_data(expected, hysteresis)
            )

    @pytest.mark.asyncio
    async def test_a_hysteresis_below_the_minimum_is_raised_not_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, _whole = await _baseline(monkeypatch)
        now = _now_of(len(slots) - 1)
        edited = await _update(ValleyState(), now, _opts(valley_hysteresis=5))  # a hand edit
        minimum = await _update(ValleyState(), now, _opts(valley_hysteresis=15))
        assert edited == minimum


class TestTheRebuild:
    @pytest.mark.asyncio
    async def test_a_rebuild_replays_the_whole_history_window_from_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, _whole = await _baseline(monkeypatch)
        rig = _install_rig(monkeypatch, slots)
        now = _now_of(len(slots) - 1)
        rebuilt = await _history.async_rebuild_battery(object(), _opts(), now)
        # max_history_days is 5 and the recorder keeps 10: five days
        floor = now - DELAY - timedelta(days=5)
        assert min(start for _ids, start, _end in rig.stat_calls) >= floor - SLOT
        window = slots[len(slots) - 1 - 5 * PER_DAY :]
        expected = valley_feed(
            ValleyState(),
            [
                BatSlot(s.start, s.soc, s.charge_kwh, s.discharge_kwh, s.capacity_kwh)
                for s in window
            ],
            now,
            H,
        )
        _close(valley_restore_data(rebuilt, H), valley_restore_data(expected, H))
        assert rebuilt.estimate.counted_spans >= 4

    @pytest.mark.asyncio
    async def test_the_next_slot_job_is_idempotent_over_what_the_rebuild_replayed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, whole = await _baseline(monkeypatch)
        rig = _install_rig(monkeypatch, slots)
        now = _now_of(len(slots) - 1)
        rebuilt = await _history.async_rebuild_battery(object(), _opts(), now)
        rig.stat_calls.clear()
        rig.capacity_calls.clear()
        # the job of the same slot reads nothing and changes nothing
        assert await _update(rebuilt, now) is rebuilt
        assert rig.stat_calls == [] and rig.capacity_calls == []
        # the job of the next slot takes that one slot on and agrees with a long run
        again = await _update(rebuilt, now + SLOT)
        assert again.last_slot == rebuilt.last_slot + SLOT
        assert {start for _ids, start, _end in rig.stat_calls} == {now - DELAY + SLOT}

    @pytest.mark.asyncio
    async def test_the_rebuild_window_is_the_one_of_the_charge_and_discharge_efficiency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slots, _whole = await _baseline(monkeypatch)
        rig = _install_rig(monkeypatch, slots)
        now = _now_of(len(slots) - 1)
        assert _history.efficiency_rebuild_window(_opts(), 10) == timedelta(days=5)
        await _history.async_rebuild_battery(object(), _opts(max_history_days=2), now)
        assert (
            min(start for _ids, start, _end in rig.stat_calls)
            >= now - DELAY - timedelta(days=2) - SLOT
        )

    @pytest.mark.asyncio
    async def test_a_rebuild_without_a_battery_reads_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rig = _install_rig(monkeypatch, series(S1))
        options = _opts()
        del options["capacity_entity"]
        rebuilt = await _history.async_rebuild_battery(object(), options, _now_of(len(S1) - 1))
        assert rebuilt == ValleyState()
        assert rig.stat_calls == [] and rig.capacity_calls == []


def test_the_float_helpers_of_this_file_are_not_the_thing_under_test() -> None:
    # _close is a test tool: it must reject a real difference
    with pytest.raises(AssertionError):
        _close({"a": 1.0}, {"a": 1.1})
    with pytest.raises(AssertionError):
        _close({"a": [1, 2]}, {"a": [1, 3]})
    _close({"a": [1.0, math.pi]}, {"a": [1.0 + 1e-12, math.pi]})
