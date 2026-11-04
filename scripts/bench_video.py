"""Run the frame pipeline and consensus on the bench videos (live model calls).

Needs data/bench_video/videos.csv (human-made, never committed), the files
from fetch_video_bench.py, ffmpeg and model keys. Costs a few dollars.

    uv run python scripts/fetch_video_bench.py --manifest data/bench_video/videos.csv
    uv run python scripts/bench_video.py

Writes data/eval/bench_video.json and contact sheets to
data/eval/bench_video_sheets/<id>.png. Videos with no reference count are
listed as "no reference yet" and never scored (see eval_report).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
from datetime import date
from pathlib import Path

try:  # pytest: repo root on sys.path
    from scripts.fetch_video_bench import VideoRow, read_manifest
except ImportError:  # direct run: scripts/bench_video.py
    from fetch_video_bench import VideoRow, read_manifest

from stackcheck.config import Settings
from stackcheck.models import QuarterFiling
from stackcheck.pipeline.run_check import VideoUpload, run_video_evidence

logger = logging.getLogger(__name__)

OUT_JSON = Path("data/eval/bench_video.json")
SHEETS_DIR = Path("data/eval/bench_video_sheets")
WORK_ROOT = Path("data/work/bench_video")


def _reference(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def stage_video(row: VideoRow, files_dir: Path, work: Path) -> Path | None:
    """Copy files/<id>.* to work/sources/<id>.* so the pipeline finds it."""
    matches = sorted(files_dir.glob(f"{row.id}.*"))
    matches = [p for p in matches if p.suffix.lower() in (".mp4", ".mov", ".webm")]
    if not matches:
        return None
    sources = work / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    staged = sources / f"{row.id}{matches[0].suffix.lower()}"
    shutil.copyfile(matches[0], staged)
    return staged


async def bench_one(row: VideoRow, files_dir: Path, settings: Settings) -> dict:
    """Sample, count and agree one video with real clients. Never raises."""
    work = WORK_ROOT / row.id
    try:
        if stage_video(row, files_dir, work) is None:
            return {
                "id": row.id,
                "status": "missing file",
                "reference_floors": _reference(row.reference_floors),
            }
        credit = ", ".join(part for part in (row.author, row.licence, row.page_url) if part) or None
        evidence = await run_video_evidence(
            VideoUpload(upload_id=row.id, block_label="MAIN", credit=credit),
            work,
            settings,
            f"bench-{row.id}",
            None,
            QuarterFiling(doc_id=row.id),
            credit=credit,
        )
    except Exception as exc:
        logger.error("Bench %s failed: %s %s", row.id, type(exc).__name__, exc)
        return {
            "id": row.id,
            "status": f"FAIL {type(exc).__name__}",
            "reference_floors": _reference(row.reference_floors),
        }
    kept = len(list((work / "video_frames").glob("frame_*.png")))
    if evidence.contact_sheet:
        await asyncio.to_thread(SHEETS_DIR.mkdir, parents=True, exist_ok=True)
        src = work / evidence.contact_sheet
        if src.is_file():
            shutil.copyfile(src, SHEETS_DIR / f"{row.id}.png")
    return {
        "id": row.id,
        "status": "OK",
        "frames_kept": kept,
        "frame_counts": evidence.frame_counts,
        "tier": evidence.tier,
        "lower": evidence.lower,
        "upper": evidence.upper,
        "reference_floors": _reference(row.reference_floors),
        "credit": evidence.credit,
        "measured_on": date.today().isoformat(),
    }


def bench_all(manifest: Path, files_dir: Path, settings: Settings) -> list[dict]:
    """Bench every manifest row in order. Live calls; reruns are cached."""
    rows = read_manifest(manifest)
    if not rows:
        raise ValueError(f"no video rows in {manifest}")
    return [asyncio.run(bench_one(row, files_dir, settings)) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/bench_video/videos.csv")
    parser.add_argument("--files-dir", default=None)
    parser.add_argument("--cache-dir", default=str(WORK_ROOT / "cache"))
    args = parser.parse_args(argv)
    manifest = Path(args.manifest)
    files_dir = Path(args.files_dir) if args.files_dir else manifest.parent / "files"
    settings = Settings(data_dir=Path(args.cache_dir))
    videos = bench_all(manifest, files_dir, settings)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"videos": videos}, indent=2), encoding="utf-8")
    for video in videos:
        counts = video.get("frame_counts", [])
        print(f"{video['id']}: {video.get('status')} kept={video.get('frames_kept')}")
        print(f"  counts={counts} tier={video.get('tier')} range={video.get('lower')}-{video.get('upper')}")
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
