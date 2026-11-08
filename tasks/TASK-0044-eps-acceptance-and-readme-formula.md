# Task: CAP-12 acceptance test and the README's formula and ADR index

- **Status:** done
- **Capability:** CAP-12
- **Related ADRs:** [ADR-026, ADR-001, ADR-005]
- **Dependencies:** \[TASK-0042-eps-activity-in-standby-and-efficiency,
  TASK-0043-eps-sensors-and-published-entries\]
- **Gate:** Gate 2 approved by the human (2026-11-06); ADR-026 accepted.

## Goal

Close CAP-12: one end-to-end test that runs the demonstration of the capability,
and the README's description of the changed formula, so that code, README and
glossary say the same.

## Acceptance Criteria

- Given the CAP-12 demonstration, then one test module drives the real history
  recalculation on the in-memory recorder with an EPS sensor: 200 W out lowers
  the loss by 200 W; 150 W in raises the input total and takes an equal share; Σ
  effective inputs = Σ outputs; the same sources without an EPS sensor give
  results identical to before; an active EPS sensor makes a slot impure; the
  published list carries both entries.
- Given the README "How the algorithm works", then `total_loss` shows the EPS
  terms (`max(0, Σ inputs + eps_in − Σ outputs − eps_out)`), says what the
  signed sensor is split into, mentions the smoothing and the net-per-slot
  limit, and the worked example is unchanged or extended by one EPS line.
- Given the README ADR table, then ADR-026 is listed.
- Given `GLOSSARY.md`, `tasks/adr-summary.md` and ADR-026, then they agree with
  what was built; any deviation is a stop-and-ask (Phase 0), not a silent edit.
- Given the whole repository, then the full gate (`ruff format --check`,
  `ruff check`, `mypy --strict`, `pytest`) shows nothing the repository did not
  already have.

## Estimated File / Module Footprint (hint, not a commitment)

`tests/test_eps_capability.py` (new), `README.md`, `GLOSSARY.md` and
`tasks/adr-summary.md` only if the built code differs from their text.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code) · `ruff format` clean ·
  `mypy --strict` and `ruff check` show no finding the repository did not
  already have
- Docs updated (README, translations en and de where text changes) · no open ADR
  conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation, from the Delivered Artifacts of the dependency tasks. -->

- From TASK-0042-eps-activity-in-standby-and-efficiency: no production code
  changed; the EPS activity is read by `history.async_read_drain_activity` from
  `history._effy_smoothed_entity_id(eps)` (`mean != 0`, either sign); tests in
  `tests/test_eps_activity.py` (helpers `_add_eps`, `_rig`).
- From TASK-0043-eps-sensors-and-published-entries:
  `effy_exports.EFFY_ROLE_EPS`, `EFFY_FUNCTION_EPS`, `EFFY_DIRECTION_BOTH`;
  `catalog.describe_source_series(..., eps_id=...)`; `coordinator.eps_id`; the
  published entries `sensor.effy_<slug>` (`effective`, `input`) and
  `sensor.effy_<slug>_smoothed` (`smoothed`, `both`), both role and function
  `eps`.
- From TASK-0041-eps-split-in-slot-loop and TASK-0039-eps-signed-split-pure:
  `calculation.split_signed_reading`, `calculation.split_eps_readings`,
  `inputs.all_input_ids` (EPS last), `history._compute_effective_slots`; tests
  in `tests/test_eps_waterfall.py` (helpers `_world`, `_compute`,
  `_assert_all`).
- Pre-existing: the in-memory recorder rig of `tests/support.py` (`FakeStorage`,
  `StrictRecorderMixin`) and the harness of
  `tests/test_history_recent_equivalence.py`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact: downstream tasks depend on this. -->

- **No production code changed.**
- `tests/test_eps_capability.py` (new, 13 tests) → `TestTheDemonstration`,
  `TestWithoutAnEpsSensorNothingChanges`,
  `TestAnActiveEpsSensorMakesASlotImpure`,
  `TestThePublishedListCarriesBothEntries`, `TestTheReadmeExample`. One scenario
  (PV 1000 W; charge output and EPS sensor in three phases of eight slots) run
  through the real `history.async_recalculate_history` on the in-memory
  recorder: EPS +200 W lowers the loss from 300 W to 100 W; EPS −150 W raises
  the input total to 1150 W, the loss from 100 W to 250 W, equal shares of 125
  W; Σ effective inputs = Σ outputs in every slot; an idle port equals a run
  without the sensor; without the option the result equals the pre-CAP-12
  formula and a recorder without the sensor's data; the option adds exactly the
  two EPS series; a charging night shows the activity reading taking the written
  signed series and `classify_slot` turning a pure charge slot impure; the two
  published entries (`role`/`function` `eps`, `direction` `input` and `both`,
  schema version 1) describe series the recalculation wrote; the README's EPS
  lines run through `split_eps_readings` and `distribute_loss`.
- Negative control run once and not kept: removing the `split_eps_readings` call
  in `history._compute_effective_slots` made exactly the three formula tests
  fail.
- `README.md` → "How the algorithm works": `total_loss` with the EPS terms, the
  split, the smoothing on the signed series and the net-per-slot limit, the
  invariant with both halves; "Worked example": a new subsection "With an
  EPS/UPS sensor" (+20 W → loss 10 W; −15 W → loss 45 W, EPS effective value 4
  W) after the unchanged example; ADR table: ADR-026 added. **Beyond the task
  text:** ADR-022 (marked draft), 023, 024 and 025 were also missing from the
  table (it stopped at 021) and are added, so that it has no gap before 026.
  Titles are the ADRs' own.
- `tasks/adr-summary.md` → the ADR-026 entry said "Not yet implemented" and now
  says it is implemented by CAP-12; "Last updated" line. A status fact, not a
  decision: `GLOSSARY.md`, ADR-026 and the summary agree with the built code
  (checked), so no stop-and-ask was needed. `GLOSSARY.md`, ADR-026, translations
  (no text changed): unchanged.
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged).
- Verification: 1755 tests pass (1742 before, 13 new); `ruff check` clean;
  `ruff format --check` and `mypy custom_components/effy tests` show only what
  the repository had before (`tests/test_cache.py` formatting; one
  `no-any-return` in `tests/test_import_debug_export.py:82`, from TASK-0037, not
  touched); `mypy --strict custom_components` clean; `mdformat --check` clean on
  README and adr-summary (run with the `exclude` key of `.mdformat.toml` removed
  temporarily: it needs Python 3.13+, the sandbox has 3.12).
- Review (inline, separate pass): PASS.
