"""Unit tests for vision/frames.py and contact sheets. No ffmpeg needed."""

import asyncio
import io
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stackcheck.models import Box, FloorCount
from stackcheck.vision.frames import (
    VideoError,
    consensus,
    laplacian_variance,
    sample_indices,
    sample_sharp_frames,
)


def _png(pattern: str, size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("L", size, 128)
    draw = ImageDraw.Draw(img)
    for x in range(0, size[0], 8):
        draw.line([x, 0, x, size[1]], fill=255 if pattern == "stripes" else 128)
    if pattern == "blurred":
        img = img.filter(ImageFilter.GaussianBlur(4))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def test_sample_sharp_frames_missing_work_dir_raises_video_error(tmp_path: Path) -> None:
    """A not-yet-created work dir must not escape as FileNotFoundError."""
    with pytest.raises(VideoError):
        await sample_sharp_frames(tmp_path / "clip.mp4", tmp_path / "video_frames", duration_s=10.0)


async def test_sample_sharp_frames_keep_frames_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returned frame paths must still exist after sampling (ffmpeg stubbed)."""
    from stackcheck.vision import frames as frames_mod

    async def _fake_extract(path: Path, times: Sequence[float], out_dir: Path) -> list[Path]:
        await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
        saved = []
        for i in range(len(times)):
            png = out_dir / f"frame_{i:02d}.png"
            png.write_bytes(_png("stripes"))
            saved.append(png)
        return saved

    monkeypatch.setattr(frames_mod, "extract_frames", _fake_extract)
    frames = await sample_sharp_frames(tmp_path / "clip.mp4", tmp_path / "video_frames", duration_s=10.0)
    assert len(frames) == 8
    for frame_path, _ in frames:
        assert frame_path.is_file()


def test_sample_indices_even() -> None:
    assert sample_indices(60.0, 12) == [pytest.approx(2.5 + i * 5.0) for i in range(12)]
    assert sample_indices(10.0, 4) == [pytest.approx(1.25 + i * 2.5) for i in range(4)]
    assert sample_indices(0.0, 12) == []


def test_laplacian_variance_sharp_beats_blurred() -> None:
    assert laplacian_variance(_png("stripes")) > laplacian_variance(_png("blurred"))


def _count(value: int | None) -> FloorCount:
    if value is None:
        return FloorCount(image_id="f", status="CANT_VERIFY", reason="no building found")
    return FloorCount(
        image_id="f",
        status="OK",
        lower=value,
        upper=value,
        sam_rows=value,
        vision_estimate=value,
        prompts_used=[],
        reason="test",
    )


def test_consensus_confident() -> None:
    result = consensus([_count(v) for v in [17, 17, 18, 17, 18]])
    assert result.tier == "CONFIDENT"
    assert (result.lower, result.upper) == (17, 18)


def test_consensus_estimate_with_two() -> None:
    result = consensus([_count(v) for v in [9, 10, None, None]])
    assert result.tier == "ESTIMATE"
    assert (result.lower, result.upper) == (9, 10)


def test_consensus_cant_verify_when_frames_disagree() -> None:
    result = consensus([_count(v) for v in [5, 9, 14, 20]])
    assert result.tier == "CANT_VERIFY"
    assert "do not agree" in result.reason


def test_consensus_ignores_cant_verify_frames() -> None:
    result = consensus([_count(None), _count(None), _count(7), _count(7), _count(7), _count(7)])
    assert result.tier == "CONFIDENT"
    assert (result.lower, result.upper) == (7, 7)


def test_consensus_needs_two_minimum() -> None:
    result = consensus([_count(7)])
    assert result.tier == "CANT_VERIFY"


def test_consensus_open_top_votes_lower_bound() -> None:
    first = _count(6)
    first.upper = None
    result = consensus([first, _count(6), _count(6), _count(7)])
    assert result.tier == "CONFIDENT"
    assert (result.lower, result.upper) == (6, 7)


def test_consensus_range_counts_do_not_vote() -> None:
    wide = _count(5)
    wide.upper = 9
    result = consensus([wide, wide, wide, wide])
    assert result.tier == "CANT_VERIFY"


def test_video_tools_available_returns_bool() -> None:
    from stackcheck.vision.frames import video_tools_available

    assert isinstance(video_tools_available(), bool)


async def test_extract_frames_missing_file_raises(tmp_path: Path) -> None:
    from stackcheck.vision.frames import extract_frames

    with pytest.raises(VideoError):
        await extract_frames(tmp_path / "nope.mp4", [1.0], tmp_path / "out")


def test_contact_sheet_size(tmp_path: Path) -> None:
    from stackcheck.vision.contact_sheet import build_contact_sheet

    frames = []
    for i in range(4):
        path = tmp_path / f"f{i}.png"
        from PIL import Image

        Image.new("RGB", (100, 80), (i * 40, 0, 0)).save(path, format="PNG")
        count = _count(3)
        count.rows_y = [20.0, 40.0, 60.0]
        count.building_box = Box(x1=10.0, y1=10.0, x2=90.0, y2=70.0)
        frames.append((path, count))
    out = build_contact_sheet(frames, tmp_path / "sheet.png")
    from PIL import Image

    with Image.open(out) as img:
        assert img.size[0] > 100 and img.size[1] > 80
