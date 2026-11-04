"""Video skip sentences and the real-ffmpeg frame test. No model calls."""

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stackcheck.errors import FetchError
from stackcheck.pipeline.run_check import _video_skip_note
from stackcheck.vision.frames import VideoError, VideoToolError


def test_skip_note_names_tool_failure() -> None:
    assert _video_skip_note(VideoToolError("video tools missing")) == (
        "Video skipped: our server could not open video files just now."
    )


def test_skip_note_names_bad_file() -> None:
    assert _video_skip_note(VideoError("ffprobe failed")) == (
        "Video skipped: this file is not a video we can open."
    )
    assert _video_skip_note(FetchError("gone")) == ("Video skipped: this file is not a video we can open.")


def test_skip_note_keeps_generic_for_unknown() -> None:
    assert _video_skip_note(RuntimeError("boom")) == "Video skipped: it could not be read."


def test_skip_note_names_sample_and_count_stages() -> None:
    from stackcheck.vision.frames import VideoCountError, VideoSampleError

    assert _video_skip_note(VideoSampleError("no frames")) == (
        "Video skipped: we could not sample frames from it."
    )
    assert _video_skip_note(VideoCountError("none counted")) == (
        "Video skipped: we could not count floors in the frames."
    )


async def test_missing_tools_raise_tool_error(
    tmp_path: Path, tmp_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stackcheck.pipeline.run_check as rc
    from stackcheck.models import QuarterFiling
    from stackcheck.pipeline.run_check import VideoUpload

    monkeypatch.setattr(rc, "video_tools_available", lambda: False)
    with pytest.raises(VideoToolError):
        await rc.run_video_evidence(
            VideoUpload(upload_id="v" * 16),
            tmp_path,
            tmp_settings,
            "chk_" + "v" * 10,
            None,
            QuarterFiling(doc_id="v"),
        )


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        pytest.fail("ffmpeg is not installed: the frame step cannot run here")
    return exe


async def test_real_clip_samples_at_least_eight_frames(tmp_path: Path) -> None:
    """End to end through the real frame step: generate, probe, sample.

    Fails (never skips) without ffmpeg, so a missing binary is loud.
    """
    from stackcheck.vision.frames import probe_duration, sample_sharp_frames

    clip = tmp_path / "clip.mp4"
    made = await asyncio.to_thread(
        subprocess.run,
        [
            _ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=2",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        capture_output=True,
    )
    assert made.returncode == 0, made.stderr.decode()[:200]
    duration = probe_duration(clip)
    assert 1.5 < duration < 2.5
    frames = await sample_sharp_frames(clip, tmp_path / "shots", duration)
    assert len(frames) >= 8
