"""The frontend's /api calls must exist in the backend OpenAPI with the same method."""

import re
from pathlib import Path

from stackcheck.api.app import create_app
from stackcheck.config import Settings
from stackcheck.errors import StackCheckError


def _frontend_calls() -> dict[str, tuple[str, str]]:
    src = (Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    calls: dict[str, tuple[str, str]] = {}
    for chunk in re.split(r"(?=export async function )", src):
        name = re.match(r"export async function (\w+)", chunk)
        path = re.search(r"request\(\s*[\"`]([^\"`]+)[\"`]", chunk)
        if not name or not path or not path.group(1).startswith("/api/"):
            continue
        method = "POST" if 'method: "POST"' in chunk else "GET"
        calls[name.group(1)] = (method, path.group(1))
    return calls


def _normalize(path: str) -> str:
    return re.sub(r"\{[^}]*\}|\$\{[^}]*\}", "{p}", path)


class _Runner:
    def submit(self, *args: object) -> str:
        raise AssertionError("must not submit")

    def status(self, check_id: str) -> object:
        raise StackCheckError("unknown")


def test_frontend_api_paths_match_backend(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path, meta_api_key="test", baseten_api_key="test", fireworks_api_key="test"
    )
    app = create_app(settings=settings, runner=_Runner())  # type: ignore[arg-type]
    backend = {
        (method.upper(), _normalize(path)) for path, ops in app.openapi()["paths"].items() for method in ops
    }
    calls = _frontend_calls()
    assert calls, "no /api calls parsed from api.ts"
    for name, (method, path) in calls.items():
        assert (method, _normalize(path)) in backend, f"{name}: {method} {path}"
