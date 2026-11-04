# Debug export fixtures

Anonymized debug exports (TASK-0037), replayed with the real code by
`tests/test_debug_export_fixtures.py`. Stored as `*.json.gz` (compact JSON, gzip
without a timestamp).

| Folder | What it holds | Who writes it |
| -- | -- | -- |
| `curated/` | Real exports of an installation, anonymized | A maintainer, with the tool below |
| `synthetic/` | Exports made by the developers to test the test system (e.g. `FAIL`) | The developers |

Both folders are picked up automatically: no test code changes per fixture, and
no fixture is zero cases, not a failure.

## Adding one

1. Switch on the `debug_export` option and press **Re-calculate History**; the
   log line names the file under `<config>/effy_debug/`.

1. Import it:

   ```
   python utils/import_debug_export.py path/to/effy-export-....json --name what_it_shows
   ```

   The tool replaces the entity IDs by role (`sensor.grid_import_energy`, ...),
   shifts all times by a whole number of days to a fixed date, sets the time
   zone to `UTC` and prints a report of what it did. Read the report before
   committing. It refuses a file in which an original entity ID would remain.

1. Run the tests. A new fixture holds the rebuild results of the Effy that wrote
   it; if the calculation has changed since, the case fails and leaves the
   regenerated fixture (see below): check the new numbers are the right ones and
   copy it over.

## What a replay checks

The standby, efficiency and battery rebuilds are run again from the fixture's
cache alone (a read the file does not hold raises) and compared field by field
with `result.rebuilds`: where each machine began and ended, what it counted and
its value. Numbers compare with a relative tolerance of 1e-9, everything else
exactly.

The history recalculation itself is replayed only for a fixture that is not
anonymized: its cache is keyed by a hash of the IDs and the real times, which no
renaming survives.

## The optional `meta.fixture.expected`

`PASS` (the default) or `FAIL`. `FAIL` marks a fixture that must be rejected,
for example a tampered result: the case passes only if the replay disagrees with
it.

## When a `PASS` fixture starts failing "correctly"

If the calculation changed on purpose, the failing case leaves a copy with the
regenerated rebuild results at `_generated/<folder>/<same name>` (gitignored,
there only between a failing run and the next). If the change is intended, copy
it over the stale fixture.
