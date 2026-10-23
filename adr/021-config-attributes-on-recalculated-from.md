# ADR-021 – Effy's Configuration Published as Attributes of the Recalculated-From Sensor

**Date:** 2026-10-18 **Status:** Accepted 2026-10-18 (human, Gate 2) — every
decision below was taken by the human in four grill rounds on 2026-10-18. Terms
follow `GLOSSARY.md`.

---

## Context

Effy's sibling projects Shady and Chargy depend on Effy only through entity
state: neither imports Effy, and both say they do not require it. Today the
person configuring them picks the matching `sensor.effy_*` entities by hand and
repeats a history length that Effy already knows. After a reboot, every Effy
sensor except the standby power sensor (a restore entity since ADR-018) is
`unknown` until the first slot job, because none of them restores its state:

- **Shady** picks each string's actual-yield entity from all sensors (its
  ADR-010) and has a training window of 28 days by default, a default copied
  from Effy's. The effective series of the PV inputs are exactly the ready-made
  actual-yield input its README names.
- **Chargy** picks the charge and discharge counters and the standby power
  sensor, and has a `window_days` of 28 by default. `sensor.effy_standby_power`
  is the sensor it would pre-select.
- **Chargy** reads counters as statistics `sum` (its ADR-0005 §2) and allows
  Effy's sensors as counters (its ADR-0004). Effy writes `mean` and `state`
  only, never `sum` (ADR-003). A consumer therefore needs to know which
  aggregations a series offers.

`sensor.effy_recalculated_from` is the one Effy sensor with a fixed entity ID
and exactly one instance per entry (ADR-012). Both recalculation paths, the slot
job and the manual history rewrite, push it through
`EffyCoordinator.set_recalculated_from`. Today that push is **not** the last
step: the slot job pushes it first, then the live states (`notify_updated`,
ADR-016), then rewrites the hour's long-term row (ADR-011 Amendment 2026-10-16)
and updates the standby power (ADR-018). A listener woken by the push would see
a recalculation that is not finished. Neither consumer listens to the sensor
today.

## Decision

1. **Attributes.** `sensor.effy_recalculated_from` carries three attributes
   besides its state:

   | Attribute | Content |
   | -- | -- |
   | `schema_version` | integer, `1`; raised only for a change that breaks existing keys or meanings (additive keys do not raise it) |
   | `max_history_days` | the configured value of the option of the same name, unchanged (glossary: **History days**) |
   | `sensors` | list of dicts, one per Effy sensor (below) |

   ```yaml
   max_history_days: 28
   schema_version: 1
   sensors:
     - entity_id: sensor.effy_pv_south
       series: effective
       source_entity_id: sensor.pv_south
       direction: input
       role: pv
       aggregations: [mean, state]
     - entity_id: sensor.effy_bms_bat_charge_power
       series: derived_power
       source_entity_id: sensor.bms_bat_charge
       direction: output
       role: bms
       aggregations: [mean, state]
     - entity_id: sensor.effy_standby_power
       series: standby_power
       source_entity_id:
       direction: output
       role: bms
       aggregations: [mean, min, max]
   ```

1. **Which Effy sensors are listed.** Every Effy sensor that exists: the
   effective series (inputs), the derived power series (energy-family inputs and
   outputs), the smoothed series (power-family inputs) and the standby power
   sensor (only with a BMS input). Not the recalculated-from sensor itself. A
   sensor that is created late (its source had no state at setup) is listed from
   the next closing step on; a sensor that is removed or disabled is not listed.

1. **The keys of an entry.**

   - `series`: `effective`, `derived_power`, `smoothed` or `standby_power`.
   - `direction`: `input` or `output` (glossary terms; the config labels say
     import/export): the direction of the flow the sensor stands for. The
     standby power sensor is an `output`.
   - `role`: `pv` or `bms` (ADR-019). Every output is `bms`, the standby sensor
     included.
   - `source_entity_id`: the configured source sensor; `null` for the standby
     sensor, which has no single source.
   - `aggregations`: the aggregations the sensor's **short-term** statistics
     offer with non-null values, as the `types` a consumer can pass to
     `statistics_during_period`. The series Effy writes have `[mean, state]`
     (ADR-003); the standby sensor, for which Effy writes no statistics, has the
     recorder's own `[mean, min, max]`. The Effy list is one constant next to
     the writer, and a test fails when the writer's rows and the constant
     differ.
   - There is no `unit` key: the unit follows the source, and the live state and
     the statistics metadata are authoritative.

1. **The closing step.** Both recalculation entry points call
   `set_recalculated_from` **last**: the slot job after the live-state pushes,
   the hour's long-term rewrite and the standby update; the manual history
   rewrite after its live-state pushes. An update of this sensor therefore means
   "the recalculation is finished; its statistics and states are final". The
   attributes are refreshed only in this write, not when an entity is added. A
   run that touches nothing neither pushes nor refreshes (ADR-012 is unchanged
   there). The sensor sets `force_update`, so two closing steps with equal
   timestamp and attributes still fire a `state_changed` event each. Amends
   ADR-012 (Amendment 2026-10-18).

