"""R4: claimed progress must not go down between quarters."""

from datetime import date

from stackcheck.models import Finding, ProjectEvidence
from stackcheck.rules.checks import RuleSettings, r4_claim_regression
from tests.factories import make_project, make_quarter

Q1 = date(2025, 3, 31)
Q2 = date(2025, 6, 30)
SETTINGS = RuleSettings()


def _run(project: ProjectEvidence) -> list[Finding]:
    return r4_claim_regression(project, SETTINGS)


def test_single_quarter() -> None:
    findings = _run(make_project([make_quarter(Q1, slabs=3)]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_slabs_down_high() -> None:
    from stackcheck.models import PageRef

    older = make_quarter(Q1, slabs=5)
    newer = make_quarter(Q2, slabs=4)
    older.filing.blocks[0].ref = PageRef(doc_id=older.filing.doc_id, page=6)
    newer.filing.blocks[0].ref = PageRef(doc_id=newer.filing.doc_id, page=9)
    project = make_project([older, newer])
    findings = _run(project)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "high")
    assert findings[0].title == "Claimed slabs went down from 5 to 4"


def _with_block_and_activity_refs(project: ProjectEvidence, page: int = 7) -> ProjectEvidence:
    from stackcheck.models import PageRef

    for q in project.quarters:
        for b in q.filing.blocks:
            b.ref = PageRef(doc_id=q.filing.doc_id, page=page)
        for a in q.filing.activities:
            a.ref = PageRef(doc_id=q.filing.doc_id, page=page)
    return project


def test_activity_down_medium() -> None:
    older = make_quarter(Q1, activities={"1 Excavation": 60})
    newer = make_quarter(Q2, activities={"1 Excavation": 57})
    project = _with_block_and_activity_refs(make_project([older, newer]))
    findings = _run(project)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "medium")


def test_activity_within_two_points_ok() -> None:
    project = _with_block_and_activity_refs(
        make_project(
            [
                make_quarter(Q1, activities={"1 Excavation": 60}),
                make_quarter(Q2, activities={"1 Excavation": 59}),
            ]
        )
    )
    findings = _run(project)
    assert [f.status for f in findings] == ["OK"]


def test_different_blocks_not_compared() -> None:
    project = make_project(
        [make_quarter(Q1, slabs=5, block="MAIN"), make_quarter(Q2, slabs=9, block="Annex")]
    )
    findings = _run(project)
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_uncomparable_activities_skipped() -> None:
    project = _with_block_and_activity_refs(
        make_project(
            [
                make_quarter(Q1, activities={"1 Excavation": 60}),
                make_quarter(Q2, activities={"1 Excavation": 57, "2 New": 40.0, "3 Blank": None}),
            ]
        ),
        page=1,
    )
    findings = _run(project)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "medium")


def _with_activity_refs(project: ProjectEvidence) -> ProjectEvidence:
    return _with_block_and_activity_refs(project, page=7)


def test_r4_activity_cross_block_no_flag() -> None:
    """Sept B-2 staircases 30 vs Dec Block E staircases 10: different blocks, no flag."""
    older = make_quarter(Q1, block="B-2", activities={"9 Staircases, Lifts Wells": 30.0})
    newer = make_quarter(Q2, block="E", activities={"9 Staircases, Lifts Wells": 10.0})
    project = _with_activity_refs(make_project([older, newer]))
    findings = [f for f in _run(project) if f.rule == "R4_CLAIM_REGRESSION" and f.status == "FLAG"]
    assert findings == []


def test_r4_activity_same_block_flag_carries_two_refs() -> None:
    """Block C Dec 45 vs Mar 40: exactly one flag with two page refs and the plain wording."""
    from stackcheck.models import PageRef

    older = make_quarter(
        Q1, block="C", activities={"14 Staircases, Lifts Wells and Lobbies": 45.0}, sha="a" * 64
    )
    newer = make_quarter(
        Q2, block="C", activities={"14 Staircases, Lifts Wells and Lobbies": 40.0}, sha="b" * 64
    )
    for q in (older, newer):
        q.filing.blocks[0].ref = PageRef(doc_id=q.filing.doc_id, page=7)
        for a in q.filing.activities:
            a.ref = PageRef(doc_id=q.filing.doc_id, page=7)
    project = make_project([older, newer])
    findings = [f for f in _run(project) if f.status == "FLAG"]
    assert len(findings) == 1
    flag = findings[0]
    assert flag.rule == "R4_CLAIM_REGRESSION"
    pages = sorted(e.ref.page for e in flag.evidence if e.ref is not None)
    assert pages == [7, 7]
    doc_ids = {e.ref.doc_id for e in flag.evidence if e.ref is not None}
    assert len(doc_ids) == 2
    assert "reported progress fell from 45% to 40% between two filings" in flag.explanation
    assert "A question to ask the builder or RERA" in flag.explanation


