"""R7: money columns must add up within a filing and across quarters."""

from datetime import date
from decimal import Decimal

from stackcheck.models import Finding, PageRef, ProjectEvidence
from stackcheck.rules.checks import RuleSettings, r7_arithmetic
from tests.factories import make_project, make_quarter

Q1 = date(2025, 3, 31)
Q2 = date(2025, 6, 30)
SETTINGS = RuleSettings()
REF = PageRef(doc_id="d", page=1)


def _inr(**kwargs) -> dict:
    return {"currency_unit": "INR", "ref": REF, **kwargs}


def _run(project: ProjectEvidence) -> list[Finding]:
    return r7_arithmetic(project, SETTINGS)


def test_spent_exceeds_cumulative_low() -> None:
    fin = _inr(cumulative_spent=Decimal("500000"), spent_this_quarter=Decimal("800000"))
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "low")


def test_received_exceeds_cumulative_low() -> None:
    fin = _inr(cumulative_received=Decimal("100000"), received_this_quarter=Decimal("200000"))
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert len(findings) == 1
    assert findings[0].status == "FLAG"


def test_small_drift_needs_second_read() -> None:
    """Any drift, however small, breaks the exact identity: CANT_VERIFY with guard."""
    fin_a = _inr(cumulative_spent=Decimal("10000000"))
    fin_b = _inr(cumulative_spent=Decimal("10050000"), spent_this_quarter=Decimal("0"))
    project = make_project([make_quarter(Q1, fin=fin_a), make_quarter(Q2, fin=fin_b)])
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].inputs.get("guard") == "failed"


def test_large_mismatch_needs_second_read() -> None:
    fin_a = _inr(cumulative_spent=Decimal("10000000"))
    fin_b = _inr(cumulative_spent=Decimal("10500000"), spent_this_quarter=Decimal("0"))
    project = make_project([make_quarter(Q1, fin=fin_a), make_quarter(Q2, fin=fin_b)])
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].inputs.get("guard") == "failed"


def test_received_mismatch_needs_second_read() -> None:
    fin_a = _inr(cumulative_received=Decimal("1000000"))
    fin_b = _inr(cumulative_received=Decimal("1500000"), received_this_quarter=Decimal("0"))
    project = make_project([make_quarter(Q1, fin=fin_a), make_quarter(Q2, fin=fin_b)])
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].inputs.get("guard") == "failed"


def test_mixed_units_converted() -> None:
    fin_a = {"currency_unit": "INR_LAKH", "cumulative_spent": Decimal("100")}
    fin_b = _inr(cumulative_spent=Decimal("10050000"), spent_this_quarter=Decimal("50000"))
    project = make_project([make_quarter(Q1, fin=fin_a), make_quarter(Q2, fin=fin_b)])
    findings = _run(project)
    assert [f.status for f in findings] == ["OK"]


def test_nothing_checkable() -> None:
    project = make_project([make_quarter(Q1), make_quarter(Q2)])
    findings = _run(project)
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_identity_finding_carries_page_refs_and_guard() -> None:
    fin_a = _inr(cumulative_spent=Decimal("10000000"))
    fin_b = _inr(cumulative_spent=Decimal("10500000"), spent_this_quarter=Decimal("0"))
    project = make_project([make_quarter(Q1, fin=fin_a), make_quarter(Q2, fin=fin_b)])
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].inputs.get("guard") == "failed"
    assert findings[0].evidence
    assert all(e.ref is not None for e in findings[0].evidence)


def test_mismatch_without_page_ref_is_cant_verify() -> None:
    bare = {"currency_unit": "INR", "cumulative_spent": Decimal("500000")}
    fin = {**bare, "spent_this_quarter": Decimal("800000")}
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"


def test_cross_quarter_mismatch_without_page_ref_is_cant_verify() -> None:
    fin_a = {"currency_unit": "INR", "cumulative_spent": Decimal("10000000")}
    fin_b = {
        "currency_unit": "INR",
        "cumulative_spent": Decimal("10500000"),
        "spent_this_quarter": Decimal("0"),
    }
    project = make_project([make_quarter(Q1, fin=fin_a), make_quarter(Q2, fin=fin_b)])
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].inputs.get("guard") == "failed"


def test_net_balance_sept_fires_low() -> None:
    """Sept 2024: net 11,42,92,006 vs collected-spent 1,55,77,340 -> FLAG low."""
    fin = _inr(
        cumulative_received=Decimal("296633535"),
        cumulative_spent=Decimal("281056195"),
        net_balance=Decimal("114292006"),
        estimated_total_cost=Decimal("993300000"),
    )
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    flags = [f for f in findings if f.status == "FLAG" and "Net amount" in f.title]
    assert len(flags) == 1
    assert flags[0].severity == "low"
    assert "A question to ask the builder or RERA" in flags[0].explanation
    assert all(e.ref is not None for e in flags[0].evidence)


