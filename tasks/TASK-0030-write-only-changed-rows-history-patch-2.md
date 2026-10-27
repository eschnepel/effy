# Task: The Stored-Rows Check Compares Only What Home Assistant Returns

- **Status:** done — Gate 2 approved 2026-10-26: option A, widened by the human:
  "rewrite every hour affected, no compare is needed for the hour". Reviewer
  PASS (inline).
- **Related ADRs:** \[ADR-024 (Decision 2, amendment proposed below), ADR-003,
  ADR-004, ADR-012 (Amendment 2026-10-25)\]
- **Dependencies:** [TASK-0030-write-only-changed-rows-history-patch-1]

## Goal

On the Pi no stored row counts as unchanged. The record of 2026-10-26 shows, in
every series, `mean differs 0` and `state differs` for every row read, with
`state stored None`. The cause, verified against the source of Home Assistant
**2026.10.4** (`recorder/statistics.py`, tag `2026.10.4`, the function that
extracts metadata ids): when no series in the request has `has_sum` set, the
read discards the `sum` **and** `state` types. Effy's metadata sets
`has_sum: False` (`history.py`, metadata builder), so `statistics_during_period`
can never return `state` for Effy's rows, whatever is stored. The comparison of
ADR-024 Decision 2 ("`mean` and `state` equal") can therefore never succeed
through this public call. The tests missed it because their stored-row fake
returns `state` regardless of `has_sum`.

Not verified: whether the `state` column is really stored as written (the API
hides it). Option A does not depend on it; option B reads it directly.

Note: `sum` is discarded by the same rule, so "`sum` is empty" is not checkable
either; with `has_sum` false the recorder never fills it, so nothing is lost.

## Options (the human decides)

**A. Compare what the public API returns; derive `state` from the writer's
invariants.** Public API only (ADR-003/004 stay intact).

- 5-minute row: unchanged if `mean` equals the new value exactly and `min`,
  `max`, `last_reset` are empty. The writer sets `state = mean` for these rows,
  so a row with an equal `mean` and no native columns is what Effy wrote.
- Hourly row: its `state` is the hour's last slot value. Unchanged if `mean`
  equals, `min`, `max`, `last_reset` are empty, **and** the hour's last 5-minute
  row is itself unchanged (or, for a run without 5-minute rows, the hour is
  written).
- Residual risk (inferred, not verified): a native recorder row with the right
  `mean` but a null `state` and no `min`/`max`. Native rows of a `has_mean`
  series carry `min`/`max`, which the check catches, so such a row should not
  occur. The first Pi record after the fix shows `rows unchanged` and can be
  compared with the `recalculated from` result.
- Relaxes ADR-024 Decision 2 from "`mean` and `state` equal" to "`mean` equal,
  `state` implied by the writer's rule".

**B. Read the `state` column directly** from the recorder's database session
(`StatisticsShortTerm` / `Statistics` tables), keeping the exact rule of
Decision 2 unchanged. Cost: depends on recorder internals (table models and
session access), which can change with any Home Assistant release, against the
"public, stable API" policy of ADR-003/004; needs a new ADR decision and an
executor job with its own failure handling (the fail-safe of Decision 3 still
applies).

**Recommendation: A.** It keeps the public API, and the one thing it gives up
(`state` read-back) is determined by the writer's own rule.

## Acceptance Criteria

- Given a stored series whose rows the writer wrote (`mean` = new value, `state`
  hidden by the API because `has_sum` is false), then every 5-minute row counts
  as unchanged and `rows unchanged` equals the rows read. (A)
- Given a stored row with equal `mean` but `min`, `max` or `last_reset` set,
  then it counts as changed. (A)
- Given a stored row with a different `mean`, then it counts as changed.
- Given an hourly row with equal `mean` whose last 5-minute row differs, then
  the hourly row counts as changed. (A)
- Given the test fake, then it mirrors Home Assistant: it drops `sum` and
  `state` when the metadata has `has_sum` false, and `mean`, `min`, `max` when
  `has_mean` is false, as the source of 2026.10.4 does. A mutant that compares
  `state` again is caught.
- Given the detail block, then the `state differs` count no longer appears for a
  column the API cannot return; the line shows which columns were compared.
- Given the whole suite, `ruff`, `mypy`, `mdformat`, then they pass.

## Estimated File / Module Footprint (hint)

