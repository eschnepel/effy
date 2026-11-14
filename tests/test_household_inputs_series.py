"""
The series of the household meter sensors in the history computation (TASK-0048, CAP-15,
ADR-029).

A household sensor gets the series an input gets before the loss is distributed, a derived power
series (energy family) or a smoothed series (power family), by the code that makes them for an
input, and takes part in no formula. The real ``history.py`` runs against the in-memory recorder
world of the CAP-1 equivalence test (``tests/test_history_recent_equivalence.py``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tests.test_additional_sensors_series import (
    GRID,
    OUT,
    POWER,
    PV,
    _compute,
    _slots,
    _world,
)
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
)

from tests.test_history_write_only_changed import (
    REAL_UNITS,
    REAL_WRITE,
    StoredRecorder,
    _install_ha_api,
    _snapshot,
)

HI = "household_import_sensor"
HE = "household_export_sensor"
ADDITIONAL = "additional_sensors"

# The additional-sensor tests' counter (10 Wh steps, idle for hours) and gappy power sensor are the
# household import here; the export is a counter of its own (``EXPORT``) or a power sensor.
IMPORT_COUNTER = GRID
IMPORT_POWER = POWER
EXPORT_COUNTER = "sensor.house_export_counter"
EXPORT_POWER = "sensor.house_export_power"

IMPORT_COUNTER_POWER = _history._effy_power_entity_id(IMPORT_COUNTER)
IMPORT_POWER_SMOOTHED = _history._effy_smoothed_entity_id(IMPORT_POWER)
EXPORT_COUNTER_POWER = _history._effy_power_entity_id(EXPORT_COUNTER)
EXPORT_POWER_SMOOTHED = _history._effy_smoothed_entity_id(EXPORT_POWER)

_EXPORT_READINGS = [
    (_ts(9, 0), "40.0"),
    (_ts(9, 25), "50.0"),
    (_ts(10, 40), "60.0"),
]


def _household_world(*, with_inputs: bool = True) -> Any:
    world = _world(with_inputs=with_inputs)
    world.energy[EXPORT_COUNTER] = _EXPORT_READINGS
    world.units[EXPORT_COUNTER] = "Wh"
    world.state_classes[EXPORT_COUNTER] = _SensorStateClass.TOTAL_INCREASING
    world.units[EXPORT_POWER] = "W"
    world.state_classes[EXPORT_POWER] = _SensorStateClass.MEASUREMENT
    world.power_means[EXPORT_POWER] = {
        slot: 30.0 + index for index, slot in enumerate(_slots(0, 48))
    }
    return world


def _with(
    options: dict[str, Any], household_import: str | None = None, **more: Any
) -> dict[str, Any]:
    result = dict(options)
    if household_import is not None:
        result[HI] = household_import
    result.update(more)
    return result


BASE = _options([PV], [OUT])
CONSUMPTION = _const.HOUSEHOLD_CONSUMPTION_ENTITY_ID  # TASK-0049: a member of the first result


def _effective(per_sensor: dict[str, Any]) -> dict[str, Any]:
    """The effective series of the first result: the consumption series (TASK-0049) is the
    household sensors' own and is not one of them."""
    return {k: v for k, v in per_sensor.items() if k != CONSUMPTION}


