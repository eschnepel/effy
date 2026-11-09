"""
The PV combined series and the sensor "Effective PV Sum now" (TASK-0045a, CAP-13, ADR-027
decision 1).

Per slot the series is the addition of the effective values of all PV inputs, in W. It is
written like any effective series: it is a member of ``per_sensor`` of
``history._compute_effective_slots``, so the writer, the stored-rows check (ADR-024), the
hourly rows and the live push treat it like the effective series of an input.

The history tests run the real ``history.py`` against the in-memory recorder world of
``test_history_recent_equivalence`` (and, for the stored rows and the hourly row, the real
writer against the stateful recorder of ``test_history_write_only_changed``). The sensor
tests load the real ``sensor.py`` the way ``test_standby_sensor`` does.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.test_history_recent_equivalence import (
    _const,
    _energy_world,
    _equivalent,
    _FrozenDatetime,
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
from tests.test_history_write_only_changed import _recalculate, _rig
from tests.test_published_sensors import _Coordinator, _Entry, _last
from tests.test_sensor_setup import ENERGY, POWER, _Platform, _SetupEntry, _state
from tests.test_standby_sensor import _Registry, _registry, _sensor

PV_A = "sensor.pv_a_w"  # PV input, W
PV_B = "sensor.pv_b_kw"  # PV input, kW
OUT = "sensor.out_w"  # output (charge counter)
DIS = "sensor.dis_w"  # BMS input (discharge counter)
EPS = "sensor.eps_w"  # the signed EPS/UPS sensor

START = _ts(10, 0, day=10)
END = _ts(12, 0, day=10)
SLOT = timedelta(minutes=5)
SLOTS = [START + SLOT * i for i in range(24)]

PV_SUM = _const.PV_SUM_ENTITY_ID
EFF_A = _history._effy_entity_id(PV_A)
EFF_B = _history._effy_entity_id(PV_B)
EFF_DIS = _history._effy_entity_id(DIS)
EFF_EPS = _history._effy_entity_id(EPS)


def _add_power(
    world: _World,
    eid: str,
    value: float,
    slots: list[datetime] | None = None,
    unit: str = "W",
) -> None:
    world.units[eid] = unit
    world.state_classes[eid] = _SensorStateClass.MEASUREMENT
    world.power_means[eid] = {s: value for s in (slots if slots is not None else SLOTS)}


def _series(per_sensor: dict[str, dict[str, Any]], statistic_id: str) -> dict[datetime, float]:
    return {slot: value for slot, value in per_sensor[statistic_id]["slot_values"]}


def _assert_all(series: dict[datetime, float], expected: float) -> None:
    """Every slot of a non-empty series has the expected value."""
    assert series
    assert all(v == pytest.approx(expected) for v in series.values()), series


async def _compute(
    monkeypatch: pytest.MonkeyPatch, world: _World, options: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    _install(monkeypatch, world)
    per_sensor: dict[str, dict[str, Any]]
    per_sensor, _power, _smoothed = await _history._compute_effective_slots(
        object(), options, START, END
    )
    return per_sensor


# ---------------------------------------------------------------------------
# The series
# ---------------------------------------------------------------------------


class TestTheSeriesIsTheAdditionOfThePvInputs:
    async def test_the_effective_values_of_a_w_and_a_kw_input_add_up_in_w(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World()
        _add_power(world, PV_A, 300.0)  # W
        _add_power(world, PV_B, 0.2, unit="kW")  # 200 W
        _add_power(world, OUT, 400.0)
        per_sensor = await _compute(monkeypatch, world, _options([PV_A, PV_B], [OUT]))

        # 500 W in, 400 W out: loss 100 W, 50 W each; effective 250 W and 150 W (0.15 kW)
        _assert_all(_series(per_sensor, EFF_A), 250.0)
        _assert_all(_series(per_sensor, EFF_B), 0.15)
        combined = _series(per_sensor, PV_SUM)
        assert set(combined) == set(SLOTS)
        assert all(v == pytest.approx(400.0) for v in combined.values())  # 250 W + 0.15 kW
        assert per_sensor[PV_SUM]["unit"] == "W"

    async def test_one_input_is_the_series_of_that_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World()
        _add_power(world, PV_A, 300.0)
        _add_power(world, OUT, 250.0)
        per_sensor = await _compute(monkeypatch, world, _options([PV_A], [OUT]))
        assert _series(per_sensor, PV_SUM) == pytest.approx(_series(per_sensor, EFF_A))

    async def test_a_kw_only_entry_still_writes_the_series_in_w(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World()
        _add_power(world, PV_B, 0.3, unit="kW")
        _add_power(world, OUT, 200.0)
        per_sensor = await _compute(monkeypatch, world, _options([PV_B], [OUT]))
        assert per_sensor[EFF_B]["unit"] == "kW"
        assert per_sensor[PV_SUM]["unit"] == "W"
        _assert_all(_series(per_sensor, PV_SUM), 200.0)

    async def test_a_pv_input_without_a_value_adds_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World()
        _add_power(world, PV_A, 300.0)
        _add_power(world, PV_B, 0.2, SLOTS[:12], unit="kW")  # no value in the last 12 slots
        _add_power(world, OUT, 400.0)
        per_sensor = await _compute(monkeypatch, world, _options([PV_A, PV_B], [OUT]))

        combined = _series(per_sensor, PV_SUM)
        assert set(combined) == set(SLOTS)
        assert all(combined[s] == pytest.approx(400.0) for s in SLOTS[:12])
        # PV_B has no reading: 300 W in, 400 W out, no loss, PV_A keeps its 300 W
        assert all(combined[s] == pytest.approx(300.0) for s in SLOTS[12:])

    async def test_a_slot_without_a_pv_value_has_no_row_and_the_bms_input_is_not_in_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World()
        _add_power(world, PV_A, 300.0, SLOTS[:12])
        _add_power(world, PV_B, 0.2, SLOTS[:12], unit="kW")
        _add_power(world, DIS, 100.0)  # a BMS input with a value in every slot
        _add_power(world, OUT, 400.0)
        per_sensor = await _compute(monkeypatch, world, _options([PV_A, PV_B], [OUT], bms=[DIS]))

        combined = _series(per_sensor, PV_SUM)
        assert set(combined) == set(SLOTS[:12])  # no PV value, no row
        assert set(_series(per_sensor, EFF_DIS)) == set(SLOTS)  # the slot still exists
        # 600 W in (300 + 200 + 100), 400 W out: loss 200 W, 200/3 each. The discharge
        # keeps 100 - 66.67 W, the PV inputs together the rest of the 400 W.
        eff_dis = _series(per_sensor, EFF_DIS)
        for slot in SLOTS[:12]:
            assert combined[slot] == pytest.approx(400.0 - eff_dis[slot])
            assert combined[slot] == pytest.approx(366.6667, abs=1e-3)

    async def test_the_eps_sensor_is_not_in_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        world = _World()
        _add_power(world, PV_A, 1000.0)
        _add_power(world, OUT, 800.0)
        _add_power(world, EPS, -300.0)  # another inverter feeds 300 W in
        options = _options([PV_A], [OUT])
        options[_const.CONF_EPS_SENSOR] = EPS
        per_sensor = await _compute(monkeypatch, world, options)

        # 1300 W in, 800 W out: loss 500 W, 250 W each; the EPS input keeps 50 W
        _assert_all(_series(per_sensor, EFF_EPS), 50.0)
        _assert_all(_series(per_sensor, PV_SUM), 750.0)

    async def test_without_a_pv_input_there_is_no_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _World()
        _add_power(world, DIS, 300.0)
        _add_power(world, OUT, 100.0)
        per_sensor = await _compute(monkeypatch, world, _options([], [OUT], bms=[DIS]))
        assert EFF_DIS in per_sensor
        assert PV_SUM not in per_sensor

    async def test_the_series_is_not_a_source_series_of_an_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The statistic ID is the sensor's entity ID, so it cannot collide with the
        effective series of a source sensor named ``effective_pv_sum_now``."""
        assert PV_SUM == "sensor.effy_effective_pv_sum_now"
        assert PV_SUM not in {EFF_A, EFF_B, EFF_DIS}


