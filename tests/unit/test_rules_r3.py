"""R3: photo date stamps must fall inside the quarter they are filed for."""

from datetime import date

from stackcheck.models import Finding, QuarterEvidence
from stackcheck.rules.checks import RuleSettings, r3_photo_date
from tests.factories import make_photo, make_project, make_quarter

QE = date(2025, 3, 31)
IN_WINDOW = date(2025, 2, 1)
OUTSIDE = date(2024, 6, 1)


def _run(quarter: QuarterEvidence, states: set[str] | None = None) -> list[Finding]:
    settings = RuleSettings(stamp_required_states=states or set())
    return r3_photo_date(make_project([quarter]), settings)


def test_no_photos() -> None:
    findings = _run(make_quarter(QE))
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"


def test_stamp_outside_window_flags() -> None:
    findings = _run(make_quarter(QE, photos=[make_photo(stamp=OUTSIDE)]))
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "medium")
    assert findings[0].title == "Photo dated 1 Jun 2024 filed for quarter ending 31 Mar 2025"


def test_bihar_mostly_unstamped_flags() -> None:
    photos = [make_photo(f"p{i}", stamp=None) for i in range(3)]
    photos.append(make_photo("p3", stamp=IN_WINDOW))
    findings = _run(make_quarter(QE, photos=photos), states={"Bihar"})
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "low")
    assert findings[0].title == "Most photos have no readable date stamp"


def test_non_bihar_unstamped_ok() -> None:
    photos = [make_photo(f"p{i}", stamp=None) for i in range(2)]
    findings = _run(make_quarter(QE, photos=photos, state="UP"), states={"Bihar"})
    assert [f.status for f in findings] == ["CANT_VERIFY"]
    assert findings[0].title == "No dated site photos to check"


def test_in_window_stamps_ok() -> None:
    photos = [make_photo(f"p{i}", stamp=IN_WINDOW) for i in range(2)]
    findings = _run(make_quarter(QE, photos=photos), states={"Bihar"})
    assert [f.status for f in findings] == ["OK"]


def test_bihar_half_unstamped_ok() -> None:
    photos = [make_photo("p0", stamp=None), make_photo("p1", stamp=IN_WINDOW)]
    findings = _run(make_quarter(QE, photos=photos), states={"Bihar"})
    assert [f.status for f in findings] == ["OK"]


def test_all_document_photos_cant_verify() -> None:
    """Seven document scans and no site photo: R3 must not say OK."""
    photos = [make_photo(f"p{i}", kind="document_or_logo", stamp=None) for i in range(7)]
    findings = _run(make_quarter(QE, photos=photos))
    assert [f.status for f in findings] == ["CANT_VERIFY"]
    assert findings[0].title == "No dated site photos to check"


def test_missing_stamp_flag_cites_photo_pages() -> None:
    """The most-photos-unstamped FLAG carries a page ref for every photo."""
    photos = [make_photo(f"p{i}", stamp=None) for i in range(3)]
    photos.append(make_photo("p3", stamp=IN_WINDOW))
    findings = _run(make_quarter(QE, photos=photos), states={"Bihar"})
    assert len(findings) == 1
    assert findings[0].status == "FLAG"
    photo_evidence = [e for e in findings[0].evidence if e.kind == "photo"]
    assert len(photo_evidence) == 3
    assert all(e.ref is not None and e.ref.page == 1 for e in photo_evidence)


def test_explanation_names_unusable_dates() -> None:
    """An impossible stamp is counted missing and named in the explanation."""
    photos = [make_photo("p0", stamp=IN_WINDOW), make_photo("p1", stamp=date(2025, 12, 31))]
    findings = _run(make_quarter(QE, photos=photos, filed_on=date(2025, 4, 13), state="UP"))
    assert [f.status for f in findings] == ["OK"]
    assert "could not use" in findings[0].explanation
    assert "p1" in findings[0].inputs.get("unusable_stamp_dates", "")


def test_no_quarter_end() -> None:
    findings = _run(make_quarter(None, photos=[make_photo(stamp=IN_WINDOW)]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_impossible_date_resolves_day_month_swap() -> None:
    """4 Jun 2025 in a filing made 13 Apr 2025 reads as 6 Apr, and is not flagged."""
    photos = [make_photo(stamp=date(2025, 6, 4))]
    quarter = make_quarter(QE, photos=photos, filed_on=date(2025, 4, 13))
    findings = _run(quarter)
    assert [f.status for f in findings] == ["OK"]
    assert "2025-04-06" in findings[0].inputs.get("resolved_stamp_dates", "")
    facts = quarter.photos[0].facts
    assert facts is not None
    assert facts.stamp_date_resolved == date(2025, 4, 6)
    assert facts.stamp_date_status == "swapped_low_confidence"


def test_impossible_date_marks_status() -> None:
    """A post-filing stamp whose swap is real but out of window is impossible."""
    photos = [make_photo(stamp=date(2026, 5, 4))]
    quarter = make_quarter(QE, photos=photos, filed_on=date(2025, 4, 13), state="UP")
    _run(quarter)
    facts = quarter.photos[0].facts
    assert facts is not None
    assert facts.stamp_date_resolved is None
    assert facts.stamp_date_status == "impossible"


def test_ok_stamp_marks_status() -> None:
    photos = [make_photo(stamp=IN_WINDOW)]
    quarter = make_quarter(QE, photos=photos)
    _run(quarter)
    facts = quarter.photos[0].facts
    assert facts is not None
    assert facts.stamp_date_resolved == IN_WINDOW
    assert facts.stamp_date_status == "ok"


def test_impossible_date_with_no_valid_swap_is_unreadable() -> None:
    """A stamp after filing with no in-window swap counts as unreadable, not a flag."""
    photos = [make_photo(stamp=date(2025, 12, 31))]
    quarter = make_quarter(QE, photos=photos, filed_on=date(2025, 4, 13), state="UP")
    findings = _run(quarter)
    assert [f.status for f in findings] == ["CANT_VERIFY"]
    assert "could not use" in findings[0].explanation
    facts = quarter.photos[0].facts
    assert facts is not None
    assert facts.stamp_date_resolved is None
    assert facts.stamp_date_status == "unreadable"


def test_photo_two_quarters_early_flags() -> None:
    """A stamp well before the quarter, filed as current progress, is the real signal."""
    photos = [make_photo(stamp=date(2024, 6, 15))]
    findings = _run(make_quarter(QE, photos=photos, filed_on=date(2025, 4, 15)))
    assert len(findings) == 1
    assert (findings[0].status, findings[0].severity) == ("FLAG", "medium")


def test_photo_between_quarter_end_and_filing_is_ok() -> None:
    """Photos taken for the filing, after quarter end but before it was filed, are normal."""
    photos = [make_photo(stamp=date(2025, 4, 10))]
    findings = _run(make_quarter(QE, photos=photos, filed_on=date(2025, 4, 15)))
    assert [f.status for f in findings] == ["OK"]
