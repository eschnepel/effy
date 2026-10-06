# Project Dependencies

> Maintained by the Lead Agent. Workers append entries; never edit existing ones
> without Lead approval. One entry per library — no duplicates.

This change set adds **no new external dependencies**. Entries below record what
already exists so workers don't add redundant installs.

## homeassistant (runtime-provided)

- **Install:** already provided by the Home Assistant runtime (`hacs.json`: min
  2024.1.0) — do not install separately; never imported by `calculation.py`
- **Import:**
  `from homeassistant.components.recorder import get_instance as get_recorder`
  (as in `history.py`)
- **Added by task:** pre-existing
- **Purpose:** recorder / statistics APIs used by `history.py`; stubbed (not
  installed) in tests via `sys.modules` — see `tests/test_coordinator_slot.py`

---

## pytest 9.1.1

- **Install:** `uv sync` (dev group, pinned in `uv.lock`)
- **Import:** `import pytest`
- **Added by task:** pre-existing
- **Purpose:** test runner (`pytest.ini`: `asyncio_mode = auto`,
  `testpaths = tests`)

## pytest-asyncio 1.4.0

- **Install:** `uv sync` (dev group)
- **Import:** none needed — `asyncio_mode = auto`, write plain
  `async def test_...`
- **Added by task:** pre-existing
- **Purpose:** async tests (e.g. `async_recalculate_recent`)

## mypy 2.1.0

- **Install:** `uv sync` (dev group)
- **Import:** n/a (CLI):
  `mypy custom_components/effy tests --config-file mypy.ini`
- **Added by task:** pre-existing
- **Purpose:** `--strict` typing gate (ADR-000 §1)

## ruff 0.15.20

- **Install:** `uv sync` (dev group)
- **Import:** n/a (CLI): `ruff format custom_components/ tests/` and
  `ruff check custom_components/ tests/`
- **Added by task:** pre-existing
- **Purpose:** formatting + linting gate (ADR-000 §1), line length 100
