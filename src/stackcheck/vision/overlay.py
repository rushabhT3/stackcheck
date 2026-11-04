"""Draw a FloorCount on its photo: boxes, row lines, and a summary bar.

Reads one image, writes one PNG that is 28 px taller than the original.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from stackcheck.models import Detection, FloorCount

BAR_H = 28
THUMB_W = 240
_BAR_BG = "#101828"
_TEXT = "#FFFFFF"
_BUILDING = "#1D4ED8"
_ELEMENT = "#F59E0B"
_ROW = "#DC2626"


def _tag(draw: ImageDraw.ImageDraw, x: float, y: float, label: str) -> None:
    font = ImageFont.load_default()
    box = draw.textbbox((0, 0), label, font=font)
    pad = 2
    draw.rectangle(
        [x, y, x + (box[2] - box[0]) + pad * 2, y + (box[3] - box[1]) + pad * 2],
        fill=_ROW,
    )
    draw.text((x + pad, y + pad), label, font=font, fill=_TEXT)


def draw_floor_overlay(
    image_path: Path, count: FloorCount, detections: list[Detection], out_path: Path
) -> Path:
    """Render `count` over the photo at `image_path` and save PNG to `out_path`."""
    with Image.open(image_path) as src:
        base = src.convert("RGB")
    width, height = base.size
    canvas = Image.new("RGB", (width, height + BAR_H), _BAR_BG)
    canvas.paste(base, (0, BAR_H))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text(
        (8, 6),
        f"StackCheck  rows {count.sam_rows}  visual {count.vision_estimate}  {count.status}",
        font=font,
        fill=_TEXT,
    )
    if count.building_box is not None:
        box = count.building_box
        draw.rectangle(
            [box.x1, box.y1 + BAR_H, box.x2, box.y2 + BAR_H],
            outline=_BUILDING,
            width=3,
        )
        for det in detections:
            d = det.box
            draw.rectangle([d.x1, d.y1 + BAR_H, d.x2, d.y2 + BAR_H], outline=_ELEMENT, width=1)
        for rank, row_y in enumerate(sorted(count.rows_y, reverse=True), start=1):
            draw.line([box.x1, row_y + BAR_H, box.x2, row_y + BAR_H], fill=_ROW, width=2)
            _tag(draw, box.x1 + 4, row_y + BAR_H - 8, str(rank))
    canvas.save(out_path, format="PNG")
    return out_path


def write_overlay_thumb(overlay_path: Path, thumb_path: Path | None = None) -> Path:
    """Save a ~240 px wide JPEG next to the full overlay for list thumbnails."""
    dest = thumb_path or overlay_path.with_name(overlay_path.stem + ".thumb.jpg")
    with Image.open(overlay_path) as src:
        rgb = src.convert("RGB")
    width, height = rgb.size
    if width > THUMB_W:
        rgb = rgb.resize((THUMB_W, round(height * THUMB_W / width)), Image.LANCZOS)
    rgb.save(dest, format="JPEG", quality=70)
    return dest
