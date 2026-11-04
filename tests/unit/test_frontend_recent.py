"""Part A/B frontend rules: recent checks stay on-device, links and wording match spec."""

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web" / "src"


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_recent_checks_are_on_device_only() -> None:
    recent = _read("lib/recent.ts")
    assert "localStorage" in recent
    assert "try" in recent and "catch" in recent
    assert "100" in recent  # last-100 cap
    assert "MAX = 100" in recent
    assert "/api/" not in recent


def _flat(rel: str) -> str:
    return " ".join(_read(rel).split())


def test_recent_list_and_copy_link_wiring() -> None:
    start = _read("views/StartView.svelte")
    assert "Your recent checks on this device" in start
    assert "Clear list" in start
    assert "It stays on this device" not in start
    assert "It is saved with the report on our server" in start
    copy = _flat("components/CopyLink.svelte")
    assert "Copy link" in copy
    assert "Recent checks also appear on the home page of this browser" in copy
    assert "Link copied" in copy
    for rel in ("views/ProgressView.svelte", "views/ReportView.svelte"):
        assert "CopyLink" in _read(rel)


def test_recent_list_paginates_five_per_page() -> None:
    start = _read("views/StartView.svelte")
    assert "RECENT_PAGE_SIZE = 5" in start
    assert "recentVisible" in start
    assert "recentPage * RECENT_PAGE_SIZE" in start
    assert "Previous" in start
    assert "Next" in start
    assert "Page {recentPage + 1} of {recentPageCount}" in start
    assert "Your recent checks on this device" in start
    assert "Clear list" in start


def test_recent_list_states_browser_only_storage() -> None:
    start = _read("views/StartView.svelte")
    assert "Saved in this browser only. Report links work on any device." in start


def test_sample_empty_state_and_report_link() -> None:
    start = _read("views/StartView.svelte")
    assert "No sample reports yet." not in start
    assert "Try a finished sample" in start
    assert "#/report/${s.check_id}" in start
    assert 'maxlength="80"' in start


def test_recent_failed_word_tag_style_submit_and_thumb() -> None:
    start = _flat("views/StartView.svelte")
    assert "Failed" in start
    assert "setInterval" in start and "5000" in start
    assert "Starting" in start
    assert "tag" in start  # status word reuses the status-tag style
    css = _read("app.css")
    assert "7.5rem" in css  # overlay thumbnail about 120 px wide
    assert "object-fit: cover" in css


def test_chart_encoding_and_chrome_polish() -> None:
    money = _read("components/MoneyChart.svelte").lower()
    assert "polygon" in money  # triangle markers, not circles-only
    assert "area-progress" in money


def test_report_hides_empty_trace_and_collapses_notes() -> None:
    """No 'Investigator trace (0 steps)'; the count line renders expandable."""
    report = _read("views/ReportView.svelte")
    assert "report.trace.length > 0" in report
    assert "could not be matched" in report
    assert "<details>" in report
    assert "Block Block" not in report
    assert "replace(/^\\s*block" in report


def test_accuracy_reviewer_line_has_separator() -> None:
    """Reviewer note and date never run together."""
    page = _read("views/AccuracyView.svelte")
    assert '{" "}' in page


def test_money_line_uses_converted_crore() -> None:
    """Money line converts lakh/crore units and prints Rs X.X crore."""
    fmt = _read("lib/format.ts")
    assert "formatCrore" in fmt
    assert "100_000" in fmt or "100000" in fmt
    assert "10_000_000" in fmt or "10000000" in fmt
    report = _read("views/ReportView.svelte")
    assert "formatCrore" in report
    assert "unit_overrides" in report
    assert "of Rs 9,933" not in report and "Rs 9,933" not in report
    timeline = _read("components/QuarterTimeline.svelte").lower()
    assert "nodata" in timeline
    assert "value-label" in timeline
    css = _read("app.css").lower()
    assert "#2563eb" in css  # money and ranges are blue, not a third green
    assert "#d97706" in css  # no-data is amber, never green
    assert "inset 0 1px 0 #7fd08a" in css  # verdict mint glow
    assert "outline: 2px solid #0f7a45" in css  # focus with contrast
    assert "#6b7280" in css  # placeholders and chart labels
    assert "stroke-dasharray" in css  # dotted money line
    assert "dropzone-input" in css  # hidden native file input
    assert "skeleton" in css  # loading state after Run check


def test_plain_errors_and_way_back() -> None:
    api = _read("lib/api.ts")
    assert "userMessage" in api
    assert "Something went wrong on our side" in api
    for rel in ("views/StartView.svelte", "views/ProgressView.svelte", "views/ReportView.svelte"):
        assert "${e.code}" not in _read(rel)
    for rel in ("views/ProgressView.svelte", "views/ReportView.svelte"):
        assert "Start a new check" in _read(rel)
    report = _read("views/ReportView.svelte")
    assert "Report not found" in report
    assert "This check didn't finish" in report
    assert "Check didn't finish | StackCheck" in report
    progress = _read("views/ProgressView.svelte")
    assert "We can't find that check" in progress
    assert "Check not found | StackCheck" in progress
    app = _read("App.svelte")
    assert "Skip to content" in app
    assert 'id="main"' in app
    copy = _flat("components/CopyLink.svelte")
    assert "Recent checks also appear on the home page of this browser" in copy


def test_phone_cards_hints_and_titles() -> None:
    assert "scroll-hint" in _read("app.css")
    for rel in ("components/QuarterTimeline.svelte", "components/MoneyChart.svelte"):
        assert "scroll-hint" in _read(rel)
    start = _read("views/StartView.svelte")
    assert "recent-link" in start
    assert "link-btn" in start
    assert "Verified" not in start
    for rel in ("views/ProgressView.svelte", "views/ReportView.svelte"):
        assert "| StackCheck" in _read(rel)


def test_phone_header_stays_compact() -> None:
    import re

    css = _read("app.css")
    assert re.search(r"\.brand\s*\{[^}]*line-height", css), "brand needs a tight line-height"


def test_quarter_card_and_banner_wording() -> None:
    report = _flat("views/ReportView.svelte")
    assert "No slab count claimed" in report
    assert "Floors sanctioned: not reported" in report
    assert "Money spent: not reported (estimated project cost" in report
    banner = _read("components/VerdictBanner.svelte")
    assert "is-flag" in banner and "is-ok" in banner
    css = _read("app.css")
    assert ".banner h1" in css
    assert ".banner.is-flag" in css
    assert ".banner.is-ok" in css