class TestTheSeriesOfAHouseholdSensor:
    async def test_an_energy_counter_gets_a_derived_power_series_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eff0, power0, smooth0 = await _compute(monkeypatch, _household_world(), BASE)
        eff, power, smooth = await _compute(
            monkeypatch, _household_world(), _with(BASE, IMPORT_COUNTER)
        )
        assert set(power) - set(power0) == {IMPORT_COUNTER_POWER}
        assert _effective(eff) == eff0  # no effective series, no loss share
        assert smooth == smooth0
        assert _history._effy_entity_id(IMPORT_COUNTER) not in eff

    async def test_a_power_sensor_gets_a_smoothed_series_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eff0, power0, smooth0 = await _compute(monkeypatch, _household_world(), BASE)
        eff, power, smooth = await _compute(
            monkeypatch, _household_world(), _with(BASE, IMPORT_POWER)
        )
        assert set(smooth) - set(smooth0) == {IMPORT_POWER_SMOOTHED}
        assert _effective(eff) == eff0 and power == power0

    async def test_the_derived_power_is_the_series_an_input_gets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same machinery (ADR-029 decision 4): as an input or as a household sensor, one and the
        same counter has the same derived power series."""
        as_input = await _compute(
            monkeypatch, _household_world(), _options([PV, IMPORT_COUNTER], [OUT])
        )
        as_household = await _compute(monkeypatch, _household_world(), _with(BASE, IMPORT_COUNTER))
        assert as_household[1][IMPORT_COUNTER_POWER] == as_input[1][IMPORT_COUNTER_POWER]

    async def test_the_smoothed_series_is_the_series_an_input_gets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_input = await _compute(
            monkeypatch, _household_world(), _options([PV, IMPORT_POWER], [OUT])
        )
        as_household = await _compute(monkeypatch, _household_world(), _with(BASE, IMPORT_POWER))
        assert as_household[2][IMPORT_POWER_SMOOTHED] == as_input[2][IMPORT_POWER_SMOOTHED]

    async def test_the_series_is_the_one_an_additional_sensor_gets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for sensor, index, statistic_id in (
            (IMPORT_COUNTER, 1, IMPORT_COUNTER_POWER),
            (IMPORT_POWER, 2, IMPORT_POWER_SMOOTHED),
        ):
            household = await _compute(monkeypatch, _household_world(), _with(BASE, sensor))
            additional = await _compute(
                monkeypatch, _household_world(), _with(BASE, None, **{ADDITIONAL: [sensor]})
            )
            assert household[index][statistic_id] == additional[index][statistic_id]

    async def test_an_idle_counter_reads_an_explicit_zero_not_a_missing_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _eff, power, _smooth = await _compute(
            monkeypatch, _household_world(), _with(BASE, IMPORT_COUNTER)
        )
        values = dict(power[IMPORT_COUNTER_POWER]["slot_values"])
        idle = [_ts(11, 0) + timedelta(minutes=5 * i) for i in range(12)]  # 11:00 .. 11:55
        assert [values[slot] for slot in idle] == [0.0] * 12
        assert max(values) == _ts(12, 55)  # up to the last closed slot before ``END``

    async def test_a_low_resolution_counter_is_redistributed_with_energy_conserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _eff, power, _smooth = await _compute(
            monkeypatch, _household_world(), _with(BASE, IMPORT_COUNTER)
        )
        energy_wh = sum(v * 5 / 60 for _slot, v in power[IMPORT_COUNTER_POWER]["slot_values"])
        assert energy_wh == pytest.approx(30.0)  # three 10 Wh steps: a smooth power in W

    async def test_a_gap_of_two_slots_is_bridged_and_a_longer_one_stays_a_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _eff, _power, smooth = await _compute(
            monkeypatch, _household_world(), _with(BASE, IMPORT_POWER)
        )
        values = dict(smooth[IMPORT_POWER_SMOOTHED]["slot_values"])
        assert all(slot in values for slot in _slots(6, 2))  # 2 slots: interpolated
        assert not any(slot in values for slot in _slots(20, 3))  # 3 slots: a gap
        assert smooth[IMPORT_POWER_SMOOTHED]["unit"] == "W"

    async def test_the_export_sensor_gets_its_own_series_next_to_the_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = _with(BASE, IMPORT_COUNTER, **{HE: EXPORT_POWER})
        _eff, power, smooth = await _compute(monkeypatch, _household_world(), options)
        assert IMPORT_COUNTER_POWER in power and EXPORT_POWER_SMOOTHED in smooth
        both_counters = _with(BASE, IMPORT_POWER, **{HE: EXPORT_COUNTER})
        _eff, power, smooth = await _compute(monkeypatch, _household_world(), both_counters)
        assert IMPORT_POWER_SMOOTHED in smooth and EXPORT_COUNTER_POWER in power
        # an idle export counter reads 0 like any counter (ADR-013)
        values = dict(power[EXPORT_COUNTER_POWER]["slot_values"])
        assert values[_ts(12, 0)] == 0.0

    async def test_without_an_export_sensor_there_is_no_export_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _eff, power, smooth = await _compute(
            monkeypatch, _household_world(), _with(BASE, IMPORT_COUNTER)
        )
        assert EXPORT_COUNTER_POWER not in power and EXPORT_POWER_SMOOTHED not in smooth

    async def test_an_export_without_an_import_is_no_household_sensor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plain = await _compute(monkeypatch, _household_world(), BASE)
        lonely = await _compute(
            monkeypatch, _household_world(), _with(BASE, None, **{HE: EXPORT_POWER})
        )
        assert lonely == plain  # ADR-029 decision 1: no import, no household sensor


class TestTheSignedImport:
    """ADR-029 decision 2: a power-family household import with no export sensor is signed."""

    SLOTS = _slots(0, 6)
    # -40 at slot 1, 140 at slot 4: a gap of two slots across the sign change
    MEANS = {SLOTS[0]: -100.0, SLOTS[1]: -40.0, SLOTS[4]: 140.0, SLOTS[5]: 100.0}

    def _signed_world(self) -> Any:
        world = _household_world()
        world.power_means[IMPORT_POWER] = dict(self.MEANS)
        return world

    async def test_a_negative_reading_is_not_clamped_and_the_smoothing_runs_on_signed_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _eff, _power, smooth = await _compute(
            monkeypatch, self._signed_world(), _with(BASE, IMPORT_POWER)
        )
        values = dict(smooth[IMPORT_POWER_SMOOTHED]["slot_values"])
        assert values[self.SLOTS[0]] == -100.0 and values[self.SLOTS[1]] == -40.0
        # the two missing slots lie on the line from -40 to 140, through the sign change
        assert values[self.SLOTS[2]] == pytest.approx(20.0)
        assert values[self.SLOTS[3]] == pytest.approx(80.0)
        assert values[self.SLOTS[4]] == 140.0

    async def test_the_series_is_the_measured_one_with_an_export_sensor_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With a household export sensor neither series is signed, a negative reading counts as
        0 *where the series is used* (TASK-0049): the series Effy writes is the measured one."""
        alone = await _compute(monkeypatch, self._signed_world(), _with(BASE, IMPORT_POWER))
        paired = await _compute(
            monkeypatch, self._signed_world(), _with(BASE, IMPORT_POWER, **{HE: EXPORT_POWER})
        )
        assert paired[2][IMPORT_POWER_SMOOTHED] == alone[2][IMPORT_POWER_SMOOTHED]
        assert min(v for _s, v in paired[2][IMPORT_POWER_SMOOTHED]["slot_values"]) < 0

    async def test_the_signed_series_does_not_reach_the_waterfall(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = await _compute(monkeypatch, self._signed_world(), BASE)
        signed = await _compute(monkeypatch, self._signed_world(), _with(BASE, IMPORT_POWER))
        # a negative import loses nothing and takes no share
        assert _effective(signed[0]) == _effective(base[0])


class TestInNoFormula:
    async def test_the_effective_rows_are_identical_with_and_without_household_sensors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eff0, power0, smooth0 = await _compute(monkeypatch, _household_world(), BASE)
        for options in (
            _with(BASE, IMPORT_COUNTER),
            _with(BASE, IMPORT_POWER, **{HE: EXPORT_COUNTER}),
            _with(BASE, IMPORT_COUNTER, **{HE: EXPORT_POWER}),
        ):
            eff, power, smooth = await _compute(monkeypatch, _household_world(), options)
            # every effective row, the PV sum and the PV share included
            assert eff0 and _effective(eff) == eff0
            added = set(power) - set(power0) | set(smooth) - set(smooth0)
            assert added  # the series exist, and everything else is untouched
            assert {k: v for k, v in power.items() if k in power0} == power0
            assert {k: v for k, v in smooth.items() if k in smooth0} == smooth0

    async def test_the_slots_of_a_household_sensor_start_no_waterfall_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The PV input (a power sensor: no zero-fill) has data only in the morning; the
        household sensors have it all day. The effective rows still stop where the input's data
        stops, so no slot of a household sensor enters the waterfall's slot union."""
        world = _household_world()
        world.energy.pop(PV)
        world.units[PV] = "W"
        world.state_classes[PV] = _SensorStateClass.MEASUREMENT
        world.power_means[PV] = {slot: 80.0 for slot in _slots(0, 6)}  # 09:00 .. 09:25
        eff0, _p, _s = await _compute(monkeypatch, world, BASE)
        options = _with(BASE, IMPORT_POWER, **{HE: EXPORT_COUNTER})
        eff, _p, _s = await _compute(monkeypatch, world, options)
        assert _effective(eff) == eff0
        slots = {slot for info in _effective(eff).values() for slot, _v in info["slot_values"]}
        assert slots and max(slots) == _ts(9, 25)

    async def test_household_sensors_alone_still_have_their_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No input or output has a row at all: nothing effective, the series of the household
        sensors all the same."""
        world = _household_world(with_inputs=False)
        world.units[PV] = world.units[OUT] = "W"
        world.state_classes[PV] = world.state_classes[OUT] = _SensorStateClass.MEASUREMENT
        options = _with(BASE, IMPORT_COUNTER, **{HE: EXPORT_POWER})
        eff, power, smooth = await _compute(monkeypatch, world, options)
        assert _effective(eff) == {}
        assert set(power) == {IMPORT_COUNTER_POWER} and set(smooth) == {EXPORT_POWER_SMOOTHED}

    async def test_no_household_option_changes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        emptied = {**BASE, HI: None, HE: ""}
        assert await _compute(monkeypatch, _household_world(), emptied) == await _compute(
            monkeypatch, _household_world(), BASE
        )


class TestARoleWinsOverTheHouseholdOption:
    """A hand-edited entry: the options flow refuses all of these (ADR-019 amendment)."""

    @pytest.mark.parametrize("role_sensor", [PV, OUT])
    async def test_a_sensor_that_is_also_a_role_is_not_a_household_sensor(
        self, monkeypatch: pytest.MonkeyPatch, role_sensor: str
    ) -> None:
        plain = await _compute(monkeypatch, _household_world(), BASE)
        listed = await _compute(monkeypatch, _household_world(), _with(BASE, role_sensor))
        assert listed == plain

    async def test_a_sensor_that_is_additional_and_household_has_one_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        household = await _compute(monkeypatch, _household_world(), _with(BASE, IMPORT_COUNTER))
        both = await _compute(
            monkeypatch,
            _household_world(),
            _with(BASE, IMPORT_COUNTER, **{ADDITIONAL: [IMPORT_COUNTER]}),
        )
        assert both == household  # one series, written once, not two


class TestSlotJobAndFullRecalculation:
    OPTIONS = _with(BASE, IMPORT_COUNTER, **{HE: EXPORT_POWER})

    def _jumpy_world(self) -> Any:
        world = _household_world()
        # steps spread over more than the window, then an idle stretch
        world.energy[IMPORT_COUNTER] = [(_ts(7, 30), "100.0")] + [
            (_ts(9, 35) + timedelta(minutes=23 * i), f"{110.0 + 10.0 * i:.1f}") for i in range(5)
        ]
        return world

    async def test_the_timer_and_the_full_recalculation_leave_identical_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CAP-1's invariant covers the household series: a counter whose steps are spread over
        more than the window, an idle stretch, and a power sensor with gaps."""
        world = self._jumpy_world()
        _install(monkeypatch, world)
        options = _with(BASE, IMPORT_COUNTER, **{HE: EXPORT_POWER, ADDITIONAL: []})
        world.power_means[EXPORT_POWER] = {
            slot: value
            for slot, value in world.power_means[EXPORT_POWER].items()
            if slot not in _slots(10, 2)
        }

        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), _ts(13, 5, 5))
        full = await _run_full_recalc(world, options, _ts(13, 5, 5))

        assert _equivalent(timer, full, _ts(10, 0), _ts(13, 5))
        assert IMPORT_COUNTER_POWER in timer and EXPORT_POWER_SMOOTHED in timer
        assert any(v == 0.0 for v in timer[IMPORT_COUNTER_POWER].values())  # the idle zero-fill

    async def test_the_live_value_is_the_last_completed_slot_and_is_pushed_every_cycle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _household_world()
        _install(monkeypatch, world)
        timer = await _run_timer_cycles(world, self.OPTIONS, _ts(12, 0, 5), _ts(12, 30, 5))
        last = world.cycles[-1]

        assert {IMPORT_COUNTER_POWER, EXPORT_POWER_SMOOTHED} <= last.touched
        assert last.last_values[IMPORT_COUNTER_POWER][0] == timer[IMPORT_COUNTER_POWER][_ts(12, 25)]
        assert (
            last.last_values[EXPORT_POWER_SMOOTHED][0] == timer[EXPORT_POWER_SMOOTHED][_ts(12, 25)]
        )
        assert last.last_values[EXPORT_POWER_SMOOTHED][1] == "W"

    async def test_the_series_are_written_by_the_same_writer_as_the_inputs_and_no_effective_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _household_world()
        _install(monkeypatch, world)
        await _run_full_recalc(world, self.OPTIONS, _ts(13, 5, 5))
        written = {sid for _lt, _st, series in world.write_calls for sid in series}
        assert {IMPORT_COUNTER_POWER, EXPORT_POWER_SMOOTHED} <= written
        assert not written & {
            _history._effy_entity_id(IMPORT_COUNTER),
            _history._effy_entity_id(EXPORT_POWER),
        }
        # the hourly rows are written for them too (long-term, ADR-011 D2)
        hourly = [series for lt, _st, series in world.write_calls if lt]
        assert any(IMPORT_COUNTER_POWER in series for series in hourly)


