# ADR-029 – Household Consumption and the Typed Published Contract

**Date:** 2026-11-13 **Status:** Accepted 2026-11-13 — by the human in chat
("ADR is accepted"). The decisions are the human's answers of 2026-11-13 in a
design interview (grill-with-docs); the text was written by the Lead Agent and
has not been read line by line by the human, who reviews the file changes
afterwards. Terms follow `GLOSSARY.md`.

---

## Context

The human wants a sensor for the household's consumption. Effy's loss balance is
at the battery system; the house sits behind the system's AC port and has its
own meter at the grid connection. The consumption is what the meter and the
port's flows leave for the house.

While planning it the human found the published contract (ADR-021) not yet fit
to carry more sensors: `series` mixes the processing stage (`effective`,
`smoothed`), the kind of quantity (`standby_power`, `*_efficiency`) and the
scope (`pv_sum`), and the energy sensors of ADR-027 would hold the time span in
the series name (`pv_delta_integral_today`). `function` is empty for most
entries. No other project reads the contract yet, so it can be reworked now
without a migration. TASK-0045f, which publishes the CAP-13 sensors, has not
started and takes over the rework.

## Decision

### Household consumption

1. **Household meter inputs.** Two optional pickers in the config and options
   flows: **Household import** and **Household export**. Each is a sensor in W,
   kW, Wh or kWh. Household export needs household import; without household
   import there is no household sensor. A sensor that has another role, is in
   the additional sensors list, or is chosen twice is refused (ADR-019's rule
   that a sensor has one role).

