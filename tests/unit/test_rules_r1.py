"""R1: the elevation photo must show about as many floors as slabs claimed."""

from datetime import date

from stackcheck.models import Finding, PageRef, QuarterEvidence
from stackcheck.rules.checks import RuleSettings, apply_user_floors, r1_photo_vs_claim
from tests.factories import make_photo, make_project, make_quarter

QE = date(2025, 3, 31)
SETTINGS = RuleSettings()


def _run(quarter: QuarterEvidence) -> list[Finding]:
    return r1_photo_vs_claim(make_project([quarter]), SETTINGS)


def test_no_claim() -> None:
    findings = _run(make_quarter(QE, photos=[make_photo(lower=3, upper=3)]))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert "claim" in findings[0].explanation.lower()


def test_no_usable_photo() -> None:
    findings = _run(make_quarter(QE, slabs=3))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert "photo" in findings[0].explanation.lower()


def test_ok() -> None:
    findings = _run(make_quarter(QE, slabs=3, photos=[make_photo(lower=3, upper=3)]))
    assert len(findings) == 1
    assert findings[0].status == "OK"
    assert findings[0].severity == "info"


def test_flag_high() -> None:
    quarter = make_quarter(QE, slabs=7, photos=[make_photo("p1", lower=3, upper=3)])
    quarter.filing.blocks[0].ref = PageRef(doc_id="d", page=4)
    findings = _run(quarter)
    assert len(findings) == 1
    flag = findings[0]
    assert (flag.status, flag.severity) == ("FLAG", "high")
    assert flag.title == "Photo shows 3 floors, report claims 7 slabs"
    kinds = [e.kind for e in flag.evidence]
    assert kinds == ["quote", "photo", "computation"]
    assert flag.evidence[0].ref is not None and flag.evidence[0].ref.page == 4
    assert flag.evidence[2].value == "(7 - 1) - 3 = 3 floors short"
    assert flag.inputs["gap_floors"] == "3"


def test_flag_medium() -> None:
    findings = _run(make_quarter(QE, slabs=6, photos=[make_photo(lower=3, upper=4)]))
    assert findings[0].status == "FLAG"
    assert findings[0].severity == "medium"


def test_overlap_with_open_top() -> None:
    findings = _run(make_quarter(QE, slabs=7, photos=[make_photo(lower=4, upper=None)]))
    assert findings[0].status == "CANT_VERIFY"
    assert "overlap" in findings[0].explanation.lower()


def test_stamp_outside_window_excluded() -> None:
    photo = make_photo(lower=3, upper=3, stamp=date(2024, 1, 1))
    findings = _run(make_quarter(QE, slabs=3, photos=[photo]))
    assert findings[0].status == "CANT_VERIFY"


def test_block_guess_mismatch_excluded() -> None:
    photo = make_photo(lower=3, upper=3, block_guess="Block B")
    findings = _run(make_quarter(QE, slabs=3, photos=[photo]))
    assert findings[0].status == "CANT_VERIFY"


def test_failed_floor_count_unused() -> None:
    photo = make_photo(status="CANT_VERIFY")
    findings = _run(make_quarter(QE, slabs=3, photos=[photo]))
    assert findings[0].status == "CANT_VERIFY"


def test_no_quarter_end() -> None:
    findings = _run(make_quarter(None, slabs=3, photos=[make_photo(lower=3, upper=3)]))
    assert findings[0].status == "CANT_VERIFY"


def test_stamp_inside_window_ok() -> None:
    photo = make_photo(lower=3, upper=3, stamp=date(2025, 2, 1))
    findings = _run(make_quarter(QE, slabs=3, photos=[photo]))
    assert findings[0].status == "OK"


def test_overlap_with_closed_range() -> None:
    findings = _run(make_quarter(QE, slabs=5, photos=[make_photo(lower=3, upper=8)]))
    assert findings[0].status == "CANT_VERIFY"
    assert "3 to 8 floors" in findings[0].explanation


def test_obscured_top_ok_when_lower_covers_claim() -> None:
    photo = make_photo(lower=5, upper=None, top_obscured=True)
    findings = _run(make_quarter(QE, slabs=3, photos=[photo]))
    assert findings[0].status == "OK"


def test_photo_without_facts_skipped() -> None:
    photo = make_photo(lower=3, upper=3).model_copy(update={"facts": None})
    findings = _run(make_quarter(QE, slabs=3, photos=[photo]))
    assert findings[0].status == "CANT_VERIFY"


def test_estimate_close_to_claim_is_a_question() -> None:
    photo = make_photo(lower=4, upper=4, tier="ESTIMATE")
    findings = _run(make_quarter(QE, slabs=5, photos=[photo]))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert "question to ask the builder" in findings[0].explanation


def test_estimate_flags_only_at_margin_three() -> None:
    quarter = make_quarter(QE, slabs=8, photos=[make_photo("p1", lower=5, upper=5, tier="ESTIMATE")])
    quarter.filing.blocks[0].ref = PageRef(doc_id="d", page=4)
    findings = _run(quarter)
    assert len(findings) == 1
    flag = findings[0]
    assert (flag.status, flag.severity) == ("FLAG", "high")
    assert flag.title == "Photo shows about 5 floors (estimate), report claims 8 slabs"
    assert flag.evidence[0].ref is not None and flag.evidence[0].ref.page == 4
    assert flag.inputs["gap_floors"] == "3"


