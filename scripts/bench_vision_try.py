"""Try vision-count variants on bench photos (live: spends vision calls).

Variants (each trial prompt_name gets its own cache lane):
  a: photo_v1 text plus a storey-listing instruction, same schema.
  b: 3 center crops (1.00, 0.95, 0.90) with the photo_v1 text; median of the
     counts; abstain when the range exceeds 1.
  c: crop to the SAM building box from bench_floors.json, photo_v1 text.
Default photo set: the CANT_VERIFY files from data/eval/bench_floors.json.
Writes data/eval/vision_try_<variant>.json with per-photo vision counts.

Usage: uv run python scripts/bench_vision_try.py --variant a   # STACKCHECK_LIVE=1
"""

import argparse
import asyncio
import io
import json
import os
from pathlib import Path

from stackcheck.config import get_settings
from stackcheck.extract.photos import _post_process
from stackcheck.extract.prompts import load_prompt
from stackcheck.llm.base import ImagePart
from stackcheck.llm.registry import vision_client
from stackcheck.llm.structured import ask_json
from stackcheck.models import ExtractedImage, PhotoFacts, QuarterFiling

LIST_INSTRUCTION = (
    "\n\nBefore answering, work it out line by line: list the storeys you see "
    "from bottom to top, one per line, saying for each whether it is the ground "
    "floor, stilt parking, a normal floor or a roof room. Count them and put the "
    "total in vision_storeys_visible."
)


def _center_crop(png: bytes, scale: float) -> bytes:
    """Crop the center `scale` fraction. Pure CPU; run in a thread."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        width, height = img.size
        box = (
            (width - width * scale) / 2,
            (height - height * scale) / 2,
            (width + width * scale) / 2,
            (height + height * scale) / 2,
        )
        buf = io.BytesIO()
        img.crop(box).save(buf, format="PNG")
        return buf.getvalue()


def _box_crop(png: bytes, box: dict | None) -> bytes:
    if not box:
        return png
    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        width, height = img.size
        pad_x = 0.02 * width
        pad_y = 0.02 * height
        region = img.crop(
            (
                max(0.0, box["x1"] - pad_x),
                max(0.0, box["y1"] - pad_y),
                min(float(width), box["x2"] + pad_x),
                min(float(height), box["y2"] + pad_y),
            )
        )
        buf = io.BytesIO()
        region.save(buf, format="PNG")
        return buf.getvalue()


async def _one(prompt_name: str, text: str, png: bytes, client, filing, image_id: str):  # type: ignore[no-untyped-def]
    facts, _ = await ask_json(
        client,
        prompt_name=prompt_name,
        system=load_prompt("photo_v1").system,
        text=text,
        schema=PhotoFacts,
        images=[ImagePart(data=png, mime="image/png")],
        purpose="bench_vision_try",
        check_id="bench-try",
    )
    probe = ExtractedImage(
        image_id=image_id,
        doc_id="bench",
        page=1,
        index=0,
        path="",
        width=0,
        height=0,
        sha256="",
    )
    return _post_process(facts, probe).vision_storeys_visible


async def _run(variant: str, images_dir: Path, rows: list[dict]) -> list[dict]:
    settings = get_settings()
    client = vision_client(settings, "url")  # type: ignore[arg-type]
    filing = QuarterFiling(doc_id="bench")
    base_prompt = load_prompt("photo_v1")
    out = []
    for row in rows:
        path = images_dir / row["file"]
        raw = await asyncio.to_thread(path.read_bytes)
        text = base_prompt.fill(image_id=row["file"], quarter_end="unknown", block_labels="MAIN")
        if variant == "a":
            value = await _one("photo_try_a", text + LIST_INSTRUCTION, raw, client, filing, row["file"])
            out.append({"file": row["file"], "label": row["label"], "vision_a": value})
        elif variant == "c":
            cropped = await asyncio.to_thread(_box_crop, raw, row["building_box"])
            value = await _one("photo_try_c", text, cropped, client, filing, row["file"])
            out.append({"file": row["file"], "label": row["label"], "vision_c": value})
        else:
            votes = []
            for scale in (1.00, 0.95, 0.90):
                cropped = await asyncio.to_thread(_center_crop, raw, scale)
                value = await _one("photo_try_b", text, cropped, client, filing, row["file"])
                if value is not None:
                    votes.append(value)
            votes.sort()
            median = votes[len(votes) // 2] if votes else None
            kept = median if votes and votes[-1] - votes[0] <= 1 else None
            out.append({"file": row["file"], "label": row["label"], "votes": votes, "vision_b": kept})
            value = kept
        print(f"{row['file']:<10} label={row['label']:>3} vision={value}", flush=True)
    return out


def main() -> int:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this live probe.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("a", "b", "c"), required=True)
    parser.add_argument("--images", default="data/bench/images")
    parser.add_argument("--bench", default="data/eval/bench_floors.json")
    parser.add_argument("--all", action="store_true", help="All 25 photos, not just abstained ones.")
    parser.add_argument("--files", default=None, help="Comma-separated file names to run (chunking).")
    args = parser.parse_args()
    bench = json.loads(Path(args.bench).read_text(encoding="utf-8"))
    rows = bench["results"]
    if not args.all:
        rows = [r for r in rows if r["status"] != "OK"]
    if args.files:
        wanted = {f.strip() for f in args.files.split(",") if f.strip()}
        rows = [r for r in rows if r["file"] in wanted]
    out = asyncio.run(_run(args.variant, Path(args.images), rows))
    out_path = Path(f"data/eval/vision_try_{args.variant}.json")
    if out_path.exists():
        merged = {r["file"]: r for r in json.loads(out_path.read_text(encoding="utf-8"))}
        merged.update({r["file"]: r for r in out})
        out = [merged[k] for k in sorted(merged)]
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} ({len(out)} photos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
