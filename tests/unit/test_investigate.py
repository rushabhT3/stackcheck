"""Unit tests for the bounded investigate agent. Fake vision + fake SAM, no network."""

import json
from datetime import date
from pathlib import Path

from stackcheck.config import Settings
from stackcheck.models import Box, Finding, PhotoRecord, QuarterEvidence
from stackcheck.pipeline.investigate import (
    FindingBudget,
    investigate_finding,
    needs_investigation,
)
from stackcheck.rules.checks import RuleSettings, r1_photo_vs_claim
from stackcheck.rules.engine import run_rules
from stackcheck.vision.floors import count_floors
from stackcheck.vision.sam import SamClient
from tests.conftest import make_png
from tests.factories import building_detection, grid_detections, make_photo, make_project, make_quarter
from tests.fakes import FakeModelClient, FakeSamSDK

Q1 = date(2025, 3, 31)
SETTINGS = RuleSettings()


def _sam_text(rows: list[tuple[int, int, int, int]], width: int = 640, height: int = 480) -> str:
    """One building box plus one box per row member. No confidences."""
    parts = [f"0<|box;x1=0;y1=0;x2={width};y2={height};w={width};h={height}|>"]
    for i, (x1, y1, x2, y2) in enumerate(rows, start=1):
        parts.append(f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height}|>")
    return "<0f>" + ",".join(parts)


def _row_boxes(n: int) -> list[tuple[int, int, int, int]]:
    boxes = []
    for r in range(n):
        cy = 100 + r * 90
        boxes.append((100, cy - 10, 160, cy + 10))
        boxes.append((300, cy - 10, 360, cy + 10))
    return boxes


GRID_5 = _sam_text(_row_boxes(5))
GRID_2 = _sam_text(_row_boxes(2))
GRID_4_SMALL = _sam_text(
    [
        (20, 20, 60, 40),
        (90, 20, 130, 40),
        (20, 60, 60, 80),
        (90, 60, 130, 80),
        (20, 100, 60, 120),
        (90, 100, 130, 120),
        (20, 128, 60, 148),
        (90, 128, 130, 148),
    ],
    200,
    150,
)


def _with_vision(photo: PhotoRecord, storeys: int) -> PhotoRecord:
    assert photo.facts is not None
    facts = photo.facts.model_copy(update={"vision_storeys_visible": storeys})
    return photo.model_copy(update={"facts": facts})


def _photo_with_cant_floors(tmp_path: Path) -> tuple[PhotoRecord, QuarterEvidence]:
    """Elevation photo, SAM 2 rows, vision 5: OK/ESTIMATE; R1 still CANT_VERIFY as a question."""
    png = make_png(640, 480, "building")
    path = tmp_path / "p1.png"
    path.write_bytes(png)
    photo = make_photo("p1")
    photo = _with_vision(photo, 5)
    photo = photo.model_copy(update={"image": photo.image.model_copy(update={"path": str(path)})})
    floors = count_floors("p1", [building_detection()], grid_detections(2, 4), photo.facts, ["window"])
    assert (floors.status, floors.tier) == ("OK", "ESTIMATE")
    photo = photo.model_copy(update={"floors": floors})
    quarter = make_quarter(Q1, slabs=5, photos=[photo], state="Bihar")
    return photo, quarter


def _r1_cant(quarter: QuarterEvidence):
    findings = r1_photo_vs_claim(make_project([quarter]), SETTINGS)
    assert len(findings) == 1 and findings[0].status == "CANT_VERIFY"
    return findings[0]


def _choice(action: str, argument: str | None, rationale: str = "try this") -> str:
    payload: dict[str, object] = {"action": action, "rationale": rationale}
    if argument is not None:
        payload["argument"] = argument
    return json.dumps(payload)


