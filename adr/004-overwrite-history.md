# ADR-004 – Overwrite (Not Append) for History Statistics

**Date:** 2026-06-30
**Status:** Accepted (superseded implementation detail – see "Implementation" below)

---

## Context

When the *Re-calculate History* button is pressed, Effy must decide whether
to **overwrite** existing `effy_*` statistics or only **fill in missing
slots**.

---

## Decision

Always **overwrite** existing statistics for the same `statistic_id` +
timestamp, across both the short-term (5-minute) and long-term (hourly)
recorder statistics tables.

---

## Rationale

The primary use cases for history recalculation are:

1. **First installation** – the BMS has been running for days/weeks before
   Effy was installed. There are no `effy_*` statistics yet; overwrite and
   append are equivalent.
2. **Configuration change** – the user adds or removes a sensor. The previous
   effective values were computed with a different set of inputs/outputs and
   are therefore wrong. They must be replaced, not preserved.
3. **Bug fix / algorithm change** – correcting past values requires
   overwriting.

An append-only strategy would leave stale rows from a previous configuration,
producing incorrect energy totals in dashboards.

---

## Implementation

> This section documents *how* overwrite is achieved technically. It was
> revised after the original implementation (using
> `async_add_external_statistics`) turned out to be incompatible with
> ADR-003's 5-minute requirement — see ADR-003 for the full investigation.

Overwrite is achieved by calling the recorder instance's own
`async_import_statistics(metadata, stats, table)` method directly, once for
`table=StatisticsShortTerm` and once for `table=Statistics`, instead of the
public `async_add_external_statistics` wrapper.

This method is not a custom "delete + insert" routine written by Effy.
Internally, for every `(metadata_id, start)` pair it processes, it:

1. Checks whether a row already exists for that exact metadata_id + start
   timestamp (`_statistics_exists`).
2. If yes → **updates** the existing row in place (`_update_statistics`).
3. If no → **inserts** a new row (`_insert_statistics`).

This is precisely the mechanism HA's own `async_add_external_statistics`
uses for hourly long-term statistics — Effy simply also drives it for the
short-term table, which the public wrapper does not expose. There is no
risk of duplicate rows or unique-constraint violations on repeated button
presses: the `(metadata_id, start_ts)` unique index on both
`statistics_short_term` and `statistics` is respected because every write
goes through this existing-row check first.

Practical implication for "what does overwrite actually overwrite":

- **Short-term table (5-minute):** only rows within the recorder's
  *actually configured* `purge_keep_days` are written (read at runtime
  via `instance.keep_days`; this is a single global recorder setting,
  not configurable per entity — Effy reads whatever the user has set
  instead of assuming HA's 10-day default). Older 5-minute slots are
  skipped entirely — HA's own purge task would delete them again within
  hours regardless of what Effy writes, so writing them would be wasted
  work.
- **Long-term table (hourly):** every clock-hour across the full
  `max_history_days` window is written, using the average of that hour's
  5-minute effective values. This is what survives long-term and is what
  the Energy dashboard and long-range history graphs read from.

See ADR-003 for why this requires using an internal, non-versioned part
of the recorder API (`Recorder.async_import_statistics` parametrized with
`table=StatisticsShortTerm`), the risks involved, and why no public,
documented alternative exists.

---

## Consequences

- **Pro:** History is always internally consistent with the current
  configuration, for both 5-minute and hourly granularity.
- **Pro:** No custom overwrite logic was written — Effy reuses HA's own
  existing-row check (`_statistics_exists` → update vs. insert), so the
  same correctness guarantees the recorder gives its own
  `async_add_external_statistics` callers also apply here.
- **Pro:** No logic is needed to detect and skip already-correct slots.
- **Con:** A full rewrite of up to 28 days × 288 slots/day = 8 064 short-term
  rows per sensor *would* be triggered on every button press if the
  short-term table had no retention limit — in practice this is capped by
  the recorder's actually configured `purge_keep_days` (10 days by
  default, but read live from `instance.keep_days` so a user-configured
  value of e.g. 3 or 31 days is respected exactly), so the number of
  short-term rows touched scales with that setting, plus up to 672 hourly
  long-term rows (28 days × 24 h/day). This is a one-shot operation with
  negligible performance impact on typical hardware regardless of the
  configured retention.
- **Con:** Relies on `Recorder.async_import_statistics` accepting a
  `StatisticsShortTerm` table argument — an internal implementation detail,
  not a public contract (see ADR-003).