def test_estimate_gap_two_is_not_a_flag() -> None:
    findings = _run(make_quarter(QE, slabs=7, photos=[make_photo(lower=5, upper=5, tier="ESTIMATE")]))
    assert findings[0].status == "CANT_VERIFY"
    assert "question to ask the builder" in findings[0].explanation


def test_estimate_above_claim_is_not_ok() -> None:
    findings = _run(make_quarter(QE, slabs=3, photos=[make_photo(lower=5, upper=5, tier="ESTIMATE")]))
    assert findings[0].status == "CANT_VERIFY"


def test_confident_decides_when_estimate_disagrees() -> None:
    photos = [
        make_photo("p1", lower=3, upper=3),
        make_photo("p2", lower=9, upper=9, tier="ESTIMATE"),
    ]
    findings = _run(make_quarter(QE, slabs=3, photos=photos))
    assert len(findings) == 1
    assert findings[0].status == "OK"


def test_disagree_two_estimate_flags_at_margin_three() -> None:
    findings = _run(make_quarter(QE, slabs=10, photos=[make_photo(lower=5, upper=7, tier="ESTIMATE")]))
    assert findings[0].status == "FLAG"
    assert findings[0].inputs["gap_floors"] == "3"


def test_percent_without_sanctioned_names_the_gap() -> None:
    """80% claimed but no sanctioned floors: the percent sentence, not the no-claim one."""
    findings = _run(make_quarter(QE, percent=80.0))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].explanation == (
        "The filing says 80% of the slabs are done, but it does not say how many "
        "floors the building has, so we can't turn that into a floor count."
    )


def test_no_claim_at_all_keeps_old_sentence() -> None:
    findings = _run(make_quarter(QE))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].title == "No slab claim for block MAIN"
    assert findings[0].explanation == "The filing claims no slab count or percent for block MAIN."


def test_activity_percent_without_block_claim_names_percent() -> None:
    """Work percent present but no block slab claim: never say 'no slab count or percent'."""
    quarter = make_quarter(QE, activities={"9 Slabs of Super Structure": 80.0})
    findings = _run(quarter)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"
    assert "no slab count or percent" not in findings[0].explanation.lower()
    assert "percent" in findings[0].explanation.lower()
    assert "floor count" in findings[0].explanation.lower()


def test_percent_with_sanctioned_still_converts() -> None:
    """80% of 19 sanctioned floors reads as 16 slabs (existing conversion)."""
    findings = _run(make_quarter(QE, percent=80.0, floors=19, photos=[make_photo(lower=16, upper=16)]))
    assert findings[0].status == "OK"


def test_block_named_after_project_shows_the_building() -> None:
    quarter = make_quarter(QE, block="ACME TOWER")
    quarter.filing.project_name = "ACME TOWER"
    findings = _run(quarter)
    assert findings[0].title == "No slab claim for the building"
    assert "for the building" in findings[0].explanation
    assert findings[0].block_label == "ACME TOWER"


def test_user_floors_percent_gives_claim_of_four() -> None:
    """80% with user floors 5 reads as 4 slabs, marked as user-entered."""
    quarter = make_quarter(QE, percent=80.0, photos=[make_photo(lower=4, upper=4)])
    apply_user_floors([quarter], 5)
    (finding,) = _run(quarter)
    assert finding.status == "OK"
    assert finding.inputs["claimed_slabs"] == "4"
    assert finding.inputs["floors_entered_by_you"] == "5"


def test_empty_floors_keeps_today_sentence() -> None:
    findings = _run(make_quarter(QE, percent=80.0))
    assert findings[0].status == "CANT_VERIFY"
    assert findings[0].explanation == (
        "The filing says 80% of the slabs are done, but it does not say "
        "how many floors the building has, so we can't turn that into a floor count."
    )


def test_user_floors_activity_percent_sentence() -> None:
    quarter = make_quarter(QE, activities={"RCC work": 80.0})
    apply_user_floors([quarter], 5)
    (finding,) = _run(quarter)
    assert finding.status == "CANT_VERIFY"
    assert finding.explanation == "The filing gives a percent of work done but not the building's floors."


def _photo_evidence_of(finding: Finding) -> list:
    return [e for e in finding.evidence if e.kind == "photo"]


def test_ok_finding_carries_counted_photo() -> None:
    photo = make_photo("p1", lower=3, upper=3)
    photo.overlay_path = "media/p1.overlay.png"
    (finding,) = _run(make_quarter(QE, slabs=3, photos=[photo]))
    assert finding.status == "OK"
    (photo_ev,) = _photo_evidence_of(finding)
    assert photo_ev.image_id == "p1"
    assert photo_ev.overlay_path == "media/p1.overlay.png"


def test_estimate_question_carries_counted_photo() -> None:
    photo = make_photo("p1", lower=4, upper=4, tier="ESTIMATE")
    photo.overlay_path = "media/p1.overlay.png"
    (finding,) = _run(make_quarter(QE, slabs=5, photos=[photo]))
    assert finding.status == "CANT_VERIFY"
    (photo_ev,) = _photo_evidence_of(finding)
    assert photo_ev.image_id == "p1"
    assert photo_ev.overlay_path == "media/p1.overlay.png"