# ---------------------------------------------------------------------------
# The three paths
# ---------------------------------------------------------------------------


def _energy_options() -> dict[str, Any]:
    return _options([PV_A, PV_B], [OUT])


def _energy_pv_world() -> _World:
    """Two PV counters with the BMS screenshot's slow ticks, an output with fewer."""
    return _energy_world(
        **{
            PV_A: _screenshot_readings(),
            PV_B: _screenshot_readings(37),
            OUT: [(_ts(7, 30), "1.000"), (_ts(11, 25), "1.010"), (_ts(12, 53), "1.020")],
        }
    )


class TestAllPathsProduceTheSeries:
    async def test_the_slot_timer_and_the_full_recalculation_give_identical_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _energy_pv_world()
        _install(monkeypatch, world)
        options = _energy_options()
        last_cycle = _ts(13, 5, 5)

        timer = await _run_timer_cycles(world, options, _ts(10, 0, 5), last_cycle)
        cycles = list(world.cycles)
        full = await _run_full_recalc(world, options, last_cycle)

        assert PV_SUM in timer and PV_SUM in full
        assert timer[PV_SUM], "the slot timer wrote no row of the combined series"
        assert _equivalent(timer, full, _ts(10, 0), _ts(13, 5))
        # the energy-family effective series are in kW: the series is their sum in W
        for slot, value in full[PV_SUM].items():
            if slot in full[EFF_A] and slot in full[EFF_B]:
                assert value == pytest.approx(1000.0 * (full[EFF_A][slot] + full[EFF_B][slot]))
        # the slot job reports it as touched, with the live value and its unit
        assert any(PV_SUM in c.touched for c in cycles)
        assert any(c.last_values.get(PV_SUM, (0, ""))[1] == "W" for c in cycles)

    async def test_the_button_reports_the_series_as_touched_with_its_last_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _energy_pv_world()
        _install(monkeypatch, world)
        _written, _earliest, touched, last_values = await _call_history(_energy_options(), END)
        assert PV_SUM in touched
        value, unit = last_values[PV_SUM]
        assert unit == "W"
        assert value == pytest.approx(
            1000.0 * (last_values[EFF_A][0] + last_values[EFF_B][0]), rel=1e-9, abs=1e-6
        )


