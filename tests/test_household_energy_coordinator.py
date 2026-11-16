"""
The coordinator's part of the household consumption energy sensors (TASK-0050, CAP-15, ADR-029
decision 5, ADR-027 decisions 3 to 6 and 9).

Runs the real ``coordinator.py`` against the fake history of ``test_coordinator_slot``. What is
tested: the order of the steps of the implicit full recalculation, who gets which base, the
derivation again of the consumption sensors when an input of the consumption changed (Q27 = A),
that nothing else makes it, a failing step, ``delta_skip`` for a series without a base, the
booking of a run's deltas and the midnight timer for an entry with a household import and no PV
input. The arithmetic is tested without any fake in ``test_household_energy_booking``.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

from tests.test_coordinator_slot import (
    EffyCoordinator,
    _coord_mod,
    _delta_fail_at,
    _delta_flow,
    _delta_lock,
    _delta_read_return,
    _delta_skip_seen,
    _deltas_return,
    _derive_return,
    _FakeEntry,
    _FakeHass,
    _household_derive_calls,
    _household_derive_return,
    _lock_probe,
    _rederive_return,
    _reset_slot_job_fakes,
    _stored_units,
)

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 10, 12, 30, tzinfo=UTC)  # 13:30 in Berlin
MIDNIGHT = datetime(2026, 11, 10, 0, 0, tzinfo=BERLIN)
NEXT_MIDNIGHT = datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN)

PV_A = "sensor.pv_a"
PV_B = "sensor.pv_b"
HI = "sensor.house_import"
HE = "sensor.house_export"
GRID_IN = "sensor.bms_grid_in"
GRID_OUT = "sensor.bms_grid_out"
EPS = "sensor.eps"
TODAY_A = "sensor.effy_pv_a_delta_integral_today"
COMBINED_TODAY = "sensor.effy_pv_deltasum_integral_today"
COMBINED_TOTAL = "sensor.effy_pv_deltasum_integral_total"
TODAY = "sensor.effy_household_consumption_today"
TOTAL = "sensor.effy_household_consumption_total"
SERIES = "sensor.effy_household_consumption_now"
SIGNATURE_KEY = "formula_signature"
NEW_SIGNATURE = {"pv_input_sensors": [PV_A, PV_B], "marker": "the new one"}

_inputs = sys.modules["effy.inputs"]

# the five options the consumption depends on, in the order of ``consumption_inputs``
CONSUMPTION_OPTIONS = (
    "household_import_sensor",
    "household_export_sensor",
    "grid_import_sensor",
    "grid_export_sensor",
    "eps_sensor",
)


@dataclass(frozen=True)
class _Base:
    state: float
    last_reset: datetime | None
    total: float = 0.0


class _Unsub:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    """Fresh fakes and a recorded midnight timer."""
    _reset_slot_job_fakes()
    mock = _coord_mod.async_track_point_in_utc_time
    mock.reset_mock()
    mock.side_effect = lambda _hass, _cb, _when: _Unsub()
    yield mock
    _lock_probe[0] = None
    mock.reset_mock()
    mock.side_effect = None


def _entry(pv: list[str], household: bool = True, export: bool = False) -> Any:
    entry = _FakeEntry(pv, ["sensor.export"])
    entry.options.update(
        {
            "grid_import_sensor": GRID_IN,
            "grid_export_sensor": GRID_OUT,
            "eps_sensor": EPS,
        }
    )
    if household:
        entry.options["household_import_sensor"] = HI
        if export:
            entry.options["household_export_sensor"] = HE
    return entry


def _coordinator(
    pv: list[str] | None = None,
    *,
    household: bool = True,
    export: bool = False,
    stored: Any = "absent",
) -> EffyCoordinator:
    pv = [PV_A, PV_B] if pv is None else pv
    entry = _entry(pv, household, export)
    if stored == "same":
        # the signature of this very entry, with a marker the formula-change test can differ in
        stored = {**_inputs.formula_signature(entry.options), "marker": "old"}
    if stored != "absent":
        entry.data = {SIGNATURE_KEY: stored, "other": "kept"}
    coord = EffyCoordinator(_FakeHass(), entry)
    coord._now = lambda: NOW  # type: ignore[method-assign]
    _lock_probe[0] = lambda: coord._recorder_job_lock().locked()
    return coord


def _sensors(
    coord: EffyCoordinator, *, today: float | None = None, total: float | None = None
) -> dict[str, list[Any]]:
    """The two consumption sensors restored (None: nothing restored), with a recording listener."""
    pushes: dict[str, list[Any]] = {}

    def listener(entity_id: str) -> Callable[[Any], None]:
        def push(integral: Any) -> None:
            pushes.setdefault(entity_id, []).append(integral.state)

        return push

    spec: tuple[tuple[str, Literal["today", "total"], float | None], ...] = (
        (TODAY, "today", today),
        (TOTAL, "total", total),
    )
    for entity_id, kind, value in spec:
        coord.restore_delta_integral(
            entity_id,
            kind,
            None,
            "kWh",
            value,
            MIDNIGHT if kind == "today" else None,
            series=SERIES,
        )
        coord.subscribe_delta_integral(entity_id, listener(entity_id))
    return pushes


def _state(coord: EffyCoordinator, entity_id: str) -> float | None:
    integral = coord._delta_integrals.get(entity_id)
    assert integral is not None
    return integral.state


def _run(coord: EffyCoordinator, signature: Any = NEW_SIGNATURE) -> bool:
    return asyncio.run(coord.async_run_formula_recalculation(signature))


def _existing_statistics() -> None:
    """The two consumption integrals have statistics and rows (last rows 0.92 and 0.98 kWh)."""
    _delta_read_return[0] = (
        {TODAY: _Base(0.92, MIDNIGHT), TOTAL: _Base(0.98, None)},
        {TODAY: "kWh", TOTAL: "kWh"},
    )
    _stored_units.update({TODAY: "kWh", TOTAL: "kWh"})


def _derived_again() -> None:
    """What the derivation returns when it runs (a new base for both)."""
    _household_derive_return[0] = {TODAY: _Base(0.4, MIDNIGHT), TOTAL: _Base(30.0, None)}
    _stored_units.update({TODAY: "kWh", TOTAL: "kWh"})


def _steps() -> list[str]:
    return [s for s in _delta_flow if s != "units"]


class TestEnabled:
    def test_the_integrals_exist_with_a_pv_input_or_with_a_household_import(self) -> None:
        assert _coordinator([PV_A], household=False).delta_integrals_enabled is True
        assert _coordinator([], household=True).delta_integrals_enabled is True
        assert _coordinator([PV_A], household=True).delta_integrals_enabled is True

    def test_without_either_there_are_none(self) -> None:
        assert _coordinator([], household=False).delta_integrals_enabled is False

    def test_an_export_alone_is_no_household_sensor(self) -> None:
        entry = _entry([], household=False)
        entry.options["household_export_sensor"] = HE
        assert EffyCoordinator(_FakeHass(), entry).delta_integrals_enabled is False

    def test_the_pv_property_is_still_about_the_pv_inputs_only(self) -> None:
        assert _coordinator([], household=True).pv_delta_enabled is False
        assert _coordinator([PV_A], household=False).pv_delta_enabled is True


class TestRestoreAndSkip:
    def test_a_household_integral_is_restored_with_its_series(self) -> None:
        coord = _coordinator([])
        integral = coord.restore_delta_integral(
            TODAY, "today", None, "kWh", 1.5, MIDNIGHT, series=SERIES
        )
        assert (integral.state, integral.unit, integral.series) == (1.5, "kWh", SERIES)

    def test_a_pv_integral_is_restored_as_before(self) -> None:
        coord = _coordinator()
        integral = coord.restore_delta_integral(TODAY_A, "today", PV_A, "Wh", 12.5, MIDNIGHT)
        assert (integral.state, integral.source, integral.series) == (12.5, PV_A, None)

    def test_the_series_is_skipped_until_one_of_its_sensors_has_a_base(self) -> None:
        coord = _coordinator([])
        _sensors(coord)
        assert coord._delta_skip() == {SERIES}
        _sensors(coord, total=5.0)
        assert coord._delta_skip() == frozenset()

    def test_pv_inputs_and_the_series_are_skipped_independently(self) -> None:
        coord = _coordinator([PV_A])
        _sensors(coord, today=1.0)
        assert coord._delta_skip() == {PV_A}  # the series has a base, the PV input has none

    def test_without_a_household_import_the_series_is_never_in_the_skip_set(self) -> None:
        coord = _coordinator([PV_A], household=False)
        assert SERIES not in coord._delta_skip()


class TestBooking:
    async def test_a_slot_job_books_the_series_delta_into_both_sensors(self) -> None:
        coord = _coordinator([])
        pushes = _sensors(coord, today=10.0, total=100.0)
        _deltas_return[0] = {SERIES: (250.0, "Wh")}
        await coord._async_recalculate_recent_and_report()
        assert _state(coord, TODAY) == pytest.approx(10.25)
        assert _state(coord, TOTAL) == pytest.approx(100.25)
        assert pushes[TODAY] == [pytest.approx(10.25)]
        assert pushes[TOTAL] == [pytest.approx(100.25)]

    async def test_the_skip_set_reaches_the_history_run(self) -> None:
        coord = _coordinator([])
        _sensors(coord)
        await coord._async_recalculate_recent_and_report()
        assert _delta_skip_seen == [("recent", frozenset({SERIES}))]

    async def test_a_sensor_with_a_base_is_not_skipped(self) -> None:
        coord = _coordinator([])
        _sensors(coord, today=0.0, total=0.0)
        await coord._async_recalculate_recent_and_report()
        assert _delta_skip_seen == [("recent", frozenset())]

    async def test_an_unavailable_sensor_books_nothing(self) -> None:
        coord = _coordinator([])
        _sensors(coord, total=100.0)  # today restored nothing
        _deltas_return[0] = {SERIES: (1000.0, "Wh")}
        await coord._async_recalculate_recent_and_report()
        assert _state(coord, TODAY) is None
        assert _state(coord, TOTAL) == pytest.approx(101.0)

    async def test_a_run_without_a_delta_books_nothing(self) -> None:
        coord = _coordinator([])
        pushes = _sensors(coord, today=10.0, total=100.0)
        _deltas_return[0] = None
        await coord._async_recalculate_recent_and_report()
        assert pushes == {} and _state(coord, TOTAL) == 100.0

    async def test_pv_and_consumption_deltas_of_one_run_are_booked_each_to_its_own(self) -> None:
        coord = _coordinator([PV_A])
        pv = coord.restore_delta_integral(TODAY_A, "today", PV_A, "Wh", 500.0, MIDNIGHT)
        combined: tuple[tuple[str, Literal["today", "total"]], ...] = (
            (COMBINED_TODAY, "today"),
            (COMBINED_TOTAL, "total"),
        )
        for entity_id, kind in combined:
            coord.restore_delta_integral(
                entity_id, kind, None, "kWh", 2.0, MIDNIGHT if kind == "today" else None
            )
        _sensors(coord, today=10.0, total=100.0)
        _deltas_return[0] = {PV_A: (100.0, "Wh"), SERIES: (400.0, "Wh")}
        await coord._async_recalculate_recent_and_report()
        assert pv.state == pytest.approx(600.0)
        assert _state(coord, COMBINED_TODAY) == pytest.approx(2.1)
        assert _state(coord, TODAY) == pytest.approx(10.4)

    async def test_the_button_books_its_deltas_too(self) -> None:
        coord = _coordinator([])
        _sensors(coord, today=1.0, total=2.0)
        _deltas_return[0] = {SERIES: (500.0, "Wh")}
        await coord.async_recalculate_history_and_report()
        assert _state(coord, TODAY) == pytest.approx(1.5)


class TestMidnight:
    def test_a_household_import_alone_schedules_the_timer(self, _fresh: Any) -> None:
        coord = _coordinator([])
        coord.async_setup()
        _fresh.assert_called_once()
        hass, callback, when = _fresh.call_args.args
        assert hass is coord._hass and callback == coord._on_midnight
        assert when == NEXT_MIDNIGHT.astimezone(UTC)

    def test_neither_a_pv_input_nor_a_household_import_schedules_nothing(self, _fresh: Any) -> None:
        _coordinator([], household=False).async_setup()
        _fresh.assert_not_called()

    def test_the_midnight_resets_today_of_the_consumption_and_never_its_total(self) -> None:
        coord = _coordinator([])
        pushes = _sensors(coord, today=3.5, total=80.0)
        coord.async_setup()
        coord._now = lambda: NEXT_MIDNIGHT.astimezone(UTC) + timedelta(milliseconds=40)  # type: ignore[method-assign]
        coord._on_midnight(coord._now())
        integral = coord._delta_integrals.get(TODAY)
        assert integral is not None
        assert (integral.state, integral.last_reset) == (0.0, NEXT_MIDNIGHT)
        assert pushes[TODAY] == [0.0]
        assert _state(coord, TOTAL) == 80.0 and TOTAL not in pushes

    def test_an_unavailable_sensor_is_not_reset(self) -> None:
        coord = _coordinator([])
        pushes = _sensors(coord, total=80.0)
        coord.async_setup()
        coord._on_midnight(coord._now())
        assert _state(coord, TODAY) is None and TODAY not in pushes

    def test_the_timer_is_scheduled_again(self, _fresh: Any) -> None:
        coord = _coordinator([])
        _sensors(coord, today=1.0, total=1.0)
        coord.async_setup()
        coord._now = lambda: NEXT_MIDNIGHT.astimezone(UTC)  # type: ignore[method-assign]
        coord._on_midnight(coord._now())
        assert _fresh.call_count == 2
        assert _fresh.call_args.args[2] == (NEXT_MIDNIGHT + timedelta(days=1)).astimezone(UTC)

    def test_shutdown_cancels_it(self) -> None:
        coord = _coordinator([])
        coord.async_setup()
        unsub = coord._unsub_midnight
        assert isinstance(unsub, _Unsub)
        coord.async_shutdown()
        assert unsub.calls == 1 and coord._unsub_midnight is None


class TestTheSteps:
    def test_the_household_derivation_follows_the_pv_ones_under_the_recorder_lock(self) -> None:
        coord = _coordinator(stored=None)
        _sensors(coord)
        _derived_again()
        assert _run(coord) is True
        assert _steps() == ["read", "recalculate", "derive", "household"]
        assert all(_delta_lock[step] for step in ("read", "recalculate", "derive", "household"))

    def test_a_first_setup_derives_without_a_rewrite_and_hands_over_the_base(self) -> None:
        coord = _coordinator(stored=None)
        pushes = _sensors(coord)
        _derived_again()
        _run(coord)
        assert [c["rewrite"] for c in _household_derive_calls] == [False]
        assert (_state(coord, TODAY), _state(coord, TOTAL)) == (0.4, 30.0)
        assert pushes[TODAY] == [0.4] and pushes[TOTAL] == [30.0]

    def test_the_household_derivation_gets_the_coordinators_storage(self) -> None:
        coord = _coordinator(stored=None)
        _sensors(coord)
        _run(coord)
        assert [c["storage"] for c in _household_derive_calls] == [coord._storage]

    def test_the_signature_is_stored_after_the_household_step(self) -> None:
        coord = _coordinator(stored=None)
        _sensors(coord)
        _derived_again()
        assert _run(coord) is True
        assert coord._entry.data[SIGNATURE_KEY] == NEW_SIGNATURE

    def test_without_a_household_import_the_flow_is_what_it_was(self) -> None:
        coord = _coordinator(household=False, stored=None)
        _derive_return[0] = {}
        _run(coord)
        assert _steps() == ["read", "recalculate", "derive"]
        assert _household_derive_calls == []

    def test_a_household_only_entry_runs_the_flow_without_a_pv_input(self) -> None:
        coord = _coordinator([], stored=None)
        _sensors(coord)
        _derived_again()
        assert _run(coord) is True
        assert _steps() == ["read", "recalculate", "derive", "household"]
        assert _state(coord, TOTAL) == 30.0

    def test_the_full_recalculation_skips_a_series_that_has_no_base_yet(self) -> None:
        coord = _coordinator(stored=None)
        _sensors(coord)
        _run(coord)
        assert _delta_skip_seen == [("history", frozenset({PV_A, PV_B, SERIES}))]


class TestWhoGetsWhichBase:
    def test_statistics_that_exist_give_the_sensors_their_last_row_without_a_rewrite(self) -> None:
        """The same inputs, the formula changed in another way: the consumption is not derived
        again, and the sensors that restored nothing get the last row they had."""
        coord = _coordinator(stored="same")
        pushes = _sensors(coord)  # nothing restored
        _existing_statistics()
        _run(coord)
        assert [c["rewrite"] for c in _household_derive_calls] == [False]
        assert pushes[TODAY] == [0.92] and pushes[TOTAL] == [0.98]

    def test_sensors_that_restored_a_state_keep_it_and_book_the_runs_delta(self) -> None:
        coord = _coordinator(stored="same")
        pushes = _sensors(coord, today=0.9, total=5.0)
        _existing_statistics()
        _deltas_return[0] = {SERIES: (1000.0, "Wh")}
        _run(coord)
        assert _state(coord, TODAY) == pytest.approx(1.9)
        assert _state(coord, TOTAL) == pytest.approx(6.0)
        assert pushes[TODAY] == [pytest.approx(1.9)]  # booked once, no base handed over

    def test_a_series_that_is_not_derived_yet_has_no_base_to_hand_over(self) -> None:
        coord = _coordinator(stored="same")
        pushes = _sensors(coord)
        _run(coord)  # no statistics, the derivation (fake) returns nothing
        assert pushes == {} and _state(coord, TODAY) is None


class TestTheConsumptionIsDerivedAgainWhenItsInputsChange:
    def _setup(
        self, stored: Any, *, derives: bool = True, **values: float | None
    ) -> tuple[EffyCoordinator, Any]:
        """``derives``: the derivation returns a new base, as the real one does when it runs;
        with the statistics there and no rewrite asked for it returns nothing."""
        coord = _coordinator(export=True, stored=stored)
        pushes = _sensors(coord, **values)
        _existing_statistics()
        if derives:
            _derived_again()
        return coord, pushes

    @staticmethod
    def _stored_with(option: str, value: str | None) -> dict[str, Any]:
        entry = _entry([PV_A, PV_B], household=True, export=True)
        signature = dict(_inputs.formula_signature(entry.options))
        assert option in signature
        signature[option] = value
        return signature

    @pytest.mark.parametrize("option", CONSUMPTION_OPTIONS)
    def test_a_change_of_any_of_the_five_derives_it_again_after_the_other_steps(
        self, option: str
    ) -> None:
        coord, _ = self._setup(
            self._stored_with(option, "sensor.somebody_else"), today=1.0, total=5.0
        )
        _run(coord)
        assert [c["rewrite"] for c in _household_derive_calls] == [True]
        assert _steps() == ["read", "recalculate", "derive", "household"]
        assert (_state(coord, TODAY), _state(coord, TOTAL)) == (0.4, 30.0)

    @pytest.mark.parametrize("option", CONSUMPTION_OPTIONS)
    def test_a_sensor_taken_out_counts_as_a_change_too(self, option: str) -> None:
        coord, _ = self._setup(self._stored_with(option, None), today=1.0, total=5.0)
        _run(coord)
        assert [c["rewrite"] for c in _household_derive_calls] == [True]

    def test_the_sensors_are_unavailable_meanwhile_and_get_exactly_the_new_base(self) -> None:
        coord, pushes = self._setup(
            self._stored_with("household_import_sensor", "sensor.old_meter"), today=1.0, total=5.0
        )
        _run(coord)
        assert pushes[TODAY] == [None, 0.4] and pushes[TOTAL] == [None, 30.0]

    def test_nothing_is_booked_into_them_during_the_recalculation(self) -> None:
        coord, pushes = self._setup(
            self._stored_with("household_import_sensor", "sensor.old_meter"), today=1.0, total=5.0
        )
        _deltas_return[0] = {SERIES: (1000.0, "Wh")}
        _run(coord)
        assert pushes[TOTAL] == [None, 30.0]  # the booking found it unavailable

    def test_the_last_row_of_the_old_composition_is_not_handed_over_first(self) -> None:
        coord, pushes = self._setup(
            self._stored_with("household_import_sensor", "sensor.old_meter")
        )  # nothing restored
        _run(coord)
        assert 0.98 not in pushes[TOTAL] and 0.92 not in pushes[TODAY]

    def test_a_series_that_is_skipped_during_the_recalculation_is_the_rewritten_one(self) -> None:
        coord, _ = self._setup(
            self._stored_with("household_import_sensor", "sensor.old_meter"), today=1.0, total=5.0
        )
        _run(coord)
        assert _delta_skip_seen == [("history", frozenset({PV_A, PV_B, SERIES}))]

    def test_the_same_inputs_in_a_changed_formula_keep_the_sensors(self) -> None:
        coord, pushes = self._setup("same", derives=False, today=1.0, total=5.0)
        _run(coord)
        assert [c["rewrite"] for c in _household_derive_calls] == [False]
        assert TODAY not in pushes and TOTAL not in pushes
        assert (_state(coord, TODAY), _state(coord, TOTAL)) == (1.0, 5.0)

    def test_a_change_of_the_pv_inputs_alone_does_not_touch_the_consumption(self) -> None:
        stored = {**_inputs.formula_signature(_entry([PV_A], True, True).options)}
        coord, _ = self._setup(stored, derives=False, today=1.0, total=5.0)
        _run(coord)
        assert [c["rewrite"] for c in _household_derive_calls] == [False]
        assert (_state(coord, TODAY), _state(coord, TOTAL)) == (1.0, 5.0)

    def test_without_a_stored_signature_existing_statistics_are_derived_again(self) -> None:
        coord, _ = self._setup(None, today=1.0, total=5.0)
        _run(coord)
        assert [c["rewrite"] for c in _household_derive_calls] == [True]

    def test_a_signature_older_than_the_household_sensors_counts_as_unknown(self) -> None:
        coord, _ = self._setup({"pv_input_sensors": [PV_A, PV_B]}, today=1.0, total=5.0)
        _run(coord)
        assert [c["rewrite"] for c in _household_derive_calls] == [True]

    def test_without_statistics_and_without_a_stored_signature_it_is_derived_once(self) -> None:
        coord = _coordinator(stored=None)
        _sensors(coord)
        _derived_again()
        _run(coord)  # nothing in the read result: no statistics
        assert [c["rewrite"] for c in _household_derive_calls] == [False]
        assert _state(coord, TOTAL) == 30.0

    def test_a_pv_change_and_a_household_change_rewrite_both_in_order(self) -> None:
        coord = _coordinator(
            export=True,
            stored={
                **self._stored_with("household_import_sensor", "sensor.old_meter"),
                "pv_input_sensors": [PV_A],
            },
        )
        pushes = _sensors(coord, today=1.0, total=5.0)
        combined: tuple[tuple[str, Literal["today", "total"]], ...] = (
            (COMBINED_TODAY, "today"),
            (COMBINED_TOTAL, "total"),
        )
        for entity_id, kind in combined:
            coord.restore_delta_integral(
                entity_id, kind, None, "kWh", 2.0, MIDNIGHT if kind == "today" else None
            )
        _delta_read_return[0] = (
            {
                COMBINED_TODAY: _Base(2.0, MIDNIGHT),
                COMBINED_TOTAL: _Base(50.0, None),
                TODAY: _Base(0.92, MIDNIGHT),
                TOTAL: _Base(0.98, None),
            },
            {s: "kWh" for s in (COMBINED_TODAY, COMBINED_TOTAL, TODAY, TOTAL)},
        )
        _rederive_return[0] = (
            {COMBINED_TODAY: _Base(0.1, MIDNIGHT), COMBINED_TOTAL: _Base(9.0, None)},
            {COMBINED_TODAY: "kWh", COMBINED_TOTAL: "kWh"},
        )
        _derived_again()
        _stored_units.update({COMBINED_TODAY: "kWh", COMBINED_TOTAL: "kWh"})
        _run(coord)
        assert _steps() == ["read", "recalculate", "derive", "rederive", "household"]
        assert [c["rewrite"] for c in _household_derive_calls] == [True]
        assert _state(coord, COMBINED_TOTAL) == 9.0 and _state(coord, TOTAL) == 30.0
        assert pushes[TOTAL] == [None, 30.0]

    def test_a_household_change_does_not_rewrite_the_pv_combined(self) -> None:
        coord, _ = self._setup(
            self._stored_with("household_import_sensor", "sensor.old_meter"), today=1.0, total=5.0
        )
        _run(coord)
        assert "rederive" not in _delta_flow


class TestAFailingHouseholdStep:
    def test_the_run_fails_the_signature_is_not_stored_and_the_sensors_stay_unavailable(
        self,
    ) -> None:
        coord = _coordinator(
            export=True,
            stored={
                **TestTheConsumptionIsDerivedAgainWhenItsInputsChange._stored_with(
                    "household_import_sensor", "sensor.old_meter"
                )
            },
        )
        pushes = _sensors(coord, today=1.0, total=5.0)
        _existing_statistics()
        _delta_fail_at[0] = "household"
        assert _run(coord) is False
        assert coord._hass.config_entries.updates == []  # the signature is stored on success only
        assert coord._entry.data[SIGNATURE_KEY] != NEW_SIGNATURE
        assert _state(coord, TODAY) is None and _state(coord, TOTAL) is None
        assert pushes[TODAY] == [None]  # unavailable, and nothing after

    def test_the_slot_jobs_still_work_afterwards(self) -> None:
        coord = _coordinator([], stored=None)
        _sensors(coord)
        _delta_fail_at[0] = "household"
        assert _run(coord) is False
        _delta_fail_at[0] = None
        _skip_before = len(_delta_skip_seen)
        asyncio.run(coord._async_recalculate_recent_and_report())
        assert _delta_skip_seen[_skip_before:] == [("recent", frozenset({SERIES}))]  # skipped
