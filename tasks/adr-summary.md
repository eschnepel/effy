# ADR Summary — Effy (architectural ground truth for sub-agents)

Last updated: 2026-10-06 (after ADR-014 amendments). Keep this concise; ADRs in
`adr/` are authoritative.

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
- Live accumulation path is **disabled** (`disabled/`); `EffyCoordinator` runs a
  slot timer (5 s after every 5-min boundary) calling `async_recalculate_recent`
  (ADR-010/011/012).
- Slot-timer path writes **short-term only**; hourly long-term only from full
  recalc (ADR-011 D2). Full recalc = button/service (`max_history_days`, default
  28).
- **Slot-timer rewrite range (ADR-014 amended 2026-10-05):** last
  `RECENT_RECALC_WINDOW`=20 min, extended back (global across sensors/series,
  max 1 day) to the window start of any real jump **or offline recovery (any
  delta, ADR-014 Amendment 2026-10-06)** that arrived inside the trigger window
  (default that window; reaches back to the last *completed* cycle, one-off
  catch-up of 1 day on the first cycle after start-up — ADR-014 Amendment
  2026-10-06). The still-open slot is written with the previous slot's value as
  a provisional guess (ADR-016 Amendment 2026-10-06; full recalc unchanged).
  Invariant: after each cycle, written closed slots == full recalc at the same
  time.
- Offline-anchor lookback is staged 30 min → 1 day → full history, with
  in-memory last-valid cache (ADR-014/015).
- Dashboard refresh: `notify_updated` pushes last-written slot value as live
  state (ADR-013 D3, ADR-016) — on the timer path that is the provisional open
  slot (= previous slot's value).

## Non-functional requirements

- Bounded recorder cost per 5-min cycle (no unbounded queries; HA bootstrap must
  not time out — ADR-014).
- Statistics must be correct for graph/energy-dashboard cards; live state is
  best-effort (ADR-011 D4, ADR-016).
- Required user config: recorder excludes `sensor.effy_*`; runtime warning if
  missing (ADR-017).
- `calculation.py` tested with zero mocking, loaded via file-path import
  (ADR-000 §6).

## Explicit exclusions ("we deliberately do NOT …")

- No neighbour-percentage smoothing (`smooth_zero_noise`, ADR-009 superseded).
- No `sum` statistics / no `has_sum` (ADR-003).
- No per-entity rewrite ranges in the slot-timer path (would corrupt waterfall
  rows — ADR-014 amendment).
- No hourly long-term write from the slot-timer path (ADR-011 D2).
- No backdating of live states into closed slots (no public API; ADR-011 D4).
- Integration cannot set recorder exclusions itself (ADR-017).
