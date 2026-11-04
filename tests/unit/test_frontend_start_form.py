"""StartView form pins: PDF label wording and filing-scoped video confirmation.

The "same project and block" checkbox is required only when a filing
(PDF URLs or uploaded PDFs) is attached; photo-only/video-only submits
go through without it. All validation sentences stay unchanged.
"""

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web" / "src"


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_pdf_url_box_label() -> None:
    start = _read("views/StartView.svelte")
    assert '<label for="pdf-urls" class="sc-label sc-label-gap">PUBLIC PDF URLS</label>' in start
    assert "02 · Public PDF URLs, one per line" not in start


def test_video_confirm_required_only_with_filing() -> None:
    start = _read("views/StartView.svelte")
    assert "sources.length > 0 && !videoSameProject" in start
    assert "Confirm the video shows the same project and block as the filing." in start
    assert "video_same_project: videoSameProject" in start
    assert "video_same_project: true" not in start


def test_validation_sentences_unchanged() -> None:
    start = _read("views/StartView.svelte")
    assert "Enter a project name and at least one filing, photo or video." in start
    assert "Floors above ground must be a whole number of 1 or more." in start
    assert "You can add up to 5 site photos." in start
    assert "Site photos must be JPEG, PNG or WebP images." in start
    assert "This video shows the same project and block as the filing." in start
