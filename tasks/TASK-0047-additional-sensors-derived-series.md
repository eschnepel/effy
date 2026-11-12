# Task: Additional sensors that get a derived power or smoothed series

- **Status:** done
- **Capability:** CAP-14
- **Related ADRs:** \[ADR-028, ADR-012, ADR-013, ADR-019, ADR-021, ADR-024,
  ADR-027\]
- **Dependencies:** []. It shares `sensor.py`, `catalog.py`, `history.py`,
  `coordinator.py`, `config_flow.py`, `README.md` and the translations with
  CAP-13, so it runs after the CAP-13 task that last touches them, one task at a
  time (see the order note in CAP-14).
- **Gate:** released 2026-11-12 by the human's instruction in chat ("Work on
  task 47"), as the project did for ADR-023: the instruction is Gate 2 for this
  task and accepts ADR-028. The text of ADR-028 and of this task was written by
  the Lead Agent from the human's answers of 2026-11-09 and has not been read
  line by line by the human. The three open points of Gate 1 are settled by the
  assumptions stated under them. Order: CAP-13's TASK-0046, TASK-0045f and
  TASK-0045g are still `todo`; this task runs before them at the human's
  instruction. TASK-0045e (the formula signature) is `done`, so this task makes
  the signature change.

## Goal

The user can name sensors that are **not** an input, an output, the EPS sensor,
the state of charge or the capacity of Effy, and Effy gives each of them the
same series treatment an input gets before the loss is distributed: a derived
power series for an energy-family sensor, a smoothed series for a power-family
sensor. They take no part in any loss, effective value, standby, efficiency or
battery calculation. The use case: part of Effy's logic (the redistribution of a
low-resolution counter, the gap smoothing, the idle zero-fill) applied to the
house's grid import sensor, which Effy does not otherwise use.

## Acceptance Criteria

- Given the config flow and the options flow, then there is one optional
  multi-entity picker **Additional sensors** (a list of any `sensor` entity; no
  device-class or state-class filter beyond the checks below). An entry without
  it changes nothing.

- Given an energy-family sensor in the list (`total_increasing` or `total`
  energy, Wh or kWh; `is_energy_family`), then Effy creates the derived power
  sensor `sensor.effy_<slug>_power` and writes its statistics exactly like the
  derived power series of an input: trapezoidal redistribution (ADR-012, the
  120-minute cap of ADR-014), the offline anchor and gap rules, the idle-sensor
  zero-fill (ADR-013: an idle counter gives an explicit 0, not a missing slot).
  Example: a 10 Wh-resolution grid import counter shows a smooth power in W
  instead of spikes.

- Given a power-family sensor in the list (W or kW, `measurement`), then Effy
  creates `sensor.effy_<slug>_smoothed` and writes its statistics like the
  smoothed series of an input: a gap of up to two slots is interpolated, a
  longer gap stays a gap (ADR-013 decision 2, `INTERPOLATION_MAX_GAP_SLOTS`).

- Given either kind, then **no effective sensor** is created for it, and it is
  in no formula: the inputs' loss, the waterfall, the loss and share attributes,
  the activity reading of standby and efficiency, the PV sums and the PV share
  (CAP-13) are the same with or without the additional sensors. A test pins
  this: the effective rows of an entry are identical with and without a
  grid-import sensor in the additional list.

- Given a sensor whose family is not known yet at startup, then the derived or
  smoothed entity is created late like for an input (ADR-013 decision 1, the
  one-shot state listener).

- Given the picker, then saving is **refused** (like the existing role checks in
  ADR-019) for a sensor that already is a PV input, a BMS counter, the EPS
  sensor, the state of charge sensor or the battery capacity entity, and for the
  same sensor listed twice; and a sensor chosen here is refused as any of those
  elsewhere. The message names the conflict. The sensor's unit, if it has one,
  must be W, kW, Wh or kWh; otherwise saving is refused (ADR-002).

- Given the slot job, the recent recalculation and the button, then all three
  produce the series, a slot whose value changed is rewritten and unchanged rows
  are not (ADR-024), the hourly row is the mean of its slots like every other
  derived series, the live value of the sensor is the last completed slot's
  (ADR-016) and it is restored after a restart. A full recalculation and the
  recent recalculation over the same slots give identical rows (the CAP-1
  equivalence test covers the new series).

- Given a sensor removed from the list, then its Effy sensor is removed from the
  entity registry; its statistics stay.

