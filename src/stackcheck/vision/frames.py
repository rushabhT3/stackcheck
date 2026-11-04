"""Video frame sampling, blur filtering and per-frame count consensus.

Pure except `extract_frames`/`probe_duration`, which shell out to ffmpeg.
"""

import asyncio
import contextlib
import io
import logging
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

N_FRAMES = 12
KEEP_FRAMES = 8
AGREE_WINDOW = 1
CONFIDENT_MIN_FRAMES = 4


class VideoError(Exception):
    """A video cannot be opened, read or trimmed."""


class VideoToolError(VideoError):
    """ffmpeg/ffprobe or the image library is missing on this machine."""


class VideoProbeError(VideoError):
    """The file is not a readable video: probing it failed."""


class VideoSampleError(VideoError):
    """Probe worked but no frame could be sampled from the video."""


class VideoCountError(VideoError):
    """Frames were sampled but no floor count came out of them."""


def video_tools_available() -> bool:
    """ffmpeg, ffprobe and the image library the frame step decodes with."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return False
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        return False
    return True


def video_tool_versions() -> dict[str, str | None]:
    """First `ffmpeg -version` / `ffprobe -version` line per binary; None if missing."""
    versions: dict[str, str | None] = {}
    for name in ("ffmpeg", "ffprobe"):
        exe = shutil.which(name)
        if exe is None:
            versions[name] = None
            continue
        try:
            out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            versions[name] = None
            continue
        line = (out.stdout or "").splitlines()
        versions[name] = line[0].strip() if line else None
    return versions


def sample_indices(duration_s: float, n: int = N_FRAMES) -> list[float]:
    """Evenly spaced sample times in seconds; empty when there is no duration."""
    if duration_s <= 0:
        return []
    step = duration_s / n
    return [step * (i + 0.5) for i in range(n)]


def laplacian_variance(png: bytes) -> float:
    """Sharpness score for one frame: variance of the Laplacian response."""
    from PIL import Image, ImageFilter

    with Image.open(io.BytesIO(png)) as img:
        grey = img.convert("L")
    kernel = ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1)
    response = grey.filter(kernel)
    values = list(response.getdata())
    return statistics.pvariance(values)


def probe_duration(path: Path) -> float:
    """Video duration in seconds via ffprobe. Raises VideoError without ffmpeg."""
    if shutil.which("ffprobe") is None:
        raise VideoProbeError("ffprobe not found; cannot read video duration")
    result = subprocess.run(
        [
            "ffprobe",
            "-hide_banner",
            "-loglevel",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise VideoProbeError(f"ffprobe failed: {result.stderr.decode()[:200]}")
    try:
        return float(result.stdout.decode().strip())
    except ValueError as exc:
        raise VideoProbeError(f"ffprobe gave no duration for {path}") from exc


async def extract_frames(path: Path, times_s: list[float], out_dir: Path) -> list[Path]:
    """Dump one PNG per sample time with ffmpeg. Raises VideoError on failure."""
    if shutil.which("ffmpeg") is None:
        raise VideoSampleError("ffmpeg not found; cannot sample video frames")

    def _run() -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, at in enumerate(times_s):
            out = out_dir / f"frame_{i:02d}.png"
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    str(at),
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    str(out),
                ],
                capture_output=True,
            )
            if result.returncode != 0:
                raise VideoSampleError(f"ffmpeg frame {i} failed: {result.stderr.decode()[:200]}")
            saved.append(out)
        return saved

    return await asyncio.to_thread(_run)


async def frame_sharpness(paths: list[Path]) -> list[tuple[Path, float]]:
    """Score every frame; sharpest last. Unreadable frames are skipped."""

    def _score(path: Path) -> tuple[Path, float] | None:
        try:
            return path, laplacian_variance(path.read_bytes())
        except (OSError, ValueError):
            return None

    scored = await asyncio.gather(*[asyncio.to_thread(_score, path) for path in paths])
    return sorted((item for item in scored if item is not None), key=lambda item: item[1])


@dataclass
class VideoConsensus:
    tier: str  # CONFIDENT, ESTIMATE or CANT_VERIFY
    lower: int | None
    upper: int | None
    frames_used: int
    reason: str


def consensus(counts: list) -> VideoConsensus:
    """Agree per-frame counts within 1 floor of their median.

    Exact OK counts vote their value; an open-top count (upper None) votes its
    lower bound. Closed ranges (lower != upper, both set) and CANT_VERIFY
    frames do not vote. 4+ agreeing frames give CONFIDENT, 2-3 give ESTIMATE.
    """
    from stackcheck.models import FloorCount

    votes: list[int] = []
    for count in counts:
        if not isinstance(count, FloorCount) or count.status != "OK":
            continue
        if count.lower is None:
            continue
        if count.upper is None or count.lower == count.upper:
            votes.append(count.lower)
    if len(votes) < 2:
        return VideoConsensus(
            tier="CANT_VERIFY",
            lower=None,
            upper=None,
            frames_used=0,
            reason="The frames do not agree." if votes else "No frame produced a count.",
        )
    ordered = sorted(votes)
    median = ordered[len(ordered) // 2]
    agreeing = [v for v in votes if abs(v - median) <= AGREE_WINDOW]
    if len(agreeing) >= CONFIDENT_MIN_FRAMES:
        return VideoConsensus(
            tier="CONFIDENT",
            lower=min(agreeing),
            upper=max(agreeing),
            frames_used=len(agreeing),
            reason=f"{len(agreeing)} frames agree within 1 floor of the median ({median}).",
        )
    if len(agreeing) >= 2:
        return VideoConsensus(
            tier="ESTIMATE",
            lower=min(agreeing),
            upper=max(agreeing),
            frames_used=len(agreeing),
            reason=f"only {len(agreeing)} frames agree (estimate).",
        )
    return VideoConsensus(
        tier="CANT_VERIFY",
        lower=None,
        upper=None,
        frames_used=0,
        reason="The frames do not agree.",
    )


async def sample_sharp_frames(
    path: Path,
    work_dir: Path,
    duration_s: float | None = None,
    keep: int = KEEP_FRAMES,
) -> list[tuple[Path, float]]:
    """Sample N_FRAMES evenly, drop blurry ones, keep the sharpest `keep`.

    Returns (frame path, sharpness) pairs, sharpest last. Raises VideoError
    when ffmpeg is missing or no frame can be read.
    """
    if duration_s is None:
        duration_s = await asyncio.to_thread(probe_duration, path)
    times = sample_indices(duration_s)
    if not times:
        raise VideoError(f"Video has no duration: {path}")
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    dumped = await extract_frames(path, times, work_dir)
    scored = await frame_sharpness([p for p in dumped if p.is_file()])
    if not scored:
        raise VideoSampleError(f"No frames could be read from {path}")
    kept = scored[-keep:] if keep else []
    dropped = scored[: len(scored) - len(kept)]
    for frame_path, _ in dropped:
        with contextlib.suppress(OSError):
            frame_path.unlink()
    return kept
