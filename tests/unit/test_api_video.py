"""The report JSON carries everything the Site video section renders."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stackcheck.config import Settings
from stackcheck.models import (
    CostSummary,
    ProjectEvidence,
    Report,
    Verdict,
    VideoEvidence,
)
from stackcheck.pipeline.run_check import check_dir, write_json
from tests.conftest import make_pdf, make_png
from tests.unit.test_api import FakeRunner, _client, _status


def _mp4() -> bytes:
    """Magic bytes of an mp4: size + ftyp. Not a playable video."""
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def test_report_serves_video_section_data(tmp_settings: Settings) -> None:
    """Video fields in the report JSON, and the contact sheet resolves."""
    check_id = "chk_" + "0" * 10
    now = datetime.now(UTC)
    video_id = "1f0fd0e7d4676e5c"
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
        evidence=ProjectEvidence(
            project_label="T",
            quarters=[],
            video=VideoEvidence(
                video_id=video_id,
                block_label="MAIN",
                tier="ESTIMATE",
                lower=28,
                upper=28,
                frames_used=6,
                frame_counts=[28, 25, 30, 30, 24, 30, 22, 28],
                contact_sheet=f"media/{video_id}-sheet.png",
                uploaded_on=now.date(),
                credit="Test Author, CC0, https://example.com/v",
            ),
        ),
        trace=[],
        summary_en=None,
        costs=CostSummary(usd_total=Decimal("0"), by_model={}, calls=0, cache_hits=0),
        versions={},
        disclaimer="test",
    )
    work = check_dir(tmp_settings, check_id)
    write_json(work / "report.json", report)
    media = work / "media"
    media.mkdir(parents=True)
    (media / f"{video_id}-sheet.png").write_bytes(make_png(10, 10, "stripes"))
    runner = FakeRunner()
    runner.statuses[check_id] = _status("done")
    client = _client(tmp_settings, runner)
    body = client.get(f"/api/checks/{check_id}/report").json()
    video = body["evidence"]["video"]
    assert video["tier"] == "ESTIMATE"
    assert (video["lower"], video["upper"]) == (28, 28)
    assert video["frames_used"] == 6
    assert video["frame_counts"] == [28, 25, 30, 30, 24, 30, 22, 28]
    assert video["contact_sheet"] == f"media/{video_id}-sheet.png"
    assert video["credit"].startswith("Test Author")
    assert video["uploaded_on"] == now.date().isoformat()
    sheet = client.get(f"/media/{check_id}/{video_id}-sheet.png")
    assert sheet.status_code == 200
    assert sheet.headers["content-type"] == "image/png"


def test_check_with_video_credit_reaches_runner(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """video_credit is capped, stripped and passed through to the runner."""
    monkeypatch.setattr("stackcheck.api.app.probe_duration", lambda path: 12.5)
    tmp_settings.data_dir.mkdir(parents=True, exist_ok=True)
    client = _client(tmp_settings, FakeRunner())
    pdf_path = make_pdf(tmp_settings.data_dir / "q.pdf", [{"text": "hi"}])
    pdf = client.post(
        "/api/uploads",
        files={"file": ("q.pdf", pdf_path.read_bytes(), "application/pdf")},
    )
    assert pdf.status_code == 200
    video = client.post("/api/uploads", files={"file": ("v.mp4", _mp4(), "video/mp4")})
    assert video.status_code == 200
    runner = FakeRunner()
    response = _client(tmp_settings, runner).post(
        "/api/checks",
        json={
            "project_label": "X",
            "sources": [{"upload_id": pdf.json()["upload_id"]}],
            "video_upload_id": video.json()["upload_id"],
            "video_same_project": True,
            "video_block_label": "MAIN",
            "video_credit": "  Test Author, CC0, https://example.com/v  ",
        },
    )
    assert response.status_code == 202
    assert runner.submitted[0][2].credit == "Test Author, CC0, https://example.com/v"


def test_check_with_video_credit_too_long(tmp_settings: Settings) -> None:
    """A credit over 200 chars is rejected by validation."""
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/checks",
        json={
            "project_label": "X",
            "sources": [{"upload_id": "1" * 16}],
            "video_upload_id": "2" * 16,
            "video_same_project": True,
            "video_credit": "c" * 201,
        },
    )
    assert response.status_code == 422