- `custom_components/effy/history.py`: `_COMPARED_TYPES`, `_unchanged_slots`,
  `_unchanged_rows`, `_hourly_rows` use.
- `tests/test_history_write_only_changed.py`: the fake store; new tests.
- `adr/024-history-recalculation-write-phase.md`: amendment (on approval).

## Amendment to apply to ADR-024 on approval (option A)

```markdown
## Amendment — 2026-10-26
**Reason:** Home Assistant 2026.10.4 discards the `state` and `sum` types of
`statistics_during_period` when the series' metadata has `has_sum` false, which
Effy's metadata always has; Decision 2's comparison of `state` could never be
evaluated (verified in the release's source and in the Pi's record).
**Decision:** a stored row is unchanged if its `mean` equals the new value
exactly and `min`, `max`, `last_reset` are empty; `state` is implied by the
writer's rule (5-minute: `state = mean`; hourly: the hour's last slot value,
so an hourly row is unchanged only if its last 5-minute row is too).
**Decided by:** human
```

## Definition of Done

- Tests green · docs updated · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- Any new external dependencies recorded in `tasks/DEPENDENCIES.md` (none
  expected)

## Consumed Interfaces

- `history._unchanged_slots`, `history._unchanged_rows`, `history._hourly_rows`,
  `history._mark_unchanged` from `custom_components/effy/history.py` (→ task:
  TASK-0030-write-only-changed-rows-history-patch-1)

## Delivered Artifacts

- `custom_components/effy/history.py` (48 changed lines against the pushed
  state; `async_rewrite_hourly` untouched): `_CLEARED_COLUMNS` = (`min`, `max`,
  `last_reset`), `_COMPARED_TYPES` = {`mean`, `min`, `max`, `last_reset`};
  `_unchanged_slots(hass, statistic_id, period, expected: dict[datetime, float])`
  compares the mean; `_unchanged_rows` keeps its signature and result; new
  `_unchanged_hours(slot_values, unchanged_short)`; `_mark_unchanged` derives
  `unchanged_hourly` from the 5-minute result in a history run and, as before,
  reads the hourly rows only in the hour rewrite; the detail block's
  `stored rows check` header says what is compared.
- `tests/test_history_write_only_changed.py`: the stored-row fake drops `sum`
  and `state` without `has_sum`, and `mean`/`min`/`max` without `has_mean`, as
  Home Assistant 2026.10.4 does (it reproduced the Pi's defect before the
  change); `TestTheAffectedHours`; the 8 tests that encoded the old rule
  rewritten. `tests/test_history_write_only_changed_recent.py` is unchanged.
- `adr/024-…md` Amendment 2026-10-26; `tasks/adr-summary.md`; `README.md`.
- External dependencies added: none.

## Review

**PASS** (inline; 655 tests, ruff, mypy on source and tests, mdformat). Eight
mutants caught: `state` compared again, no hour ever unchanged, every hour
unchanged, an uncompared slot ignored, a history run reading the hours, the hour
rewrite no longer comparing, `min` ignored, a tolerance on `mean`. An earlier
version of this patch also removed the hour rewrite's comparison and changed
`_unchanged_rows`' signature; the human rejected it, and it was redone from the
pushed state.

## Verification on the Pi (2026-10-27, HA 2026.10.4, button pressed after a run)

- History run: total 3.92 s (before: 106 s). Write 0.20 s (before: 104 s),
  `rows written 0`, `rows unchanged 109762`, `earliest change None`, no hourly
  rows written. 109,762 is the sum of the rows read per series (46,818 effective
  \+ 32,252 derived power + 30,692 smoothed), and each of the 14 series reads
  `equal` = `read`, `mean differs 0`, `columns set 0`.
- The stored-row reads cost 1.37 s (0.08 to 0.14 s per series) and are the whole
  rise of the fetch phase (1.8 s to 2.96 s). 20 recorder queries: units, 4 raw,
  power, 14 stored; no hourly read.
- Event loop held at most 0.37 s (limit 1 s).
- Recent run (slot job, with the 24-hour catch-up window because of a counter
  jump): 14 rows written (the new slot, 14 series), 4,018 unchanged, 0.19 s.
- Not exercised by this log: a run that writes a change (so the hours of changed
  slots), the hour rewrite at an hour boundary, and the hours older than
  `keep_days` (60 days kept, 28 history days).
- Not visible to the check: the stored `state` (Home Assistant does not return
  it).
