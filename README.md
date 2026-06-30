# Effy – Effective PV Loss Distribution

A [Home Assistant](https://www.home-assistant.io/) custom integration that calculates
the conversion/wiring losses of a PV + BMS system and distributes them **absolutely
evenly** across all active input sources using a waterfall model.

---

## Installation

### Via HACS (recommended)

1. Add this repository as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) in HACS (category: Integration).
2. Search for **Effy** in HACS and install it.
3. Restart Home Assistant.
4. Go to **Settings → Integrations → Add Integration** and search for **Effy**.

### Manual installation

1. Copy `custom_components/effy/` into `config/custom_components/effy/`.
2. Restart Home Assistant.
3. Go to **Settings → Integrations → Add Integration** and search for **Effy**.

---

## Configuration

All parameters are set via the UI (Config Flow + Options Flow).

| Parameter | Description | Default |
|---|---|---|
| **Input sensors** | PV strings, BMS import-from-battery, BMS import-from-grid | – |
| **Output sensors** | BMS export-to-battery, BMS export-to-grid | – |
| **Max history days** | How many days of 5-min statistics to reprocess | 28 |

Sensors may be in **W, kW, Wh, or kWh**. Effy normalises everything to W
internally and writes output sensors in the same unit as their source.

---

## Output entities

For every input sensor one output sensor is created:

| Source entity ID | Effy entity ID | Effy entity name |
|---|---|---|
| `sensor.pv_south` | `sensor.effy_pv_south` | PV South (effective) |
| `sensor.bms_bat_import` | `sensor.effy_bms_bat_import` | BMS bat import (effective) |

Each output sensor exposes three extra attributes for diagnostics:

- `source_entity` – the original entity ID
- `total_loss_w` – total system loss in W for the current reading
- `loss_share_w` – the share of that loss assigned to this sensor in W

---

## How the algorithm works

### 1 – Total loss

```
total_loss = max(0, Σ inputs − Σ outputs)
```

The cap at zero prevents measurement noise from producing negative losses.

### 2 – Waterfall distribution

Only **non-zero** inputs participate. They are sorted **ascending by value**
and processed one by one:

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
|---|---|---|---|
| PV South | input | 153 | W |
| PV East | input | 60 | W |
| PV North | input | 7 | W |
| PV Garage | input | 0 | W |
| BMS bat import | input | 5 | Wh |
| BMS grid import | input | 0 | Wh |
| BMS bat export | output | 95 | Wh |
| BMS grid export | output | 100 | Wh |

> W and Wh are treated identically within one interval (the time factor
> cancels because it applies equally to all terms).

### Step 1 – Total loss

```
Σ inputs  = 153 + 60 + 7 + 0 + 5 + 0 = 225
Σ outputs = 95 + 100                  = 195
total_loss = max(0, 225 − 195)        =  30
```

### Step 2 – Active inputs, sorted ascending

| # | Sensor | Value |
|---|---|---|
| 1 | BMS bat import | 5 |
| 2 | PV North | 7 |
| 3 | PV East | 60 |
| 4 | PV South | 153 |

*(PV Garage = 0 and BMS grid import = 0 → excluded)*

### Step 3 – Waterfall

| Step | Sensor | Value | Equal share | Can pay? | Assigned | Remaining |
|---|---|---|---|---|---|---|
| 1 | BMS bat import | 5 | 30 ÷ 4 = **7.50** | ✗ (5 < 7.50) | **5.00** *(full)* | 25.00 |
| 2 | PV North | 7 | 25 ÷ 3 = **8.33** | ✗ (7 < 8.33) | **7.00** *(full)* | 18.00 |
| 3 | PV East | 60 | 18 ÷ 2 = **9.00** | ✓ | **9.00** | 9.00 |
| 4 | PV South | 153 | 9 ÷ 1 = **9.00** | ✓ | **9.00** | 0.00 |

Both BMS bat import and PV North are too small to absorb their equal share;
their entire value is assigned as loss and the remainder cascades to the
larger sensors.

### Step 4 – Effective values

| Sensor | Raw | Loss share | **Effective** |
|---|---|---|---|
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
Integrations panel, category *Diagnostic*) to reprocess up to
`max_history_days` of 5-minute statistics.

The same loss-distribution algorithm is applied to each 5-minute slot.
Existing statistics for the `effy_*` sensors are **overwritten**, which is
useful after first installation or after changing the sensor list.

### State-class handling during history recalculation

