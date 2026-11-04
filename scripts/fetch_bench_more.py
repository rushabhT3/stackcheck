"""Download 40 more floor-count benchmark candidates from Wikimedia Commons.

Three fixed queries with per-query quotas (20/10/10). Same filters as
scripts/fetch_bench.py (licence allow-list, size floor, title block-list):
no model filtering, no labelling, no bench run. Skips files whose SHA-1 is
already in data/bench/candidates.json. Numbers from b32, writes its own
candidates.json manifest (same schema, plus the query and file name).

Run from the repo root:
    uv run python scripts/fetch_bench_more.py
    uv run python scripts/fetch_bench_more.py --out data/bench_more

Then zip for the labelling handoff (kept out of this script so the zip step
stays visible):
    Compress-Archive -Path data/bench_more/images/*,data/bench_more/candidates.json `
        -DestinationPath bench_more.zip -Force
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_bench as fb

QUERIES: list[tuple[str, int]] = [
    ('intitle:"Tower Block UK photo"', 20),
    # Only two "(apartment building under construction)" files exist in the
    # Green Bay July 2024 series and both are already in data/bench, so the
    # Green Bay quota comes from the same series' other building photos
    # (banks, hotels, apartments, townhouses). The labeller marks any
    # uncountable photo "unsure", same as the T03 flow.
    ('intitle:"Green Bay July 2024" construction', 10),
    ("Khrushchyovka", 10),
]
THUMB_WIDTH = 1920
START_NUMBER = 32
PAGE_SIZE = 50
MAX_PAGES_PER_QUERY = 8


def existing_sha1() -> set[str]:
    manifest = Path("data/bench/candidates.json")
    if not manifest.exists():
        return set()
    return {entry["sha1"] for entry in json.loads(manifest.read_text(encoding="utf-8")) if entry.get("sha1")}


def search_page(client: httpx.Client, query: str, offset: int) -> list[fb.Candidate]:
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": str(PAGE_SIZE),
        "gsroffset": str(offset),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|sha1|extmetadata",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist",
        "iiurlwidth": str(THUMB_WIDTH),
    }
    return fb.parse_pages(fb.api_get(client, params), query)


def collect(client: httpx.Client, query: str, quota: int, known: set[str]) -> list[fb.Candidate]:
    """Paginate one query until the quota is met, skipping known SHA-1s."""
    chosen: list[fb.Candidate] = []
    offset = 0
    for _ in range(MAX_PAGES_PER_QUERY):
        page = search_page(client, query, offset)
        if not page:
            break
        for cand in page:
            key = cand.sha1 or cand.title
            if key in known:
                continue
            known.add(key)
            chosen.append(cand)
            if len(chosen) >= quota:
                return chosen
        offset += PAGE_SIZE
        time.sleep(fb.PAUSE_SECONDS)
    return chosen


def main() -> int:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 to hit the Commons API.")
    with contextlib.suppress(AttributeError):  # very old Python without reconfigure
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/bench_more"))
    args = parser.parse_args()

    images_dir = args.out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    if any(images_dir.iterdir()):
        print(f"{images_dir} is not empty. Empty it first, so names stay b32, b33, ...")
        return 1

    known = existing_sha1()
    picked: list[fb.Candidate] = []
    short: list[str] = []
    with httpx.Client(headers={"User-Agent": fb.USER_AGENT}, timeout=60, follow_redirects=True) as client:
        for query, quota in QUERIES:
            found = collect(client, query, quota, known)
            picked.extend(found)
            print(f"{len(found):3d}/{quota} usable  <- {query}")
            if len(found) < quota:
                short.append(f"{query}: {len(found)}/{quota}")
            time.sleep(fb.PAUSE_SECONDS)

        names: list[str] = []
        for number, cand in enumerate(picked, start=START_NUMBER):
            suffix = ".png" if cand.mime == "image/png" else ".jpg"
            name = f"b{number:02d}{suffix}"
            fb.download(client, cand.thumb_url, images_dir / name)
            names.append(name)
            print(f"{name}  {cand.licence:<14} {cand.title.removeprefix('File:')[:70]}")
            time.sleep(fb.PAUSE_SECONDS)

    manifest = [{"file": n, **asdict(c)} for n, c in zip(names, picked, strict=True)]
    (args.out / "candidates.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nSaved {len(names)} images to {images_dir}")
    print(f"Wrote candidates.json in {args.out} (zip it with the images as bench_more.zip)")
    if short:
        print("SHORTFALL (fewer usable photos than quota):")
        for line in short:
            print(f"  {line}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
