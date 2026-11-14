# Task: Household meter inputs and their series

- **Status:** done
- **Capability:** CAP-15
- **Related ADRs:** \[ADR-029, ADR-028, ADR-019, ADR-012, ADR-013, ADR-014,
  ADR-024, ADR-026, ADR-027\]
- **Dependencies:** []. TASK-0047 and TASK-0046 are `done`. It shares
  `config_flow.py`, `inputs.py`, `history.py`, `coordinator.py`, `sensor.py` and
  the translations with TASK-0049 and TASK-0050, so the three run one after the
  other: 0048, 0049, 0050.
- **Gate:** ADR-029 accepted by the human on 2026-11-13 ("ADR is accepted").
  Released for work by the human on 2026-11-14 ("work on effy task 48").
  Requested by the human on 2026-11-13; design questions answered in chat the
  same day.

## Goal

The user can name the **household meter** of the house: a **Household import**
sensor and an optional **Household export** sensor. Effy reads them, gives each
the series an input gets before the loss is distributed (derived power for an
energy-family sensor, smoothed for a power-family one), and keeps them out of
every loss, effective, standby, efficiency and battery calculation. This task
does **not** publish the series; TASK-0045f does.

## Acceptance Criteria

- Given the config flow and the options flow, then there are two optional
  single-entity pickers, **Household import** and **Household export** (a
  `sensor` entity; no device-class filter beyond the checks below). An entry
  without them changes nothing.

- Given the pickers, then saving is **refused**, with a message that names the
  conflict, for a sensor that already is a PV input, a BMS counter, the EPS
  sensor, the state of charge sensor, the battery capacity entity or an
  additional sensor; for the same sensor chosen as both household import and
  household export; and for a household export without a household import. A
  sensor chosen here is refused as any of those elsewhere (ADR-019's rule). A
  unit, if the sensor has one, must be W, kW, Wh or kWh (ADR-002); otherwise
  saving is refused. A sensor without a unit or without a state is accepted, as
  for the additional sensors.

- Given an energy-family household sensor, then Effy computes its derived power
  series; given a power-family one, its smoothed series, by the same code and
  rules as for an input (ADR-012, ADR-013, ADR-014: trapezoidal redistribution,
  the 120-minute cap, the offline anchor, idle zero-fill; ADR-013 decision 2: a
  gap of up to two slots is bridged). Example: a 10 Wh-resolution household
  import counter gives a smooth power in W.

- Given a household sensor whose family is not known yet at startup, then its
  series is created late like an input's (ADR-013 decision 1).

- Given a power-family household import sensor with no household export sensor,
  then the series is **signed** (a negative reading is not clamped; the
  smoothing runs on the signed values like the EPS sensor's), and its published
  direction will be `both` (TASK-0045f). Given a household export sensor, then
  neither series is signed: a negative reading of either counts as 0 where the
  series is used (TASK-0049). A counter with no export sensor gives an export of
  0\.

- Given the household series, then they are in no loss, effective, waterfall,
  share, PV, standby, efficiency or battery calculation, and not in the
  waterfall's slot union: the effective rows of an entry are identical with and
  without them. A test pins this with the pattern of TASK-0047.

- Given the slot job, the recent recalculation and the button, then all three
  produce the series with the writers of ADR-024 as for any series; unchanged
  rows are not written, the live value is the last completed slot's (ADR-016)
  and is restored after a restart. Each series has an Effy sensor, as an
  additional sensor's has (ADR-028): `sensor.effy_<slug>_power` (energy-family
  source) or `sensor.effy_<slug>_smoothed` (power-family source), unique ID
  `<entry_id>_household-<slug>_power|_smoothed` (the marker `household-`
  alongside ADR-028's `additional-`, so that the registry cleanup of a sensor
  taken out finds exactly the household entities). A sensor taken out of the
  options leaves the entity registry; its statistics stay. The entities are
  **not** registered with `register_published` here; TASK-0045f does.

- Given the formula signature (ADR-027 decision 6), then both household sensors
  are part of it under their option keys, so a change recalculates by itself.
  `inputs.signature_pv_inputs` is unchanged; an entry that stored the earlier
  signature recalculates once more.

- Given the translations en and de, then the labels, the errors and a sentence
  in the step descriptions are translated. The README's configuration table
  names the two options.

## Decisions

Human, 2026-11-13, in answer to the questions of this task:

1. The house meter is a different measurement point from the battery system's
   grid import and export counters; new terms in `GLOSSARY.md` (household meter,
   household import, household export).
1. Two pickers. A power-family household import sensor with no export sensor is
   signed: the negative part is export.
1. The two sensors' series have Effy sensors and are published in the contract
   (TASK-0045f publishes them).
1. A sensor that is already in the additional sensors list is refused as
   household import or export; the user removes it from the list first.

## Open points for Gate 1 (assumed here; the human can overrule; implemented as assumed on 2026-11-14)

- A household export sensor needs a household import sensor (assumed; the human:
  "import required to enable the sensor").
- A sensor listed as additional by a hand-edited entry and also chosen here:
  `inputs` drops it from the additional list at read time, as ADR-028's
  amendment does for any role.

## Estimated File / Module Footprint (hint, not a commitment)

`const.py` (option keys, `HOUSEHOLD_UNIQUE_ID_MARKER`), `config_flow.py`,
`inputs.py` (`household_import_id`, `household_export_id`, the role conflict
checks, `formula_signature`), `history.py` (read, convert and smooth like
`_additional_series` of TASK-0047; keep them out of the slot union), `sensor.py`
(the late listener and the registry cleanup if entities are created),
`coordinator.py` (accessors), `translations/en.json`, `translations/de.json`,
`README.md` (the configuration table), and tests for each.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (README configuration table;
  translations en and de) · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-13). -->

