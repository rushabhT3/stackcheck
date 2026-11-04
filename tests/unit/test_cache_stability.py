"""Cache stability: a rerun under a new check_id must hit the model cache.

Same content, different check_id: every ask text and image byte is identical,
so the second run should make zero SDK calls. If this fails, something
run-specific (check_id, timestamp, ordering, temperature) leaks into a key.
"""

import json
from datetime import date
from pathlib import Path

from stackcheck.config import Settings
from stackcheck.llm.meta import MetaClient
from stackcheck.models import Finding
from stackcheck.pipeline.investigate import investigate_finding
from stackcheck.vision.floors import count_floors
from stackcheck.vision.sam import SamClient
from tests.conftest import make_png
from tests.factories import building_detection, grid_detections, make_photo, make_quarter
from tests.fakes import FakeResponsesSDK, FakeSamSDK


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[arg-type]
        data_dir=tmp_path,
        meta_api_key="test",
        baseten_api_key="test",
        fireworks_api_key="test",
    )


def _cant_case(tmp_path: Path) -> tuple:
    png = make_png(640, 480, "building")
    path = tmp_path / "p1.png"
    path.write_bytes(png)
    photo = make_photo("p1")
    facts = photo.facts.model_copy(update={"vision_storeys_visible": 5})
    photo = photo.model_copy(
        update={"facts": facts, "image": photo.image.model_copy(update={"path": str(path)})}
    )
    floors = count_floors("p1", [building_detection()], grid_detections(2, 4), photo.facts, ["window"])
    assert (floors.status, floors.tier) == ("OK", "ESTIMATE")
    photo = photo.model_copy(update={"floors": floors})
    quarter = make_quarter(date(2025, 3, 31), slabs=5, photos=[photo], state="Bihar")
    finding = Finding(
        finding_id="R1_PHOTO_VS_CLAIM:2025-03-31:MAIN:0",
        rule="R1_PHOTO_VS_CLAIM",
        status="CANT_VERIFY",
        severity="info",
        quarter_end=date(2025, 3, 31),
        block_label="MAIN",
        title="Slabs claimed but photo shows fewer floors",
        explanation="The photo count does not confirm the claim.",
    )
    return photo, quarter, finding


def _vision(cache_dir: Path, settings: Settings, sdk: FakeResponsesSDK) -> MetaClient:
    return MetaClient(settings, settings.private_model, data_dir=cache_dir, sdk=sdk)


async def _run_once(
    tmp_path: Path,
    settings: Settings,
    cache_dir: Path,
    sdk: FakeResponsesSDK,
    check_id: str,
) -> None:
    photo, quarter, finding = _cant_case(tmp_path)
    sam = SamClient(settings, data_dir=tmp_path, sdk=FakeSamSDK(chunks=("<0f>",)))
    vision = _vision(cache_dir, settings, sdk)
    media_dir = tmp_path / "media"
    crops_dir = tmp_path / "crops"
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
        settings=settings,
        check_id=check_id,
        images_dir=tmp_path,
        media_dir=media_dir,
        crops_dir=crops_dir,
    )
    assert [s.action for s in steps] == ["give_up"]


async def test_investigate_rerun_with_new_check_id_hits_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    cache_dir = tmp_path / "cachehome"
    choice = json.dumps({"action": "give_up", "rationale": "nothing more to try"})
    sdk1 = FakeResponsesSDK(texts=[choice])
    await _run_once(tmp_path, settings, cache_dir, sdk1, "chk_first0000")
    assert len(sdk1.calls) == 1
    sdk2 = FakeResponsesSDK(texts=[choice])
    await _run_once(tmp_path, settings, cache_dir, sdk2, "chk_second000")
    assert len(sdk2.calls) == 0
