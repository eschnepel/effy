# ADR-010 – Idle-Entity Staleness in the Live Path & a Slot-Aligned Recalculation Timer

**Date:** 2026-07-07
**Status:** Accepted — amends ADR-006 (Option C flow) and ADR-007 (`LiveReading.reset`)

**See also:** the live path this ADR describes was disabled 2026-07-09
(`disabled/README.md`). ADR-011 (2026-07-10) repurposes the slot-aligned
timer introduced here to drive single-slot history recalculation instead
of live recalculation, and changes it to fire *after* each boundary
instead of *before* — the "Decision" section below (lead time, `Option C`
live flow) reflects the pre-ADR-011 live design.

---

## Context

After ADR-007 shipped, two related but distinct staleness bugs surfaced in
production for ENERGY-family (`TOTAL_INCREASING` / `TOTAL`-as-energy)
sensors, plus a third problem that isn't about the accumulator math at all:

**Bug A — ENERGY mirrors the pre-2026-07-03 POWER bug.**
`_reset_all_cache` resets *every* watched entity on *every* debounce cycle,
not just the entity that triggered it (ADR-006 Option C). ADR-006's
2026-07-03 amendment fixed this for POWER by carrying `avg` forward instead
of zeroing it. The analogous fix was never applied to ENERGY's
`to_sensor_reading`: whenever `elapsed_h <= 0` — which is true on almost
every cycle for any entity that updates less often than whatever else is
triggering recalculation — it returned a hardcoded `0.0`. An entity updating
every few minutes while a fast power sensor triggers debounce cycles every
0.3 s would read ~0 W on nearly all of them, with the true rate visible only
in the single cycle immediately following its own event.

**Bug B — naively carrying the rate forward doesn't expire.**
An initial fix mirrored ADR-006's approach directly: store the last computed
rate (`last_rate`) and return it whenever `elapsed_h <= 0`. This fixed Bug A
but created the opposite failure: if the entity's real-world rate later
dropped to (and genuinely stayed at) zero — e.g. a battery-discharge counter
that stops incrementing overnight — and the source entity only ever reports
on change, `last_rate` had nothing to invalidate it and was reported
forever, indistinguishable from a sensor that was still actively
discharging.

**Problem C — no event anywhere means no recalculation at all.**
Independently of the accumulator math: `_do_refresh` is *only* scheduled
from `_on_state_change` (ADR-006 Option B/C). If literally no watched entity
changes state for an extended period (e.g. a quiet system overnight),
`_do_refresh` never fires — pushed sensor values stay frozen at whatever
they last happened to be, regardless of what `to_sensor_reading` would
compute if it were asked.

---

## Options considered

### Rejected — keep `last_rate`, add a `STALE_AFTER_SECONDS` timeout

An intermediate fix added `last_event_ts` per entity and a hardcoded
`STALE_AFTER_SECONDS = 900` cutoff: carry `last_rate` forward, but force it
to `0.0` once the entity has gone longer than that without a real event.

| | |
|---|---|
| **Pro** | Bounds Bug B; entities do eventually reach 0. |
| **Con** | `900` is an arbitrary magic number, unrelated to anything else in the codebase — a second, invented timing convention living alongside the 5-minute statistics grid (`SLOT_MINUTES`, ADR-003/008/009) that already governs this kind of question everywhere else. |
| **Con** | Still a guess (the carried-forward rate) with a timer bolted on to decide when to stop trusting the guess, rather than a mechanism that computes an honest answer directly from the data actually available. |
| **Con** | Does not address Problem C at all — a fully quiet system still never recalculates. |

Rejected in favour of a mechanism that doesn't need a magic number.

---

## Decision

Two independent, coexisting changes.

### 1. `LiveReading.reset()` — decouple `reset_ts` from `updated_ts` while idle

A new per-entity flag, `_touched_since_reset`, is set by `update_power` /
`update_energy` on every real event and consulted (then cleared) by
`reset()`:

```python
def reset(self, now: datetime) -> None:
    if not self.is_seeded():
        self.reset_ts = self.updated_ts = now
        if self.family != _FAMILY_POWER:
            self.raw_start = self.raw_last
        self._touched_since_reset = False
        return

    if self.family == _FAMILY_POWER or self._touched_since_reset:
        # Window closed by a real event (or POWER, whose avg doesn't
        # depend on elapsed_h at read time) — roll forward together,
        # exactly as before.
        new_reset = self.updated_ts
        self.reset_ts = new_reset
        self.updated_ts = new_reset
        if self.family != _FAMILY_POWER:
            self.raw_start = self.raw_last
    else:
        # ENERGY, genuinely idle since the last reset: reset_ts stays
        # put (it still marks the last real event); only updated_ts
        # advances to the real `now`.
        self.updated_ts = now

    self._touched_since_reset = False
```

