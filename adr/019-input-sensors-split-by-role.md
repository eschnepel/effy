# ADR-019 – Input Sensors Split by Role (PV / BMS)

**Date:** 2026-10-13 **Status:** Draft — every open decision was taken by the
human on 2026-10-13; awaiting the human's confirmation. Terms follow
`GLOSSARY.md`.

---

## Context

Effy's configuration has one mixed list of input sensors (`CONF_INPUT_SENSORS`):
the README names PV strings, the BMS import-from-battery and the BMS
import-from-grid side by side. The loss model treats all inputs alike (ADR-001),
so nothing needed the difference until ADR-018 (standby power): a PV input that
is active alone with no output shows conversion loss and drain mixed, with no
evidence which is which (human), whereas a BMS input active alone shows the
BMS's own draw. Only BMS inputs can be a drain source, and Effy cannot tell them
apart from PV strings.

## Decision (proposed)

1. **Two config keys replace one.** `CONF_PV_INPUT_SENSORS` (`pv_input_sensors`)
   and `CONF_BMS_INPUT_SENSORS` (`bms_input_sensors`) replace
   `CONF_INPUT_SENSORS`, which is removed. Both are multi-selects in the config
   and options flow; outputs are unchanged.
1. **The calculation does not change (human).** The waterfall treats PV and BMS
   inputs as one mixed pool exactly as before the split; entity names, unique
   IDs and every `effy_*` series stay as they are. ADR-001/005 are untouched.
   The two lists are merged only to build that pool; the result does not depend
   on their order (equal values get equal shares).
1. **Validation.** At least one input sensor in total (error
   `at_least_one_input` as today); a sensor may not be in both lists (new error
   key); no BMS input is required, so PV-only systems stay possible.
1. **No migration (human: it is the only installation).** No `VERSION` bump, no
   `async_migrate_entry`, no special error: the old key is not read anywhere, an
   old entry fails at setup, and the human sets the integration up again.
1. **The role is exposed** to the rest of the integration as two collections of
   source entity IDs, so ADR-018's state machine can restrict drain sources to
   BMS inputs.

## Consequences

- A breaking configuration change; README, translations (en, de) and the tests
  that name the old key change.
- A PV-only system stays possible; it simply has no standby sensor (ADR-018).
- Setting the integration up again must give the same `sensor.effy_*` entity IDs
  as before, so the recorder statistics continue; Stage 2 verifies this.
- A regression test asserts that the same sources, split into the two lists,
  give identical results to the unsplit list.

## Open decisions

None. Decided by the human on 2026-10-13: a sensor in both lists is an error,
and no BMS input is required.
