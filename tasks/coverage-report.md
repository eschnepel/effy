# Branch Coverage Report (TASK-0028)

Date: 2026-10-24. Measured with `pytest --cov` (branch coverage, configuration
in `pyproject.toml` under `[tool.coverage.*]`, source =
`custom_components/effy`). Baseline before any change of this task: 456 tests
passed, `ruff format`/`ruff check` clean, `mypy --strict` showed exactly 3
errors (`tests/test_catalog.py:43`, `tests/test_catalog.py:101`,
`tests/test_published_sensors.py:520`). "Nothing new" below means these three
and no other.

## Slices as worked

The order of the task, leaf modules first (ADR-000 §3). Sub-slices are where the
module is over about 400 lines; the cuts follow the modules' own sections.

| # | Slice | Result |
| -- | -- | -- |
| 0 | tooling | coverage configuration, first run, `DEPENDENCIES.md` |
| 1 | `effy_exports.py` | 100 % before, nothing to do |
| 2 | `const.py`, `inputs.py` | 100 % before, nothing to do |
| 3 | `catalog.py`, `sensor_utils.py` | `catalog.py` 100 % before; `sensor_utils.py` worked |
| 4 | `calculation.py` (4a slot helpers and trapezoid, 4b gap handling and waterfall, 4c the standby machine) | worked |
| 5 | `history.py` (5a reading helpers, 5b raw-state fetch and anchor lookback, 5c compute, 5d recalculation paths, 5e standby glue, 5f statistics read and write) | worked |
| 6 | `config_flow.py` | worked |
| 7 | `coordinator.py` | worked |
| 8 | `sensor.py` (8a platform setup and late binding, 8b identity and restore of the sensor classes, 8c the live path) | worked |
| 9 | `button.py`, `__init__.py` | worked |

How to reproduce one number: `pytest -q --cov --cov-report=term-missing`.

## How the classes were applied

The classes A to D are those of the task. Three readings the worker had to fix,
so that the sections below are comparable:

- **An uncovered branch** is an arc `line → line` that coverage.py lists as
  untaken, or a statement range that no test executes. When an `if` has an
  uncovered body, the branch is the `if` line with the direction "true" (or
  "false"), and the body's statement range is named with it, not counted twice.
- **C (dead)** is "no state of the running integration and no caller inside
  `custom_components/` reaches it", also when a test could call the function
  directly. A public function with no caller and no ADR that requires it as an
  API is C; whether to remove it is a question for the human.
- **A or B** for a guard: A if the guarded failure is specified (logged, state
  unchanged, an error key, a published attribute) or visible to a user, a
  downstream integration or a recorder row; B if only a malformed value from
  outside would reach it and nothing specifies what then happens.

## Slice 0 — tooling

- `pyproject.toml`: new `[tool.coverage.run]` (`branch = true`,
  `source = ["custom_components/effy"]`) and `[tool.coverage.report]`
  (`show_missing`, `skip_empty`). No threshold is set (task: the human decides).
- `pytest-cov` 7.1.0 (coverage.py 7.14.3) was already in the `dev` dependency
  group and `uv.lock`; no new package. Recorded in `tasks/DEPENDENCIES.md` as a
  development-only tool; not in `manifest.json`.
- First run (all 456 tests): 1805 statements, 215 missed; 502 branches, 95
  untaken directions on 41 partially covered lines, plus the entries into
  never-run code. Coverage.py's combined figure is 87 %; the table gives lines
  and branches apart.

| Module | Lines before | Branches before | Lines / branches not covered |
| -- | -- | -- | -- |
| `__init__.py` | 75.7 % | 50.0 % | 9 / 4 |
| `button.py` | 0.0 % | — | 31 / 0 |
| `calculation.py` | 98.9 % | 95.5 % | 4 / 5 |
| `catalog.py` | 100.0 % | 100.0 % | 0 / 0 |
| `config_flow.py` | 98.2 % | 100.0 % | 1 / 0 |
| `const.py` | 100.0 % | — | 0 / 0 |
| `coordinator.py` | 99.2 % | 94.2 % | 2 / 3 |
| `effy_exports.py` | 100.0 % | — | 0 / 0 |
| `history.py` | 85.6 % | 78.4 % | 78 / 45 |
| `inputs.py` | 100.0 % | 100.0 % | 0 / 0 |
| `sensor.py` | 82.1 % | 58.5 % | 75 / 34 |
| `sensor_utils.py` | 61.5 % | 66.7 % | 15 / 4 |
| **total** | 88.1 % | 81.1 % | 215 / 95 |

