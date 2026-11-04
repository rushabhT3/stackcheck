# T01: Project scaffold, config, errors, contracts

**Owner:** Muse Code. **Day:** D1. **Read:** AGENTS.md; CONTRACTS C1, C2, C3; ARCHITECTURE "Approved dependencies" and "Repo layout".

## Goal

Create the Python package skeleton with every contract model, configuration and error type, so every later card builds on fixed shapes. Nothing here calls an API.

## Files

Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`, `PROGRESS.md`, `README.md` (stub), `src/stackcheck/__init__.py`, `config.py`, `errors.py`, `models.py`, empty packages `llm/`, `ingest/`, `extract/` (with `prompts/`), `vision/`, `rules/`, `pipeline/`, `report/`, `api/` (each with `__init__.py`), `scripts/` (empty), `tests/conftest.py`, `tests/unit/test_models.py`, `tests/unit/test_config.py`, `tests/unit/test_repo_hygiene.py`, `data/sources.csv` (header only), `data/bench/.gitkeep`, `data/truth/.gitkeep`, `data/samples/.gitkeep`.

## Steps

1. `pyproject.toml`, exactly:

```toml
[project]
name = "stackcheck"
version = "0.1.0"
description = "Checks RERA quarterly progress reports against the builder's own photos and numbers."
readme = "README.md"
requires-python = ">=3.13"
license = "AGPL-3.0-or-later"
dependencies = [
  "fastapi>=0.162.0",
  "uvicorn>=0.54.0",
  "python-multipart>=0.0.32",
  "pydantic>=2.14.1",
  "pydantic-settings>=2.16.2",
  "openai>=3.40.0",
  "httpx>=0.28.1",
  "pymupdf>=2.0",
  "pillow>=13.0.0",
  "numpy>=2.5.4",
]

[dependency-groups]
dev = [
  "pytest>=9.2.0",
  "pytest-asyncio>=1.4.1",
  "pytest-cov>=7.1.0",
  "respx>=0.23.1",
  "ruff>=0.17.3",
]

[build-system]
requires = ["uv_build>=0.13.6,<0.14"]
build-backend = "uv_build"

[tool.ruff]
line-length = 110
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF", "ASYNC", "PTH"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = ["live: calls real paid APIs; run only with STACKCHECK_LIVE=1"]
addopts = "-m 'not live'"
```

The licence is AGPL because PyMuPDF is AGPL. This is fine for a public hackathon repo.

2. `.python-version` contains `3.15`.
3. `.gitignore`:

```text
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.coverage
data/work/
data/cache/
data/uploads/
data/eval/
data/ledger.jsonl
*.pdf
web/node_modules/
web/dist/
```

4. `config.py`, `errors.py`, `models.py`: copy C1, C2, C3 exactly. Add `DISCLAIMER` to `models.py`. Add `__version__ = "0.1.0"` in `__init__.py`.
5. `.env.example`: every env variable from C1 with an empty value or its default, one per line, grouped with comments.
6. `PROGRESS.md`: title, a one-line description, and the report template from AGENTS.md section 9.
7. `README.md`: one line ("StackCheck: work in progress for the Meta Global AI Developer Hackathon, Oct 26 to Nov 4, 2026.").
8. `tests/conftest.py`: a `tmp_settings` fixture returning `Settings(data_dir=tmp_path / "data", meta_api_key="test", baseten_api_key="test", fireworks_api_key="test")`. Leave the PDF and image factories for T04.
9. `uv sync`, then run the checks.

## Tests first

- `test_models.py::test_page_ref_rejects_zero_page`: `PageRef(doc_id="x", page=0)` raises `ValidationError`.
- `test_models.py::test_quarter_filing_defaults_are_independent`: two `QuarterFiling(doc_id=...)` instances don't share `blocks`, `activities`, `notes`, `refs` or `financials` objects.
- `test_models.py::test_box_properties`: width, height, cx, cy, area for `Box(x1=10, y1=20, x2=30, y2=60)`.
- `test_models.py::test_finding_title_length_limit`: a 91-character title raises.
- `test_models.py::test_report_round_trip_json`: build a minimal `Report`, dump with `model_dump_json`, load with `model_validate_json`, equal.
- `test_config.py::test_env_aliases`: with `monkeypatch.setenv("MODEL_API_KEY", "k1")` and `STACKCHECK_READER_MODEL=x`, `Settings()` reads both.
- `test_config.py::test_model_for_privacy_rule`: `model_for("url", s) == s.reader_model`, `model_for("upload", s) == s.private_model`.
- `test_repo_hygiene.py::test_no_forbidden_files_tracked`: if `git` and `.git` exist, run `git ls-files` and assert no path ends with `.pdf`, none starts with `data/work/`, `data/cache/`, `data/uploads/`, and `.env` is absent. Skip otherwise.

## Done when

`uv run pytest -q` is green, ruff is clean, and the tree matches ARCHITECTURE "Repo layout" for the folders created here.

## How an expert thinks about it

The contracts are the spine. If they're right and frozen on day 1, every later card is a small, testable piece that snaps in. So this card is mostly copying with care: one wrong field name here becomes a bug in eight modules. The independence test exists because shared mutable defaults are the classic Pydantic surprise.

## Traps

- Don't add anything to the models that isn't in C3, even helpful-looking fields.
- `pydantic-settings` needs `validation_alias` for the non-prefixed env names; `env_prefix` doesn't apply to aliased fields. With an alias set, building `Settings(meta_api_key="test")` by field name only works because C1 sets `validate_by_name=True`. Keep that line. The tests cover both paths.
- Don't create `web/` yet (T11).
