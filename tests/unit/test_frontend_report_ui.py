"""Report-view UI fixes: restored images, collapsible sections, back arrow.

Static source assertions (repo convention, cf. test_frontend_recent.py): these
lock in markup that must survive future redesigns of ReportView/FindingCard.
"""

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web" / "src"


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_report_images_restored() -> None:
    """Thumbnails/overlays render through mediaUrl with alt text."""
    card = _read("components/FindingCard.svelte")
    assert "photo-thumb" in card
    assert "mediaUrl(checkId, thumb)" in card
    assert "mediaUrl(checkId, full)" in card
    assert "Elevation photo with ${rows} counted floor rows" in card
    assert "PhotoEvidence" in card

    photo = _read("components/PhotoEvidence.svelte")
    assert "photo-img" in photo
    assert "mediaUrl(checkId, srcName)" in photo
    assert "Elevation photo, quarter ending" in photo

    report = _read("views/ReportView.svelte")
    assert "photos={metas}" in report  # FindingCard gets the photo metadata
    assert "mediaUrl(report.check_id, video.contact_sheet)" in report
    assert "Contact sheet of the clearest video frames" in report


def test_report_sections_collapsible() -> None:
    """Flags/Confirmed/Quarters each collapse via an aria-expanded toggle."""
    report = _read("views/ReportView.svelte")
    assert "toggleSection" in report
    for key in ("flags", "confirmed", "quarters"):
        assert key in report
        assert f'aria-controls="{key}-body"' in report
        assert f'id="{key}-body"' in report
        assert f"collapsed.{key}" in report
    assert report.count("aria-expanded") >= 3
    assert "rp-toggle" in report
    assert "rp-arrow" in report


def test_back_arrow_home_navigation() -> None:
    """Report and progress views offer a home back arrow in every state."""
    report = _read("views/ReportView.svelte")
    assert report.count('class="back-link"') >= 2  # notice states + main view
    assert report.count('href="#/"') >= 2
    assert "← Back" in report
    assert "rp-loading-back" in report  # loading state keeps the way back

    progress = _read("views/ProgressView.svelte")
    assert 'class="back-link"' in progress
    assert 'href="#/"' in progress
    assert "← Back" in progress
