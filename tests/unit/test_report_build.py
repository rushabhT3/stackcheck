"""Report headlines: low-only flags read as minor notes, never as no gaps."""

from datetime import UTC, datetime
from decimal import Decimal

from stackcheck.config import Settings
from stackcheck.models import (
    CostSummary,
    Finding,
    ProjectEvidence,
    QuarterEvidence,
    QuarterFiling,
    Report,
    SourceDoc,
    Verdict,
)
from stackcheck.report.build import _redact, build_report


def _finding(status: str, severity: str) -> Finding:
    return Finding(
        finding_id="",
        rule="R6_MONEY_VS_PROGRESS",
        status=status,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        title="t",
        explanation="e",
        evidence=[],
        inputs={},
    )


async def _headline(findings: list[Finding], tmp_path) -> str:
    settings = Settings(
        data_dir=tmp_path / "data",
        meta_api_key="test",
        baseten_api_key="test",
        fireworks_api_key="test",
    )
    report = await build_report(
        check_id="chk_" + "0" * 10,
        project_label="T",
        evidence=ProjectEvidence(project_label="T", quarters=[]),
        findings=findings,
        trace=[],
        settings=settings,
    )
    return report.verdict.headline


async def test_low_only_flags_are_minor_notes(tmp_path) -> None:
    assert await _headline([_finding("FLAG", "low")], tmp_path) == "No major gaps. 1 minor note."


async def test_two_low_flags_count_notes(tmp_path) -> None:
    headline = await _headline([_finding("FLAG", "low"), _finding("FLAG", "low")], tmp_path)
    assert headline == "No major gaps. 2 minor notes."


async def test_high_flag_still_gaps_found(tmp_path) -> None:
    findings = [_finding("FLAG", "low"), _finding("FLAG", "high")]
    assert await _headline(findings, tmp_path) == "Gaps found"


async def test_no_flags_no_oks_keeps_not_enough_data(tmp_path) -> None:
    assert await _headline([_finding("CANT_VERIFY", "info")], tmp_path) == "Not enough data to check"


async def test_ok_without_flags_keeps_no_gaps(tmp_path) -> None:
    assert await _headline([_finding("OK", "info")], tmp_path) == "No gaps found in what we could check"


async def test_hindi_summary_when_enabled(tmp_path) -> None:
    from stackcheck.models import ProjectEvidence
    from tests.fakes import FakeModelClient

    settings = Settings(data_dir=tmp_path, summary_languages="en,hi")
    evidence = ProjectEvidence(project_label="T", quarters=[])
    report = await build_report(
        check_id="chk_" + "0" * 10,
        project_label="T",
        evidence=evidence,
        findings=[],
        trace=[],
        settings=settings,
        reader=FakeModelClient(['{"summary": "s"}', '{"summary": "s-hi"}']),
    )
    assert report.summary_en == "s"
    assert report.summary_hi == "s-hi"


async def test_hindi_summary_absent_when_disabled(tmp_path) -> None:
    from stackcheck.models import ProjectEvidence
    from tests.fakes import FakeModelClient

    settings = Settings(data_dir=tmp_path)
    evidence = ProjectEvidence(project_label="T", quarters=[])
    report = await build_report(
        check_id="chk_" + "0" * 10,
        project_label="T",
        evidence=evidence,
        findings=[],
        trace=[],
        settings=settings,
        reader=FakeModelClient(['{"summary": "s"}']),
    )
    assert report.summary_en == "s"
    assert report.summary_hi is None


async def test_guard_failed_findings_excluded_from_summary(tmp_path) -> None:
    """A guard-failed finding never reaches the model summary input."""
    from tests.fakes import FakeModelClient

    settings = Settings(data_dir=tmp_path, summary_languages="en")
    reader = FakeModelClient(['{"summary": "s"}'])
    findings = [
        Finding(
            finding_id="",
            rule="R7_ARITHMETIC",  # type: ignore[arg-type]
            status="CANT_VERIFY",  # type: ignore[arg-type]
            severity="info",  # type: ignore[arg-type]
            title="Money figures look copied, needs a second read",
            explanation="Needs a second read of the page.",
            evidence=[],
            inputs={"guard": "failed"},
        ),
        Finding(
            finding_id="",
            rule="R4_CLAIM_REGRESSION",  # type: ignore[arg-type]
            status="CANT_VERIFY",  # type: ignore[arg-type]
            severity="info",  # type: ignore[arg-type]
            title="Block A activity has conflicting reads",
            explanation="A read fault, so a drop can't be verified.",
            evidence=[],
            inputs={"guard": "failed", "block": "A"},
        ),
        Finding(
            finding_id="",
            rule="R6_MONEY_VS_PROGRESS",  # type: ignore[arg-type]
            status="FLAG",  # type: ignore[arg-type]
            severity="low",  # type: ignore[arg-type]
            title="Ordinary note",
            explanation="Something worth a minor note.",
            evidence=[],
            inputs={},
        ),
    ]
    await build_report(
        check_id="chk_" + "0" * 10,
        project_label="T",
        evidence=ProjectEvidence(project_label="T", quarters=[]),
        findings=findings,
        trace=[],
        settings=settings,
        reader=reader,
    )
    assert reader.calls
    text = reader.calls[0]["text"]
    assert "Money figures look copied" not in text
    assert "conflicting reads" not in text
    assert "Ordinary note" in text


def _redacted_report() -> Report:
    filing = QuarterFiling(doc_id="d" * 16, project_name="ACME TOWER")
    source = SourceDoc(
        doc_id="d" * 16,
        origin="upload",
        filename="q.pdf",
        sha256="0" * 64,
        retrieved_at=datetime(2025, 1, 1, tzinfo=UTC),
        page_count=1,
    )
    return Report(
        check_id="chk_" + "0" * 10,
        project_label="T",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        verdict=Verdict(headline="h", flags_high=0, flags_medium=0, flags_low=0, cant_verify=1, ok=0),
        findings=[
            Finding(
                finding_id="",
                rule="R1_PHOTO_VS_CLAIM",
                status="CANT_VERIFY",
                severity="info",
                block_label="ACME TOWER",
                title="No slab claim for block ACME TOWER",
                explanation="The filing claims no slab count or percent for block ACME TOWER.",
                evidence=[],
                inputs={},
            )
        ],
        evidence=ProjectEvidence(
            project_label="T",
            quarters=[QuarterEvidence(source=source, filing=filing, photos=[])],
        ),
        trace=[],
        costs=CostSummary(usd_total=Decimal(0), by_model={}, calls=0, cache_hits=0),
        versions={},
        disclaimer="d",
    )


def test_redacted_block_label_shows_the_building() -> None:
    report = _redacted_report()
    _redact(report)
    finding = report.findings[0]
    assert finding.title == "No slab claim for the building"
    assert finding.explanation == "The filing claims no slab count or percent for the building."
    assert finding.block_label is None
    assert "ACME TOWER" not in report.model_dump_json()
