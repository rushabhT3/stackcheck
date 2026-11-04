"""Run the 8-video bench inside Modal (no local ffmpeg needed).

The worker image has ffmpeg 5.1.9. The manifest stays local: the entrypoint
reads data/bench_video/videos.csv, passes the rows to the remote function,
which downloads each file_url once to the Volume, trims V06 to 40 s,
re-encodes clips over 50 MB down to 1080p, runs real frame sampling and
consensus, and writes bench_video.json plus contact sheets to the Volume.

    uv run --with modal modal run deploy/bench_video_modal.py
    uv run --with modal modal volume get stackcheck-data /eval/bench_video.json data/eval/bench_video.json
    uv run --with modal modal volume get stackcheck-data /eval/bench_video_sheets data/eval/bench_video_sheets

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

app = modal.App("stackcheck-video-bench")
volume = modal.Volume.from_name("stackcheck-data", create_if_missing=True)

TRIM_FIRST = {"V06": 40}
MAX_BYTES = 200_000_000
UPLOAD_BYTES = 50_000_000
_EXTENSIONS = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("stackcheck-keys")],
    volumes={"/data": volume},
    timeout=3600,
)
def bench_videos(rows: list[dict]) -> list[dict]:
    """Download, trim, shrink, sample and agree every bench video. Never raises per row."""
    import asyncio
    import hashlib
    import json
    import logging
    import shutil
    import subprocess
    import tempfile
    from datetime import date
    from pathlib import Path

    import httpx

    from stackcheck.config import Settings
    from stackcheck.models import QuarterFiling
    from stackcheck.pipeline.run_check import VideoUpload, run_video_evidence

    logger = logging.getLogger(__name__)
    files_dir = Path("/data/bench_video/files")
    files_dir.mkdir(parents=True, exist_ok=True)

    def extension_for(content_type: str, url: str) -> str:
        base = content_type.split(";")[0].strip().lower()
        if base in _EXTENSIONS:
            return _EXTENSIONS[base]
        suffix = Path(url.split("?")[0]).suffix.lower()
        return suffix if suffix in (".mp4", ".mov", ".webm") else ".mp4"

    def downloaded(row: dict) -> Path | None:
        known = sorted(files_dir.glob(f"{row['id']}.*"))
        known = [p for p in known if p.suffix.lower() in (".mp4", ".mov", ".webm")]
        if known:
            print(f"{row['id']}: already on Volume ({known[0].name})")
            return known[0]
        with httpx.stream("GET", row["file_url"], follow_redirects=True, timeout=300.0) as response:
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "")
            if not content_type.split(";")[0].strip().lower().startswith("video/"):
                raise ValueError(f"not a video ({content_type!r})")
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(delete=False, dir=str(files_dir)) as tmp:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError(f"larger than {MAX_BYTES} bytes")
                    digest.update(chunk)
                    tmp.write(chunk)
            dest = files_dir / f"{row['id']}{extension_for(content_type, row['file_url'])}"
            Path(tmp.name).replace(dest)
            print(f"{row['id']}: downloaded {size} bytes, sha256 {digest.hexdigest()[:16]}")
            return dest

    def run_ffmpeg(args: list[str], what: str) -> None:
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"{what} failed: {result.stderr[-200:]}")

    def trim_first(path: Path, seconds: int) -> Path:
        tmp = path.with_name(f"{path.stem}.first{seconds}s{path.suffix}")
        run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-t",
                str(seconds),
                "-c",
                "copy",
                str(tmp),
            ],
            "trim",
        )
        tmp.replace(path)
        return path

    def shrink_to_limit(path: Path) -> Path:
        heights = [1080, 720]
        for height in heights:
            if path.stat().st_size <= UPLOAD_BYTES:
                return path
            tmp = path.with_name(f"{path.stem}.h{height}p{path.suffix}")
            run_ffmpeg(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(path),
                    "-vf",
                    f"scale=-2:{height}",
                    "-crf",
                    "23",
                    "-preset",
                    "veryfast",
                    "-c:a",
                    "aac",
                    str(tmp),
                ],
                "shrink",
            )
            tmp.replace(path)
        return path

    def reference(value: object) -> int | None:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    settings = Settings(data_dir=Path("/data"))
    out: list[dict] = []
    for row in rows:
        video_id = row["id"]
        try:
            path = downloaded(row)
            if row["id"] in TRIM_FIRST:
                path = trim_first(path, TRIM_FIRST[row["id"]])
            path = shrink_to_limit(path)
            work = Path("/data/work/bench_video") / video_id
            sources = work / "sources"
            sources.mkdir(parents=True, exist_ok=True)
            staged = sources / f"{video_id}{path.suffix.lower()}"
            shutil.copyfile(path, staged)
            credit = (
                ", ".join(p for p in (row.get("author"), row.get("licence"), row.get("page_url")) if p)
                or None
            )
            evidence = asyncio.run(
                run_video_evidence(
                    VideoUpload(upload_id=video_id, block_label="MAIN", credit=credit),
                    work,
                    settings,
                    f"bench-{video_id}",
                    None,
                    QuarterFiling(doc_id=video_id),
                    credit=credit,
                )
            )
            kept = len(list((work / "video_frames").glob("frame_*.png")))
            sheets = Path("/data/eval/bench_video_sheets")
            sheets.mkdir(parents=True, exist_ok=True)
            if evidence.contact_sheet:
                src = work / evidence.contact_sheet
                if src.is_file():
                    shutil.copyfile(src, sheets / f"{video_id}.png")
            out.append(
                {
                    "id": video_id,
                    "status": "OK",
                    "frames_kept": kept,
                    "frame_counts": evidence.frame_counts,
                    "tier": evidence.tier,
                    "lower": evidence.lower,
                    "upper": evidence.upper,
                    "reference_floors": reference(row.get("reference_floors")),
                    "credit": evidence.credit,
                    "measured_on": date.today().isoformat(),
                }
            )
        except Exception as exc:
            logger.error("Bench %s failed: %s %s", video_id, type(exc).__name__, exc)
            out.append(
                {
                    "id": video_id,
                    "status": f"FAIL {type(exc).__name__}: {exc}"[:200],
                    "reference_floors": reference(row.get("reference_floors")),
                }
            )
    eval_dir = Path("/data/eval")
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "bench_video.json").write_text(json.dumps({"videos": out}, indent=2), encoding="utf-8")
    volume.commit()
    return out


@app.local_entrypoint()
def main() -> None:
    """Read the local manifest and run the bench on Modal."""
    import csv
    from pathlib import Path as LocalPath

    manifest = LocalPath("data/bench_video/videos.csv")
    if not manifest.is_file():
        raise SystemExit("missing data/bench_video/videos.csv: copy video_bench_manifest.csv there first")
    with manifest.open(encoding="utf-8-sig", newline="") as handle:
        rows = [dict(r) for r in csv.DictReader(handle)]
    rows = [r for r in rows if (r.get("id") or "").strip() and (r.get("file_url") or "").strip()]
    if not rows:
        raise SystemExit(f"no video rows in {manifest}")
    print(f"benching {len(rows)} videos on Modal...")
    for video in bench_videos.remote(rows):
        print(
            f"{video['id']}: {video.get('status')} kept={video.get('frames_kept')} "
            f"counts={video.get('frame_counts')} tier={video.get('tier')} "
            f"range={video.get('lower')}-{video.get('upper')} ref={video.get('reference_floors')}"
        )
    print(
        "copy down with: modal volume get stackcheck-data /eval/bench_video.json data/eval/bench_video.json"
    )