class TestUnchangedRowsAreNotWritten:
    """ADR-024 for the household series: the REAL writer and read, a stateful recorder."""

    OPTIONS = _with(BASE, IMPORT_COUNTER, **{HE: EXPORT_POWER})

    def _rig(self, monkeypatch: pytest.MonkeyPatch, world: Any) -> StoredRecorder:
        _install(monkeypatch, world)
        recorder = StoredRecorder()
        _install_ha_api(monkeypatch, recorder)
        monkeypatch.setattr(_history, "get_recorder", lambda _hass: recorder)
        monkeypatch.setattr(_history, "_write_recorder_statistics", REAL_WRITE)
        sources = dict(world.units)
        monkeypatch.setattr(
            _history,
            "_get_statistics_units",
            lambda hass, ids: {**sources, **REAL_UNITS(hass, list(ids))},
        )
        _FrozenDatetime.frozen = _ts(13, 5, 5)
        return recorder

    async def test_a_second_run_with_nothing_changed_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = self._rig(monkeypatch, _household_world())
        await _history.async_recalculate_history(object(), self.OPTIONS)
        assert recorder.short(IMPORT_COUNTER_POWER) and recorder.short(EXPORT_POWER_SMOOTHED)
        assert recorder.hourly(IMPORT_COUNTER_POWER)
        stored, imports = _snapshot(recorder), len(recorder.imports)

        written, earliest, touched, _last, _deltas = await _history.async_recalculate_history(
            object(), self.OPTIONS
        )

        assert (written, earliest) == (0, None)
        assert len(recorder.imports) == imports and _snapshot(recorder) == stored
        assert {IMPORT_COUNTER_POWER, EXPORT_POWER_SMOOTHED} <= touched  # live states: every entity

    async def test_a_changed_household_reading_rewrites_household_rows_and_no_effective_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _household_world()
        recorder = self._rig(monkeypatch, world)
        await _history.async_recalculate_history(object(), self.OPTIONS)
        effective_before = {
            sid: dict(recorder.short(sid))
            for sid in {k[1] for k in recorder.rows}
            if sid.startswith("sensor.effy_")
            and not sid.endswith(("_power", "_smoothed"))
            and sid != CONSUMPTION
        }
        assert effective_before  # the effective series and the PV sum exist

        world.energy[IMPORT_COUNTER] = [*world.energy[IMPORT_COUNTER][:-1], (_ts(10, 20), "140.0")]
        written, earliest, _touched, _last, _deltas = await _history.async_recalculate_history(
            object(), self.OPTIONS
        )

        assert written > 0 and earliest is not None
        for sid, before in effective_before.items():
            assert dict(recorder.short(sid)) == before  # not one effective row changed
