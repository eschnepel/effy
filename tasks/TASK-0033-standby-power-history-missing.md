# Task: Find and fix why the standby sensor has no history

- **Status:** done — 2026-10-30, resolved by the human: the cause was candidate
  1 (a leftover recorder exclusion of `sensor.effy_*`); removing it fixed the
  history. Stage 1 was not needed and Stage 2 was not built (see Findings).
- **Capability:** CAP-3 (recorded entities), CAP-2 (the standby sensor)
- **Related ADRs:** \[ADR-020 (Effy entities are recorded, no exclusion),
  ADR-017 (superseded; asked users to exclude `sensor.effy_*`), ADR-018 §5,
  ADR-023 decision 3\]
- **Dependencies:** — (Stage 2's README wording is coordinated with
  TASK-0035-hourly-statistic-written-before-recorder-rollup)
- **Gate:** Stage 2 follows Stage 1 without a stop. The one exception is a fix
  that needs a private recorder attribute (ADR-017's check did, and its own text
  calls that "not a documented, versioned contract"): the human gives the go for
  that before it is built.

## Goal

Stage 1: find out why `sensor.effy_standby_power` shows only its current value
and no history on the human's instance, and whether that is the human's
configuration, a defect in Effy, or only the way a rarely changing sensor looks
in Home Assistant. Stage 2: fix what Stage 1 found, so that the sensor is
recorded on the human's instance, or, where the cause is the human's
configuration, so that Effy tells the user about it and the README says what to
do.

This matters beyond the graph: ADR-023 decision 3 makes **the recorded statistic
of `sensor.effy_standby_power` for a slot** the only source of the standby
energy of that slot, and a slot without it does not count. If the sensor is not
recorded, the charge and discharge efficiency cannot form (TASK-0034 depends on
this finding).

## Context (the human's report, 2026-10-30)

- The standby sensor "only shows the current value, no history is available".
- The README told users of versions before ADR-020 to add
  `recorder: exclude: entity_globs: - sensor.effy_*` to `configuration.yaml`,
  and now says they "may remove it". An instance that updated through HACS and
  kept that block still excludes every Effy sensor from the recorder, and
  nothing in Effy reports it any more (ADR-017's check and its repair issue were
  removed with TASK-0007b).

## Candidate causes to confirm or refute

1. **The old exclusion is still in the recorder configuration** (the glob of
   ADR-017, or a broader one such as `sensor.*`, or an `include` list that does
   not name the entity). Then no state and no statistic exist at all.
1. **The sensor was `unavailable` or unknown for most of its life** (the
   estimate needs a first counted drain interval; `available` is false before
   it), so the recorder holds no numeric state and compiles no mean.
1. **The state rarely changes.** The estimate moves only when an interval is
   counted (hours apart) and `async_write_ha_state` is called after every slot
   job, so Home Assistant records one state per real change; the history panel
   then shows one flat line that starts at the last change.
1. **Statistics are missing though states exist**: no state class, a unit
   change, or a statistics issue shown under Developer tools, Statistics.
1. **An entity registry mismatch**: an old registry entry with another entity ID
   or a disabled entry from an earlier installation, while the state is written
   under the fixed ID `sensor.effy_standby_power`.
1. **A defect in Effy's recording of the sensor** (what it writes into the state
   machine, or an interference with its own statistics writer, ADR-016).

## Acceptance Criteria

### Stage 1 — evaluation (no file under `custom_components/` changes)

- Given the human's instance, then the report states, with the query or the
  screen that showed it, for `sensor.effy_standby_power`:
  - whether `states` holds rows for it, and since when (the first and the last
    row, and how many);
  - whether `statistics_meta` has a row for it (source, unit, `has_mean`);
  - whether `statistics_short_term` and `statistics` hold rows with non-null
    `mean`, `min` and `max`, and from when to when;
  - the values of its state around a recorder restart and a Home Assistant
    restart (unavailable gaps).
- Given the recorder configuration (`configuration.yaml`, or the UI's recorder
  settings if any), then the report states whether any `exclude` or `include`
  rule matches `sensor.effy_standby_power` (and `sensor.effy_*` as a whole), and
  whether the other Effy sensors, the charge, discharge, battery and roundtrip
  efficiency sensors, are recorded.
- Given the causes above, then each is marked **confirmed**, **refuted** or
  **cannot be decided from the data**, with the evidence.
- Given the confirmed cause, then the report states whether it is
  - a **configuration** the human changes (and the README or a message in Effy
    should have prevented), or
  - a **defect** in Effy (the report describes the failing behaviour and the
    test that would pin it, and does not write the test), or
  - **expected behaviour** (a rarely changing sensor), where only the
    documentation changes.
- Given the finding, then the report states **for how many 5-minute slots of the
  last three days a standby statistic exists**, and what that does to the charge
  and discharge efficiency (a slot without one does not count). This number is
  handed to TASK-0034.
