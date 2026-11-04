"""Unit tests for the HTTP API. TestClient with a fake runner, no network."""

import importlib
import importlib.metadata
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stackcheck.api.app import create_app
from stackcheck.config import Settings
from stackcheck.errors import StackCheckError
from stackcheck.ingest.sources import _allowed_hosts
from stackcheck.models import (
    CheckStatus,
    CostSummary,
    ProjectEvidence,
    QuarterEvidence,
    QuarterFiling,
    Report,
    SourceDoc,
    StepStatus,
    Verdict,
)
from stackcheck.pipeline.jobs import new_check_id
from stackcheck.pipeline.run_check import check_dir, write_json
from stackcheck.report.build import build_report
from tests.conftest import make_pdf, make_png


class FakeRunner:
    """Stands in for JobRunner: records submits, serves scripted statuses."""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, list, object]] = []
        self.floors: list[object] = []
        self.statuses: dict[str, CheckStatus] = {}

    def submit(
        self, project_label: str, sources: list, video: object = None, floors_above_ground: object = None
    ) -> str:
        check_id = new_check_id()
        self.submitted.append((project_label, sources, video))
        self.floors.append(floors_above_ground)
        return check_id

    def status(self, check_id: str) -> CheckStatus:
        try:
            return self.statuses[check_id]
        except KeyError:
            raise StackCheckError(f"Unknown check {check_id}") from None


def _pdf(tmp_settings: Settings, name: str) -> bytes:
    """Build a synthetic PDF, creating the folder first."""
    path = tmp_settings.data_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return make_pdf(path, [{"text": "hello"}]).read_bytes()


def _client(tmp_settings: Settings, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(settings=tmp_settings, runner=runner))  # type: ignore[arg-type]


def _status(state: str) -> CheckStatus:
    now = datetime.now(UTC)
    return CheckStatus(
        check_id="chk_" + "0" * 10,
        project_label="T",
        state=state,  # type: ignore[arg-type]
        steps=[StepStatus(name="fetch", state="done")],
        progress=1.0 if state == "done" else 0.5,
        created_at=now,
        updated_at=now,
    )


