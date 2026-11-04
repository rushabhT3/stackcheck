"""One photo in, PhotoFacts out. The model describes; this module checks ranges."""

import asyncio
import hashlib
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from stackcheck.errors import IngestError
from stackcheck.extract.prompts import load_prompt
from stackcheck.llm.base import ImagePart, ModelClient
from stackcheck.llm.structured import ask_json
from stackcheck.models import ExtractedImage, IngestedDoc, PhotoFacts, QuarterFiling

logger = logging.getLogger(__name__)

EARLIEST_STAMP = date(2015, 1, 1)
MAX_STOREYS = 120


def photo_prompt_version() -> str:
    """Hash of the describe prompt: facts keyed on bytes plus this version."""
    prompt = load_prompt("photo_v1")
    return hashlib.sha256((prompt.system + "\n" + prompt.user_template).encode()).hexdigest()[:16]


def _facts_store_paths(data_dir: Path, sha: str, model: str, version: str) -> tuple[Path, Path]:
    safe_model = "".join(c if c.isalnum() or c in "-_." else "_" for c in model)
    base = data_dir / "facts" / safe_model / version / sha
    return base.with_suffix(".meta.json"), base.with_suffix(".photo.json")


def _load_cached_facts(data_dir: Path, sha: str, model: str) -> dict | None:
    """Stored facts for these exact image bytes, or None on any mismatch."""
    version = photo_prompt_version()
    meta_path, facts_path = _facts_store_paths(data_dir, sha, model, version)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        meta.get("prompt_version") != version
        or meta.get("model") != model
        or meta.get("image_sha256") != sha
        or not facts_path.exists()
    ):
        return None
    try:
        return json.loads(facts_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_cached_facts(data_dir: Path, sha: str, model: str, facts: PhotoFacts) -> None:
    version = photo_prompt_version()
    meta_path, facts_path = _facts_store_paths(data_dir, sha, model, version)
    payload = facts.model_dump(mode="json")
    payload.pop("image_id", None)
    facts_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps({"image_sha256": sha, "model": model, "prompt_version": version}),
        encoding="utf-8",
    )
    facts_path.write_text(json.dumps(payload), encoding="utf-8")


async def describe_cached(
    image: ExtractedImage,
    image_path: Path,
    filing: QuarterFiling,
    client: ModelClient,
    *,
    check_id: str | None,
    data_dir: Path,
    model: str,
) -> PhotoFacts:
    """Same bytes give the same facts: reuse stored facts across reruns.

    The store key is image bytes plus prompt version plus model only. Nothing
    per-run (no check id, no image id) goes in, so a rerun of a seen photo
    never calls the model. The stored record drops the old image id; the live
    one is set from the current image.
    """
    try:
        raw = await asyncio.to_thread(image_path.read_bytes)
    except OSError as exc:
        raise IngestError(f"Cannot read image {image_path}: {exc}") from exc
    sha = hashlib.sha256(raw).hexdigest()
    stored = await asyncio.to_thread(_load_cached_facts, data_dir, sha, model)
    if stored is not None:
        logger.info("Photo facts cache hit for image sha %s", sha[:12])
        facts = PhotoFacts.model_validate({**stored, "image_id": image.image_id})
        return _post_process(facts, image)
    facts = await describe_photo(image, image_path, filing, client, check_id=check_id)
    await asyncio.to_thread(_save_cached_facts, data_dir, sha, model, facts)
    return facts


def select_photos(doc: IngestedDoc, limit: int = 12) -> list[ExtractedImage]:
    """Largest images first, at most `limit`.

    When the filing has no extracted images, return one placeholder per page (up
    to `limit`) with `from_page_render` set. The pipeline renders the page, saves
    `images/<image_id>.png`, and fills in width, height and sha256.
    """
    if doc.images:
        by_area = sorted(doc.images, key=lambda img: img.width * img.height, reverse=True)
        return by_area[:limit]
    renders = []
    for page in doc.pages[:limit]:
        image_id = f"{doc.source.doc_id}-p{page.page}-i0"
        renders.append(
            ExtractedImage(
                image_id=image_id,
                doc_id=doc.source.doc_id,
                page=page.page,
                index=0,
                path=f"images/{image_id}.png",
                width=0,
                height=0,
                sha256="",
                from_page_render=True,
            )
        )
    return renders


def _post_process(facts: PhotoFacts, image: ExtractedImage) -> PhotoFacts:
    facts.image_id = image.image_id
    if facts.stamp_date is not None and not (
        EARLIEST_STAMP <= facts.stamp_date <= date.today() + timedelta(days=1)
    ):
        facts.stamp_date = None
    if facts.stamp_lat is not None and not -90 <= facts.stamp_lat <= 90:
        facts.stamp_lat = None
    if facts.stamp_lon is not None and not -180 <= facts.stamp_lon <= 180:
        facts.stamp_lon = None
    storeys = facts.vision_storeys_visible
    if facts.kind != "elevation" or (storeys is not None and not 0 <= storeys <= MAX_STOREYS):
        facts.vision_storeys_visible = None
    return facts


async def describe_photo(
    image: ExtractedImage,
    image_path: Path,
    filing: QuarterFiling,
    client: ModelClient,
    *,
    check_id: str | None,
) -> PhotoFacts:
    """Describe one photo. Never trust the model on ids, dates, places or counts."""
    prompt = load_prompt("photo_v1")
    quarter_end = filing.quarter_end.isoformat() if filing.quarter_end else "unknown"
    block_labels = ", ".join(b.block_label for b in filing.blocks) or "MAIN"
    text = prompt.fill(image_id=image.image_id, quarter_end=quarter_end, block_labels=block_labels)
    try:
        raw = await asyncio.to_thread(image_path.read_bytes)
    except OSError as exc:
        raise IngestError(f"Cannot read image {image_path}: {exc}") from exc
    facts, _ = await ask_json(
        client,
        prompt_name="photo_v1",
        system=prompt.system,
        text=text,
        schema=PhotoFacts,
        images=[ImagePart(data=raw, mime="image/png")],
        purpose="describe_photo",
        check_id=check_id,
    )
    return _post_process(facts, image)
