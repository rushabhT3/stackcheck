"""Audit A2/A3/A4 + B8/B9: plain errors, URL limits, labels, static, headers."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stackcheck.api.app import create_app
from stackcheck.config import Settings
from stackcheck.errors import FetchError
from stackcheck.ingest.sources import validate_source_url
from stackcheck.pipeline.jobs import mark_interrupted
from tests.conftest import make_pdf
from tests.unit.test_api import FakeRunner


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "web_dist_dir": tmp_path / "dist",
        "meta_api_key": "test",
        "baseten_api_key": "test",
        "fireworks_api_key": "test",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings=settings, runner=FakeRunner()))  # type: ignore[arg-type]


def test_validator_rejects_port_userinfo_empty_path() -> None:
    hosts = {"rera.bihar.gov.in"}
    for bad in [
        "https://rera.bihar.gov.in:8443/q.pdf",
        "https://user:pw@rera.bihar.gov.in/q.pdf",
        "https://rera.bihar.gov.in",
        "http://rera.bihar.gov.in/q.pdf",
        "not a url",
    ]:
        with pytest.raises(FetchError) as exc:
            validate_source_url(bad, hosts)
        assert "/data/" not in str(exc.value)
        assert "Traceback" not in str(exc.value)
        assert " _" not in str(exc.value).replace("https://", "")
    validate_source_url("https://rera.bihar.gov.in/q.pdf", hosts)


def test_unsupported_host_sentence_lists_sites() -> None:
    with pytest.raises(FetchError, match=r"rera\.bihar\.gov\.in") as exc:
        validate_source_url("https://example.com/q.pdf", {"rera.bihar.gov.in"})
    assert "isn't supported yet" in str(exc.value)


def test_check_rejects_blank_label(tmp_path: Path) -> None:
    response = _client(_settings(tmp_path)).post(
        "/api/checks",
        json={"project_label": "   ", "sources": [{"url": "https://rera.bihar.gov.in/q.pdf"}]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Enter a project name."


def test_check_rejects_long_label(tmp_path: Path) -> None:
    response = _client(_settings(tmp_path)).post(
        "/api/checks",
        json={"project_label": "x" * 81, "sources": [{"url": "https://rera.bihar.gov.in/q.pdf"}]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Project name must be 80 characters or fewer."


def test_legacy_error_text_mapped_on_read(tmp_path: Path) -> None:
    from stackcheck.pipeline.jobs import JobRunner

    settings = _settings(tmp_path)
    root = settings.data_dir / "work" / "chk_legacy"
    root.mkdir(parents=True)
    (root / "status.json").write_text(
        '{"check_id": "chk_legacy", "project_label": "X", "state": "failed",'
        ' "steps": [], "progress": 0.1, "created_at": "2026-01-01T00:00:00+00:00",'
        ' "updated_at": "2026-01-01T00:00:00+00:00",'
        ' "error": "Upload abc was not registered in /data/work/chk_x/sources"}',
        encoding="utf-8",
    )
    state = JobRunner(settings, run=None).status("chk_legacy")  # type: ignore[arg-type]
    assert state.error == "Something went wrong while reading these filings. Try again, or upload the PDFs."


def test_check_rejects_bad_port_and_userinfo(tmp_path: Path) -> None:
    client = _client(_settings(tmp_path))
    for url in [
        "https://rera.bihar.gov.in:8443/q.pdf",
        "https://user:pw@rera.bihar.gov.in/q.pdf",
    ]:
        response = client.post("/api/checks", json={"project_label": "T", "sources": [{"url": url}]})
        assert response.status_code == 400
        assert "doesn't look like a link" in response.json()["error"]["message"]


def test_upload_filename_is_basename(tmp_path: Path) -> None:
    data = make_pdf(tmp_path / "q.pdf", [{"text": "hello"}]).read_bytes()
    response = _client(_settings(tmp_path)).post(
        "/api/uploads", files={"file": ("../../etc/x.pdf", data, "application/pdf")}
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "x.pdf"


def _check_body() -> dict:
    return {"project_label": "T", "sources": [{"url": "https://rera.bihar.gov.in/q.pdf"}]}


def test_fourth_simultaneous_check_per_ip_returns_429(tmp_path: Path) -> None:
    settings = _settings(tmp_path, per_ip_active_max=3, per_ip_hourly_max=100)
    client = _client(settings)
    body = _check_body()
    for _ in range(3):
        assert client.post("/api/checks", json=body).status_code == 202
    denied = client.post("/api/checks", json=body)
    assert denied.status_code == 429
    assert "busy" in denied.json()["error"]["message"].lower()


def test_hourly_limit_returns_429(tmp_path: Path) -> None:
    settings = _settings(tmp_path, per_ip_active_max=100, per_ip_hourly_max=3)
    client = _client(settings)
    body = _check_body()
    for _ in range(3):
        assert client.post("/api/checks", json=body).status_code == 202
    denied = client.post("/api/checks", json=body)
    assert denied.status_code == 429


def test_mark_interrupted_is_plain(tmp_settings: Settings) -> None:
    root = tmp_settings.data_dir / "work" / "chk_x"
    root.mkdir(parents=True)
    (root / "status.json").write_text(
        '{"check_id": "chk_x", "project_label": "X", "state": "running",'
        ' "steps": [], "progress": 0.1, "created_at": "2026-01-01T00:00:00+00:00",'
        ' "updated_at": "2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    assert mark_interrupted(tmp_settings) == 1


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("x", encoding="utf-8")
    return dist


def test_unknown_file_returns_404_not_shell(tmp_path: Path) -> None:
    _dist(tmp_path)
    client = _client(_settings(tmp_path))
    assert client.get("/nope.js").status_code == 404
    assert client.get("/favicon.ico").status_code == 404
    assert client.get("/some/route").status_code == 200


def test_real_favicon_served_not_shell(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    response = _client(_settings(tmp_path)).get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] in ("image/x-icon", "image/vnd.microsoft.icon")


def test_security_headers(tmp_path: Path) -> None:
    response = _client(_settings(tmp_path)).get("/api/health")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["strict-transport-security"] == "max-age=31536000"
