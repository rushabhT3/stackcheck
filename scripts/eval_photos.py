"""Describe labelled photos and score the answers (live: spends vision calls).

For every SHA in data/truth/photos.csv found under data/work/**/images/,
runs describe_photo and reports kind accuracy, stamp-date accuracy (rows with
a truth date) and storeys within ±1 (elevation rows with a truth count).
Writes data/eval/photos.json.
"""

import argparse
import csv
import hashlib
import json
import os
from datetime import date
from pathlib import Path

from stackcheck.config import get_settings
from stackcheck.extract.photos import describe_photo
from stackcheck.llm.registry import vision_client
from stackcheck.models import ExtractedImage, QuarterFiling


def _load_truth(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return {row["sha256"]: row for row in csv.DictReader(fh) if row.get("sha256")}


def _find_images(work_root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for png in work_root.glob("**/images/*.png"):
        found.setdefault(hashlib.sha256(png.read_bytes()).hexdigest(), png)
    return found


def _parse_date(text: str) -> date | None:
    text = text.strip()
    if not text:
        return None
    year, month, day = (int(part) for part in text.split("-"))
    return date(year, month, day)


async def _evaluate(work_root: Path, origin: str) -> dict:
    import asyncio

    settings = get_settings()
    truth = _load_truth(Path("data/truth/photos.csv"))
    found = _find_images(work_root)
    client = vision_client(settings, origin)  # type: ignore[arg-type]
    results = []
    for sha, row in truth.items():
        path = found.get(sha)
        if path is None:
            results.append({"sha256": sha, "status": "missing"})
            continue
        image = ExtractedImage(
            image_id=path.stem,
            doc_id="eval",
            page=1,
            index=0,
            path=str(path),
            width=0,
            height=0,
            sha256=sha,
        )
        facts = await describe_photo(
            image, path, QuarterFiling(doc_id="eval"), client, check_id="eval-photos"
        )
        entry: dict[str, object] = {
            "sha256": sha,
            "status": "scored",
            "kind_truth": row["kind"],
            "kind_got": facts.kind,
            "kind_ok": facts.kind == row["kind"],
        }
        want_date = _parse_date(row.get("stamp_date", ""))
        if want_date is not None:
            entry["date_ok"] = facts.stamp_date == want_date
            entry["date_got"] = facts.stamp_date.isoformat() if facts.stamp_date else None
        want_storeys = row.get("storeys_visible", "").strip()
        if row["kind"] == "elevation" and want_storeys:
            got = facts.vision_storeys_visible
            entry["storeys_ok"] = got is not None and abs(got - int(want_storeys)) <= 1
            entry["storeys_got"] = got
        results.append(entry)
        await asyncio.sleep(0)
    return {"results": results}


def _summarize(results: list[dict]) -> dict[str, object]:
    scored = [r for r in results if r["status"] == "scored"]
    dated = [r for r in scored if "date_ok" in r]
    storeyed = [r for r in scored if "storeys_ok" in r]
    return {
        "photos_scored": len(scored),
        "photos_missing": sum(1 for r in results if r["status"] == "missing"),
        "kind_accuracy": sum(1 for r in scored if r["kind_ok"]) / len(scored) if scored else None,
        "date_accuracy": sum(1 for r in dated if r["date_ok"]) / len(dated) if dated else None,
        "storeys_accuracy": (
            sum(1 for r in storeyed if r["storeys_ok"]) / len(storeyed) if storeyed else None
        ),
    }


def main() -> int:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this paid eval.")
    import asyncio

    parser = argparse.ArgumentParser(description="Score photo descriptions against hand labels.")
    parser.add_argument("--work", default="data/work", help="Root holding check folders")
    parser.add_argument("--origin", default="url", choices=("url", "upload"))
    args = parser.parse_args()
    evaluated = asyncio.run(_evaluate(Path(args.work), args.origin))
    summary = _summarize(evaluated["results"])
    evaluated["summary"] = summary
    out = Path("data/eval/photos.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evaluated, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
