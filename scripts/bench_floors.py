"""Benchmark floor counting on Commons photos (live: spends SAM + vision calls).

For each labelled image: SAM "building" plus each element prompt (all cached and
ledgered), `describe_photo` with a dummy filing, then `count_floors`. Reports
within1 / wrong_confident / abstain per prompt set and picks the best set.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path

from PIL import Image

from stackcheck.config import get_settings
from stackcheck.extract.photos import describe_photo
from stackcheck.llm.registry import vision_client
from stackcheck.models import Box, Detection, ExtractedImage, FloorCount, QuarterFiling
from stackcheck.vision.floors import count_floors
from stackcheck.vision.overlay import draw_floor_overlay
from stackcheck.vision.sam import SamClient
from stackcheck.vision.sam_parse import parse_sam_output


def _load_labels(path: Path) -> list[dict]:
    """Rows with a storey count; unlabelled rows are skipped, not scored."""
    labelled = []
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            storeys = (row.get("storeys_visible") or "").strip()
            if not storeys:
                continue
            obscured = (row.get("top_obscured") or "").strip().lower()
            labelled.append(
                {
                    "file": (row.get("file") or "").strip(),
                    "storeys": int(storeys),
                    "top_obscured": obscured == "true",
                }
            )
    return labelled


def _within1(count: FloorCount, label: int) -> bool | None:
    """Does the OK range contain a value within 1 of the label."""
    if count.status != "OK" or count.lower is None:
        return None
    if count.lower > label + 1:
        return False
    return count.upper is None or count.upper >= label - 1


def _distance(count: FloorCount, label: int) -> float | None:
    """Error size for an OK result; uses lower when upper is open."""
    if count.status != "OK" or count.lower is None:
        return None
    upper = count.upper if count.upper is not None else count.lower
    return min(abs(count.lower - label), abs(upper - label))


def _summarize(scored: list[dict]) -> dict:
    oks = [r for r in scored if r["status"] == "OK"]
    decided = [r for r in oks if r["within1"] is not None]
    wrong = [r for r in oks if r["distance"] is not None and r["distance"] >= 2]
    return {
        "n": len(scored),
        "within1": (sum(1 for r in decided if r["within1"]) / len(decided) if decided else None),
        "wrong_confident": len(wrong) / len(scored) if scored else None,
        "abstain": sum(1 for r in scored if r["status"] == "CANT_VERIFY") / len(scored) if scored else None,
    }


async def _evaluate_sets(
    images_dir: Path,
    labelled: list[dict],
    prompt_sets: list[list[str]],
    origin: str,
) -> tuple[dict, dict]:
    """Run every prompt set over every image. Returns (results, detections)."""
    settings = get_settings()
    sam = SamClient(settings, data_dir=settings.data_dir)
    vision = vision_client(settings, origin)  # type: ignore[arg-type]
    filing = QuarterFiling(doc_id="bench")
    results: dict[str, list[dict]] = {}
    detections: dict[str, dict[str, tuple[list[Detection], list[Detection]]]] = {}
    for prompts in prompt_sets:
        name = ",".join(prompts)
        results[name] = []
        detections[name] = {}
        element_dets: dict[str, list[Detection]] = {}
        for item in labelled:
            path = images_dir / item["file"]
            building_text = await sam.segment_image(path, "building", check_id="bench")
            building_dets = parse_sam_output(building_text, "building")
            for prompt in prompts:
                key = (item["file"], prompt)
                if key not in element_dets:
                    text = await sam.segment_image(path, prompt, check_id="bench")
                    element_dets[key] = parse_sam_output(text, prompt)
            dets = [d for prompt in prompts for d in element_dets[(item["file"], prompt)]]
            detections[name][item["file"]] = (building_dets, dets)
            with Image.open(path) as img:
                width, height = img.size
            data = await asyncio.to_thread(path.read_bytes)
            image = ExtractedImage(
                image_id=path.stem,
                doc_id="bench",
                page=1,
                index=0,
                path=str(path),
                width=width,
                height=height,
                sha256=hashlib.sha256(data).hexdigest(),
            )
            facts = await describe_photo(image, path, filing, vision, check_id="bench-floors")
            count = count_floors(path.stem, building_dets, dets, facts, ["building", *prompts])
            within1 = _within1(count, item["storeys"])
            results[name].append(
                {
                    "file": item["file"],
                    "label": item["storeys"],
                    "label_top_obscured": item["top_obscured"],
                    "status": count.status,
                    "tier": count.tier,
                    "lower": count.lower,
                    "upper": count.upper,
                    "sam_rows": count.sam_rows,
                    "sam_pitch_estimate": count.sam_pitch_estimate,
                    "vision": count.vision_estimate,
                    "within1": within1,
                    "distance": _distance(count, item["storeys"]),
                    "rows_y": count.rows_y,
                    "building_box": count.building_box.model_dump()
                    if count.building_box is not None
                    else None,
                    "reason": count.reason,
                }
            )
    return results, detections


def _pick_best(summaries: dict[str, dict]) -> str:
    """Highest within1 among sets with wrong_confident <= 0.05 (ties: lower abstain).

    Falls back to the lowest wrong_confident when no set meets 0.05.
    """
    eligible = {
        name: s
        for name, s in summaries.items()
        if s["wrong_confident"] is not None and s["wrong_confident"] <= 0.05
    }
    pool = eligible or summaries
    if eligible:
        return min(
            pool,
            key=lambda name: (
                -(pool[name]["within1"] if pool[name]["within1"] is not None else -1),
                pool[name]["abstain"] if pool[name]["abstain"] is not None else 1,
            ),
        )
    return min(
        pool,
        key=lambda name: pool[name]["wrong_confident"] if pool[name]["wrong_confident"] is not None else 1,
    )


def _worst(results: list[dict], limit: int = 6) -> list[dict]:
    """Wrong-and-confident first by error size, then abstentions."""
    wrong = sorted(
        (r for r in results if r["status"] == "OK" and not r["within1"]),
        key=lambda r: r["distance"] or 0,
        reverse=True,
    )
    abstained = [r for r in results if r["status"] == "CANT_VERIFY"]
    return (wrong + abstained)[:limit]


def main() -> int:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this live probe.")
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Benchmark floor counting.")
    parser.add_argument("--prompts", default=settings.sam_prompts)
    parser.add_argument("--labels", default="data/bench/labels.csv")
    parser.add_argument("--images", default="data/bench/images")
    parser.add_argument("--origin", default="url", choices=("url", "upload"))
    parser.add_argument(
        "--compare",
        default=None,
        help='Semicolon-separated prompt sets, e.g. "window,balcony;window,concrete column"',
    )
    args = parser.parse_args()
    if args.compare:
        prompt_sets = [
            [p.strip() for p in group.split(",") if p.strip()] for group in args.compare.split(";")
        ]
    else:
        prompt_sets = [[p.strip() for p in args.prompts.split(",") if p.strip()]]
    labelled = _load_labels(Path(args.labels))
    if not labelled:
        raise SystemExit(f"No labelled rows in {args.labels}")
    results, detections = asyncio.run(_evaluate_sets(Path(args.images), labelled, prompt_sets, args.origin))
    summaries = {name: _summarize(rows) for name, rows in results.items()}
    best = _pick_best(summaries)
    overlay_dir = Path("data/eval/bench_overlays")
    overlay_dir.mkdir(parents=True, exist_ok=True)
    overlaid = []
    for row in _worst(results[best]):
        _, dets = detections[best][row["file"]]
        count = FloorCount(
            image_id=Path(row["file"]).stem,
            status=row["status"],  # type: ignore[arg-type]
            lower=row["lower"],
            upper=row["upper"],
            sam_rows=row["sam_rows"],
            sam_pitch_estimate=row["sam_pitch_estimate"],
            vision_estimate=row["vision"],
            prompts_used=best.split(","),
            rows_y=row["rows_y"],
            building_box=Box(**row["building_box"]) if row["building_box"] is not None else None,
            reason=row["reason"],
        )
        out = draw_floor_overlay(
            Path(args.images) / row["file"],
            count,
            dets,
            overlay_dir / f"{Path(row['file']).stem}.png",
        )
        overlaid.append(str(out))
    payload = {
        "prompt_sets": {name: {"summary": summaries[name]} for name in results},
        "best": best,
        "results": results[best],
        "overlays": overlaid,
    }
    out_path = Path("data/eval/bench_floors.json")
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"{'prompts':<32}{'n':>4}{'within1':>9}{'wrong_conf':>11}{'abstain':>9}")
    for name, summary in summaries.items():
        mark = " BEST" if name == best else ""
        print(
            f"{name:<32}{summary['n']:>4}"
            f"{(summary['within1'] or 0):>9.3f}"
            f"{(summary['wrong_confident'] or 0):>11.3f}"
            f"{(summary['abstain'] or 0):>9.3f}{mark}"
        )
    print(f"\n{'photo':<10}{'label':>6}{'sam':>5}{'vision':>7}  status       reason")
    for row in results[best]:
        print(
            f"{row['file']:<10}{row['label']:>6}{row['sam_rows']!s:>5}"
            f"{row['vision']!s:>7}  {row['status']:<12} {row['reason']}"
        )
    abstained: dict[str, int] = {}
    for row in results[best]:
        if row["status"] != "OK":
            key = row["reason"].split(" (vision")[0].split(": SAM")[0]
            abstained[key] = abstained.get(key, 0) + 1
    print("\nabstained by reason:")
    for reason, count in sorted(abstained.items(), key=lambda item: -item[1]):
        print(f"  {count:>3}  {reason}")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
