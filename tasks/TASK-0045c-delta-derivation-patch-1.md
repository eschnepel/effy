# Task: Patch 1 of TASK-0045c: the combined integrals' IDs read `deltasum`

- **Status:** done
- **Capability:** CAP-13
- **Related ADRs:** [ADR-027 (Amendment 2026-11-10)]
- **Dependencies:** [TASK-0045c-delta-derivation]

## Goal

The human decided (2026-11-10) on the open point of TASK-0045c: the entity IDs
of the two integrals that combine all PV inputs are renamed to
`sensor.effy_pv_deltasum_integral_today` and `sensor.effy_pv_deltasum_integral_total`.
Change only the artifacts that carry those IDs and drop the collision rule that
is no longer needed. TASK-0045c stays `done` and is not reopened.

## Acceptance Criteria

- Given `const.PV_DELTA_TODAY_ENTITY_ID` and `const.PV_DELTA_TOTAL_ENTITY_ID`,
  then they are `sensor.effy_pv_deltasum_integral_today` and
  `sensor.effy_pv_deltasum_integral_total`; `PV_DELTA_INTEGRAL_TODAY_SUFFIX` is
  unchanged.
- Given a PV source with the slug `pv` (for example `sensor.pv`), then the
  derivation derives its per-input series `sensor.effy_pv_delta_integral_today`
  as for any other input, logs no warning, and the combined series have their
  own IDs.
- Given the derivation otherwise, then nothing changes (the same rows, the same
  bases, keyed by the new combined IDs).

## Estimated File / Module Footprint (hint, not a commitment)

`const.py`, `history.py` (remove the skip and warning in
`async_derive_pv_delta_integrals`), `tests/test_delta_derivation.py`.

## Definition of Done

- Tests green · `ruff format` clean · `mypy --strict` and `ruff check` show no
  finding the repository did not already have
- `Delivered Artifacts` block completed and accurate
- No new external dependencies; `tasks/DEPENDENCIES.md` unchanged

## Consumed Interfaces

- `const.PV_DELTA_INTEGRAL_TODAY_SUFFIX`, `const.PV_DELTA_TODAY_ENTITY_ID`,
  `const.PV_DELTA_TOTAL_ENTITY_ID` in `custom_components/effy/const.py`
  (→ task: TASK-0045c-delta-derivation).
- `history.async_derive_pv_delta_integrals`, `history._pv_delta_integral_id`
  in `custom_components/effy/history.py` (→ task: TASK-0045c-delta-derivation).

## Delivered Artifacts

- `custom_components/effy/const.py` → `PV_DELTA_TODAY_ENTITY_ID` is now
  `"sensor.effy_pv_deltasum_integral_today"`, `PV_DELTA_TOTAL_ENTITY_ID`
  `"sensor.effy_pv_deltasum_integral_total"`; `PV_DELTA_INTEGRAL_TODAY_SUFFIX`
  unchanged (`"_delta_integral_today"`).
- `custom_components/effy/history.py` → `async_derive_pv_delta_integrals`: the
  collision skip and its warning are removed; the returned dict is keyed by the
  new combined IDs. No other symbol changed.
- Tests: `tests/test_delta_derivation.py` constants renamed; the guard test is
  replaced by `test_a_source_named_pv_is_derived_like_any_other_beside_the_combined_ones`
  and `test_the_constants_name_the_combined_integrals_deltasum` (32 → 33 tests there).
- External dependencies added: none.
- Reviewer (inline, no sub-agent available): PASS. Acceptance criteria met; the
  change stays within ADR-027 Amendment 2026-11-10; no other file references the
  old combined IDs (grep over `custom_components` and `tests`); full suite 1843
  passed; `ruff check` and `mypy --strict` clean, `ruff format` flags only
  `tests/test_cache.py` as before.
