"""Turn a debug export into an anonymized, gzip-compressed fixture (TASK-0037).

A debug export (the file the history button writes with the ``debug_export`` option) holds
the real entity IDs, the time zone and the real dates of an installation. This reads one,
replaces everything that identifies it and writes ``<name>.json.gz`` into
``tests/fixtures/debug_export/curated/``, where ``tests/test_debug_export_fixtures.py``
picks it up without a change to the tests.

Usage (from the repository root, stdlib only)::

    python utils/import_debug_export.py debug.json --name charge_smear
    python utils/import_debug_export.py debug.json --name charge_smear --out some/dir
    python utils/import_debug_export.py debug.json --name x --anchor 2026-03-15
    python utils/import_debug_export.py debug.json --name x --keep-dates   # no date shift

What is replaced:

- the entity IDs of the configuration, by role (``sensor.pv_string_1_power``,
  ``sensor.grid_import_energy``, ...), in every string and key of the file, which also
  renames the statistics Effy derives from them (``sensor.effy_<generic>_power``); any other
  entity ID that is left becomes ``sensor.entity_<n>``. ``sensor.effy_standby_power`` is
  Effy's own and stays;
- all times, shifted by a whole number of days so that the run's date becomes ``--anchor``
  (slot alignment and the time of day are kept, which the machines depend on);
- the time zone (``UTC``);
- ``observed.unchanged_rows`` (dropped: it is keyed by a hash of the IDs and the real times,
  which no rename survives) and the order of the IDs in ``observed.statistics_units`` keys.
  An anonymized fixture therefore replays the standby, efficiency and battery rebuilds, not
  the history recalculation itself;
- configuration values that are not entity IDs, numbers or flags (``anonymized``).

What is kept, because the replay needs it: all numbers (counter values, power, SoC), the
versions and the structure. The counter totals still tell how much energy the system has
moved since the counters began; that is not personal data but it is a fingerprint, so look
at the report this prints before the fixture is committed.

The output is deterministic (gzip without a timestamp): importing the same export twice
gives the same bytes. The fixture is refused if an original entity ID is still in it.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "debug_export"
DEFAULT_OUT = FIXTURE_DIR / "curated"
DEFAULT_ANCHOR = date(2026, 3, 15)
GENERIC_TIME_ZONE = "UTC"

# Effy's own statistics: no part of them identifies an installation.
KEEP_IDS = frozenset({"sensor.effy_standby_power"})

_ENTITY = re.compile(r"\b(?:sensor|number|input_number|binary_sensor|switch)\.[a-z0-9_]+")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00")

# The roles of the configuration and the generic name of each (``{n}`` numbers a list).
_ROLES: dict[str, str] = {
    "grid_import_sensor": "grid_import_energy",
    "discharge_sensor": "battery_discharge_energy",
    "charge_sensor": "battery_charge_energy",
    "grid_export_sensor": "grid_export_energy",
    "soc_sensor": "battery_soc",
    "capacity_entity": "battery_capacity",
}
_LISTS: dict[str, str] = {"pv_input_sensors": "pv_string_{n}_power"}


class ImportRefused(ValueError):
    """The export cannot be anonymized safely."""


def load(payload: bytes) -> dict[str, Any]:
    """The document of a debug export, plain JSON or gzip."""
    raw = gzip.decompress(payload) if payload[:2] == b"\x1f\x8b" else payload
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict) or "config" not in document or "inputs" not in document:
        raise ImportRefused("this is not a debug export (no config and inputs sections)")
    return document


def _split(entity_id: str) -> tuple[str, str]:
    domain, _, object_id = entity_id.partition(".")
    return domain, object_id


def entity_map(config: dict[str, Any]) -> dict[str, str]:
    """``entity id -> generic entity id`` for the sensors the configuration names."""
    mapping: dict[str, str] = {}
    for key, generic in _ROLES.items():
        value = config.get(key)
        if isinstance(value, str) and "." in value:
            mapping[value] = f"{_split(value)[0]}.{generic}"
    for key, pattern in _LISTS.items():
        values = config.get(key)
        if isinstance(values, list):
            for number, value in enumerate(values, start=1):
                if isinstance(value, str) and "." in value:
                    mapping[value] = f"{_split(value)[0]}.{pattern.format(n=number)}"
    return mapping


def _strings(value: Any) -> Iterator[str]:
    """Every string and dictionary key of ``value``."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _map_all(value: Any, function: Callable[[str], str]) -> Any:
    """``value`` with ``function`` applied to every string and every dictionary key."""
    if isinstance(value, str):
        return function(value)
    if isinstance(value, dict):
        return {function(key): _map_all(item, function) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_all(item, function) for item in value]
    return value


def _is_generic(entity_id: str, generic_objects: set[str]) -> bool:
    if entity_id in KEEP_IDS:
        return True
    object_id = _split(entity_id)[1]
    return any(generic in object_id for generic in generic_objects)


