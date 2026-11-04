"""T09 Part B smoke: two synthetic PDFs with photos end to end, no network."""

import json
from datetime import date
from pathlib import Path

import pytest

from stackcheck.config import Settings
from stackcheck.ingest.sources import register_upload
from stackcheck.pipeline.run_check import SourceRequest, check_dir, run_check
from stackcheck.vision.sam import SamClient
from tests.conftest import make_pdf, make_png
from tests.fakes import FakeModelClient, FakeSamSDK


def _sam_grid() -> str:
    boxes = ["0<|box;x1=0;y1=0;x2=640;y2=480;w=640;h=480|>"]
    for i, cy in enumerate([100, 220, 340], start=1):
        boxes.append(f"{i * 2 - 1}<|box;x1=100;y1={cy - 10};x2=160;y2={cy + 10};w=640;h=480|>")
        boxes.append(f"{i * 2}<|box;x1=300;y1={cy - 10};x2=360;y2={cy + 10};w=640;h=480|>")
    return "<0f>" + ",".join(boxes)


def _filing_json(quarter_end: str, slabs: int) -> str:
    return json.dumps(
        {
            "doc_id": "x",
            "quarter_end": quarter_end,
            "project_name": "Demo Project",
            "promoter_name": "Demo Builders",
            "blocks": [
                {
                    "block_label": "MAIN",
                    "slabs_claimed_count": slabs,
                    "ref": {"doc_id": "x", "page": 1},
                }
            ],
        }
    )


FILLER = "Progress details for the quarter. Work continued on all fronts. " * 6


def _body(text: str) -> str:
    return f"{text} {FILLER}"


def _photo_json() -> str:
    return json.dumps(
        {
            "image_id": "x",
            "kind": "elevation",
            "confidence": "high",
            "reason": "three rows visible",
            "vision_storeys_visible": 3,
        }
    )


async def test_pipeline_smoke(tmp_path: Path, tmp_settings: Settings) -> None:
    bodies = [
        ("q1.pdf", _body("March 2025 quarter. Slabs of super structure 3."), "building"),
        ("q2.pdf", _body("June 2025 quarter. Slabs of super structure 2."), "gradient"),
    ]
    check_id = "chk_0123456789"
    work = check_dir(tmp_settings, check_id)
    requests = []
    for name, text, pattern in bodies:
        pdf_path = make_pdf(tmp_path / name, [{"text": text, "images": [make_png(640, 480, pattern)]}])
        source = register_upload(pdf_path.read_bytes(), name, work)
        requests.append(SourceRequest(upload_id=source.doc_id))
    reader = FakeModelClient(
        [_filing_json("2025-03-31", 3), _filing_json("2025-06-30", 2)],
        # Route on page-only phrases: the filing prompt template itself
        # contains a "March 2025" example, so month names match every request.
        routes={
            "super structure 3": _filing_json("2025-03-31", 3),
            "super structure 2": _filing_json("2025-06-30", 2),
        },
    )
    vision = FakeModelClient([_photo_json(), _photo_json()])
    sam = SamClient(tmp_settings, data_dir=tmp_path, sdk=FakeSamSDK(chunks=(_sam_grid(),)))
    clients = {"reader": reader, "vision": vision, "sam": sam}

    report = await run_check(check_id, "Demo Project", requests, tmp_settings, clients=clients)

    assert (work / "report.json").exists()
    flags = [
        f
        for f in report.findings
        if f.rule == "R4_CLAIM_REGRESSION" and f.status == "FLAG" and f.severity == "high"
    ]
    assert len(flags) == 1
    assert "went down from 3 to 2" in flags[0].title
    r1 = [f for f in report.findings if f.rule == "R1_PHOTO_VS_CLAIM"]
    assert r1, "photos step must feed R1"
    first = next(q for q in report.evidence.quarters if q.photos)
    photo = first.photos[0]
    assert photo.floors is not None and photo.floors.status == "OK"
    assert photo.overlay_path is not None and (work / photo.overlay_path).exists()
    overlays = list((work / "media").glob("*.overlay.png"))
    assert len(overlays) == 2, "each elevation photo gets an overlay"
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done"
    assert {s["name"]: s["state"] for s in status["steps"]} == {
        "fetch": "done",
        "read_filings": "done",
        "photos": "done",
        "floors": "done",
        "rules": "done",
        "investigate": "done",
        "report": "done",
    }
    assert report.summary_en is None


async def test_investigate_failure_still_builds_report(
    tmp_path: Path, tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising agent marks investigate failed; the report is still built."""
    import stackcheck.pipeline.run_check as rc
    from stackcheck.models import Finding

    bodies = [("q1.pdf", _body("March 2025 quarter. Slabs of super structure 3."), "building")]
    check_id = "chk_abcdef0123"
    work = check_dir(tmp_settings, check_id)
    requests = []
    for name, text, pattern in bodies:
        pdf_path = make_pdf(tmp_path / name, [{"text": text, "images": [make_png(640, 480, pattern)]}])
        source = register_upload(pdf_path.read_bytes(), name, work)
        requests.append(SourceRequest(upload_id=source.doc_id))
    reader = FakeModelClient([_filing_json("2025-03-31", 3)])
    vision = FakeModelClient([_photo_json()])
    sam = SamClient(tmp_settings, data_dir=tmp_path, sdk=FakeSamSDK(chunks=(_sam_grid(),)))
    finding = Finding(
        finding_id="R1_PHOTO_VS_CLAIM:2025-03-31:MAIN:0",
        rule="R1_PHOTO_VS_CLAIM",
        status="CANT_VERIFY",
        severity="info",
        quarter_end=date(2025, 3, 31),
        block_label="MAIN",
        title="Slabs claimed but photo shows fewer floors",
        explanation="The photo count does not confirm the claim.",
    )
    monkeypatch.setattr(rc, "run_rules", lambda evidence, settings: [finding])
    monkeypatch.setattr(rc, "needs_investigation", lambda f, p: True)

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("agent down")

    monkeypatch.setattr(rc, "investigate_finding", _boom)
    report = await run_check(
        check_id,
        "Demo Project",
        requests,
        tmp_settings,
        clients={"reader": reader, "vision": vision, "sam": sam},
    )

    assert (work / "report.json").exists()
    assert report.trace == []
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done"
    assert status["steps"][5] == {
        "name": "investigate",
        "state": "failed",
        "started_at": status["steps"][5]["started_at"],
        "finished_at": status["steps"][5]["finished_at"],
        "note": "Investigator unavailable; report built without an agent trace.",
    }
