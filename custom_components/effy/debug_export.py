"""The debug export of a history recalculation (TASK-0037, ADR-025 Amendment 2026-11-03).

Pure: no Home Assistant import (ADR-000 §6). The input of a history recalculation is
Effy's configuration, the clock, and what the recorder returned; the cache holds the
last (``Cache.snapshot()``), so one file with those three is enough to run the same
recalculation again, offline (``tests/replay.py``).

The file is JSON, gzip-compressed, with an explicit ``format_version``. Sections:

- ``meta``: versions, the time zone, when it was written, which sections were left out
  for size (``omitted``) and how large each one is (``section_bytes``);
- ``config``: the entry's options;
- ``inputs``: ``ran_at`` (the instant the recalculation took for "now"), ``window_start``
  and the volatile last-valid-reading cache (ADR-015) as it was before the run;
- ``result``: what the run returned (rows written, ``recalculated_from``);
- ``cache``: the snapshot, everything the run read, plus the stored rows and the units
  it observed;
- ``written``: the rows the writer scheduled, per statistic and table.

Datetimes are ISO 8601 in UTC with their offset; states are the strings the recorder
returned. Writing the file is the only I/O here, and it is the caller's job to run it off
the event loop.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1
FILE_PREFIX = "effy-export-"
FILE_SUFFIX = ".json.gz"
DIRECTORY = "effy_debug"
KEEP_FILES = 5
# The bound is on the uncompressed JSON; the Pi's 28 days are a few MB (TASK-0037 Stage 1).
MAX_BYTES = 50 * 1024 * 1024

# Always written: small, and the replay needs them. ``cache`` first, then ``written``: the
# sections that may be left out, in the order they are kept.
_BULK = ("cache", "written")
_REQUIRED = ("format_version", "meta", "config", "inputs", "cache")


class ExportError(ValueError):
    """The file is not one this Effy can replay (wrong version, damaged, incomplete)."""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@dataclass
class ExportRecord:
    """What a history recalculation tells the export that the cache does not hold."""

    ran_at: datetime | None = None
    window_start: datetime | None = None
    energy_reading_cache: dict[str, list[str]] = field(default_factory=dict)
    written: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)

    def note_reading_cache(self, readings: Mapping[str, tuple[datetime, str]] | None) -> None:
        """A copy, taken before the run: the run keeps the original warm."""
        self.energy_reading_cache = {
            entity: [_iso(ts), state] for entity, (ts, state) in (readings or {}).items()
        }

    def note_written(
        self, statistic_id: str, unit: str, table: str, rows: Iterable[Mapping[str, Any]]
    ) -> None:
        """One import the writer scheduled (``table`` is ``5minute`` or ``hour``)."""
        self.written.append(
            {
                "statistic_id": statistic_id,
                "unit": unit,
                "table": table,
                "rows": [
                    {"start": _iso(row["start"]), "mean": row["mean"], "state": row["state"]}
                    for row in rows
                ],
            }
        )


@dataclass(frozen=True)
class ExportOutcome:
    path: str
    size: int
    omitted: tuple[str, ...] = ()


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=True)


def encode(
    record: ExportRecord,
    *,
    config: Mapping[str, Any],
    cache: Mapping[str, Any],
    meta: Mapping[str, Any],
    max_bytes: int = MAX_BYTES,
) -> tuple[bytes, list[str]]:
    """The file's bytes, and the names of the sections left out for size.

    The small sections are always in; ``cache`` and ``written`` are kept in that order
    while the total stays within ``max_bytes`` (uncompressed), and a section that would
    exceed it is left out and named in ``meta``: never cut short.
    """
    if record.ran_at is None or record.window_start is None:
        raise ExportError("the recalculation did not note when it ran")
    inputs = {
        "ran_at": _iso(record.ran_at),
        "window_start": _iso(record.window_start),
        "energy_reading_cache": record.energy_reading_cache,
    }
    small = {
        "config": _dumps(dict(config)),
        "inputs": _dumps(inputs),
        "result": _dumps(record.result),
    }
    bulk = {"cache": _dumps(dict(cache)), "written": _dumps(record.written)}
    used = sum(len(text) for text in small.values())
    omitted: list[str] = []
    kept: dict[str, str] = {}
    for name in _BULK:
        if used + len(bulk[name]) > max_bytes:
            omitted.append(name)
        else:
            kept[name] = bulk[name]
            used += len(bulk[name])
    full_meta = {
        **meta,
        "omitted": omitted,
        "max_bytes": max_bytes,
        "section_bytes": {
            **{name: len(text) for name, text in small.items()},
            **{name: len(text) for name, text in bulk.items()},
        },
    }
    sections = {"format_version": str(SCHEMA_VERSION), "meta": _dumps(full_meta), **small, **kept}
    raw = "{" + ",".join(f'"{name}":{text}' for name, text in sections.items()) + "}"
    return gzip.compress(raw.encode("utf-8"), compresslevel=6, mtime=0), omitted


def loads(payload: bytes) -> dict[str, Any]:
    """The document in ``payload`` (gzip or plain JSON); refuses anything it cannot replay."""
    try:
        raw = gzip.decompress(payload) if payload[:2] == b"\x1f\x8b" else payload
        document = json.loads(raw.decode("utf-8"))
    except (OSError, EOFError, zlib.error, UnicodeDecodeError, ValueError) as err:
        raise ExportError(f"the debug export is damaged ({type(err).__name__}: {err})") from err
    if not isinstance(document, dict):
        raise ExportError("the debug export is damaged (it is not an object)")
    version = document.get("format_version")
    if version != SCHEMA_VERSION:
        raise ExportError(
            f"the debug export has format version {version!r}, this Effy reads {SCHEMA_VERSION}"
        )
    for name in _REQUIRED:
        if name not in document:
            omitted = document.get("meta", {}).get("omitted", [])
            hint = f" (left out for size: {', '.join(omitted)})" if name in omitted else ""
            raise ExportError(f"the debug export has no {name!r} section{hint}")
    return document


def entry_tag(entry_id: str) -> str:
    """Eight letters and digits of the entry id: enough to tell two entries apart."""
    return re.sub(r"[^A-Za-z0-9]", "", entry_id)[:8] or "entry"


def file_name(entry_id: str, moment: datetime) -> str:
    stamp = f"{moment.astimezone(timezone.utc):%Y%m%dT%H%M%S}Z"
    return f"{FILE_PREFIX}{stamp}-{entry_tag(entry_id)}{FILE_SUFFIX}"


def files_to_remove(names: Iterable[str], entry_id: str, keep: int) -> list[str]:
    """The oldest of this entry's export files past ``keep``; nothing else is ever named."""
    pattern = re.compile(
        rf"^{re.escape(FILE_PREFIX)}\d{{8}}T\d{{6}}Z-{re.escape(entry_tag(entry_id))}"
        rf"{re.escape(FILE_SUFFIX)}$"
    )
    ours = sorted(name for name in names if pattern.match(name))
    return ours[: max(0, len(ours) - keep)]


def export_to_file(
    directory: str | os.PathLike[str],
    entry_id: str,
    record: ExportRecord,
    *,
    config: Mapping[str, Any],
    cache: Mapping[str, Any],
    meta: Mapping[str, Any],
    keep: int = KEEP_FILES,
    max_bytes: int = MAX_BYTES,
) -> ExportOutcome:
    """Encode, write (a temporary file renamed, so never a corrupt one) and prune the old.

    Blocking: run it in an executor.
    """
    payload, omitted = encode(record, config=config, cache=cache, meta=meta, max_bytes=max_bytes)
    assert record.ran_at is not None
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file_name(entry_id, record.ran_at)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    for name in files_to_remove(os.listdir(target_dir), entry_id, keep):
        try:
            (target_dir / name).unlink()
        except OSError as err:
            _LOGGER.warning("Effy: could not remove the old debug export %s (%s)", name, err)
    return ExportOutcome(str(target), len(payload), tuple(omitted))
