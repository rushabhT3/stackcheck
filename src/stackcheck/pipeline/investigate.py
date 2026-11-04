"""Bounded agent: retry a stuck floor count in at most 3 validated steps.

The program runs R1; the model only picks the next action from a menu, and the
program checks every pick. The agent's text never reaches a finding.
"""

import asyncio
import logging
import time
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from stackcheck.config import Settings
from stackcheck.extract.photos import describe_photo
from stackcheck.extract.prompts import load_prompt
from stackcheck.llm.base import ModelClient
from stackcheck.llm.structured import ask_json
from stackcheck.models import (
    AgentStep,
    Detection,
    ExtractedImage,
    Finding,
    FloorCount,
    PhotoRecord,
    ProjectEvidence,
    QuarterEvidence,
)
from stackcheck.rules.checks import RuleSettings, r1_photo_vs_claim
from stackcheck.vision.floors import count_floors
from stackcheck.vision.overlay import draw_floor_overlay, write_overlay_thumb
from stackcheck.vision.sam import SamClient
from stackcheck.vision.sam_parse import parse_sam_output

logger = logging.getLogger(__name__)

ALLOWED_RETRY_PROMPTS = ["balcony", "window", "window opening", "concrete column", "floor slab"]
MAX_STEPS = 3


class AgentChoice(BaseModel):
    action: Literal["retry_sam_prompt", "use_other_photo", "crop_to_building", "give_up"]
    argument: str | None = None
    rationale: str = Field(max_length=240)


def needs_investigation(finding: Finding, photo: PhotoRecord) -> bool:
    """An R1 CANT_VERIFY with a claim, whose photo count failed by a photo-side cause.

    Disagreements and single-method counts qualify, as do ESTIMATE tiers
    (vision-only or methods differing by 2): retrying SAM may still confirm them.
    """
    if finding.rule != "R1_PHOTO_VS_CLAIM" or finding.status != "CANT_VERIFY":
        return False
    if finding.inputs.get("claimed_slabs") is None:
        return False
    floors = photo.floors
    if floors is None:
        return False
    if floors.status == "OK":
        return floors.tier == "ESTIMATE"
    return "only one method" in floors.reason or "disagree" in floors.reason


_TOOL_FAILURE_PREFIXES = ("SAM call failed", "crop failed", "vision call failed")
TOOL_UNOPENABLE = "We couldn't open that photo."
STOPPED_UNOPENABLE = "Stopped: the photo could not be opened."
MAX_CONSECUTIVE_TOOL_FAILURES = 2


def _is_tool_failure(result: str) -> bool:
    """True when a tool call failed instead of counting: never shown to users."""
    return result.startswith(_TOOL_FAILURE_PREFIXES)


def _image_path(images_dir: Path, photo: PhotoRecord) -> Path:
    """Resolve a photo file. image.path is relative to the check folder
    (see ExtractedImage), and images_dir is <check>/images, so relative
    paths resolve against its parent. Absolute paths pass through."""
    raw = Path(photo.image.path)
    if raw.is_absolute():
        return raw
    return images_dir.parent / raw


def _same_block(guess: str | None, block_label: str) -> bool:
    return guess == block_label or (block_label == "MAIN" and guess is None)


def _elevation_ids(quarter: QuarterEvidence, block_label: str, skip: str) -> list[str]:
    """Elevation photo ids for the quarter and block, excluding one photo."""
    ids = []
    for photo in quarter.photos:
        if photo.image.image_id == skip or photo.facts is None:
            continue
        if photo.facts.kind == "elevation" and _same_block(photo.facts.block_label_guess, block_label):
            ids.append(photo.image.image_id)
    return ids


def _r1_decided(quarter: QuarterEvidence, quarter_end: date | None, block_label: str) -> bool:
    """Re-run R1 on this quarter only; decided means no longer CANT_VERIFY."""
    project = ProjectEvidence(project_label="", quarters=[quarter])
    for finding in r1_photo_vs_claim(project, RuleSettings()):
        if finding.quarter_end == quarter_end and finding.block_label == block_label:
            return finding.status != "CANT_VERIFY"
    return False


