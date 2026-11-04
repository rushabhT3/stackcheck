"""Match an incoming site photo against filing photos from previous checks."""

import json
import logging
from pathlib import Path

from pydantic import BaseModel

from stackcheck.vision.phash import hamming

logger = logging.getLogger(__name__)

REUSED_DISTANCE = 6  # same threshold as R2


class PhotoHit(BaseModel):
    image_id: str
    doc_id: str
    page: int
    quarter_end: str | None = None
    distance: int = 0


def _quarter_end(work_dir: Path, doc_id: str) -> str | None:
    path = work_dir / "facts" / f"{doc_id}.filing.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("quarter_end")
    except (OSError, ValueError):
        return None


def find_matching_photo(data_dir: Path, digest: str) -> PhotoHit | None:
    """Best stored photo within the R2 threshold, else None. Reads facts only."""
    work = data_dir / "work"
    if not work.is_dir():
        return None
    best: PhotoHit | None = None
    for photo_path in sorted(work.glob("*/facts/*.photo.json")):
        try:
            record = json.loads(photo_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stored = record.get("dhash") or ""
        if len(stored) != 16:
            continue
        try:
            distance = hamming(digest, stored)
        except ValueError:
            continue
        if distance > REUSED_DISTANCE:
            continue
        image = record.get("image") or {}
        hit = PhotoHit(
            image_id=str(image.get("image_id", "")),
            doc_id=str(image.get("doc_id", "")),
            page=int(image.get("page", 1) or 1),
            quarter_end=_quarter_end(photo_path.parent.parent, str(image.get("doc_id", ""))),
            distance=distance,
        )
        if best is None or hit.distance < best.distance:
            best = hit
    return best