- `history._additional_series`, `history._compute_effective_slots` (reads,
  converts and smooths the additional sensors with the inputs and leaves them
  out of the slot union) (→ task: TASK-0047, done).
- `inputs.additional_ids`, `inputs.formula_signature`, `inputs.eps_id`,
  `inputs.pv_input_ids`; `config_flow._validate_user_input` and the role
  conflict check (`sensor_in_two_roles`), `_validate_additional_option`.
- `calculation.split_eps_readings` (the signed split TASK-0049 reuses for the
  signed import sensor).
- `sensor._build_derived_entities`, `sensor._add_late_derived_entities`,
  `sensor._remove_dropped_additional_entities`.
- `tests/test_additional_sensors_*.py`, `tests/test_formula_signature.py`,
  `tests/test_history_recent_equivalence.py`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- `custom_components/effy/const.py` →
  `CONF_HOUSEHOLD_IMPORT_SENSOR = "household_import_sensor"`,
  `CONF_HOUSEHOLD_EXPORT_SENSOR = "household_export_sensor"`,
  `HOUSEHOLD_UNITS = ADDITIONAL_UNITS` (`("W", "kW", "Wh", "kWh")`),
  `HOUSEHOLD_UNIQUE_ID_MARKER = "household-"`.
- `custom_components/effy/inputs.py` →
  `household_import_id(options) -> str | None`,
  `household_export_id(options) -> str | None` (`None` without a valid import),
  `household_ids(options) -> list[str]` (import first),
  `household_import_is_signed(options) -> bool` (true with an import and no
  valid export: the smoothed power-family import series is then read with its
  sign, TASK-0049 splits it and TASK-0045f gives it the direction `both`; the
  series written is the measured one either way),
  `series_only_ids(options) -> list[str]` (additional first, then household: the
  sensors that get the series of an input and are in no formula);
  `additional_ids` now also drops a household sensor; `formula_signature` gains
  the keys `CONF_HOUSEHOLD_IMPORT_SENSOR` and `CONF_HOUSEHOLD_EXPORT_SENSOR`
  (`None` when unset). `signature_pv_inputs` unchanged.
- `custom_components/effy/config_flow.py` →
  `_build_schema(..., household_import_sensor=None, household_export_sensor=None)`
  (two optional single-entity `sensor` pickers, suggested value, no device-class
  filter), `_HOUSEHOLD_KEYS`, `_validate_household_option(hass, user_input)`,
  household role-conflict check inside `_validate_user_input` (error
  `sensor_in_two_roles` on the household picker, after the additional list), new
  error keys `household_export_needs_import` (on the export picker) and
  `household_unit_not_supported`; `_normalised` saves each only when set.
- `custom_components/effy/history.py` → `_compute_effective_slots` reads,
  converts and smooths `series_only_ids(entry_options)` (was `additional_ids`);
  `_additional_series` unchanged. Nothing else in `history.py` changed.
- `custom_components/effy/coordinator.py` →
  `EffyCoordinator.household_import_id`, `.household_export_id`,
  `.household_ids` (properties; `household_ids` a copy).
- `custom_components/effy/sensor.py` →
  `EffyDerivedPowerSensor(..., additional: bool = False, household: bool = False)`
  and `EffySmoothedSensor(..., additional=False, household=False)` (unique ID
  `<entry_id>_household-<slug>_power|_smoothed` when `household`; a household
  entity is **not** registered with `register_published`);
  `_build_derived_entities(..., additional=frozenset(), household=frozenset())`,
  `_add_late_derived_entities(..., additional=None, household=None)`,
  `_remove_dropped_marked_entities(hass, entry, marker, sources)` (new, shared),
  `_remove_dropped_additional_entities` (now delegates; unchanged behaviour),
  `_remove_dropped_household_entities(hass, entry, household)` (new). No
  effective sensor is built for a household sensor.
- `custom_components/effy/translations/en.json`, `de.json` → labels
  `household_import_sensor` and `household_export_sensor` in the user and init
  steps, errors `household_export_needs_import` and
  `household_unit_not_supported` in both, a sentence in both step descriptions.
- Docs: `README.md` (configuration table, a paragraph, ADR table row 029),
  `adr/029` Amendment 2026-11-14 (implementation), `tasks/adr-summary.md`.
- Tests: `tests/test_household_inputs_options.py`,
  `tests/test_household_inputs_series.py`,
  `tests/test_household_inputs_entities.py` (new); added to
  `tests/test_config_flow.py`, `tests/test_coordinator_slot.py`; updated
  `tests/test_formula_signature.py` and
  `tests/test_additional_sensors_options.py` (the signature's key set),
  `tests/test_published_sensors.py` (fake coordinator accessors).
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged).

## Review

Reviewer pass (2026-11-14, run inline by the Lead Agent after the worker pass,
as no sub-agent was available): **PASS.** Acceptance criteria checked one by one
against the code and the tests; the symbols above exist as declared; the
Consumed Interfaces are used as declared; the refactored registry cleanup is
logic-identical to ADR-028's; no dependency changed. Mutation checks (history
ignoring the household sensors, the household sensors read as inputs, the
published registration guard removed, the cleanup marker swapped) each fail the
tests meant to catch them. Not run against a real Home Assistant (see the
amendment's known limit).
