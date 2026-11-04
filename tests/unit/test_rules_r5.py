"""R5: a quarter must not repeat the previous quarter's figures."""

from datetime import date, datetime
from decimal import Decimal

from stackcheck.models import (
    ActivityProgress,
    Financials,
    Finding,
    PageRef,
    ProjectEvidence,
    QuarterEvidence,
    QuarterFiling,
    SourceDoc,
)
from stackcheck.rules.checks import RuleSettings, r5_copy_paste_quarter
from tests.factories import make_project, make_quarter

Q1 = date(2025, 3, 31)
Q2 = date(2025, 6, 30)
Q3 = date(2025, 9, 30)
SETTINGS = RuleSettings()


def _fin(**kwargs) -> dict:
    return {"currency_unit": "INR", **kwargs}


def _six_equal() -> dict[str, float]:
    return {f"{i} Work": 50.0 for i in range(1, 7)}


def _run(project: ProjectEvidence) -> list[Finding]:
    return r5_copy_paste_quarter(project, SETTINGS)


def _quarter(
    qend: date,
    acts: dict[str, tuple[float | None, bool]],
    fin: dict,
    sha: str,
    page: int,
) -> QuarterEvidence:
    """Quarter where each activity is (percent, has_page_ref)."""
    doc = sha[:16]
    filing = QuarterFiling(
        doc_id=doc,
        quarter_end=qend,
        activities=[
            ActivityProgress(
                name=name,
                percent_done=value,
                ref=PageRef(doc_id=doc, page=page) if anchored else None,
            )
            for name, (value, anchored) in acts.items()
        ],
        financials=Financials(**fin, ref=PageRef(doc_id=doc, page=10)),
    )
    source = SourceDoc(
        doc_id=doc,
        origin="upload",
        filename=f"{doc}.pdf",
        sha256=sha,
        retrieved_at=datetime(2025, 1, 1),
        page_count=12,
    )
    return QuarterEvidence(source=source, filing=filing, photos=[])


def test_identical_report_high() -> None:
    project = make_project([make_quarter(Q1, sha="a" * 64), make_quarter(Q2, sha="a" * 64)])
    findings = _run(project)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "high")
    assert findings[0].title == "Identical report filed for two quarters"


def test_same_figures_medium() -> None:
    """Ref-less repeats never FLAG (A4: no page refs means no FLAG)."""
    fin = _fin(cumulative_received=Decimal("100"), cumulative_spent=Decimal("50"))
    project = make_project(
        [
            make_quarter(Q1, activities=_six_equal(), fin=fin, sha="a" * 64),
            make_quarter(Q2, activities=_six_equal(), fin=fin, sha="b" * 64),
        ]
    )
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"


def test_spending_without_progress_low() -> None:
    """Ref-less repeats with spend never FLAG low (A4)."""
    fin_a = _fin(cumulative_received=Decimal("100"), cumulative_spent=Decimal("50"))
    fin_b = _fin(
        cumulative_received=Decimal("100"),
        cumulative_spent=Decimal("60"),
        spent_this_quarter=Decimal("500000"),
    )
    project = make_project(
        [
            make_quarter(Q1, activities=_six_equal(), fin=fin_a, sha="a" * 64),
            make_quarter(Q2, activities=_six_equal(), fin=fin_b, sha="b" * 64),
        ]
    )
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"


def _all(value: float, anchored: bool) -> dict[str, tuple[float, bool]]:
    return {f"{i} Work": (value, anchored) for i in range(1, 7)}


def test_single_anchored_repeat_is_cant_verify() -> None:
    """Six identical repeats but only one has refs on both quarters."""
    fin = _fin(cumulative_received=Decimal("100"), cumulative_spent=Decimal("50"))
    old = _all(50.0, False)
    new = _all(50.0, False)
    old["1 Work"] = (50.0, True)
    new["1 Work"] = (50.0, True)
    project = make_project(
        [
            _quarter(Q1, old, fin, "a" * 64, 5),
            _quarter(Q2, new, fin, "b" * 64, 5),
        ]
    )
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"


def test_zero_only_repeats_are_cant_verify() -> None:
    """0.0 == 0.0 repeats do not anchor, even with refs."""
    fin = _fin(cumulative_received=Decimal("100"), cumulative_spent=Decimal("50"))
    project = make_project(
        [
            _quarter(Q1, _all(0.0, True), fin, "a" * 64, 5),
            _quarter(Q2, _all(0.0, True), fin, "b" * 64, 5),
        ]
    )
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"


