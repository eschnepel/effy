# Effy – Effective PV Loss Distribution

A [Home Assistant](https://www.home-assistant.io/) custom integration that
calculates the conversion/wiring losses of a PV + BMS system and distributes
them **absolutely evenly** across all active input sources using a waterfall
model.

---

## Installation

### Via HACS (recommended)

1. Add this repository as a
   [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) in HACS
   (category: Integration).
1. Search for **Effy** in HACS and install it.
1. Restart Home Assistant.
1. Go to **Settings → Integrations → Add Integration** and search for **Effy**.

### Manual installation

1. Copy `custom_components/effy/` into `config/custom_components/effy/`.
1. Restart Home Assistant.
1. Go to **Settings → Integrations → Add Integration** and search for **Effy**.

---

## Recorder

Effy's `sensor.effy_*` entities are recorded like any other sensor; nothing
needs to be excluded from the recorder. Effy writes statistics for these
entities itself, but never inserts a row the recorder is about to insert: it
starts its work from the recorder's own 5-minute statistics event, never writes
a statistic for the slot that is still open, and only updates a row after the
recorder has compiled it (ADR-011, ADR-016, ADR-020).

If you added this to `configuration.yaml` for an earlier version, you may remove
it and restart Home Assistant:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.effy_*
```

Because the entities are recorded, a slot for which Effy has no data (for
example during an outage of your source sensors) keeps the recorder's own row
for the first slot of the outage, which holds the last value Effy reported; the
following slots stay empty. Effy requires Home Assistant 2026.3 or newer.

## Configuration

All parameters are set via the UI (Config Flow + Options Flow).

| Parameter | Description | Default |
| -- | -- | -- |
| **PV input sensors** | PV strings | – |
| **BMS input sensors** | BMS import-from-battery, BMS import-from-grid (optional) | – |
| **Output sensors** | BMS export-to-battery, BMS export-to-grid | – |
| **Max history days** | How many days of 5-min statistics to reprocess | 28 |

At least one input sensor is required in total (PV or BMS); a sensor cannot be
in both lists. The loss calculation treats both lists as one pool of inputs —
the split only tells Effy which inputs belong to the battery system.

**Upgrading from a version with one input list:** the old list is moved into the
PV input list automatically when Effy starts, and every value stays as it was.
Open Effy's options once and move the BMS sensors to the BMS input list.

Sensors may be in **W, kW, Wh, or kWh**. Effy converts all values to
W-equivalent average power internally. Output sensors are written in W (or kW),
regardless of whether the source sensor uses Wh or kWh — the output represents
average power over the interval, which is the physically meaningful quantity.

---

## Effy sensors

For every input sensor one effective sensor is created:

| Source entity ID | Effy entity ID | Effy entity name |
| -- | -- | -- |
| `sensor.pv_south` | `sensor.effy_pv_south` | PV South (effective) |
| `sensor.bms_bat_import` | `sensor.effy_bms_bat_import` | BMS bat import (effective) |

Each effective sensor exposes three extra attributes for diagnostics:

- `source_entity` – the original entity ID
- `total_loss_w` – total system loss in W for the current reading
- `loss_share_w` – the share of that loss assigned to this sensor in W

### Standby power

If at least one **BMS input** is configured, Effy also creates
`sensor.effy_standby_power` (**Standby power**, W, state class measurement,
recorded like every Effy sensor): the power the battery system itself draws to
keep running. The standby stays inside the loss above; this sensor only reads it
separately, so every other `effy_*` series is unchanged. Without a BMS input the
sensor does not exist.

How it is read (ADR-018): a BMS counter that reports in coarse steps (for
example 10 Wh) jumps once per step. When two consecutive jumps of the same BMS
input are exactly one step each, and nothing else — no PV input, no output, no
other BMS input — showed any flow from just before the first jump until just
after the second, the time between them is a *drain interval*: the standby
energy of one step divided by that time is one standby power value. An interval
is confirmed 125 minutes after its second jump, so that a counter that reports a
load late has redistributed it first. Each confirmed interval moves the state by
5 %: `new = 0.95 · old + 0.05 · value`. A BMS fed from the grid is found the
same way; no option names the drain source. A BMS input that reports power (W)
works too: an isolated reading above 0 is a jump, two consecutive readings above
0 are sustained power and break the chain.

The sensor is **unavailable** (not unknown, and never an invented default) until
the first interval has counted. It is rounded to 0.1 W and has these attributes:
`last_counted_value`, `last_drain_interval_end`, `drain_source`,
`counted_intervals` and `last_processed_slot`.

After a restart the estimate and its counters come back from Home Assistant's
restored state; Effy then replays the last 24 hours of BMS jumps and slot
statistics inside its first slot job (it never blocks Home Assistant's startup),
which gives the same result as uninterrupted operation. A downtime longer than 3
days loses the intervals that would have been confirmed in the gap. If your BMS
reports in a unit that jumps rarely (hours between jumps), the estimate is slow
to appear.

---

## For other integrations

`sensor.effy_recalculated_from` is the one Effy sensor with a fixed entity ID.
Besides its state (the earliest slot touched by the latest recalculation) it
publishes Effy's configuration as attributes, so another integration can find
Effy's sensors and the history length by reading a single entity instead of
asking the person to pick them (ADR-021):

```yaml
schema_version: 1
max_history_days: 28
sensors:
  - entity_id: sensor.effy_pv_south
    series: effective
    source_entity_id: sensor.pv_south
    direction: input
    role: pv
    aggregations: [mean, state]
  - entity_id: sensor.effy_standby_power
    series: standby_power
    source_entity_id:
    direction: output
    role: bms
    aggregations: [mean, min, max]
```

| Key | Meaning |
| -- | -- |
| `schema_version` | Raised only when an existing key or its meaning changes; new keys do not raise it |
| `max_history_days` | The configured number of history days |
| `series` | `effective`, `derived_power`, `smoothed` or `standby_power` |
| `source_entity_id` | The configured source sensor; `null` for the standby sensor |
| `direction` | `input` or `output`; the standby sensor is an `output` |
| `role` | `pv` or `bms`; every output is `bms` |
| `aggregations` | The aggregations the sensor's **short-term** statistics offer, i.e. the `types` to pass to `statistics_during_period`; Effy's series have `mean` and `state` (never `sum`) |

- **Which sensors are listed:** every Effy sensor that exists, except
  `sensor.effy_recalculated_from` itself. A sensor that is created late (its
  source had no state at startup) is listed from the next recalculation on.
- **One signal:** the sensor is updated as the *last* step of every
  recalculation (the 5-minute job and the manual history rewrite), after the
  statistics and the sensors' states are final, and it fires a `state_changed`
  event every time, even for an equal timestamp. Subscribing to this one entity
  is enough; the attributes are refreshed in the same write.
- **After a restart** every Effy sensor comes back with its last state. This
  sensor also keeps its attributes and marks them with `restored_as_of` (when
  the data was last written) and `restored_at` (when it was restored) until the
  next recalculation, normally within five minutes. The very first start has no
  `sensors` attribute: read its absence as "not ready yet".
- **Size:** the attributes are recorded; Home Assistant stores no attribute set
  above 16 KiB (roughly 35 sources).

### The contract as a file you can copy

`custom_components/effy/effy_exports.py` holds every fixed entity ID, attribute
key and value above as importable symbols, plus a `TypedDict` for each of the
two shapes. It uses only the standard library and imports nothing from Effy, so
another project can copy it verbatim into its own package and still not depend
on Effy: no import of Effy, no version pin. Effy imports the very same file, so
the copy is what Effy publishes with. Every symbol starts with `EFFY_` (the
types with `Effy`), so a copy cannot collide with your own names. It needs
Python 3.11 or newer.

Read `sensors` tolerantly: ignore keys you do not know. Compare the published
`schema_version` with `EFFY_PUBLISHED_SCHEMA_VERSION` in your copy; a larger
published value means your copy is stale and should be copied again.

## How the algorithm works

### 1 – Total loss

```
total_loss = max(0, Σ inputs − Σ outputs)
```

The cap at zero prevents measurement noise from producing negative losses.

### 2 – Waterfall distribution

Only **non-zero** inputs participate. They are sorted **ascending by value** and
processed one by one:

```
equal_share = remaining_loss / count_of_remaining_active_sensors

if sensor_value ≥ equal_share:
    sensor gets  equal_share           → continue
else:
    sensor gets  sensor_value (→ 0)   → redistribute remainder
```

### 3 – Effective value

```
effective_value = sensor_value − loss_share    (≥ 0)
```

The invariant `Σ effective_inputs = Σ outputs` always holds.

---

## Worked example

**Scenario** – one 5-minute interval, mixed units (W for PV, Wh for BMS):

| Sensor | Role | Raw value | Unit |
| -- | -- | -- | -- |
| PV South | input | 153 | W |
| PV East | input | 60 | W |
| PV North | input | 7 | W |
| PV Garage | input | 0 | W |
| BMS bat import | input | 5 | Wh |
| BMS grid import | input | 0 | Wh |
| BMS bat export | output | 95 | Wh |
| BMS grid export | output | 100 | Wh |

> For sensors in Wh or kWh, Effy converts the 5-minute energy delta to
> W-equivalent average power (÷ slot duration in hours) before applying the
> waterfall. W and kW sensors are used as-is. All values in the table below are
> the W-equivalent figures seen by `distribute_loss`.

### Step 1 – Total loss

```
Σ inputs  = 153 + 60 + 7 + 0 + 5 + 0 = 225
Σ outputs = 95 + 100                  = 195
total_loss = max(0, 225 − 195)        =  30
```

### Step 2 – Active inputs, sorted ascending

| # | Sensor | Value |
| -- | -- | -- |
| 1 | BMS bat import | 5 |
| 2 | PV North | 7 |
| 3 | PV East | 60 |
| 4 | PV South | 153 |

*(PV Garage = 0 and BMS grid import = 0 → excluded)*

### Step 3 – Waterfall

| Step | Sensor | Value | Equal share | Can pay? | Assigned | Remaining |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | BMS bat import | 5 | 30 ÷ 4 = **7.50** | ✗ (5 < 7.50) | **5.00** *(full)* | 25.00 |
| 2 | PV North | 7 | 25 ÷ 3 = **8.33** | ✗ (7 < 8.33) | **7.00** *(full)* | 18.00 |
| 3 | PV East | 60 | 18 ÷ 2 = **9.00** | ✓ | **9.00** | 9.00 |
| 4 | PV South | 153 | 9 ÷ 1 = **9.00** | ✓ | **9.00** | 0.00 |

Both BMS bat import and PV North are too small to absorb their equal share;
their entire value is assigned as loss and the remainder cascades to the larger
sensors.

### Step 4 – Effective values

| Sensor | Raw | Loss share | **Effective** |
| -- | -- | -- | -- |
| `sensor.effy_pv_south` | 153 W | 9.00 W | **144.00 W** |
| `sensor.effy_pv_east` | 60 W | 9.00 W | **51.00 W** |
| `sensor.effy_pv_north` | 7 W | 7.00 W | **0.00 W** |
| `sensor.effy_pv_garage` | 0 W | 0.00 W | **0.00 W** |
| `sensor.effy_bms_bat_import` | 5 Wh | 5.00 Wh | **0.00 Wh** |
| `sensor.effy_bms_grid_import` | 0 Wh | 0.00 Wh | **0.00 Wh** |

**Verification:** `144 + 51 + 0 + 0 + 0 + 0 = 195 = Σ outputs ✓`

---

## History recalculation

Press the **Re-calculate History** button (under the Effy device in the
Integrations panel, category *Diagnostic*) to reprocess up to `max_history_days`
of 5-minute statistics.

The same loss-distribution algorithm is applied to each 5-minute slot. Existing
statistics for the `effy_*` sensors are **overwritten**, which is useful after
first installation or after changing the sensor list.

### How long did a recalculation take?

Every history recalculation writes one line to the Home Assistant log (logger
`custom_components.effy.history`, level INFO). If it does not show up, raise the
level:

```yaml
logger:
  logs:
    custom_components.effy.history: info
```

The line has this shape (numbers made up):

```text
Effy recalculation timing (history): total 301.20 s = fetch 40.10 s + compute 250.40 s + write 9.90 s + parse probe 0.60 s + other 0.20 s; recorder queries 23, raw states 1204331, slots 8064, rows written 24192; parse probe (one extra pass over all raw states, not part of compute): values 0.35 s, timestamps 0.25 s
```

| Part | What it measures |
| -- | -- |
| `fetch` | waiting for the recorder to read raw states, units and statistics |
| `compute` | Effy's own calculation on the event loop (trapezoid, interpolation, the slot loop, and the scans between the recorder reads) |
| `write` | importing the statistics into the recorder |
| `parse probe` | the extra pass below, kept out of `compute` |
| `other` | everything else in the call |
| `recorder queries` | recorder jobs issued (units, one raw read per energy sensor, offline-anchor lookbacks, power statistics) |
| `raw states` | raw state entries held for the calculation |
| `slots`, `rows written` | 5-minute slots calculated, short-term rows written |

The **parse probe** measures what parsing the raw states costs, separately: one
extra pass over all raw states, converting the state strings to numbers
(`values`) and the timestamps to epoch seconds (`timestamps`). Effy parses every
state at least once in the trapezoid and, when called from the button or the
slot timer, once more while reading the history, so the real parsing time is the
probe's `values` times the number of those passes. The probe adds roughly its
own time to the recalculation and is a diagnostic, not a feature.

The history recalculation's record continues with a **detail block** on the
lines below that line (numbers made up):

```text
  detail v2: HA 2026.3.4, Python 3.13.2 aarch64, 4 CPUs, recorder sqlite, keep_days 60, history days 60, energy sources 4, power sources 4
  cpu / wall (cpu is process-wide, it includes anything else Home Assistant did meanwhile): fetch 3.10 / 40.20 s, compute 12.00 / 12.10 s, write 20.50 / 170.20 s
  longest stretch without awaiting (event loop held): 12.05 s
  fetch jobs (wall = run in the executor + wait for the executor):
    raw states sensor.battery_discharge: calls 1, wall 12.30 s = run 12.10 s + wait 0.20 s, rows 543210
  compute stages (carved out of compute):
    trapezoid sensor.battery_discharge: 5.50 s, 543210 states -> 16865 slots
  slot loop: 4.10 s for 16865 slots = readings 1.20 s + waterfall 2.00 s + conversion 0.70 s + rest 0.20 s
  write calls (build = preparing and scheduling the imports on the event loop, recorder = awaiting the recorder):
    effective: 101190 short-term + 8430 hourly rows, build 0.80 s, recorder 60.20 s
```

| Part | How to read it |
| -- | -- |
| `detail v2` line | proves which build wrote the record, and on what machine |
| `cpu / wall` | CPU far below wall means waiting (storage, queues); near wall means computing |
| `longest stretch` | the longest time the recalculation held the event loop without giving it back |
| `run` and `wait` of a job | seconds inside the recorder's executor, and queued for it |
| `trapezoid`, `slot loop` | where `compute` goes; `rest` includes the instrumentation's own cost |
| `build` and `recorder` | preparing the imports, and awaiting the recorder until it has stored them |

The detail costs well under 1 % of a recalculation (measured on 480,000 states).

Since TASK-0030 the recalculation reads the stored statistics first and writes
only the rows that are new or different (ADR-024). The first line then ends with
`rows unchanged N` (rows found already up to date), the reads show up as
`stored rows <sensor>` lines under the fetch jobs, and a repeated recalculation
with unchanged settings writes nothing and leaves
`sensor.effy_recalculated_from` as it is. That sensor shows the earliest slot
that was actually changed. After a change of settings every value is new and the
run costs what it always did, about 1 ms per row.

If no `Effy recalculation timing` line shows up after a history recalculation,
the copy Home Assistant is running does not contain this feature. The logger
name in the log tells which copy that is (`custom_components.<folder>.history`);
install the current `helper_sensors` branch and press the button again.

The 5-minute recalculation writes the first line only, at level DEBUG, without
the probe and the detail. When reporting a slow recalculation, send the whole
record (all its lines).

### State-class handling during history recalculation

| Source state class | Field read | Wh→W conversion | Written as |
| -- | -- | -- | -- |
| `TOTAL_INCREASING` | `change` (HA-computed delta, Wh/kWh) | ÷ (5 min / 60) → W | `mean=val_W` |
| `TOTAL` (Wh/kWh unit) | `change` | ÷ (5 min / 60) → W | `mean=val_W` |
| `TOTAL` (W/kW unit) | `mean` | none | `mean=val_W` |
| `MEASUREMENT` | `mean` | none | `mean=val_W` |

All statistics Effy writes carry `mean` and `state` – never `min`, `max` or
`sum` – using `{"has_mean": True, "has_sum": False}` metadata. In a 5-minute row
`state` equals `mean`; in an hourly row `state` is the last 5-minute value. The
`mean` value is always in W (or kW), representing the average power over the
5-minute interval. For `TOTAL_INCREASING` and `TOTAL`-as-energy sources this is
the energy delta divided by the slot duration; for `MEASUREMENT` and
`TOTAL`-as-power sources it is the HA-computed 5-minute mean directly.

### ⚠️ Genuine 5-minute statistics via an internal recorder API

Home Assistant's recorder stores 5-minute data in a separate
`statistics_short_term` table, which the **public** statistics import API
(`async_add_external_statistics`) cannot write to — it only supports hourly
long-term statistics, and rejects any timestamp that isn't aligned to the top of
the hour. There is no documented, versioned API to retroactively write 5-minute
statistics for a past period; HA's own 5-minute compiler only ever runs against
"now".

To still provide genuine 5-minute data (rather than silently degrading to
hourly), Effy's `history.py` calls `Recorder.async_import_statistics(...)`
directly with `table=StatisticsShortTerm` — a method that exists on the recorder
instance but is not part of HA's documented integration API surface. It writes
to **both** tables on every recalculation:

- `statistics_short_term` (5-minute) – only for slots within the recorder's
  *actually configured* `purge_keep_days` (Effy reads the live value from the
  recorder instance at runtime — this is a single, global recorder setting that
  is **not** configurable per entity, but users commonly change it from HA's
  10-day default via `configuration.yaml: recorder: purge_keep_days: N`, so Effy
  always matches whatever is actually configured); older 5-minute slots are
  skipped since HA's own purge would delete them again regardless.
- `statistics` (hourly, long-term) – for the entire `max_history_days` window,
  so data survives beyond the short-term retention window (e.g. for the Energy
  dashboard).

**Practical consequence for users:** if you inspect `effy_*` sensor history
further back than your configured `purge_keep_days` (10 days unless you changed
it), you will see hourly granularity instead of 5-minute granularity — this is
expected and is HA's own short-term retention at work, not a bug in Effy.

**Practical consequence for maintainers:** this relies on private recorder
internals (the `table` parameter of `Recorder.async_import_statistics`, and the
`StatisticsShortTerm` / `Statistics` SQLAlchemy models) that are not guaranteed
to be stable across Home Assistant core releases. If history recalculation
starts failing after an HA core update, check
`custom_components/effy/history.py`'s module docstring first — it explains the
full reasoning and the exact internal calls involved — and consult ADR-003 and
ADR-004 for the design history behind this choice. This approach was validated
against a real, in-memory recorder instance
(`pytest-homeassistant-custom-component`) during development, covering both the
initial write and the overwrite-on-rerun case, but no automated regression test
currently runs against this in CI.

This already happened once in practice, not just hypothetically: HA core
≈2025.10+ replaced the `has_mean`/`has_sum` flags on `StatisticMetaData` with
`mean_type` (`StatisticMeanType` enum) and a new required `unit_class` field,
which broke history recalculation with `KeyError: 'unit_class'` on any core new
enough to require it. `history.py` now builds its metadata through
`_build_statistic_metadata()`, which detects at import time whether
`StatisticMeanType` exists and includes the new fields only when it does,
falling back to the legacy flags otherwise. See ADR-003's `unit_class` /
`mean_type` section for the full story — including the caveat that this
particular fix could not be re-verified against a real instance of the newer
core that originally hit the bug, only against the older core this integration's
test suite runs against.

---

## Architecture Decision Records

| ADR | Title |
| -- | -- |
| [000](adr/000-coding-standards.md) | Code quality standards, programming style & core concepts |
| [001](adr/001-waterfall-loss-distribution.md) | Waterfall model for absolute loss distribution |
| [002](adr/002-unit-normalisation.md) | Unit normalisation: W and Wh treated identically within an interval |
| [003](adr/003-statistics-api-usage.md) | Statistics API usage for history recalculation |
| [004](adr/004-overwrite-history.md) | Overwrite (not append) for history statistics |
| [005](adr/005-negative-loss-capping.md) | Capping negative loss at zero |
| [006](adr/006-live-update-strategy.md) | Live sensor update strategy (shared coordinator with debouncing) |
| [007](adr/007-total-increasing-live-delta.md) | Live accumulation and Wh→W conversion for energy sensors |
| [008](adr/008-mixed-unit-comparability.md) | Comparability of W and Wh in mixed-unit configurations |
| [009](adr/009-low-resolution-kwh-smoothing.md) | Smoothing low-resolution kWh sensor noise |
| [010](adr/010-idle-energy-staleness-and-slot-timer.md) | Idle-entity staleness in the live path & a slot-aligned recalculation timer |
| [011](adr/011-slot-timer-history-recalc.md) | Slot-timer-driven single-slot history recalculation |
| [012](adr/012-trapezoidal-energy-redistribution.md) | Trapezoidal energy redistribution, derived-power sensor, and recalculated-from tracking |
| [013](adr/013-late-binding-smoothing-push-and-zero-fill.md) | Late-bound derived sensors, gap smoothing, dashboard-refresh push, and idle-sensor zero-fill |
| [014](adr/014-larger-trapezoid-cap-and-staged-offline-lookback.md) | Larger trapezoidal cap, and a staged (not unbounded) offline-anchor lookback |
| [015](adr/015-skip-and-cache-offline-anchor-lookback.md) | Skip the offline-anchor lookback when nothing changed, and cache its answer |
| [016](adr/016-push-last-slot-value-as-live-state.md) | Push the last recalculated slot's value as a live state |
| [017](adr/017-recorder-exclusion-for-effy-entities.md) | Recorder exclusion for `sensor.effy_*` (superseded by ADR-020) |
| [018](adr/018-standby-power-sensor.md) | Standby power estimate from drain intervals |
| [019](adr/019-input-sensors-split-by-role.md) | Input sensors split by role (PV / BMS) |
| [020](adr/020-effy-entities-recorded-without-exclusion.md) | Effy entities are recorded; the exclusion requirement of ADR-017 is retired |
| [021](adr/021-config-attributes-on-recalculated-from.md) | Effy's configuration published as attributes of the recalculated-from sensor |