`to_sensor_reading()` itself is **unchanged** — no `last_rate`, no `now`
parameter, no staleness check. The existing formula

```
elapsed_h = (updated_ts − reset_ts) / 3600
W_equiv   = max(0, raw_last − raw_start) / elapsed_h
```

now does the right thing on its own: while idle, the numerator stays 0 (
`raw_start`/`raw_last` are untouched) and the denominator grows honestly, so
the reading is a real `0`, not an artifact of a zero-width window and not a
guess. `reset_ts` staying pinned at the last real event also means that once
a new real event *does* arrive, `elapsed_h` spans the *entire* real gap —
including however many unrelated debounce cycles happened in between — so
the resulting rate is the correct average over the true interval, not
shrunk by intervening resets.

POWER is untouched: `avg` doesn't depend on `elapsed_h` at read time (it's
already a running average), so there is nothing to decouple there.

### 2. An independent, slot-aligned recalculation timer (solves Problem C)

`EffyCoordinator` now schedules a second, self-rescheduling timer alongside
the debounce timer, firing `SLOT_TIMER_LEAD_SECONDS` (5 s) before every
`SLOT_MINUTES`-aligned wall-clock boundary — the same 5-minute grid already
used by the history path (`sensor_utils.SLOT_MINUTES`, ADR-003/008/009), not
a separately invented interval:

```python
def _next_slot_trigger_delay(now, slot_minutes=SLOT_MINUTES, lead_seconds=SLOT_TIMER_LEAD_SECONDS) -> float:
    slot_seconds = slot_minutes * 60
    epoch = now.timestamp()
    current_slot_start = epoch - (epoch % slot_seconds)
    trigger_at = current_slot_start + slot_seconds - lead_seconds
    if trigger_at <= epoch:
        trigger_at += slot_seconds
    return trigger_at - epoch
```

This is **additive, not a replacement** for the debounce timer from ADR-006:
it calls the same recalculation logic (extracted into
`_recalculate_and_reset`, shared by both `_do_refresh` and the new
`_on_slot_timer`) but deliberately never touches `_refresh_pending` /
`_unsub_refresh` — a pending debounce cycle is left completely alone. The
two timers coexist and either may fire a given recalculation; nothing
cancels the other.

```
state_change event → _on_state_change → debounce timer (0.3 s) ─┐
                                                                  ├─→ _recalculate_and_reset → reset
slot boundary − 5 s → _on_slot_timer (self-rescheduling) ────────┘
```

This guarantees at least one recalculation per slot even if no watched
entity changes state at all for an extended period — the case Problem C
describes, which the `reset()` fix alone cannot address, since it only
changes what a recalculation *computes*, not whether one *happens*.

---

## Consequences

- **Pro:** ENERGY readings are always the honest `delta / elapsed_h` from
  real accumulated data — no fabricated carry-forward value, no arbitrary
  expiry timer, no second timing convention alongside `SLOT_MINUTES`.
- **Pro:** Recalculation is now guaranteed at least once per 5-minute slot
  even in a fully quiet system, so pushed sensor values can't freeze simply
  because nothing at all triggered a refresh.
- **Con / by design:** an idle ENERGY entity's live reading legitimately
  shows `0` between its own real events whenever some *other* entity's
  debounce cycle fires in between — e.g. a kWh sensor reporting every 30 s
  next to a W sensor triggering every second will show brief `0` dips
  between the kWh sensor's own updates. This is the mathematically honest
  answer given the information available at each instant (no evidence of
  change ⇒ report none), not a bug to "fix" by carrying a guess forward
  again — see Bug A/B above for why that guess-based approach was rejected.
- **Neutral:** `to_sensor_reading()`'s signature and the POWER family are
  both unchanged; only `LiveReading.reset()` gained the idle/touched
  distinction for ENERGY.
- **Neutral:** `last_rate`, `STALE_AFTER_SECONDS`, and `last_event_ts` from
  the rejected intermediate approach do not exist in the shipped code.

---

## Corrections to earlier ADRs

- **ADR-007**, *"After recalculation, `reset` zeroes `avg`..."* (POWER
  section): already outdated independently of this ADR — ADR-006's
  2026-07-03 amendment carries `avg` forward instead. Noted here since this
  ADR touches the same method; see ADR-006 for the authoritative POWER
  behaviour.
- **ADR-007**, *"`reset_ts = updated_ts` / `raw_start = raw_last`"* (ENERGY
  roll-forward): still correct for the case where the entity was touched
  since its last reset. Superseded by this ADR for the idle case, where
  `reset_ts` now stays put and only `updated_ts` advances.
- **ADR-006**, *Flow* section: superseded by this ADR to the extent it
  described a single debounce timer as the only trigger for `_do_refresh`.
  The slot-aligned timer is a second, independent trigger for the same
  underlying recalculation.
