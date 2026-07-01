# ADR-003 – Statistics API Usage for History Recalculation

**Date:** 2026-06-30
**Status:** Accepted

---

## Context

Home Assistant's recorder stores long-term statistics at 5-minute resolution.
For history recalculation Effy must read these statistics for all configured
sensors and write corrected values for its own `effy_*` sensors.

Several design questions arose:

1. **Which statistic field to read** for each state class?
2. **How many API calls** to use for fetching?
3. **Which fields to write** for the output statistics?

---

## Decision

### Reading

Use `statistics_during_period` with `types={"mean", "change"}` in a **single
call** for all sensors:

- `TOTAL_INCREASING` sensors → read `change` (HA computes the per-interval
  delta internally, including counter-reset handling). No manual
  `sum[t] − sum[t−1]` bookkeeping is needed and there is no risk of negative
  deltas from sensor resets leaking into the calculation.
- `TOTAL` / `MEASUREMENT` sensors → read `mean` (already an instantaneous
  rate or average).

Requesting both fields in one call is simpler and cheaper than splitting
sensors into two groups and issuing two separate requests.

### Writing

Every `effy_*` statistic is computed as `{"start": ts, "mean": val}` –
**mean only, no state/sum field** – regardless of the source sensor's
`state_class`:

| Source state class | Field read | Written as |
|---|---|---|
| `TOTAL_INCREASING` | `change` | `mean=val` |
| `TOTAL` | `mean` | `mean=val` |
| `MEASUREMENT` | `mean` | `mean=val` |

`state` is intentionally omitted: it represents the live cumulative sensor
reading at the end of the interval, which is not available during history
recalculation without re-reading the raw `states` table. `mean` alone is
sufficient for all HA dashboard and Energy use-cases.

Importantly, this is **not** a per-source-state_class branch in code –
there is no logic anywhere in `history.py` that inspects `state_class` to
decide what to write. The `StatisticMetaData` registered for every
`effy_*` statistic is unconditionally:

```python
{"has_mean": True, "has_sum": False, ...}
```

This single, uniform metadata shape is what makes the output a **mean
statistic** (the only kind HA's `StatisticMetaData` model supports besides
sum statistics, since `state_class` itself is not a field of
`StatisticMetaData` — `has_mean`/`has_sum` are the only knobs). A
`TOTAL_INCREASING` source's effective value is already an interval delta
(W or Wh per 5 minutes, depending on the source unit) computed by
`distribute_loss`, which is exactly what a `mean` statistic represents for
that interval — so writing `has_sum=True` and tracking a running total
would be redundant and would additionally require Effy to manage counter
resets itself, which provides no benefit here. In effect, `TOTAL_INCREASING`
sources are **downgraded to a TOTAL-style mean statistic** on output, but
this falls out naturally from always writing `has_mean=True`/`has_sum=False`
for every sensor — it is not a conditional code path that treats
`TOTAL_INCREASING` differently from `TOTAL`/`MEASUREMENT` sources.

#### Why `async_add_external_statistics` could not be used

The original implementation wrote these values using
`async_add_external_statistics`, HA's public, documented API for
integrations to import statistics. Two problems emerged in practice:

1. **Hourly-only.** `async_add_external_statistics` writes exclusively to
   the long-term `statistics` table, which Home Assistant requires to be
   aligned to the top of the hour. Passing 5-minute timestamps (`HH:05`,
   `HH:10`, …) raises
   `HomeAssistantError: Invalid timestamp: timestamps must be from the top
   of the hour`. There is **no public API parameter** to request 5-minute
   granularity for externally-sourced statistics — `statistics_short_term`
   (the table that actually holds 5-minute data) is, by design, populated
   exclusively by HA's own periodic compiler (`compile_statistics`), which
   always runs against "now" and derives its values from real rows in the
   `states` table for that exact 5-minute window. It cannot be triggered
   for an arbitrary past timestamp, and it does not accept externally
   supplied values.
2. **Statistic ID mismatch.** `async_add_external_statistics` requires an
   external-style `statistic_id` (`domain:object_id`, e.g.
   `effy:effy_pv_south`), separate from the real `sensor.effy_pv_south`
   entity that the live coordinator (ADR-006) already updates. Using it
   would have created a second, disconnected statistics series that never
   lines up with the live sensor's own recorder-tracked history.