def test_net_balance_mar_fires_low() -> None:
    """Mar 2025: net 13,18,34,933 vs collected-spent -5,64,39,387 -> FLAG low."""
    fin = _inr(
        cumulative_received=Decimal("491961415"),
        cumulative_spent=Decimal("548400802"),
        net_balance=Decimal("131834933"),
        estimated_total_cost=Decimal("993300000"),
    )
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    flags = [f for f in findings if f.status == "FLAG" and "Net amount" in f.title]
    assert len(flags) == 1
    assert flags[0].severity == "low"


def test_net_balance_matching_is_ok() -> None:
    """Net equals collected minus spent: no net flag."""
    fin = _inr(
        cumulative_received=Decimal("100"),
        cumulative_spent=Decimal("40"),
        net_balance=Decimal("60"),
        estimated_total_cost=Decimal("10000"),
    )
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert [f for f in findings if f.status == "FLAG" and "Net amount" in f.title] == []
    assert [f.status for f in findings] == ["OK"]


def test_equal_values_guard_net_matches_received() -> None:
    """Net equalling collected looks like a copying slip: CANT_VERIFY, no net flag."""
    fin = _inr(
        cumulative_received=Decimal("100"),
        cumulative_spent=Decimal("40"),
        net_balance=Decimal("100"),
        estimated_total_cost=Decimal("10000"),
    )
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    guards = [f for f in findings if f.inputs.get("guard") == "failed"]
    assert len(guards) == 1
    assert guards[0].status == "CANT_VERIFY"
    assert "needs a second read" in guards[0].title
    assert [f for f in findings if f.status == "FLAG" and "Net amount" in f.title] == []


def test_equal_values_guard_received_matches_spent() -> None:
    """Collected equalling spent is also an extraction error, excluded from net."""
    fin = _inr(
        cumulative_received=Decimal("80"),
        cumulative_spent=Decimal("80"),
        net_balance=Decimal("5"),
        estimated_total_cost=Decimal("10000"),
    )
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    guards = [f for f in findings if f.inputs.get("guard") == "failed"]
    assert len(guards) == 1
    assert guards[0].status == "CANT_VERIFY"
    assert [f for f in findings if f.status == "FLAG" and "Net amount" in f.title] == []


def test_identity_received_chain_exact_ok() -> None:
    """Sept received plus Dec received-this-quarter equals Dec received: no finding."""
    sept = make_quarter(Q1, fin=_inr(cumulative_received=Decimal("296633535")))
    dec = make_quarter(
        Q2,
        fin=_inr(
            cumulative_received=Decimal("405894977"),
            received_this_quarter=Decimal("109261442"),
        ),
    )
    findings = _run(make_project([sept, dec]))
    assert [f.status for f in findings] == ["OK"]


def test_identity_spent_chain_exact_ok() -> None:
    """Dec spent plus Mar spent-this-quarter equals Mar spent: no finding."""
    dec = make_quarter(Q1, fin=_inr(cumulative_spent=Decimal("399226445")))
    mar = make_quarter(
        Q2,
        fin=_inr(
            cumulative_spent=Decimal("548400802"),
            spent_this_quarter=Decimal("149174357"),
        ),
    )
    findings = _run(make_project([dec, mar]))
    assert [f.status for f in findings] == ["OK"]


def test_identity_broken_spent_needs_second_read() -> None:
    """Spent identity off by one: CANT_VERIFY with the guard marker."""
    dec = make_quarter(Q1, fin=_inr(cumulative_spent=Decimal("399226445")))
    mar = make_quarter(
        Q2,
        fin=_inr(
            cumulative_spent=Decimal("548400802"),
            spent_this_quarter=Decimal("149174358"),
        ),
    )
    findings = _run(make_project([dec, mar]))
    guards = [f for f in findings if f.inputs.get("guard") == "failed"]
    assert len(guards) == 1
    assert guards[0].status == "CANT_VERIFY"
    assert "needs a second read" in guards[0].title


