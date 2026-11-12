"""
The live state of the PV delta integrals (TASK-0045d, CAP-13, ADR-027 decisions 3 to 5).

``delta_integrals.py`` and ``calculation.py`` are pure (no Home Assistant): they are loaded by
file path and run without a fake. What is tested here: the restore of a sensor (and the reset
of a *today* integral whose ``last_reset`` is of an earlier day), the booking of a run's PV
deltas, the reset at local midnight (daylight saving included) and the base the derivation
hands over.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

_BASE = Path(__file__).resolve().parent.parent / "custom_components" / "effy"
_PKG = "effy_delta_integrals_under_test"


def _load() -> tuple[ModuleType, ModuleType]:
    pkg = ModuleType(_PKG)
    pkg.__path__ = [str(_BASE)]
    pkg.__package__ = _PKG
    sys.modules[_PKG] = pkg
    loaded: dict[str, ModuleType] = {}
    for name in ("calculation", "delta_integrals"):
        spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _BASE / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _PKG
        sys.modules[f"{_PKG}.{name}"] = mod
        setattr(pkg, name, mod)
        spec.loader.exec_module(mod)
        loaded[name] = mod
    return loaded["calculation"], loaded["delta_integrals"]


_calc, _di = _load()

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
YESTERDAY = datetime(2026, 11, 9, 0, 0, tzinfo=BERLIN)


@dataclass(frozen=True)
class _Base:
    """What ``history.DeltaBase`` has: the two fields the registry reads."""

    state: float
    last_reset: datetime | None
    total: float = 0.0


def _restore(
    integrals: Any,
    entity_id: str,
    kind: str,
    source: str | None,
    value: float | None,
    last_reset: datetime | None = MIDNIGHT,
    unit: str = "kWh",
    now: datetime = NOW,
) -> Any:
    return integrals.restore(
        entity_id=entity_id,
        kind=kind,
        source=source,
        unit=unit,
        value=value,
        last_reset=last_reset,
        now=now,
        tz=BERLIN,
    )


def _all(values: dict[str, float | None] | None = None) -> Any:
    """All four sensors of two PV inputs, restored with ``values`` (None: nothing restored)."""
    values = values or {}
    integrals = _di.DeltaIntegrals()
    _restore(integrals, TODAY_A, "today", PV_A, values.get(TODAY_A), unit="Wh")
    _restore(integrals, TODAY_B, "today", PV_B, values.get(TODAY_B), unit="kWh")
    _restore(integrals, COMBINED_TODAY, "today", None, values.get(COMBINED_TODAY))
    _restore(integrals, COMBINED_TOTAL, "total", None, values.get(COMBINED_TOTAL), last_reset=None)
    return integrals


def _state(integrals: Any, entity_id: str) -> float | None:
    state: float | None = integrals.get(entity_id).state
    return state


# ---------------------------------------------------------------------------
# next_local_midnight
# ---------------------------------------------------------------------------


class TestNextLocalMidnight:
    def test_it_is_the_first_local_midnight_after_the_time(self) -> None:
        assert _calc.next_local_midnight(NOW, BERLIN) == datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN)

    def test_at_exactly_midnight_it_is_the_next_one_not_the_same(self) -> None:
        assert _calc.next_local_midnight(MIDNIGHT, BERLIN) == datetime(
            2026, 11, 11, 0, 0, tzinfo=BERLIN
        )

    def test_just_before_midnight_it_is_that_midnight(self) -> None:
        late = datetime(2026, 11, 10, 22, 59, 59, tzinfo=UTC)  # 23:59:59 in Berlin
        assert _calc.next_local_midnight(late, BERLIN) == datetime(
            2026, 11, 11, 0, 0, tzinfo=BERLIN
        )

    def test_a_day_with_daylight_saving_has_23_or_25_hours(self) -> None:
        spring = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)  # the day before the change
        nxt = _calc.next_local_midnight(spring, BERLIN)
        after = _calc.next_local_midnight(nxt, BERLIN)
        assert (after.astimezone(UTC) - nxt.astimezone(UTC)).total_seconds() == 23 * 3600
        autumn = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
        nxt = _calc.next_local_midnight(autumn, BERLIN)
        after = _calc.next_local_midnight(nxt, BERLIN)
        assert (after.astimezone(UTC) - nxt.astimezone(UTC)).total_seconds() == 25 * 3600

    def test_it_agrees_with_the_midnight_of_the_next_day(self) -> None:
        nxt = _calc.next_local_midnight(NOW, BERLIN)
        assert _calc.local_midnight(nxt, BERLIN) == nxt
        assert nxt > NOW and nxt - NOW <= timedelta(hours=25)


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


class TestRestore:
    def test_nothing_restored_is_unavailable(self) -> None:
        integrals = _all()
        for entity_id in (TODAY_A, TODAY_B, COMBINED_TODAY, COMBINED_TOTAL):
            assert integrals.get(entity_id).available is False
            assert _state(integrals, entity_id) is None

    def test_a_restored_state_is_kept_exactly(self) -> None:
        integrals = _all({TODAY_A: 123.456789012345, COMBINED_TOTAL: 99.5})
        assert _state(integrals, TODAY_A) == 123.456789012345
        assert integrals.get(TODAY_A).last_reset == MIDNIGHT
        assert integrals.get(TODAY_A).available is True
        assert _state(integrals, COMBINED_TOTAL) == 99.5
        assert integrals.get(COMBINED_TOTAL).last_reset is None

    def test_the_sensor_knows_its_kind_unit_and_source(self) -> None:
        integrals = _all()
        a = integrals.get(TODAY_A)
        assert (a.kind, a.source, a.unit) == ("today", PV_A, "Wh")
        total = integrals.get(COMBINED_TOTAL)
        assert (total.kind, total.source, total.unit) == ("total", None, "kWh")

    def test_a_today_integral_of_an_earlier_day_is_reset_at_startup(self) -> None:
        integrals = _all()
        restored = _restore(integrals, TODAY_A, "today", PV_A, 7.5, last_reset=YESTERDAY)
        assert restored.state == 0.0
        assert restored.last_reset == MIDNIGHT

    def test_a_today_integral_without_a_last_reset_is_reset_too(self) -> None:
        integrals = _all()
        restored = _restore(integrals, TODAY_A, "today", PV_A, 7.5, last_reset=None)
        assert restored.state == 0.0 and restored.last_reset == MIDNIGHT

    def test_a_today_integral_of_this_day_keeps_its_state(self) -> None:
        integrals = _all()
        restored = _restore(
            integrals,
            TODAY_A,
            "today",
            PV_A,
            7.5,
            last_reset=MIDNIGHT + timedelta(minutes=0),
        )
        assert restored.state == 7.5 and restored.last_reset == MIDNIGHT

    def test_the_day_is_the_local_one_in_a_zone_ahead_of_utc(self) -> None:
        """23:30 UTC on the 9th is 00:30 on the 10th in Berlin: a last_reset at the 9th's
        local midnight is a day old."""
        late = datetime(2026, 11, 9, 23, 30, tzinfo=UTC)
        integrals = _di.DeltaIntegrals()
        restored = integrals.restore(
            entity_id=TODAY_A,
            kind="today",
            source=PV_A,
            unit="Wh",
            value=5.0,
            last_reset=YESTERDAY,
            now=late,
            tz=BERLIN,
        )
        assert restored.state == 0.0 and restored.last_reset == MIDNIGHT

    def test_the_total_is_never_reset(self) -> None:
        integrals = _all()
        restored = _restore(
            integrals, COMBINED_TOTAL, "total", None, 1234.5, last_reset=YESTERDAY, unit="kWh"
        )
        assert restored.state == 1234.5
        assert restored.last_reset is None  # the total has none, whatever was restored

    def test_a_negative_state_is_restored_like_any_other(self) -> None:
        integrals = _all({COMBINED_TODAY: -0.25})
        assert _state(integrals, COMBINED_TODAY) == -0.25


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


class TestBooking:
    def test_each_input_grows_by_its_delta_and_the_combined_by_the_addition_in_kwh(self) -> None:
        integrals = _all({TODAY_A: 100.0, TODAY_B: 1.0, COMBINED_TODAY: 1.1, COMBINED_TOTAL: 50.0})
        changed = integrals.book({PV_A: (250.0, "Wh"), PV_B: (0.5, "kWh")})
        assert _state(integrals, TODAY_A) == pytest.approx(350.0)  # Wh
        assert _state(integrals, TODAY_B) == pytest.approx(1.5)  # kWh
        assert _state(integrals, COMBINED_TODAY) == pytest.approx(1.1 + 0.25 + 0.5)
        assert _state(integrals, COMBINED_TOTAL) == pytest.approx(50.0 + 0.25 + 0.5)
        assert {i.entity_id for i in changed} == {
            TODAY_A,
            TODAY_B,
            COMBINED_TODAY,
            COMBINED_TOTAL,
        }

    def test_a_negative_delta_lowers_the_states_and_nothing_is_clamped(self) -> None:
        integrals = _all({TODAY_A: 100.0, TODAY_B: 0.5, COMBINED_TODAY: 0.2, COMBINED_TOTAL: 10.0})
        integrals.book({PV_A: (-300.0, "Wh"), PV_B: (-1.0, "kWh")})
        assert _state(integrals, TODAY_A) == pytest.approx(-200.0)
        assert _state(integrals, TODAY_B) == pytest.approx(-0.5)
        assert _state(integrals, COMBINED_TODAY) == pytest.approx(0.2 - 0.3 - 1.0)  # below 0
        assert _state(integrals, COMBINED_TOTAL) == pytest.approx(10.0 - 1.3)

    def test_the_state_is_kept_at_full_precision(self) -> None:
        integrals = _all({TODAY_A: 0.0, TODAY_B: 0.0, COMBINED_TODAY: 0.0, COMBINED_TOTAL: 0.0})
        for _ in range(3):
            integrals.book({PV_A: (0.1234567891, "Wh")})
        assert _state(integrals, TODAY_A) == pytest.approx(3 * 0.1234567891, abs=1e-15)
        assert _state(integrals, TODAY_A) != round(_state(integrals, TODAY_A) or 0.0, 3)

    def test_a_delta_in_another_unit_is_converted_to_the_sensors_unit(self) -> None:
        integrals = _all({TODAY_A: 0.0, TODAY_B: 0.0, COMBINED_TODAY: 0.0, COMBINED_TOTAL: 0.0})
        integrals.book({PV_A: (2.0, "kWh"), PV_B: (500.0, "Wh")})
        assert _state(integrals, TODAY_A) == pytest.approx(2000.0)  # the sensor is in Wh
        assert _state(integrals, TODAY_B) == pytest.approx(0.5)  # the sensor is in kWh
        assert _state(integrals, COMBINED_TOTAL) == pytest.approx(2.5)

    def test_an_unavailable_sensor_books_nothing_and_neither_does_the_combined_for_it(self) -> None:
        integrals = _all({TODAY_B: 1.0, COMBINED_TODAY: 4.0, COMBINED_TOTAL: 9.0})
        # TODAY_A has no base: its delta is not booked, not even into the combined ones (the
        # derivation that gives it its base also adds it to them)
        changed = integrals.book({PV_A: (1000.0, "Wh"), PV_B: (0.5, "kWh")})
        assert _state(integrals, TODAY_A) is None
        assert _state(integrals, TODAY_B) == pytest.approx(1.5)
        assert _state(integrals, COMBINED_TODAY) == pytest.approx(4.5)
        assert _state(integrals, COMBINED_TOTAL) == pytest.approx(9.5)
        assert TODAY_A not in {i.entity_id for i in changed}

    def test_an_unavailable_combined_sensor_books_nothing(self) -> None:
        integrals = _all({TODAY_A: 1.0, COMBINED_TOTAL: 9.0})
        integrals.book({PV_A: (1000.0, "Wh")})
        assert _state(integrals, COMBINED_TODAY) is None
        assert _state(integrals, COMBINED_TOTAL) == pytest.approx(10.0)
        assert _state(integrals, TODAY_A) == pytest.approx(1001.0)

    def test_an_input_without_a_sensor_books_nothing(self) -> None:
        integrals = _all({TODAY_A: 1.0, COMBINED_TODAY: 1.0, COMBINED_TOTAL: 1.0})
        assert integrals.book({"sensor.unknown": (5.0, "kWh")}) == []
        assert _state(integrals, COMBINED_TOTAL) == 1.0

    @pytest.mark.parametrize("deltas", [None, {}])
    def test_a_run_without_a_delta_books_nothing(self, deltas: Any) -> None:
        integrals = _all({TODAY_A: 1.0, TODAY_B: 1.0, COMBINED_TODAY: 1.0, COMBINED_TOTAL: 1.0})
        assert integrals.book(deltas) == []
        assert _state(integrals, TODAY_A) == 1.0 and _state(integrals, COMBINED_TOTAL) == 1.0

    def test_a_zero_delta_books_a_state_that_is_unchanged(self) -> None:
        integrals = _all({TODAY_A: 5.0, COMBINED_TODAY: 1.0, COMBINED_TOTAL: 1.0})
        integrals.book({PV_A: (0.0, "Wh")})
        assert _state(integrals, TODAY_A) == 5.0 and _state(integrals, COMBINED_TOTAL) == 1.0

    def test_booking_never_touches_last_reset(self) -> None:
        integrals = _all({TODAY_A: 5.0, COMBINED_TODAY: 1.0, COMBINED_TOTAL: 1.0})
        integrals.book({PV_A: (10.0, "Wh")})
        assert integrals.get(TODAY_A).last_reset == MIDNIGHT
        assert integrals.get(COMBINED_TOTAL).last_reset is None


class TestSkippedInputs:
    def test_an_input_has_a_base_when_its_sensor_is_available(self) -> None:
        integrals = _all({TODAY_A: 0.0})
        assert integrals.has_base(PV_A) is True
        assert integrals.has_base(PV_B) is False  # unavailable
        assert integrals.has_base("sensor.unknown") is False  # no sensor yet

    def test_the_inputs_without_a_base_are_the_ones_to_skip(self) -> None:
        integrals = _all({TODAY_A: 0.0})
        skip = integrals.skip_sources([PV_A, PV_B, "sensor.unknown"])
        assert skip == frozenset({PV_B, "sensor.unknown"})

    def test_all_inputs_are_skipped_before_any_sensor_exists(self) -> None:
        assert _di.DeltaIntegrals().skip_sources([PV_A, PV_B]) == frozenset({PV_A, PV_B})


# ---------------------------------------------------------------------------
# Midnight
# ---------------------------------------------------------------------------


class TestMidnight:
    def test_both_today_kinds_are_set_to_zero_with_the_midnight_as_last_reset(self) -> None:
        integrals = _all({TODAY_A: 12.0, TODAY_B: 3.0, COMBINED_TODAY: 3.5, COMBINED_TOTAL: 80.0})
        new_midnight = datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN)
        changed = integrals.reset_today(new_midnight)
        for entity_id in (TODAY_A, TODAY_B, COMBINED_TODAY):
            assert _state(integrals, entity_id) == 0.0
            assert integrals.get(entity_id).last_reset == new_midnight
        assert {i.entity_id for i in changed} == {TODAY_A, TODAY_B, COMBINED_TODAY}

    def test_the_total_is_never_reset(self) -> None:
        integrals = _all({COMBINED_TOTAL: 80.0})
        integrals.reset_today(datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN))
        assert _state(integrals, COMBINED_TOTAL) == 80.0
        assert integrals.get(COMBINED_TOTAL).last_reset is None

    def test_an_unavailable_sensor_stays_unavailable(self) -> None:
        integrals = _all({TODAY_B: 3.0})
        changed = integrals.reset_today(datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN))
        assert _state(integrals, TODAY_A) is None
        assert integrals.get(TODAY_A).last_reset == MIDNIGHT  # untouched
        assert {i.entity_id for i in changed} == {TODAY_B}

    def test_a_negative_today_is_reset_to_zero_as_well(self) -> None:
        integrals = _all({COMBINED_TODAY: -2.0})
        integrals.reset_today(datetime(2026, 11, 11, 0, 0, tzinfo=BERLIN))
        assert _state(integrals, COMBINED_TODAY) == 0.0


# ---------------------------------------------------------------------------
# The base the derivation hands over
# ---------------------------------------------------------------------------


class TestHandOver:
    def test_each_sensor_gets_exactly_the_base_state_and_last_reset(self) -> None:
        integrals = _all()
        bases = {
            TODAY_A: _Base(12.5, MIDNIGHT),
            COMBINED_TODAY: _Base(0.0125, MIDNIGHT),
            COMBINED_TOTAL: _Base(7777.125, None),
        }
        changed = integrals.hand_over(bases, NOW, BERLIN)
        assert _state(integrals, TODAY_A) == 12.5
        assert integrals.get(TODAY_A).last_reset == MIDNIGHT
        assert _state(integrals, COMBINED_TODAY) == 0.0125
        assert _state(integrals, COMBINED_TOTAL) == 7777.125
        assert integrals.get(COMBINED_TOTAL).last_reset is None
        assert integrals.get(TODAY_A).available is True
        assert {i.entity_id for i in changed} == {TODAY_A, COMBINED_TODAY, COMBINED_TOTAL}

    def test_a_sensor_that_is_not_in_the_hand_over_is_left_alone(self) -> None:
        integrals = _all({TODAY_B: 4.0})
        integrals.hand_over({TODAY_A: _Base(1.0, MIDNIGHT)}, NOW, BERLIN)
        assert _state(integrals, TODAY_B) == 4.0
        assert _state(integrals, COMBINED_TOTAL) is None

    def test_the_base_replaces_a_restored_state(self) -> None:
        integrals = _all({TODAY_A: 99.0})
        integrals.hand_over({TODAY_A: _Base(3.0, MIDNIGHT)}, NOW, BERLIN)
        assert _state(integrals, TODAY_A) == 3.0

    def test_nothing_to_derive_starts_at_zero_with_this_days_midnight(self) -> None:
        integrals = _all()
        integrals.hand_over(
            {TODAY_A: _Base(0.0, None), COMBINED_TOTAL: _Base(0.0, None)}, NOW, BERLIN
        )
        assert _state(integrals, TODAY_A) == 0.0
        assert integrals.get(TODAY_A).last_reset == MIDNIGHT  # a today integral has one
        assert _state(integrals, COMBINED_TOTAL) == 0.0
        assert integrals.get(COMBINED_TOTAL).last_reset is None

    def test_a_base_of_an_earlier_day_is_reset_like_a_restored_state(self) -> None:
        """The midnight passed while the sensor was unavailable: nothing reset it then."""
        integrals = _all()
        integrals.hand_over({TODAY_A: _Base(8.0, YESTERDAY)}, NOW, BERLIN)
        assert _state(integrals, TODAY_A) == 0.0
        assert integrals.get(TODAY_A).last_reset == MIDNIGHT

    def test_the_unit_of_the_derived_series_is_taken_over_when_given(self) -> None:
        integrals = _all()
        integrals.hand_over({TODAY_A: _Base(3.0, MIDNIGHT)}, NOW, BERLIN, units={TODAY_A: "kWh"})
        assert integrals.get(TODAY_A).unit == "kWh"
        integrals.book({PV_A: (500.0, "Wh")})
        assert _state(integrals, TODAY_A) == pytest.approx(3.5)

    def test_a_base_for_a_sensor_that_is_not_there_yet_is_applied_when_it_restores(self) -> None:
        integrals = _di.DeltaIntegrals()
        integrals.hand_over({TODAY_A: _Base(6.0, MIDNIGHT)}, NOW, BERLIN)
        restored = _restore(integrals, TODAY_A, "today", PV_A, None, unit="Wh")
        assert restored.state == 6.0 and restored.last_reset == MIDNIGHT
        # and only once
        again = _restore(integrals, TODAY_A, "today", PV_A, None, unit="Wh")
        assert again.state is None

    def test_after_the_hand_over_the_sensors_book_deltas(self) -> None:
        integrals = _all()
        integrals.hand_over(
            {
                TODAY_A: _Base(10.0, MIDNIGHT),
                COMBINED_TODAY: _Base(0.5, MIDNIGHT),
                COMBINED_TOTAL: _Base(20.0, None),
            },
            NOW,
            BERLIN,
        )
        integrals.book({PV_A: (100.0, "Wh")})
        assert _state(integrals, TODAY_A) == pytest.approx(110.0)
        assert _state(integrals, COMBINED_TODAY) == pytest.approx(0.6)
        assert _state(integrals, COMBINED_TOTAL) == pytest.approx(20.1)


# ---------------------------------------------------------------------------
# Unavailable on purpose (TASK-0045e: the combined integrals are rewritten)
# ---------------------------------------------------------------------------


class TestInvalidate:
    def test_the_named_sensors_become_unavailable_and_are_returned(self) -> None:
        integrals = _all({TODAY_A: 1.0, COMBINED_TODAY: 2.0, COMBINED_TOTAL: 3.0})
        changed = integrals.invalidate([COMBINED_TODAY, COMBINED_TOTAL])
        assert {i.entity_id for i in changed} == {COMBINED_TODAY, COMBINED_TOTAL}
        assert integrals.get(COMBINED_TODAY).available is False
        assert integrals.get(COMBINED_TOTAL).available is False
        assert _state(integrals, TODAY_A) == 1.0  # the others are not touched

    def test_a_sensor_that_is_unavailable_already_is_not_reported(self) -> None:
        integrals = _all({COMBINED_TOTAL: 3.0})
        changed = integrals.invalidate([COMBINED_TODAY, COMBINED_TOTAL])
        assert [i.entity_id for i in changed] == [COMBINED_TOTAL]

    def test_an_unknown_sensor_is_ignored(self) -> None:
        assert _all({}).invalidate(["sensor.nothing"]) == []

    def test_an_unavailable_integral_books_nothing_until_it_has_a_base_again(self) -> None:
        integrals = _all({TODAY_A: 1.0, COMBINED_TODAY: 2.0, COMBINED_TOTAL: 3.0})
        integrals.invalidate([COMBINED_TODAY, COMBINED_TOTAL])
        integrals.book({PV_A: (10.0, "Wh")})
        assert _state(integrals, COMBINED_TOTAL) is None
        integrals.hand_over({COMBINED_TOTAL: _Base(5.0, None)}, NOW, BERLIN)
        integrals.book({PV_A: (1000.0, "Wh")})
        assert _state(integrals, COMBINED_TOTAL) == pytest.approx(6.0)
