# Task: The typed published contract, and every sensor in it

- **Status:** todo
- **Capability:** CAP-13 (re-scoped 2026-11-13: the contract rework of ADR-029
  and the publication of CAP-13 and CAP-15 sensors)
- **Related ADRs:** [ADR-027, ADR-021, ADR-029]
- **Dependencies:** \[TASK-0045a-pv-combined-series,
  TASK-0045d-pv-delta-integral-sensors, TASK-0046-pv-share-sensor,
  TASK-0048-household-meter-inputs,
  TASK-0049-household-consumption-power-sensor,
  TASK-0050-household-consumption-energy-sensors\]. The CAP-15 tasks create the
  sensors and do **not** publish them; this task publishes all of them under one
  model. TASK-0047 is `done`.
- **Gate:** Gate 2 approved by the human (2026-11-09, with Q27 = A); re-scoped
  in a design interview on 2026-11-07, split from the held TASK-0045; re-scoped
  again by the human on 2026-11-13 ("publishing is part of the rework"); ADR-029
  accepted by the human the same day.

## Goal

The published contract is stable before anyone reads it: every Effy sensor is
described with a **quantity**, a **scope** and a **span** as well as its series,
role, direction and function, and `effy_exports.py` carries the symbols. No
other project reads the contract yet, so the rework breaks version 1 and raises
`schema_version` to 2.

## Acceptance Criteria

- Given the attribute `sensors`, then it is a list of **groups**, one per
  distinct combination of `series`, `quantity` (`power`, `energy`, `efficiency`,
  `share`), `scope` (`source`, `combined`, `system`), `span` (`slot`, `today`,
  `total`), `role`, `direction`, `function` and `aggregations`; each group has
  the key `sensors`, the sorted list of the Effy entity IDs with exactly these
  attributes. Groups are sorted by the attribute values in the order above. The
  key `source_entity_id` is gone. An example group:

  ```yaml
    - series: effective
      quantity: power
      scope: source
      span: slot
      role: pv
      direction: input
      function: pv
      aggregations: [mean, state]
      sensors: [sensor.effy_pv1, sensor.effy_pv2]
  ```

- Given the sources, then the sensor has a second attribute `sources`, a map
  keyed by each configured source sensor (a PV input, a BMS counter, the EPS
  sensor, an additional sensor, a household meter sensor) whose value is the
  sorted list of every Effy entity ID that depends on it: its effective, derived
  and smoothed series and, for a PV input, its PV energy sensor. Effy sensors of
  scope `combined` or `system` are in no list. A source with no Effy sensor has
  no key. Example:

  ```yaml
  sources:
    sensor.pv1: [sensor.effy_pv1, sensor.effy_pv1_delta_integral_today]
    sensor.pv2: [sensor.effy_pv2, sensor.effy_pv2_delta_integral_today]
  ```

  `function` is present in **every** group. `series` values: `effective`,
  `derived`, `smoothed`, `standby`, `charge`, `discharge`, `battery`,
  `roundtrip`, `pv`, `consumption` (`derived_power` → `derived`, `standby_power`
  → `standby`, the four efficiencies lose `_efficiency`, `pv_share` → `pv`; no
  `pv_sum`, `pv_delta_integral*`: the words delta, integral, sum and deltasum
  stay out of the contract).