- Given the history of a newly added sensor, then it is empty until the history
  button runs (saving the options reloads the entry and starts no
  recalculation). The README says so. **When TASK-0045e's formula signature
  exists, the list of additional sensors is part of it**, so adding or removing
  one then starts the full recalculation by itself (human, 2026-11-09);
  whichever of the two tasks is done second makes that change, and the
  signature's other content is unchanged.

- Given the published list, then each additional sensor appears with `role`
  `other`, `direction` `unknown`, `function` `unknown` (the key is present, with
  the value `unknown`; human, 2026-11-09: the role of the sensor is not known to
  Effy), `series` `derived_power` or `smoothed`, `source_entity_id` the sensor,
  unit and aggregations as for an input's series. `schema_version` stays 1
  (additive: a new `role` value and the value `unknown` for `direction` and
  `function`); `effy_exports.py` gets `EFFY_DIRECTION_UNKNOWN`,
  `EFFY_FUNCTION_UNKNOWN` and the `role` value, and a copy of it still works
  without Effy installed. Existing contract tests pass unchanged.

- Given the sensor names, then the translations en and de name them like the
  inputs' derived and smoothed series; the config-flow labels and errors are
  translated en and de.

## Decisions

Human, 2026-11-09, in answer to the questions of this task:

1. One picker, one list of any energy or power sensor; Effy chooses derived
   power or smoothed by the sensor's family, as for inputs.
1. A sensor that already has a role is refused; no sensor gets two roles.
1. Idle zero-fill applies, as for an input (grid import is often idle).
1. The history comes from the button; with TASK-0045e the change recalculates by
   itself.
