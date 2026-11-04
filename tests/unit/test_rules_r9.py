"""R9: the site video must show about as many floors as the filing claims."""

from datetime import date

from stackcheck.models import ProjectEvidence, VideoEvidence
from stackcheck.rules.checks import RuleSettings, apply_user_floors, r9_video_vs_claim
from tests.factories import make_project, make_quarter

QE = date(2025, 3, 31)
QE2 = date(2025, 6, 30)
SETTINGS = RuleSettings()


def _video(lower: int | None = 17, upper: int | None = 18, block: str = "MAIN") -> VideoEvidence:
    return VideoEvidence(video_id="vid1", block_label=block, tier="CONFIDENT", lower=lower, upper=upper)


def _run(project: ProjectEvidence):
    return r9_video_vs_claim(project, SETTINGS)


def test_no_video_no_findings() -> None:
    assert _run(make_project([make_quarter(QE, slabs=17)])) == []


def test_video_above_claim_is_ok() -> None:
    """One direction only: a video showing more than claimed is later progress, never a flag."""
    project = make_project([make_quarter(QE, slabs=10)])
    project.video = _video(12, 13)
    (finding,) = _run(project)
    assert finding.status == "OK"
    assert "consistent with later progress" in finding.explanation


def test_video_short_of_claim_is_a_question_not_a_flag() -> None:
    project = make_project([make_quarter(QE, slabs=10)])
    project.video = _video(7, 8)
    (finding,) = _run(project)
    assert (finding.status, finding.severity) == ("CANT_VERIFY", "question")
    assert "about 7 to 8 floors" in finding.explanation
    assert "quarter ending 2025-03-31" in finding.explanation
    assert "says 10 slabs were done" in finding.explanation
    assert "not proof of wrongdoing" in finding.explanation


def test_video_gap_table() -> None:
    """Gap of 2+ asks a question; overlap and higher video keep old branches."""
    gap = make_project([make_quarter(QE, slabs=10)])
    gap.video = _video(7, 8)
    (question,) = r9_video_vs_claim(gap, SETTINGS)
    assert (question.status, question.severity) == ("CANT_VERIFY", "question")
    assert question.inputs["gap_floors"] == "2"
    over = make_project([make_quarter(QE, slabs=10)])
    over.video = _video(11, 12)
    (ok,) = r9_video_vs_claim(over, SETTINGS)
    assert (ok.status, ok.severity) == ("OK", "info")


def test_video_overlapping_claim_cant_verify() -> None:
    project = make_project([make_quarter(QE, slabs=10)])
    project.video = _video(9, 10)
    (finding,) = _run(project)
    assert finding.status == "CANT_VERIFY"


def test_video_one_short_cant_verify() -> None:
    project = make_project([make_quarter(QE, slabs=10)])
    project.video = _video(8, 9)
    (finding,) = _run(project)
    assert finding.status == "CANT_VERIFY"


def test_video_uses_latest_claim() -> None:
    project = make_project([make_quarter(QE, slabs=4), make_quarter(QE2, slabs=10)])
    project.video = _video(7, 8)
    (finding,) = _run(project)
    assert (finding.status, finding.severity) == ("CANT_VERIFY", "question")
    assert finding.quarter_end == QE2


def test_video_percent_claim() -> None:
    """80% of 19 sanctioned floors reads as 16 slabs (R1 rule: percent of sanctioned + 1)."""
    project = make_project([make_quarter(QE, percent=80.0, floors=19)])
    project.video = _video(17, 18)
    (finding,) = _run(project)
    assert finding.status == "OK"


def test_video_no_claim_cant_verify() -> None:
    project = make_project([make_quarter(QE)])
    project.video = _video(7, 8)
    (finding,) = _run(project)
    assert finding.status == "CANT_VERIFY"
    assert "no slab count" in finding.explanation.lower()


def test_video_wrong_block_cant_verify() -> None:
    project = make_project([make_quarter(QE, slabs=10, block="A")])
    project.video = _video(7, 8, block="B")
    (finding,) = _run(project)
    assert finding.status == "CANT_VERIFY"


def test_video_no_range_cant_verify() -> None:
    project = make_project([make_quarter(QE, slabs=10)])
    project.video = VideoEvidence(video_id="vid1", block_label="MAIN")
    (finding,) = _run(project)
    assert finding.status == "CANT_VERIFY"


def test_video_no_counts_says_no_building() -> None:
    project = make_project([make_quarter(QE, slabs=10)])
    project.video = VideoEvidence(video_id="vid1", block_label="MAIN", frame_counts=[])
    (finding,) = _run(project)
    assert finding.status == "CANT_VERIFY"
    assert finding.explanation == "We could not find a building in the frames."


def test_video_disagreeing_counts_keep_old_sentence() -> None:
    project = make_project([make_quarter(QE, slabs=10)])
    project.video = VideoEvidence(video_id="vid1", block_label="MAIN", frame_counts=[6, 9])
    (finding,) = _run(project)
    assert finding.status == "CANT_VERIFY"
    assert "did not agree" in finding.explanation


def test_video_wording_has_no_accusation() -> None:
    project = make_project([make_quarter(QE, slabs=10)])
    project.video = _video(7, 8)
    (finding,) = _run(project)
    text = (finding.title + finding.explanation).lower()
    for word in ("fraud", "scam", "lying", "cheating"):
        assert word not in text


def test_user_floors_marks_video_claim() -> None:
    project = make_project([make_quarter(QE, percent=80.0)])
    apply_user_floors(project.quarters, 5)
    project.video = _video(4, 5)
    (finding,) = _run(project)
    assert finding.inputs["claimed_slabs"] == "4"
    assert finding.inputs["floors_entered_by_you"] == "5"
