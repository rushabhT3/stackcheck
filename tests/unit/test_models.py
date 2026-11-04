from datetime import UTC

import pytest
from pydantic import ValidationError

from stackcheck.models import BlockProgress, Box, Financials, Finding, PageRef, QuarterFiling


def test_page_ref_rejects_zero_page() -> None:
    with pytest.raises(ValidationError):
        PageRef(doc_id="x", page=0)


def test_quarter_filing_defaults_are_independent() -> None:
    a = QuarterFiling(doc_id="a")
    b = QuarterFiling(doc_id="b")
    assert a.blocks == [] and b.blocks == []
    assert a.blocks is not b.blocks
    assert a.activities is not b.activities
    assert a.notes is not b.notes
    assert a.refs is not b.refs
    assert isinstance(a.financials, Financials)
    assert isinstance(b.financials, Financials)
    assert a.financials is not b.financials
    a.blocks.append(BlockProgress(block_label="MAIN"))
    assert b.blocks == []


def test_box_properties() -> None:
    box = Box(x1=10, y1=20, x2=30, y2=60)
    assert box.width == 20
    assert box.height == 40
    assert box.cx == 20
    assert box.cy == 40
    assert box.area == 800


def test_finding_title_length_limit() -> None:
    with pytest.raises(ValidationError):
        Finding(
            finding_id="R1_PHOTO_VS_CLAIM:all:all:1",
            rule="R1_PHOTO_VS_CLAIM",
            status="OK",
            severity="info",
            title="x" * 91,
            explanation="explanation",
        )


def test_report_round_trip_json() -> None:
    from datetime import datetime
    from decimal import Decimal

    from stackcheck.models import (
        CheckStatus,
        CostSummary,
        Evidence,
        ProjectEvidence,
        QuarterEvidence,
        Report,
        SourceDoc,
        Verdict,
    )

    now = datetime.now(UTC)
    source = SourceDoc(
        doc_id="a" * 16,
        origin="url",
        url="https://rera.bihar.gov.in/Progress_Doc/a.pdf",
        filename="a.pdf",
        sha256="s",
        retrieved_at=now,
        page_count=1,
    )
    filing = QuarterFiling(doc_id="a" * 16)
    report = Report(
        check_id="chk_abc",
        project_label="Project A (Patna)",
        created_at=now,
        verdict=Verdict(
            headline="No gaps found in what we could check",
            flags_high=0,
            flags_medium=0,
            flags_low=0,
            cant_verify=0,
            ok=1,
        ),
        findings=[
            Finding(
                finding_id="R1_PHOTO_VS_CLAIM:all:all:1",
                rule="R1_PHOTO_VS_CLAIM",
                status="OK",
                severity="info",
                title="ok",
                explanation="matches",
                evidence=[
                    Evidence(
                        kind="number",
                        ref=PageRef(doc_id="a" * 16, page=1),
                        value="4",
                        note="slabs",
                    )
                ],
                inputs={"a": "b"},
            )
        ],
        evidence=ProjectEvidence(
            project_label="Project A (Patna)",
            quarters=[QuarterEvidence(source=source, filing=filing, photos=[])],
        ),
        trace=[],
        costs=CostSummary(usd_total=Decimal("0"), by_model={}, calls=0, cache_hits=0),
        versions={},
        disclaimer="StackCheck compares public RERA filings with each other. "
        "A gap is a question to ask the builder or RERA, not proof of wrongdoing.",
    )
    raw = report.model_dump_json()
    assert Report.model_validate_json(raw) == report
    assert isinstance(CheckStatus, type)
