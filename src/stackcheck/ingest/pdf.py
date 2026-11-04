"""QPR PDF to page texts and extracted photos. CPU work, no I/O beyond files."""

import hashlib
import io
import logging
from pathlib import Path

import pymupdf
from PIL import Image

from stackcheck.errors import IngestError
from stackcheck.models import ExtractedImage, IngestedDoc, PageText, SourceDoc

logger = logging.getLogger(__name__)

MIN_SIDE_PX = 200
SCANNED_TEXT_CHARS = 200


def _to_rgb_png(raw: bytes) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(raw)) as img:
        rgb = img.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, format="PNG")
        return buf.getvalue(), rgb.width, rgb.height


def render_page(pdf_path: Path, page: int, dpi: int = 110) -> bytes:
    """Render one 1-based page to PNG bytes (for scanned PDFs)."""
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        raise IngestError(f"Cannot open {pdf_path}: {exc}") from exc
    try:
        if page < 1 or page > doc.page_count:
            raise IngestError(f"Page {page} out of range in {pdf_path}")
        return doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


def ingest_pdf(pdf_path: Path, source: SourceDoc, out_dir: Path) -> IngestedDoc:
    """Extract texts and photos. Raises IngestError for encrypted or unreadable PDFs."""
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        raise IngestError(f"Cannot open {pdf_path}: {exc}") from exc
    if doc.is_encrypted or doc.needs_pass:
        doc.close()
        raise IngestError(f"Encrypted PDF needs a password: {pdf_path}")
    try:
        pages = [
            PageText(doc_id=source.doc_id, page=n + 1, text=page.get_text("text"))
            for n, page in enumerate(doc)
        ]
        is_scanned = sum(len(p.text) for p in pages) < SCANNED_TEXT_CHARS
        images_dir = out_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        images: list[ExtractedImage] = []
        for n, page in enumerate(doc):
            index = 0
            for xref, *_ in page.get_images(full=True):
                if xref == 0:
                    continue
                pix = pymupdf.Pixmap(doc, xref)
                if pix.n - pix.alpha > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if pix.width < MIN_SIDE_PX or pix.height < MIN_SIDE_PX:
                    continue
                png, width, height = _to_rgb_png(pix.tobytes("png"))
                image_id = f"{source.doc_id}-p{n + 1}-i{index}"
                (images_dir / f"{image_id}.png").write_bytes(png)
                images.append(
                    ExtractedImage(
                        image_id=image_id,
                        doc_id=source.doc_id,
                        page=n + 1,
                        index=index,
                        path=f"images/{image_id}.png",
                        width=width,
                        height=height,
                        sha256=hashlib.sha256(png).hexdigest(),
                    )
                )
                index += 1
        if is_scanned and not images:
            for n in range(doc.page_count):
                png = render_page(pdf_path, n + 1)
                _, width, height = _to_rgb_png(png)
                image_id = f"{source.doc_id}-p{n + 1}-i0"
                (images_dir / f"{image_id}.png").write_bytes(png)
                images.append(
                    ExtractedImage(
                        image_id=image_id,
                        doc_id=source.doc_id,
                        page=n + 1,
                        index=0,
                        path=f"images/{image_id}.png",
                        width=width,
                        height=height,
                        sha256=hashlib.sha256(png).hexdigest(),
                        from_page_render=True,
                    )
                )
        return IngestedDoc(source=source, pages=pages, images=images, is_scanned=is_scanned)
    finally:
        doc.close()