class FindingBudget:
    """Per-finding shared state across its photo investigations.

    One finding can own several eligible photos; the agent must not repeat
    an action plus argument on any of them, stop after MAX_FINDING_STEPS
    trace steps, and write exactly one terminal line.
    """

    def __init__(self) -> None:
        self.used: set[tuple[str, str | None]] = set()
        self.total_steps = 0
        self.closed = False


MAX_FINDING_STEPS = 8
MAX_REPEAT_STRIKES = 2


def _validate(
    choice: AgentChoice,
    photo: PhotoRecord,
    other_ids: list[str],
) -> str | None:
    """None when the choice is usable, else the rejection result text."""
    if choice.action == "give_up":
        return None
    if choice.action == "retry_sam_prompt":
        if choice.argument in ALLOWED_RETRY_PROMPTS:
            return None
        return "invalid choice rejected by program"
    if choice.action == "use_other_photo":
        if choice.argument is not None and choice.argument in other_ids:
            return None
        return "invalid choice rejected by program"
    if choice.action == "crop_to_building":
        floors = photo.floors
        if choice.argument == photo.image.image_id and floors is not None and floors.building_box is not None:
            return None
        return "invalid choice rejected by program"
    return "invalid choice rejected by program"


def _step(
    n: int,
    finding: Finding,
    action: Literal["retry_sam_prompt", "use_other_photo", "crop_to_building", "give_up"],
    argument: str | None,
    rationale: str,
    result: str,
) -> AgentStep:
    return AgentStep(
        step=n,
        finding_id=finding.finding_id,
        action=action,
        argument=argument,
        rationale=rationale,
        result=result,
    )


async def _store(
    quarter: QuarterEvidence,
    image_id: str,
    count: FloorCount,
    detections: list[Detection],
    base: Path,
    media_dir: Path,
    overlay_stem: str | None = None,
) -> None:
    """Save a recount on the photo and redraw its overlay. Overlay failure clears it."""
    stem = overlay_stem or image_id
    for record in quarter.photos:
        if record.image.image_id != image_id:
            continue
        record.floors = count
        try:
            out = media_dir / f"{stem}.overlay.png"
            await asyncio.to_thread(draw_floor_overlay, base, count, detections, out)
            try:
                await asyncio.to_thread(write_overlay_thumb, out)
            except Exception as thumb_exc:
                logger.warning("Thumb failed for %s: %s", image_id, thumb_exc)
            record.overlay_path = f"media/{stem}.overlay.png"
        except Exception as exc:
            logger.warning("Overlay failed for %s: %s", image_id, exc)
            record.overlay_path = None


async def _execute_retry(
    *,
    photo: PhotoRecord,
    quarter: QuarterEvidence,
    prompt: str,
    building_dets: list[Detection],
    element_dets: dict[str, list[Detection]],
    tried: list[str],
    sam: SamClient,
    check_id: str | None,
    images_dir: Path,
    media_dir: Path,
) -> str:
    """Run SAM with a new prompt and recount this photo."""
    path = _image_path(images_dir, photo)
    retry_started = time.perf_counter()
    try:
        output = await sam.segment_image(path, prompt, check_id=check_id)
    except Exception as exc:
        return f"SAM call failed: {exc}"
    logger.info(
        "Investigate retry SAM %s on %s took %.1fs",
        prompt,
        photo.image.image_id,
        time.perf_counter() - retry_started,
    )
    dets = parse_sam_output(output, prompt)
    element_dets[prompt] = dets
    tried.append(prompt)
    facts = photo.facts
    assert facts is not None
    count = count_floors(photo.image.image_id, building_dets, dets, facts, ["building", prompt])
    await _store(quarter, photo.image.image_id, count, building_dets + dets, path, media_dir)
    return (
        f"recount with {prompt} gives {count.status} "
        f"(SAM rows {count.sam_rows}, visual {count.vision_estimate})"
    )