## Slices 1 and 2 — `effy_exports.py`, `const.py`, `inputs.py`

All three are at 100 % lines and 100 % branches (`effy_exports.py` and
`const.py` have no branch). No uncovered branch, nothing to classify, no test
added.

## Slice 3 — `catalog.py`, `sensor_utils.py`

`catalog.py`: 100 % lines, 100 % branches before; nothing to do.

`sensor_utils.py`: lines 61.5 % → 74.4 %, branches 66.7 % → 83.3 %.

| Where | Condition and direction not taken | Class | Reason and document |
| -- | -- | -- | -- |
| `sensor_utils.py:92-96`, `93→94` | `get_sensor_meta`: `state is None`, true (returns `{}`) | A | A source with no state yet is the startup race of ADR-013 Decision 1; the empty result keeps the derived entity from being created until it reports. Visible to the user as a missing entity. |
| `sensor_utils.py:93→95` | same condition, false (reads the attributes) | A | The metadata `sensor.py::_build_derived_entities` classifies a source by (ADR-012, ADR-013). |
| `sensor_utils.py:115-126`, `116→117`, `116→119` | `get_current_value`: whole function (`state is None or state.state in (…)` both directions, the `float()` failure that logs and returns `None`, the unit lookup) | C | No caller in `custom_components/`. ADR-007 (Consequences, Neutral) says it is used "during initial setup seeding"; no code does that, and `disabled/coordinator_live.py` only mentions it in a comment. See open question Q1. |

Tests added (`tests/test_sensor_utils.py`, class `TestGetSensorMeta`, 5 tests):
the empty dict for a source without state; that this empty result does not
classify as energy family; the three keys for a reported source; the
late-binding flip of the same entity id from empty to energy family; the
defaults for missing attributes (unit `W`, state class `None`, friendly name =
entity id).

Breaking the behaviour on purpose, one at a time, each undone with the checksum
of `sensor_utils.py` verified afterwards (6 of 6 killed by a failing test): the
`None` guard removed; the `None` case returning a made-up energy classification;
the unit, the state class, the friendly-name default and the unit default each
changed.

## Slice 4 — `calculation.py`

Cut by section (the file is 1023 lines): 4a slot helpers and trapezoid (lines
1-420), 4b gap handling and the waterfall (to 740), 4c the standby machine (from
740). Only the functions that hold an uncovered branch were read.

Lines 98.9 % → 99.1 %, branches 95.5 % → 96.4 %.

| Where | Condition and direction not taken | Class | Reason and document |
| -- | -- | -- | -- |
| `calculation.py:117-119` | `distribute_loss`: `remaining_loss > 1e-6 and sorted_active`, true (residual goes to the largest active input) | B | ADR-001 (Consequences) names the guard for rounding errors. A fuzz of 300 000 cases with non-negative outputs left at most 7.5e-9 W, never 1e-6; it fires only for a *negative* output sum (loss larger than every input can carry), and then `history.py` writes `max(0, …)` = 0 either way. `shares` is read only by the disabled live path (`sensor.py::_on_distribution`). Changes no recorder row. |
| `calculation.py:357-358` | `trapezoidal_slot_contributions`: `window_seconds <= 0`, true | B | `window_start` is always before `t2` (`t2 > t1` is checked just before) unless `max_minutes <= 0`; no caller passes `max_minutes`, the constant is 120. A guard against a value the callers rule out. |
| `calculation.py:368→372` | same function: `overlap_seconds > 0`, false | C | The cursor is aligned down to at or before `window_start` and stays below `t2`, so the overlap is always positive. No input reaches it. |
| `calculation.py:943-944` | `drain_feed`: `not slot_list`, true (returns `state`) | A | Docstring: events at or after the end of the last completed slot belong to the open slot, so with no completed slot every event is left out and the state is unchanged. |
| `calculation.py:1009→1015` | `drain_state_from_restore`: `raw_resolution` is a mapping, false | B | `drain_restore_data` always writes the key as a dict; only a dump this code never wrote reaches it. See open question Q2 (the docstring says malformed data gives `None`). |

Test added (`tests/test_standby.py`, `TestFeedAndRestore`): a batch with no
completed slot leaves the state unchanged, for a fresh state, an empty iterator,
and a state that already holds an estimate, whatever the events are.

Breaking it on purpose (2 of 2 killed, `calculation.py` checksum verified after
each): the guard deleted; the empty case returning a state with its armed turn,
pending intervals and estimate reset.

## Slice 5 — `history.py`

