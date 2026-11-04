"""Download the 8 bench videos listed in a manifest CSV, one at a time.

Reads only the file_url values from the manifest (no searching, no crawling).
Checks content-type is video/* and size is at most 200 MB. Skips failures with
the page_url printed so a human can save that one by hand. Writes sha256 into
videos.lock.csv next to the manifest. Never commit a video.

Usage: uv run python scripts/fetch_video_bench.py --manifest data/bench_video/videos.csv
       [--keep-first V06=40]
"""

import argparse
import asyncio
import csv
import hashlib
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

MAX_BYTES = 200_000_000
_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


@dataclass
class VideoRow:
    id: str
    page_url: str
    file_url: str
    licence: str
    author: str
    reference_floors: str


@dataclass
class VideoEntry:
    id: str
    path: Path
    sha256: str


def read_manifest(path: Path) -> list[VideoRow]:
    """Parse the human-made manifest CSV."""
    rows: list[VideoRow] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            rows.append(
                VideoRow(
                    id=(record.get("id") or "").strip(),
                    page_url=(record.get("page_url") or "").strip(),
                    file_url=(record.get("file_url") or "").strip(),
                    licence=(record.get("licence") or "").strip(),
                    author=(record.get("author") or "").strip(),
                    reference_floors=(record.get("reference_floors") or "").strip(),
                )
            )
    return [row for row in rows if row.id and row.file_url]


def extension_for(content_type: str, url: str) -> str:
    """Extension from the content-type, falling back to the URL suffix."""
    base = content_type.split(";")[0].strip().lower()
    if base in _EXTENSIONS:
        return _EXTENSIONS[base]
    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix in (".mp4", ".mov", ".webm"):
        return suffix
    return ".mp4"


async def fetch_one(client: httpx.AsyncClient, video_id: str, url: str, files_dir: Path) -> VideoEntry:
    """Stream one video with a size cap. Raises ValueError when it is not a video."""
    async with client.stream("GET", url, follow_redirects=True) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if not content_type.split(";")[0].strip().lower().startswith("video/"):
            raise ValueError(f"not a video (content-type {content_type!r})")
        digest = hashlib.sha256()
        size = 0
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            digest.update(chunk)
            if size > MAX_BYTES:
                raise ValueError(f"larger than {MAX_BYTES} bytes")
    path = files_dir / f"{video_id}{extension_for(content_type, url)}"
    body = b"".join(chunks)

    def _write() -> None:
        files_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    await asyncio.to_thread(_write)
    return VideoEntry(id=video_id, path=path, sha256=digest.hexdigest())


async def fetch_all(manifest: Path, files_dir: Path) -> tuple[list[VideoEntry], list[str]]:
    """Download every manifest row, skipping failures. Writes the lock file."""
    entries: list[VideoEntry] = []
    skipped: list[str] = []
    rows = read_manifest(manifest)
    async with httpx.AsyncClient(timeout=300.0, headers={"User-Agent": "StackCheck/0.1 (bench)"}) as client:
        for row in rows:
            try:
                entries.append(await fetch_one(client, row.id, row.file_url, files_dir))
                print(f"OK {row.id}")
            except (ValueError, httpx.HTTPError) as exc:
                print(f"SKIP {row.id} ({exc}); save by hand from {row.page_url}")
                skipped.append(row.id)
    lock_path = manifest.parent / "videos.lock.csv"
    with lock_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "file", "sha256"])
        for entry in entries:
            writer.writerow([entry.id, entry.path.name, entry.sha256])
    print(f"Wrote {lock_path} ({len(entries)} ok, {len(skipped)} skipped)")
    return entries, skipped


def trim_first_seconds(path: Path, seconds: int) -> Path:
    """Keep the first N seconds of a video with ffmpeg, replacing the file."""
    if shutil.which("ffmpeg") is None:
        raise ValueError("ffmpeg not found; cannot trim")
    tmp = path.with_name(path.stem + f".first{seconds}s" + path.suffix)
    result = subprocess.run(
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
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(f"ffmpeg trim failed: {result.stderr.decode()[:200]}")
    tmp.replace(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/bench_video/videos.csv")
    parser.add_argument("--files-dir", default=None)
    parser.add_argument("--keep-first", default=None, help="ID=SECONDS, e.g. V06=40")
    args = parser.parse_args(argv)
    manifest = Path(args.manifest)
    files_dir = Path(args.files_dir) if args.files_dir else manifest.parent / "files"
    entries, _ = asyncio.run(fetch_all(manifest, files_dir))
    if args.keep_first:
        video_id, _, seconds = args.keep_first.partition("=")
        match = next((e for e in entries if e.id == video_id), None)
        if match is None:
            print(f"No downloaded file for {video_id}; cannot trim")
        else:
            trim_first_seconds(match.path, int(seconds))
            print(f"Trimmed {video_id} to the first {seconds}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