async def _run(
    tmp_path: Path,
    settings,
    sam_text: str,
    vision_texts: list[str],
    choice_texts: list[str],
    photo: PhotoRecord,
    quarter: QuarterEvidence,
    extra_photos: list[PhotoRecord] | None = None,
    images_dir: Path | None = None,
    media_dir: Path | None = None,
    crops_dir: Path | None = None,
) -> tuple[QuarterEvidence, list]:
    if extra_photos:
        images_subdir = tmp_path / "images"
        images_subdir.mkdir(exist_ok=True)
        for extra in extra_photos:
            (images_subdir / f"{extra.image.image_id}.png").write_bytes(make_png(640, 480, "building"))
        quarter = make_quarter(Q1, slabs=5, photos=[photo, *extra_photos], state="Bihar")
    finding = _r1_cant(quarter)
    current = next(p for p in quarter.photos if p.image.image_id == photo.image.image_id)
    sam = SamClient(settings, data_dir=tmp_path, sdk=FakeSamSDK(chunks=(sam_text,)))
    vision = FakeModelClient(choice_texts + vision_texts)
    building_dets = [building_detection(Box(x1=0, y1=0, x2=640, y2=480))]
    element_dets = {"window": grid_detections(2, 4)}
    steps = await investigate_finding(
        finding=finding,
        quarter=quarter,
        block_label="MAIN",
        photo=current,
        building_dets=building_dets,
        element_dets=element_dets,
        tried_prompts=["window"],
        sam=sam,
        vision=vision,
        settings=settings,
        check_id="chk_test",
        images_dir=images_dir if images_dir is not None else tmp_path / "images",
        media_dir=media_dir if media_dir is not None else tmp_path / "media",
        crops_dir=crops_dir if crops_dir is not None else tmp_path / "crops",
    )
    return quarter, steps


def _photo_facts_json(storeys: int) -> str:
    return json.dumps(
        {
            "image_id": "crop",
            "kind": "elevation",
            "confidence": "high",
            "reason": "four rows visible",
            "vision_storeys_visible": storeys,
        }
    )


async def test_retry_agrees_and_stops(tmp_path: Path, tmp_settings: Settings) -> None:
    photo, quarter = _photo_with_cant_floors(tmp_path)
    quarter, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_5,
        [],
        [_choice("retry_sam_prompt", "window opening")],
        photo,
        quarter,
    )
    assert len(steps) == 1
    assert steps[0].action == "retry_sam_prompt"
    assert steps[0].argument == "window opening"
    done = next(p for p in quarter.photos if p.image.image_id == "p1")
    assert done.floors is not None and done.floors.status == "OK"
    assert (tmp_path / "media" / "p1.overlay.png").exists()


async def test_other_quarter_photo_rejected(tmp_path: Path, tmp_settings: Settings) -> None:
    photo, quarter = _photo_with_cant_floors(tmp_path)
    _, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_5,
        [],
        [_choice("use_other_photo", "q9-photo")],
        photo,
        quarter,
    )
    assert len(steps) == 1
    assert steps[0].action == "give_up"
    assert steps[0].result == "invalid choice rejected by program"


async def test_three_useless_steps_then_stop(tmp_path: Path, tmp_settings: Settings) -> None:
    photo, quarter = _photo_with_cant_floors(tmp_path)
    _, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_2,
        [],
        [
            _choice("retry_sam_prompt", "balcony"),
            _choice("retry_sam_prompt", "concrete column"),
            _choice("retry_sam_prompt", "floor slab"),
            _choice("retry_sam_prompt", "balcony"),
        ],
        photo,
        quarter,
    )
    assert len(steps) == 4
    assert [s.action for s in steps] == ["retry_sam_prompt"] * 3 + ["give_up"]
    assert steps[-1].result.startswith("Concluded:")
    assert "Left as can't verify." in steps[-1].result


async def test_extra_key_ignored(tmp_path: Path, tmp_settings: Settings) -> None:
    photo, quarter = _photo_with_cant_floors(tmp_path)
    payload = {"action": "give_up", "rationale": "nothing left", "status": "FLAG"}
    _, steps = await _run(tmp_path, tmp_settings, GRID_5, [], [json.dumps(payload)], photo, quarter)
    assert len(steps) == 1
    assert steps[0].action == "give_up"
    decided = run_rules(make_project([quarter]), SETTINGS)
    r1 = [f for f in decided if f.rule == "R1_PHOTO_VS_CLAIM"]
    assert r1 and all(f.status == "CANT_VERIFY" for f in r1)


def _live_work(tmp_path: Path, image_id: str) -> tuple[Path, Path, Path]:
    """Live check-folder layout: work/images/<id>.png with a relative image path."""
    images = tmp_path / "work" / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / f"{image_id}.png").write_bytes(make_png(640, 480, "building"))
    return images, tmp_path / "work" / "media", tmp_path / "work" / "crops"


def _relative_path(photo: PhotoRecord, path: str) -> PhotoRecord:
    return photo.model_copy(update={"image": photo.image.model_copy(update={"path": path})})


