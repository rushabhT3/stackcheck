"""Count visible storeys from SAM detections plus the model's own count.

Pure: no I/O. Two weak counters that fail differently make one strong counter,
but only when they agree within tolerance; otherwise the answer is CANT_VERIFY.
"""

import itertools
import logging
import statistics

from stackcheck.models import Box, Detection, FloorCount, PhotoFacts

logger = logging.getLogger(__name__)

MIN_CONF = 0.35
BUILDING_MIN_CONF = 0.30
ROW_GAP_FACTOR = 0.6
MIN_ROW_MEMBERS = 2
MIN_ROWS = 3
AGREE_TOLERANCE = 1
ESTIMATE_TOLERANCE = 2  # F1: methods differing by exactly 2 give an ESTIMATE
ESTIMATE_FLAG_MARGIN = 3  # F1: R1 flags from an ESTIMATE only at a gap of 3 or more


def pick_building_box(
    building_dets: list[Detection], element_dets: list[Detection]
) -> tuple[Box | None, bool]:
    """Largest confident building box, else the padded span of all elements.

    Returns the box and whether it was inferred. Missing confidence counts as
    passing. No detections at all gives (None, True); the caller abstains.
    """
    candidates = [d for d in building_dets if d.confidence is None or d.confidence >= BUILDING_MIN_CONF]
    if candidates:
        return max(candidates, key=lambda d: d.box.area).box, False
    if not element_dets:
        return None, True
    pad_x = 0.02 * element_dets[0].image_w
    pad_y = 0.02 * element_dets[0].image_h
    return Box(
        x1=min(d.box.x1 for d in element_dets) - pad_x,
        y1=min(d.box.y1 for d in element_dets) - pad_y,
        x2=max(d.box.x2 for d in element_dets) + pad_x,
        y2=max(d.box.y2 for d in element_dets) + pad_y,
    ), True


def group_rows(element_dets: list[Detection], building: Box) -> list[list[Detection]]:
    """Group element detections into horizontal rows inside the building box."""
    kept = [
        d
        for d in element_dets
        if (d.confidence is None or d.confidence >= MIN_CONF)
        and building.x1 <= d.box.cx <= building.x2
        and building.y1 <= d.box.cy <= building.y2
        and 0.005 * building.height <= d.box.height <= 0.25 * building.height
    ]
    if not kept:
        return []
    h_med = statistics.median(d.box.height for d in kept)
    rows = []
    means = []
    for det in sorted(kept, key=lambda d: d.box.cy):
        if rows and det.box.cy - means[-1] <= ROW_GAP_FACTOR * h_med:
            rows[-1].append(det)
            members = rows[-1]
            means[-1] = sum(d.box.cy for d in members) / len(members)
        else:
            rows.append([det])
            means.append(det.box.cy)
    narrow = building.width < 3 * statistics.median(d.box.width for d in kept)
    return [row for row in rows if len(row) >= MIN_ROW_MEMBERS or narrow]


def count_floors(
    image_id: str,
    building_dets: list[Detection],
    element_dets: list[Detection],
    facts: PhotoFacts | None,
    prompts_used: list[str],
) -> FloorCount:
    """Agree SAM rows with the visual count, or abstain with a reason."""
    building, inferred = pick_building_box(building_dets, element_dets)
    if building is None:
        return FloorCount(
            image_id=image_id,
            status="CANT_VERIFY",
            prompts_used=list(prompts_used),
            building_box_inferred=True,
            reason="no building found",
        )
    rows = group_rows(element_dets, building)
    sam_rows = len(rows)
    rows_y = sorted(sum(d.box.cy for d in row) / len(row) for row in rows)
    pitch = None
    if len(rows_y) >= 2 and not inferred:
        gaps = [later - earlier for earlier, later in itertools.pairwise(rows_y)]
        pitch = round(building.height / statistics.median(gaps))
    vision = facts.vision_storeys_visible if facts is not None and facts.kind == "elevation" else None
    sam_counts = sam_rows >= MIN_ROWS
    if not sam_counts and vision is None:
        return FloorCount(
            image_id=image_id,
            status="CANT_VERIFY",
            sam_rows=sam_rows,
            vision_estimate=vision,
            prompts_used=list(prompts_used),
            rows_y=rows_y,
            building_box=building,
            building_box_inferred=inferred,
            reason="not enough evidence in this photo",
        )
    if vision is not None and not sam_counts:
        return FloorCount(
            image_id=image_id,
            status="OK",
            tier="ESTIMATE",
            lower=vision,
            upper=vision,
            sam_rows=sam_rows,
            vision_estimate=vision,
            prompts_used=list(prompts_used),
            rows_y=rows_y,
            building_box=building,
            building_box_inferred=inferred,
            reason=f"about {vision} floors (estimate); SAM found no rows",
        )
    if not sam_counts or vision is None:
        method, value = ("sam", sam_rows) if sam_counts else ("vision", vision)
        return FloorCount(
            image_id=image_id,
            status="CANT_VERIFY",
            sam_rows=sam_rows,
            vision_estimate=vision,
            prompts_used=list(prompts_used),
            rows_y=rows_y,
            building_box=building,
            building_box_inferred=inferred,
            reason=f"only one method produced a count ({method} = {value})",
        )
    if abs(sam_rows - vision) <= AGREE_TOLERANCE:
        lower, upper = min(sam_rows, vision), max(sam_rows, vision)
        assert facts is not None  # vision only comes from facts
        if facts.top_obscured:
            upper = None
        return FloorCount(
            image_id=image_id,
            status="OK",
            tier="CONFIDENT",
            lower=lower,
            upper=upper,
            sam_rows=sam_rows,
            sam_pitch_estimate=pitch,
            vision_estimate=vision,
            prompts_used=list(prompts_used),
            rows_y=rows_y,
            building_box=building,
            building_box_inferred=inferred,
            reason=f"methods agree: SAM rows {sam_rows}, visual count {vision}",
        )
    if abs(sam_rows - vision) == ESTIMATE_TOLERANCE:
        lower, upper = min(sam_rows, vision), max(sam_rows, vision)
        return FloorCount(
            image_id=image_id,
            status="OK",
            tier="ESTIMATE",
            lower=lower,
            upper=upper,
            sam_rows=sam_rows,
            sam_pitch_estimate=pitch,
            vision_estimate=vision,
            prompts_used=list(prompts_used),
            rows_y=rows_y,
            building_box=building,
            building_box_inferred=inferred,
            reason=(
                f"methods differ by 2 (SAM rows {sam_rows}, visual count {vision}); "
                f"about {lower} to {upper} floors (estimate)"
            ),
        )
    return FloorCount(
        image_id=image_id,
        status="CANT_VERIFY",
        sam_rows=sam_rows,
        vision_estimate=vision,
        prompts_used=list(prompts_used),
        rows_y=rows_y,
        building_box=building,
        building_box_inferred=inferred,
        reason=f"methods disagree: SAM rows {sam_rows}, visual count {vision}",
    )
