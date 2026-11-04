"""Download openly licensed building photos from Wikimedia Commons for the floor-count benchmark.

Run from the repo root:
    uv run python scripts/fetch_bench.py
    uv run python scripts/fetch_bench.py --target 24 --out data/bench

What it does:
- searches Wikimedia Commons (File namespace) with a fixed list of queries;
- keeps only JPEG/PNG photos at least 1000 x 800 px whose licence is public domain, CC0,
  CC BY or CC BY-SA (no NC, no ND, nothing else);
- skips titles that suggest no visible storeys (aerial, skyline, interior, plans, renders, night shots);
- downloads a 1600 px wide copy of each, named b01.jpg, b02.jpg, ...;
- writes ATTRIBUTION.md (title, author, licence, source) and labels.csv with empty labels.

It only talks to the public Commons API, one request at a time, with a descriptive User-Agent,
which is how Wikimedia asks tools to behave. Files in data/bench are allowed in the repo
because every one of them is openly licensed and attributed.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "StackCheckBench/0.1 (https://github.com/rushabhT3; benchmark for a hackathon research tool)"

QUERIES = [
    "residential building under construction India",
    "apartment building under construction",
    "building under construction concrete frame",
    "high-rise under construction Mumbai",
    "apartment building Hyderabad",
    "residential tower Bangalore",
    "apartment block facade",
    "tower block housing estate",
    "residential building Pune",
    "building construction site Kolkata",
]

EXCLUDE_TITLE_WORDS = [
    "aerial",
    "drone",
    "skyline",
    "panorama",
    "interior",
    "inside",
    "plan",
    "map",
    "drawing",
    "diagram",
    "logo",
    "render",
    "rendering",
    "model",
    "night",
    "sketch",
    "blueprint",
    "elevator",
    "staircase",
    "stairs",
    "room",
    "kitchen",
    "lobby",
    "satellite",
]

ALLOWED_MIME = {"image/jpeg", "image/png"}
MIN_WIDTH = 1000
MIN_HEIGHT = 800
THUMB_WIDTH = 1600
PER_QUERY_CAP = 4
PAUSE_SECONDS = 1.0


@dataclass(frozen=True)
class Candidate:
    title: str
    page_url: str
    thumb_url: str
    width: int
    height: int
    mime: str
    sha1: str
    licence: str
    licence_url: str
    author: str
    query: str


def clean_html(value: str) -> str:
    """Commons author fields contain HTML links; keep only the visible text."""
    text = re.sub(r"<[^>]+>", "", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def is_allowed_licence(short_name: str) -> bool:
    name = (short_name or "").strip().lower()
    if not name or "nc" in re.split(r"[\s\-]+", name) or "nd" in re.split(r"[\s\-]+", name):
        return False
    if name in {"public domain", "pd", "cc0", "cc0 1.0", "cc-zero"} or name.startswith("pd-"):
        return True
    return bool(re.fullmatch(r"cc[\s\-]by(?:[\s\-]sa)?(?:[\s\-]\d\.\d)?(?:[\s\-][a-z]{2,})?", name))


def title_ok(title: str) -> bool:
    lowered = title.lower()
    return not any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in EXCLUDE_TITLE_WORDS)


def parse_pages(data: dict, query: str) -> list[Candidate]:
    """Turn one API response (formatversion=2) into candidates that pass every filter."""
    pages = (data.get("query") or {}).get("pages") or []
    pages = sorted(pages, key=lambda p: p.get("index", 0))
    out: list[Candidate] = []
    for page in pages:
        title = page.get("title", "")
        info_list = page.get("imageinfo") or []
        if not info_list or not title_ok(title):
            continue
        info = info_list[0]
        meta = info.get("extmetadata") or {}
        licence = (meta.get("LicenseShortName") or {}).get("value", "")
        if info.get("mime") not in ALLOWED_MIME or not is_allowed_licence(licence):
            continue
        width, height = int(info.get("width", 0)), int(info.get("height", 0))
        if width < MIN_WIDTH or height < MIN_HEIGHT or not info.get("thumburl"):
            continue
        out.append(
            Candidate(
                title=title,
                page_url=info.get("descriptionurl", ""),
                thumb_url=info["thumburl"],
                width=width,
                height=height,
                mime=info["mime"],
                sha1=info.get("sha1", ""),
                licence=licence,
                licence_url=(meta.get("LicenseUrl") or {}).get("value", ""),
                author=clean_html((meta.get("Artist") or {}).get("value", "")) or "Unknown author",
                query=query,
            )
        )
    return out


def pick(
    by_query: dict[str, list[Candidate]], target: int, per_query: int = PER_QUERY_CAP
) -> list[Candidate]:
    """Round-robin across queries for variety, no duplicates, at most per_query from each."""
    chosen: list[Candidate] = []
    seen: set[str] = set()
    taken = dict.fromkeys(by_query, 0)
    queues = {q: list(c) for q, c in by_query.items()}
    while len(chosen) < target and any(queues.values()):
        for query in list(queues):
            if len(chosen) >= target:
                break
            while queues[query]:
                cand = queues[query].pop(0)
                key = cand.sha1 or cand.title
                if key in seen:
                    continue
                if taken[query] >= per_query:
                    queues[query] = []
                    break
                seen.add(key)
                taken[query] += 1
                chosen.append(cand)
                break
    return chosen


BUSY_ERROR_CODES = {"cirrussearch-too-busy-error", "maxlag"}


def api_get(client: httpx.Client, params: dict) -> dict:
    for attempt in range(6):
        response = client.get(API_URL, params=params)
        if response.status_code in (429, 500, 502, 503, 504):
            wait = float(response.headers.get("Retry-After", 2 * (attempt + 1)))
            time.sleep(wait)
            continue
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            if data["error"].get("code") in BUSY_ERROR_CODES:
                time.sleep(30 * (attempt + 1))
                continue
            raise RuntimeError(f"Commons API error: {data['error']}")
        return data
    raise RuntimeError("Commons API kept failing; try again in a few minutes.")


def search(client: httpx.Client, query: str) -> list[Candidate]:
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "40",
        "prop": "imageinfo",
        "iiprop": "url|size|mime|sha1|extmetadata",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist",
        "iiurlwidth": str(THUMB_WIDTH),
    }
    return parse_pages(api_get(client, params), query)


def download(client: httpx.Client, url: str, dest: Path) -> None:
    for attempt in range(4):
        response = client.get(url)
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(float(response.headers.get("Retry-After", 2 * (attempt + 1))))
            continue
        response.raise_for_status()
        dest.write_bytes(response.content)
        return
    raise RuntimeError(f"Download kept failing: {url}")


def write_outputs(chosen: list[Candidate], out_dir: Path, names: list[str]) -> None:
    rows = [
        "# Benchmark image attribution",
        "",
        "All images are from Wikimedia Commons under the licence shown. Overlays drawn by StackCheck",
        "on CC BY-SA images are shared under CC BY-SA.",
        "",
        "| File | Title | Author | Licence | Source |",
        "|---|---|---|---|---|",
    ]
    for name, cand in zip(names, chosen, strict=True):
        title = cand.title.removeprefix("File:").replace("|", "/")
        author = cand.author.replace("|", "/")
        rows.append(f"| {name} | {title} | {author} | {cand.licence} | {cand.page_url} |")
    (out_dir / "ATTRIBUTION.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    with (out_dir / "labels.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "storeys_visible", "top_obscured", "notes"])
        for name in names:
            writer.writerow([name, "", "", ""])

    manifest = [{"file": n, **asdict(c)} for n, c in zip(names, chosen, strict=True)]
    (out_dir / "candidates.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> int:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 to hit the Commons API.")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target", type=int, default=24, help="how many photos to download (default 24)")
    parser.add_argument(
        "--out", type=Path, default=Path("data/bench"), help="output folder (default data/bench)"
    )
    args = parser.parse_args()

    images_dir = args.out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    if any(images_dir.iterdir()):
        print(f"{images_dir} is not empty. Move or delete its files first, so names stay b01, b02, ...")
        return 1

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as client:
        by_query: dict[str, list[Candidate]] = {}
        for query in QUERIES:
            found = search(client, query)
            by_query[query] = found
            print(f"{len(found):3d} usable  <- {query}")
            time.sleep(PAUSE_SECONDS)

        chosen = pick(by_query, args.target)
        if not chosen:
            print("No usable images found. Check your internet connection and try again.")
            return 1

        names: list[str] = []
        for number, cand in enumerate(chosen, start=1):
            suffix = ".png" if cand.mime == "image/png" else ".jpg"
            name = f"b{number:02d}{suffix}"
            download(client, cand.thumb_url, images_dir / name)
            names.append(name)
            print(f"{name}  {cand.licence:<14} {cand.title.removeprefix('File:')[:70]}")
            time.sleep(PAUSE_SECONDS)

    write_outputs(chosen, args.out, names)
    print(f"\nSaved {len(names)} images to {images_dir}")
    print(f"Wrote ATTRIBUTION.md, labels.csv (labels empty) and candidates.json in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
