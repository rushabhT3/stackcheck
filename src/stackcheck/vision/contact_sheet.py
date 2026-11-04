"""A contact sheet of the best video frames with counted rows drawn."""

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from stackcheck.models import FloorCount
from stackcheck.vision.overlay import draw_floor_overlay


def build_contact_sheet(
    frames: list[tuple[Path, FloorCount]], out_path: Path, caption: str | None = None
) -> Path:
    """Tile up to 4 frames side by side, each with its floor overlay.

    Each input frame is rendered through draw_floor_overlay (no detections: the
    counted rows come from the FloorCount), then the tiles are joined with a
    small gap. Pure image work; no I/O beyond reading and writing.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not frames:
        raise ValueError("No frames to tile.")
    tiles: list[Image.Image] = []
    with tempfile.TemporaryDirectory(prefix="sheet_") as tmp:
        for index, (path, count) in enumerate(frames[:4]):
            tile_path = Path(tmp) / f"tile{index}.png"
            draw_floor_overlay(path, count, [], tile_path)
            with Image.open(tile_path) as tile:
                tiles.append(tile.convert("RGB"))
    gap = 8
    width = sum(tile.width for tile in tiles) + gap * (len(tiles) - 1)
    height = max(tile.height for tile in tiles)
    strip = 20 if caption else 0
    sheet = Image.new("RGB", (width, height + strip), (255, 255, 255))
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0))
        x += tile.width + gap
    if caption:
        draw = ImageDraw.Draw(sheet)
        draw.text((8, height + 4), caption[:160], fill=(15, 39, 26))
    sheet.save(out_path, format="PNG")
    for tile in tiles:
        tile.close()
    return out_path