def _sept_dec_mar_money() -> ProjectEvidence:
    """Three firing quarters with exact received/spent chains and distinct pages."""
    from stackcheck.models import PageRef

    sept = make_quarter(
        date(2024, 9, 30),
        fin={
            "currency_unit": "INR",
            "cumulative_received": Decimal("296633535"),
            "cumulative_spent": Decimal("281056195"),
            "net_balance": Decimal("114292006"),
            "estimated_total_cost": Decimal("993300000"),
            "ref": PageRef(doc_id="s" * 16, page=3),
        },
        sha="s" * 64,
    )
    dec = make_quarter(
        date(2024, 12, 31),
        fin={
            "currency_unit": "INR",
            "cumulative_received": Decimal("405894977"),
            "received_this_quarter": Decimal("109261442"),
            "cumulative_spent": Decimal("399226445"),
            "spent_this_quarter": Decimal("118170250"),
            "net_balance": Decimal("96668532"),
            "estimated_total_cost": Decimal("993300000"),
            "ref": PageRef(doc_id="t" * 16, page=5),
        },
        sha="t" * 64,
    )
    mar = make_quarter(
        date(2025, 3, 31),
        fin={
            "currency_unit": "INR",
            "cumulative_received": Decimal("491961415"),
            "received_this_quarter": Decimal("86086438"),
            "cumulative_spent": Decimal("548400802"),
            "spent_this_quarter": Decimal("149174357"),
            "net_balance": Decimal("131834933"),
            "estimated_total_cost": Decimal("993300000"),
            "ref": PageRef(doc_id="u" * 16, page=8),
        },
        sha="u" * 64,
    )
    return make_project([sept, dec, mar])


def test_net_two_firing_quarters_give_one_flag() -> None:
    """Two firing quarters aggregate into exactly one low FLAG with both refs."""
    project = _sept_dec_mar_money()
    two = make_project(project.quarters[:2], label="TEST")
    findings = _run(two)
    flags = [f for f in findings if f.status == "FLAG" and "Net amount" in f.title]
    assert len(flags) == 1
    assert flags[0].severity == "low"
    assert flags[0].quarter_end == date(2024, 12, 31)
    assert {e.ref.page for e in flags[0].evidence if e.ref is not None} == {3, 5}


def test_net_three_firing_quarters_give_one_flag() -> None:
    """Sept/Dec/Mar: one FLAG, three distinct pages, latest X/Y in the explanation."""
    from stackcheck.rules.checks import format_inr

    findings = _run(_sept_dec_mar_money())
    flags = [f for f in findings if f.status == "FLAG" and "Net amount" in f.title]
    assert len(flags) == 1
    assert flags[0].severity == "low"
    assert flags[0].quarter_end == date(2025, 3, 31)
    assert {e.ref.page for e in flags[0].evidence if e.ref is not None} == {3, 5, 8}
    assert format_inr(Decimal("131834933")) in flags[0].explanation
    assert format_inr(Decimal("491961415") - Decimal("548400802")) in flags[0].explanation


def test_implausible_spent_with_crore_estimate_needs_second_read() -> None:
    """Estimate 99.33cr with cumulative spent 56195: one CANT_VERIFY guard, no flags."""
    fin = _inr(cumulative_spent=Decimal("56195"), estimated_total_cost=Decimal("993300000"))
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].inputs.get("guard") == "failed"
    assert "needs a second read" in findings[0].title


def test_implausible_guard_skips_within_filing_flag() -> None:
    """Guard fires: a within-filing column problem in the same quarter is skipped."""
    fin = _inr(
        cumulative_spent=Decimal("56195"),
        spent_this_quarter=Decimal("800000"),
        estimated_total_cost=Decimal("993300000"),
    )
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert [f for f in findings if f.status == "FLAG"] == []
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].inputs.get("guard") == "failed"


def test_implausible_guard_skips_net_flag() -> None:
    """Guard fires: a net mismatch in the same quarter is skipped too."""
    fin = _inr(
        cumulative_received=Decimal("50000"),
        cumulative_spent=Decimal("56195"),
        net_balance=Decimal("114292006"),
        estimated_total_cost=Decimal("993300000"),
    )
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert [f for f in findings if f.status == "FLAG" and "Net amount" in f.title] == []
    assert len(findings) == 1
    assert findings[0].inputs.get("guard") == "failed"


def test_small_estimate_tiny_figures_unaffected() -> None:
    """Estimate under 1 crore: tiny figures still check out as OK."""
    fin = _inr(cumulative_spent=Decimal("56195"), estimated_total_cost=Decimal("10000"))
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert [f.status for f in findings] == ["OK"]
    assert all(f.inputs.get("guard") != "failed" for f in findings)


def test_identity_received_chain_dec_to_mar_exact_ok() -> None:
    """Dec 40,58,74,977 + 8,60,86,438 equals Mar 49,19,61,415: exact, no finding."""
    dec = make_quarter(Q1, fin=_inr(cumulative_received=Decimal("405874977")))
    mar = make_quarter(
        Q2,
        fin=_inr(
            cumulative_received=Decimal("491961415"),
            received_this_quarter=Decimal("86086438"),
        ),
    )
    assert [f.status for f in _run(make_project([dec, mar]))] == ["OK"]