| Source state class | Field read | Written as |
|---|---|---|
| `TOTAL_INCREASING` | `change` (HA-computed delta) | `mean=val` |
| `TOTAL` | `mean` | `mean=val` |
| `MEASUREMENT` | `mean` | `mean=val` |

All output statistics are written with `mean` only – no `state`/`sum`
field – using the same `{"has_mean": True, "has_sum": False}` metadata for
every `effy_*` sensor, regardless of the source sensor's `state_class`.
There is no conditional logic that treats `TOTAL_INCREASING` sources
differently on the write side; `state` would require the live cumulative
reading, which is not available during recalculation. For
`TOTAL_INCREASING` sources this effectively means their interval delta is
written as a TOTAL-style mean statistic — but that falls out naturally
from the uniform metadata, not from a special case in the code.

### ⚠️ Genuine 5-minute statistics via an internal recorder API

Home Assistant's recorder stores 5-minute data in a separate
`statistics_short_term` table, which the **public** statistics import API
(`async_add_external_statistics`) cannot write to — it only supports
hourly long-term statistics, and rejects any timestamp that isn't aligned
to the top of the hour. There is no documented, versioned API to
retroactively write 5-minute statistics for a past period; HA's own
5-minute compiler only ever runs against "now".

To still provide genuine 5-minute data (rather than silently degrading to
hourly), Effy's `history.py` calls `Recorder.async_import_statistics(...)`
directly with `table=StatisticsShortTerm` — a method that exists on the
recorder instance but is not part of HA's documented integration API
surface. It writes to **both** tables on every recalculation:

- `statistics_short_term` (5-minute) – only for slots within the
  recorder's *actually configured* `purge_keep_days` (Effy reads the live
  value from the recorder instance at runtime — this is a single, global
  recorder setting that is **not** configurable per entity, but users
  commonly change it from HA's 10-day default via `configuration.yaml:
  recorder: purge_keep_days: N`, so Effy always matches whatever is
  actually configured); older 5-minute slots are skipped since HA's own
  purge would delete them again regardless.
- `statistics` (hourly, long-term) – for the entire `max_history_days`
  window, so data survives beyond the short-term retention window (e.g.
  for the Energy dashboard).

**Practical consequence for users:** if you inspect `effy_*` sensor history
further back than your configured `purge_keep_days` (10 days unless you
changed it), you will see hourly granularity instead of 5-minute
granularity — this is expected and is HA's own short-term retention at
work, not a bug in Effy.

**Practical consequence for maintainers:** this relies on private recorder
internals (the `table` parameter of `Recorder.async_import_statistics`,
and the `StatisticsShortTerm` / `Statistics` SQLAlchemy models) that are
not guaranteed to be stable across Home Assistant core releases. If history
recalculation starts failing after an HA core update, check
`custom_components/effy/history.py`'s module docstring first — it explains
the full reasoning and the exact internal calls involved — and consult
ADR-003 and ADR-004 for the design history behind this choice. This
approach was validated against a real, in-memory recorder instance
(`pytest-homeassistant-custom-component`) during development, covering
both the initial write and the overwrite-on-rerun case, but no automated
regression test currently runs against this in CI.

This already happened once in practice, not just hypothetically: HA core
≈2025.10+ replaced the `has_mean`/`has_sum` flags on `StatisticMetaData`
with `mean_type` (`StatisticMeanType` enum) and a new required
`unit_class` field, which broke history recalculation with `KeyError:
'unit_class'` on any core new enough to require it. `history.py` now
builds its metadata through `_build_statistic_metadata()`, which detects
at import time whether `StatisticMeanType` exists and includes the new
fields only when it does, falling back to the legacy flags otherwise. See
ADR-003's `unit_class` / `mean_type` section for the full story — including
the caveat that this particular fix could not be re-verified against a
real instance of the newer core that originally hit the bug, only against
the older core this integration's test suite runs against.

---

## Architecture Decision Records

| ADR | Title |
|---|---|
| [000](adr/000-coding-standards.md) | Code quality standards, programming style & core concepts |
| [001](adr/001-waterfall-loss-distribution.md) | Waterfall model for absolute loss distribution |
| [002](adr/002-unit-normalisation.md) | Unit normalisation: W and Wh treated identically within an interval |
| [003](adr/003-statistics-api-usage.md) | Statistics API usage for history recalculation |
| [004](adr/004-overwrite-history.md) | Overwrite (not append) for history statistics |
| [005](adr/005-negative-loss-capping.md) | Capping negative loss at zero |
| [006](adr/006-live-update-strategy.md) | Live sensor update strategy (shared coordinator with debouncing) |