1. **Signed import.** A power-family household import sensor with no household
   export sensor is signed: its positive part is household import, its negative
   part household export (the EPS sensor's split, ADR-026). With a household
   export sensor, each sensor is taken as it is and a negative reading counts as
   0\. A counter with no export sensor gives an export of 0.

1. **Formula.** Per slot, in W:

   ```
   consumption = max(0, hi − he + sys_export + eps_out − sys_import)
   ```

   `hi`, `he` are household import and export, `sys_export` and `sys_import` the
   battery system's grid export and grid import counters, `eps_out` the positive
   part of the EPS sensor. All are the series before the loss is distributed
   (derived power or smoothed), not the effective values: the balance is at the
   AC port, where the loss share of an input does not exist. The EPS input is
   not subtracted. A slot without a household import value has no row; any other
   missing term counts as 0.

1. **Series.** The household meter sensors get the series an input gets before
   the loss (ADR-012, ADR-013, ADR-014: derived power for an energy-family
   sensor, smoothed for a power-family one) by the same code, are in no loss,
   effective, standby, efficiency or battery calculation (as ADR-028's
   additional sensors) and are not in the waterfall's slot union.

1. **Sensors.** `sensor.effy_household_consumption_now` (W, `measurement`, power
   series written like the PV sum) and, by the mechanism of ADR-027 generalised
   from the PV inputs to any series, the energy sensors
   `sensor.effy_household_consumption_today` (`last_reset` at local midnight)
   and `sensor.effy_household_consumption_total`, kWh, state class `total`. They
   exist while household import is configured.

1. **Formula signature.** Both household sensors are part of the formula
   signature of ADR-027 decision 6; a change recalculates by itself and derives
   the consumption energy sensors again.

### The typed contract

7. **Keys.** The attribute `sensors` is no longer one dict per sensor but a list
   of **groups**: one group per distinct combination of the attributes `series`,
   `quantity` (`power`, `energy`, `efficiency`, `share`), `scope` (`source`,
   `combined`, `system`), `span` (`slot`, `today`, `total`), `role`,
   `direction`, `function` and `aggregations`, each with the list `sensors` of
   the entity IDs of the Effy sensors that have exactly these attributes. A
   reader picks by attributes and takes the entity IDs, instead of filtering a
   flat list. `series` stays; its values no longer repeat the quantity.

   ```yaml
   sensors:
     - series: effective
       quantity: power
       scope: source
       span: slot
       role: pv
       direction: input
       function: pv
       aggregations: [mean, state]
       sensors: [sensor.effy_pv1, sensor.effy_pv2]
   sources:
     sensor.pv1: [sensor.effy_pv1, sensor.effy_pv1_delta_integral_today]
     sensor.pv2: [sensor.effy_pv2, sensor.effy_pv2_delta_integral_today]
   ```

   Groups are sorted by the values of the attributes in that order, the entity
   IDs inside a group sorted. The old key `source_entity_id` has no place in a
   group. A second attribute of the sensor, `sources`, is keyed by the
   configured source sensor and lists every Effy sensor that depends on it (its
   effective, derived and smoothed series, its PV energy sensor), sorted. Effy
   sensors that stand for no single source (`combined` and `system` scope) are
   in no list.

1. **Series values.** `effective`, `derived`, `smoothed`, `standby`, `charge`,
   `discharge`, `battery`, `roundtrip`, `pv`, `consumption`. (Renamed:
   `derived_power` → `derived`; `standby_power` → `standby`; the four
   efficiencies lose `_efficiency`; `pv_share` → `pv`. Gone: `pv_sum`,
   `pv_delta_integral`, `pv_delta_integral_today`, `pv_delta_integral_total`.)
   The words delta, integral, sum and deltasum describe how Effy books energy,
   not what it is, and stay out of the contract; entity IDs are unchanged.

1. **`function` on every group.** The key is present everywhere. Its values are
   `grid_import`, `discharge`, `charge`, `grid_export`, `eps`, `unknown` as
   before, plus `pv` (a PV input, was `null`, and the PV sum and the PV energy
   sensors), `standby`, `efficiency`, `share`, `household_import`,
   `household_export`, and `consumption` for the household consumption sensors
   (the entries that had no key take the value of their `series`; the PV ones
   are typed `pv`, by the human's decision of 2026-11-13).

1. **`role`** gains `household`; **`direction`**: the household import meter
   `input`, the export meter `output`, both `both` when the series is signed
   (decision 2); the consumption entries `output`.

1. **Version.** `EFFY_PUBLISHED_SCHEMA_VERSION` becomes 2: values and the
   `function` of existing entries change.

### Published entries

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

## Alternatives considered

- **One signed household meter sensor** (like the EPS sensor): rejected by the
  human; two pickers, with the sign rule of decision 2 for a meter that has only
  one sensor.
- **The EPS input subtracted like the system's grid import:** rejected; energy
  another inverter feeds into the port is not taken from the house.
- **Effective values for the system's grid import:** rejected; the loss share is
  lost inside the system, not at the AC port.
- **Additive contract keys, version 1:** rejected; nobody reads the contract, so
  the cleaner model costs nothing now and is hard to change later.
- **A separate copy of the delta mechanism for consumption (option B) or a plain
  live `total_increasing` sensor (option C):** rejected; the first duplicates
  ADR-027, the second is wrong after a history rewrite.
- **Key `period` for the time span:** rejected; the glossary reserves "period"
  against slot.
- **A flat list of one dict per sensor, as in version 1:** rejected by the
  human; a reader picks by attributes, so the attributes are the keys and the
  sensors the values.
- **Keeping `source_entity_id` per sensor by making `sensors` a list of dicts:**
  rejected; the human's layout lists plain entity IDs, and asked for the sources
  as an attribute of their own, keyed by source sensor and listing the sensors
  that depend on it.

## Consequences

- The attribute is shorter: the attributes of a group are written once, which
  also stretches the 16 KiB limit of Home Assistant's attribute storage.

- TASK-0045f publishes every sensor under the new model, existing entries
  included; the existing contract tests change with it. `effy_exports.py` is the
  one place of the symbols.

- The `function` of a PV input changes from `null` to `pv`: a reader written
  against version 1 sees `schema_version` 2 and is stale by the rule of ADR-021.

- ADR-027's PV delta code is generalised to one series source; CAP-13 code that
  is `done` is touched by TASK-0050.

- ADR-021 gets an amendment (written with this ADR, in force with it), as do
  ADR-019 (the household roles) and ADR-027 (the mechanism generalised, the
  signature extended).

- Until the history recalculation of the formula signature has run, a new
  household entry has no history.

**Decided by:** the human, 2026-11-13 (chat); text by the Lead Agent, not yet
read line by line by the human.
