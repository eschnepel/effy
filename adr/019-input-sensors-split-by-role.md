# ADR-019 – Input Sensors Split by Role (PV / BMS)

**Date:** 2026-10-13 **Status:** Accepted 2026-10-16 (human) — every open
decision was taken by the human on 2026-10-13; the migration of decision 4 was
decided and the ADR confirmed on 2026-10-16. Terms follow `GLOSSARY.md`.

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

## Decision

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
1. **Silent migration of the old key (human, 2026-10-16).** No `VERSION` bump
   and no `async_migrate_entry`. At the start of `async_setup_entry`, before
   `add_update_listener` is registered (so no reload is triggered), an entry
   whose options still contain the old key `input_sensors` is rewritten once
   with `async_update_entry`: the old list becomes `pv_input_sensors`,
   `bms_input_sensors` is empty, and the old key is removed. If either new key
   is already present, the new keys win and the old key is dropped unread. One
   warning is logged saying that the inputs were moved to PV inputs and that BMS
   inputs must be assigned in the options flow; no repair issue (a PV-only
   system is valid). The old key survives only as a private literal used by this
   migration; the public `CONF_INPUT_SENSORS` is removed.
1. **The role is exposed** to the rest of the integration as two collections of
   source entity IDs, so ADR-018's state machine can restrict drain sources to
   BMS inputs.

## Consequences

- A configuration change with a silent migration: entry ID, unique IDs and
  entity IDs stay the same, so nothing is deleted or set up again and the
  recorder statistics continue. After the migration every former input is a PV
  input and the results do not change; the human reassigns the BMS sensors in
  the options flow. Until then no BMS input exists and ADR-018's standby sensor
  does not exist. The old key is removed, so an older Effy version can no longer
  read the entry (no version bump, so Home Assistant does not warn; accepted:
  the only installation).
- README, translations (en, de) and the tests that name the old key change.
- A PV-only system stays possible; it simply has no standby sensor (ADR-018).
- Tests assert: the migration writes the list once, removes the old key, does
  not reload the entry, and leaves new keys untouched when present.
- A regression test asserts that the same sources, split into the two lists,
  give identical results to the unsplit list.

## Open decisions

None. Decided by the human on 2026-10-13: a sensor in both lists is an error,
and no BMS input is required.

---

## Amendment — 2026-10-23 (BMS counters by function; in force since ADR-023 was accepted, 2026-10-27)

**Reason:** ADR-023 (Draft, efficiency sensors) needs to know which BMS counter
is the grid import, the discharge, the charge and the grid export counter. The
two unnamed lists (BMS inputs here, outputs) cannot say.

**Decision:** The `bms_input_sensors` list and the output list are replaced by
four optional single sensor pickers: grid import counter and discharge counter
(BMS inputs), charge counter and grid export counter (BMS outputs). The PV list
and decisions 2, 3 (as to PV/BMS exclusivity) and 5 stand. There is no migration
of the replaced lists: the old keys are ignored and drop at the next options
save, and an entry without a charge or a grid export counter refuses to set up
(ADR-023 decision 1). The migration of decision 4 (the old `input_sensors` key)
stays as it is.

**Decided by:** human (grill session 2026-10-23: "no migration path, silent drop
is fine, I will reconfigure my system; it is the only installation yet")
