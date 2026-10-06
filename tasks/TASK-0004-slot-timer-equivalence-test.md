# Task: End-to-end equivalence test — slot timer vs full recalc

- **Status:** done
- **Related ADRs:** \[ADR-000 (§6), ADR-004, ADR-012, ADR-014 (Amendment
  2026-10-05, item 7)\]
- **Dependencies:** [TASK-0003-history-extended-rewrite-range]

## Goal

Capability CAP-1 acceptance test at the `history.py` level: run real
`async_recalculate_recent` cycle by cycle against faked recorder fetch/write
functions and prove the stored series equals `async_recalculate_history` and
conserves energy. HA is stubbed via `sys.modules` exactly like
`tests/test_coordinator_slot.py` (no HA install).

## Acceptance Criteria

- Given the screenshot scenario (single kWh input, readings
  07:30/10:50/11:25/11:52/12:14/12:34/12:53, cycles 10:00:05 → 13:05:05) Then,
  for every closed slot in `[10:00, 13:05)`, the timer-path store equals the
  full-recalc store, and total derived-power area = 60 Wh.
- Given an offline-recovery scenario (valid reading, 3 h of `unavailable`,
  recovery jump) Then the same equality holds for the whole outage range within
  `RECENT_REWRITE_MAX_LOOKBACK`.
- Given two sensors (a low-res energy input that jumps + a power-family input or
  energy output with its own rows) Then effective (`effy_*`) series also equal
  the full recalc — guards the "global range" rule (item 3).
- Given an idle scenario (no jumps) Then `_fetch_raw_energy_states` is called
  exactly once per energy sensor per cycle and the fetched start equals today's
  `now - RECENT_RECALC_WINDOW - margin`.
- The in-progress slot (`>= slot-aligned(now)`) is excluded from comparisons
  (out of scope per ADR-014 Amendment).

## Estimated File / Module Footprint (hint, not a commitment)

- `tests/test_history_recent_equivalence.py` (new; stubs `homeassistant.*`,
  patches `_fetch_raw_energy_states`, `_fetch_statistics`,
  `_get_statistics_units`, `_get_state_class`, `_get_unit`,
  `_write_recorder_statistics`)

## Definition of Done

- Tests green · `ruff`/`mypy --strict` clean (test file included) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none
  expected)

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from TASK-0003 Delivered Artifacts. -->

- `async_recalculate_recent(hass, entry_options, now, energy_reading_cache=None) -> tuple[int, datetime | None, set[str], dict[str, tuple[float, str]]]`,
  `async_recalculate_history(hass, entry_options, energy_reading_cache=None)`,
  `RECENT_RECALC_WINDOW`, `RECENT_REWRITE_MAX_LOOKBACK`,
  `_RAW_HISTORY_BOUNDARY_MARGIN`, `_effy_entity_id`, `_effy_power_entity_id`
  from `custom_components/effy/history.py` (→ task:
  TASK-0003-history-extended-rewrite-range)
- Existing patch targets in `custom_components/effy/history.py`:
  `_fetch_raw_energy_states`, `_fetch_statistics`, `_get_statistics_units`,
  `_get_state_class`, `_get_unit`, `_write_recorder_statistics`
- Stub pattern from `tests/test_coordinator_slot.py` (`_stub`, `_load`)

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. -->

- `tests/test_history_recent_equivalence.py` (new) →
  - helpers `_load_history` (real `history.py` under private package
    `effy_history_under_test`, HA stubbed only during load and `sys.modules`
    restored afterwards), `_World`, `_FakeRecorder`, `_FrozenDatetime`,
    `_install`, `_options`, `_energy_world`, `_run_timer_cycles`,
    `_run_full_recalc`, `_equivalent`, `_fixed_window_rule`,
    `_screenshot_readings`.
  - `TestSlotTimerEqualsFullRecalc`: screenshot scenario (jitter 0 / 37 s;
    equality + 0.06 kWh conserved), negative control (pre-amendment rule is
    *not* equivalent, < 0.04 kWh), offline recovery, 3-sensor waterfall (input
    energy + input power + sparse output energy; incl. negative control).
  - `TestRewriteRangeBehaviour`: idle cycle = 1 raw fetch/sensor over unchanged
    range; jump cycle = 2 fetches, power-family stats from the extended start,
    all sensors rewritten from it; extension bounded by
    `RECENT_REWRITE_MAX_LOOKBACK` with anchor needed for both fetches.
- No new external dependencies.
