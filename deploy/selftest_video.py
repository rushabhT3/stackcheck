"""Self-test for the site-video frame pipeline, run after every deploy.

    uv run --with modal modal deploy deploy/modal_app.py
    uv run --with modal modal run deploy/selftest_video.py

Runs inside the deployed image (same ffmpeg, same libraries as the check):
generates a 3 s testsrc clip, then runs probe, sampling, the blur filter
and consensus with counting stubbed. Prints one OK or FAIL line per stage.

Keep the image below in sync with deploy/modal_app.py.
"""

from __future__ import annotations

import modal

image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("ffmpeg")
    .uv_sync()
    .add_local_python_source("stackcheck", ignore=["**/__pycache__", "**/*.pyc"])
)

app = modal.App("stackcheck-video-selftest")


@app.function(image=image, secrets=[modal.Secret.from_name("stackcheck-keys")], timeout=300)
def check_video_tools() -> dict[str, str]:
    """Generate a clip and run every frame-pipeline stage with counting stubbed.

    The count stage runs one real vision describe call on the sharpest frame
    (one cached or live call, well under a cent); SAM stays stubbed.
    """
    import asyncio
    import hashlib
    import subprocess
    import tempfile
    from pathlib import Path

    from PIL import Image as PILImage

    from stackcheck.config import Settings, model_for
    from stackcheck.extract.photos import describe_cached
    from stackcheck.llm.registry import vision_client
    from stackcheck.models import ExtractedImage, FloorCount, QuarterFiling
    from stackcheck.vision.frames import (
        consensus,
        probe_duration,
        sample_sharp_frames,
        video_tool_versions,
        video_tools_available,
    )

    stages: dict[str, str] = {}
    stages["tools"] = f"OK {video_tool_versions()}" if video_tools_available() else "FAIL tools missing"
    tmp = Path(tempfile.mkdtemp(prefix="selftest_video_"))
    clip = tmp / "selftest.mp4"
    gen = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        capture_output=True,
        text=True,
    )
    if gen.returncode != 0 or not clip.is_file():
        stages["generate"] = f"FAIL {gen.stderr[-200:]}"
        return stages
    stages["generate"] = f"OK {clip.stat().st_size} bytes"
    try:
        duration = probe_duration(clip)
    except Exception as exc:
        stages["probe"] = f"FAIL {type(exc).__name__}: {exc}"
        return stages
    stages["probe"] = f"OK duration={duration:.2f}s"
    try:
        frames = asyncio.run(sample_sharp_frames(clip, tmp / "frames", duration))
    except Exception as exc:
        stages["sample"] = f"FAIL {type(exc).__name__}: {exc}"
        return stages
    sharp = [round(score, 1) for _, score in frames]
    stages["sample"] = f"OK {len(frames)} frames kept"
    stages["blur_filter"] = f"OK sharpness min={min(sharp)} max={max(sharp)}"
    stubbed = [
        FloorCount(image_id=f"selftest-f{i}", status="OK", lower=3, upper=3, reason="stub")
        for i in range(len(frames))
    ]
    result = consensus(stubbed)
    stages["consensus"] = (
        f"OK tier={result.tier} range=[{result.lower},{result.upper}]"
        if result.tier == "CONFIDENT"
        else f"FAIL tier={result.tier} ({result.reason})"
    )

    sharpest = frames[-1][0]

    async def _describe_sharpest(frame: Path) -> object:
        raw = await asyncio.to_thread(frame.read_bytes)
        with PILImage.open(frame) as img:
            width, height = img.size
        settings = Settings(data_dir="/data")
        model = model_for("upload", settings)
        vision = vision_client(settings, "upload")
        image = ExtractedImage(
            image_id="selftest-f0",
            doc_id="selftest",
            page=0,
            index=0,
            path=str(frame),
            width=width,
            height=height,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        return await describe_cached(
            image,
            frame,
            QuarterFiling(doc_id="selftest"),
            vision,
            check_id="selftest",
            data_dir=tmp,
            model=model,
        )

    try:
        facts = asyncio.run(_describe_sharpest(sharpest))
    except Exception as exc:
        stages["count"] = f"FAIL {type(exc).__name__}: {exc}"
        return stages
    stages["count"] = f"OK kind={facts.kind} confidence={facts.confidence}"
    return stages


@app.local_entrypoint()
def main() -> None:
    """Run the remote self-test and print one line per stage."""
    stages = check_video_tools.remote()
    failed = 0
    for name, line in stages.items():
        print(f"{name}: {line}")
        if line.startswith("FAIL"):
            failed += 1
    if failed:
        raise SystemExit(f"selftest_video: {failed} stage(s) FAILED")
    print("selftest_video: all stages OK")
