# ADR Summary — Effy (architectural ground truth for sub-agents)

Last updated: 2026-10-29 (TASK-0024: the valley and span machine; TASK-0023:
SoC, capacity and hysteresis options; ADR-023 accepted 2026-10-27; the ADR-019
amendment is in force: four counter pickers, no update listener;
`effy_exports.py` added). Keep this concise; ADRs in `adr/` are authoritative.

## Tech stack

- Home Assistant custom integration (`custom_components/effy`, HACS), Python,
  async.
- Tooling gates (ADR-000): `ruff format`, `ruff check`, `mypy --strict`,
  `pytest` — all must pass.
- `from __future__ import annotations`, built-in generics, `X | None`, full
  signatures everywhere.

## Architecture

- Layering, dependencies point upward only (ADR-000 §3): `calculation.py` (pure,
  no HA imports, no I/O) → `sensor_utils.py` → `coordinator.py` →
  `sensor.py`/`button.py`/`history.py` → `__init__.py`.
- Loss model: absolute waterfall over active inputs, loss capped at 0 (ADR-001,
  005); W and Wh unified via `to_power_equivalent` (ADR-002, 008).
- History path writes `effy_*` statistics directly via recorder
  `async_import_statistics` (mean+state, no sum; overwrite semantics) (ADR-003,
  004).
- Energy-family sensors (TOTAL_INCREASING / TOTAL-as-Wh,kWh) are sourced from
  **raw state history** via `trapezoidal_slot_contributions` (ADR-012): jump
  spread evenly over the time it took, cap `TRAPEZOID_MAX_MINUTES`=120
  (ADR-014), offline gaps uncapped, explicit 0 for idle stretches (ADR-013),
  conservation of energy guaranteed.
- Per energy sensor: `effy_*` (effective, inputs only), `effy_*_power` (derived,
  all), power-family inputs: `effy_*_smoothed` (gap interpolation ≤2 slots)
  (ADR-012/013).
- Live accumulation path is **disabled** (`disabled/`); Input sensors are
  configured by role, PV and BMS inputs (ADR-019); the waterfall sees one mixed
  pool, and an old single-list entry is migrated silently into the PV list.
  `EffyCoordinator` runs a recorder-synchronized slot job (the recorder's
  5-minute statistics event, a probe until the last completed slot is readable,
  a 6-min wall-clock watchdog; HA minimum 2026.3) calling
  `async_recalculate_recent` (ADR-010/011/012). One lock serializes it with the
  history recalculation (ADR-011 Amendment 2026-10-16).
- Standby power (ADR-018): `sensor.effy_standby_power`, only with a BMS input,
  recorded (ADR-020). A pure machine in `calculation.py` reads *drain intervals*
  (two consecutive one-step peaks of one BMS input, everything else quiet from
  the slot before the first to the slot after the last peak, confirmed 125 min
  later) into a 5 % running average; the loss model is untouched. The slot job
  feeds it last, under the lock, from the statistics Effy wrote; restart =
  restore plus a replay of 24 h inside the first job.
- Slot-timer path writes **short-term only**; hourly long-term only from full
  recalc (ADR-011 D2). Full recalc = button/service (`max_history_days`, default
  28).
- **Slot-timer rewrite range (ADR-014 amended 2026-10-05):** last
  `RECENT_RECALC_WINDOW`=20 min, extended back (global across sensors/series,
  max 1 day) to the window start of any real jump **or offline recovery (any
  delta, ADR-014 Amendment 2026-10-06)** that arrived inside the trigger window
  (default that window; reaches back to the last *completed* cycle, one-off
  catch-up of 1 day on the first cycle after start-up — ADR-014 Amendment
  2026-10-06). No statistic is written for the still-open slot, in the slot job
  or the full recalc (ADR-016 Amendment 2026-10-16): the recorder inserts that
  row, Effy updates it after the recorder-synchronized trigger. The first job
  after a restart waits for the recorder, and the slot job rewrites an hour's
  long-term row when it completes the hour's last slot (ADR-011 Amendment
  2026-10-16). Invariant: after each cycle, written closed slots == full recalc
  at the same time.
- Offline-anchor lookback is staged 30 min → 1 day → full history, with
  in-memory last-valid cache (ADR-014/015).
- Effy publishes its configuration for sibling projects (Shady, Chargy) as
  attributes of `sensor.effy_recalculated_from` — `schema_version`,
  `max_history_days`, `sensors` (a list of dicts: entity, series, source,
  direction, role, aggregations) — **ADR-021, accepted 2026-10-18**. The
  sensor's update is the *last* step of every recalculation (ADR-012 Amendment
  2026-10-18), it sets `force_update`, and every Effy sensor restores its state
  after a reboot, and this sensor also its attributes, marked by
  `restored_as_of` (kept across a second reboot) / `restored_at` until the next
  recalculation. All attributes are recorded.
- The published contract (ADR-021) is also **one standalone file**,
  `custom_components/effy/effy_exports.py` (ADR-021 and ADR-000 Amendments
  2026-10-23): standard library only, no import from Effy, symbols only, every
  symbol prefixed `EFFY_` (types `Effy`). Downstream projects copy it verbatim;
  Effy imports the same file (`catalog.py`, `sensor.py`), and a test pins every
  value and fails if a contract literal is spelled out in another module. A
  breaking change raises `EFFY_PUBLISHED_SCHEMA_VERSION` in the same commit.
