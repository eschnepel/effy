"""The live state of the delta integrals (ADR-027 decisions 3 to 5, TASK-0045d; ADR-029, TASK-0050).

Pure logic, no Home Assistant imports (ADR-000 §3). The coordinator owns one
``DeltaIntegrals``; the sensors read its entries and the coordinator pushes the changes.

There are three kinds of integral, all booked the same way. The *per-input* one of a PV input
and the two *combined* ones (the addition over the PV inputs) are the PV case of ADR-027. Since
ADR-029 a *series* integral is a *today* or a *total* integral of one series with no inputs
under it (the household consumption power series): its deltas arrive under the key of the
series and land in its own integrals only. The mechanism is the same for all of them; what a
delta belongs to is the only thing that differs.

What it keeps, per integral: the state (``None`` while the sensor is *unavailable*, that is
until it has a base), the ``last_reset`` of a *today* integral, and the unit. What it does:

- ``restore``: a sensor that restarts hands over what it restored. A *today* integral whose
  ``last_reset`` is not of the current local day is reset (state 0, ``last_reset`` the local
  midnight of today), the way the midnight timer would have.
- ``book``: the deltas of one run (TASK-0045b; for a series, TASK-0050) are added to the
  states. Attribution is by the time of the write: whatever slot a row belongs to, the delta
  lands in the live state, with any sign and without a clamp. An integral that is unavailable
  books nothing, and neither does the combined one for an input that has no base.
- ``reset_today``: at local midnight the *today* integrals go to 0 with the midnight as their
  ``last_reset``. The *total* is never reset.
- ``hand_over``: the base the derivation of TASK-0045c wrote becomes the state, exactly.

Statistics belong to Home Assistant (decision 4): nothing here writes one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Literal, Protocol

from .calculation import convert_unit, local_midnight

Kind = Literal["today", "total"]
TODAY: Kind = "today"
TOTAL: Kind = "total"

# The unit the combined integrals are in, and the one the deltas are added up in.
COMBINED_UNIT = "kWh"


class Base(Protocol):
    """What the derivation hands over per series (``history.DeltaBase``)."""

    @property
    def state(self) -> float: ...

    @property
    def last_reset(self) -> datetime | None: ...


@dataclass
class DeltaIntegral:
    """One delta integral: a per-input *today* one, the combined *today* or *total* one, or the
    *today* or *total* one of a series.

    ``source`` is the PV input of a per-input integral, ``None`` for the others. ``series`` is
    the key of the series a series integral belongs to (the statistic ID of the series), ``None``
    for the PV ones; an integral with neither is a combined one.
    ``state`` is ``None`` while there is no base: the sensor is then unavailable.
    """

    entity_id: str
    kind: Kind
    source: str | None
    unit: str
    state: float | None = None
    last_reset: datetime | None = None
    series: str | None = None

    @property
    def available(self) -> bool:
        return self.state is not None


def _of_the_current_day(
    kind: Kind,
    state: float | None,
    last_reset: datetime | None,
    now: datetime,
    tz: tzinfo,
) -> tuple[float | None, datetime | None]:
    """The ``(state, last_reset)`` a sensor continues with, given what it had.

    The *total* has no ``last_reset``. A *today* integral keeps its state only if its
    ``last_reset`` is the local midnight of ``now``'s day or later; one of an earlier day,
    or none, is reset. Home Assistant would book a state under a new ``last_reset`` as fresh
    energy, so the old state must not be carried over into the new day.
    """
    if kind == TOTAL:
        return state, None
    midnight = local_midnight(now, tz)
    if state is None:
        return None, last_reset
    if last_reset is None or last_reset < midnight:
        return 0.0, midnight
    return state, last_reset


class DeltaIntegrals:
    """The live delta integrals of one config entry."""

    def __init__(self) -> None:
        self._by_id: dict[str, DeltaIntegral] = {}
        self._inputs: dict[str, DeltaIntegral] = {}
        self._combined: list[DeltaIntegral] = []
        self._series: dict[str, list[DeltaIntegral]] = {}
        # a base handed over before its sensor restored; applied when it does
        self._pending: dict[str, tuple[Base, str | None]] = {}

    def get(self, entity_id: str) -> DeltaIntegral | None:
        return self._by_id.get(entity_id)

    def all(self) -> list[DeltaIntegral]:
        return list(self._by_id.values())

    def restore(
        self,
        *,
        entity_id: str,
        kind: Kind,
        source: str | None,
        unit: str,
        value: float | None,
        last_reset: datetime | None,
        now: datetime,
        tz: tzinfo,
        series: str | None = None,
    ) -> DeltaIntegral:
        """Register a sensor with what it restored (``value`` ``None``: nothing restored).

        ``series`` names the series of a series integral (``source`` is then ``None``).
        """
        state, reset = _of_the_current_day(kind, value, last_reset, now, tz)
        integral = DeltaIntegral(entity_id, kind, source, unit, state, reset, series)
        self._forget(entity_id)
        self._by_id[entity_id] = integral
        if series is not None:
            self._series.setdefault(series, []).append(integral)
        elif source is not None:
            self._inputs[source] = integral
        else:
            self._combined.append(integral)
        pending = self._pending.pop(entity_id, None)
        if pending is not None:
            self._apply(integral, pending[0], pending[1], now, tz)
        return integral

    def _forget(self, entity_id: str) -> None:
        old = self._by_id.pop(entity_id, None)
        if old is None:
            return
        if old.series is not None:
            remaining = [i for i in self._series.get(old.series, []) if i is not old]
            if remaining:
                self._series[old.series] = remaining
            else:
                self._series.pop(old.series, None)
        elif old.source is not None:
            self._inputs.pop(old.source, None)
        else:
            self._combined = [i for i in self._combined if i is not old]

    def has_base(self, source: str) -> bool:
        """Whether the integral of a PV input, or an integral of a series, is there and available.

        A series has two integrals (*today* and *total*); one that is available is enough to
        book the series' delta, which an unavailable one then simply does not take.
        """
        integral = self._inputs.get(source)
        if integral is not None:
            return integral.available
        return any(own.available for own in self._series.get(source, ()))

    def skip_sources(self, sources: Iterable[str]) -> frozenset[str]:
        """The PV inputs and series without a base: their deltas are neither computed nor booked."""
        return frozenset(source for source in sources if not self.has_base(source))

    def book(self, deltas: Mapping[str, tuple[float, str]] | None) -> list[DeltaIntegral]:
        """Add the deltas of one run to the states; returns the integrals that changed.

        ``deltas`` maps a PV input, or the key of a series, to ``(delta, "Wh" | "kWh")``. Each
        per-input state grows by its input's delta, converted into the sensor's unit; the
        combined *today* and *total* by the addition of the booked deltas in kWh; the integrals
        of a series by the delta of the series alone, converted into their unit, and the
        combined ones are not touched by it. Nothing is booked for an unavailable integral, for
        an input or series without a sensor or base, or when there is no delta.
        """
        if not deltas:
            return []
        changed: list[DeltaIntegral] = []
        added_kwh = 0.0
        booked = False
        for source, (delta, unit) in deltas.items():
            integral = self._inputs.get(source)
            if integral is None:
                for own in self._series.get(source, ()):
                    if own.state is not None:
                        own.state += convert_unit(delta, unit, own.unit)
                        changed.append(own)
                continue
            if integral.state is None:
                continue
            integral.state += convert_unit(delta, unit, integral.unit)
            changed.append(integral)
            added_kwh += convert_unit(delta, unit, COMBINED_UNIT)
            booked = True
        if booked:
            for combined in self._combined:
                if combined.state is not None:
                    combined.state += convert_unit(added_kwh, COMBINED_UNIT, combined.unit)
                    changed.append(combined)
        return changed

    def reset_today(self, midnight: datetime) -> list[DeltaIntegral]:
        """Set every available *today* integral to 0 at ``midnight``; returns those changed."""
        changed: list[DeltaIntegral] = []
        for integral in self._by_id.values():
            if integral.kind == TODAY and integral.state is not None:
                integral.state = 0.0
                integral.last_reset = midnight
                changed.append(integral)
        return changed

    def invalidate(self, entity_ids: Iterable[str]) -> list[DeltaIntegral]:
        """Make the named integrals unavailable until a base is handed over; returns those changed.

        Used while the combined integrals are rewritten (ADR-027 decision 9): nothing is booked
        into an integral without a base, and its sensor shows ``unavailable`` meanwhile.
        """
        changed: list[DeltaIntegral] = []
        for entity_id in entity_ids:
            integral = self._by_id.get(entity_id)
            if integral is not None and integral.state is not None:
                integral.state = None
                changed.append(integral)
        return changed

    def hand_over(
        self,
        bases: Mapping[str, Base],
        now: datetime,
        tz: tzinfo,
        units: Mapping[str, str] | None = None,
    ) -> list[DeltaIntegral]:
        """Make each base the state of its sensor; returns the integrals that changed.

        ``bases`` is keyed by statistic ID, which is the entity ID of the sensor
        (``history.async_derive_pv_delta_integrals``). ``units`` optionally gives the unit
        the derived series was written in, so that the sensor continues in the very unit of
        its statistics. A base whose sensor is not there yet waits for it.
        """
        changed: list[DeltaIntegral] = []
        for statistic_id, base in bases.items():
            unit = units.get(statistic_id) if units else None
            integral = self._by_id.get(statistic_id)
            if integral is None:
                self._pending[statistic_id] = (base, unit)
                continue
            self._apply(integral, base, unit, now, tz)
            changed.append(integral)
        return changed

    @staticmethod
    def _apply(
        integral: DeltaIntegral, base: Base, unit: str | None, now: datetime, tz: tzinfo
    ) -> None:
        if unit is not None:
            integral.unit = unit
        integral.state, integral.last_reset = _of_the_current_day(
            integral.kind, base.state, base.last_reset, now, tz
        )
