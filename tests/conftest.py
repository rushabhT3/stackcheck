from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from stackcheck.config import Settings


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        meta_api_key="test",
        baseten_api_key="test",
        fireworks_api_key="test",
    )


def make_png(width: int, height: int, pattern: str) -> bytes:
    """Synthetic test image. Patterns: stripes, checker, gradient, building."""
    import io

    img = Image.new("RGB", (width, height), (200, 200, 200))
    draw = ImageDraw.Draw(img)
    if pattern == "stripes":
        for x in range(0, width, 20):
            draw.rectangle([x, 0, x + 9, height], fill=(0, 0, 0))
    elif pattern == "checker":
        for y in range(0, height, 20):
            for x in range(0, width, 20):
                if (x + y) // 20 % 2 == 0:
                    draw.rectangle([x, y, x + 19, y + 19], fill=(0, 0, 0))
    elif pattern == "gradient":
        for x in range(width):
            shade = round(255 * x / max(width - 1, 1))
            draw.line([(x, 0), (x, height)], fill=(shade, shade, shade))
    elif pattern == "building":
        rows = max(2, height // 60)
        for r in range(rows):
            for c in range(4):
                x0 = 10 + c * ((width - 20) // 4)
                y0 = 10 + r * ((height - 20) // rows)
                draw.rectangle(
                    [x0, y0, x0 + (width - 20) // 8, y0 + (height - 20) // (rows * 2)],
                    fill=(80, 80, 80),
                )
    else:
        raise ValueError(f"Unknown pattern: {pattern}")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_pdf(path: Path, pages: list[dict[str, Any]]) -> Path:
    """Build a synthetic PDF. Each dict may have "text" and "images" (PNG bytes)."""
    import pymupdf

    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page()
        if spec.get("text"):
            # Textbox with wrapping: insert_text at one point lets long text
            # run off the page, and extraction then sees almost nothing.
            box = pymupdf.Rect(72, 72, page.rect.width - 72, page.rect.height - 72)
            page.insert_textbox(box, spec["text"], fontsize=11)
        for i, png in enumerate(spec.get("images", [])):
            x0 = 50 + i * 260
            page.insert_image(pymupdf.Rect(x0, 100, x0 + 220, 320), stream=png)
    doc.save(path)
    doc.close()
    return path


def make_encrypted_pdf(path: Path) -> Path:
    """A PDF that needs a password to open."""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()
    return path
