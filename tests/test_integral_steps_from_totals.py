"""
The energy of a slot from the stored running totals of an integral (TASK-0045e, CAP-13,
ADR-027 decision 9, Q27 = A).

When the PV inputs change, the combined integrals are derived again as the per-slot addition of
the per-input statistics: the per-slot energy of an input is the change of its running total.
``calculation.steps_from_running_totals`` is that reading, the inverse of
``calculation.integral_rows``. Pure: it runs without a fake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from tests.test_delta_integrals import BERLIN, _calc

UTC = timezone.utc
SLOT = timedelta(minutes=5)
HOUR = timedelta(hours=1)


def _u(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def _steps(five: dict[datetime, float], hourly: dict[datetime, float]) -> list[tuple[Any, ...]]:
    return [
        (s.start, pytest.approx(s.energy), s.short)
        for s in _calc.steps_from_running_totals(five, hourly)
    ]


class TestFiveMinuteRows:
    def test_the_energy_of_a_slot_is_the_change_of_the_running_total(self) -> None:
        five = {_u(10, 10): 100.0, _u(10, 10, 5): 125.0, _u(10, 10, 10): 155.0}
        assert _steps(five, {}) == [
            (_u(10, 10, 5), 25.0, True),
            (_u(10, 10, 10), 30.0, True),
        ]

    def test_the_first_row_only_fixes_the_starting_point(self) -> None:
        assert _steps({_u(10, 10): 100.0}, {}) == []

    def test_no_rows_no_steps(self) -> None:
        assert _calc.steps_from_running_totals({}, {}) == []

    def test_a_decrease_is_a_negative_step_and_is_kept(self) -> None:
        """A correction that took energy away (ADR-027 decision 3) lowers the running total."""
        five = {_u(10, 10): 100.0, _u(10, 10, 5): 90.0, _u(10, 10, 10): 95.0}
        assert _steps(five, {}) == [(_u(10, 10, 5), -10.0, True), (_u(10, 10, 10), 5.0, True)]

    def test_a_missing_row_puts_what_the_gap_holds_into_the_next_slot(self) -> None:
        five = {_u(10, 10): 100.0, _u(10, 10, 20): 160.0}
        assert _steps(five, {}) == [(_u(10, 10, 20), 60.0, True)]

    def test_the_rows_need_not_come_sorted(self) -> None:
        five = {_u(10, 10, 10): 155.0, _u(10, 10): 100.0, _u(10, 10, 5): 125.0}
        assert [s[0] for s in _steps(five, {})] == [_u(10, 10, 5), _u(10, 10, 10)]

    def test_the_slot_length_is_a_parameter(self) -> None:
        steps = _calc.steps_from_running_totals({_u(10, 10): 1.0, _u(10, 10, 5): 3.0}, {}, 5)
        assert [s.energy for s in steps] == [2.0]


class TestHourlyRows:
    def test_an_hour_older_than_the_5_minute_rows_is_a_step_of_its_own(self) -> None:
        hourly = {_u(10, 7): 10.0, _u(10, 8): 40.0, _u(10, 9): 100.0}
        assert _steps({}, hourly) == [(_u(10, 8), 30.0, False), (_u(10, 9), 60.0, False)]

    def test_the_first_5_minute_step_continues_from_the_last_hourly_total(self) -> None:
        hourly = {_u(10, 8): 40.0, _u(10, 9): 100.0}  # the total after 10:00 is 100
        five = {_u(10, 10): 110.0, _u(10, 10, 5): 125.0}
        assert _steps(five, hourly) == [
            (_u(10, 9), 60.0, False),
            (_u(10, 10), 10.0, True),
            (_u(10, 10, 5), 15.0, True),
        ]

    def test_the_hour_that_holds_the_first_5_minute_row_is_carried_by_it(self) -> None:
        """Hours end where the first 5-minute row starts or later: their hourly row is ignored
        (the 5-minute rows are the finer reading), and the energy from the last older hourly
        total up to the first 5-minute row lands in that row, so nothing is lost."""
        hourly = {_u(10, 8): 40.0, _u(10, 9): 100.0, _u(10, 10): 190.0}
        five = {_u(10, 10, 20): 160.0, _u(10, 10, 25): 175.0}
        steps = _calc.steps_from_running_totals(five, hourly)
        assert [(s.start, s.short) for s in steps] == [
            (_u(10, 9), False),
            (_u(10, 10, 20), True),
            (_u(10, 10, 25), True),
        ]
        assert [s.energy for s in steps] == pytest.approx([60.0, 60.0, 15.0])

    def test_an_hourly_row_inside_the_5_minute_range_is_ignored(self) -> None:
        five = {_u(10, 10): 100.0, _u(10, 10, 5): 110.0, _u(10, 10, 10): 130.0}
        hourly = {_u(10, 10): 999.0}  # its end is after the first 5-minute row
        assert [s.energy for s in _calc.steps_from_running_totals(five, hourly)] == [10.0, 20.0]

    def test_the_energy_is_conserved_over_the_whole_series(self) -> None:
        hourly = {_u(10, 6): 5.0, _u(10, 7): 15.0, _u(10, 8): 40.0, _u(10, 9): 100.0}
        five = {_u(10, 10): 110.0, _u(10, 10, 5): 125.0, _u(10, 10, 15): 150.0}
        steps = _calc.steps_from_running_totals(five, hourly)
        assert sum(s.energy for s in steps) == pytest.approx(150.0 - 5.0)


class TestInverseOfIntegralRows:
    def test_the_rows_of_an_integral_give_back_its_steps(self) -> None:
        energies = [
            _calc.IntegralStep(_u(10, 7), 12.0, False),
            _calc.IntegralStep(_u(10, 8), 30.0, False),
            _calc.IntegralStep(_u(10, 9), -4.0, False),
            _calc.IntegralStep(_u(10, 10), 7.5, True),
            _calc.IntegralStep(_u(10, 10, 5), 0.0, True),
            _calc.IntegralStep(_u(10, 10, 10), 3.25, True),
        ]
        rows = _calc.integral_rows(energies, BERLIN, today=True)
        five = {r.start: r.total for r in rows if r.short}
        hourly = {r.start: r.total for r in rows if not r.short}
        # an hourly row's total is the total after the hour, so the one of 09:00 is the
        # total after 10:00: that is what the recorder holds for hour 09:00
        steps = _calc.steps_from_running_totals(five, hourly)
        again = [(s.start, s.energy, s.short) for s in steps]
        expected = [(e.start, e.energy, e.short) for e in energies[1:]]
        assert again == [(start, pytest.approx(energy), short) for start, energy, short in expected]

    def test_the_combination_of_two_series_is_the_addition_of_their_steps(self) -> None:
        a = _calc.integral_rows(
            [_calc.IntegralStep(_u(10, 10, 5 * i), float(i)) for i in range(1, 5)],
            BERLIN,
            today=False,
        )
        b = _calc.integral_rows(
            [_calc.IntegralStep(_u(10, 10, 5 * i), 0.5) for i in range(1, 5)], BERLIN, today=False
        )
        steps_a = _calc.steps_from_running_totals({r.start: r.total for r in a}, {})
        steps_b = _calc.steps_from_running_totals({r.start: r.total for r in b}, {})
        today_rows, total_rows = _calc.combined_integral_rows(
            [(steps_a, "Wh"), (steps_b, "kWh")], BERLIN
        )
        # the first row of each series is only its starting point: steps 2..4 remain
        wh = [2.0, 3.0, 4.0]
        kwh = [0.5, 0.5, 0.5]
        expected_kwh = [w / 1000 + k for w, k in zip(wh, kwh, strict=True)]
        assert [r.total for r in total_rows] == pytest.approx(
            [sum(expected_kwh[: i + 1]) for i in range(3)]
        )
        assert [r.start for r in today_rows] == [_u(10, 10, 10), _u(10, 10, 15), _u(10, 10, 20)]
