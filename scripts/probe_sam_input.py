"""Probe how SAM 3.1 wants the image passed. Refuses to run unless STACKCHECK_LIVE=1."""

import asyncio
import os
import sys
from pathlib import Path

from stackcheck.config import Settings, get_settings
from stackcheck.vision.sam import SamClient
from stackcheck.vision.sam_parse import parse_sam_output


async def _try(settings: Settings, image: Path, mode: str) -> str:
    settings.sam_image_mode = mode
    client = SamClient(settings, data_dir=settings.data_dir)
    return await client.segment_image(image, "building", check_id=None)


async def _amain(image: Path) -> None:
    settings = get_settings()
    try:
        output = await _try(settings, image, "data_url")
        print("SAM_IMAGE_MODE: data_url")
    except Exception as exc:
        print(f"data_url failed: {type(exc).__name__}: {str(exc)[:300]}")
        try:
            output = await _try(settings, image, "file_id")
            print("SAM_IMAGE_MODE: file_id")
        except Exception as exc2:
            print(f"file_id failed: {type(exc2).__name__}: {str(exc2)[:300]}")
            print("SAM_IMAGE_MODE: NONE")
            return
    print("OUTPUT_HEAD:", output[:300].replace("\n", "\\n"))
    print("DETECTIONS:", len(parse_sam_output(output, "building")))


def main(argv: list[str]) -> None:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this live probe.")
    if len(argv) != 2:
        raise SystemExit("Usage: probe_sam_input.py <image path>")
    image = Path(argv[1])
    if not image.exists():
        raise SystemExit(f"No such image: {image}")
    asyncio.run(_amain(image))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