1. **Registry.** Each Effy sensor registers a descriptor of itself with the
   coordinator when it is added and removes it when it is removed. The
   recalculated-from sensor reads the registered descriptors when the closing
   step writes its state; it does not subscribe to the registry.

1. **Restore.** **Every Effy sensor restores its last state after a reboot** —
   the effective, derived power and smoothed series with their value, unit and
   attributes, the standby power sensor as ADR-018 already does, and the
   recalculated-from sensor. A last state that was `unknown` or `unavailable` is
   not restored. Only the recalculated-from sensor marks what it restored, as
   follows.

   The recalculated-from sensor restores its state and all of its attributes
   exactly as they were before a reboot, including `sensors`, `max_history_days`
   and `schema_version`, so that subscribers get a consistent snapshot instead
   of `unknown` and a missing list. While restored it carries two temporary
   attributes: `restored_as_of`, the time the restored state was last written
   (its `last_updated`), and `restored_at`, the time of the restoration. Both
   are datetimes; the next closing step writes the state without them. A reboot
   caused by an Effy code or configuration update can therefore show the old
   list, and the old `max_history_days`, until that closing step, normally
   within one slot; the human accepted this as rare. Without a usable last state
   (a fresh install) the state is `unknown`, `sensors` is absent, the two
   scalars are present and current, and there are no `restored_*` attributes.
   Consumers read an absent `sensors` as "not ready yet".

   A restored value of the other sensors is a provisional live state like the
   one ADR-016 pushes: it lands in the slot that is open at the restart, Effy
   writes no statistic for it (ADR-020), and the first `notify_updated` push or
   the slot job's correction of that slot replaces it.

1. **Recorder.** All attributes are recorded; none is excluded
   (`_unrecorded_attributes`). The human judged the volume low: the attributes
   change only when the list changes, and Home Assistant stores identical
   attribute sets once.

1. **Boundary.** The change is Effy-only. Shady and Chargy are not edited from
   here; what they can do with the contract is recorded under Consequences. The
   README gets a short section for other integrations that documents the
   contract, and its statistics paragraph is corrected to `mean` and `state` (it
   still says "mean only").

## What was verified, and what was not

Verified by reading this repository on 2026-10-18: the order of the calls in
`_async_recalculate_recent_and_report` and
`async_recalculate_history_and_report`; that the hour rewrite and the standby
update catch and log their own exceptions and so cannot skip a later push; that
the history writer writes `mean` and `state` and no `sum`; that an options
change reloads the entry (`_async_reload_entry`); and that neither sibling
project references the sensor.

Not verified (to be checked in TASK-0009 and corrected by an amendment if
wrong): that the recorder's native short-term statistics of the standby sensor
offer `mean`, `min` and `max`; that Home Assistant skips storing an attribute
set above 16 KiB (a list of about 70 entries, roughly 35 sources, would reach
it; the live state is unaffected); that a restored state of a `measurement`
sensor leaves the recorder's own row of the open slot to be corrected by the
slot job as ADR-016 and ADR-020 imply; that a list of dicts survives the restore
store unchanged; the `force_update` event behaviour on a live Home Assistant.

## Consequences

- A consumer can read one entity to learn which Effy sensors exist, what each
  is, which aggregations it offers and how many history days Effy recalculates.
  Shady can offer exactly the effective series of the PV inputs and default its
  training window to `max_history_days`; Chargy can pre-select the standby power
  sensor and the BMS series and match its `window_days`. Both can subscribe to
  state changes of this one sensor instead of polling.
- Chargy's counter assumption (`sum`) does not hold for Effy's sensors: its
  owner either integrates the `mean` power per slot or reads another source. The
  `aggregations` key makes the difference visible; it does not fix it.
- The list is a public interface. Changing a key or a meaning needs a new
  `schema_version`, an amendment here and a note in the README.
- The closing push now waits for the hour rewrite and the standby update. The
  first slot job after a restart may replay up to `STANDBY_REPLAY_MAX` (3 days)
  of standby data, so that first push can come later than it did.
- A restored list can name a late-created sensor that does not exist yet in this
  session; its entity ID then resolves to an unavailable or restored state.
- The recalculated-from sensor now writes its state, with unchanged attributes,
  about once per slot.
- The effective, derived power and smoothed sensors become restore entities; the
  dashboard shows the last value after a reboot instead of `unknown`, and the
  restore store holds one more state per Effy sensor.

## Points reviewed and approved by the human (2026-10-18)

