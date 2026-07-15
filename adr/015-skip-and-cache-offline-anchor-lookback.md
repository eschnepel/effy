# ADR-015 – Skip the Offline-Anchor Lookback When Nothing Changed, and Cache Its Answer

**Date:** 2026-07-13
**Status:** Accepted — amends ADR-014 Decision 3
(`_fetch_last_valid_state_before`'s trigger condition and cost).

---

## Context

Two refinements to ADR-014's staged offline-anchor lookback, both raised
in the same discussion:

1. **"Invalid" only means the raw state string itself is non-numeric**
   (`unavailable`/`unknown`/anything else `_parse_energy_state` can't
   parse) — it does *not* mean "no new reading event happened". A sensor
   that simply stops changing (e.g. a battery discharge sensor sitting at
   its last value all night because the battery is empty, with its
   owning integration still reporting that same numeric value, just not
   writing new rows for it) is not "invalid" in this sense at all, and
   never reaches `_fetch_last_valid_state_before` in the first place —
   `_fetch_raw_energy_states`'s regular `include_start_time_state=True`
   fetch already reliably returns that sensor's true last known value,
   however old, for free. This was worth stating explicitly because the
   ADR-014 write-up didn't make the distinction sharp enough.
2. Given that distinction, a sensor whose raw state string *does* become
   genuinely invalid for an *extended*, *unbroken* stretch (e.g. an
   integration that reports a battery's discharge sensor as
   `unavailable` rather than a valid `0` while the battery is empty) hits
   a real inefficiency: ADR-014's trigger condition ("the window's first
   fetched entry is invalid") stays true on *every* recalculation cycle
   for as long as the outage lasts, re-running the (now staged, but still
   real) lookback query every ~5 minutes even though nothing has changed
   and the anchor it would find is never actually used until the sensor
   actually recovers.

---

## Decision 1 — only search when the window also contains a recovery

`trapezoidal_slot_contributions` only ever uses a pre-outage anchor once a
*valid reading actually follows the invalid stretch* — that's what makes
it a "transition" at all. A sensor that's invalid for the *entire*
fetched window has no such following valid reading yet, so an anchor
found for it would sit unused (no transition can be formed from it, and
the sensor doesn't qualify for the synthetic now-continuation either,
since that requires it to be *currently* valid, ADR-013). The caller
(`_compute_effective_slots`) now checks for this explicitly: it only
invokes `_fetch_last_valid_state_before` when the fetched window's first
entry is invalid *and* the window contains at least one valid reading
somewhere in it (`any(_parse_energy_state(state) is not None for _, state
in raw_states)`). In practice: no lookback runs while an outage is
ongoing and nothing has changed; exactly one lookback runs on the cycle
the sensor's first post-outage reading actually arrives.

## Decision 2 — a volatile, coordinator-owned last-known-valid-reading cache

Even with Decision 1, the *first* time a genuine, extended outage's
recovery is observed still costs a real lookback query. `EffyCoordinator`
now keeps `last_valid_energy_readings: dict[str, tuple[datetime, str]]` —
an in-memory only, never-persisted, session-lifetime cache of each
energy-family entity's most recently observed valid `(timestamp,
raw_state)`. `_compute_effective_slots` updates it every cycle (cheap: no
extra query, just remembering the latest valid entry already present in
whatever it fetched anyway) and, when Decision 1's trigger condition
fires, checks this cache *before* touching the recorder: if a cached
reading already exists from before the current window's `raw_history_start`,
it's used directly as the anchor, with `_fetch_last_valid_state_before`
never called at all. The recorder-backed lookback is now only reached for
an entity whose outage predates this coordinator's own runtime — in
practice, an outage that started before Home Assistant's last restart
(or before this integration's config entry was last reloaded), since any
outage beginning *after* that point will already have a cached
pre-outage reading from whatever cycle last observed it valid.

The cache is deliberately **not** persisted: it's rebuilt naturally
within the first few cycles after any restart (the cost this ADR is
optimizing away only matters for a *sustained* outage anyway, and a fresh
restart's own first lookback, if needed at all, is already the bounded,
staged one from ADR-014, not the original unbounded one from ADR-013).
`history.py`'s recalculation functions accept it as an optional
parameter (`energy_reading_cache`, default `None`) rather than reaching
into a global — this keeps them testable and explicit about the one
piece of cross-call state they now use, matching how `entry_options` is
already passed explicitly rather than read from `hass.data`.

---

## Consequences

- A sensor with an extended, genuine outage (raw state itself invalid,
  not just unchanging) no longer re-runs the offline-anchor lookback
  every ~5-minute cycle for the duration of the outage — at most once,
  right when it ends.
- The very first such lookback for a *new* outage (one that started after
  the coordinator itself started observing this entity) is now answered
  from an in-memory cache instead of a recorder query, at negligible cost.
- The recorder-backed, staged lookback from ADR-014 is now reserved for
  the genuinely rare remaining case: an outage that predates this
  coordinator's own runtime (e.g. it started before the last Home
  Assistant restart or config entry reload).
- No behavioural change to any computed statistic — this is purely an
  efficiency refinement; the anchor found (whether from cache or
  recorder) is the same value either way, and Decision 1's skip never
  omits a search that would otherwise have produced a usable
  contribution.
