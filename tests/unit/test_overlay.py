"""Unit tests for vision/overlay.py. Uses synthetic PNGs, no network."""

from pathlib import Path

from PIL import Image

from stackcheck.models import Box, Detection, FloorCount
from stackcheck.vision.overlay import draw_floor_overlay, write_overlay_thumb


def _photo(path: Path, w: int = 200, h: int = 150) -> Path:
    photo = path / "photo.png"
    Image.new("RGB", (w, h), (180, 30, 30)).save(photo, format="PNG")
    return photo


def _detection(x1: float, y1: float, x2: float, y2: float) -> Detection:
    return Detection(
        prompt="window",
        frame=0,
        object_id=0,
        box=Box(x1=x1, y1=y1, x2=x2, y2=y2),
        confidence=0.9,
        image_w=200,
        image_h=150,
    )


def test_overlay_size_and_format(tmp_path: Path) -> None:
    photo = _photo(tmp_path)
    count = FloorCount(
        image_id="t",
        status="OK",
        lower=3,
        upper=3,
        sam_rows=3,
        vision_estimate=3,
        rows_y=[40.0, 80.0, 120.0],
        building_box=Box(x1=10, y1=10, x2=190, y2=140),
        reason="test",
    )
    out = draw_floor_overlay(photo, count, [_detection(20, 30, 50, 50)], tmp_path / "overlay.png")
    assert out.exists()
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.size == (200, 150 + 28)


def test_overlay_thumb_is_small_jpeg_next_to_overlay(tmp_path: Path) -> None:
    photo = _photo(tmp_path, 480, 320)
    count = FloorCount(
        image_id="t",
        status="OK",
        lower=3,
        upper=3,
        sam_rows=3,
        vision_estimate=3,
        rows_y=[40.0, 80.0, 120.0],
        building_box=Box(x1=10, y1=10, x2=190, y2=140),
        reason="test",
    )
    overlay = draw_floor_overlay(photo, count, [_detection(20, 30, 50, 50)], tmp_path / "t.overlay.png")
    thumb = write_overlay_thumb(overlay)
    assert thumb.parent == overlay.parent
    assert thumb.name == "t.overlay.thumb.jpg"
    with Image.open(thumb) as img:
        assert img.format == "JPEG"
        assert img.size[0] == 240


def test_overlay_single_row_result_does_not_crash(tmp_path: Path) -> None:
    photo = _photo(tmp_path)
    count = FloorCount(
        image_id="t",
        status="CANT_VERIFY",
        sam_rows=1,
        rows_y=[75.0],
        building_box=Box(x1=10, y1=10, x2=190, y2=140),
        reason="only one method produced a count (sam = 1)",
    )
    out = draw_floor_overlay(photo, count, [], tmp_path / "overlay.png")
    assert out.exists()
    with Image.open(out) as img:
        assert img.size == (200, 150 + 28)