Since ADR-003's requirement is genuine 5-minute statistics for the actual
`sensor.effy_*` entities (not a parallel external series), no combination
of public API calls satisfies it.

#### Chosen approach: the recorder's internal `async_import_statistics`

Effy instead calls `Recorder.async_import_statistics(metadata, stats,
table)` directly — a public `@callback` method on the `Recorder` class
itself (not the module-level `async_import_statistics` function, and not
`async_add_external_statistics`). Unlike the documented wrappers, this
method is *not* hardcoded to a single table: it accepts
`table: type[Statistics | StatisticsShortTerm]`. Effy calls it twice per
sensor — once with `table=StatisticsShortTerm` (5-minute, ADR-003's
requirement) and once with `table=Statistics` (hourly, long-term,
persisted beyond the 10-day short-term retention window) — using
`source="recorder"` and `statistic_id == entity_id`, matching the metadata
the live `sensor.effy_*` entity already produces through normal HA
recorder operation.

This call:

- **Must run on the event loop**, not a worker thread. It is a
  `@callback`, and the underlying `StatisticsMetaManager` explicitly
  asserts it is only touched from the recorder's own thread — calling the
  equivalent blocking function (`homeassistant.components.recorder.
  statistics.import_statistics`) via `recorder.async_add_executor_job`
  was tried first and failed with
  `RuntimeError: Detected unsafe call not in recorder thread` (confirmed
  with a real recorder instance during development, not just by reading
  source).
- Schedules an internal `ImportStatisticsTask` on the recorder's own task
  queue; Effy awaits `instance.async_block_till_done()` afterwards so the
  task has actually been processed before reporting a row count back to
  the caller.
- Performs the same existing-row check / update-or-insert HA's own
  `async_add_external_statistics` callers rely on, so overwrite semantics
  (ADR-004) hold for both tables without Effy implementing its own
  delete-then-insert logic.

#### `unit_class` / `mean_type`: a metadata schema migration that broke this in production

Home Assistant core ≈2025.10+ replaced the `has_mean`/`has_sum` boolean
flags on `StatisticMetaData` with `mean_type` (a `StatisticMeanType` enum:
`NONE`, `ARITHMETIC`, `CIRCULAR`) and added a new required `unit_class`
field (the unit converter class to use, or `None` if none applies). This
broke Effy in the field with:

```
KeyError: 'unit_class'
  File ".../statistics_meta.py", line 224, in _update_metadata
    or old_metadata["unit_class"] != new_metadata["unit_class"]
```

This occurred specifically in `_update_metadata` (i.e. only on the second
or later recalculation run, once metadata already exists for a
`sensor.effy_*` statistic_id) — not on first creation. The traceback's own
metadata dump showed `mean_type` already present (HA's core had derived it
from `has_mean=True` automatically for backward compatibility), but
`unit_class` was simply absent from the dict Effy had built, because Effy's
code at the time only ever set `has_mean`/`has_sum`/`name`/`source`/
`statistic_id`/`unit_of_measurement` — fields that were sufficient for the
HA core version this integration was originally developed and tested
against (2025.1.4), which has no `unit_class` concept at all.

Two things are worth being explicit about here, since they determined the
fix:

