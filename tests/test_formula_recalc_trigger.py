"""
When the entry runs the full recalculation by itself (TASK-0045e, CAP-13, ADR-027 decision 6).

``__init__.py`` is the module ``test_migration`` loads (Home Assistant and the coordinator
stubbed). At setup the formula signature of the options is compared with the one stored in the
entry data: if it differs, or none was stored, the recalculation is started as a background task
of the entry, after the platforms are set up (their sensors have restored their states by then).
Everything else in the options changes nothing here: it reloads the entry and runs nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from tests.test_migration import (
    _BACKGROUND,
    _CALLS,
    _FakeEntry,
    _FakeHass,
    _const,
    _init,
    _refused,
)

PV_A, PV_B = "sensor.pv_a", "sensor.pv_b"


def _options(**changes: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        _const.CONF_PV_INPUT_SENSORS: [PV_A, PV_B],
        _const.CONF_GRID_IMPORT_SENSOR: "sensor.grid_in",
        _const.CONF_DISCHARGE_SENSOR: "sensor.discharge",
        _const.CONF_CHARGE_SENSOR: "sensor.charge",
        _const.CONF_GRID_EXPORT_SENSOR: "sensor.grid_out",
        _const.CONF_MAX_HISTORY_DAYS: 30,
        _const.CONF_SOC_SENSOR: "sensor.soc",
    }
    options.update(changes)
    return options


def _setup(options: dict[str, Any], stored: Any = "absent") -> tuple[_FakeEntry, bool]:
    _CALLS.clear()
    _BACKGROUND.clear()
    entry = _FakeEntry(options)
    if stored != "absent":
        entry.data = {_const.CONF_FORMULA_SIGNATURE: stored}
    ok = asyncio.run(_init.async_setup_entry(_FakeHass(), entry))
    return entry, ok


def _signature(**changes: Any) -> dict[str, Any]:
    signature: dict[str, Any] = _init.formula_signature(_options(**changes))
    return signature


class TestStartsWhenTheSignatureDiffers:
    def test_an_entry_without_a_stored_signature_starts_the_recalculation(self) -> None:
        _entry, ok = _setup(_options())
        assert ok is True
        assert _BACKGROUND == [("effy_formula_recalculation_entry-1", _signature())]

    def test_a_different_stored_signature_starts_it(self) -> None:
        _setup(_options(), stored=_signature(**{_const.CONF_PV_INPUT_SENSORS: [PV_A]}))
        assert len(_BACKGROUND) == 1

    def test_a_change_of_a_counter_starts_it(self) -> None:
        _setup(_options(), stored=_signature(**{_const.CONF_GRID_IMPORT_SENSOR: "sensor.other"}))
        assert len(_BACKGROUND) == 1

    def test_a_stored_value_that_is_not_a_signature_starts_it(self) -> None:
        _setup(_options(), stored="garbage")
        assert len(_BACKGROUND) == 1

    def test_the_task_is_made_for_the_signature_of_the_current_options(self) -> None:
        _setup(_options(**{_const.CONF_PV_INPUT_SENSORS: [PV_B]}), stored=_signature())
        (_name, signature) = _BACKGROUND[0]
        assert signature == _signature(**{_const.CONF_PV_INPUT_SENSORS: [PV_B]})

    def test_it_starts_after_the_platforms_are_set_up(self) -> None:
        """The sensors have restored their states by then: the base is handed to what exists."""
        _setup(_options())
        assert _CALLS.index("async_forward_entry_setups") < _CALLS.index(
            "async_create_background_task"
        )
        assert _CALLS.index("coordinator.async_setup") < _CALLS.index(
            "async_create_background_task"
        )

    def test_an_entry_without_a_pv_input_starts_it_too(self) -> None:
        options = _options()
        del options[_const.CONF_PV_INPUT_SENSORS]
        _setup(options)
        assert len(_BACKGROUND) == 1


class TestDoesNotStart:
    def test_the_same_signature_stored_starts_nothing(self) -> None:
        _setup(_options(), stored=_signature())
        assert _BACKGROUND == []
        assert "async_create_background_task" not in _CALLS

    def test_the_order_of_the_pv_inputs_does_not_matter(self) -> None:
        _setup(_options(), stored=_signature(**{_const.CONF_PV_INPUT_SENSORS: [PV_B, PV_A]}))
        assert _BACKGROUND == []

    def test_the_signature_after_a_json_round_trip_is_the_same(self) -> None:
        import json

        _setup(_options(), stored=json.loads(json.dumps(_signature())))
        assert _BACKGROUND == []

    def test_other_options_start_nothing(self) -> None:
        for change in (
            {_const.CONF_MAX_HISTORY_DAYS: 90},
            {_const.CONF_SOC_SENSOR: "sensor.other_soc"},
            {_const.CONF_VALLEY_HYSTERESIS: 45},
            {_const.CONF_DEBUG_EXPORT: True},
        ):
            _setup(_options(**change), stored=_signature())
            assert _BACKGROUND == [], change

    def test_an_entry_that_refuses_to_set_up_starts_nothing(self) -> None:
        options = _options()
        del options[_const.CONF_CHARGE_SENSOR]
        del options[_const.CONF_GRID_EXPORT_SENSOR]
        _refused(options)
        assert _BACKGROUND == []


class TestNothingElseChanges:
    def test_no_update_listener_is_registered_and_the_options_are_not_written(self) -> None:
        _setup(_options())
        assert "add_update_listener" not in _CALLS
        assert "async_update_entry" not in _CALLS  # storing the signature is the coordinator's
