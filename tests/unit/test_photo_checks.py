"""Photo-only and video-only checks: count, overlay and report shape."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from stackcheck.api.app import _register_photo_upload
from stackcheck.config import Settings, model_for
from stackcheck.llm.meta import MetaClient
from stackcheck.models import ActivityProgress, BlockProgress, QuarterFiling
from stackcheck.pipeline.run_check import (
    SourceRequest,
    VideoUpload,
    check_dir,
    run_check,
)
from stackcheck.report.build import COUNT_ONLY_HEADLINE
from tests.conftest import make_pdf, make_png
from tests.fakes import FakeModelClient, FakeResponsesSDK
from tests.unit.test_api import FakeRunner, _client


def _grid_sam_text(rows: int = 4) -> str:
    """Window rows by two columns inside the 200px-wide test photo."""
    records = []
    object_id = 1
    for row in range(rows):
        for x1 in (30, 100):
            y1 = 30 + row * 40
            records.append(f"{object_id}<|box;x1={x1};y1={y1};x2={x1 + 30};y2={y1 + 20};w=200;h=300;c=0.9|>")
            object_id += 1
    return "<0f>" + ",".join(records)


class _GridSam:
    """SAM stub: one building plus a window grid of `rows` rows."""

    def __init__(self, rows: int = 4) -> None:
        self._rows = rows

    async def segment_image(self, path: Path, prompt: str, check_id: str | None = None) -> str:
        if prompt == "building":
            return "<0f>1<|box;x1=10;y1=10;x2=190;y2=290;w=200;h=300;c=0.9|>"
        if prompt == "window":
            return _grid_sam_text(self._rows)
        return ""


def _vision_json(storeys: int) -> str:
    """Vision fake reply: an elevation showing `storeys` floors."""
    return json.dumps(
        {
            "image_id": "x",
            "kind": "elevation",
            "vision_storeys_visible": storeys,
            "confidence": "high",
            "reason": "test elevation",
        }
    )


def _seed_photo(settings: Settings, height: int = 240) -> str:
    """One valid PNG in the uploads store; returns its upload id."""
    png = make_png(200, height, "building")
    return _register_photo_upload(png, "site.png", settings.data_dir / "uploads").doc_id


class _PhotoRunner(FakeRunner):
    """FakeRunner that also records the site photo ids of each submit."""

    def __init__(self) -> None:
        super().__init__()
        self.photos: list[list[str] | None] = []

    def submit(
        self,
        project_label: str,
        sources: list,
        video: object = None,
        floors_above_ground: object = None,
        photos: list[str] | None = None,
    ) -> str:
        check_id = super().submit(project_label, sources, video, floors_above_ground)
        self.photos.append(photos)
        return check_id


async def test_photo_only_counts_overlays_and_reports_count_only(tmp_settings: Settings) -> None:
    """No filing: the site photo is counted, overlaid and carried in the report."""
    upload_id = _seed_photo(tmp_settings)
    check_id = "chk_" + "b" * 10
    report = await run_check(
        check_id,
        "T",
        [],
        tmp_settings,
        clients={
            "reader": FakeModelClient('{"summary": "x"}'),
            "vision": FakeModelClient(_vision_json(4)),
            "sam": _GridSam(),
        },
        photos=[upload_id],
    )
    assert report.evidence.quarters == []
    assert report.findings == []
    assert report.verdict.headline == COUNT_ONLY_HEADLINE
    assert report.verdict.headline == (
        "Count only. No filing attached, so there was nothing to check against."
    )
    assert len(report.evidence.photos) == 1
    photo = report.evidence.photos[0]
    assert photo.image.image_id == upload_id
    assert photo.floors is not None
    assert photo.floors.sam_rows == 4
    assert photo.floors.vision_estimate == 4
    assert photo.overlay_path == f"media/{upload_id}.overlay.png"
    work = check_dir(tmp_settings, check_id)
    assert (work / "media" / f"{upload_id}.overlay.png").is_file()
    payload = report.model_dump(mode="json")["evidence"]["photos"][0]
    assert payload["image"]["image_id"] == upload_id
    assert payload["floors"]["sam_rows"] == 4
    assert payload["overlay_path"] == f"media/{upload_id}.overlay.png"
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done"


async def test_photo_only_runs_vision_and_records_both_methods(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Site photo runs the vision estimate (private model) plus SAM; both counts recorded."""
    import stackcheck.pipeline.run_check as rc

    upload_id = _seed_photo(tmp_settings, height=300)
    sdk = FakeResponsesSDK(_vision_json(5))
    seen_origins: list[str] = []

    def _vision_client(settings: Settings, origin: Literal["url", "upload"]) -> MetaClient:
        seen_origins.append(origin)
        return MetaClient(settings, model_for(origin, settings), data_dir=settings.data_dir, sdk=sdk)

    monkeypatch.setattr(rc, "vision_client", _vision_client)
    report = await run_check(
        "chk_" + "e" * 10,
        "T",
        [],
        tmp_settings,
        clients={"reader": FakeModelClient('{"summary": "x"}'), "sam": _GridSam(rows=5)},
        photos=[upload_id],
    )
    assert seen_origins == ["upload"]
    assert len(sdk.calls) == 1
    assert report.verdict.headline == COUNT_ONLY_HEADLINE
    photo = report.evidence.photos[0]
    assert photo.facts is not None
    assert photo.facts.vision_storeys_visible == 5
    assert photo.floors is not None
    assert photo.floors.status == "OK"
    assert (photo.floors.sam_rows, photo.floors.vision_estimate) == (5, 5)
    assert "SAM rows 5" in photo.floors.reason
    assert "visual count 5" in photo.floors.reason
    ledger = (tmp_settings.data_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    vision_lines = [json.loads(line) for line in ledger if json.loads(line)["purpose"] == "describe_photo"]
    assert len(vision_lines) == 1
    assert vision_lines[0]["model"] == tmp_settings.private_model
    assert Decimal(vision_lines[0]["usd"]) > 0


async def test_video_only_report_shape(
    tmp_path: Path, tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Video without filings: no quarters, no findings, ESTIMATE video evidence."""
    import stackcheck.pipeline.run_check as rc
    from tests.conftest import make_png as _png

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    paths = []
    for i in range(4):
        path = frames_dir / f"frame_{i:02d}.png"
        path.write_bytes(_png(100, 80, "building"))
        paths.append(path)

    async def _sampler(video: Path, work_dir: Path, duration: float, keep: int = 8):
        return [(path, 100.0 - i) for i, path in enumerate(paths)]

    async def _probe(video: Path) -> float:
        return 30.0

    monkeypatch.setattr(rc, "sample_sharp_frames", _sampler)
    monkeypatch.setattr(rc, "video_tools_available", lambda: True)
    monkeypatch.setattr(rc, "probe_duration", _probe)
    facts_json = json.dumps(
        {
            "image_id": "x",
            "kind": "elevation",
            "vision_storeys_visible": 6,
            "confidence": "high",
            "reason": "test",
        }
    )

    class _BuildingSam:
        async def segment_image(self, path: Path, prompt: str, check_id: str | None = None) -> str:
            if prompt == "building":
                return "<0f>1<|box;x1=10;y1=10;x2=90;y2=70;w=100;h=80;c=0.9|>"
            return ""

    check_id = "chk_" + "w" * 10
    work = check_dir(tmp_settings, check_id)
    (work / "sources").mkdir(parents=True, exist_ok=True)
    (work / "sources" / ("w" * 16 + ".mp4")).write_bytes(b"\x00" * 16)
    report = await run_check(
        check_id,
        "T",
        [],
        tmp_settings,
        clients={
            "reader": FakeModelClient('{"summary": "x"}'),
            "vision": FakeModelClient([facts_json] * 4),
            "sam": _BuildingSam(),
        },
        video=VideoUpload(upload_id="w" * 16, block_label="MAIN"),
    )
    assert report.evidence.quarters == []
    assert report.findings == []
    assert report.evidence.video is not None
    assert report.evidence.video.tier == "ESTIMATE"
    assert (report.evidence.video.lower, report.evidence.video.upper) == (6, 6)
    assert report.evidence.video.frames_used == 4
    assert report.evidence.video.contact_sheet == f"media/{'w' * 16}-sheet.png"
    assert (work / "media" / ("w" * 16 + "-sheet.png")).is_file()
    assert report.verdict.headline == COUNT_ONLY_HEADLINE
    assert report.verdict.headline == (
        "Count only. No filing attached, so there was nothing to check against."
    )


async def test_photo_with_filing_and_floors_carries_n_of_m(
    tmp_path: Path, tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filing plus photo plus floors: the count (N) and the entered floors (M) persist."""
    import stackcheck.pipeline.run_check as rc

    async def _filing(doc, reader, *, check_id, pdf_path):
        return QuarterFiling(
            doc_id=doc.source.doc_id,
            quarter_end=date(2025, 3, 31),
            project_name="Demo Project",
            promoter_name="Demo Builders",
            blocks=[BlockProgress(block_label="MAIN")],
            activities=[ActivityProgress(name="9.1 Slabs", percent_done=50.0)],
        )

    render_facts = json.dumps(
        {"image_id": "x", "kind": "document_or_logo", "confidence": "high", "reason": "test"}
    )

    monkeypatch.setattr(rc, "extract_filing", _filing)
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": "Quarterly progress report. " * 20}])
    (tmp_settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    from stackcheck.ingest.sources import register_upload

    pdf_id = register_upload(pdf.read_bytes(), "q.pdf", tmp_settings.data_dir / "uploads").doc_id
    photo_id = _seed_photo(tmp_settings)
    report = await run_check(
        "chk_" + "c" * 10,
        "T",
        [SourceRequest(upload_id=pdf_id)],
        tmp_settings,
        clients={
            "reader": FakeModelClient('{"summary": "x"}'),
            "vision": FakeModelClient(render_facts),
            "sam": _GridSam(),
        },
        floors_above_ground=6,
        photos=[photo_id],
    )
    assert len(report.evidence.photos) == 1
    assert report.evidence.photos[0].floors is not None
    assert report.evidence.photos[0].floors.sam_rows == 4
    latest = report.evidence.quarters[-1]
    assert photo_id in [p.image.image_id for p in latest.photos]
    assert latest.filing.blocks[0].floors_entered_by_user == 6
    assert latest.filing.blocks[0].floors_above_ground_sanctioned == 5
    assert report.verdict.headline != COUNT_ONLY_HEADLINE


def test_upload_photo_reaches_runner(tmp_settings: Settings) -> None:
    """A PNG upload returns kind photo and a photo-only check submits its id."""
    client = _client(tmp_settings, FakeRunner())
    upload = client.post(
        "/api/uploads",
        files={"file": ("site.png", make_png(200, 240, "building"), "image/png")},
    )
    assert upload.status_code == 200
    assert upload.json()["kind"] == "photo"
    upload_id = upload.json()["upload_id"]
    runner = _PhotoRunner()
    response = _client(tmp_settings, runner).post(
        "/api/checks",
        json={"project_label": "X", "sources": [], "photo_upload_ids": [upload_id]},
    )
    assert response.status_code == 202
    assert runner.photos == [[upload_id]]


def test_upload_rejects_non_photo_files(tmp_settings: Settings) -> None:
    """Text uploads and magic-byte spoofs are rejected with a plain 400."""
    client = _client(tmp_settings, FakeRunner())
    text = client.post("/api/uploads", files={"file": ("note.txt", b"hello", "text/plain")})
    assert text.status_code == 400
    spoof = client.post(
        "/api/uploads",
        files={"file": ("site.png", b"not a photo" * 10, "image/png")},
    )
    assert spoof.status_code == 400
    assert spoof.json()["error"]["code"] == "not_a_photo"


def test_check_rejects_sixth_photo(tmp_settings: Settings) -> None:
    """At most five site photos fit one check."""
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/checks",
        json={"project_label": "X", "sources": [], "photo_upload_ids": [f"{i:016d}" for i in range(6)]},
    )
    assert response.status_code == 422


def test_check_needs_some_input(tmp_settings: Settings) -> None:
    """No filing, photo or video is rejected with the form's sentence."""
    response = _client(tmp_settings, FakeRunner()).post(
        "/api/checks", json={"project_label": "X", "sources": []}
    )
    assert response.status_code == 422
    assert "Enter a project name and at least one filing, photo or video." in response.text


async def test_photo_only_with_floors_echoes_m(tmp_settings: Settings) -> None:
    """Photo-only plus floors: the entered floors persist so the report can show N of M."""
    upload_id = _seed_photo(tmp_settings)
    report = await run_check(
        "chk_" + "d" * 10,
        "T",
        [],
        tmp_settings,
        clients={
            "reader": FakeModelClient('{"summary": "x"}'),
            "vision": FakeModelClient(_vision_json(4)),
            "sam": _GridSam(),
        },
        floors_above_ground=5,
        photos=[upload_id],
    )
    assert report.evidence.quarters == []
    assert report.evidence.floors_above_ground == 5
    assert report.model_dump(mode="json")["evidence"]["floors_above_ground"] == 5
    assert report.evidence.photos[0].floors is not None
    assert report.evidence.photos[0].floors.sam_rows == 4
