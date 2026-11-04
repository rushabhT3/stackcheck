"""Spike: which noun phrases find repeated building elements. Live, cached, ledgered."""

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

from stackcheck.config import get_settings
from stackcheck.vision.sam import SamClient
from stackcheck.vision.sam_parse import parse_sam_output


async def _amain(files: list[Path], prompts: list[str]) -> None:
    settings = get_settings()
    client = SamClient(settings, data_dir=settings.data_dir)
    rows: dict[str, dict] = {}
    for prompt in prompts:
        counts: list[int] = []
        confidences: list[float] = []
        with_one, with_six = 0, 0
        for path in files:
            output = await client.segment_image(path, prompt, check_id=None)
            dets = parse_sam_output(output, prompt)
            counts.append(len(dets))
            confidences.extend(d.confidence for d in dets if d.confidence is not None)
            with_one += len(dets) >= 1
            with_six += len(dets) >= 6
        rows[prompt] = {
            "images_ge_1": with_one,
            "images_ge_6": with_six,
            "median_detections": statistics.median(counts),
            "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
        }
    out_dir = Path("data/eval")
    (out_dir / "spike_sam.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"{'prompt':<18}{'images>=1':>10}{'images>=6':>10}{'median':>9}{'mean_conf':>11}")
    for prompt, row in rows.items():
        print(
            f"{prompt:<18}{row['images_ge_1']:>10}{row['images_ge_6']:>10}"
            f"{row['median_detections']:>9}{row['mean_confidence']:>11}"
        )
    ranked = sorted(
        (p for p in prompts if p != "building"),
        key=lambda p: (rows[p]["images_ge_6"], rows[p]["mean_confidence"]),
        reverse=True,
    )
    winners = ranked[:2]
    print("RECOMMENDED_SAM_PROMPTS:", ",".join(winners))


def main() -> None:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this live probe.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--prompts", required=True)
    args = parser.parse_args()
    images = Path(args.images)
    files = sorted(p for p in images.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})[: args.limit]
    if not files:
        raise SystemExit(f"No images in {images}")
    Path("data/eval").mkdir(parents=True, exist_ok=True)
    asyncio.run(_amain(files, [p.strip() for p in args.prompts.split(",") if p.strip()]))


if __name__ == "__main__":
    sys.exit(main())