def test_health(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert isinstance(response.json()["video_tools"], bool)


def test_upload_pdf(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/uploads", files={"file": ("q.pdf", _pdf(tmp_settings, "q.pdf"), "application/pdf")}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["upload_id"]) == 16
    saved = tmp_settings.data_dir / "uploads" / "sources" / f"{body['upload_id']}.pdf"
    assert saved.exists()


def test_upload_accepts_png_as_photo(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/uploads", files={"file": ("a.png", make_png(10, 10, "stripes"), "image/png")}
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "photo"


def test_upload_over_cap(tmp_settings: Settings) -> None:
    settings = tmp_settings.model_copy(update={"fetch_max_bytes": 10})
    response = _client(settings, FakeRunner()).post(
        "/api/uploads", files={"file": ("q.pdf", _pdf(tmp_settings, "q.pdf"), "application/pdf")}
    )
    assert response.status_code == 413


def _mp4() -> bytes:
    """Magic bytes of an mp4: size + ftyp. Not a playable video."""
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _webm() -> bytes:
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def test_upload_video_mp4(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stackcheck.api.app.probe_duration", lambda path: 12.5)
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/uploads", files={"file": ("v.mp4", _mp4(), "video/mp4")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "video"
    saved = tmp_settings.data_dir / "uploads" / "sources" / f"{body['upload_id']}.mp4"
    assert saved.exists()


def test_upload_video_webm_extension(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stackcheck.api.app.probe_duration", lambda path: 5.0)
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/uploads", files={"file": ("v.webm", _webm(), "video/webm")}
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "video"


def test_upload_video_rejects_bad_magic(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/uploads", files={"file": ("v.mp4", b"not a video" * 10, "video/mp4")}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_a_video"


def test_upload_video_rejects_long_video(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stackcheck.api.app.probe_duration", lambda path: 61.0)
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/uploads", files={"file": ("v.mp4", _mp4(), "video/mp4")}
    )
    assert response.status_code == 400


def test_upload_video_over_cap(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stackcheck.api.app.probe_duration", lambda path: 5.0)
    settings = tmp_settings.model_copy(update={"video_max_bytes": 10})
    response = _client(settings, FakeRunner()).post(
        "/api/uploads", files={"file": ("v.mp4", _mp4(), "video/mp4")}
    )
    assert response.status_code == 413


def _uploaded_pdf_id(tmp_settings: Settings) -> str:
    client = _client(tmp_settings, FakeRunner())
    response = client.post(
        "/api/uploads", files={"file": ("q.pdf", _pdf(tmp_settings, "q.pdf"), "application/pdf")}
    )
    assert response.status_code == 200
    return response.json()["upload_id"]


def test_check_with_video_needs_confirmation(tmp_settings: Settings) -> None:
    pdf_id = _uploaded_pdf_id(tmp_settings)
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/checks",
        json={
            "project_label": "X",
            "sources": [{"upload_id": pdf_id}],
            "video_upload_id": "1" * 16,
            "video_same_project": False,
        },
    )
    assert response.status_code == 400


def test_check_with_video_passes_to_runner(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf_id = _uploaded_pdf_id(tmp_settings)
    monkeypatch.setattr("stackcheck.api.app.probe_duration", lambda path: 12.5)
    client = _client(tmp_settings, FakeRunner())
    video = client.post("/api/uploads", files={"file": ("v.mp4", _mp4(), "video/mp4")})
    assert video.status_code == 200
    video_id = video.json()["upload_id"]
    runner = FakeRunner()
    response = _client(tmp_settings, runner).post(
        "/api/checks",
        json={
            "project_label": "X",
            "sources": [{"upload_id": pdf_id}],
            "video_upload_id": video_id,
            "video_same_project": True,
            "video_block_label": "A",
        },
    )
    assert response.status_code == 202
    assert runner.submitted and runner.submitted[0][2] is not None
    assert runner.submitted[0][2].upload_id == video_id
    assert runner.submitted[0][2].block_label == "A"


def test_check_video_only_skips_confirmation(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Video without a filing needs no same-project confirmation."""
    monkeypatch.setattr("stackcheck.api.app.probe_duration", lambda path: 12.5)
    client = _client(tmp_settings, FakeRunner())
    video = client.post("/api/uploads", files={"file": ("v.mp4", _mp4(), "video/mp4")})
    assert video.status_code == 200
    runner = FakeRunner()
    response = _client(tmp_settings, runner).post(
        "/api/checks",
        json={
            "project_label": "X",
            "sources": [],
            "video_upload_id": video.json()["upload_id"],
            "video_same_project": False,
        },
    )
    assert response.status_code == 202
    assert runner.submitted and runner.submitted[0][2] is not None


def test_checks_rejects_bad_host(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/checks",
        json={"project_label": "X", "sources": [{"url": "https://evil.example/x.pdf"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "host_not_allowed"


def test_checks_rejects_too_many_sources(tmp_settings: Settings) -> None:
    host = sorted(_allowed_hosts(tmp_settings))[0]
    sources = [{"url": f"https://{host}/{i}.pdf"} for i in range(25)]
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/checks", json={"project_label": "X", "sources": sources}
    )
    assert response.status_code == 422


def test_checks_rejects_two_ids(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/checks",
        json={
            "project_label": "X",
            "sources": [{"url": "https://example.com/x.pdf", "upload_id": "a" * 16}],
        },
    )
    assert response.status_code == 422


def test_checks_accepts_valid(tmp_settings: Settings) -> None:
    runner = FakeRunner()
    host = sorted(_allowed_hosts(tmp_settings))[0]
    response = _client(tmp_settings, runner).post(
        "/api/checks",
        json={"project_label": "X", "sources": [{"url": f"https://{host}/x.pdf"}]},
    )
    assert response.status_code == 202
    assert response.json()["check_id"].startswith("chk_")
    assert runner.submitted and runner.submitted[0][0] == "X"


def test_checks_passes_floors_above_ground(tmp_settings: Settings) -> None:
    runner = FakeRunner()
    host = sorted(_allowed_hosts(tmp_settings))[0]
    response = _client(tmp_settings, runner).post(
        "/api/checks",
        json={
            "project_label": "X",
            "sources": [{"url": f"https://{host}/x.pdf"}],
            "floors_above_ground": 5,
        },
    )
    assert response.status_code == 202
    assert runner.floors == [5]


def test_checks_rejects_zero_floors(tmp_settings: Settings) -> None:
    host = sorted(_allowed_hosts(tmp_settings))[0]
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/checks",
        json={
            "project_label": "X",
            "sources": [{"url": f"https://{host}/x.pdf"}],
            "floors_above_ground": 0,
        },
    )
    assert response.status_code == 422


def test_status_unknown(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).get("/api/checks/chk_0000000000")
    assert response.status_code == 404


def test_status_known(tmp_settings: Settings) -> None:
    runner = FakeRunner()
    runner.statuses["chk_" + "0" * 10] = _status("running")
    response = _client(tmp_settings, runner).get("/api/checks/chk_0000000000")
    assert response.status_code == 200
    assert response.json()["state"] == "running"


def _write_report(tmp_settings: Settings) -> str:
    check_id = "chk_" + "0" * 10
    now = datetime.now(UTC)
    report = Report(
        check_id=check_id,
        project_label="T",
        created_at=now,
        verdict=Verdict(
            headline="No gaps found in what we could check",
            flags_high=0,
            flags_medium=0,
            flags_low=0,
            cant_verify=0,
            ok=1,
        ),
        findings=[],
        evidence=ProjectEvidence(project_label="T", quarters=[]),
        trace=[],
        summary_en=None,
        costs=CostSummary(usd_total=Decimal("0"), by_model={}, calls=0, cache_hits=0),
        versions={},
        disclaimer="test",
    )
    work = check_dir(tmp_settings, check_id)
    write_json(work / "report.json", report)
    return check_id


def test_report_before_done(tmp_settings: Settings) -> None:
    runner = FakeRunner()
    runner.statuses["chk_" + "0" * 10] = _status("running")
    response = _client(tmp_settings, runner).get("/api/checks/chk_0000000000/report")
    assert response.status_code == 404


def test_report_after_done(tmp_settings: Settings) -> None:
    check_id = _write_report(tmp_settings)
    runner = FakeRunner()
    runner.statuses[check_id] = _status("done")
    response = _client(tmp_settings, runner).get(f"/api/checks/{check_id}/report")
    assert response.status_code == 200
    assert response.json()["check_id"] == check_id


def test_media_traversal_blocked(tmp_settings: Settings, tmp_path: Path) -> None:
    settings = tmp_settings.model_copy(update={"web_dist_dir": tmp_path / "nope"})
    client = _client(settings, FakeRunner())
    assert client.get("/media/chk_0000000000/../../.env").status_code == 404


def test_media_slash_blocked(tmp_settings: Settings) -> None:
    client = _client(tmp_settings, FakeRunner())
    assert client.get("/media/chk_0000000000/a/b.png").status_code == 404


def _no_distribution(name: str) -> str:
    raise importlib.metadata.PackageNotFoundError(name)


def test_app_imports_without_package_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Modal copies source without metadata; the app must still import."""
    import stackcheck.api.app as app_module

    monkeypatch.setattr(importlib.metadata, "version", _no_distribution)
    try:
        importlib.reload(app_module)
        assert app_module.APP_VERSION == "0.0.0+deploy"
    finally:
        monkeypatch.undo()
        importlib.reload(app_module)


async def test_redacted_report_served(tmp_settings: Settings, tmp_path: Path) -> None:
    """Deploy path: redact_names=True blanks project names in the served report."""
    settings = tmp_settings.model_copy(update={"redact_names": True, "web_dist_dir": tmp_path / "dist"})
    evidence = ProjectEvidence(
        project_label="T",
        quarters=[
            QuarterEvidence(
                source=SourceDoc(
                    doc_id="d" * 16,
                    origin="url",
                    url="https://example.com/q.pdf",
                    filename="q.pdf",
                    sha256="s",
                    retrieved_at=datetime.now(UTC),
                    page_count=1,
                ),
                filing=QuarterFiling(
                    doc_id="d" * 16,
                    project_name="Sunrise Heights",
                    promoter_name="Sunrise Heights Builders",
                ),
            )
        ],
    )
    check_id = "chk_" + "0" * 10
    report = await build_report(
        check_id=check_id,
        project_label="T",
        evidence=evidence,
        findings=[],
        trace=[],
        settings=settings,
    )
    write_json(check_dir(settings, check_id) / "report.json", report)
    runner = FakeRunner()
    runner.statuses[check_id] = _status("done")
    body = _client(settings, runner).get(f"/api/checks/{check_id}/report").json()
    assert body["evidence"]["quarters"][0]["filing"]["project_name"] == "[redacted]"


def test_static_encoded_traversal_stays_inside_dist(tmp_settings: Settings, tmp_path: Path) -> None:
    """Encoded ../ must give 404 or the SPA shell, never a file outside dist."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    index = "<html>app</html>"
    (dist / "index.html").write_text(index, encoding="utf-8")
    (tmp_path / "canary.txt").write_text("CANARY", encoding="utf-8")
    settings = tmp_settings.model_copy(update={"web_dist_dir": dist})
    client = _client(settings, FakeRunner())
    # httpx decodes %2e but keeps .. segments, so these reach the app unnormalized.
    urls = ("/%2e%2e/canary.txt", "/..%2fcanary.txt", "/assets/%2e%2e/%2e%2e/canary.txt")
    for url in urls:
        response = client.get(url)
        assert response.status_code in (404, 200)
        assert "CANARY" not in response.text
        if response.status_code == 200:
            assert response.text == index


def test_media_serves_png(tmp_settings: Settings) -> None:
    check_id = "chk_" + "0" * 10
    media = check_dir(tmp_settings, check_id) / "media"
    media.mkdir(parents=True)
    (media / "a.overlay.png").write_bytes(make_png(10, 10, "stripes"))
    response = _client(tmp_settings, FakeRunner()).get(f"/media/{check_id}/a.overlay.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_media_serves_thumb_jpg(tmp_settings: Settings) -> None:
    check_id = "chk_" + "0" * 10
    media = check_dir(tmp_settings, check_id) / "media"
    media.mkdir(parents=True)
    (media / "a.overlay.thumb.jpg").write_bytes(make_png(10, 10, "stripes"))
    response = _client(tmp_settings, FakeRunner()).get(f"/media/{check_id}/a.overlay.thumb.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_static_cache_headers(tmp_settings: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "logo.png").write_bytes(make_png(10, 10, "stripes"))
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    settings = tmp_settings.model_copy(update={"web_dist_dir": dist})
    client = _client(settings, FakeRunner())
    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["cache-control"] == "no-cache"
    plain = client.get("/logo.png")
    assert plain.status_code == 200
    assert plain.headers["cache-control"] == "no-cache"
    hashed = client.get("/assets/app-abc123.js")
    assert hashed.status_code == 200
    assert hashed.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_favicon_served_not_index(tmp_settings: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x00ICO-DATA")
    settings = tmp_settings.model_copy(update={"web_dist_dir": dist})
    response = _client(settings, FakeRunner()).get("/favicon.ico")
    assert response.status_code == 200
    assert b"ICO-DATA" in response.content
    assert "<html>app</html>" not in response.text


def test_samples_empty(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).get("/api/samples")
    assert response.status_code == 200
    assert response.json() == []


def test_samples_with_file(tmp_settings: Settings) -> None:
    samples = [{"check_id": "chk_" + "0" * 10, "project_label": "T", "headline": "H"}]
    settings = tmp_settings.model_copy(update={"samples_file": tmp_settings.data_dir / "samples.json"})
    settings.samples_file.parent.mkdir(parents=True, exist_ok=True)
    settings.samples_file.write_text(json.dumps(samples), encoding="utf-8")
    response = _client(settings, FakeRunner()).get("/api/samples")
    assert response.json() == samples


def test_eval_not_measured_yet(tmp_settings: Settings) -> None:
    response = _client(tmp_settings, FakeRunner()).get("/api/eval")
    assert response.status_code == 200
    assert response.json() == {"measured": False}


def test_eval_with_file(tmp_settings: Settings) -> None:
    payload = {
        "measured": True,
        "date": "2026-10-30",
        "method": "hand labels checked against the model",
        "metrics": [{"name": "filing accuracy", "value": 99.1, "n": 114, "note": None}],
    }
    path = tmp_settings.data_dir / "eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    response = _client(tmp_settings, FakeRunner()).get("/api/eval")
    assert response.status_code == 200
    assert response.json() == payload


def test_static_fallback(tmp_settings: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    settings = tmp_settings.model_copy(update={"web_dist_dir": dist})
    response = _client(settings, FakeRunner()).get("/report/abc")
    assert response.status_code == 200
    assert "app" in response.text


def test_static_missing(tmp_settings: Settings, tmp_path: Path) -> None:
    settings = tmp_settings.model_copy(update={"web_dist_dir": tmp_path / "nope"})
    response = _client(settings, FakeRunner()).get("/")
    assert response.status_code == 200
    assert "Frontend not built" in response.text


def test_api_beats_fallback(tmp_settings: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    settings = tmp_settings.model_copy(update={"web_dist_dir": dist})
    assert _client(settings, FakeRunner()).get("/api/health").status_code == 200


def test_upload_then_check(tmp_settings: Settings) -> None:
    runner = FakeRunner()
    client = _client(tmp_settings, runner)
    upload = client.post(
        "/api/uploads", files={"file": ("q.pdf", _pdf(tmp_settings, "q.pdf"), "application/pdf")}
    )
    upload_id = upload.json()["upload_id"]
    response = client.post("/api/checks", json={"project_label": "X", "sources": [{"upload_id": upload_id}]})
    assert response.status_code == 202
    assert runner.submitted[0][1][0].upload_id == upload_id


def test_second_check_queued_before_slot_frees(tmp_settings: Settings) -> None:
    """POST two checks with one slot: the second polls 200 with state queued."""
    import asyncio as _asyncio
    import threading

    from stackcheck.pipeline.jobs import JobRunner

    settings = tmp_settings.model_copy(update={"max_concurrent_checks": 1})
    gate = threading.Event()

    async def blocking_run(check_id: str, project_label: str, sources: list, settings: Settings) -> None:
        await _asyncio.to_thread(gate.wait, 30)

    runner = JobRunner(settings, run=blocking_run)  # type: ignore[arg-type]
    client = TestClient(create_app(settings=settings, runner=runner))
    host = sorted(_allowed_hosts(settings))[0]
    body = {"project_label": "X", "sources": [{"url": f"https://{host}/x.pdf"}]}
    assert client.post("/api/checks", json=body).status_code == 202
    second = client.post("/api/checks", json=body)
    assert second.status_code == 202
    try:
        status = client.get(f"/api/checks/{second.json()['check_id']}")
        assert status.status_code == 200
        assert status.json()["state"] == "queued"
    finally:
        gate.set()
