"""
The recorder-facing helpers of history.py, run for real (TASK-0028, slices 5a/5b).

``test_history_recent_equivalence`` replaces every one of these helpers with an
in-memory fake, so their own bodies were never executed. Here the real bodies
run against stubs of the Home Assistant APIs they call. The recorder functions
are imported inside the helpers (history.py defers them), so a stub registered
in ``sys.modules`` for the duration of a test is what the helper sees.

What the stubs return is the shape the helper relies on; whether a real Home
Assistant returns exactly that is not shown here (class D in
``tasks/coverage-report.md``).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import Enum
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.test_history_recent_equivalence import _history, _load_history, _ts

UTC_LOGGER = _history._LOGGER.name


def _hass(states: dict[str, Any]) -> Any:
    """A ``hass`` whose ``states.get`` answers from ``states``."""
    return SimpleNamespace(states=SimpleNamespace(get=states.get))


def _state(**attributes: Any) -> SimpleNamespace:
    return SimpleNamespace(state="1.0", attributes=attributes)


def _register(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: Any) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


# ---------------------------------------------------------------------------
# metadata of the statistics Effy writes (newer and older cores)
# ---------------------------------------------------------------------------


class _MeanType(Enum):
    ARITHMETIC = "arithmetic"


class TestStatisticMetadata:
    def test_an_older_core_gets_the_boolean_flags_only(self) -> None:
        meta = _history._build_statistic_metadata("sensor.effy_pv", "W")
        assert meta == {
            "has_mean": True,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": "sensor.effy_pv",
            "unit_of_measurement": "W",
        }

    def test_a_newer_core_also_gets_mean_type_and_no_unit_class(self) -> None:
        """A core that ships ``StatisticMeanType`` requires ``mean_type`` and an explicit
        ``unit_class``; Effy asks the recorder for no unit conversion (ADR-002), so the
        class is None. The older flags stay for the older cores."""
        newer, _const, _exports = _load_history(mean_type=_MeanType)
        assert newer._HAS_STATISTIC_MEAN_TYPE is True
        meta = newer._build_statistic_metadata("sensor.effy_pv", "kW")
        assert meta["mean_type"] is _MeanType.ARITHMETIC
        assert meta["unit_class"] is None
        assert meta["has_mean"] is True and meta["has_sum"] is False
        assert meta["unit_of_measurement"] == "kW"


# ---------------------------------------------------------------------------
# what the live entity says about a source sensor
# ---------------------------------------------------------------------------


class TestLiveSourceLookups:
    def test_state_class_and_unit_come_from_the_live_state(self) -> None:
        hass = _hass({"sensor.pv": _state(state_class="measurement", unit_of_measurement="kW")})
        assert _history._get_state_class(hass, "sensor.pv") == "measurement"
        assert _history._get_unit(hass, "sensor.pv") == "kW"

    def test_a_source_without_a_state_has_no_state_class_and_counts_as_watt(self) -> None:
        """A removed or not yet reported source (ADR-013 Decision 1): the series
        is interpreted as plain power, the same default ``sensor_utils`` uses."""
        hass = _hass({})
        assert _history._get_state_class(hass, "sensor.gone") is None
        assert _history._get_unit(hass, "sensor.gone") == "W"

    def test_a_state_without_attributes_gives_none_and_watt(self) -> None:
        hass = _hass({"sensor.bare": _state()})
        assert _history._get_state_class(hass, "sensor.bare") is None
        assert _history._get_unit(hass, "sensor.bare") == "W"


# ---------------------------------------------------------------------------
# the recorder's configured retention
# ---------------------------------------------------------------------------


class TestShortTermRetentionDays:
    def test_the_configured_keep_days_are_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_history, "get_recorder", lambda _h: SimpleNamespace(keep_days=14))
        assert _history._get_short_term_retention_days(object()) == 14

    @pytest.mark.parametrize("keep_days", [None, 0, -3, "10", 2.5])
    def test_an_unreadable_value_falls_back_and_is_logged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        keep_days: Any,
    ) -> None:
        monkeypatch.setattr(
            _history, "get_recorder", lambda _h: SimpleNamespace(keep_days=keep_days)
        )
        with caplog.at_level(logging.WARNING, logger=UTC_LOGGER):
            days = _history._get_short_term_retention_days(object())
        assert days == _history._FALLBACK_SHORT_TERM_RETENTION_DAYS == 10
        assert "could not read recorder.keep_days" in caplog.text

    def test_a_recorder_without_keep_days_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(_history, "get_recorder", lambda _h: SimpleNamespace())
        with caplog.at_level(logging.WARNING, logger=UTC_LOGGER):
            assert _history._get_short_term_retention_days(object()) == 10
        assert "falling back to 10 days" in caplog.text


# ---------------------------------------------------------------------------
# the unit the compiled statistics are stored in
# ---------------------------------------------------------------------------


def _stub_get_metadata(monkeypatch: pytest.MonkeyPatch, fn: Callable[..., Any]) -> list[Any]:
    calls: list[Any] = []

    def get_metadata(hass: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return fn(hass, **kwargs)

    _register(
        monkeypatch, "homeassistant.components.recorder.statistics", get_metadata=get_metadata
    )
    return calls


class TestStatisticsUnits:
    def test_the_stored_unit_of_every_statistic_is_returned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _stub_get_metadata(
            monkeypatch,
            lambda _h, **_k: {
                "sensor.a": (1, {"unit_of_measurement": "Wh"}),
                "sensor.b": (2, {"unit_of_measurement": "kW"}),
            },
        )
        units = _history._get_statistics_units(object(), ["sensor.a", "sensor.b"])
        assert units == {"sensor.a": "Wh", "sensor.b": "kW"}
        assert calls == [{"statistic_ids": {"sensor.a", "sensor.b"}}]

    def test_a_statistic_without_a_unit_is_left_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The caller then uses the live unit for it (``stats_units.get(eid) or live``)."""
        _stub_get_metadata(
            monkeypatch,
            lambda _h, **_k: {
                "sensor.none": (1, {"unit_of_measurement": None}),
                "sensor.empty": (2, {"unit_of_measurement": ""}),
                "sensor.missing": (3, {}),
                "sensor.ok": (4, {"unit_of_measurement": "W"}),
            },
        )
        assert _history._get_statistics_units(object(), []) == {"sensor.ok": "W"}

    @pytest.mark.parametrize(
        "bad_entry",
        [None, 5, (1,), (1, 2, 3), (1, "not a mapping")],
        ids=["none", "int", "short", "long", "no-get"],
    )
    def test_an_entry_of_an_unexpected_shape_is_skipped_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch, bad_entry: Any
    ) -> None:
        _stub_get_metadata(
            monkeypatch,
            lambda _h, **_k: {
                "sensor.bad": bad_entry,
                "sensor.ok": (4, {"unit_of_measurement": "kWh"}),
            },
        )
        assert _history._get_statistics_units(object(), []) == {"sensor.ok": "kWh"}

    def test_a_failing_metadata_lookup_logs_and_returns_nothing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Docstring: the live units are used instead; the recalculation goes on."""

        def boom(_h: Any, **_k: Any) -> Any:
            raise RuntimeError("recorder gone")

        _stub_get_metadata(monkeypatch, boom)
        with caplog.at_level(logging.ERROR, logger=UTC_LOGGER):
            assert _history._get_statistics_units(object(), ["sensor.a"]) == {}
        assert "could not read statistics metadata" in caplog.text
        assert "recorder gone" in caplog.text  # the traceback is logged, not raised


# ---------------------------------------------------------------------------
# one slot's readings
# ---------------------------------------------------------------------------


class TestReadingsForSlot:
    SLOT = _ts(10, 0)

    def _readings(self, rows: dict[str, dict[datetime, dict[str, Any]]]) -> list[Any]:
        readings: list[Any] = _history._readings_for_slot(
            self.SLOT,
            5,
            ["sensor.pv", "sensor.grid"],
            rows,
            {"sensor.pv": "measurement", "sensor.grid": "measurement"},
            {"sensor.pv": "W", "sensor.grid": "W"},
        )
        return readings

    def test_a_row_whose_field_is_empty_gives_no_reading(self) -> None:
        """The recorder compiled the slot but has no mean for it: the sensor is
        absent from the slot, so it takes no loss share (not a reading of 0)."""
        rows: dict[str, dict[datetime, dict[str, Any]]] = {
            "sensor.pv": {self.SLOT: {"mean": None}},
            "sensor.grid": {self.SLOT: {"mean": 120.0}},
        }
        readings = self._readings(rows)
        assert [(r.entity_id, r.raw_value) for r in readings] == [("sensor.grid", 120.0)]

    def test_a_row_with_a_value_gives_a_reading_and_a_missing_row_none(self) -> None:
        rows: dict[str, dict[datetime, dict[str, Any]]] = {"sensor.pv": {self.SLOT: {"mean": 0.0}}}
        readings = self._readings(rows)
        assert [(r.entity_id, r.raw_value) for r in readings] == [("sensor.pv", 0.0)]


# ---------------------------------------------------------------------------
# raw states from the recorder
# ---------------------------------------------------------------------------


def _states(*entries: tuple[datetime, str]) -> list[SimpleNamespace]:
    return [SimpleNamespace(last_changed=t, state=s) for t, s in entries]


def _stub_history(monkeypatch: pytest.MonkeyPatch, fn: Callable[..., Any]) -> list[Any]:
    """Register ``state_changes_during_period``; return its call log."""
    calls: list[Any] = []

    def state_changes_during_period(
        hass: Any, start: Any, end: Any, entity_id: Any, **kw: Any
    ) -> Any:
        calls.append((start, end, entity_id, kw))
        return fn(start, end, entity_id, **kw)

    _register(
        monkeypatch,
        "homeassistant.components.recorder.history",
        state_changes_during_period=state_changes_during_period,
    )
    return calls


class TestFetchRawEnergyStates:
    START, END = _ts(10, 0), _ts(11, 0)

    def test_every_state_is_returned_in_order_including_the_invalid_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``trapezoidal_slot_contributions`` finds offline gaps from the
        ``unavailable`` entries, so none may be dropped (docstring, ADR-012)."""
        entries = (
            (_ts(10, 0), "5.00"),
            (_ts(10, 20), "unavailable"),
            (_ts(10, 40), "5.02"),
        )
        calls = _stub_history(monkeypatch, lambda *_a, **_k: {"sensor.e": _states(*entries)})

        result = _history._fetch_raw_energy_states(object(), "sensor.e", self.START, self.END)

        assert result == list(entries)
        ((start, end, entity_id, kwargs),) = calls
        assert (start, end, entity_id) == (self.START, self.END, "sensor.e")
        assert kwargs == {"no_attributes": True}

    def test_an_entity_the_recorder_has_no_states_for_gives_an_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_history(monkeypatch, lambda *_a, **_k: {})
        assert _history._fetch_raw_energy_states(object(), "sensor.e", self.START, self.END) == []