def test_two_anchored_repeats_flag_medium_with_refs() -> None:
    """Two non-zero repeats with refs on both quarters FLAG with page refs."""
    fin = _fin(cumulative_received=Decimal("100"), cumulative_spent=Decimal("50"))
    old = _all(50.0, False)
    new = _all(50.0, False)
    old["1 Work"] = (50.0, True)
    new["1 Work"] = (50.0, True)
    old["2 Work"] = (50.0, True)
    new["2 Work"] = (50.0, True)
    project = make_project(
        [
            _quarter(Q1, old, fin, "a" * 64, 5),
            _quarter(Q2, new, fin, "b" * 64, 6),
        ]
    )
    findings = _run(project)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "medium")
    assert findings[0].title == "Quarter repeats the previous quarter's figures"
    pages = {(ref.doc_id, ref.page) for ref in (e.ref for e in findings[0].evidence) if ref is not None}
    assert ("a" * 16, 5) in pages
    assert ("b" * 16, 6) in pages


def test_two_anchored_repeats_with_spend_flag_low() -> None:
    """Two anchored repeats with spend and differing money: unverified note, not a FLAG."""
    fin_a = _fin(cumulative_received=Decimal("100"), cumulative_spent=Decimal("50"))
    fin_b = _fin(
        cumulative_received=Decimal("100"),
        cumulative_spent=Decimal("60"),
        spent_this_quarter=Decimal("500000"),
    )
    old = _all(50.0, False)
    new = _all(50.0, False)
    old["1 Work"] = (50.0, True)
    new["1 Work"] = (50.0, True)
    old["2 Work"] = (50.0, True)
    new["2 Work"] = (50.0, True)
    project = make_project(
        [
            _quarter(Q1, old, fin_a, "a" * 64, 5),
            _quarter(Q2, new, fin_b, "b" * 64, 6),
        ]
    )
    findings = _run(project)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("CANT_VERIFY", "info")
    assert findings[0].evidence == []


def test_spend_with_single_page_repeats_is_unverified_note() -> None:
    """Repeats confined to page 9 + differing money + spend: info note naming page 9."""
    fin_a = _fin(cumulative_received=Decimal("100"), cumulative_spent=Decimal("50"))
    fin_b = _fin(
        cumulative_received=Decimal("100"),
        cumulative_spent=Decimal("60"),
        spent_this_quarter=Decimal("500000"),
    )
    old = _all(50.0, False)
    new = _all(50.0, False)
    old["1 Work"] = (50.0, True)
    new["1 Work"] = (50.0, True)
    old["2 Work"] = (50.0, True)
    new["2 Work"] = (50.0, True)
    project = make_project(
        [
            _quarter(Q1, old, fin_a, "a" * 64, 9),
            _quarter(Q2, new, fin_b, "b" * 64, 9),
        ]
    )
    findings = _run(project)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("CANT_VERIFY", "info")
    assert "page 9" in findings[0].title.lower()
    assert "page 9" in findings[0].explanation.lower()
    assert "not compared" in findings[0].explanation.lower()
    assert "5,00,000" in findings[0].explanation
    assert findings[0].evidence == []


def test_too_few_shared() -> None:
    project = make_project(
        [
            make_quarter(Q1, activities={"1 A": 10}, sha="a" * 64),
            make_quarter(Q2, activities={"1 A": 10}, sha="b" * 64),
        ]
    )
    findings = _run(project)
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_changes_ok() -> None:
    second = _six_equal()
    second["6 Work"] = 60.0
    project = make_project(
        [
            make_quarter(Q1, activities=_six_equal(), sha="a" * 64),
            make_quarter(Q2, activities=second, sha="b" * 64),
        ]
    )
    findings = _run(project)
    assert [f.status for f in findings] == ["OK"]


def test_single_quarter() -> None:
    project = make_project([make_quarter(Q1, activities=_six_equal())])
    findings = _run(project)
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_comparable_pair_without_flag_moves_on() -> None:
    """Unanchored repeats move on without FLAG, as CANT_VERIFY, never OK."""
    fin_a = _fin(cumulative_spent=Decimal("100"))
    project = make_project(
        [
            make_quarter(Q1, activities=_six_equal(), fin=fin_a, sha="a" * 64),
            make_quarter(Q2, activities=_six_equal(), fin={}, sha="b" * 64),
            make_quarter(Q3, activities={"1 A": 10}, fin={}, sha="c" * 64),
        ]
    )
    findings = _run(project)
    assert [f.status for f in findings] == ["CANT_VERIFY"]
    assert findings[0].title == "Repeated figures lack page references"
