"""Unit tests for scripts/review_sheet.py (Section C item C2). No live calls."""

import csv
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from review_sheet import COLUMNS, write_review_csv

from stackcheck.models import (
    Evidence,
    Finding,
    ProjectEvidence,
    QuarterEvidence,
    QuarterFiling,
    Report,
    SourceDoc,
)

DOC_URL = "https://example.com/q1.pdf"


def _source() -> SourceDoc:
    return SourceDoc(
        doc_id="a" * 16,
        origin="url",
        url=DOC_URL,
        filename="q1.pdf",
        sha256="b" * 64,
        retrieved_at=datetime.now(UTC),
        page_count=4,
    )


def _report() -> Report:
    with_value = Finding(
        finding_id="R1_PHOTO_VS_CLAIM:2025-03-31:MAIN:0",
        rule="R1_PHOTO_VS_CLAIM",
        status="FLAG",
        severity="medium",
        title="Photo shows fewer floors than claimed",
        explanation="Claimed 4 slabs but the photo shows 2.",
        evidence=[
            Evidence(
                kind="number",
                ref={"doc_id": "a" * 16, "page": 3},
                value="4 slabs",
                note="claimed slab count",
            ),
            Evidence(
                kind="photo",
                image_id="a" * 16 + "-p2-i0",
                overlay_path="media/overlay-p2.png",
                note="elevation photo",
            ),
        ],
    )
    without_value = Finding(
        finding_id="R8_MISSING_QUARTER:all:all:0",
        rule="R8_MISSING_QUARTER",
        status="CANT_VERIFY",
        severity="info",
        title="A quarter filing is missing",
        explanation="Can't verify without the filing.",
        evidence=[],
    )
    return Report(
        check_id="chk_review1",
        project_label="Project A (Patna)",
        created_at=datetime.now(UTC),
        verdict={
            "headline": "One gap found.",
            "flags_high": 0,
            "flags_medium": 1,
            "flags_low": 0,
            "cant_verify": 1,
            "ok": 0,
        },
        findings=[with_value, without_value],
        evidence=ProjectEvidence(
            project_label="Project A (Patna)",
            quarters=[
                QuarterEvidence(
                    source=_source(),
                    filing=QuarterFiling(doc_id="a" * 16),
                    photos=[],
                )
            ],
        ),
        trace=[],
        costs={
            "usd_total": Decimal("0"),
            "by_model": {},
            "calls": 0,
            "cache_hits": 0,
        },
        versions={"review_sheet": "c2"},
        disclaimer="Checks, not accusations.",
    )


def _write_batch(batch: Path, reports: list[Report]) -> Path:
    batch.mkdir(parents=True, exist_ok=True)
    results = [{"check_id": r.check_id, "project_label": r.project_label} for r in reports]
    (batch / "results.json").write_text(json.dumps(results), encoding="utf-8")
    for report in reports:
        check_dir = batch / report.check_id
        check_dir.mkdir(parents=True, exist_ok=True)
        (check_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return batch


def _read_rows(review_csv: Path) -> list[dict[str, str]]:
    with review_csv.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_columns_and_one_row_per_finding(tmp_path: Path) -> None:
    batch = _write_batch(tmp_path / "batch", [_report()])
    review_csv = write_review_csv(batch)
    assert review_csv == batch / "review.csv"
    with review_csv.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert header == COLUMNS
    rows = _read_rows(review_csv)
    assert len(rows) == 2
    assert rows[0]["check_id"] == "chk_review1"
    assert rows[0]["project_label"] == "Project A (Patna)"
    assert rows[0]["rule"] == "R1_PHOTO_VS_CLAIM"
    assert rows[0]["status"] == "FLAG"
    assert rows[1]["rule"] == "R8_MISSING_QUARTER"
    assert rows[1]["status"] == "CANT_VERIFY"


def test_value_carries_doc_url_and_page(tmp_path: Path) -> None:
    batch = _write_batch(tmp_path / "batch", [_report()])
    rows = _read_rows(write_review_csv(batch))
    assert f"{DOC_URL}#page=3" in rows[0]["values"]
    assert "4 slabs" in rows[0]["values"]
    assert rows[1]["values"] == ""


def test_image_file_names_come_from_evidence(tmp_path: Path) -> None:
    batch = _write_batch(tmp_path / "batch", [_report()])
    rows = _read_rows(write_review_csv(batch))
    assert "overlay-p2.png" in rows[0]["images"]
    assert rows[1]["images"] == ""


def test_reviewer_columns_start_empty(tmp_path: Path) -> None:
    batch = _write_batch(tmp_path / "batch", [_report()])
    rows = _read_rows(write_review_csv(batch))
    for row in rows:
        assert row["reviewer_verdict"] == ""
        assert row["reviewer_note"] == ""


def test_embedded_reports_in_results_json_need_no_report_files(tmp_path: Path) -> None:
    """C1 embeds each report in its results.json entry; no <check_id>/report.json needed."""
    batch = tmp_path / "batch"
    batch.mkdir(parents=True, exist_ok=True)
    report = _report()
    entry = {
        "project": report.project_label,
        "check_id": report.check_id,
        "status": {"state": "done"},
        "report": json.loads(report.model_dump_json()),
    }
    (batch / "results.json").write_text(json.dumps([entry]), encoding="utf-8")
    rows = _read_rows(write_review_csv(batch))
    assert len(rows) == 2
    assert rows[0]["check_id"] == "chk_review1"