def test_r4_block_c_staircases_dec45_mar40_flags_once() -> None:
    """Live case: S.NO drifts 14 to 13, block C on page 7 both sides, one FLAG."""
    from stackcheck.models import PageRef

    dec = make_quarter(
        date(2024, 12, 31),
        block="C",
        activities={"14. Staircases, Lifts Wells and Lobbies": 45.0},
        sha="25145d09ce81fd15" + "0" * 48,
    )
    mar = make_quarter(
        date(2025, 3, 31),
        block="C",
        activities={"13. Staircases, Lifts Wells and Lobbies": 40.0},
        sha="93aed199f53910be" + "0" * 48,
    )
    for q in (dec, mar):
        q.filing.blocks[0].ref = PageRef(doc_id=q.filing.doc_id, page=7)
        for a in q.filing.activities:
            a.ref = PageRef(doc_id=q.filing.doc_id, page=7)
    project = make_project([dec, mar])
    findings = [f for f in _run(project) if f.status == "FLAG"]
    assert len(findings) == 1
    flag = findings[0]
    assert "reported progress fell from 45% to 40% between two filings" in flag.explanation
    refs = sorted((e.ref.doc_id, e.ref.page) for e in flag.evidence if e.ref is not None)
    assert refs == [("25145d09ce81fd15", 7), ("93aed199f53910be", 7)]


def test_r4_block_label_normalises_spellings() -> None:
    """'Block A', 'BLOCK - A (ORION)', 'BLOCK-A' all key as A."""
    from stackcheck.rules.checks import _normalize_block_label

    assert _normalize_block_label("Block A") == "A"
    assert _normalize_block_label("BLOCK - A (ORION)") == "A"
    assert _normalize_block_label("BLOCK-A") == "A"
    assert _normalize_block_label("Block - A") == "A"
    assert _normalize_block_label("B-2") == "B-2"
    assert _normalize_block_label("E") == "E"


def test_r4_2023_block_in_name_matches_page_map() -> None:
    """2023 style 'Block A - ...' matches a page-mapped 'A' row without a name prefix."""
    from stackcheck.models import PageRef

    older = make_quarter(Q1, block="A", activities={"Block A - 9. Staircases": 30.0})
    newer = make_quarter(Q2, block="A", activities={"9. Staircases": 25.0})
    for q in (older, newer):
        q.filing.blocks[0].ref = PageRef(doc_id=q.filing.doc_id, page=4)
        for a in q.filing.activities:
            a.ref = PageRef(doc_id=q.filing.doc_id, page=4)
    project = make_project([older, newer])
    findings = [f for f in _run(project) if f.status == "FLAG"]
    assert len(findings) == 1
    assert findings[0].block_label == "A"


def test_r4_activity_missing_ref_downgrades_to_cant_verify() -> None:
    """A drop without page refs on both sides must never flag."""
    older = make_quarter(Q1, block="C", activities={"14 Staircases": 45.0})
    newer = make_quarter(Q2, block="C", activities={"14 Staircases": 40.0})
    # No refs set: both activities have ref None.
    project = make_project([older, newer])
    findings = [f for f in _run(project) if f.status == "FLAG"]
    assert findings == []
    statuses = {f.status for f in _run(project)}
    assert "CANT_VERIFY" in statuses


def test_r4_stored_block_label_wins_over_page_map() -> None:
    """Stored ActivityProgress.block_label beats the page-map/name derivation."""
    from stackcheck.models import PageRef

    older = make_quarter(Q1, block="B-2", activities={"9 Staircases, Lifts Wells": 45.0})
    newer = make_quarter(Q2, block="E", activities={"9 Staircases, Lifts Wells": 40.0})
    for q in (older, newer):
        q.filing.blocks[0].ref = PageRef(doc_id=q.filing.doc_id, page=7)
        for a in q.filing.activities:
            a.ref = PageRef(doc_id=q.filing.doc_id, page=7)
            a.block_label = "Block C"
    project = make_project([older, newer])
    findings = [f for f in _run(project) if f.status == "FLAG"]
    assert len(findings) == 1
    assert findings[0].block_label == "C"


