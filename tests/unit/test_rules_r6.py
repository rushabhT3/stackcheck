"""R6: money spent must not run far ahead of work done."""

from datetime import date
from decimal import Decimal

from stackcheck.models import Finding, PageRef, ProjectEvidence
from stackcheck.rules.checks import RuleSettings, r6_money_vs_progress
from tests.factories import make_project, make_quarter

Q1 = date(2025, 3, 31)
Q2 = date(2025, 6, 30)
SETTINGS = RuleSettings()
REF = PageRef(doc_id="d", page=1)


def _fin(spent: str, total: str) -> dict:
    return {
        "currency_unit": "INR",
        "cumulative_spent": Decimal(spent),
        "estimated_total_cost": Decimal(total),
        "ref": REF,
    }


def _run(project: ProjectEvidence) -> list[Finding]:
    return r6_money_vs_progress(project, SETTINGS)


def test_missing_cost() -> None:
    quarter = make_quarter(
        Q1,
        activities={"1 A": 30.0},
        fin={"currency_unit": "INR", "cumulative_spent": Decimal("6000000")},
    )
    findings = _run(make_project([quarter]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_spending_ahead_low() -> None:
    acts = {f"{i} Work": 30.0 for i in range(1, 7)}
    quarter = make_quarter(Q1, activities=acts, fin=_fin("6000000", "10000000"))
    findings = _run(make_project([quarter]))
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "low")
    assert findings[0].title == "Money spent runs ahead of work done"
    assert "Land and approval costs" in findings[0].explanation


def test_spending_close_ok() -> None:
    acts = {f"{i} Work": 30.0 for i in range(1, 7)}
    quarter = make_quarter(Q1, activities=acts, fin=_fin("4000000", "10000000"))
    findings = _run(make_project([quarter]))
    assert [f.status for f in findings] == ["OK"]


def test_ok_title_matches_direction_and_method() -> None:
    """40% spent vs 85% done trails, and the explanation says how 85% is computed."""
    acts = {f"{i} Work": 85.0 for i in range(1, 7)}
    quarter = make_quarter(Q1, activities=acts, fin=_fin("4000000", "10000000"))
    findings = _run(make_project([quarter]))
    assert len(findings) == 1
    assert findings[0].title == "Money spent trails work done"
    assert "average of 6 activity percentages" in findings[0].explanation


def test_progress_from_slabs() -> None:
    acts = {"1 A": 30.0, "2 B": 30.0}
    quarter = make_quarter(Q1, activities=acts, slabs=2, floors=4, fin=_fin("5000000", "10000000"))
    findings = _run(make_project([quarter]))
    assert [f.status for f in findings] == ["OK"]


def test_no_main_block_for_slabs() -> None:
    acts = {"1 A": 30.0, "2 B": 30.0}
    quarter = make_quarter(
        Q1, activities=acts, slabs=2, floors=4, block="Annex", fin=_fin("5000000", "10000000")
    )
    findings = _run(make_project([quarter]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_slabs_without_floors_missing() -> None:
    acts = {"1 A": 30.0, "2 B": 30.0}
    quarter = make_quarter(Q1, activities=acts, slabs=2, fin=_fin("5000000", "10000000"))
    findings = _run(make_project([quarter]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_latest_quarter_with_data_wins() -> None:
    acts = {f"{i} Work": 30.0 for i in range(1, 7)}
    older = make_quarter(Q1, activities=acts, fin=_fin("6000000", "10000000"))
    newer = make_quarter(
        Q2,
        activities=acts,
        fin={"currency_unit": "INR", "cumulative_spent": Decimal("6000000")},
    )
    findings = _run(make_project([older, newer]))
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "low")
    assert findings[0].quarter_end == Q1


def test_ok_carries_money_page_ref() -> None:
    acts = {f"{i} Work": 30.0 for i in range(1, 7)}
    quarter = make_quarter(Q1, activities=acts, fin=_fin("4000000", "10000000"))
    findings = _run(make_project([quarter]))
    assert [f.status for f in findings] == ["OK"]
    assert findings[0].evidence
    assert all(e.ref is not None for e in findings[0].evidence)


def test_implausible_money_needs_second_read() -> None:
    """Crore estimate with tiny spend: CANT_VERIFY read-fault, never OK or FLAG."""
    acts = {f"{i} Work": 30.0 for i in range(1, 7)}
    quarter = make_quarter(Q1, activities=acts, fin=_fin("4000", "993300000"))
    findings = _run(make_project([quarter]))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].title == "Money figures look implausible, needs a second read"
    assert findings[0].inputs.get("guard") == "failed"


def test_implausible_money_zero_spent_needs_second_read() -> None:
    """Crore estimate with zero spend: CANT_VERIFY read-fault, never OK."""
    acts = {f"{i} Work": 30.0 for i in range(1, 7)}
    quarter = make_quarter(Q1, activities=acts, fin=_fin("0", "993300000"))
    findings = _run(make_project([quarter]))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].title == "Money figures look implausible, needs a second read"
    assert findings[0].inputs.get("guard") == "failed"


def test_flag_without_page_ref_is_cant_verify() -> None:
    bare = {
        "currency_unit": "INR",
        "cumulative_spent": Decimal("6000000"),
        "estimated_total_cost": Decimal("10000000"),
    }
    acts = {f"{i} Work": 30.0 for i in range(1, 7)}
    quarter = make_quarter(Q1, activities=acts, fin=bare)
    findings = _run(make_project([quarter]))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