async def _execute_other(
    *,
    other_id: str,
    quarter: QuarterEvidence,
    sam: SamClient,
    settings: Settings,
    check_id: str | None,
    images_dir: Path,
    media_dir: Path,
) -> str:
    """Count floors on another elevation photo from the same quarter and block."""
    other = next(p for p in quarter.photos if p.image.image_id == other_id)
    assert other.facts is not None
    path = _image_path(images_dir, other)
    prompts = [p.strip() for p in settings.sam_prompts.split(",") if p.strip()]
    started = time.perf_counter()
    try:
        building_text = await sam.segment_image(path, "building", check_id=check_id)
    except Exception as exc:
        return f"SAM call failed: {exc}"
    logger.info(
        "Investigate other-photo SAM building on %s took %.1fs",
        other_id,
        time.perf_counter() - started,
    )
    building_dets = parse_sam_output(building_text, "building")

    async def _one(prompt: str) -> str:
        one_started = time.perf_counter()
        try:
            return await sam.segment_image(path, prompt, check_id=check_id)
        finally:
            logger.info(
                "Investigate other-photo SAM %s on %s took %.1fs",
                prompt,
                other_id,
                time.perf_counter() - one_started,
            )

    try:
        outputs = await asyncio.gather(*[_one(prompt) for prompt in prompts])
    except Exception as exc:
        return f"SAM call failed: {exc}"
    dets: list[Detection] = []
    for element_prompt, output in zip(prompts, outputs, strict=True):
        dets.extend(parse_sam_output(output, element_prompt))
    logger.info(
        "Investigate other-photo %s: %d SAM calls in %.1fs",
        other_id,
        len(prompts) + 1,
        time.perf_counter() - started,
    )
    count = count_floors(other_id, building_dets, dets, other.facts, ["building", *prompts])
    await _store(quarter, other_id, count, building_dets + dets, path, media_dir)
    return f"counted {other_id}: {count.status} (SAM rows {count.sam_rows}, visual {count.vision_estimate})"


async def _execute_crop(
    *,
    photo: PhotoRecord,
    quarter: QuarterEvidence,
    sam: SamClient,
    vision: ModelClient,
    settings: Settings,
    check_id: str | None,
    images_dir: Path,
    media_dir: Path,
    crops_dir: Path,
    step_index: int,
) -> str:
    """Crop to the building box plus 5% and rerun SAM and the vision count on the crop."""
    assert photo.floors is not None and photo.floors.building_box is not None
    assert photo.facts is not None
    box = photo.floors.building_box
    path = _image_path(images_dir, photo)
    try:
        raw = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        return f"crop failed: {exc}"
    with Image.open(BytesIO(raw)) as img:
        width, height = img.size
        pad_x = 0.05 * (box.x2 - box.x1)
        pad_y = 0.05 * (box.y2 - box.y1)
        frame = (
            max(0.0, box.x1 - pad_x),
            max(0.0, box.y1 - pad_y),
            min(float(width), box.x2 + pad_x),
            min(float(height), box.y2 + pad_y),
        )
        crop = img.crop(frame)
        crop_path = crops_dir / f"{photo.image.image_id}-crop{step_index}.png"
        crop.save(crop_path, format="PNG")
    prompts = [p.strip() for p in settings.sam_prompts.split(",") if p.strip()]
    started = time.perf_counter()
    try:
        building_text = await sam.segment_image(crop_path, "building", check_id=check_id)
    except Exception as exc:
        return f"SAM call failed: {exc}"
    logger.info(
        "Investigate crop SAM building on %s took %.1fs",
        photo.image.image_id,
        time.perf_counter() - started,
    )
    crop_building = parse_sam_output(building_text, "building")

    async def _one(prompt: str) -> str:
        one_started = time.perf_counter()
        try:
            return await sam.segment_image(crop_path, prompt, check_id=check_id)
        finally:
            logger.info(
                "Investigate crop SAM %s on %s took %.1fs",
                prompt,
                photo.image.image_id,
                time.perf_counter() - one_started,
            )

    try:
        outputs = await asyncio.gather(*[_one(prompt) for prompt in prompts])
    except Exception as exc:
        return f"SAM call failed: {exc}"
    crop_dets: list[Detection] = []
    for element_prompt, output in zip(prompts, outputs, strict=True):
        crop_dets.extend(parse_sam_output(output, element_prompt))
    crop_image = ExtractedImage(
        image_id=f"{photo.image.image_id}-crop{step_index}",
        doc_id=photo.image.doc_id,
        page=photo.image.page,
        index=photo.image.index,
        path=str(crop_path),
        width=crop.width,
        height=crop.height,
        sha256="",
    )
    describe_started = time.perf_counter()
    try:
        crop_facts = await describe_photo(crop_image, crop_path, quarter.filing, vision, check_id=check_id)
    except Exception as exc:
        return f"vision call failed on the crop: {exc}"
    logger.info(
        "Investigate crop vision describe for %s took %.1fs",
        photo.image.image_id,
        time.perf_counter() - describe_started,
    )
    logger.info(
        "Investigate crop %s: %d SAM calls plus vision in %.1fs",
        photo.image.image_id,
        len(prompts) + 1,
        time.perf_counter() - started,
    )
    transient = photo.facts.model_copy(update={"vision_storeys_visible": crop_facts.vision_storeys_visible})
    count = count_floors(photo.image.image_id, crop_building, crop_dets, transient, ["building", *prompts])
    stem = f"{photo.image.image_id}-crop{step_index}"
    await _store(quarter, photo.image.image_id, count, crop_building + crop_dets, crop_path, media_dir, stem)
    return f"recounted on crop: {count.status} (SAM rows {count.sam_rows}, visual {count.vision_estimate})"