- Given that ADR-020 says the exclusion "may be removed", then the report
  evaluates whether that wording is enough, and which of these Stage 2 builds: a
  stronger README text ("must be removed or the standby sensor and the
  efficiencies stay empty"), a repair issue or a log warning that tells the user
  when `sensor.effy_standby_power` is excluded from the recorder (a different
  check from ADR-017's, which was about the race), or both.
- Given the report is written into this file, then Stage 2 starts.

### Stage 2 — fix (tests first, each shown failing against the code as it was)

- Given the cause is a **configuration** (a leftover exclusion or filter), then
  Effy makes it visible: a repair issue or, if the human has not agreed to a
  private-attribute check, a warning in the log at setup, that names the rule's
  effect ("`sensor.effy_standby_power` is not recorded: the standby history and
  the charge and discharge efficiency stay empty") and what to change; it is
  raised only while the condition holds and cleared when it no longer does (no
  stale issue after the user fixes it). The strings exist in
  `translations/en.json` and `de.json`. The README's "Recorder" section says the
  exclusion **must** be removed, and why, **once TASK-0035 is fixed**: with the
  exclusion removed the recorder's roll-up hit a duplicate hourly row written by
  Effy (human report 2026-10-30), so until then the README says the exclusion
  has to go for the standby history and the efficiencies, and points to
  TASK-0035 for the known log error and how to avoid it.
- Given the cause is a **defect** in what Effy writes or how it behaves (an
  unavailable state that never leaves, a missing state class, a registry
  mismatch it causes), then a test reproduces it first, and the fix makes the
  sensor recorded with a mean, a min and a max for every slot that has a state.
- Given the cause is **expected behaviour** (a rarely changing sensor), then the
  README says so in one sentence and what the history panel shows, and the
  sensor is checked to write a state at every slot job so the recorder has a
  value for every slot (ADR-018 §5); a test pins that.
- Given the fix, then the whole suite, `ruff`, `mypy` and `mdformat` pass, and
  no existing assertion is weakened.
- Given the fix is released, then the **human verifies on the instance** that
  `sensor.effy_standby_power` has states and 5-minute statistics after the next
  slot jobs (the queries under "Evidence"), and the result is written into the
  findings. Until then the task stays in `review`, not `done`.

## Evidence the human has to supply

- The `recorder:` section of `configuration.yaml` (and `packages/`), verbatim.

- For `sensor.effy_standby_power`: the history panel for the last 7 days; the
  Developer tools, Statistics page entry (any issue shown); the entity's
  settings page (is it enabled, which entity ID).

- If the human can read the database, rows for the entity (adjust the names to
  the schema of the running Home Assistant):

  ```sql
  SELECT COUNT(*), MIN(s.last_updated_ts), MAX(s.last_updated_ts)
    FROM states s JOIN states_meta m ON m.metadata_id = s.metadata_id
   WHERE m.entity_id = 'sensor.effy_standby_power';

  SELECT * FROM statistics_meta WHERE statistic_id = 'sensor.effy_standby_power';

  SELECT COUNT(*), MIN(start_ts), MAX(start_ts), COUNT(mean), COUNT(min), COUNT(max)
    FROM statistics_short_term t JOIN statistics_meta m ON m.id = t.metadata_id
   WHERE m.statistic_id = 'sensor.effy_standby_power';
  ```

- The log lines of the last restart that mention Effy, the recorder or
  `sensor.effy_standby_power`.

## Estimated File / Module Footprint (hint, not a commitment)

Stage 1: none in production; the report is written into this file. Stage 2,
depending on the cause: `__init__.py` or `coordinator.py` (the check, issue
creation and clearing), `const.py`, `translations/en.json` and `de.json`,
`sensor.py` (only for a defect), `README.md` ("Recorder"), `adr/020-*.md` (an
amendment), `tasks/adr-summary.md`, and new tests.

## Definition of Done

- The findings section below is complete: cause, classification (configuration,
  defect or expected), the slot count, what Stage 2 built
- Stage 2 tests were shown failing first; `pytest`, `ruff format --check`,
  `ruff check`, `mypy` and `mdformat --check` pass
- Docs updated (README, ADR-020 amendment, translations en and de) · no open ADR
  conflicts
- The human's verification on the instance is recorded
- `Delivered Artifacts` block completed and accurate
- No new external dependencies

## Consumed Interfaces

- `EffyStandbyPowerSensor` (`available`, `native_value`, state class
  measurement, device class power, unit W) in `custom_components/effy/sensor.py`
  (→ TASK-0005); read in Stage 1, changed in Stage 2 only if the finding is a
  defect.
- `async_read_efficiency_energy` in `custom_components/effy/history.py`, which
  reads the standby statistic of a slot (→ TASK-0020); read, not changed.

## Findings

The human removed the old `recorder: exclude: entity_globs: - sensor.effy_*`
block from `configuration.yaml` (2026-10-30) and the standby sensor is recorded
from then on: the cause is candidate 1, a configuration the human changed. The
evaluation of Stage 1 (queries, classification, slot count) was not carried out
on the human's word that the task is fixed; candidates 2 to 6 were not examined
and nothing contradicts the result.

Not built, because the human closed the task: the repair issue or log warning
that tells a user an exclusion hides `sensor.effy_*`, and the stronger README
wording ("must be removed"). The README still says the exclusion "may be
removed". The consequence of the missing warning is what this task found: with
the exclusion left in place the standby history and, through ADR-023 decision 3,
the charge and discharge efficiency stay empty, and nothing in Effy says so.

The removal exposed a different problem, tracked in
TASK-0035-hourly-statistic-written-before-recorder-rollup.

## Delivered Artifacts

None: no production code, test or document was changed by this task. The
configuration change was the human's.