async def _call_history(
    options: dict[str, Any], at: datetime
) -> tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]:
    _FrozenDatetime.frozen = at
    result: tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]
    result = await _history.async_recalculate_history(object(), options)
    return result


class TestStoredRowsAndTheHourlyRow:
    """The real writer against the stateful recorder (ADR-024)."""

    async def test_a_repeated_run_writes_no_row_of_the_series(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world, recorder = _rig(monkeypatch)
        await _recalculate()
        assert recorder.short(PV_SUM), "the first run wrote no row of the combined series"
        first_imports = len(recorder.imports)

        await _recalculate()
        again = [i for i in recorder.imports[first_imports:] if i[1] == PV_SUM]
        assert again == []  # every row was already what Effy would write

    async def test_the_hourly_row_is_the_mean_of_its_slots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _world, recorder = _rig(monkeypatch)
        await _recalculate()
        hour = _ts(10, 0, day=10)
        slots = [recorder.short(PV_SUM)[hour + SLOT * i]["mean"] for i in range(12)]
        row = recorder.hourly(PV_SUM)[hour]
        assert row["mean"] == pytest.approx(sum(slots) / 12)
        assert row["sum"] is None  # no running total: ADR-003, the series has no sum
        assert recorder.units[PV_SUM] == "W"


# ---------------------------------------------------------------------------
# The sensor
# ---------------------------------------------------------------------------


class _PvCoordinator(_Coordinator):
    def __init__(self, pv: list[str] | None = None) -> None:
        super().__init__()
        self.pv_input_ids = ["sensor.pv_a_w"] if pv is None else pv


def _pv_sum(coordinator: _Coordinator | None = None) -> Any:
    return _sensor.EffyPvSumSensor(object(), _Entry(), coordinator or _PvCoordinator())


class TestTheSensorsIdentity:
    def test_name_ids_class_and_unit(self) -> None:
        entity = _pv_sum()
        assert entity.entity_id == "sensor.effy_effective_pv_sum_now"
        assert entity.unique_id == "entry1_effective_pv_sum_now"
        assert entity._attr_translation_key == "effective_pv_sum_now"
        assert entity._attr_device_class == "power"
        assert entity._attr_state_class == "measurement"
        assert entity._attr_native_unit_of_measurement == "W"
        assert entity._attr_has_entity_name is True
        assert entity.device_info["name"] == "Effy"

    def test_the_translations_name_it_in_english_and_german(self) -> None:
        import json
        from pathlib import Path

        base = Path(__file__).resolve().parents[1] / "custom_components" / "effy" / "translations"
        en = json.loads((base / "en.json").read_text(encoding="utf-8"))
        de = json.loads((base / "de.json").read_text(encoding="utf-8"))
        assert en["entity"]["sensor"]["effective_pv_sum_now"]["name"] == "Effective PV Sum now"
        assert de["entity"]["sensor"]["effective_pv_sum_now"]["name"]


class TestExistence:
    def _created(self, coordinator: _Coordinator) -> list[str]:
        entities = _sensor._pv_sum_entities(object(), _Entry(), coordinator)
        return [e.entity_id for e in entities]

    def test_it_exists_with_a_pv_input(self) -> None:
        _registry[0] = _Registry({})
        assert self._created(_PvCoordinator(["sensor.a", "sensor.b"])) == [PV_SUM]
        assert _registry[0].removed == []

    def test_without_a_pv_input_it_does_not_and_the_registered_entity_is_removed(self) -> None:
        _registry[0] = _Registry({("sensor", "effy", "entry1_effective_pv_sum_now"): PV_SUM})
        assert self._created(_PvCoordinator([])) == []
        assert _registry[0].removed == [PV_SUM]

    def test_nothing_is_removed_that_was_never_registered(self) -> None:
        _registry[0] = _Registry({})
        assert self._created(_PvCoordinator([])) == []
        assert _registry[0].removed == []

    async def test_the_platform_adds_it_with_the_other_entities(self) -> None:
        _registry[0] = _Registry({})
        entry = _SetupEntry(["sensor.pv_energy", "sensor.pv_power"], [])
        platform = _Platform(
            entry, {"sensor.pv_energy": _state(ENERGY), "sensor.pv_power": _state(POWER)}
        )
        await platform.setup()
        names = [type(e).__name__ for batch in platform.added for e in batch]
        assert names.count("EffyPvSumSensor") == 1

    async def test_the_platform_adds_none_without_a_pv_input(self) -> None:
        _registry[0] = _Registry({})
        entry = _SetupEntry([], [])
        platform = _Platform(entry, {})
        platform.coordinator.pv_input_ids = []
        await platform.setup()
        names = [type(e).__name__ for batch in platform.added for e in batch]
        assert "EffyPvSumSensor" not in names


class TestLiveStateAndRestore:
    async def test_the_last_slots_value_is_pushed_in_w(self) -> None:
        coordinator = _PvCoordinator()
        entity = _pv_sum(coordinator)
        entity.async_write_ha_state = lambda: None
        await entity.async_added_to_hass()
        coordinator.updates[PV_SUM](412.3456, "W")
        assert entity._attr_native_value == 412.346
        assert entity._attr_native_unit_of_measurement == "W"

    async def test_a_missing_value_is_unknown_not_a_number(self) -> None:
        coordinator = _PvCoordinator()
        entity = _pv_sum(coordinator)
        entity.async_write_ha_state = lambda: None
        await entity.async_added_to_hass()
        coordinator.updates[PV_SUM](None, None)
        assert entity._attr_native_value is None

    async def test_it_restores_its_last_state_like_the_other_effective_sensors(self) -> None:
        entity = _pv_sum()
        entity.last_state = _last("123.4", {"unit_of_measurement": "W"})
        await entity.async_added_to_hass()
        assert entity._attr_native_value == 123.4

    @pytest.mark.parametrize("state", ["unknown", "unavailable", "not-a-number"])
    async def test_a_last_state_that_is_not_a_number_restores_nothing(self, state: str) -> None:
        entity = _pv_sum()
        entity.last_state = _last(state, {"unit_of_measurement": "W"})
        await entity.async_added_to_hass()
        assert entity._attr_native_value is None

    async def test_it_is_not_published_yet_and_unsubscribes_on_removal(self) -> None:
        coordinator = _PvCoordinator()
        entity = _pv_sum(coordinator)
        await entity.async_added_to_hass()
        assert coordinator.registry == {}  # TASK-0045f publishes it, not this task
        assert PV_SUM in coordinator.updates
        await entity.async_will_remove_from_hass()