- Given the published list, then its groups are made from these entries
  (ADR-029; entity IDs unchanged; a sensor that is not configured has no entity
  in a group, and a group without a sensor does not exist):

  | Entry | series | quantity | scope | span | role | direction | function |
  | -- | -- | -- | -- | -- | -- | -- | -- |
  | PV input | effective / derived / smoothed | power | source | slot | pv | input | pv |
  | Grid import counter | effective / derived / smoothed | power | source | slot | bms | input | grid_import |
  | Discharge counter | effective / derived / smoothed | power | source | slot | bms | input | discharge |
  | Charge counter | derived / smoothed | power | source | slot | bms | output | charge |
  | Grid export counter | derived / smoothed | power | source | slot | bms | output | grid_export |
  | EPS sensor, input half | effective | power | source | slot | eps | input | eps |
  | EPS sensor, signed | smoothed | power | source | slot | eps | both | eps |
  | Additional sensor | derived / smoothed | power | source | slot | other | unknown | unknown |
  | Standby power | standby | power | system | slot | bms | output | standby |
  | Charge efficiency | charge | efficiency | system | slot | bms | output | efficiency |
  | Discharge efficiency | discharge | efficiency | system | slot | bms | output | efficiency |
  | Battery efficiency | battery | efficiency | system | slot | bms | output | efficiency |
  | Roundtrip efficiency | roundtrip | efficiency | system | slot | bms | output | efficiency |
  | Effective PV Sum now | effective | power | combined | slot | pv | input | pv |
  | PV energy today, per PV input | effective | energy | source | today | pv | input | pv |
  | PV energy today, all PV | effective | energy | combined | today | pv | input | pv |
  | PV energy total, all PV | effective | energy | combined | total | pv | input | pv |
  | PV share now | pv | share | system | slot | pv | input | share |
  | Household import meter | derived / smoothed | power | source | slot | household | input (`both` if signed) | household_import |
  | Household export meter | derived / smoothed | power | source | slot | household | output | household_export |
  | Household consumption now | consumption | power | system | slot | household | output | consumption |
  | Household consumption today | consumption | energy | system | today | household | output | consumption |
  | Household consumption total | consumption | energy | system | total | household | output | consumption |

  `sources` (see above) lists, for the per-input PV energy sensor too, the PV
  input it depends on. Aggregations: `mean`, `state` for the series Effy writes;
  `mean`, `min`, `max` for standby and the efficiencies (as now); `state`,
  `sum`, `change` for every `energy` entry. The household import meter's
  direction is `both` exactly when its series is signed (a power-family sensor
  with no household export sensor).

- Given `schema_version`, then it is **2**. The existing contract tests are
  rewritten for the new keys and values; the README's contract section and
  `GLOSSARY.md` (quantity, scope, span) are the documentation of record.

- Given `effy_exports.py`, then it has the new keys (`EFFY_KEY_SENSORS`,
  `EFFY_KEY_QUANTITY`, `EFFY_KEY_SCOPE`, `EFFY_KEY_SPAN`), the attribute
  `EFFY_ATTR_SOURCES`, the new values of `series`, `function` and `role`, the
  `quantity`, `scope`, `span` constants and `Literal` types, a TypedDict for a
  group in place of `EffyPublishedSensor` (the keys `entity_id` and
  `source_entity_id` are gone), the `change` aggregation, a set of the symbols
  whose guards in `tests/test_effy_exports.py` pin the values; every symbol
  keeps the `EFFY_` / `Effy` prefix, and a copy of it still works without Effy
  installed. The literals of `const.py` that name a published value (for
  instance the PV sum) are replaced by imports from `effy_exports.py`.

- Given a sensor that is removed from the registry, then its entity ID leaves
  its group and every `sources` list, a group left with no sensor leaves the
  list, and a source left with no sensor leaves the map.

- Given the size of the attribute (Home Assistant stores no attribute set above
  16 KiB, README "Size"), then grouping shortens it: the attributes of a group
  are written once. The README's number of sources that fit is re-measured and
  restated.

- Given an old stored attribute set restored after the upgrade (ADR-021's
  restore), then the sensor still comes up; the new keys are there after the
  next recalculation.

## Decisions

- ADR-027 decision 7 and the amendment of ADR-021 (human, 2026-11-07), as
  reworked by ADR-029 (human, 2026-11-13): orthogonal keys `quantity`, `scope`,
  `span`; the attribute `sensors` keyed by those attributes (groups with a list
  of entity IDs, instead of one dict per sensor); series renamed so they never
  repeat the quantity; delta, integral, sum and deltasum are not contract words;
  entity IDs unchanged; `function` on every group, its value the `series` for
  the entries that had no key (`consumption`), `pv` for the PV sum and the PV
  energy sensors (the human, 2026-11-13); PV share is role `pv`, direction
  `input`, function `share`; a PV input's function is `pv` (was `null`);
  `schema_version` 2; the key for the time span is `span`, not `period` (the
  glossary reserves "period" against slot).
- The attribute `sources`, keyed by the source sensor and listing the Effy
  sensors that depend on it, replaces `source_entity_id` (the human,
  2026-11-13).

## Estimated File / Module Footprint (hint, not a commitment)

