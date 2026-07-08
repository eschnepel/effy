"""
Unit tests for sensor_utils.py.

Covers:
  - effective_unit_for: the Wh->W / kWh->kW mapping used to correctly label
    an "effective" (post-loss-distribution) reading.
  - to_power_equivalent: Wh/kWh -> W-equivalent conversion, and that its
    returned unit stays consistent with effective_unit_for.

Regression coverage for a unit-mislabeling bug found while investigating a
report of BMS (battery) effective values reading consistently 0: an
energy-family (Wh/kWh) source's *effective* value is always a W/kW-equivalent
power reading (to_power_equivalent converts before distribute_loss ever
runs), never a genuine Wh/kWh energy figure — labeling it with the source's
raw unit was a category error (energy vs. power). As shown below, this is a
pure label mismatch (the underlying number is unaffected, since _from_w
treats Wh/W and kWh/kW identically) — it explains misleading units on
BMS effective sensors, but is not by itself sufficient to explain a value
reading exactly 0; see the conversation for the other candidates considered
for that (statistics-metadata unit mismatch; the waterfall algorithm
legitimately assigning 0 to small inputs). Both sensor.py (live) and
history.py (recalc write path) must use effective_unit_for, not the
source's raw unit, when reporting an effective value for an energy-family
sensor.

Uses the same importlib-by-file-path pattern as test_coordinator_slot.py
(ADR-000 §6). sensor_utils.py only needs homeassistant.core stubbed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from effy.sensor_utils import (
        effective_unit_for as effective_unit_for,
    )
    from effy.sensor_utils import (
        to_power_equivalent as to_power_equivalent,
    )

# ---------------------------------------------------------------------------
# 1. HA stubs
# ---------------------------------------------------------------------------


def _stub(name: str, **attrs: Any) -> ModuleType:
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object)

# ---------------------------------------------------------------------------
# 2. Load effy modules by file path
# ---------------------------------------------------------------------------

_base = Path(__file__).resolve().parent.parent / "custom_components" / "effy"

_effy_pkg = ModuleType("effy")
_effy_pkg.__path__ = [str(_base)]
_effy_pkg.__package__ = "effy"
sys.modules["effy"] = _effy_pkg


def _load(reg_name: str, filename: str) -> ModuleType:
    path = _base / filename
    spec = importlib.util.spec_from_file_location(reg_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "effy"
    sys.modules[reg_name] = mod
    setattr(_effy_pkg, filename.replace(".py", ""), mod)
    spec.loader.exec_module(mod)
    return mod


_calc_mod = _load("effy.calculation", "calculation.py")
_su_mod = _load("effy.sensor_utils", "sensor_utils.py")

if not TYPE_CHECKING:
    effective_unit_for = _su_mod.effective_unit_for
    to_power_equivalent = _su_mod.to_power_equivalent

_from_w = _calc_mod._from_w
_to_w = _calc_mod._to_w


# ---------------------------------------------------------------------------
# effective_unit_for
# ---------------------------------------------------------------------------


class TestEffectiveUnitFor:
    def test_wh_becomes_w(self) -> None:
        assert effective_unit_for("Wh") == "W"

    def test_kwh_becomes_kw(self) -> None:
        assert effective_unit_for("kWh") == "kW"

    def test_w_passes_through(self) -> None:
        assert effective_unit_for("W") == "W"

    def test_kw_passes_through(self) -> None:
        assert effective_unit_for("kW") == "kW"

    def test_never_returns_an_energy_unit(self) -> None:
        """The whole point of this function: it must never hand back Wh/kWh,
        since that's what caused effective values to be mislabeled as
        energy when they were actually power (see module docstring)."""
        for unit in ("Wh", "kWh", "W", "kW"):
            assert effective_unit_for(unit) not in ("Wh", "kWh")


# ---------------------------------------------------------------------------
# to_power_equivalent
# ---------------------------------------------------------------------------


class TestToPowerEquivalent:
    def test_wh_over_5min_slot(self) -> None:
        # 5 Wh over a 5-minute slot -> 5 * 12 = 60 W
        value, unit = to_power_equivalent(5.0, "Wh")
        assert value == pytest.approx(60.0)
        assert unit == "W"

    def test_kwh_over_5min_slot(self) -> None:
        value, unit = to_power_equivalent(0.005, "kWh")
        assert value == pytest.approx(0.06)
        assert unit == "kW"

    def test_w_passes_through_unchanged(self) -> None:
        value, unit = to_power_equivalent(153.0, "W")
        assert value == pytest.approx(153.0)
        assert unit == "W"

    def test_kw_passes_through_unchanged(self) -> None:
        value, unit = to_power_equivalent(1.53, "kW")
        assert value == pytest.approx(1.53)
        assert unit == "kW"

    def test_custom_slot_duration(self) -> None:
        # 10 Wh over a 1-minute slot -> 10 * 60 = 600 W
        value, unit = to_power_equivalent(10.0, "Wh", slot_minutes=1)
        assert value == pytest.approx(600.0)
        assert unit == "W"

    def test_returned_unit_always_matches_effective_unit_for(self) -> None:
        """to_power_equivalent's returned unit must stay in lockstep with
        effective_unit_for, since callers (history.py's write path) use
        effective_unit_for independently to label a value that was itself
        produced via this function."""
        for unit in ("Wh", "kWh", "W", "kW"):
            _value, returned_unit = to_power_equivalent(1.0, unit)
            assert returned_unit == effective_unit_for(unit)


# ---------------------------------------------------------------------------
# Round-trip regression: an energy-family reading, once inside
# distribute_loss's W-normalised world, must convert back out using
# effective_unit_for — not the original Wh/kWh unit — or the numeric result
# is silently mislabeled (a category error, not merely a scale error).
# ---------------------------------------------------------------------------


class TestEffectiveValueRoundTrip:
    def test_energy_source_round_trips_through_effective_unit_for(self) -> None:
        raw_wh = 50.0
        w_equiv, converted_unit = to_power_equivalent(raw_wh, "Wh")
        assert w_equiv == pytest.approx(600.0)  # 50 Wh / 5min-slot

        # distribute_loss normalises with _to_w using the *converted* unit...
        normalised_w = _to_w(w_equiv, converted_unit)
        assert normalised_w == pytest.approx(600.0)  # already W, no-op

        # ...and effective_in_original_unit must convert back out using the
        # SAME unit family (W), not the original "Wh" — using "Wh" here
        # would silently divide/multiply by the wrong factor family (a
        # kilo-prefix conversion applied to a per-hour quantity), which is
        # exactly the bug this test guards against.
        assert effective_unit_for("Wh") == converted_unit
        round_tripped = _from_w(normalised_w, effective_unit_for("Wh"))
        assert round_tripped == pytest.approx(600.0)

        # Using the raw "Wh" unit instead (the pre-fix behaviour) does NOT
        # raise or obviously break — it just silently returns the wrong
        # *kind* of number (still 600, since _from_w treats "Wh" as a
        # pass-through, same as "W" — the mislabeling is invisible at the
        # "Wh" end but very visible for "kWh", see the next test).
        wrongly_labeled = _from_w(normalised_w, "Wh")
        assert wrongly_labeled == pytest.approx(600.0)

    def test_kwh_source_mislabel_is_a_pure_label_bug_not_a_magnitude_bug(self) -> None:
        """The concrete failure mode for a kWh-unit source (e.g. a BMS
        battery sensor): ``_from_w`` treats "kWh" exactly like "kW" (both
        divide by 1000), so using the raw "kWh" unit instead of
        effective_unit_for("kWh") == "kW" produces the *same number* — it
        only mislabels it. That number is genuinely a kW (power) reading
        displayed under a "kWh" (energy) label, which is misleading and
        breaks anything downstream that expects real energy accumulation
        (e.g. a utility_meter), but it is NOT the source of a 1000x-too-
        small magnitude error by itself — see the module docstring for why
        this alone does not explain "value reads consistently 0".
        """
        raw_kwh = 0.05
        w_equiv, converted_unit = to_power_equivalent(raw_kwh, "kWh")
        assert converted_unit == "kW"
        normalised_w = _to_w(w_equiv, converted_unit)

        correct = _from_w(normalised_w, effective_unit_for("kWh"))
        buggy = _from_w(normalised_w, "kWh")  # pre-fix: raw source unit

        assert correct == pytest.approx(buggy)  # same number...
        assert effective_unit_for("kWh") != "kWh"  # ...under a different label
