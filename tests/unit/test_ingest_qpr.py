"""QPR web reports (rera.bihar.gov.in/QPR/QPR-Show.aspx): routing, parsing, photos."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import respx

from stackcheck.ingest.qpr import (
    apply_qpr_slot,
    is_qpr_url,
    parse_money,
    parse_qpr_html,
    parse_qpr_slot,
    parse_upload_time,
    qpr_quarter,
    slot_kind,
)
from stackcheck.ingest.sources import fetch_pdf
from stackcheck.models import PhotoFacts, SourceDoc
from stackcheck.rules.checks import RuleSettings, r1_photo_vs_claim
from tests.factories import make_photo, make_project, make_quarter

QPR_URL = (
    "https://rera.bihar.gov.in/QPR/QPR-Show.aspx"
    "?pr=RERAP08022024125722&ld=pan&pj=RERAP08022024125722-1&f=2025-2026&q=1"
)
FIXTURE = Path(__file__).with_name("fixtures") / "qpr_apogee.html"


def test_qpr_url_routing() -> None:
    assert is_qpr_url(QPR_URL) is True
    assert is_qpr_url("http://rera.bihar.gov.in/QPR/QPR-Show.aspx?pr=x&ld=pan&pj=y&f=2025-2026&q=1") is False
    assert is_qpr_url("https://rera.bihar.gov.in/QPR/Other.aspx?pr=x&ld=pan&pj=y&f=2025-2026&q=1") is False
    assert is_qpr_url("https://example.com/QPR/QPR-Show.aspx?pr=x&ld=pan&pj=y&f=2025-2026&q=1") is False
    assert is_qpr_url("https://rera.bihar.gov.in/QPR/QPR-Show.aspx?pr=x&f=2025-2026") is False
    assert is_qpr_url("https://rera.bihar.gov.in/Progress_Doc/RERAP08022024125722-1.pdf") is False


def test_qpr_quarter() -> None:
    assert qpr_quarter("2025-2026", "1") == (date(2025, 6, 30), date(2025, 4, 1))
    assert qpr_quarter("2025-2026", "2") == (date(2025, 9, 30), date(2025, 7, 1))
    assert qpr_quarter("2025-2026", "3") == (date(2025, 12, 31), date(2025, 10, 1))
    assert qpr_quarter("2025-2026", "4") == (date(2026, 3, 31), date(2026, 1, 1))
    assert qpr_quarter("2025-2026", "5") is None
    assert qpr_quarter("2025", "1") is None


def test_parse_upload_time() -> None:
    assert parse_upload_time("FrontElevation_010725143022.jpeg") == date(2025, 7, 1)
    assert parse_upload_time("fuDisplayBoard_311225000000.jpeg") == date(2025, 12, 31)
    assert parse_upload_time("FrontElevation_nodate.jpeg") is None
    assert parse_upload_time("FrontElevation_991399000000.jpeg") is None


def test_slot_kind() -> None:
    assert slot_kind("FrontElevation") == "elevation"
    assert slot_kind("fuInteriorWorks") == "site_other"
    assert slot_kind("fuCommonAreas") == "site_other"
    assert slot_kind("fuDisplayBoard") == "site_other"
    assert slot_kind("SomethingElse") is None


def test_parse_money() -> None:
    assert parse_money("Rs. 5,00,000") == Decimal("500000")
    assert parse_money("17,78,000=00") == Decimal("1778000")
    assert parse_money("No Collection") == Decimal("0")
    assert parse_money("NIL") == Decimal("0")
    assert parse_money("-") is None
    assert parse_money("") is None


def test_parse_fixture() -> None:
    page = parse_qpr_html(FIXTURE.read_text(encoding="utf-8"), QPR_URL)
    assert page.project_name == "APOGEE TOWER"
    assert page.project_reg_no == "RERAP08022024125722"
    assert page.quarter_end == date(2025, 6, 30)
    assert page.quarter_start == date(2025, 4, 1)
    assert page.sanctioned_floors == 4
    assert page.slabs_percent == 80
    assert ("2 Slabs of Super Structure", 80) in [(a.name, a.percent) for a in page.activities]
    assert page.money["cumulative_received"] == Decimal("4500000")
    assert page.money["cumulative_spent"] == Decimal("4200000")
    assert page.money["estimated_total_cost"] == Decimal("38561456")
    assert page.booked_units == 12
    assert [photo.slot for photo in page.photos] == [
        "FrontElevation",
        "fuInteriorWorks",
        "fuCommonAreas",
        "fuDisplayBoard",
    ]
    assert page.photos[0].uploaded_on == date(2025, 7, 1)
    assert page.photos[0].url.startswith("https://rera.bihar.gov.in/QPRimages/")


def test_parse_fixture_reg_falls_back_to_param() -> None:
    html = FIXTURE.read_text(encoding="utf-8").replace("RERAP08022024125722", "BRERAP92324.I 1963")
    page = parse_qpr_html(html, QPR_URL)
    assert page.project_reg_no == "RERAP08022024125722"


def test_apply_qpr_slot_overrides_kind_and_clears_stamp() -> None:
    facts = PhotoFacts(
        image_id="x",
        kind="document_or_logo",
        stamp_text="stamp",
        stamp_date=date(2025, 7, 1),
        stamp_date_resolved=date(2025, 7, 1),
        stamp_date_status="ok",
        vision_storeys_visible=4,
        confidence="high",
        reason="r",
    )
    fixed = apply_qpr_slot(facts, "FrontElevation")
    assert fixed.kind == "elevation"
    assert fixed.stamp_text is None
    assert fixed.stamp_date is None
    assert fixed.stamp_date_resolved is None
    assert fixed.stamp_date_status == "unreadable"
    assert fixed.vision_storeys_visible == 4
    assert fixed.confidence == "high"


def test_apply_qpr_slot_unknown_keeps_model_kind() -> None:
    facts = PhotoFacts(
        image_id="x",
        kind="elevation",
        vision_storeys_visible=3,
        confidence="medium",
        reason="r",
    )
    fixed = apply_qpr_slot(facts, "SomethingElse")
    assert fixed.kind == "elevation"
    assert fixed.stamp_date_status == "unreadable"


def test_parse_qpr_slot_from_image_id() -> None:
    assert parse_qpr_slot("abc123-qpr0-FrontElevation") == "FrontElevation"
    assert parse_qpr_slot("abc123-p1-i0") is None


def _write_qpr_sources(tmp_path: Path, doc_id: str, missing: set[int] | None = None) -> Path:
    from io import BytesIO

    from PIL import Image

    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / f"{doc_id}.html").write_bytes(FIXTURE.read_bytes())
    for index in range(4):
        if missing and index in missing:
            continue
        buf = BytesIO()
        Image.new("RGB", (720, 1280), (10 * index, 20, 30)).save(buf, format="JPEG")
        (sources / f"{doc_id}-qpr{index}.jpg").write_bytes(buf.getvalue())
    return sources / f"{doc_id}.html"


def _qpr_source(doc_id: str) -> SourceDoc:
    return SourceDoc(
        doc_id=doc_id,
        origin="url",
        url=QPR_URL,
        filename="QPR-Show.aspx",
        sha256="0" * 64,
        retrieved_at="2025-07-02T00:00:00+00:00",
        page_count=1,
    )


def test_ingest_qpr_builds_doc_and_filing(tmp_path: Path) -> None:
    from stackcheck.ingest.qpr import ingest_qpr

    doc_id = "a" * 16
    html_path = _write_qpr_sources(tmp_path, doc_id)
    source = _qpr_source(doc_id)
    doc, filing = ingest_qpr(html_path, source, tmp_path)
    assert doc.is_scanned is False
    assert len(doc.images) == 4
    assert doc.images[0].image_id == f"{doc_id}-qpr0-FrontElevation"
    assert (tmp_path / "images" / f"{doc_id}-qpr0-FrontElevation.png").is_file()
    assert filing.doc_id == doc_id
    assert filing.quarter_end == date(2025, 6, 30)
    assert filing.blocks[0].slabs_claimed_percent == 80
    assert any("uploaded on 01-Jul-2025" in note for note in filing.notes)


def test_ingest_qpr_missing_photo_skips_with_note(tmp_path: Path) -> None:
    from stackcheck.ingest.qpr import ingest_qpr

    doc_id = "b" * 16
    html_path = _write_qpr_sources(tmp_path, doc_id, missing={2})
    source = _qpr_source(doc_id)
    assert isinstance(source, SourceDoc)
    doc, filing = ingest_qpr(html_path, source, tmp_path)
    assert len(doc.images) == 3
    assert any("1 site photo(s) could not be downloaded" in note for note in filing.notes)


def test_r1_percent_claim_flag_needs_gap_of_two() -> None:
    settings = RuleSettings()
    flagged = r1_photo_vs_claim(
        make_project(
            [make_quarter(date(2025, 6, 30), percent=80, floors=4, photos=[make_photo(lower=2, upper=2)])]
        ),
        settings,
    )
    assert flagged[0].status == "FLAG"
    # A confident count one below the claim stays covered by R1's standing
    # within-one rule; the brief's boundary (no FLAG unless the gap is 2+)
    # holds either way.
    covered = r1_photo_vs_claim(
        make_project(
            [make_quarter(date(2025, 6, 30), percent=80, floors=4, photos=[make_photo(lower=3, upper=3)])]
        ),
        settings,
    )
    assert covered[0].status == "OK"
    overlap = r1_photo_vs_claim(
        make_project(
            [make_quarter(date(2025, 6, 30), percent=80, floors=4, photos=[make_photo(lower=2, upper=5)])]
        ),
        settings,
    )
    assert overlap[0].status == "CANT_VERIFY"
    assert "overlap" in overlap[0].explanation.lower()
    no_sanctioned = r1_photo_vs_claim(
        make_project([make_quarter(date(2025, 6, 30), percent=80, photos=[make_photo(lower=3, upper=3)])]),
        settings,
    )
    assert no_sanctioned[0].status == "CANT_VERIFY"
    assert no_sanctioned[0].explanation == (
        "The filing says 80% of the slabs are done, but it does not say how many "
        "floors the building has, so we can't turn that into a floor count."
    )


@respx.mock
async def test_fetch_qpr_page_and_images(tmp_path: Path) -> None:
    import httpx

    from stackcheck.config import Settings

    html = FIXTURE.read_bytes()
    respx.get(QPR_URL).mock(return_value=httpx.Response(200, content=html))
    for name in (
        "FrontElevation_010725143022",
        "fuInteriorWorks_010725143105",
        "fuCommonAreas_010725143210",
        "fuDisplayBoard_010725143255",
    ):
        respx.get(f"https://rera.bihar.gov.in/QPRimages/{name}.jpeg").mock(
            return_value=httpx.Response(200, content=b"\xff\xd8fakejpeg")
        )
    settings = Settings(
        data_dir=tmp_path,
        allowed_hosts="rera.bihar.gov.in",
        openai_api_key="k",
        openai_base_url="https://x",
        extract_model="m",
        vision_model="v",
    )
    source = await fetch_pdf(QPR_URL, settings, tmp_path)
    assert source.url == QPR_URL
    assert source.page_count == 1
    assert (tmp_path / "sources" / f"{source.doc_id}.html").read_bytes() == html
    assert (tmp_path / "sources" / f"{source.doc_id}-qpr0.jpg").read_bytes() == b"\xff\xd8fakejpeg"
