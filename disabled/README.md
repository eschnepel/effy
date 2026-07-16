# disabled/

Live-path support is temporarily switched off (2026-07-09) until the
history path (`custom_components/effy/history.py`) works correctly on its
own. This folder holds the code that implements the live, event-driven
computation path, relocated here so it is not imported or executed by the
integration, but stays available for reactivation.

## Why not just delete it?

`EffyCoordinator` is going to be **extended**, not replaced: a future
change will repurpose the slot-aligned timer (still active in
`custom_components/effy/coordinator.py`) so that, at the end of every
5-minute slot, it computes that slot's result using the **history-path
logic** (an `async_recalculate_history`-style computation) instead of the
live event-driven accumulation implemented here. Concrete instructions for
that extension are still pending. Until they arrive, this folder is the
authoritative, last-known-working snapshot of the live-path implementation
— don't reconstruct any of it from memory if it's needed again before then.

## What's here

- **`coordinator_live.py`** — verbatim copy of the full, working
  `EffyCoordinator` as it existed before this split, including
  `LiveReading` (the per-entity accumulator: `update_power`/`update_energy`,
  `to_sensor_reading`, `reset`), `_state_class_family`,
  `_on_state_change`, the debounce timer (`DEBOUNCE_SECONDS`,
  `_do_refresh`), and the live version of `_recalculate_and_reset`.
  Its relative imports (`from .calculation import ...` etc.) are left
  exactly as they were — this file is only valid as a package member of
  `custom_components/effy/` again, i.e. restoring live-path support means
  moving this file back to `custom_components/effy/coordinator.py`
  (overwriting the current reduced shell), not importing it from here.

- **`test_coordinator_live.py`** — the test coverage for everything in
  `coordinator_live.py` (`LiveReading`, `_state_class_family`), moved out
  of `tests/` alongside the code it tests. Not collected by the active
  suite (`pyproject.toml`'s `testpaths = ["tests"]` only looks at the
  repo-root `tests/` folder), so it has zero effect on `pytest`/CI while
  disabled. Can still be run explicitly:
  ```
  pytest disabled/test_coordinator_live.py
  ```

## What's still active

`custom_components/effy/coordinator.py` was **not** deleted — it's a
reduced shell that keeps:
- Config-entry lifecycle (`__init__`, `async_setup`, `async_shutdown`).
- The subscriber registry (`subscribe`) — `EffySensor` still registers,
  but nothing currently pushes to it (see "Net effect" below).
- The slot-aligned timer (`_schedule_next_slot_timer`,
  `_next_slot_trigger_delay`) — as of 2026-07-10 (ADR-011) this is no
  longer a placeholder: it fires `SLOT_TIMER_LAG_SECONDS` after every
  `SLOT_MINUTES` boundary and calls `history.async_recalculate_slot` for
  the slot that just closed, writing short-term (5-minute) statistics only
  — see ADR-011 for why the long-term (hourly) write is deliberately
  skipped here and stays the responsibility of the full history recalc.
- `force_refresh()` — still a no-op, kept so `sensor.py`'s unconditional
  call to it on platform setup doesn't need touching.

`sensor.py`, `button.py`, `history.py`, `config_flow.py`, `__init__.py` and
all ADRs were **not** modified or moved — they don't depend on anything
that moved here beyond the `EffyCoordinator` class name and its surviving
methods (`subscribe`, `force_refresh`, `async_setup`, `async_shutdown`),
all of which still exist on the reduced shell.

## Net effect while disabled

- `EffySensor` entities still get created, but their live *displayed*
  state does not update — no state-change listeners are registered and
  nothing calls the subscriber callbacks (see ADR-011 for why a live push
  wouldn't substitute for the 5-minute history write below anyway: there's
  no API to backdate a live state into an already-closed slot).
- Every 5-minute slot, shortly after it closes, is recalculated from the
  underlying statistics and written as a short-term (5-minute) `effy_*`
  statistic — so historical graphs stay current even though the live
  entity state does not (ADR-011).
- The history recalculation button/service (`history.py`,
  unaffected by this move beyond the `_compute_effective_slots` /
  `async_recalculate_slot` split — see ADR-011) continues to work exactly
  as before, and remains the only path that writes long-term (hourly)
  statistics.
