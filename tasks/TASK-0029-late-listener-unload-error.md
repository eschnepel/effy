# Task: Late-Binding Listener Must Survive a Second Removal

- **Status:** done (2026-10-25) — fix of a defect the human reported from the
  Pi; implemented inline, tests first, reviewer pass inline
- **Related ADRs:** [ADR-012]
- **Dependencies:** []

## Goal

Changing Effy's options reloads the entry. On the Pi the unload failed with
`ValueError: list.remove(x): x not in list` from Home Assistant's
`helpers/event.py`, so the entry was left in a failed-unload state. Cause:
`_add_late_derived_entities` (`sensor.py`) removes its state-change listener
itself once every pending source has a state, and also registers the same
removal with `entry.async_on_unload`; on unload Home Assistant removed it a
second time, which its tracker does not allow. The defect is older than
TASK-0010 (it came with the late-binding listener of the smoothed sensors) and
has nothing to do with the timing records.

## Acceptance Criteria

- Given every pending source has resolved (the listener removed itself), when
  the entry unloads, then no error is raised and the tracker was told to remove
  the listener once.
- Given a source still waits, when the entry unloads, then the listener is
  removed exactly once.
- Given the removal is called twice in a row, then the second call does nothing.
- Given the whole existing suite, then it passes unmodified.

## Definition of Done

- Tests green, `ruff`, `mypy` clean · `Delivered Artifacts` completed · no new
  dependency

## Consumed Interfaces

- `_add_late_derived_entities` from `custom_components/effy/sensor.py` (→ task:
  the smoothed-sensor late-binding feature, pre-ADR-numbered work)
- `_SetupEntry`, `_Platform`, `_Tracker`, `_event`, `_state` from
  `tests/test_sensor_setup.py`

## Delivered Artifacts

- `custom_components/effy/sensor.py` — in `_add_late_derived_entities`, the
  local `unsub` is now a once-only wrapper around the tracker's removal; no new
  symbol is exported.
- `tests/test_sensor_setup.py` — `_StrictTracker` (raises on a second removal,
  as Home Assistant's does) and `TestLateBindingUnload` (3 tests; two failed
  before the fix with the reported `ValueError`).
- External dependencies added: none.

## Review

**PASS.** 594 tests (591 before), `ruff` and `mypy` clean. The reported error is
reproduced by the new test against the unfixed code. Limit: verified against a
stub that mimics Home Assistant's tracker, read from the traceback; not run
against a real Home Assistant.
