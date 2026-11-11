"""
The coordinator's part of the PV delta integrals (TASK-0045d, CAP-13, ADR-027 decisions 3 to 5).

Runs the real ``coordinator.py`` against the fake history of ``test_coordinator_slot`` (its
fakes return the deltas a test sets). What is tested: restore and subscription of a sensor,
the booking of a run's deltas after its write, ``delta_skip`` for inputs without a base, the
midnight timer (wall clock, daylight saving included) and the hand-over of the base. The
arithmetic itself is tested without any fake in ``test_delta_integrals``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from tests.test_coordinator_slot import (
    EffyCoordinator,
    _coord_mod,
    _delta_skip_seen,
    _deltas_return,
    _FakeEntry,
    _FakeHass,
    _history_full_calls,
    _history_raise_next,
    _reset_slot_job_fakes,
)

if TYPE_CHECKING:
    from effy.delta_integrals import Kind

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
PV_A = "sensor.pv_a"
PV_B = "sensor.pv_b"
TODAY_A = "sensor.effy_pv_a_delta_integral_today"
TODAY_B = "sensor.effy_pv_b_delta_integral_today"
COMBINED_TODAY = "sensor.effy_pv_deltasum_integral_today"
COMBINED_TOTAL = "sensor.effy_pv_deltasum_integral_total"

NOW = datetime(2026, 11, 10, 12, 30, tzinfo=UTC)  # 13:30 in Berlin
MIDNIGHT = datetime(2026, 11, 10, 0, 0, tzinfo=BERLIN)
NEXT_MIDNIGHT = datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN)


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
def _timer() -> Any:
    """``async_track_point_in_utc_time`` records its calls and hands back a counting unsub."""
    _reset_slot_job_fakes()
    mock = _coord_mod.async_track_point_in_utc_time
    mock.reset_mock()
    mock.side_effect = lambda _hass, _cb, _when: _Unsub()
    yield mock
    mock.reset_mock()
    mock.side_effect = None


def _coordinator(
    pv: list[str] | None = None, now: datetime = NOW
) -> tuple[EffyCoordinator, dict[str, list[Any]]]:
    pv = [PV_A, PV_B] if pv is None else pv
    coord = EffyCoordinator(_FakeHass(), _FakeEntry(pv, ["sensor.export"]))
    coord._now = lambda: now  # type: ignore[method-assign]
    pushes: dict[str, list[Any]] = {}

    def _recorder(e: str) -> Callable[[Any], None]:
        def _record(integral: Any) -> None:
            pushes.setdefault(e, []).append(integral.state)

        return _record

    for entity_id in (TODAY_A, TODAY_B, COMBINED_TODAY, COMBINED_TOTAL):
        coord.subscribe_delta_integral(entity_id, _recorder(entity_id))
    return coord, pushes


def _restore_all(coord: EffyCoordinator, **values: float | None) -> None:
    """The four sensors restore: ``a``, ``b``, ``today`` and ``total`` (None: nothing)."""
    spec: tuple[tuple[str, Kind, str | None, str, float | None], ...] = (
        (TODAY_A, "today", PV_A, "Wh", values.get("a")),
        (TODAY_B, "today", PV_B, "kWh", values.get("b")),
        (COMBINED_TODAY, "today", None, "kWh", values.get("today")),
        (COMBINED_TOTAL, "total", None, "kWh", values.get("total")),
    )
    for entity_id, kind, source, unit, value in spec:
        coord.restore_delta_integral(
            entity_id,
            kind,
            source,
            unit,
            value,
            None if kind == "total" else MIDNIGHT,
        )


def _state(coord: EffyCoordinator, entity_id: str) -> float | None:
    integral = coord._delta_integrals.get(entity_id)
    assert integral is not None
    return integral.state


class TestEnabled:
    def test_the_integrals_exist_only_with_a_pv_input(self) -> None:
        assert _coordinator([PV_A])[0].pv_delta_enabled is True
        assert _coordinator([])[0].pv_delta_enabled is False


class TestRestoreAndSubscribe:
    def test_a_restored_state_is_returned_and_available(self) -> None:
        coord, _ = _coordinator()
        integral = coord.restore_delta_integral(TODAY_A, "today", PV_A, "Wh", 12.5, MIDNIGHT)
        assert (integral.state, integral.unit, integral.source) == (12.5, "Wh", PV_A)
        assert integral.last_reset == MIDNIGHT

    def test_nothing_restored_is_unavailable(self) -> None:
        coord, _ = _coordinator()
        integral = coord.restore_delta_integral(TODAY_A, "today", PV_A, "Wh", None, None)
        assert integral.available is False

    def test_the_clock_and_the_zone_of_home_assistant_decide_what_an_old_day_is(self) -> None:
        coord, _ = _coordinator()
        yesterday = datetime(2026, 11, 9, 0, 0, tzinfo=BERLIN)
        integral = coord.restore_delta_integral(TODAY_A, "today", PV_A, "Wh", 9.0, yesterday)
        assert integral.state == 0.0 and integral.last_reset == MIDNIGHT

    def test_unsubscribe_stops_the_pushes(self) -> None:
        coord, _ = _coordinator()
        seen: list[Any] = []
        unsub = coord.subscribe_delta_integral(TODAY_A, seen.append)
        _restore_all(coord, a=0.0, b=0.0, today=0.0, total=0.0)
        _deltas_return[0] = {PV_A: (1.0, "Wh")}
        asyncio.run(coord._async_recalculate_recent_and_report())
        assert len(seen) == 1
        unsub()
        asyncio.run(coord._async_recalculate_recent_and_report())
        assert len(seen) == 1


class TestBookingAfterTheWrite:
    async def test_a_slot_job_books_the_deltas_of_its_run(self) -> None:
        coord, pushes = _coordinator()
        _restore_all(coord, a=100.0, b=1.0, today=1.1, total=50.0)
        _deltas_return[0] = {PV_A: (250.0, "Wh"), PV_B: (0.5, "kWh")}
        await coord._async_recalculate_recent_and_report()
        assert _state(coord, TODAY_A) == pytest.approx(350.0)
        assert _state(coord, TODAY_B) == pytest.approx(1.5)
        assert _state(coord, COMBINED_TODAY) == pytest.approx(1.85)
        assert _state(coord, COMBINED_TOTAL) == pytest.approx(50.75)
        assert all(len(v) == 1 for v in pushes.values())  # each sensor is told once

    async def test_the_button_books_its_deltas_too(self) -> None:
        coord, _ = _coordinator()
        _restore_all(coord, a=0.0, b=0.0, today=0.0, total=0.0)
        _deltas_return[0] = {PV_A: (-500.0, "Wh")}
        await coord.async_recalculate_history_and_report()
        assert _state(coord, TODAY_A) == pytest.approx(-500.0)
        assert _state(coord, COMBINED_TODAY) == pytest.approx(-0.5)  # today may go below 0
        assert _state(coord, COMBINED_TOTAL) == pytest.approx(-0.5)

    async def test_nothing_is_booked_when_the_run_fails(self) -> None:
        coord, pushes = _coordinator()
        _restore_all(coord, a=0.0, b=0.0, today=0.0, total=0.0)
        _deltas_return[0] = {PV_A: (250.0, "Wh")}
        _history_raise_next[0] = True
        with pytest.raises(RuntimeError):
            await coord._async_recalculate_recent_and_report()
        assert _state(coord, TODAY_A) == 0.0 and pushes == {}

    @pytest.mark.parametrize("deltas", [None, {}])
    async def test_a_run_without_a_delta_books_and_pushes_nothing(self, deltas: Any) -> None:
        coord, pushes = _coordinator()
        _restore_all(coord, a=5.0, b=5.0, today=5.0, total=5.0)
        _deltas_return[0] = deltas
        await coord._async_recalculate_recent_and_report()
        await coord.async_recalculate_history_and_report()
        assert _state(coord, COMBINED_TOTAL) == 5.0 and pushes == {}

    async def test_an_unavailable_sensor_books_nothing(self) -> None:
        coord, pushes = _coordinator()
        _restore_all(coord, b=1.0, today=2.0, total=3.0)  # no base for input A
        _deltas_return[0] = {PV_A: (1000.0, "Wh"), PV_B: (0.5, "kWh")}
        await coord._async_recalculate_recent_and_report()
        assert _state(coord, TODAY_A) is None and TODAY_A not in pushes
        assert _state(coord, TODAY_B) == pytest.approx(1.5)
        assert _state(coord, COMBINED_TOTAL) == pytest.approx(3.5)

    async def test_the_booking_is_at_the_time_of_the_write_whatever_slot_it_concerns(
        self,
    ) -> None:
        """A correction of earlier days lands in the live state of today (ADR-027 decision 3)."""
        coord, _ = _coordinator()
        _restore_all(coord, a=10.0, b=0.0, today=0.01, total=100.0)
        _deltas_return[0] = {PV_A: (-4000.0, "Wh")}  # the run lowered rows of a week ago
        await coord._async_recalculate_recent_and_report()
        assert _state(coord, TODAY_A) == pytest.approx(-3990.0)
        assert _state(coord, COMBINED_TODAY) == pytest.approx(0.01 - 4.0)
        assert _state(coord, COMBINED_TOTAL) == pytest.approx(96.0)

    async def test_a_sensor_without_a_listener_is_booked_just_the_same(self) -> None:
        coord = EffyCoordinator(_FakeHass(), _FakeEntry([PV_A], ["sensor.export"]))
        coord._now = lambda: NOW  # type: ignore[method-assign]
        coord.restore_delta_integral(TODAY_A, "today", PV_A, "Wh", 1.0, MIDNIGHT)
        _deltas_return[0] = {PV_A: (2.0, "Wh")}
        await coord._async_recalculate_recent_and_report()
        assert _state(coord, TODAY_A) == pytest.approx(3.0)


class TestDeltaSkip:
    async def test_every_input_is_skipped_until_its_sensor_has_a_base(self) -> None:
        coord, _ = _coordinator()
        await coord._async_recalculate_recent_and_report()
        await coord.async_recalculate_history_and_report()
        assert _delta_skip_seen == [
            ("recent", frozenset({PV_A, PV_B})),
            ("history", frozenset({PV_A, PV_B})),
        ]

    async def test_an_input_with_a_base_is_not_skipped(self) -> None:
        coord, _ = _coordinator()
        _restore_all(coord, a=0.0)  # B has none
        await coord._async_recalculate_recent_and_report()
        await coord.async_recalculate_history_and_report()
        assert [skip for _kind, skip in _delta_skip_seen] == [frozenset({PV_B})] * 2

    async def test_nothing_is_skipped_once_all_have_a_base(self) -> None:
        coord, _ = _coordinator()
        _restore_all(coord, a=0.0, b=0.0)
        await coord._async_recalculate_recent_and_report()
        assert _delta_skip_seen == [("recent", frozenset())]

    async def test_without_a_pv_input_nothing_is_skipped(self) -> None:
        coord, _ = _coordinator([])
        await coord._async_recalculate_recent_and_report()
        assert _delta_skip_seen == [("recent", frozenset())]
        assert _history_full_calls == []


class TestMidnight:
    def test_setup_schedules_the_timer_for_the_next_local_midnight(self, _timer: Any) -> None:
        coord, _ = _coordinator()
        coord.async_setup()
        _timer.assert_called_once()
        hass, callback, when = _timer.call_args.args
        assert hass is coord._hass
        assert callback == coord._on_midnight
        assert when == NEXT_MIDNIGHT.astimezone(UTC)
        assert when.utcoffset() == timedelta(0)

    def test_without_a_pv_input_no_timer_is_scheduled(self, _timer: Any) -> None:
        coord, _ = _coordinator([])
        coord.async_setup()
        _timer.assert_not_called()

    def test_shutdown_cancels_the_timer(self, _timer: Any) -> None:
        coord, _ = _coordinator()
        coord.async_setup()
        unsub = coord._unsub_midnight
        assert isinstance(unsub, _Unsub)
        coord.async_shutdown()
        assert unsub.calls == 1 and coord._unsub_midnight is None
        coord.async_shutdown()  # twice is harmless
        assert unsub.calls == 1

    def test_the_midnight_resets_both_today_kinds_and_never_the_total(self, _timer: Any) -> None:
        coord, pushes = _coordinator()
        _restore_all(coord, a=12.0, b=3.0, today=3.5, total=80.0)
        coord.async_setup()
        # the timer fires a little late: the new last_reset is still the midnight itself
        coord._now = lambda: NEXT_MIDNIGHT.astimezone(UTC) + timedelta(milliseconds=40)  # type: ignore[method-assign]
        coord._on_midnight(coord._now())
        for entity_id in (TODAY_A, TODAY_B, COMBINED_TODAY):
            integral = coord._delta_integrals.get(entity_id)
            assert integral is not None
            assert integral.state == 0.0 and integral.last_reset == NEXT_MIDNIGHT
            assert pushes[entity_id] == [0.0]
        assert _state(coord, COMBINED_TOTAL) == 80.0 and COMBINED_TOTAL not in pushes

    def test_an_unavailable_sensor_is_not_reset(self, _timer: Any) -> None:
        coord, pushes = _coordinator()
        _restore_all(coord, b=3.0)
        coord.async_setup()
        coord._on_midnight(coord._now())
        assert _state(coord, TODAY_A) is None and TODAY_A not in pushes

    def test_the_timer_is_scheduled_again_for_the_following_midnight(self, _timer: Any) -> None:
        coord, _ = _coordinator()
        _restore_all(coord, a=1.0)
        coord.async_setup()
        coord._now = lambda: NEXT_MIDNIGHT.astimezone(UTC) + timedelta(seconds=1)  # type: ignore[method-assign]
        coord._on_midnight(coord._now())
        assert _timer.call_count == 2
        assert _timer.call_args.args[2] == datetime(2026, 11, 12, 0, 0, tzinfo=BERLIN).astimezone(
            UTC
        )

    def test_a_day_with_daylight_saving_has_23_hours_and_the_reset_follows_the_wall_clock(
        self, _timer: Any
    ) -> None:
        before = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
        coord, _ = _coordinator(now=before)
        _restore_all(coord, a=1.0)
        # the restored last_reset has to be of the day before for the state to be kept
        coord.restore_delta_integral(
            TODAY_A, "today", PV_A, "Wh", 1.0, datetime(2026, 3, 28, 0, 0, tzinfo=BERLIN)
        )
        coord.async_setup()
        first = _timer.call_args.args[2]
        assert first == datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN).astimezone(UTC)
        coord._now = lambda: first + timedelta(seconds=1)  # type: ignore[method-assign]
        coord._on_midnight(coord._now())
        second = _timer.call_args.args[2]
        assert second == datetime(2026, 3, 30, 0, 0, tzinfo=BERLIN).astimezone(UTC)
        assert second - first == timedelta(hours=23)  # the clock went forward that night
        integral = coord._delta_integrals.get(TODAY_A)
        assert integral is not None
        assert integral.last_reset == datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN)

    def test_a_booking_after_the_midnight_lands_in_the_new_day(self, _timer: Any) -> None:
        coord, _ = _coordinator()
        _restore_all(coord, a=50.0, b=1.0, today=2.0, total=9.0)
        coord.async_setup()
        coord._on_midnight(NEXT_MIDNIGHT.astimezone(UTC))
        _deltas_return[0] = {PV_A: (30.0, "Wh")}
        asyncio.run(coord._async_recalculate_recent_and_report())
        assert _state(coord, TODAY_A) == pytest.approx(30.0)
        assert _state(coord, COMBINED_TODAY) == pytest.approx(0.03)
        assert _state(coord, COMBINED_TOTAL) == pytest.approx(9.03)


class TestHandOver:
    def test_each_sensor_is_pushed_exactly_the_base(self) -> None:
        coord, pushes = _coordinator()
        _restore_all(coord)
        coord.hand_over_delta_bases(
            {
                TODAY_A: _Base(12.5, MIDNIGHT),
                COMBINED_TODAY: _Base(0.0125, MIDNIGHT),
                COMBINED_TOTAL: _Base(7777.125, None),
            }
        )
        assert pushes == {TODAY_A: [12.5], COMBINED_TODAY: [0.0125], COMBINED_TOTAL: [7777.125]}
        integral = coord._delta_integrals.get(TODAY_A)
        assert integral is not None and integral.available and integral.last_reset == MIDNIGHT

    def test_a_sensor_without_a_base_stays_unavailable(self) -> None:
        coord, pushes = _coordinator()
        _restore_all(coord)
        coord.hand_over_delta_bases({TODAY_A: _Base(1.0, MIDNIGHT)})
        assert _state(coord, TODAY_B) is None and TODAY_B not in pushes

    def test_the_unit_of_the_derived_series_is_passed_on(self) -> None:
        coord, _ = _coordinator()
        _restore_all(coord)
        coord.hand_over_delta_bases({TODAY_B: _Base(1.0, MIDNIGHT)}, units={TODAY_B: "Wh"})
        integral = coord._delta_integrals.get(TODAY_B)
        assert integral is not None and integral.unit == "Wh"

    async def test_after_the_hand_over_an_input_is_no_longer_skipped_and_books(self) -> None:
        coord, _ = _coordinator()
        _restore_all(coord)
        coord.hand_over_delta_bases(
            {
                TODAY_A: _Base(10.0, MIDNIGHT),
                TODAY_B: _Base(0.01, MIDNIGHT),
                COMBINED_TODAY: _Base(0.02, MIDNIGHT),
                COMBINED_TOTAL: _Base(5.0, None),
            }
        )
        _deltas_return[0] = {PV_A: (100.0, "Wh")}
        await coord._async_recalculate_recent_and_report()
        assert _delta_skip_seen == [("recent", frozenset())]
        assert _state(coord, TODAY_A) == pytest.approx(110.0)
        assert _state(coord, COMBINED_TOTAL) == pytest.approx(5.1)

    def test_a_base_that_comes_before_the_sensor_waits_for_it(self) -> None:
        coord, _ = _coordinator()
        coord.hand_over_delta_bases({TODAY_A: _Base(6.0, MIDNIGHT)})
        integral = coord.restore_delta_integral(TODAY_A, "today", PV_A, "Wh", None, None)
        assert integral.state == 6.0