def test_identity_spent_chain_sept_to_dec_exact_ok() -> None:
    """Sept 28,10,56,195 + 11,81,70,250 equals Dec 39,92,26,445: exact, no finding."""
    sept = make_quarter(Q1, fin=_inr(cumulative_spent=Decimal("281056195")))
    dec = make_quarter(
        Q2,
        fin=_inr(
            cumulative_spent=Decimal("399226445"),
            spent_this_quarter=Decimal("118170250"),
        ),
    )
    assert [f.status for f in _run(make_project([sept, dec]))] == ["OK"]


def test_identity_received_chain_broken_needs_second_read() -> None:
    """Received chain off by one: CANT_VERIFY with the guard marker."""
    dec = make_quarter(Q1, fin=_inr(cumulative_received=Decimal("405874977")))
    mar = make_quarter(
        Q2,
        fin=_inr(
            cumulative_received=Decimal("491961415"),
            received_this_quarter=Decimal("86086439"),
        ),
    )
    findings = _run(make_project([dec, mar]))
    guards = [f for f in findings if f.inputs.get("guard") == "failed"]
    assert len(guards) == 1
    assert guards[0].status == "CANT_VERIFY"
    assert "needs a second read" in guards[0].title


def test_identity_spent_chain_broken_needs_second_read() -> None:
    """Spent chain off by one: CANT_VERIFY with the guard marker."""
    sept = make_quarter(Q1, fin=_inr(cumulative_spent=Decimal("281056195")))
    dec = make_quarter(
        Q2,
        fin=_inr(
            cumulative_spent=Decimal("399226445"),
            spent_this_quarter=Decimal("118170251"),
        ),
    )
    findings = _run(make_project([sept, dec]))
    guards = [f for f in findings if f.inputs.get("guard") == "failed"]
    assert len(guards) == 1
    assert guards[0].status == "CANT_VERIFY"
    assert "needs a second read" in guards[0].title


def test_net_balance_without_page_ref_is_cant_verify() -> None:
    """Net mismatch with no money page ref is CANT_VERIFY, never a flag."""
    fin = {
        "currency_unit": "INR",
        "cumulative_received": Decimal("296633535"),
        "cumulative_spent": Decimal("281056195"),
        "net_balance": Decimal("114292006"),
        "estimated_total_cost": Decimal("993300000"),
    }
    findings = _run(make_project([make_quarter(Q1, fin=fin)]))
    assert [f for f in findings if f.status == "FLAG" and "Net amount" in f.title] == []
    assert any(f.status == "CANT_VERIFY" for f in findings)


def test_net_2023_filings_stay_flagged_with_page_refs() -> None:
    """Hand-verified Project E 2023 regression: net never matches collected-spent.

    June 2023 p10: net 37,05,041.71 vs 3,04,59,767.69 - 5,29,48,388,
    with received 45,26,496 and cost 1,03,48,388 this quarter.
    Sept 2023 p10: net 31,91,952 vs 41,24,34,379.69 - 6,46,74,004,
    with received 1,07,83,612 and cost 1,17,25,616 this quarter.
    The 2023 estimate is 75.94cr (7594 lakh), confirmed from the money page.
    """
    from stackcheck.rules.checks import format_inr

    estimate = Decimal("759400000")
    june = make_quarter(
        date(2023, 6, 30),
        fin={
            "currency_unit": "INR",
            "cumulative_received": Decimal("30459767.69"),
            "received_this_quarter": Decimal("4526496"),
            "cumulative_spent": Decimal("52948388"),
            "spent_this_quarter": Decimal("10348388"),
            "net_balance": Decimal("3705041.71"),
            "estimated_total_cost": estimate,
            "ref": PageRef(doc_id="482a102bcd80a2ec", page=10),
        },
        sha="4" * 64,
    )
    sept = make_quarter(
        date(2023, 9, 30),
        fin={
            "currency_unit": "INR",
            "cumulative_received": Decimal("412434379.69"),
            "received_this_quarter": Decimal("10783612"),
            "cumulative_spent": Decimal("64674004"),
            "spent_this_quarter": Decimal("11725616"),
            "net_balance": Decimal("3191952"),
            "estimated_total_cost": estimate,
            "ref": PageRef(doc_id="fe8bfd11b4f01f85", page=10),
        },
        sha="f" * 64,
    )
    findings = _run(make_project([june, sept]))
    flags = [f for f in findings if f.status == "FLAG" and "Net amount" in f.title]
    assert len(flags) == 1
    assert flags[0].severity == "low"
    assert flags[0].quarter_end == date(2023, 9, 30)
    assert flags[0].explanation.startswith("Net amounts do not reconcile in 2 quarters.")
    assert format_inr(Decimal("3191952")) in flags[0].explanation
    refs = {(e.ref.doc_id, e.ref.page) for e in flags[0].evidence if e.ref is not None}
    assert ("482a102bcd80a2ec", 10) in refs
    assert ("fe8bfd11b4f01f85", 10) in refs
