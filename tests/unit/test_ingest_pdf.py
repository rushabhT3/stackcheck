from pathlib import Path

import pytest

from stackcheck.errors import IngestError
from stackcheck.ingest.pdf import ingest_pdf
from stackcheck.models import SourceDoc
from tests.conftest import make_encrypted_pdf, make_pdf, make_png


def _source(doc_id: str = "d" * 16) -> SourceDoc:
    from datetime import UTC, datetime

    return SourceDoc(
        doc_id=doc_id,
        origin="upload",
        filename="q.pdf",
        sha256="s",
        retrieved_at=datetime.now(UTC),
        page_count=3,
    )


def test_three_pages_of_text(tmp_path: Path) -> None:
    pdf = make_pdf(
        tmp_path / "q.pdf",
        [{"text": "alpha " * 50}, {"text": "beta " * 50}, {"text": "gamma " * 50}],
    )
    ingested = ingest_pdf(pdf, _source(), tmp_path)
    assert [p.page for p in ingested.pages] == [1, 2, 3]
    assert "beta" in ingested.pages[1].text
    assert ingested.images == []
    assert ingested.is_scanned is False


def test_image_on_page_two(tmp_path: Path) -> None:
    png = make_png(400, 300, "stripes")
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": "t"}, {"text": "t", "images": [png]}, {"text": "t"}])
    ingested = ingest_pdf(pdf, _source(), tmp_path)
    (image,) = ingested.images
    assert (image.page, image.index) == (2, 0)
    assert image.image_id == ingested.source.doc_id + "-p2-i0"
    saved = tmp_path / image.path
    assert saved.exists()
    assert (image.width, image.height) == (400, 300)


def test_small_image_skipped(tmp_path: Path) -> None:
    png = make_png(100, 100, "checker")
    text = "\n".join(f"line {i} of the report" for i in range(30))
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": text, "images": [png]}])
    assert ingest_pdf(pdf, _source(), tmp_path).images == []


def test_two_images_get_indexes_zero_and_one(tmp_path: Path) -> None:
    pdf = make_pdf(
        tmp_path / "q.pdf",
        [{"text": "t", "images": [make_png(400, 300, "stripes"), make_png(500, 400, "gradient")]}],
    )
    images = ingest_pdf(pdf, _source(), tmp_path).images
    assert [i.index for i in images] == [0, 1]


def test_scanned_pdf_with_image_uses_extraction(tmp_path: Path) -> None:
    png = make_png(800, 1000, "building")
    pdf = make_pdf(tmp_path / "q.pdf", [{"images": [png]}])
    ingested = ingest_pdf(pdf, _source(), tmp_path)
    assert ingested.is_scanned is True
    (image,) = ingested.images
    assert image.from_page_render is False


def test_empty_pdf_renders_every_page(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "q.pdf", [{}, {}])
    ingested = ingest_pdf(pdf, _source(), tmp_path)
    assert ingested.is_scanned is True
    assert len(ingested.images) == 2
    assert all(i.from_page_render for i in ingested.images)
    assert [i.page for i in ingested.images] == [1, 2]


def test_encrypted_pdf_raises(tmp_path: Path) -> None:
    pdf = make_encrypted_pdf(tmp_path / "q.pdf")
    with pytest.raises(IngestError):
        ingest_pdf(pdf, _source(), tmp_path)