- The meaning given to `restored_as_of` (the restored state's `last_updated`)
  and `restored_at` (the restoration time) is the Lead Agent's reading of the
  words; correct it here if another meaning was intended.
- "Restore all as before" is read to include `max_history_days` and
  `schema_version`: the version describes the shape of the restored list, so the
  two travel together.
- The standby aggregations `[mean, min, max]` are an expectation until verified.
- "Restore all sensors" is read as every Effy sensor (value, unit, attributes),
  with only the recalculated-from sensor carrying `restored_as_of` and
  `restored_at`. An earlier draft restored only that sensor.
- The standby power sensor is listed as `direction: output` (human, 2026-10-18).
  `source_entity_id` stays `null`: it has no single source.

---

## Amendment — 2026-10-18 (verification, TASK-0009)

**Reason:** "What was verified, and what was not" listed four points as not
verified.

**Decision:** No change to the contract. Read in the source of Home Assistant
2026.2.3 (the nearest release available to the sandbox; the integration targets
2026.3):

- Measurement sensors compile `mean`, `min` and `max` natively
  (`components/sensor/recorder.py`, `DEFAULT_STATISTICS`), so the standby
  sensor's `[mean, min, max]` is confirmed.
- An attribute set above `MAX_STATE_ATTRS_BYTES` (16384) is not stored and a
  warning is logged (`components/recorder/db_schema.py`).
- The restore store keeps `State.as_dict()` as JSON and loads it with
  `State.from_dict`, so a list of dicts survives; it is dumped every 15 minutes
  and when Home Assistant stops (`EVENT_HOMEASSISTANT_STOP`,
  `helpers/restore_state.py`). After a clean stop nothing is lost; after a crash
  or power loss the restored state can be up to 15 minutes old.
- `force_update` makes a write with an equal state a new state and fires
  `state_changed` instead of `state_reported` (`core.py`, `helpers/entity.py`).

Still not verified: that the recorder's own row of the open slot is corrected by
the slot job after a restored state (ADR-016 and ADR-020 reasoning only), and
everything on a live Home Assistant: nothing was run against one.

**Decided by:** Lead Agent (a verification record, no decision)

---

## Amendment — 2026-10-18 (`restored_as_of` after a second reboot)

**Reason:** Decision 6 defines `restored_as_of` as the restored state's
`last_updated`. After a second reboot before any closing step that is the time
of the first restore, so the attribute would understate the age of the data.

**Decision:** If the restored state already carries `restored_as_of`, that value
is kept; otherwise it is the state's `last_updated`. `restored_at` is always the
time of this restoration.

**Decided by:** Lead Agent (confirmed by human, 2026-10-18: keep a carried
`restored_as_of`, overwrite only `restored_at`)

---

## Amendment — 2026-10-23 (efficiency sensors and the `function` key; takes effect when ADR-023 is accepted)

**Reason:** ADR-023 (Draft) adds four sensors and gives the BMS counters named
roles; consumers such as Chargy should pick by function, not by guessing.

**Decision:** Additive only, so `schema_version` stays 1 (decision 1).

- `series` gains `charge_efficiency`, `battery_efficiency`,
  `discharge_efficiency` and `roundtrip_efficiency`. Like the standby sensor
  they have `direction: output`, `role: bms`, `source_entity_id: null` and the
  recorder's own aggregations `[mean, min, max]`; each is listed only while the
  sensor exists (ADR-023 decision 2).
- Every entry whose `series` is a source series (`effective`, `derived_power`,
  `smoothed`) gains `function`: `grid_import`, `discharge`, `charge` or
  `grid_export`, and `null` for a PV input. The standby and efficiency entries
  have no `function` key.
- The closing step (decision 4) comes after the efficiency update as it does
  after the standby update.

**Decided by:** human (grill session 2026-10-23, Q17 and Q18 to Q23)

---

## Amendment — 2026-10-23 (the contract as a copyable file, `effy_exports.py`)

**Reason:** Shady and Chargy depend on Effy only through entity state, so each
spells out the entity IDs, attribute keys and values by hand, and a copy drifts
silently when the contract changes.

**Decision:** The symbols of this contract — the schema version, the two fixed
entity IDs, the attribute keys, the keys of a `sensors` entry, the values of
`series`, `direction`, `role` and the aggregations, and a `TypedDict` for each
shape — live in one file, `custom_components/effy/effy_exports.py`. Other
projects copy it verbatim into their own namespace, without a dependency on
Effy. Effy imports the same file, so the contract is spelled out nowhere else
inside Effy (a test fails when a quoted contract literal appears in another
module).

- Standard library only, no import from the Effy package, no relative import,
  symbols only (no behaviour).
- Every symbol is prefixed `EFFY_`; the types `Effy` (ADR-000 §5 names Effy's
  classes that way).
- A test pins every symbol and value. A change that breaks an existing key or
  its meaning raises `EFFY_PUBLISHED_SCHEMA_VERSION` in the same commit
  (decision 1); an additive key does not. A reader ignores keys it does not know
  and treats a published `schema_version` larger than its copy's as a stale
  copy.
- `PUBLISHED_SCHEMA_VERSION`, `EFFY_AGGREGATIONS` and `STANDBY_AGGREGATIONS`
  moved from `const.py` into the file (the latter two renamed with the prefix),
  and the series, direction and role constants of `catalog.py` likewise.
- What Effy publishes does not change; nothing in a running system differs.
- The Draft ADR-023 symbols (the efficiency series, `function`) join the file
  with ADR-023, in the same task.

**Decided by:** human (request 2026-10-23: a symbol file "which other downstream
projects can copy into their own namespace … Effy also uses the same file", and
"each export definition should have an EFFY\_ prefix")