Cut by section: 5a reading helpers (lines 150-630), 5b raw-state fetch and
anchor lookback (630-780), 5c compute (780-1190), 5d the recalculation paths
(1190-1610), 5e the standby glue (1610-1770), 5f statistics read and write
(1770-end).

Lines 85.6 % → 99.4 %, branches 78.4 % → 98.1 %.

The existing tests replace every recorder-facing helper of this module by an
in-memory fake (`_install` in `tests/test_history_recent_equivalence.py`), so
the real bodies of `_get_unit`, `_get_state_class`, `_get_statistics_units`,
`_get_short_term_retention_days`, `_fetch_raw_energy_states`,
`_fetch_last_valid_state_before`, `_fetch_statistics` and parts of
`_write_recorder_statistics` never ran. The new tests run those bodies against
stubs of the Home Assistant calls they make (they import them lazily, so a stub
in `sys.modules` is what they see). What the stubs return is the shape the
helper relies on; that a real Home Assistant returns that shape is class D (see
the list below).

| Where | Condition and direction not taken | Class | Reason and document |
| -- | -- | -- | -- |
| `history.py:167`, `508→509` (509-510) | newer-core import of `StatisticMeanType` and the metadata keys `mean_type`, `unit_class` | A | Docstring of `_build_statistic_metadata`: a newer core requires both; they reach the recorder as the statistic's metadata. Reached by loading the module a second time with a stub that has the enum. |
| `history.py:321→322` | `_PhaseTimings.finish`: `not self.enabled`, true | A | Class docstring: a disabled instance does nothing and keeps no state; visible as the absence of a log line. |
| `history.py:469→470, 469→471` (467-475) | `_get_short_term_retention_days`: valid `keep_days`; unreadable value, warning and fallback of 10 | A | Docstring and the logged fallback; the cutoff decides which slots are written short-term. |
| `history.py:515-519`, `516→517`, `516→518` | `_get_state_class`: no state / state | A | A removed or not-yet-reported source (ADR-013 Decision 1): no state class, so the series is read as power. Tie-break: it decides which statistics field is read, hence the rows. |
| `history.py:523-527`, `524→525`, `524→526` | `_get_unit`: no state / state | A | Same; the unit `W` feeds `units`, so the unit label of the written rows. |
| `history.py:551-571`, `563→564`, `563→571`, `569→563`, `569→570` | `_get_statistics_units`: the lookup, the failure (logged, `{}` returned), an entry of unexpected shape (skipped), a statistic without a unit (left out) | A | Docstring: falls back to the live unit when the metadata API fails or its shape changed; logged. |
| `history.py:621→622` | `_readings_for_slot`: `raw_val is None`, true | A | A compiled row whose field is empty gives no reading: the sensor is absent from the slot and takes no loss share (reachable with a legal recorder row; tie-break: it changes the rows). |
| `history.py:653-665` | `_fetch_raw_energy_states`: the whole body; entity with no states → `[]` | A | Docstring: every state including `unavailable`/`unknown`, in order, `no_attributes=True`; ADR-012 needs the invalid entries for offline gaps. |
| `history.py:725-752`, `731→732`, `731→752`, `732→736`, `732→737`, `748→749`, `748→751`, `749→748`, `749→750` | `_fetch_last_valid_state_before`: every stage, the skip of a stage not wider than the one tried, the invalid-only stage, the exhausted search | A | Docstring, ADR-013/014/015: staged lookback 30 min → 1 day → `max_history_days`, bounded recorder cost. |
| `history.py:989→990` | `_compute_effective_slots`: statistics unit differs from the live unit, true | A | Comment and docstring: the statistics unit is used and the difference is logged as a warning. |
| `history.py:1105→1106` | power-family input with no `mean` row, true | A | Docstring: a smoothed series exists for every power-family input that had at least one reading. Removing this `continue` alone changes nothing (finding F1). |
| `history.py:1151→1152` | input with no effective values, true | A | No statistic is written for an input with nothing in the range (the rows would be empty). |
| `history.py:1167→1168` | energy sensor with no derived-power values, true | A | Same, for `effy_*_power`; a counter with no history at all. |
| `history.py:1177→1178` | smoothed values all outside `[start, end)`, true | B | The recorder returns only rows inside the requested range and interpolation only fills between existing rows, so a non-empty series always has an in-range value. |
| `history.py:1233→1234` | `_last_slot_values`: empty `slot_values`, true | B | Its callers drop empties first (`_drop_open_slot`, and the compute loop writes no empty entry). |
| `history.py:1326→1330`, `1330→1334` | `async_recalculate_history`: no effective series / no derived-power series, so that write is skipped | A | Only the series that exist are written; reachable with every input silent but an output counter, or with power-family sensors only. Changes which recorder rows exist. |
| `history.py:1643→1644` | `async_read_drain_events`: no BMS input, true | A | Docstring: nothing is read, not even the unit lookup. |
| `history.py:1655→1659` (1659-1660) | power-family BMS input read through `power_drain_events` | A | ADR-018 §1; kW is scaled to W; events outside `[since, until)` are left out. |
| `history.py:1683→1684` | `async_read_drain_activity`: no sensors, true | A | Docstring; no statistics read. |
| `history.py:1690→1692`, `1692→1693`, `1692→1695` (1692-1695) | which statistic is read: energy-family → `effy_*_power`, power-family input → `effy_*_smoothed`, power-family output → its own `mean` | A | Docstring and ADR-018 §3. |
| `history.py:1702→1703` | statistic id in the answer that was not asked for, true | B | The recorder answers for the ids it was given. |
| `history.py:1707→1708` | row without a `mean` (or outside the range) | A (the empty `mean`); the out-of-range half is B | Docstring: a slot without any statistic is unknown, never quiet; a slot of zero readings is known and quiet. The range half is guaranteed by the recorder. |
| `history.py:1775-1800`, `1794→1795`, `1794→1800`, `1795→1794`, `1795→1796`, `1797→1795`, `1797→1798` | `_fetch_statistics`: the call (5-minute period, `units=None`, types) and the normalization of a float `start` to a UTC datetime | A | Comment in the function: some cores return a unix timestamp; everything downstream needs a tz-aware datetime. |
| `history.py:1892→1893` | `_write_recorder_statistics`: `not include_long_term`, true | A | ADR-011 D2 and the docstring: the slot path never writes hourly rows; a single hour row from a few slots would replace the hour's correct mean. |
| `history.py:1900→1862` | `if hourly:` false | B | Needs an empty `slot_values`, which no caller passes (see `1233→1234`). |