def _renamer(document: dict[str, Any]) -> tuple[Callable[[str], str], dict[str, str]]:
    mapping = entity_map(document["config"])
    if not mapping:
        raise ImportRefused("the configuration names no sensor this tool knows how to rename")
    by_object = {_split(orig)[1]: _split(new)[1] for orig, new in mapping.items()}
    generic_objects = set(by_object.values())
    ordered = sorted(by_object.items(), key=lambda pair: -len(pair[0]))

    def rename_known(text: str) -> str:
        for original, generic in ordered:
            text = text.replace(original, generic)
        return text

    leftovers: dict[str, str] = {}
    for text in _strings(_map_all(document, rename_known)):
        for found in _ENTITY.findall(text):
            if not _is_generic(found, generic_objects) and found not in leftovers:
                leftovers[found] = f"{_split(found)[0]}.entity_{len(leftovers) + 1}"

    def rename(text: str) -> str:
        text = rename_known(text)
        for found in sorted(leftovers, key=len, reverse=True):
            text = re.sub(rf"{re.escape(found)}(?![a-z0-9_])", leftovers[found], text)
        return text

    return rename, {**mapping, **leftovers}


def _shifter(days: int) -> Callable[[str], str]:
    delta = timedelta(days=days)

    def shift(text: str) -> str:
        return _TIME.sub(lambda m: (datetime.fromisoformat(m.group()) + delta).isoformat(), text)

    return shift


def _shift_epochs(value: Any, seconds: float) -> Any:
    """The ``end`` of a statistics row is seconds since the epoch, not an ISO time."""
    if isinstance(value, dict):
        return {
            key: (
                item + seconds
                if key == "end" and isinstance(item, int | float) and not isinstance(item, bool)
                else _shift_epochs(item, seconds)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_shift_epochs(item, seconds) for item in value]
    return value


def _fix_observed(cache: dict[str, Any]) -> None:
    """What the cache keys by a property of the names or the times that a rename or a shift
    breaks. ``statistics_units`` is keyed by the sorted, comma-joined IDs: sorted again.
    ``unchanged_rows`` is keyed by a hash of the IDs and the absolute times of rows that the
    file does not hold, so it cannot be rebuilt: it is dropped, and the history recalculation
    of an anonymized fixture cannot be replayed (the rebuilds can)."""
    observed = cache.get("observed", {})
    units = observed.get("statistics_units")
    if isinstance(units, dict):
        observed["statistics_units"] = {
            ",".join(sorted(key.split(","))): value for key, value in sorted(units.items())
        }
    if "unchanged_rows" in observed:
        observed["unchanged_rows"] = {}


def _redact_config(config: dict[str, Any], renamed: dict[str, str]) -> list[str]:
    """Strings in the configuration that are not entity IDs are not known to be harmless."""
    redacted: list[str] = []
    for key, value in list(config.items()):
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str) and item not in renamed and not _ENTITY.fullmatch(item):
                config[key] = "anonymized"
                redacted.append(key)
                break
    return redacted


def anonymize(
    document: dict[str, Any], *, anchor: date | None = DEFAULT_ANCHOR
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The anonymized copy of ``document`` and a report of what was done."""
    rename, mapping = _renamer(document)
    ran_at = datetime.fromisoformat(document["inputs"]["ran_at"])
    days = (anchor - ran_at.astimezone(timezone.utc).date()).days if anchor is not None else 0
    shift = _shifter(days)
    out = json.loads(json.dumps(document))  # a deep copy of plain JSON
    redacted = _redact_config(out["config"], mapping)
    out = _map_all(out, lambda text: shift(rename(text)))
    if days:
        out = _shift_epochs(out, days * 86400.0)
    _fix_observed(out["cache"])
    meta = out["meta"]
    meta["time_zone"] = GENERIC_TIME_ZONE
    meta["fixture"] = {"anonymized": True, "date_shift_days": days, "expected": "PASS"}
    text = "\n".join(_strings(out))
    leaks = sorted(orig for orig in mapping if orig not in KEEP_IDS and orig in text)
    leaks += sorted(_split(orig)[1] for orig in mapping if _split(orig)[1] in text)
    if leaks:
        raise ImportRefused(f"an original entity ID is still in the fixture: {leaks}")
    report = {
        "entities": dict(sorted(mapping.items())),
        "date_shift_days": days,
        "redacted_config_keys": redacted,
        "time_zone": f"{document['meta'].get('time_zone')} -> {GENERIC_TIME_ZONE}",
    }
    return out, report


def fixture_bytes(document: dict[str, Any]) -> bytes:
    """Compact JSON in gzip with no timestamp: the same document gives the same bytes."""
    text = json.dumps(document, separators=(",", ":"), ensure_ascii=True)
    return gzip.compress(text.encode("ascii"), compresslevel=9, mtime=0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("export", type=Path, help="a debug export (JSON or gzip)")
    parser.add_argument("--name", required=True, help="the fixture's name (no extension)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the fixture directory")
    parser.add_argument("--anchor", type=date.fromisoformat, default=DEFAULT_ANCHOR)
    parser.add_argument("--keep-dates", action="store_true", help="do not shift the times")
    parser.add_argument("--force", action="store_true", help="overwrite an existing fixture")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("--name takes letters, digits, '_' and '-' only")
    target = args.out / f"{args.name}.json.gz"
    if target.exists() and not args.force:
        parser.error(f"{target} exists (--force overwrites it)")
    try:
        document, report = anonymize(
            load(args.export.read_bytes()), anchor=None if args.keep_dates else args.anchor
        )
    except ImportRefused as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fixture_bytes(document))
    print(f"wrote {target} ({target.stat().st_size / 1024:.0f} KiB)")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