def test_r4_stored_block_labels_differ_no_flag() -> None:
    """Same filing block, but stored row labels disagree: no match, no flag."""
    from stackcheck.models import PageRef

    older = make_quarter(Q1, block="C", activities={"9 Staircases": 45.0})
    newer = make_quarter(Q2, block="C", activities={"9 Staircases": 40.0})
    for q in (older, newer):
        q.filing.blocks[0].ref = PageRef(doc_id="d", page=7)
        for a in q.filing.activities:
            a.ref = PageRef(doc_id="d", page=7)
    older.filing.activities[0].block_label = "A"
    newer.filing.activities[0].block_label = "B"
    project = make_project([older, newer])
    assert [f for f in _run(project) if f.status == "FLAG"] == []


def _b2_pair(
    older_vals: dict[str, float],
    newer_vals: dict[str, float],
    newer_page: int = 6,
    older_page: int = 6,
) -> ProjectEvidence:
    """Block B-2 pair with page refs, like the Mar 2025 filing (page 6)."""
    from stackcheck.models import PageRef

    older = make_quarter(date(2024, 12, 31), block="B-2", activities=older_vals)
    newer = make_quarter(date(2025, 3, 31), block="B-2", activities=newer_vals)
    older.filing.blocks[0].ref = PageRef(doc_id=older.filing.doc_id, page=older_page)
    newer.filing.blocks[0].ref = PageRef(doc_id=newer.filing.doc_id, page=newer_page)
    for a in older.filing.activities:
        a.ref = PageRef(doc_id=older.filing.doc_id, page=older_page)
    for a in newer.filing.activities:
        a.ref = PageRef(doc_id=newer.filing.doc_id, page=newer_page)
    return make_project([older, newer])


def test_r4_b2_finished_items_zero_flags() -> None:
    """Live case Mar 2025 Block B-2 p6: 100->10, 100->70, 100->70 give zero FLAGs."""
    project = _b2_pair(
        {"1 A": 100.0, "2 B": 100.0, "3 C": 100.0},
        {"1 A": 10.0, "2 B": 70.0, "3 C": 70.0},
    )
    findings = _run(project)
    assert [f for f in findings if f.status == "FLAG"] == []
    guards = [f for f in findings if f.inputs.get("guard") == "failed"]
    assert len(guards) == 3
    assert all(f.status == "CANT_VERIFY" for f in guards)
    assert all("needs a second read" in f.title for f in guards)


def test_r4_two_drops_same_page_become_cant_verify() -> None:
    """Two drops sharing one newer page: CANT_VERIFYs with guard, never flags."""
    project = _b2_pair({"1 A": 60.0, "2 B": 80.0}, {"1 A": 50.0, "2 B": 70.0})
    findings = _run(project)
    assert [f for f in findings if f.status == "FLAG"] == []
    guards = [f for f in findings if f.inputs.get("guard") == "failed"]
    assert len(guards) == 2
    assert all(f.status == "CANT_VERIFY" for f in guards)
    assert all("needs a second read" in f.title for f in guards)


def test_r4_single_finished_item_drop_needs_second_read() -> None:
    """A lone 100->70 drop is CANT_VERIFY with guard, never a flag."""
    project = _b2_pair({"1 A": 100.0}, {"1 A": 70.0})
    findings = _run(project)
    assert [f for f in findings if f.status == "FLAG"] == []
    guards = [f for f in findings if f.inputs.get("guard") == "failed"]
    assert len(guards) == 1
    assert guards[0].status == "CANT_VERIFY"
    assert "needs a second read" in guards[0].title


def test_r4_finished_item_small_slip_still_flags() -> None:
    """A lone 100->90 drop is a small genuine slip: still one FLAG."""
    project = _b2_pair({"1 A": 100.0}, {"1 A": 90.0})
    findings = _run(project)
    flags = [f for f in findings if f.status == "FLAG"]
    assert len(flags) == 1
    assert flags[0].inputs.get("guard") != "failed"


def test_r4_slab_flag_carries_both_refs() -> None:
    """Slab drops need the doc_id and page of both quarters."""
    older = make_quarter(Q1, slabs=5, block="MAIN", sha="c" * 64)
    newer = make_quarter(Q2, slabs=4, block="MAIN", sha="d" * 64)
    from stackcheck.models import PageRef

    older.filing.blocks[0].ref = PageRef(doc_id=older.filing.doc_id, page=6)
    newer.filing.blocks[0].ref = PageRef(doc_id=newer.filing.doc_id, page=6)
    project = make_project([older, newer])
    findings = [f for f in _run(project) if f.status == "FLAG"]
    assert len(findings) == 1
    refs = [e.ref for e in findings[0].evidence if e.ref is not None]
    assert len(refs) >= 2
    assert {r.page for r in refs} == {6}
    assert len({r.doc_id for r in refs}) == 2
