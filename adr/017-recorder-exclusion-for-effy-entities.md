# ADR-017 – Require Recorder Exclusion for `sensor.effy_*` Entities

**Date:** 2026-07-17
**Status:** Accepted

---

## Context

A real-world traceback was reported:

```
sqlite3.IntegrityError: UNIQUE constraint failed:
statistics_short_term.metadata_id, statistics_short_term.start_ts
```

raised from HA core's own recorder — `compile_missing_statistics` →
`_compile_statistics` → `StatisticsMetaManager.update_or_add` →
`_update_metadata`, failing on an autoflush of a pending
`StatisticsShortTerm` insert. There is no effy frame anywhere in the
stack: this is Home Assistant's *own* periodic statistics compiler
failing on its own insert.

### Root cause

ADR-003 established that `history.py` writes `effy_*` statistics via the
recorder's internal `Recorder.async_import_statistics(metadata, stats,
table=StatisticsShortTerm)`, deliberately using:

```python
{"source": "recorder", "statistic_id": statistic_id, ...}  # statistic_id == the real entity_id
```

so the statistic is indistinguishable, from the frontend's perspective,
from one HA compiled itself for an ordinary sensor — that was the whole
point (ADR-003: "no combination of public API calls satisfies" the
requirement for genuine, entity-attached 5-minute statistics).

The consequence, not fully appreciated at the time: `source: "recorder"`
plus a real `sensor.*` `statistic_id` is *also* exactly what an ordinary,
natively-tracked HA sensor looks like to the recorder's own statistics
compiler. Every `EffySensor` / `EffyDerivedPowerSensor` /
`EffySmoothedSensor` additionally:

- sets `_attr_state_class = SensorStateClass.MEASUREMENT`,
- is a normal, non-excluded entity in the `sensor` domain, and
- pushes real `state_changed` events (ADR-016's live push,
  `async_write_ha_state()` in `_on_updated`), so genuine rows land in the
  `states` table for it like for any other integration's sensor.

Nothing in `manifest.json`/`__init__.py` excludes these entities from the
recorder. HA's native sensor-statistics compiler therefore treats them
exactly like any other tracked sensor with a `state_class` and
periodically, independently tries to compile short-term statistics for
them from their own raw `states` history — with no awareness that
`history.py` is *also* writing directly into `statistics_short_term` for
the same `(metadata_id, start_ts)` via its own side-channel
`ImportStatisticsTask`. Two independent writers targeting the same
primary key on the same table is exactly what produces a
`UNIQUE constraint failed` race, and it can recur indefinitely — it is
not a one-off timing fluke, since the second writer (HA's native
compiler) runs on its own permanent schedule for as long as these
entities remain ordinary, recorder-tracked sensors.

### Options considered

1. **Drop `_attr_state_class` from the live entities.** Removes the
   attribute HA's native compiler keys off, so it stops compiling these
   entities entirely — `StatisticMetaData` has no `state_class` field, so
   Effy's own direct writes don't need it either. Rejected: some frontend
   cards and the Energy dashboard use the *live* entity's `state_class`
   attribute to decide whether it is eligible as a source at all; losing
   that would remove real functionality for users who want to add
   `effy_*` sensors to those views, even though the underlying statistics
   data would still be correct.
2. **Exclude `sensor.effy_*` from the recorder entirely** (chosen). Stops
   raw `states` writes for these entities, so HA's native compiler has no
   state history to compile from — the second writer disappears, and the
   race is eliminated at the source rather than raced against. The live,
   *current* value shown on dashboards is unaffected: `hass.states` (what
   a dashboard card reads) is independent of whether the recorder
   persists that state to the database. Effy's own statistics writes
   (`history.py`) are a completely separate path that never reads from
   `states` at all, so history/energy-dashboard graphs for `effy_*`
   entities are unaffected. The only real cost: cards that specifically
   read *raw* recent `states` history (not statistics) for these entities
   lose that fine-grained recent window — accepted, since Effy's own
   5-minute statistics already cover the same window at the same
   resolution.

### Why this can't be set by the integration itself

Recorder's include/exclude filters (`Filters`, built from the `recorder:`
YAML config's `include`/`exclude` blocks) are constructed once at
recorder startup from the user's own configuration; there is no
supported, documented API for a *different* integration to register an
exclusion for entities it doesn't own. This has to be the user's own
`configuration.yaml` change — Effy cannot silently apply it.

---

## Decision

1. **Document the required config** in `README.md`: users must add

   ```yaml
   recorder:
     exclude:
       entity_globs:
         - sensor.effy_*
   ```

   This single glob covers every entity this integration creates
   (`sensor.effy_{slug}`, `sensor.effy_{slug}_power`,
   `sensor.effy_{slug}_smoothed`, `sensor.effy_recalculated_from` — see
   `sensor.py`), so no per-entity list is needed and it stays correct as
   new input/output sensors are added.

2. **Detect and warn at runtime**, since a missing YAML change is
   otherwise silent until the race actually fires. `__init__.py`'s new
   `_async_check_recorder_exclusion` probes the recorder's own
   `entity_filter` callable with a synthetic id,
   `const.RECORDER_EXCLUSION_PROBE_ENTITY_ID =
   "sensor.effy_recorder_exclusion_probe"`, matching the glob without
   requiring any real effy entity to exist yet. `entity_filter` is a pure
   name-pattern test, so this works even before the `sensor` platform has
   created anything. If the probe id would still be recorded, a
   `homeassistant.helpers.issue_registry` Repairs issue
   (`recorder_not_excluded`, `IssueSeverity.WARNING`, not user-fixable
   from within HA since it requires a YAML edit + restart) is raised in
   **Settings → System → Repairs**; if exclusion is correctly configured,
   any existing issue is cleared. Called once per config-entry setup,
   after platform forwarding, in `async_setup_entry`.

3. Like the `async_import_statistics` usage this ADR is responding to,
   reading `instance.entity_filter` is a call against a public attribute
   of the internal `Recorder` instance, not a documented, versioned
   contract — wrapped in a defensive `try`/`except` (matching ADR-003's
   own stance) so a future core change to this attribute can only disable
   the warning, never break `async_setup_entry` itself.

---

## Consequences

- **Pro:** Eliminates the race at its source — HA's native compiler never
  runs for these entities at all once excluded, rather than Effy trying
  to out-race or out-guess undocumented internals it doesn't own (that
  internal logic lives in `home-assistant/core`, not in this repo, and
  isn't something this project can audit or fix directly).
- **Pro:** Effy's own statistics writes, and everything that reads them
  (history graphs, the Energy dashboard, `apexcharts-card`), are entirely
  unaffected — this fix only removes the *second*, redundant writer.
- **Pro:** Users who forget the config step get a visible, actionable
  Repairs entry instead of an eventual, confusing database error deep in
  HA core's own log output.
- **Con:** Requires a manual `configuration.yaml` edit and restart; there
  is no way for Effy to apply this on the user's behalf, and no
  Repairs "fix it for me" button is offered (`is_fixable=False`) since
  Repairs flows cannot edit YAML/restart HA on a user's behalf either.
- **Con:** Raw, recent `states` history for `effy_*` entities is no
  longer recorded (only accessible from HA's live in-memory state until
  the next restart). Any card reading *raw* state history rather than
  statistics for these specific entities loses that recent window —
  believed to be a non-issue in practice, since Effy's own 5-minute
  statistics already provide equivalent resolution for the same window,
  but not verified against every possible third-party card.
- **Not implemented in this pass:** automatically validating the
  exclusion glob's *exact* pattern (e.g. detecting a too-narrow
  user-written exclude that misses `_power`/`_smoothed` suffixes) — the
  probe only checks the literal glob this ADR asks users to add; a
  differently-shaped exclude block that happens to still leave some
  `effy_*` entities recorded would not be caught.
