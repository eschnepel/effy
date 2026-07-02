# ADR-000 – Code Quality Standards, Programming Style & Core Concepts

**Date:** 2026-06-30
**Status:** Accepted

---

## Context

This ADR documents the overarching engineering conventions used throughout
the Effy codebase. It exists so that contributors and future maintainers can
understand *why* the code looks the way it does without having to infer it
from individual diffs. The numbered ADRs (001 onward) cover specific
domain decisions; this one covers everything that applies uniformly across
all files.

---

## Decision

### 1 — Tooling: ruff, mypy strict, pytest

Three tools gate every change, run via `.github/workflows/ci.yml`:

| Tool | Purpose | Invocation |
|---|---|---|
| `ruff format` | Code formatting (replaces black) | `ruff format custom_components/` |
| `ruff check` | Linting (replaces flake8/isort/pyupgrade) | `ruff check custom_components/` |
| `mypy --strict` | Static type checking | `mypy custom_components/effy tests --config-file mypy.ini` |
| `pytest` | Unit tests | `pytest tests/` |

All four must pass with zero errors before a change is considered complete.
`mypy --strict` is non-negotiable: every function signature carries full
type annotations, including return types on methods that return `None`.

### 2 — Handling Home Assistant's untyped surface

Home Assistant ships without inline type stubs for many of its base classes
and decorators (`SensorEntity`, `ConfigFlow`, `@callback`, etc.). Under
`mypy --strict` this produces two categories of unavoidable noise:

- `misc` errors when subclassing a base class typed as `Any`.
- `untyped-decorator` errors when `@callback` wraps a method.

These are suppressed **per-file**, not globally, via `mypy.ini`
(`warn_unused_ignores = False` on the specific modules that subclass HA
entities or use `@callback`), combined with targeted
`# type: ignore[<code>]` comments at the exact line mypy flags. Suppression
is never broad (no bare `# type: ignore` without a code, no
`disable_error_code` at the global `[mypy]` level) — the goal is to silence
exactly the HA-stub gap, not to weaken type checking elsewhere.

The comment goes on the exact source line mypy reports, not on a nearby
line: `misc` errors on subclassing are attached to the base-class entry in
the class statement (e.g. `class EffySensor(SensorEntity):  # type:
ignore[misc]`), and `untyped-decorator` errors are attached to the
`@callback` line itself, not the `def` line below it — mypy's reported line
number is authoritative and should never be guessed at.

`calculation.py` has zero Home Assistant imports and is held to the full,
unsuppressed strict standard — it is pure, framework-independent Python and
is tested as such (see §6).

### 3 — Module boundaries and dependency direction

```
calculation.py  (pure logic, no HA imports, no I/O)
       ↑
sensor_utils.py (reads HA state → SensorReading)
       ↑
coordinator.py  (orchestrates: cache + debounce + distribute_loss)
       ↑
sensor.py / button.py / history.py  (HA entity glue)
       ↑
__init__.py     (wires platforms + coordinator into hass.data)
```

Dependencies point upward only. `calculation.py` never imports from any
other Effy module. This separation is what allows `calculation.py` to be
unit-tested in complete isolation from Home Assistant (see §6) and is the
foundation that ADR-001/002/005 build on.

### 4 — Type-hinting conventions