`catalog.py` (the describe functions take `quantity`, `scope`, `span`),
`effy_exports.py`, `sensor.py` (registering every descriptor, including the PV
sum, the PV energy sensors, the PV share, the household entities of TASK-0048 to
TASK-0050), `const.py`, `coordinator.py`, `README.md` (the contract table and
the YAML example), `adr/021` (amendment), and tests for each (`test_catalog`,
`test_effy_exports`, `test_published_sensors`).

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (translations en and de for sensor
  names) · no open ADR conflicts (ADR-027 decision 9 decided: Q27 = A)
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-07). -->

- `catalog.describe_source_series`, `catalog.describe_efficiency`,
  `catalog.describe_standby`, `catalog.published_attributes`.
- `effy_exports.EffySeries`, `EFFY_AGGREGATIONS`,
  `EFFY_EFFICIENCY_AGGREGATIONS`, `EFFY_PUBLISHED_SCHEMA_VERSION`.
- `coordinator.register_published`, `coordinator.published_descriptors`.
- The sensor `sensor.EffyPvSumSensor` (`const.PV_SUM_ENTITY_ID`, unique ID
  `sensor.pv_sum_unique_id(entry)`, W, `measurement`; not registered with
  `register_published` yet) (→ task: TASK-0045a-pv-combined-series, done).
  Replace the literal in `const.py` by an import from `effy_exports.py`.
- The sensors of TASK-0045d-pv-delta-integral-sensors (→ task:
  TASK-0045d-pv-delta-integral-sensors, done):
  `sensor.EffyPvDeltaIntegralSensor(hass, entry, coordinator, kind, source_entity_id=None)`
  in three kinds: per PV input (entity ID
  `sensor.effy_<slug>_delta_integral_today`, unique ID
  `<entry_id>_<slug>_delta_integral_today`, Wh or kWh, `_source_entity_id` the
  input), combined today (`const.PV_DELTA_TODAY_ENTITY_ID` =
  `sensor.effy_pv_deltasum_integral_today`, unique ID suffix
  `const.PV_DELTA_TODAY_UNIQUE_ID_SUFFIX`) and combined total
  (`const.PV_DELTA_TOTAL_ENTITY_ID`, `const.PV_DELTA_TOTAL_UNIQUE_ID_SUFFIX`),
  kWh; all energy, state class `total`; created by `sensor._pv_delta_entities`;
  **not registered with `register_published` yet**. Their published `series` is
  now `effective` with `quantity` `energy` (ADR-029), although the combined
  entity IDs read `deltasum` (ADR-027 Amendment 2026-11-10; the IDs stay); the
  published `statistic_id` of an entry is the sensor's entity ID.
- The sensors of CAP-15, created and **not** published by their tasks (→ tasks:
  TASK-0048-household-meter-inputs (done),
  TASK-0049-household-consumption-power-sensor (done),
  TASK-0050-household-consumption-energy-sensors, `todo`): the household meter
  series entities, `sensor.effy_household_consumption_now`, `_today` and
  `_total` (`sensor.EffyHouseholdConsumptionSensor` of TASK-0049, statistic ID
  `const.HOUSEHOLD_CONSUMPTION_ENTITY_ID`, quantity power, function
  `consumption`, direction `output`). The household meter entities of TASK-0048
  are `sensor.EffyDerivedPowerSensor` / `sensor.EffySmoothedSensor` built with
  `household=True` (unique ID `<entry_id>_household-<slug>_power|_smoothed`,
  entity ID `sensor.effy_<slug>_power|_smoothed`, `_source_entity_id` the meter
  sensor); with `household=True` they skip `register_published`, so **this task
  removes that guard** and gives `catalog.describe_source_series` a household
  branch (it raises `ValueError` for a household sensor today). Which sensors:
  `coordinator.household_import_id`, `.household_export_id`; the import series
  is signed, direction `both`, exactly when
  `inputs.household_import_is_signed(options)` and its series is the smoothed
  one.
- `sensor.EffyPvShareSensor` and the additional sensors' descriptor path
  (`sensor._source_descriptor`,
  `catalog.describe_source_series(..., additional_ids=...)`) (→ tasks:
  TASK-0046-pv-share-sensor, TASK-0047, done).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
