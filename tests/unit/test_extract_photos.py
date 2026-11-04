"""T06: photo selection and description post-processing (fake client, no network)."""

import json
from datetime import date
from pathlib import Path

from stackcheck.extract.photos import describe_cached, describe_photo, select_photos
from stackcheck.models import (
    BlockProgress,
    ExtractedImage,
    IngestedDoc,
    PageText,
    QuarterFiling,
    SourceDoc,
)
from tests.conftest import make_png
from tests.fakes import FakeModelClient

DOC_ID = "0123456789abcdef"


def _source() -> SourceDoc:
    return SourceDoc(
        doc_id=DOC_ID,
        origin="url",
        url=None,
        filename="q.pdf",
        sha256="ab" * 32,
        retrieved_at="2025-04-01T00:00:00+00:00",
        page_count=3,
    )


def _image(image_id: str, width: int, height: int, render: bool = False) -> ExtractedImage:
    return ExtractedImage(
        image_id=image_id,
        doc_id=DOC_ID,
        page=1,
        index=0,
        path=f"images/{image_id}.png",
        width=width,
        height=height,
        sha256="cd" * 32,
        from_page_render=render,
    )


def _doc(images: list[ExtractedImage], pages: int = 3) -> IngestedDoc:
    return IngestedDoc(
        source=_source(),
        pages=[PageText(doc_id=DOC_ID, page=n + 1, text="t") for n in range(pages)],
        images=images,
        is_scanned=False,
    )


def _filing() -> QuarterFiling:
    return QuarterFiling(
        doc_id=DOC_ID,
        quarter_end=date(2025, 3, 31),
        blocks=[BlockProgress(block_label="TOWER A")],
    )


def _facts_json(**overrides: object) -> str:
    facts = {
        "image_id": "wrong-id",
        "kind": "elevation",
        "confidence": "high",
        "reason": "three floor rows visible",
    }
    facts.update(overrides)
    return json.dumps(facts)


async def _describe(tmp_path: Path, payload: str) -> object:
    image_path = tmp_path / "img.png"
    image_path.write_bytes(make_png(320, 240, "building"))
    image = _image("img-1", 320, 240)
    return await describe_photo(image, image_path, _filing(), FakeModelClient(payload), check_id=None)


def test_select_photos_largest_first_capped() -> None:
    images = [_image(f"img-{n}", 100 + n * 10, 100) for n in range(13)]
    selected = select_photos(_doc(images))
    assert len(selected) == 12
    areas = [img.width * img.height for img in selected]
    assert areas == sorted(areas, reverse=True)
    assert selected[0].image_id == "img-12"


def test_select_photos_uses_page_renders_without_extracted_images() -> None:
    selected = select_photos(_doc([]))
    assert len(selected) == 3
    assert all(img.from_page_render for img in selected)
    assert [img.page for img in selected] == [1, 2, 3]


async def test_prompt_carries_id_quarter_and_blocks(tmp_path: Path) -> None:
    fake = FakeModelClient(_facts_json())
    image_path = tmp_path / "img.png"
    image_path.write_bytes(make_png(320, 240, "building"))
    await describe_photo(_image("img-9", 320, 240), image_path, _filing(), fake, check_id=None)
    text = fake.calls[0]["text"]
    assert "img-9" in text
    assert "2025-03-31" in text
    assert "TOWER A" in text


async def test_image_id_overwritten_with_real_one(tmp_path: Path) -> None:
    facts = await _describe(tmp_path, _facts_json())
    assert facts.image_id == "img-1"


async def test_future_and_old_stamp_dates_become_none(tmp_path: Path) -> None:
    assert (await _describe(tmp_path, _facts_json(stamp_date="2031-06-01"))).stamp_date is None
    assert (await _describe(tmp_path, _facts_json(stamp_date="2013-05-01"))).stamp_date is None


async def test_storeys_kept_only_for_elevations(tmp_path: Path) -> None:
    facts = await _describe(tmp_path, _facts_json(kind="floor_level", vision_storeys_visible=5))
    assert facts.vision_storeys_visible is None
    kept = await _describe(tmp_path, _facts_json(kind="elevation", vision_storeys_visible=5))
    assert kept.vision_storeys_visible == 5


async def test_out_of_range_latitude_becomes_none(tmp_path: Path) -> None:
    facts = await _describe(tmp_path, _facts_json(stamp_lat=123.0, stamp_lon=77.0, vision_storeys_visible=4))
    assert facts.stamp_lat is None
    assert facts.stamp_lon == 77.0


async def _cached(tmp_path: Path, image_id: str, payload: str, client) -> object:
    _ = payload
    image_path = tmp_path / f"{image_id}.png"
    image_path.write_bytes(make_png(320, 240, "building"))
    image = _image(image_id, 320, 240)
    return await describe_cached(
        image,
        image_path,
        _filing(),
        client,
        check_id=None,
        data_dir=tmp_path,
        model="muse-test",
    )


async def test_cached_describe_reuses_facts_without_new_call(tmp_path: Path) -> None:
    """Same bytes under a new image id: no SDK call, id remapped to the new name."""
    first = FakeModelClient(_facts_json())
    facts = await _cached(tmp_path, "img-a", _facts_json(), first)
    assert facts.image_id == "img-a"
    second = FakeModelClient(_facts_json())
    again = await _cached(tmp_path, "img-b", _facts_json(), second)
    assert again.image_id == "img-b"
    assert len(second.calls) == 0


async def test_cached_describe_version_mismatch_recomputes(tmp_path: Path) -> None:
    import hashlib

    from stackcheck.extract.photos import _facts_store_paths, photo_prompt_version
    from tests.conftest import make_png as _png

    first = FakeModelClient(_facts_json())
    await _cached(tmp_path, "img-a", _facts_json(), first)
    sha = hashlib.sha256(_png(320, 240, "building")).hexdigest()
    meta_path, _ = _facts_store_paths(tmp_path, sha, "muse-test", photo_prompt_version())
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["prompt_version"] = "tampered"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    second = FakeModelClient(_facts_json())
    await _cached(tmp_path, "img-a", _facts_json(), second)
    assert len(second.calls) == 1