Tests added (all new except where named):
`tests/test_history_recorder_helpers.py` (new, 36 tests: metadata of both core
generations, live lookups, retention, statistics units, readings of a slot, raw
states, the staged anchor lookback, `_fetch_statistics`, the write without
long-term rows); `tests/test_history_compute_edges.py` (new, 8 tests:
statistics-unit warning, sensors without data, which series a history
recalculation writes); `tests/test_standby_glue.py` (6 new tests in two new
classes: the two standby reads); `tests/test_history_phase_timing.py`
(`TestDisabledStopwatch`, 2 tests). `tests/test_history_recent_equivalence.py`:
`_load_history` takes an optional `mean_type`, default unchanged, so a second
copy of `history.py` can be loaded as a newer core presents it.

Breaking the behaviour on purpose, one at a time, each undone with the checksum
of `history.py` verified afterwards: 14 mutants in 5a (all killed), 9 in 5b (all
killed), 9 in 5c/5d (8 killed; the removal of the `1106` guard alone survived,
see F1 — the behaviour it guards was pinned by a mutant that gives the sensor a
series), 13 in 5e/5f (11 killed at once; the two survivors, the missing "no
recorder job at all" assertion for `async_read_drain_events` and the missing
event before `since` and at `until`, were repaired in the tests and their
mutants then died).

## Slice 6 — `config_flow.py`

Lines 98.2 % → 100 %, branches 100 % → 100 %.

