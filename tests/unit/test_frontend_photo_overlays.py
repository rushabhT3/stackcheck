"""Counted R1 photos must reach the report: card alts, lists, quarters thumbnail."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "web" / "src"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def test_finding_card_alt_names_counted_rows() -> None:
    card = _read("components/FindingCard.svelte")
    assert "counted floor rows" in card


def test_cant_verify_list_renders_overlays() -> None:
    cant = _read("components/CantVerifyList.svelte")
    assert "OverlayThumb" in cant
    assert "checkId" in cant


def test_quarters_card_shows_elevation_overlay() -> None:
    report = _read("views/ReportView.svelte")
    assert "OverlayThumb" in report
    assert 'kind === "elevation"' in report


def test_photo_thumb_reserves_height() -> None:
    css = _read("app.css")
    rule = re.search(r"\.photo-thumb \{([^}]*)\}", css)
    assert rule is not None
    assert re.search(r"height:\s*\S+;", rule.group(1)) is not None