class TestFetchCapacityStates:
    """The capacity entity's raw states with their units (TASK-0025, ADR-023 decision 6)."""

    START, END = _ts(10, 0), _ts(11, 0)

    @staticmethod
    def _with_units(*entries: tuple[datetime, str, dict[str, Any]]) -> list[SimpleNamespace]:
        return [SimpleNamespace(last_changed=t, state=s, attributes=a) for t, s, a in entries]

    def test_every_state_comes_back_in_order_with_the_unit_of_that_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An ``unavailable`` state stays (a capacity that is not known is not a capacity) and
        the unit is the one the state had when it was recorded, so a changed unit is seen."""
        states = self._with_units(
            (_ts(10, 0), "10000", {"unit_of_measurement": "Wh", "friendly_name": "Cap"}),
            (_ts(10, 20), "unavailable", {}),
            (_ts(10, 40), "10", {"unit_of_measurement": "kWh"}),
        )
        calls = _stub_history(monkeypatch, lambda *_a, **_k: {"number.cap": states})

        result = _history._fetch_capacity_states(object(), "number.cap", self.START, self.END)

        assert result == [
            (_ts(10, 0), "10000", "Wh"),
            (_ts(10, 20), "unavailable", None),
            (_ts(10, 40), "10", "kWh"),
        ]
        ((start, end, entity_id, kwargs),) = calls
        assert (start, end, entity_id) == (self.START, self.END, "number.cap")
        assert kwargs == {"no_attributes": False}  # the unit lives in the attributes

    def test_an_entity_the_recorder_has_no_states_for_gives_an_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_history(monkeypatch, lambda *_a, **_k: {})
        assert _history._fetch_capacity_states(object(), "number.cap", self.START, self.END) == []


class TestFetchLastValidStateBefore:
    """The staged lookback for an offline anchor (ADR-013, ADR-014, ADR-015)."""

    BEFORE = _ts(12, 0, day=10)

    def _world(self, readings: list[tuple[datetime, str]]) -> Callable[..., Any]:
        """A recorder that answers a descending query from ``readings``."""

        def answer(start: datetime, end: datetime, entity_id: str, **kw: Any) -> Any:
            assert kw["descending"] is True and kw["include_start_time_state"] is False
            inside = [r for r in readings if start <= r[0] < end]
            inside.sort(key=lambda r: r[0], reverse=True)
            return {entity_id: _states(*inside)}

        return answer

    def test_the_first_stage_that_has_a_numeric_reading_answers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readings = [
            (self.BEFORE - timedelta(minutes=20), "7.10"),
            (self.BEFORE - timedelta(minutes=5), "unavailable"),
        ]
        calls = _stub_history(monkeypatch, self._world(readings))

        found = _history._fetch_last_valid_state_before(object(), "sensor.e", self.BEFORE, 5)

        assert found == (self.BEFORE - timedelta(minutes=20), "7.10")  # skips the newer invalid one
        assert len(calls) == 1  # no wider query was needed
        start, end, _eid, kwargs = calls[0]
        assert (start, end) == (self.BEFORE - timedelta(minutes=30), self.BEFORE)
        assert kwargs["limit"] == _history._OFFLINE_ANCHOR_QUERY_LIMIT

    def test_an_empty_first_stage_escalates_to_one_day(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readings = [(self.BEFORE - timedelta(hours=6), "3.00")]
        calls = _stub_history(monkeypatch, self._world(readings))

        found = _history._fetch_last_valid_state_before(object(), "sensor.e", self.BEFORE, 5)

        assert found == (self.BEFORE - timedelta(hours=6), "3.00")
        assert [c[0] for c in calls] == [
            self.BEFORE - timedelta(minutes=30),
            self.BEFORE - timedelta(days=1),
        ]

    def test_a_stage_with_only_invalid_states_does_not_stop_the_search(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readings = [
            (self.BEFORE - timedelta(minutes=10), "unknown"),
            (self.BEFORE - timedelta(hours=3), "unavailable"),
            (self.BEFORE - timedelta(days=2), "9.99"),
        ]
        calls = _stub_history(monkeypatch, self._world(readings))

        found = _history._fetch_last_valid_state_before(object(), "sensor.e", self.BEFORE, 5)

        assert found == (self.BEFORE - timedelta(days=2), "9.99")
        assert len(calls) == 3  # 30 minutes, 1 day, then the configured 5 days

    def test_nothing_in_the_whole_history_gives_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _stub_history(monkeypatch, self._world([]))
        assert _history._fetch_last_valid_state_before(object(), "sensor.e", self.BEFORE, 5) is None
        assert [self.BEFORE - c[0] for c in calls] == [
            timedelta(minutes=30),
            timedelta(days=1),
            timedelta(days=5),
        ]

    @pytest.mark.parametrize("max_history_days", [1, 0])
    def test_a_history_no_longer_than_a_stage_already_tried_is_not_searched_again(
        self, monkeypatch: pytest.MonkeyPatch, max_history_days: int
    ) -> None:
        """Bounded recorder cost (ADR-014): the configured days only add a query
        when they reach further back than the fixed stages did."""
        calls = _stub_history(monkeypatch, self._world([]))
        found = _history._fetch_last_valid_state_before(
            object(), "sensor.e", self.BEFORE, max_history_days
        )
        assert found is None
        assert [self.BEFORE - c[0] for c in calls] == [timedelta(minutes=30), timedelta(days=1)]

    def test_an_entity_the_recorder_does_not_know_is_searched_through_every_stage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _stub_history(monkeypatch, lambda *_a, **_k: {})
        assert _history._fetch_last_valid_state_before(object(), "sensor.e", self.BEFORE, 3) is None
        assert len(calls) == 3


# ---------------------------------------------------------------------------
# slice 5f: reading and writing statistics
# ---------------------------------------------------------------------------


class TestFetchStatistics:
    START, END = _ts(10, 0), _ts(11, 0)

    def _stub(
        self, monkeypatch: pytest.MonkeyPatch, result: dict[str, list[dict[str, Any]]]
    ) -> list[Any]:
        calls: list[Any] = []

        def statistics_during_period(hass: Any, start: Any, end: Any, **kwargs: Any) -> Any:
            calls.append((start, end, kwargs))
            return result

        _register(
            monkeypatch,
            "homeassistant.components.recorder.statistics",
            statistics_during_period=statistics_during_period,
        )
        return calls

    def test_the_five_minute_mean_and_change_of_the_given_statistics_are_asked_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = {"sensor.a": [{"start": self.START, "mean": 1.0}]}
        calls = self._stub(monkeypatch, rows)

        result = _history._fetch_statistics(object(), ["sensor.a"], self.START, self.END)

        assert result == rows
        assert calls == [
            (
                self.START,
                self.END,
                {
                    "statistic_ids": ["sensor.a"],
                    "period": "5minute",
                    "units": None,  # the unit the statistics are stored in (ADR-002)
                    "types": {"mean", "change"},
                },
            )
        ]

    def test_a_start_given_as_a_unix_timestamp_becomes_a_utc_datetime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Some cores return ``start`` as a float; everything downstream needs a
        tz-aware datetime (comment in ``_fetch_statistics``)."""
        first = _ts(10, 5)
        rows: dict[str, list[dict[str, Any]]] = {
            "sensor.a": [
                {"start": first.timestamp(), "mean": 1.0},
                {"start": int(_ts(10, 10).timestamp()), "mean": 2.0},
                {"start": _ts(10, 15), "mean": 3.0},  # already a datetime: left alone
            ],
            "sensor.b": [],
        }
        self._stub(monkeypatch, rows)

        result = _history._fetch_statistics(
            object(), ["sensor.a", "sensor.b"], self.START, self.END
        )

        starts = [row["start"] for row in result["sensor.a"]]
        assert starts == [_ts(10, 5), _ts(10, 10), _ts(10, 15)]
        assert all(s.tzinfo is not None and s.utcoffset() == timedelta(0) for s in starts)
        assert result["sensor.b"] == []
        assert [row["mean"] for row in result["sensor.a"]] == [1.0, 2.0, 3.0]

    def test_an_answer_without_any_statistic_stays_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub(monkeypatch, {})
        assert _history._fetch_statistics(object(), ["sensor.a"], self.START, self.END) == {}