async def test_relative_image_path_resolves_like_floors_step(tmp_path: Path, tmp_settings: Settings) -> None:
    """Live layout (image.path relative to the check folder) must recount, not fail."""
    images, media, crops = _live_work(tmp_path, "p1")
    photo, _ = _photo_with_cant_floors(tmp_path)
    photo = _relative_path(photo, "images/p1.png")
    quarter = make_quarter(Q1, slabs=5, photos=[photo], state="Bihar")
    _, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_5,
        [],
        [_choice("retry_sam_prompt", "window opening")],
        photo,
        quarter,
        images_dir=images,
        media_dir=media,
        crops_dir=crops,
    )
    assert len(steps) == 1
    assert "recount" in steps[0].result
    assert "failed" not in steps[0].result
    assert (media / "p1.overlay.png").exists()


async def test_two_failed_tools_stop_the_agent(tmp_path: Path, tmp_settings: Settings) -> None:
    """Two failed tool calls in a row stop the agent; the trace stays plain."""
    images = tmp_path / "work" / "images"
    images.mkdir(parents=True, exist_ok=True)
    photo, _ = _photo_with_cant_floors(tmp_path)
    photo = _relative_path(photo, "images/gone.png")
    quarter = make_quarter(Q1, slabs=5, photos=[photo], state="Bihar")
    _, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_5,
        [],
        [_choice("retry_sam_prompt", "balcony"), _choice("retry_sam_prompt", "concrete column")],
        photo,
        quarter,
        images_dir=images,
        media_dir=tmp_path / "work" / "media",
        crops_dir=tmp_path / "work" / "crops",
    )
    assert [s.action for s in steps] == ["retry_sam_prompt", "retry_sam_prompt", "give_up"]
    assert steps[0].result == "We couldn't open that photo."
    assert steps[1].result == "We couldn't open that photo."
    assert steps[2].result == "Stopped: the photo could not be opened."
    for step in steps:
        assert "/" not in step.result and "chk_" not in step.result


async def test_give_up_stops_immediately(tmp_path: Path, tmp_settings: Settings) -> None:
    photo, quarter = _photo_with_cant_floors(tmp_path)
    sam = SamClient(tmp_settings, data_dir=tmp_path, sdk=FakeSamSDK(chunks=(GRID_5,)))
    vision = FakeModelClient([_choice("give_up", None)])
    finding = _r1_cant(quarter)
    steps = await investigate_finding(
        finding=finding,
        quarter=quarter,
        block_label="MAIN",
        photo=photo,
        building_dets=[building_detection()],
        element_dets={"window": grid_detections(2, 4)},
        tried_prompts=["window"],
        sam=sam,
        vision=vision,
        settings=tmp_settings,
        check_id="chk_test",
        images_dir=tmp_path,
        media_dir=tmp_path / "media",
        crops_dir=tmp_path / "crops",
    )
    assert len(steps) == 1
    assert steps[0].action == "give_up"
    assert sam.sdk.calls == []


async def test_use_other_photo_counts_it(tmp_path: Path, tmp_settings: Settings) -> None:
    photo, quarter = _photo_with_cant_floors(tmp_path)
    other = _with_vision(make_photo("p2"), 5)
    quarter, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_5,
        [],
        [_choice("use_other_photo", "p2")],
        photo,
        quarter,
        extra_photos=[other],
    )
    assert len(steps) == 1
    assert steps[0].argument == "p2"
    done = next(p for p in quarter.photos if p.image.image_id == "p2")
    assert done.floors is not None and done.floors.status == "OK"


async def test_crop_to_building_recounts(tmp_path: Path, tmp_settings: Settings) -> None:
    photo, quarter = _photo_with_cant_floors(tmp_path)
    small = make_png(200, 150, "building")
    (tmp_path / "small.png").write_bytes(small)
    photo = photo.model_copy(
        update={"image": photo.image.model_copy(update={"path": str(tmp_path / "small.png")})}
    )
    floors = count_floors(
        "p1",
        [building_detection(box=None, conf=0.9)],
        grid_detections(2, 4),
        photo.facts,
        ["window"],
    )
    assert (floors.status, floors.tier) == ("OK", "ESTIMATE")
    photo = photo.model_copy(update={"floors": floors})
    quarter = make_quarter(Q1, slabs=4, photos=[photo], state="Bihar")
    quarter, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_4_SMALL,
        [_photo_facts_json(4)],
        [_choice("crop_to_building", "p1")],
        photo,
        quarter,
    )
    assert len(steps) == 1
    assert steps[0].action == "crop_to_building"