- `from __future__ import annotations` at the top of every module — allows
  modern `list[str] | None` syntax without runtime evaluation cost and
  without requiring Python 3.10+ at runtime (HA's actual minimum is lower).
- Built-in generics (`list[str]`, `dict[str, float]`) are used directly;
  `typing.List`/`typing.Dict` are never imported.
- `X | None` is used instead of `Optional[X]`.
- Every function and method has a complete signature: parameter types and
  a return type, including `-> None`. This applies to private helpers
  (`_to_w`, `_fresh`) and test code exactly as it does to public HA-facing
  methods — `mypy --strict` does not distinguish, and a single unannotated
  parameter (e.g. a default-valued `unit="Wh"`) triggers `no-untyped-def`
  just as an entirely bare signature does.
- `@dataclass` is used for plain data containers (`SensorReading`,
  `LossDistribution`) instead of dicts or named tuples — gives attribute
  access, auto-generated `__init__`/`__repr__`/`__eq__`, and a single place
  to add validation later if needed.

### 5 — Naming and structure

- Module-level constants are `UPPER_SNAKE_CASE` and live in `const.py`
  (cross-module) or at the top of the module that owns them (single-use,
  e.g. `DEBOUNCE_SECONDS` in `coordinator.py`).
- Private helpers are prefixed with a single underscore (`_to_w`,
  `_get_unit`) and are not exported.
- HA-facing entities (`EffySensor`, `EffyRecalculateButton`,
  `EffyCoordinator`, `EffyConfigFlow`, `EffyOptionsFlow`) are all prefixed
  `Effy` for discoverability when grepping or reading stack traces.
- One concept per module: `calculation.py` only computes, `history.py` only
  reads/writes statistics, `coordinator.py` only orchestrates live updates.
  A module that starts doing two unrelated things is a signal to split it
  (this is why the coordinator was extracted out of `sensor.py` — ADR-006).

### 6 — Testing philosophy

- `calculation.py` is unit-tested with **zero mocking** — no `unittest.mock`,
  no fake `hass` object. Because it has no Home Assistant dependency, tests
  call the real functions with real `SensorReading` instances and assert on
  real return values. This is only possible *because* of the module boundary
  in §3; it is the practical payoff of that design choice.
- Tests are loaded via direct file-path import
  (`importlib.util.spec_from_file_location`) rather than package import,
  specifically to avoid pulling in `custom_components/effy/__init__.py`
  (which imports `homeassistant.*`) just to test a dependency-free module.
  This keeps the test environment lightweight (`pytest` only — no
  `pytest-homeassistant-custom-component` needed).
- Because file-path loading yields plain `ModuleType` objects, mypy cannot
  see the real classes on attributes such as `_calc_mod.SensorReading` —
  it only sees `Any`. Test files still get full static typing for these
  names via a `TYPE_CHECKING`-only static import that mirrors the runtime
  path (`if TYPE_CHECKING: from effy.calculation import SensorReading as
  SensorReading`). This import is never executed (it runs only under
  static analysis), so it does not reintroduce the `homeassistant`
  dependency the file-path loading was designed to avoid; the runtime
  assignment (`SensorReading = _calc_mod.SensorReading`) is correspondingly
  guarded with `if not TYPE_CHECKING:` so the two bindings never conflict.
  This is mandatory for any test module that binds a dynamically-loaded
  class to a name used later as a type annotation — leaving it untyped
  cascades into dozens of unrelated `attr-defined`/`valid-type` mypy errors
  on every usage of that name, rather than a single fixable root cause.
- Every test class documents the scenario it covers in a docstring or
  comment referencing the worked example in the README where applicable
  (see `TestWaterfallHardOverflow`, which mirrors the overflow logic
  demonstrated in the README's worked example).
- Invariant checks (`sum(effective_inputs) == sum(outputs)`) are asserted
  explicitly in tests, not just spot-checked values — the conservation
  property is the most important correctness guarantee of the whole system
  and is tested as a first-class assertion in every scenario class.

### 7 — Documentation: ADRs over inline essays

Design rationale lives in `adr/`, not in large module docstrings or inline
comment blocks. Module docstrings stay short (what the module does, 1–3
sentences); the *why* behind non-obvious decisions is captured once in an
ADR and referenced by number from the code (e.g. `# Cap at 0 (ADR-005)`).
This avoids rationale drifting out of sync with the code, since an ADR is
versioned independently and can be marked `Superseded` if a decision changes,
without having to hunt down every comment that explained it.

### 8 — Error handling

- User-facing errors (config flow validation) return error keys
  (`"at_least_one_input"`) resolved via `translations/*.json`, never raw
  exception text — keeps the UI translatable and avoids leaking internals.
- Background failures (history recalculation, button press) are logged via
  `_LOGGER.exception`/`_LOGGER.warning` and swallowed rather than raised,
  since these run outside a request/response cycle where there is no caller
  to propagate the exception to.
- `calculation.py` raises no exceptions in its normal operating range; it
  uses `max(0.0, ...)` clamps (ADR-005) instead of validation errors, because
  the inputs are live sensor readings that are expected to occasionally be
  noisy rather than invalid.

---

## Consequences

- **Pro:** A new contributor can run four commands
  (`ruff format --check`, `ruff check`, `mypy --strict`, `pytest`) and know
  immediately whether their change meets the bar.
- **Pro:** The pure-logic/HA-glue split makes the core algorithm trivially
  testable and reusable (e.g. it could power a non-HA CLI tool unchanged).
- **Pro:** ADRs prevent "tribal knowledge" about *why* a clamp or a
  suppression exists from living only in a pull request that gets buried.
- **Con:** Strict mypy plus per-file suppression configuration is more
  upfront setup than "just ignore HA imports everywhere" — but it means type
  errors in actual business logic are never silently masked by a blanket
  ignore.