- Dashboard refresh: `notify_updated` pushes last-written slot value as live
  state (ADR-013 D3, ADR-016) — that is the last completed slot's value.

## Non-functional requirements

- Bounded recorder cost per 5-min cycle (no unbounded queries; HA bootstrap must
  not time out — ADR-014).
- Statistics must be correct for graph/energy-dashboard cards; live state is
  best-effort (ADR-011 D4, ADR-016).
- No recorder exclusion is required: `sensor.effy_*` is recorded, and Effy only
  updates rows the recorder inserted (ADR-020 Accepted, supersedes ADR-017; the
  README says an old exclusion may be removed).
- `calculation.py` tested with zero mocking, loaded via file-path import
  (ADR-000 §6).

## Explicit exclusions ("we deliberately do NOT …")

- No neighbour-percentage smoothing (`smooth_zero_noise`, ADR-009 superseded).
- No `sum` statistics / no `has_sum` (ADR-003).
- No per-entity rewrite ranges in the slot-timer path (would corrupt waterfall
  rows — ADR-014 amendment).
- No hourly long-term write from the slot-timer path (ADR-011 D2).
- No backdating of live states into closed slots (no public API; ADR-011 D4).
- No code in Shady or Chargy from this repo, and no `unit` key and no `sum`
  aggregation in the published `sensors` list (ADR-021).
- No setup check or repair issue about the recorder (ADR-017 retired, ADR-020).

## Recent ADRs

- **ADR-023 (Accepted, 2026-10-27):** four efficiency sensors (charge, battery,
  discharge, roundtrip) from the BMS counters configured by function (four
  single pickers replace the BMS-input and output lists, no migration), SoC and
  capacity pickers, pure runs of charge or discharge slots less standby energy,
  valleys of the SoC between spans, decayed sums at 5 % per kWh. In force since
  TASK-0017: the options hold `grid_import_sensor`, `discharge_sensor`,
  `charge_sensor`, `grid_export_sensor` (the old lists are ignored); an entry
  without a charge or grid export counter refuses to set up; the entry is
  reloaded by `OptionsFlowWithReload`, Effy has no update listener. Also in
  force since TASK-0023: the optional `soc_sensor` (state class `measurement`),
  `capacity_entity` (sensor, number or input_number; Wh or kWh if it reports a
  unit) — set together — and `valley_hysteresis` (default 30, minimum 15); they
  are neither inputs nor outputs of the waterfall. The pure valley and span
  machine is in `calculation.py` since TASK-0024: a zigzag of the SoC with
  threshold H and a 10-point band; battery efficiency = Σ discharge ÷ (Σ charge
  − Δstored) per span, half of each boundary slot, weight Σ charge − Δstored,
  the decay of the pure runs; a span is dropped (the estimate is never reset)
  for a capacity change above 1 %, an unavailable capacity, a missing charge or
  discharge statistic or a net input of 0 or less; an estimate above 100 % is
  published as it is (human, 2026-10-29); the half-slot convention has a bias of
  −0.05 points in simulation (Open decision 2 settled).
- **ADR-024 (Accepted, 2026-10-25):** the write phase is 98 % of a history
  recalculation (104 s of 107 s at 28 days on the Pi, about 1 ms per short-term
  row: Home Assistant's import does one `SELECT` and one `UPDATE`/`INSERT` per
  row). Chosen by the human 2026-10-25: write only rows that differ, in the
  history recalculation and the slot job; `recalculated_from` is the earliest
  change (ADR-012 Amendment 2026-10-25); live states still for every computed
  entity. Amendment 2026-10-26: Home Assistant drops `state`/`sum` for a series
  without `has_sum`, so only `mean`, `min`, `max`, `last_reset` are compared; a
  history run does not read hourly rows, an hour is rewritten only if one of its
  5-minute rows is written; the slot job's hour rewrite is unchanged.
- **ADR-022 (Draft, 2026-10-19):** a history recalculation takes about 5 minutes
  on a Raspberry Pi 5 and the code has no phase timing. Proposed: log the wall
  time of the fetch, compute and write phases first (TASK-0010, done 2026-10-19,
  with a separate parse probe; TASK-0010 patch 1, 2026-10-25, adds a detail
  block with the sub-processes after the first Pi run took 228 s and carried no
  record; measured 2026-10-25: compute 0.4 %, so the numpy and executor tasks
  are not released); only if compute dominates (gate G-perf, thresholds Q1) add
  an array-native compute path with numpy in a new pure module
  `calculation_grid.py` next to `calculation.py` (TASK-0011 to TASK-0013),
  tested against the pure-Python functions as oracle with an explicit tolerance
  (`abs=1e-9`, slot keys exact); optionally run the compute phase in an executor
  job (TASK-0014). Today: no runtime dependency beyond Home Assistant, the
  compute phase runs on the event loop, and the TASK-0004 harness compares with
  `pytest.approx` defaults.