1. The `KeyError` could only have come from Effy's own metadata dict
   (`new_metadata` in HA's comparison `old_metadata["unit_class"] !=
   new_metadata["unit_class"]`), not from the database-read side
   (`old_metadata`). `old_metadata` is built from a fixed set of SQL
   columns on every read (see `QUERY_STATISTIC_META` in
   `statistics_meta.py`); a `NULL` column value becomes a `None` *value*
   in that dict, not a *missing key* — so if the `unit_class` column
   exists in the schema (which it does, in any core version new enough to
   reference it in this comparison at all), `old_metadata` always has the
   key. Only a hand-built dict that simply never included the key (as
   Effy's did) can produce a `KeyError` here.
2. **Fix:** Effy now builds `StatisticMetaData` through a single helper,
   `_build_statistic_metadata()`, that defensively probes for
   `StatisticMeanType` via `try`/`except ImportError` at module load time.
   When available (newer cores), it adds `mean_type=
   StatisticMeanType.ARITHMETIC` and `unit_class=None` to the metadata in
   addition to the legacy `has_mean`/`has_sum` flags; when unavailable
   (older cores, e.g. 2025.1.4, the version this was tested against), only
   the legacy flags are sent, exactly as before. `unit_class=None` is
   correct here regardless of core version: Effy intentionally never asks
   the recorder to perform unit conversion — ADR-002 normalizes units
   itself, inside `distribute_loss`, before any statistic is ever written
   — so there is no unit-converter class for the recorder to apply.

This was not independently re-verified against the newer HA core version
that produced the original error (this integration's test environment is
constrained to an older Python/HA combination), so the fix is based on
direct analysis of the real production traceback and the published HA
developer-docs changelog for this API change, not a fresh end-to-end test
run against a matching HA core version. If recalculation still fails after
this fix on a recent core, check the exact `StatisticMetaData` shape that
core's `statistics_meta.py` expects before assuming the fix is wrong.

This is a deliberate use of recorder internals that are **not part of
Home Assistant's documented, versioned API surface**:

- The `table` parameter on `Recorder.async_import_statistics`, and the
  existence/shape of `StatisticsShortTerm` as an importable model, are
  implementation details. A future HA core release could change or remove
  them without notice, silently breaking this integration.
- The public wrappers (`async_add_external_statistics`,
  `homeassistant.components.recorder.statistics.async_import_statistics`)
  deliberately restrict callers to `table=Statistics` and validate the
  `source` field, specifically to prevent integrations from writing
  directly into `statistics_short_term`. Effy's history module
  intentionally bypasses that restriction because no other path satisfies
  the 5-minute requirement.
- This was verified to actually work — including the overwrite case and a
  full read-back via the public `statistics_during_period` API — using a
  real in-memory recorder instance (`pytest-homeassistant-custom-component`),
  not just by reading HA core source.

Two recorder tables therefore end up populated for every recalculated
slot, with different retention:

- `statistics_short_term` (5-minute) – only written for slots within the
  recorder's *actually configured* `purge_keep_days` (read at runtime via
  `instance.keep_days`, defaulting to HA's built-in 10 days only if that
  attribute is somehow unavailable). This is a single, global recorder
  setting — Home Assistant does not support per-entity short-term
  retention — but users commonly override it via `configuration.yaml:
  recorder: purge_keep_days: N`, so Effy reads the live value instead of
  assuming the default. HA's own purge task deletes anything older than
  this regardless of what Effy writes.
- `statistics` (hourly) – written for the *entire* `max_history_days`
  window, using the average of that hour's 5-minute effective values, so
  data survives beyond the short-term window (e.g. for the Energy
  dashboard).

See ADR-004 for the overwrite mechanics in more detail.

---

## Consequences

- **Pro:** Counter-reset handling is delegated to HA's own statistics engine
  (on the read side via `change`).
- **Pro:** A single `statistics_during_period` call is simpler and has lower
  overhead than per-state-class calls.
- **Pro:** The write path is fully uniform – one `{"start": ts, "mean": val}`
  for every source state class, no branching.
- **Pro:** Genuine 5-minute statistics exist in the recorder for `effy_*`
  sensors, satisfying the original requirement that the public statistics
  import API cannot fulfil on its own.
- **Con:** Both `mean` and `change` are always fetched for every sensor, even
  though each sensor only uses one field. The unused field is a small amount
  of extra data in the result dict.
- **Con:** Writing relies on `Recorder.async_import_statistics` accepting a
  `StatisticsShortTerm` table argument and on the `StatisticsShortTerm` /
  `Statistics` models keeping their current shape — neither is a documented,
  versioned contract. A future HA core release could break this without
  warning; this is accepted as the only way to satisfy the 5-minute
  requirement, and is clearly flagged in `history.py`'s module docstring for
  whoever maintains this integration through a future HA core upgrade.

---

## Amendment – 2026-07-01: Symmetry with the live path

ADR-007 introduced slot-aligned delta computation for `TOTAL_INCREASING`
sensors in the live coordinator.  The two paths are now symmetric:

| Path | Mechanism | Window |
|---|---|---|
| History (`history.py`) | `statistics_during_period` → `change` field | 5-minute slot from recorder |
| Live (`coordinator.py`) | `_slot_anchor` difference | 5-minute wall-clock slot (`_slot_start`) |

Both clamp negative deltas to 0 (counter resets).  The slot alignment
function `_slot_start` uses the same truncation as HA's own recorder, so
live and history slots are identical in boundary position.

A seamless transition occurs when a slot closes: the history path writes the
authoritative `change` value via `async_import_statistics` (ADR-004), which
overwrites whatever the live coordinator had accumulated.
