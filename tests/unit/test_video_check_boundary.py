"""Upload-then-check boundary for videos: the check must see the upload.

Regression test for the live bug where evidence.video was null with the note
"Video skipped". The upload goes through one app instance and the check runs
on a fresh instance that shares only the storage directory (like Modal's web
container, whose runner calls run_check directly). The pipeline's network and
model calls are stubbed; the video loader, consensus and contact sheet run
for real.
"""

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

import stackcheck.pipeline.run_check as rc
from stackcheck.api.app import create_app
from stackcheck.models import BlockProgress, QuarterFiling
from stackcheck.pipeline.jobs import JobRunner
from tests.conftest import make_pdf, make_png
from tests.fakes import FakeModelClient

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _filing_json() -> str:
    return json.dumps(
        {
            "image_id": "f",
            "kind": "elevation",
            "vision_storeys_visible": 6,
            "confidence": "high",
            "reason": "test",
        }
    )


async def test_uploaded_video_reaches_the_check(
    tmp_path: Path, tmp_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frame_paths = []
    for i in range(4):
        path = frames_dir / f"frame_{i:02d}.png"
        path.write_bytes(make_png(100, 80, "building"))
        frame_paths.append(path)

    async def _probe(video: Path) -> float:
        return 12.5

    async def _sampler(video: Path, work_dir: Path, duration: float, keep: int = 8):
        return [(path, 100.0 - i) for i, path in enumerate(frame_paths)]

    async def _filing(doc, client, *, check_id=None, pdf_path=None) -> QuarterFiling:
        return QuarterFiling(
            doc_id=doc.source.doc_id,
            state="Bihar",
            quarter_end=date(2025, 3, 31),
            filed_on=date(2025, 4, 13),
            blocks=[BlockProgress(block_label="MAIN", slabs_claimed_count=10)],
        )

    class _Sam:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def segment_image(self, path: Path, prompt: str, check_id: str | None) -> str:
            if prompt == "building":
                return "<0f>1<|box;x1=10;y1=10;x2=90;y2=70;w=100;h=80;c=0.9|>"
            return ""

    import stackcheck.api.app as api_app

    monkeypatch.setattr(api_app, "probe_duration", lambda path: 12.5)
    monkeypatch.setattr(rc, "video_tools_available", lambda: True)
    monkeypatch.setattr(rc, "probe_duration", _probe)
    monkeypatch.setattr(rc, "sample_sharp_frames", _sampler)
    monkeypatch.setattr(rc, "extract_filing", _filing)
    monkeypatch.setattr(rc, "SamClient", _Sam)
    monkeypatch.setattr(rc, "vision_client", lambda settings, origin: FakeModelClient([_filing_json()] * 4))
    monkeypatch.setattr(rc, "model_for", lambda origin, settings: "m-test")

    upload_app = create_app(tmp_settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=upload_app), base_url="http://test"
    ) as http:
        pdf_path = make_pdf(tmp_path / "q.pdf", [{"text": "Quarterly progress report. " * 20}])
        pdf = await http.post(
            "/api/uploads",
            files={"file": ("q.pdf", pdf_path.read_bytes(), "application/pdf")},
        )
        assert pdf.status_code == 200
        clip = await http.post("/api/uploads", files={"file": ("v.mp4", MP4, "video/mp4")})
        assert clip.status_code == 200
        assert clip.json()["kind"] == "video"

    check_app = create_app(tmp_settings, JobRunner(tmp_settings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=check_app), base_url="http://test"
    ) as http:
        created = await http.post(
            "/api/checks",
            json={
                "project_label": "Boundary",
                "sources": [{"upload_id": pdf.json()["upload_id"]}],
                "video_upload_id": clip.json()["upload_id"],
                "video_block_label": "MAIN",
                "video_same_project": True,
            },
        )
        assert created.status_code == 202
        check_id = created.json()["check_id"]
        runner = check_app.state.runner
        await asyncio.wait_for(asyncio.gather(*runner._tasks.values()), timeout=120)
        report = await http.get(f"/api/checks/{check_id}/report")
        assert report.status_code == 200

    evidence = report.json()["evidence"]
    assert evidence["video"] is not None
    assert evidence["video"]["tier"] == "ESTIMATE"
    assert (evidence["video"]["lower"], evidence["video"]["upper"]) == (6, 6)
    sheet = tmp_settings.data_dir / "work" / check_id / "media" / f"{clip.json()['upload_id']}-sheet.png"
    assert sheet.is_file()
    notes = evidence["quarters"][0]["filing"]["notes"]
    assert not [note for note in notes if note.startswith("Video skipped")]