class _Recorder:
    def __init__(self) -> None:
        self.tables: list[Any] = []
        self.rows: list[list[dict[str, Any]]] = []

    def async_import_statistics(self, _metadata: Any, stats: Any, table: Any) -> None:
        self.tables.append(table)
        self.rows.append(stats)

    async def async_block_till_done(self) -> None:
        return None


class TestWriteWithoutLongTerm:
    SERIES = {
        "sensor.effy_x": {"unit": "kW", "slot_values": [(_ts(11, 50), 1.0), (_ts(11, 55), 2.0)]}
    }

    async def test_the_slot_path_writes_the_short_term_table_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-011 D2: the slot timer never writes hourly statistics; a single hour
        row from a few slots would replace the correct mean of the whole hour."""
        recorder = _Recorder()
        monkeypatch.setattr(_history, "get_recorder", lambda _h: recorder)

        written = await _history._write_recorder_statistics(
            object(), self.SERIES, include_long_term=False
        )

        assert written == 2
        assert recorder.tables == [_history.StatisticsShortTerm]
        assert [row["start"] for row in recorder.rows[0]] == [_ts(11, 50), _ts(11, 55)]

    async def test_neither_table_is_written_when_both_are_left_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(_history, "get_recorder", lambda _h: recorder)
        written = await _history._write_recorder_statistics(
            object(), self.SERIES, include_long_term=False, include_short_term=False
        )
        assert written == 0
        assert recorder.tables == []
