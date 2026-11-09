# Task: Additional sensors that get a derived power or smoothed series

- **Status:** todo
- **Capability:** CAP-14
- **Related ADRs:** [ADR-028 (Draft), ADR-012, ADR-013, ADR-019, ADR-021,
  ADR-024, ADR-027]
- **Dependencies:** []. It shares `sensor.py`, `catalog.py`, `history.py`,
  `coordinator.py`, `config_flow.py`, `README.md` and the translations with
  CAP-13, so it runs after the CAP-13 task that last touches them, one task at a
  time (see the order note in CAP-14).
- **Gate:** Gate 1 (CAP-14, ADR-028) and Gate 2 pending (human request
  2026-11-09, design questions answered the same day). No code before approval.

## Goal

The user can name sensors that are **not** an input, an output, the EPS sensor,
the state of charge or the capacity of Effy, and Effy gives each of them the same
series treatment an input gets before the loss is distributed: a derived power
series for an energy-family sensor, a smoothed series for a power-family sensor.
They take no part in any loss, effective value, standby, efficiency or battery
calculation. The use case: part of Effy's logic (the redistribution of a
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
  smoothed series of an input: a gap of up to two slots is interpolated, a longer
  gap stays a gap (ADR-013 decision 2, `INTERPOLATION_MAX_GAP_SLOTS`).

- Given either kind, then **no effective sensor** is created for it, and it is in
  no formula: the inputs' loss, the waterfall, the loss and share attributes, the
  activity reading of standby and efficiency, the PV sums and the PV share
  (CAP-13) are the same with or without the additional sensors. A test pins this:
  the effective rows of an entry are identical with and without a grid-import
  sensor in the additional list.

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
  button runs (saving the options reloads the entry and starts no recalculation).
  The README says so. **When TASK-0045e's formula signature exists, the list of
  additional sensors is part of it**, so adding or removing one then starts the
  full recalculation by itself (human, 2026-11-09); whichever of the two tasks is
  done second makes that change, and the signature's other content is unchanged.

- Given the published list, then each additional sensor appears with `role`
  `other`, `direction` `unknown`, `function` `unknown` (the key is present, with
  the value `unknown`; human, 2026-11-09: the role of the sensor is not known to
  Effy), `series` `derived_power` or `smoothed`,
  `source_entity_id` the sensor, unit and aggregations as for an input's series.
  `schema_version` stays 1 (additive: a new `role` value and the value `unknown`
  for `direction` and `function`); `effy_exports.py` gets `EFFY_DIRECTION_UNKNOWN`,
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
   `unknown`, `function` `unknown`, series `derived_power` / `smoothed`
   (first answered as `null`; the human changed both to `unknown` on
   2026-11-09, "as this all is unknown").
1. New capability CAP-14 with its own ADR-028, independent of CAP-13.

## Open points for Gate 1 (the Lead resolves them with the human)

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
- The formula signature (→ task: TASK-0045e-formula-signature-and-implicit-recalc,
  only if it is done first).

## Delivered Artifacts

<!-- Filled by the Worker AFTER implementation. Be exact — downstream tasks
     depend on this information. -->

- _(open)_