async def test_investigate_logs_per_call_wall_time(tmp_path: Path, tmp_settings: Settings, caplog) -> None:
    """Every model/SAM call inside investigate must log its wall time and cache hit."""
    import logging

    photo, quarter = _photo_with_cant_floors(tmp_path)
    with caplog.at_level(logging.INFO):
        _, steps = await _run(
            tmp_path,
            tmp_settings,
            GRID_5,
            [],
            [_choice("retry_sam_prompt", "window opening")],
            photo,
            quarter,
        )
    assert len(steps) == 1
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "took" in joined and "hit=" in joined


async def test_retry_path_rerun_hits_cache(tmp_path: Path, tmp_settings: Settings) -> None:
    """A retry-path investigate rerun under a new check_id must make zero SDK calls."""
    import json as _json

    from stackcheck.llm.meta import MetaClient
    from tests.fakes import FakeResponsesSDK

    choice = _json.dumps({"action": "retry_sam_prompt", "argument": "balcony", "rationale": "try"})
    give_up = _json.dumps({"action": "give_up", "rationale": "done"})
    cache_dir = tmp_path / "cachehome"

    async def _once(check_id: str, sdk: FakeResponsesSDK) -> None:
        photo, quarter = _photo_with_cant_floors(tmp_path)
        finding = _r1_cant(quarter)
        sam = SamClient(tmp_settings, data_dir=cache_dir, sdk=FakeSamSDK(chunks=(GRID_2,)))
        vision = MetaClient(tmp_settings, tmp_settings.private_model, data_dir=cache_dir, sdk=sdk)
        await investigate_finding(
            finding=finding,
            quarter=quarter,
            block_label="MAIN",
            photo=photo,
            building_dets=[building_detection()],
            element_dets={"window": grid_detections(2, 4)},
            tried_prompts=["window"],
            sam=sam,
            vision=vision,
            settings=tmp_settings,
            check_id=check_id,
            images_dir=tmp_path,
            media_dir=tmp_path / "media",
            crops_dir=tmp_path / "crops",
        )

    sdk1 = FakeResponsesSDK(texts=[choice, give_up])
    await _once("chk_first0000", sdk1)
    first_calls = len(sdk1.calls)
    assert first_calls > 0
    sdk2 = FakeResponsesSDK(texts=[choice, give_up])
    await _once("chk_second000", sdk2)
    assert len(sdk2.calls) == 0


async def test_same_photo_choice_never_runs_twice(tmp_path: Path, tmp_settings: Settings) -> None:
    """A repeat is skipped silently; two in a row end with the Concluded line."""
    photo, _ = _photo_with_cant_floors(tmp_path)
    other = _with_vision(make_photo("p2"), 5)
    _, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_2,
        [],
        [_choice("use_other_photo", "p2"), _choice("use_other_photo", "p2")],
        photo,
        make_quarter(Q1, slabs=5, photos=[photo], state="Bihar"),
        extra_photos=[other],
    )
    assert [s.action for s in steps] == ["use_other_photo", "give_up"]
    assert steps[0].result.startswith("counted p2")
    assert steps[1].result.startswith("Concluded:")


async def test_repeated_tried_prompt_never_runs_twice(tmp_path: Path, tmp_settings: Settings) -> None:
    """A prompt tried during floors is already spent: two strikes, then Concluded."""
    photo, _ = _photo_with_cant_floors(tmp_path)
    quarter = make_quarter(Q1, slabs=5, photos=[photo], state="Bihar")
    _, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_2,
        [],
        [_choice("retry_sam_prompt", "window"), _choice("retry_sam_prompt", "window")],
        photo,
        quarter,
    )
    assert [s.action for s in steps] == ["give_up"]
    assert steps[0].result.startswith("Concluded:")


async def test_give_up_on_step_three_ends_at_three(tmp_path: Path, tmp_settings: Settings) -> None:
    """Two executed actions plus a give_up produce exactly three trace steps."""
    photo, _ = _photo_with_cant_floors(tmp_path)
    quarter = make_quarter(Q1, slabs=5, photos=[photo], state="Bihar")
    _, steps = await _run(
        tmp_path,
        tmp_settings,
        GRID_2,
        [],
        [
            _choice("retry_sam_prompt", "balcony"),
            _choice("retry_sam_prompt", "concrete column"),
            _choice("give_up", None),
        ],
        photo,
        quarter,
    )
    assert [s.action for s in steps] == ["retry_sam_prompt", "retry_sam_prompt", "give_up"]
    assert steps[2].result == "gave up"


