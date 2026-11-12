"""
The implicit full recalculation after a change of the formula (TASK-0045e, CAP-13, ADR-027
decisions 6 and 9).

Runs the real ``coordinator.py`` against the fake history of ``test_coordinator_slot``: its fakes
return the bases a test sets, say in which order they were called and whether the recorder lock
was held. What is tested: the order of the steps, who gets which base, the rewrite of the
combined integrals when the PV inputs changed (Q27 = A), the signature stored last and only on
success, and that a failing step leaves the sensors unavailable and the slot jobs working.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

from tests.test_coordinator_slot import (
    EffyCoordinator,
    _coord_mod,
    _delta_fail_at,
    _delta_flow,
    _delta_lock,
    _delta_pause,
    _delta_read_return,
    _delta_skip_seen,
    _deltas_return,
    _derive_return,
    _FakeEntry,
    _FakeHass,
    _lock_probe,
    _rederive_return,
    _reset_slot_job_fakes,
    _stored_units,
)

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
PV_A = "sensor.pv_a"
PV_B = "sensor.pv_b"
TODAY_A = "sensor.effy_pv_a_delta_integral_today"
TODAY_B = "sensor.effy_pv_b_delta_integral_today"
COMBINED_TODAY = "sensor.effy_pv_deltasum_integral_today"
COMBINED_TOTAL = "sensor.effy_pv_deltasum_integral_total"
NOW = datetime(2026, 11, 10, 12, 30, tzinfo=UTC)
MIDNIGHT = datetime(2026, 11, 10, 0, 0, tzinfo=BERLIN)
PV_KEY = "pv_input_sensors"
SIGNATURE_KEY = "formula_signature"
SIGNATURE = {PV_KEY: [PV_A, PV_B], "marker": "the new one"}


@dataclass(frozen=True)
class _Base:
    state: float
    last_reset: datetime | None
    total: float = 0.0


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    _reset_slot_job_fakes()
    yield
    _lock_probe[0] = None


def _coordinator(pv: list[str] | None = None, stored: Any = "absent") -> EffyCoordinator:
    pv = [PV_A, PV_B] if pv is None else pv
    entry = _FakeEntry(pv, ["sensor.export"])
    if stored != "absent":
        entry.data = {SIGNATURE_KEY: stored, "other": "kept"}
    coord = EffyCoordinator(_FakeHass(), entry)
    coord._now = lambda: NOW  # type: ignore[method-assign]
    _lock_probe[0] = lambda: coord._recorder_job_lock().locked()
    return coord


def _sensors(coord: EffyCoordinator, **values: float | None) -> dict[str, list[Any]]:
    """The four sensors restored (``a``, ``b``, ``today``, ``total``; None: nothing restored)."""
    pushes: dict[str, list[Any]] = {}

    def listener(entity_id: str) -> Any:
        def push(integral: Any) -> None:
            pushes.setdefault(entity_id, []).append(integral.state)

        return push

    spec: tuple[tuple[str, Literal["today", "total"], str | None, str, float | None], ...] = (
        (TODAY_A, "today", PV_A, "Wh", values.get("a")),
        (TODAY_B, "today", PV_B, "kWh", values.get("b")),
        (COMBINED_TODAY, "today", None, "kWh", values.get("today")),
        (COMBINED_TOTAL, "total", None, "kWh", values.get("total")),
    )
    for entity_id, kind, source, unit, value in spec:
        coord.restore_delta_integral(
            entity_id, kind, source, unit, value, None if kind == "total" else MIDNIGHT
        )
        coord.subscribe_delta_integral(entity_id, listener(entity_id))
    return pushes


def _state(coord: EffyCoordinator, entity_id: str) -> float | None:
    integral = coord._delta_integrals.get(entity_id)
    assert integral is not None
    return integral.state


def _run(coord: EffyCoordinator, signature: Any = SIGNATURE) -> bool:
    return asyncio.run(coord.async_run_formula_recalculation(signature))


class TestTheSteps:
    def test_the_steps_run_in_order_under_the_recorder_lock(self) -> None:
        coord = _coordinator(stored=None)
        _sensors(coord)
        _derive_return[0] = {TODAY_A: _Base(1.0, MIDNIGHT)}
        assert _run(coord) is True
        assert [s for s in _delta_flow if s != "units"] == ["read", "recalculate", "derive"]
        assert all(_delta_lock[step] for step in ("read", "recalculate", "derive"))

    def test_the_signature_is_stored_last_in_the_entry_data_and_nothing_else_changes(self) -> None:
        coord = _coordinator(stored={PV_KEY: [PV_A]})
        _sensors(coord)
        assert _run(coord) is True
        updates = coord._hass.config_entries.updates
        assert len(updates) == 1 and updates[0]["options"] is None
        assert updates[0]["data"] == {SIGNATURE_KEY: SIGNATURE, "other": "kept"}
        assert coord._entry.data[SIGNATURE_KEY] == SIGNATURE

    def test_the_stored_signature_is_a_copy(self) -> None:
        coord = _coordinator()
        _sensors(coord)
        signature = {PV_KEY: [PV_A, PV_B]}
        _run(coord, signature)
        signature[PV_KEY].append("sensor.changed_later")  # what the caller does afterwards
        assert coord._entry.data[SIGNATURE_KEY] == {PV_KEY: [PV_A, PV_B]}

    def test_the_full_recalculation_is_the_buttons_and_reads_fresh(self) -> None:
        coord = _coordinator()
        _sensors(coord)
        _run(coord)
        assert _delta_skip_seen == [("history", frozenset({PV_A, PV_B}))]  # no base yet: skipped

    def test_no_update_listener_is_involved_and_options_stay_untouched(self) -> None:
        coord = _coordinator()
        options_before = dict(coord._entry.options)
        _sensors(coord)
        _run(coord)
        assert coord._entry.options == options_before


class TestWhoGetsWhichBase:
    def test_an_input_with_statistics_gets_its_last_row_before_the_recalculation_books(
        self,
    ) -> None:
        """An input removed and added again: nothing is derived, its base is its last row, and
        the recalculation then books the gap as deltas."""
        coord = _coordinator(stored=SIGNATURE)
        pushes = _sensors(coord, b=0.5, today=0.5, total=9.0)  # A: no state restored
        _delta_read_return[0] = ({TODAY_A: _Base(40.0, MIDNIGHT)}, {TODAY_A: "Wh"})
        _deltas_return[0] = {PV_A: (10.0, "Wh"), PV_B: (0.25, "kWh")}
        assert _run(coord) is True
        assert _state(coord, TODAY_A) == pytest.approx(50.0)  # the base 40, then the gap 10
        assert pushes[TODAY_A] == [40.0, 50.0]
        assert _delta_skip_seen == [("history", frozenset())]  # A has its base: not skipped
        assert _state(coord, TODAY_B) == pytest.approx(0.75)  # a restored one books normally
        assert _derive_return[0] == {}

    def test_a_restored_sensor_keeps_its_state_and_is_not_given_the_last_row(self) -> None:
        coord = _coordinator(stored=SIGNATURE)
        pushes = _sensors(coord, a=7.0, b=1.0, today=1.0, total=1.0)
        _delta_read_return[0] = ({TODAY_A: _Base(40.0, MIDNIGHT)}, {TODAY_A: "Wh"})
        _run(coord)
        assert _state(coord, TODAY_A) == 7.0 and TODAY_A not in pushes

    def test_a_derived_base_is_handed_over_after_the_recalculation_with_its_unit(self) -> None:
        coord = _coordinator(stored=None)
        pushes = _sensors(coord)
        _derive_return[0] = {
            TODAY_A: _Base(12.0, MIDNIGHT),
            TODAY_B: _Base(0.012, MIDNIGHT),
            COMBINED_TODAY: _Base(0.024, MIDNIGHT),
            COMBINED_TOTAL: _Base(5.5, None),
        }
        _stored_units.update(
            {TODAY_A: "Wh", TODAY_B: "kWh", COMBINED_TODAY: "kWh", COMBINED_TOTAL: "kWh"}
        )
        _deltas_return[0] = {PV_A: (999.0, "Wh")}  # skipped: A had no base during the run
        assert _run(coord) is True
        assert _state(coord, TODAY_A) == 12.0  # exactly the base, the delta was not booked
        assert _state(coord, COMBINED_TOTAL) == 5.5
        assert pushes[TODAY_A] == [12.0]
        integral = coord._delta_integrals.get(TODAY_B)
        assert integral is not None and integral.unit == "kWh"
        assert _delta_flow.index("derive") > _delta_flow.index("recalculate")

    def test_an_input_that_is_added_is_derived_after_the_recalculation(self) -> None:
        coord = _coordinator(stored={PV_KEY: [PV_A]})
        _sensors(coord, a=3.0, today=0.003, total=1.0)  # B is the new input
        _derive_return[0] = {TODAY_B: _Base(0.2, MIDNIGHT)}
        _stored_units[TODAY_B] = "kWh"
        _deltas_return[0] = {PV_A: (5.0, "Wh"), PV_B: (0.1, "kWh")}
        _run(coord)
        assert _state(coord, TODAY_B) == 0.2  # B's delta of the run was not booked
        assert _state(coord, TODAY_A) == pytest.approx(8.0)  # A books normally

    def test_with_nothing_to_hand_over_the_units_are_not_asked_for(self) -> None:
        coord = _coordinator(stored=SIGNATURE)
        _sensors(coord, a=1.0, b=1.0, today=1.0, total=1.0)
        _run(coord)
        assert "units" not in _delta_flow

    def test_an_entry_without_a_pv_input_still_recalculates_and_derives_nothing(self) -> None:
        coord = _coordinator(pv=[], stored={PV_KEY: [PV_A]})
        assert _run(coord, {PV_KEY: []}) is True
        assert _delta_flow.count("recalculate") == 1
        assert "rederive" not in _delta_flow
        assert coord._entry.data[SIGNATURE_KEY] == {PV_KEY: []}


class TestCombinedIntegralsWhenThePvInputsChange:
    def _setup(self, stored: Any, **values: float | None) -> tuple[EffyCoordinator, Any]:
        coord = _coordinator(stored=stored)
        pushes = _sensors(coord, **values)
        _delta_read_return[0] = (
            {COMBINED_TODAY: _Base(2.0, MIDNIGHT), COMBINED_TOTAL: _Base(50.0, None)},
            {COMBINED_TODAY: "kWh", COMBINED_TOTAL: "kWh"},
        )
        _rederive_return[0] = (
            {COMBINED_TODAY: _Base(0.4, MIDNIGHT), COMBINED_TOTAL: _Base(30.0, None)},
            {COMBINED_TODAY: "kWh", COMBINED_TOTAL: "kWh"},
        )
        _stored_units.update({COMBINED_TODAY: "kWh", COMBINED_TOTAL: "kWh"})
        return coord, pushes

    def test_a_changed_set_of_pv_inputs_derives_the_combined_again_after_the_derivation(
        self,
    ) -> None:
        coord, _ = self._setup({PV_KEY: [PV_A]}, a=1.0, b=1.0, today=2.0, total=50.0)
        _run(coord)
        assert [s for s in _delta_flow if s != "units"] == [
            "read",
            "recalculate",
            "derive",
            "rederive",
        ]
        assert _state(coord, COMBINED_TODAY) == 0.4 and _state(coord, COMBINED_TOTAL) == 30.0

    def test_the_combined_sensors_are_unavailable_meanwhile_and_get_the_new_base(self) -> None:
        coord, pushes = self._setup({PV_KEY: [PV_A]}, a=1.0, b=1.0, today=2.0, total=50.0)
        _run(coord)
        assert pushes[COMBINED_TOTAL] == [None, 30.0]  # unavailable, then exactly the new base
        assert pushes[COMBINED_TODAY] == [None, 0.4]

    def test_nothing_is_booked_into_the_combined_during_the_recalculation(self) -> None:
        coord, pushes = self._setup({PV_KEY: [PV_A]}, a=1.0, b=1.0, today=2.0, total=50.0)
        _deltas_return[0] = {PV_A: (1000.0, "Wh")}
        _run(coord)
        assert pushes[COMBINED_TOTAL] == [None, 30.0]  # the booking found it unavailable

    def test_the_last_row_of_the_old_composition_is_not_handed_over_first(self) -> None:
        coord, pushes = self._setup({PV_KEY: [PV_A]}, a=1.0, b=1.0)  # combined: no state
        _run(coord)
        assert 50.0 not in pushes[COMBINED_TOTAL] and 2.0 not in pushes[COMBINED_TODAY]

    def test_the_same_pv_inputs_do_not_touch_the_combined(self) -> None:
        coord, pushes = self._setup(
            {PV_KEY: [PV_B, PV_A]}, a=1.0, b=1.0, today=2.0, total=50.0
        )  # order differs, set equal
        _run(coord)
        assert "rederive" not in _delta_flow
        assert COMBINED_TOTAL not in pushes and _state(coord, COMBINED_TOTAL) == 50.0

    def test_the_same_pv_inputs_but_unavailable_sensors_get_the_last_row(self) -> None:
        coord, pushes = self._setup(SIGNATURE, a=1.0, b=1.0)  # combined restored nothing
        _run(coord)
        assert "rederive" not in _delta_flow
        assert pushes[COMBINED_TOTAL] == [50.0] and pushes[COMBINED_TODAY] == [2.0]

    def test_without_a_stored_signature_existing_combined_integrals_are_derived_again(self) -> None:
        coord, _ = self._setup(None, a=1.0, b=1.0, today=2.0, total=50.0)
        _run(coord)
        assert "rederive" in _delta_flow

    def test_without_a_stored_signature_and_no_combined_statistics_they_are_derived_once(
        self,
    ) -> None:
        coord = _coordinator(stored=None)
        _sensors(coord)
        _derive_return[0] = {
            COMBINED_TODAY: _Base(0.1, MIDNIGHT),
            COMBINED_TOTAL: _Base(0.1, None),
        }
        _stored_units.update({COMBINED_TODAY: "kWh", COMBINED_TOTAL: "kWh"})
        _run(coord)
        assert "rederive" not in _delta_flow
        assert _state(coord, COMBINED_TOTAL) == 0.1

    def test_a_changed_formula_without_a_change_of_the_pv_inputs_keeps_the_combined(self) -> None:
        coord, _ = self._setup(
            {PV_KEY: [PV_A, PV_B], "marker": "old"}, a=1.0, b=1.0, today=2.0, total=50.0
        )
        _run(coord)
        assert "rederive" not in _delta_flow


class TestAFailingStep:
    @pytest.mark.parametrize(
        ("step", "named"),
        [
            ("read", "read the bases"),
            ("recalculate", "recalculate the history"),
            ("derive", "derive the integrals"),
            ("rederive", "derive the combined integrals again"),
        ],
    )
    def test_the_signature_is_not_stored_a_warning_is_logged_and_the_sensors_stay(
        self, step: str, named: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        coord = _coordinator(stored={PV_KEY: [PV_A]})
        pushes = _sensors(coord, today=2.0, total=50.0)  # both inputs unavailable
        _delta_read_return[0] = (
            {COMBINED_TOTAL: _Base(50.0, None)},
            {COMBINED_TOTAL: "kWh"},
        )
        _derive_return[0] = {TODAY_A: _Base(1.0, MIDNIGHT)}
        _delta_fail_at[0] = step
        with caplog.at_level("WARNING"):
            assert _run(coord) is False
        assert coord._hass.config_entries.updates == []
        assert coord._entry.data == {SIGNATURE_KEY: {PV_KEY: [PV_A]}, "other": "kept"}
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert f"'{named}'" in warnings[0] and "exploded" in warnings[0]
        assert "next setup" in warnings[0]
        assert _state(coord, TODAY_A) is None and _state(coord, TODAY_B) is None
        assert TODAY_A not in pushes  # the derived base was never handed over

    def test_a_failing_store_of_the_signature_is_a_failing_step(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coord = _coordinator()
        _sensors(coord)
        coord._hass.config_entries.fail = True
        with caplog.at_level("WARNING"):
            assert _run(coord) is False
        assert coord._entry.data == {} and "WARNING" in {r.levelname for r in caplog.records}

    def test_the_lock_is_free_again_and_the_slot_jobs_keep_working(self) -> None:
        coord = _coordinator()
        _sensors(coord, a=0.0, b=0.0, today=0.0, total=0.0)
        _delta_fail_at[0] = "recalculate"

        async def scenario() -> None:
            assert await coord.async_run_formula_recalculation(SIGNATURE) is False
            assert coord._recorder_job_lock().locked() is False
            _delta_fail_at[0] = None
            _deltas_return[0] = {PV_A: (5.0, "Wh")}
            await coord._async_recalculate_recent_and_report()

        asyncio.run(scenario())
        assert _state(coord, TODAY_A) == pytest.approx(5.0)

    def test_the_next_attempt_starts_from_scratch_and_can_succeed(self) -> None:
        coord = _coordinator(stored={PV_KEY: [PV_A]})
        _sensors(coord, a=1.0, b=1.0, today=1.0, total=1.0)
        _delta_fail_at[0] = "derive"
        assert _run(coord) is False
        _delta_fail_at[0] = None
        _delta_flow.clear()
        assert _run(coord) is True
        assert (
            _delta_flow.count("recalculate") == 1 and coord._entry.data[SIGNATURE_KEY] == SIGNATURE
        )

    def test_a_cancelled_run_is_not_a_failure_and_stores_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The entry is unloaded while the run is under way: the task is cancelled."""
        coord = _coordinator()
        _sensors(coord)

        async def forever() -> None:
            await asyncio.sleep(3600)

        _delta_pause[0] = forever

        async def scenario() -> None:
            task = asyncio.ensure_future(coord.async_run_formula_recalculation(SIGNATURE))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert _delta_flow == ["read"]  # it is waiting inside the first step
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert coord._recorder_job_lock().locked() is False

        with caplog.at_level("WARNING"):
            asyncio.run(scenario())
        assert coord._hass.config_entries.updates == [] and coord._entry.data == {}
        assert [r for r in caplog.records if r.levelname == "WARNING"] == []


class TestTheButtonAfterwards:
    def test_the_button_books_its_deltas_once_the_bases_exist(self) -> None:
        coord = _coordinator()
        _sensors(coord, a=0.0, b=0.0, today=0.0, total=0.0)
        _deltas_return[0] = {PV_A: (100.0, "Wh")}
        asyncio.run(coord.async_recalculate_history_and_report())
        assert _state(coord, TODAY_A) == pytest.approx(100.0)
        assert _delta_skip_seen == [("history", frozenset())]

    def test_the_button_runs_no_derivation_and_no_rewrite(self) -> None:
        coord = _coordinator()
        _sensors(coord, a=0.0, b=0.0, today=0.0, total=0.0)
        asyncio.run(coord.async_recalculate_history_and_report())
        assert _delta_flow == ["recalculate"]


_ = _coord_mod  # the loaded module, kept for the fakes' sake