| Where | Condition and direction not taken | Class | Reason and document |
| -- | -- | -- | -- |
| `config_flow.py:172` | `EffyConfigFlow.async_get_options_flow`: the whole body (no branch; one uncovered statement) | A | Home Assistant's entry point to the options dialog; it must return an options flow bound to the entry it is given, whose form shows that entry's options (ADR-019, the options flow, and the function's own docstring). The existing tests built `EffyOptionsFlow` directly. |

Test added (`tests/test_config_flow.py`, `TestOptionsFlowHook`, 1 test): two
entries through the hook give two flows, each prefilled with its own entry's
inputs. Broken on purpose (1 meaningful mutant killed, checksum verified): the
hook ignores the entry it is given. (A second mutant, a shared instance, died on
a `NameError` and is not counted.)

## Slice 7 — `coordinator.py`

Lines 99.2 % → 100 %, branches 94.2 % → 100 %.

| Where | Condition and direction not taken | Class | Reason and document |
| -- | -- | -- | -- |
| `coordinator.py:341→344`, `344→347` | `async_shutdown`: no recorder listener / no timer held, true | A | Docstring: shutdown releases what is held and cancels the in-flight job; it runs on an entry whose setup never ran or ran before, so it must not raise or release twice. |
| `coordinator.py:402→exit` | `subscribe_recalculated_from`'s unsubscribe: `cb` already removed | A | The unsubscribe callables are idempotent by the codebase's own convention (`test_subscribers_get_the_state_and_can_unsubscribe` pins it for `subscribe_standby`); the entity may be removed and the entry unloaded. |
| `coordinator.py:439` | `subscribe_updates`'s unsubscribe, never run | A | Docstring: call it from `async_will_remove_from_hass`; afterwards a recalculation no longer pushes to that entity (ADR-016). |
| `coordinator.py:657-658` | `_async_update_standby`: `except asyncio.CancelledError: raise` | A | Docstring: a failure is logged and never fails the slot job; a cancellation (shutdown) is no failure and must reach the task, unlogged, with the state unchanged (ADR-000 §8). Finding F2. |

Tests added (`tests/test_coordinator_slot.py`, 6): shutdown without a setup; a
second shutdown; shutdown with only one of the two handles held; the
entity-update subscription ending with its unsubscribe (and the others
untouched); the recalculated-from unsubscribe twice; a cancelled standby feed.

Breaking the behaviour on purpose, 9 mutants (`coordinator.py` checksum verified
after each): 8 killed — each handle released unconditionally, the handle kept
after release, the update unsubscribe doing nothing or clearing all, the
unguarded removal, the cancellation swallowed, the cancellation logged as a
failure; 1 survived: removing the explicit `CancelledError` clause altogether,
because `except Exception` does not catch it in the first place (finding F2).

## Slice 8 — `sensor.py`

Cut by what the module holds: 8a platform setup and the late-binding listener
(lines 1-290), 8b the identity, restore and removal of the four series sensors
and the fixed sensors (290-830), 8c the live path inside `EffySensor`.

Lines 82.1 % → 97.6 %, branches 58.5 % → 81.7 %.

The existing tests build the entity classes one by one
(`test_published_sensors`, `test_standby_sensor`); none ran `async_setup_entry`,
`_build_derived_entities` or the late-binding listener, nor the identity
properties.

| Where | Condition and direction not taken | Class | Reason and document |
| -- | -- | -- | -- |
| `sensor.py:104` | `_utcnow`: the real body | A | The restoration time published as `restored_at` (ADR-021 §6): current time, timezone-aware, UTC. |
| `sensor.py:155-200` (`180→181`, `180→189`, `182→183`, `182→185`, `194→195`, `194→200`) | `async_setup_entry`: the whole function, a source with / without a state, pending sources, the first recalculation | A | ADR-012 (which derived entity a source gets), ADR-013 Decision 1 and the function's comment (a source with no state yet is waited for, not dropped), `update_before_add=False`, one recalculation on first load. |
| `sensor.py:239-246` (`242→243`, `242→244`, `244→245`, `244→246`) | `_build_derived_entities`: energy-family source, power-family input, power-family output | A | Docstring and ADR-012: energy family gets a derived-power sensor (input or output), a power-family input a smoothed one, a power-family output neither. |
| `sensor.py:267-289` (`273→276`, `273→277`, `282→283`, `282→285`, `285→exit`, `285→286`) | the late-binding listener: no new state, source not pending, no entity to add, sources still waiting, all resolved | A | ADR-013 Decision 1 and the docstring: one-shot per entity, the listener removes itself when every pending source resolved, and is tied to the entry's unload. |
| `sensor.py:333`, `515`, `639`, `745`, `749` | `unique_id` of the effective, derived-power, smoothed and recalculated-from sensors; the fixed entity ID of the recalculated-from sensor (749) | A | The entity's identity in the registry (`{entry}_{slug}`, `…_power`, `…_smoothed`, `…_recalculated_from`); a change orphans the user's entity. |
| `sensor.py:336-339`, `519-521`, `643-645` | `name` of the three series sensors: with a friendly name, without one, without a state | A | Visible name: the source's friendly name or the slug, plus the series. |
| `sensor.py:347`, `529`, `653`, `753`, `887` | the `entity_id` setters (`pass`) | A | ADR-021 §5 publishes every sensor's entity ID (computed from the source, and `sensor.effy_recalculated_from` / `sensor.effy_standby_power` fixed by ADR-021 and ADR-018); an assignment (Home Assistant assigns one on add) does not change it. Whether Home Assistant accepts that is class D. |
| `sensor.py:351`, `533`, `657`, `757` | `device_info` | A | Every Effy entity belongs to the one Effy device. |
| `sensor.py:365→368`, `543→545`, `667→669` | restore of a numeric last state without a unit, `unit is not None`, false | A | ADR-021 §6: the value is restored, the unit stays unset until the first push. |
| `sensor.py:387→390`, `390→393`, `393→exit`; `557→560`, `560→exit`; `681→684`, `684→exit`; `818→exit` | `async_will_remove_from_hass` with a handle that is already `None` | B | Home Assistant calls it once, after `async_added_to_hass` set every handle; no document says what a removal without an add does. |
| `sensor.py:421→424`, `572→574`, `696→698` | `_on_updated` with `unit is None` | B | Its docstring calls the `(None, None)` push a defensive fallback "that shouldn't normally happen": value and unit come from one recalculation call (`_last_slot_values`). |
| `sensor.py:443-463` (`443→444`, `443→446`, `447→448`, `447→452`) | `EffySensor._on_distribution`, the whole function | C | The live path is disabled (coordinator banner, ADR-007): the callbacks stored by `EffyCoordinator.subscribe` are never called by anything in `custom_components/` (`_subscribers` is only written). See Q4. |

Tests added (`tests/test_sensor_setup.py`, new, 24 tests): setup with every kind
of source; a source without a state, waited for; the late-binding events one by
one (no state, unrelated entity, each source kind, the listener ending); restore
without a unit for the three series sensors and the kept attributes of the
effective one; unique ID, entity ID, assignment, name (three cases) and device
of the three series sensors; the same for the recalculated-from and standby
sensors; the restoration time.

Breaking the behaviour on purpose, 22 mutants (`sensor.py` checksum verified
after each), all killed: naive local time; every source deferred; pending
sources dropped; no first recalculation; `update_before_add`; outputs given no
derived entity; each of the three classification mistakes; None state resolving;
an unrelated entity resolving; a resolved source staying pending; an empty batch
added; the listener never removed, removed too early, or not tied to the entry's
unload; a unit invented on restore; the slug missing from the name; the
derived-power unique ID; the three `entity_id` setters (one honouring the
assignment, two raising).

## Slice 9 — `button.py`, `__init__.py`

`button.py`: lines 0 % → 100 %. `__init__.py`: lines 75.7 % → 100 %, branches 50
% → 100 %.

| Where | Condition and direction not taken | Class | Reason and document |
| -- | -- | -- | -- |
| `button.py` (all 31 statements) | the module: `async_setup_entry`, the entity, its device, `async_press` and its failure path | A | The diagnostic "Re-calculate History" button (ADR-004 overwrite semantics). `async_press`: runs the entry's coordinator recalculation and logs the count; a failure is logged and swallowed because a press has no caller (ADR-000 §8). |
| `__init__.py:65-67`, `66→67`, `66→69` | `async_unload_entry`: coordinator present / absent | A | Docstring and ADR-006 (one coordinator per entry): the coordinator is stopped first (listener, timer, in-flight job); an entry whose setup never stored one still unloads. |
| `__init__.py:69-72`, `70→71`, `70→72` | `async_unload_entry`: platforms unloaded / not | A | Home Assistant's contract: the result of the platform unload is the entry's; the coordinator is forgotten after a successful unload. See finding F3 for the other direction. |
| `__init__.py:77-78` | `_async_reload_entry` | A | Docstring: the entry is reloaded when its options change; `async_setup_entry` registers it as the entry's update listener. |

Tests added: `tests/test_button.py` (new, 6) and `tests/test_entry_lifecycle.py`
(new, 5, built on the loader and fakes of `test_migration`).

Breaking the behaviour on purpose, 17 mutants, all killed (checksums verified):
`button.py` 9 (update before add, category, unique ID, device, another entry's
coordinator, no recalculation, failure propagating, failure silent, count not
logged); `__init__.py` 8 (coordinator not shut down, shut down after the
platforms, kept after unload, `[]` instead of `.get`, a wrong result reported,
reload of another entry, different platforms, reload not registered).

## Result: branch coverage per module, before and after

Suite: 456 tests before, 556 after (100 added, none removed or changed). Gates
after the last slice: `ruff format --check` and `ruff check` clean,
`mypy --strict` exactly the 3 errors of the baseline
(`tests/test_catalog.py:43`, `tests/test_catalog.py:101`,
`tests/test_published_sensors.py:520`). No file under `custom_components/`
changed (verified by checksum before and after, and by a diff against the
delivered zip).

| Module | Lines before | Branches before | Not covered before (lines / branches) | Lines after | Branches after | Not covered after (lines / branches) |
| -- | -- | -- | -- | -- | -- | -- |
| `__init__.py` | 75.7 % | 50.0 % | 9 / 4 | 100.0 % | 100.0 % | 0 / 0 |
| `button.py` | 0.0 % | — | 31 / 0 | 100.0 % | — | 0 / 0 |
| `calculation.py` | 98.9 % | 95.5 % | 4 / 5 | 99.1 % | 96.4 % | 3 / 4 |
| `catalog.py` | 100.0 % | 100.0 % | 0 / 0 | 100.0 % | 100.0 % | 0 / 0 |
| `config_flow.py` | 98.2 % | 100.0 % | 1 / 0 | 100.0 % | 100.0 % | 0 / 0 |
| `const.py` | 100.0 % | — | 0 / 0 | 100.0 % | — | 0 / 0 |
| `coordinator.py` | 99.2 % | 94.2 % | 2 / 3 | 100.0 % | 100.0 % | 0 / 0 |
| `effy_exports.py` | 100.0 % | — | 0 / 0 | 100.0 % | — | 0 / 0 |
| `history.py` | 85.6 % | 78.4 % | 78 / 45 | 99.4 % | 98.1 % | 3 / 4 |
| `inputs.py` | 100.0 % | 100.0 % | 0 / 0 | 100.0 % | 100.0 % | 0 / 0 |
| `sensor.py` | 82.1 % | 58.5 % | 75 / 34 | 97.6 % | 81.7 % | 10 / 15 |
| `sensor_utils.py` | 61.5 % | 66.7 % | 15 / 4 | 74.4 % | 83.3 % | 10 / 2 |
| **total** | 88.1 % | 81.1 % | 215 / 95 | 98.6 % | 95.0 % | 26 / 25 |

What is left uncovered after the task (26 lines, 25 branches) is all class B or
C; no class A branch is left without a test:

| Class | Where | Count |
| -- | -- | -- |
| B | `calculation.py:117→118`, `357→358`, `1009→1015` | 3 branches (lines 118-119, 358) |
| C | `calculation.py:368→372` | 1 branch |
| B | `history.py:1177→1178`, `1233→1234`, `1702→1703`, `1900→1862` | 4 branches (lines 1178, 1234, 1703) |
| C | `sensor_utils.py:115-126` (`get_current_value`) | 2 branches, 10 lines |
| B | `sensor.py:387→390`, `390→393`, `393→exit`, `557→560`, `560→exit`, `681→684`, `684→exit`, `818→exit` | 8 branches |
| B | `sensor.py:421→424`, `572→574`, `696→698` | 3 branches |
| C | `sensor.py:443-463` (`EffySensor._on_distribution`) | 4 branches, 5 statement ranges |

## Class D: needs real Home Assistant

Listed, not covered here. Each is behaviour the stubs cannot show, although the
code around it is now tested against the shape the stub returns:

- The signatures and return shapes of `state_changes_during_period` (positional
  entity ID, `no_attributes`, `descending`, `include_start_time_state`,
  `limit`), `statistics_during_period` (`period="5minute"`, `units=None`,
  `types`, the `start` being a datetime or a float) and `get_metadata` (a
  `(id, meta)` pair per statistic). The tests assert what Effy passes and does
  with the answer, not that Home Assistant answers like that. See also F4.
- `_write_recorder_statistics` calling the recorder instance's
  `async_import_statistics(metadata, stats, table)` with the recorder's internal
  `Statistics` and `StatisticsShortTerm` classes (the module's own WARNING); the
  tests use a recorder double.
- Whether a real core accepts the metadata of `_build_statistic_metadata` (the
  real `StatisticMeanType` enum, `unit_class=None`). The newer-core branch runs
  against a one-member stand-in.
- What Home Assistant does with the ignored `entity_id` assignment (the setters
  of all five sensor classes): the registry may then name the entity differently
  from the ID Effy publishes.
- That `async_track_state_change_event` delivers `event.data["entity_id"]` and
  `event.data["new_state"]` as the late-binding listener reads them, and that
  `RestoreEntity` gives back what `_async_restore_measurement` expects.

## Findings

Nothing here needed a production change to be tested; each is reported, not
fixed.

- **F1 — redundant guard** (`history.py:1105-1106` and `1177-1178`). A
  power-family input without a `mean` row is skipped twice: by
  `if not raw_means: continue` and, if that were removed, by
  `if not slot_values: continue` further down. Removing the first alone changes
  no output (the mutant survives). Harmless; one of the two can go.
- **F2 — redundant clause** (`coordinator.py:657-658`).
  `except asyncio.CancelledError: raise` before `except Exception`:
  `CancelledError` is not an `Exception`, so the clause changes nothing. It
  documents the intent; the test pins the behaviour (a cancellation is not
  logged as a failure).
- **F3 — unload after a failed platform unload** (`__init__.py:62-72`). The
  coordinator is shut down (listener, timer, in-flight job cancelled) *before*
  the platforms are unloaded, and is dropped from `hass.data` only if they
  unloaded. If a platform fails to unload, the entry stays loaded with its
  coordinator registered but stopped, so its sensors no longer receive pushes.
  The tests assert the return value (`False`) and not what remains, because the
  right behaviour needs Home Assistant (class D). Proposed follow-up: shut the
  coordinator down only after the platforms unloaded, or restart it on failure.
- **F4 — stale docstring** (`history.py`, `_fetch_raw_energy_states`). The
  docstring says the call uses `significant_changes_only=False` and that the
  regular fetch uses `include_start_time_state=True`; the code passes neither
  (only `no_attributes=True`). Whether the defaults give the documented
  behaviour has to be checked against the real signature (class D). Proposed
  follow-up: confirm and correct the docstring, or pass the arguments.
- **F5 — docstring and behaviour differ** (`calculation.py:1009→1015`).
  `drain_state_from_restore` says malformed data gives `None`; a dump whose
  `resolution` is missing or not a mapping gives a state with an empty
  resolution instead. Harmless (the resolution is learned again), but one of the
  two should be changed. Class B for the branch itself; see Q2.
- **F6 — outdated ADR text** (`adr/007-total-increasing-live-delta.md`,
  Consequences). It says `get_current_value` is used "during initial setup
  seeding"; nothing calls it.

## Open questions for the human

None of these is decided here.

- **Q1** — `sensor_utils.get_current_value` (class C, no caller; ADR-007 still
  describes a use that no longer exists). Remove it with its ADR sentence, or
  keep it?
- **Q2** — `drain_state_from_restore` and a malformed `resolution` (F5): return
  `None` as documented, or fix the docstring?
- **Q3** — F3: shut down after the platforms unloaded, or leave as it is?
- **Q4** — dead remnants of the disabled live path:
  `EffySensor._on_distribution` (`sensor.py:427-463`) and the callbacks
  `EffyCoordinator.subscribe` stores but nothing calls; and
  `calculation.py:368→372`. Remove with the live path, keep for its return, or
  mark `# pragma: no cover`?
- **Q5** — the class B guards (list above). Mark any `# pragma: no cover` so the
  report reads 100 %, or leave them visible?
- **Q6** — a coverage threshold for the ADR-023 tasks. Measured now: 98.6 %
  lines, 95.0 % branches over `custom_components/effy`; all 25 branches left are
  class B or C, so marking all of them `# pragma: no cover` would read 100 % of
  the remaining branches. No threshold is configured (`[tool.coverage.report]`
  has no `fail_under`).
- **Q7** — F4: confirm against a real Home Assistant, or correct the docstring.
- **B for now (unclassifiable):** none.

## The A branches left uncovered

None. Every class A branch has at least one test that asserts the behaviour that
makes it class A, and every such test was broken on purpose at least once. Where
a guard turned out to be redundant (F1, F2) the behaviour it protects is pinned
by a mutant that changes the behaviour, and the report says so.

## What changed in the tests

New files: `tests/test_history_recorder_helpers.py` (36 tests),
`tests/test_history_compute_edges.py` (8), `tests/test_sensor_setup.py` (24),
`tests/test_button.py` (6), `tests/test_entry_lifecycle.py` (5).

Tests appended to existing files, no existing test touched:
`test_sensor_utils.py` (+5, plus the import of `SimpleNamespace` and
`get_sensor_meta`), `test_standby.py` (+1), `test_standby_glue.py` (+6),
`test_history_phase_timing.py` (+2), `test_config_flow.py` (+1),
`test_coordinator_slot.py` (+6).

One helper changed: `_load_history` in `test_history_recent_equivalence.py`
takes an optional `mean_type` (default unchanged), so a second copy of
`history.py` can be loaded as a newer core presents it. Every test of that file
passes unchanged.

Named after their module where a new file was needed (ADR-000 §6), except
`test_entry_lifecycle.py`, which covers `__init__.py`'s unload and reload.
