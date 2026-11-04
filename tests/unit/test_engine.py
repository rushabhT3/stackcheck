"""Engine: all eight rules run in order, ids numbered, sorted by severity then rule."""

from datetime import date
from decimal import Decimal

from stackcheck.models import ProjectEvidence
from stackcheck.rules.checks import RuleSettings
from stackcheck.rules.engine import run_rules
from tests.factories import make_photo, make_project, make_quarter

Q1 = date(2025, 3, 31)
Q2 = date(2025, 6, 30)
Q3 = date(2025, 12, 31)
SETTINGS = RuleSettings()
FORBIDDEN = ("fraud", "scam", "lying", "cheat")


def _regress_and_gap() -> ProjectEvidence:
    return make_project([make_quarter(Q1, slabs=5), make_quarter(Q2, slabs=4), make_quarter(Q3)])


def test_ids_unique_and_stable() -> None:
    first = run_rules(_regress_and_gap(), SETTINGS)
    second = run_rules(_regress_and_gap(), SETTINGS)
    assert [f.finding_id for f in first] == [f.finding_id for f in second]
    assert len({f.finding_id for f in first}) == len(first)


def test_same_prefix_numbered() -> None:
    from stackcheck.models import PageRef

    ref = PageRef(doc_id="d", page=1)
    fin_a = {
        "currency_unit": "INR",
        "cumulative_spent": Decimal("10000000"),
        "cumulative_received": Decimal("1000000"),
        "ref": ref,
    }
    fin_b = {
        "currency_unit": "INR",
        "cumulative_spent": Decimal("10500000"),
        "spent_this_quarter": Decimal("0"),
        "cumulative_received": Decimal("1500000"),
        "received_this_quarter": Decimal("0"),
        "ref": ref,
    }
    project = make_project([make_quarter(Q1, fin=fin_a), make_quarter(Q2, fin=fin_b, sha="b" * 64)])
    r7 = [f for f in run_rules(project, SETTINGS) if f.rule == "R7_ARITHMETIC"]
    assert [f.finding_id for f in r7] == [
        f"R7_ARITHMETIC:{Q2.isoformat()}:all:1",
        f"R7_ARITHMETIC:{Q2.isoformat()}:all:2",
    ]


def test_order_severity_then_rule() -> None:
    project = make_project(
        [
            make_quarter(Q1, slabs=5, photos=[make_photo(stamp=date(2024, 1, 1))]),
            make_quarter(Q2, slabs=4),
            make_quarter(Q3),
        ]
    )
    findings = run_rules(project, SETTINGS)
    keys = [(f.severity, f.rule) for f in findings]
    assert keys == sorted(keys, key=lambda k: ({"high": 0, "medium": 1, "low": 2, "info": 3}[k[0]], k[1]))


def _flag_projects() -> list[ProjectEvidence]:
    r1 = make_project([make_quarter(Q1, slabs=7, photos=[make_photo(lower=3, upper=3)], sha="a" * 64)])
    r2 = make_project(
        [
            make_quarter(Q1, slabs=3, photos=[make_photo("a", dhash="0" * 16)], sha="a" * 64),
            make_quarter(Q2, slabs=5, photos=[make_photo("b", dhash="f" * 16)], sha="b" * 64),
        ]
    )
    r5 = make_project([make_quarter(Q1, sha="c" * 64), make_quarter(Q2, sha="c" * 64)])
    acts = {f"{i} Work": 30.0 for i in range(1, 7)}
    r6 = make_project(
        [
            make_quarter(
                Q1,
                activities=acts,
                fin={
                    "currency_unit": "INR",
                    "cumulative_spent": Decimal("6000000"),
                    "estimated_total_cost": Decimal("10000000"),
                },
                sha="d" * 64,
            )
        ]
    )
    r7 = make_project(
        [
            make_quarter(
                Q1,
                fin={"currency_unit": "INR", "cumulative_spent": Decimal("10000000")},
                sha="e" * 64,
            ),
            make_quarter(
                Q2,
                fin={
                    "currency_unit": "INR",
                    "cumulative_spent": Decimal("10500000"),
                    "spent_this_quarter": Decimal("0"),
                },
                sha="f" * 64,
            ),
        ]
    )
    r8 = make_project([make_quarter(Q1), make_quarter(Q3)])
    return [r1, r2, r5, r6, r7, r8]


def test_no_forbidden_words() -> None:
    for project in _flag_projects():
        for finding in run_rules(project, SETTINGS):
            text = f"{finding.title} {finding.explanation}".lower()
            assert not any(word in text for word in FORBIDDEN), text


def test_empty_project_all_cant_verify() -> None:
    findings = run_rules(make_project([make_quarter(Q1)]), SETTINGS)
    assert findings
    assert {f.status for f in findings} == {"CANT_VERIFY"}
    assert {f.severity for f in findings} == {"info"}


def test_capped_mode_keeps_within_quarter_flags() -> None:
    """Unsure identity: cross-quarter rules go CANT_VERIFY, within-quarter work stays."""
    from stackcheck.models import PageRef
    from stackcheck.rules.engine import run_rules_capped

    ref = PageRef(doc_id="d", page=1)
    fin_a = {"currency_unit": "INR", "cumulative_spent": Decimal("100"), "ref": ref}
    fin_b = {
        "currency_unit": "INR",
        "cumulative_spent": Decimal("100"),
        "spent_this_quarter": Decimal("500"),
        "ref": ref,
    }
    project = make_project([make_quarter(Q1, fin=fin_a), make_quarter(Q2, fin=fin_b)])
    findings = run_rules_capped(project, SETTINGS)
    by_rule: dict[str, list] = {}
    for finding in findings:
        by_rule.setdefault(finding.rule, []).append(finding)
    assert findings
    assert len({f.finding_id for f in findings}) == len(findings)
    for rule in ("R2_REUSED_PHOTO", "R4_CLAIM_REGRESSION", "R5_COPY_PASTE_QUARTER", "R8_MISSING_QUARTER"):
        assert by_rule.get(rule), rule
        assert {f.status for f in by_rule[rule]} == {"CANT_VERIFY"}
        assert all("not compared" in f.explanation for f in by_rule[rule])
    r7 = [f for f in by_rule.get("R7_ARITHMETIC", []) if f.status == "FLAG"]
    assert len(r7) == 1
    assert r7[0].severity == "low"