1. Option name "Additional sensors"; published with `role` `other`, `direction`
   `unknown`, `function` `unknown`, series `derived_power` / `smoothed` (first
   answered as `null`; the human changed both to `unknown` on 2026-11-09, "as
   this all is unknown").
1. New capability CAP-14 with its own ADR-028, independent of CAP-13.

## Open points for Gate 1 (settled 2026-11-12 by the assumptions stated here; the human can overrule)

- Whether an additional **power-family** sensor also gets its own series when it
  needs none (a clean `measurement` sensor with no gaps still gets the smoothed
  series; assumed yes, like an input).
- `direction` and `function` `unknown` are new values of existing contract keys
  (a consumer that picks by `function` or `direction` never selects them);
  ADR-021's amendment states it and the README's contract table lists it.
- Cost: every additional sensor adds one more recorder read per run. ADR-025's
  cache holds it; no limit on the list length is set (assumed none).

## Estimated File / Module Footprint (hint, not a commitment)

`config_flow.py`, `const.py`, `inputs.py` (the list, the role conflicts),
`history.py` (the slot loop builds the derived and smoothed series without
passing them to the waterfall), `sensor.py` (`_build_derived_entities`, the late
listener, registry removal), `catalog.py`, `effy_exports.py`, `coordinator.py`,
`translations/en.json`, `translations/de.json`, `README.md`, and tests for each.

## Definition of Done

- Tests green (`pytest`, zero-mock for pure code, the in-memory recorder world
  of `tests/test_history_recent_equivalence.py` for recorder behaviour) ·
  `ruff format` clean · `mypy --strict` and `ruff check` show no finding the
  repository did not already have
- Docs updated where this task changes text (README configuration table, sensors
  section and contract table; translations en and de) · no open ADR conflicts
- `Delivered Artifacts` block completed and accurate
- No new external dependencies (standard library only); `tasks/DEPENDENCIES.md`
  unchanged

## Consumed Interfaces

<!-- Filled by the Lead Agent BEFORE implementation. Symbols marked (→ task: …)
     are filled from that task's Delivered Artifacts once it is done; the rest
     exist already (checked 2026-11-09). -->

- `sensor._build_derived_entities`, `sensor._add_late_derived_entities`,
  `sensor.EffyDerivedPowerSensor`, `sensor.EffySmoothedSensor`,
  `sensor.async_setup_entry` in `custom_components/effy/sensor.py`.
- `history._compute_effective_slots`, `history.async_recalculate_history`,
  `history.async_recalculate_recent` in `custom_components/effy/history.py`;
  `calculation.trapezoidal_slot_contributions`,
  `calculation.interpolate_slot_gaps`.
- `inputs` (the role lists and their conflict checks), the options-flow
  validation of ADR-019.
- `catalog.describe_source_series`, `catalog.published_attributes`,
  `effy_exports.EffySeries`, `coordinator.register_published`.
- `tests/test_history_recent_equivalence.py` (`_World`, `_install`,
  `_run_full_recalc`, `_equivalent`).
- The formula signature (→ task:
  TASK-0045e-formula-signature-and-implicit-recalc, done, so this task makes the
  change): `inputs.formula_signature(options) -> dict` in
  `custom_components/effy/inputs.py`, a dict keyed by option constants (the PV
  inputs, the four counters, the EPS sensor); add one key for the sorted list of
  additional sensors (keyed by the option constant, so that the guard
  `tests/test_effy_exports.py` keeps passing). `inputs.signature_pv_inputs`
  reads only the PV list and stays as it is. `__init__.async_setup_entry`
  compares the signature with `entry.data[const.CONF_FORMULA_SIGNATURE]` and
  starts `coordinator.async_run_formula_recalculation(signature)` as a
  background task; nothing else needs to change. An entry that stored the old
  signature differs from the new one, so the first start after this task
  recalculates once more. Tests: `tests/test_formula_signature.py`,
  `tests/test_formula_recalc_trigger.py`.

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- `custom_components/effy/const.py` →
  `CONF_ADDITIONAL_SENSORS = "additional_sensors"`,
  `ADDITIONAL_UNITS = ("W", "kW", "Wh", "kWh")`,
  `ADDITIONAL_UNIQUE_ID_MARKER = "additional-"`.
- `custom_components/effy/inputs.py` → `additional_ids(options) -> list[str]`
  (saved order, each once, a sensor that has a role dropped);
  `formula_signature` gains the key `CONF_ADDITIONAL_SENSORS` with the sorted
  list. `signature_pv_inputs` unchanged.
- `custom_components/effy/config_flow.py` →
  `_build_schema(..., additional_sensors=None)` (optional multi-entity picker,
  suggested value), `_validate_additional_option(hass, user_input)`, role
  conflict and duplicate check inside `_validate_user_input` (error
  `sensor_in_two_roles` on `additional_sensors`), new error key
  `additional_unit_not_supported`; `_normalised` saves the list only when it is
  not empty.
- `custom_components/effy/history.py` →
  `_additional_series(extra_ids, energy_ids, indexed, state_classes, units, smoothed_series, start, end)`;
  `_compute_effective_slots` reads, converts and smooths the additional sensors
  with the inputs, leaves them out of the slot union and the slot loop, and adds
  their series to the 2nd and 3rd result. Nothing else in `history.py` changed:
  the writers, ADR-024 and the live push take the series as they are.
- `custom_components/effy/coordinator.py` → `EffyCoordinator.additional_ids`
  (property, a copy).
- `custom_components/effy/catalog.py` →
  `describe_source_series(..., additional_ids=())`: `role` `other`, `direction`
  `unknown`, `function` `unknown` for an additional sensor.
- `custom_components/effy/effy_exports.py` → `EFFY_ROLE_OTHER = "other"`,
  `EFFY_DIRECTION_UNKNOWN = "unknown"`, `EFFY_FUNCTION_UNKNOWN = "unknown"`; the
  three `Literal` types gain the value. `EFFY_PUBLISHED_SCHEMA_VERSION` stays 1.
- `custom_components/effy/sensor.py` →
  `EffyDerivedPowerSensor(..., additional: bool = False)` and
  `EffySmoothedSensor(..., additional: bool = False)` (unique ID
  `<entry_id>_additional-<slug>_power|_smoothed` when set);
  `_build_derived_entities(..., additional=frozenset())`;
  `_remove_dropped_additional_entities(hass, entry, additional)`;
  `_add_late_derived_entities(..., additional=None)`; `_source_descriptor`
  passes `coordinator.additional_ids`. No effective sensor is built for an
  additional sensor.
- `custom_components/effy/translations/en.json`, `de.json` → label
  `additional_sensors` in the user and init steps, error
  `additional_unit_not_supported` in both, a sentence in both step descriptions.
- Docs: `README.md` (configuration table and paragraph, contract table and a
  bullet, ADR table row 028), `adr/021` Amendment 2026-11-12, `adr/028` accepted
  and Amendment 2026-11-12 (implementation).
- Tests: `tests/test_additional_sensors_options.py`,
  `tests/test_additional_sensors_series.py`,
  `tests/test_additional_sensors_entities.py` (new); added to
  `tests/test_config_flow.py`, `tests/test_formula_recalc_trigger.py`,
  `tests/test_coordinator_slot.py`; updated `tests/test_effy_exports.py` (pinned
  values), `tests/test_formula_signature.py` (seventh key),
  `tests/test_published_sensors.py` (fake coordinator accessor).
- External dependencies added: none (`tasks/DEPENDENCIES.md` unchanged).