def test_needs_investigation_requires_claim() -> None:
    """Findings without a slab claim never start the investigator."""
    photo_cant = _with_vision(make_photo(), 5)
    floors = count_floors("p1", [building_detection()], grid_detections(2, 4), photo_cant.facts, ["window"])
    assert (floors.status, floors.tier) == ("OK", "ESTIMATE")
    photo_cant = photo_cant.model_copy(update={"floors": floors})
    base: dict[str, object] = {
        "finding_id": "R1:2025-03-31:MAIN:0",
        "rule": "R1_PHOTO_VS_CLAIM",
        "status": "CANT_VERIFY",
        "severity": "info",
        "title": "t",
        "explanation": "e",
    }
    assert needs_investigation(Finding(**base, inputs={}), photo_cant) is False
    assert needs_investigation(Finding(**base, inputs={"claimed_slabs": "5"}), photo_cant) is True


async def test_investigate_loop_end_to_end_no_exceptions(tmp_path: Path, tmp_settings: Settings) -> None:
    """Whole loop on live layout with fakes: 1 to 8 steps, shared budget, no repeats."""
    images = tmp_path / "work" / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / "p1.png").write_bytes(make_png(640, 480, "building"))
    (images / "p2.png").write_bytes(make_png(640, 480, "building"))
    photo1, _ = _photo_with_cant_floors(tmp_path)
    photo1 = _relative_path(photo1, "images/p1.png")
    photo2 = _with_vision(make_photo("p2"), 5)
    photo2 = _relative_path(photo2, "images/p2.png")
    quarter = make_quarter(Q1, slabs=5, photos=[photo1, photo2], state="Bihar")
    finding = _r1_cant(quarter)
    media = tmp_path / "work" / "media"
    crops = tmp_path / "work" / "crops"
    shared = FindingBudget()
    common: dict[str, object] = {
        "finding": finding,
        "quarter": quarter,
        "block_label": "MAIN",
        "building_dets": [building_detection(Box(x1=0, y1=0, x2=640, y2=480))],
        "element_dets": {"window": grid_detections(2, 4)},
        "tried_prompts": ["window"],
        "settings": tmp_settings,
        "check_id": "chk_test",
        "images_dir": images,
        "media_dir": media,
        "crops_dir": crops,
        "shared": shared,
    }
    sam1 = SamClient(tmp_settings, data_dir=tmp_path, sdk=FakeSamSDK(chunks=(GRID_2,)))
    vision1 = FakeModelClient(
        [
            _choice("use_other_photo", "p2"),
            _choice("retry_sam_prompt", "balcony"),
        ]
    )
    current1 = next(p for p in quarter.photos if p.image.image_id == "p1")
    steps1 = await investigate_finding(photo=current1, sam=sam1, vision=vision1, **common)
    sam2 = SamClient(tmp_settings, data_dir=tmp_path, sdk=FakeSamSDK(chunks=(GRID_2,)))
    vision2 = FakeModelClient([_choice("give_up", None)])
    current2 = next(p for p in quarter.photos if p.image.image_id == "p2")
    steps2 = await investigate_finding(photo=current2, sam=sam2, vision=vision2, **common)
    assert steps2 == []
    assert 1 <= len(steps1) <= 8
    pairs = [(s.action, s.argument) for s in steps1 if s.action != "give_up"]
    assert len(pairs) == len(set(pairs))


def test_needs_investigation_predicate() -> None:
    photo_ok = make_photo(lower=5, upper=None, top_obscured=True)
    photo_cant = _with_vision(make_photo(), 5)
    floors = count_floors("p1", [building_detection()], grid_detections(2, 4), photo_cant.facts, ["window"])
    photo_cant = photo_cant.model_copy(update={"floors": floors})
    quarter = make_quarter(Q1, slabs=7, photos=[photo_ok, photo_cant])
    findings = r1_photo_vs_claim(make_project([quarter]), SETTINGS)
    assert len(findings) == 1
    assert needs_investigation(findings[0], photo_cant) is True
    assert needs_investigation(findings[0], photo_ok) is False
    decided = findings[0].model_copy(update={"status": "OK"})
    assert needs_investigation(decided, photo_cant) is False