async def investigate_finding(
    *,
    finding: Finding,
    quarter: QuarterEvidence,
    block_label: str,
    photo: PhotoRecord,
    building_dets: list[Detection],
    element_dets: dict[str, list[Detection]],
    tried_prompts: list[str],
    sam: SamClient,
    vision: ModelClient,
    settings: Settings,
    check_id: str | None,
    images_dir: Path,
    media_dir: Path,
    crops_dir: Path,
    shared: FindingBudget | None = None,
) -> list[AgentStep]:
    """Try up to 3 validated actions to decide one R1 CANT_VERIFY. Mutates the quarter."""
    if shared is not None and shared.closed:
        return []
    budget = shared if shared is not None else FindingBudget()
    budget.used.update(("retry_sam_prompt", name) for name in tried_prompts)
    await asyncio.to_thread(media_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(crops_dir.mkdir, parents=True, exist_ok=True)
    prompt = load_prompt("investigate_v1")
    tried = list(tried_prompts)
    elements = dict(element_dets)
    steps: list[AgentStep] = []
    history: list[str] = []
    consecutive_failures = 0
    repeat_strikes = 0
    decided = False
    while len(steps) < MAX_STEPS and budget.total_steps < MAX_FINDING_STEPS and not budget.closed:
        step_started = time.perf_counter()
        current = next(p for p in quarter.photos if p.image.image_id == photo.image.image_id)
        other_ids = _elevation_ids(quarter, block_label, photo.image.image_id)
        assert current.facts is not None
        text = prompt.fill(
            finding_title=finding.title,
            reason=finding.explanation,
            image_id=photo.image.image_id,
            quarter_end=finding.quarter_end.isoformat() if finding.quarter_end else "unknown",
            sam_rows=str(current.floors.sam_rows)
            if current.floors and current.floors.sam_rows is not None
            else "unknown",
            vision_estimate=str(current.floors.vision_estimate)
            if current.floors and current.floors.vision_estimate is not None
            else "unknown",
            top_obscured=str(current.facts.top_obscured),
            other_photos=", ".join(other_ids) if other_ids else "none",
            tried_prompts=", ".join(tried) if tried else "none",
            allowed_prompts=", ".join(ALLOWED_RETRY_PROMPTS),
            history="; ".join(history) if history else "none",
        )
        try:
            ask_started = time.perf_counter()
            choice, _ = await ask_json(
                vision,
                prompt_name="investigate_v1",
                system=prompt.system,
                text=text,
                schema=AgentChoice,
                purpose="investigate",
                check_id=check_id,
            )
            logger.info(
                "Investigate ask for %s step %d took %.1fs",
                photo.image.image_id,
                len(steps) + 1,
                time.perf_counter() - ask_started,
            )
        except Exception as exc:
            logger.warning("Investigator call failed: %s", exc)
            steps.append(_step(len(steps) + 1, finding, "give_up", None, "", "investigator call failed"))
            budget.closed = True
            break
        step_no = len(steps) + 1
        if choice.action == "give_up":
            steps.append(_step(step_no, finding, "give_up", choice.argument, choice.rationale, "gave up"))
            budget.closed = True
            break
        if (choice.action, choice.argument) in budget.used:
            repeat_strikes += 1
            logger.info(
                "Investigate repeat skipped for %s: %s %s (strike %d)",
                photo.image.image_id,
                choice.action,
                choice.argument,
                repeat_strikes,
            )
            history.append(
                f"{choice.action} {choice.argument or ''}: already tried that, pick a different action"
            )
            if repeat_strikes >= MAX_REPEAT_STRIKES:
                break
            continue
        repeat_strikes = 0
        rejection = _validate(choice, current, other_ids)
        if rejection is not None:
            steps.append(_step(step_no, finding, "give_up", choice.argument, choice.rationale, rejection))
            budget.closed = True
            break
        budget.used.add((choice.action, choice.argument))
        if choice.action == "retry_sam_prompt":
            assert choice.argument is not None
            result = await _execute_retry(
                photo=current,
                quarter=quarter,
                prompt=choice.argument,
                building_dets=building_dets,
                element_dets=elements,
                tried=tried,
                sam=sam,
                check_id=check_id,
                images_dir=images_dir,
                media_dir=media_dir,
            )
        elif choice.action == "use_other_photo":
            assert choice.argument is not None
            result = await _execute_other(
                other_id=choice.argument,
                quarter=quarter,
                sam=sam,
                settings=settings,
                check_id=check_id,
                images_dir=images_dir,
                media_dir=media_dir,
            )
        else:
            result = await _execute_crop(
                photo=current,
                quarter=quarter,
                sam=sam,
                vision=vision,
                settings=settings,
                check_id=check_id,
                images_dir=images_dir,
                media_dir=media_dir,
                crops_dir=crops_dir,
                step_index=step_no,
            )
        if _is_tool_failure(result):
            logger.warning(
                "Investigate tool failed for %s (check %s): %s",
                photo.image.image_id,
                check_id,
                result,
            )
            result = TOOL_UNOPENABLE
            consecutive_failures += 1
        else:
            consecutive_failures = 0
        steps.append(_step(step_no, finding, choice.action, choice.argument, choice.rationale, result))
        budget.total_steps += 1
        history.append(f"{choice.action} {choice.argument or ''}: {result}")
        if consecutive_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
            steps.append(
                _step(
                    step_no + 1,
                    finding,
                    "give_up",
                    choice.argument,
                    choice.rationale,
                    STOPPED_UNOPENABLE,
                )
            )
            budget.total_steps += 1
            budget.closed = True
            break
        logger.info(
            "Investigate step %d for %s (%s) took %.1fs",
            step_no,
            photo.image.image_id,
            choice.action,
            time.perf_counter() - step_started,
        )
        if _r1_decided(quarter, finding.quarter_end, block_label):
            decided = True
            budget.closed = True
            break
    if not decided and not budget.closed and (not steps or steps[-1].action != "give_up"):
        final = next(p for p in quarter.photos if p.image.image_id == photo.image.image_id)
        floors = final.floors
        if floors is not None:
            sam_part = str(floors.sam_rows) if floors.sam_rows is not None else "unknown"
            vision_part = str(floors.vision_estimate) if floors.vision_estimate is not None else "unknown"
            detail = f"SAM {sam_part} rows, vision {vision_part} floors"
        else:
            detail = "no count"
        steps.append(
            _step(
                len(steps) + 1,
                finding,
                "give_up",
                None,
                "",
                f"Concluded: methods disagree ({detail}). Left as can't verify.",
            )
        )
        budget.total_steps += 1
    budget.closed = True
    return steps
