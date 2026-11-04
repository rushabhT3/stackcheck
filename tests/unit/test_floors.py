"""Unit tests for vision/floors.py. All pure: no I/O, no network."""

from stackcheck.models import Box, Detection, PhotoFacts, PhotoKind
from stackcheck.vision.floors import count_floors
from tests.factories import building_detection, grid_detections


def _facts(
    vision: int | None,
    kind: PhotoKind = "elevation",
    top_obscured: bool | None = None,
) -> PhotoFacts | None:
    if vision is None and kind == "elevation":
        return None
    return PhotoFacts(
        image_id="t",
        kind=kind,
        vision_storeys_visible=vision,
        top_obscured=top_obscured,
        confidence="high",
        reason="test",
    )


def test_agree_exact() -> None:
    count = count_floors("t", [building_detection()], grid_detections(5, 4), _facts(5), ["window"])
    assert count.status == "OK"
    assert (count.lower, count.upper, count.sam_rows) == (5, 5, 5)


def test_agree_off_by_one() -> None:
    count = count_floors("t", [building_detection()], grid_detections(5, 4), _facts(6), ["window"])
    assert count.status == "OK"
    assert (count.lower, count.upper) == (5, 6)


def test_disagree_by_two_is_estimate() -> None:
    """F1 tiers: methods differing by exactly 2 give an ESTIMATE, never a flag alone."""
    count = count_floors("t", [building_detection()], grid_detections(5, 4), _facts(7), ["window"])
    assert count.status == "OK"
    assert count.tier == "ESTIMATE"
    assert (count.lower, count.upper) == (5, 7)
    assert "estimate" in count.reason


def test_disagree_by_three_still_abstains() -> None:
    count = count_floors("t", [building_detection()], grid_detections(5, 4), _facts(8), ["window"])
    assert count.status == "CANT_VERIFY"
    assert "disagree" in count.reason


def test_vision_missing_abstains() -> None:
    count = count_floors("t", [building_detection()], grid_detections(5, 4), None, ["window"])
    assert count.status == "CANT_VERIFY"
    assert "only one method" in count.reason


def test_vision_only_is_estimate() -> None:
    """F1 tiers: vision alone (SAM found nothing usable) gives an ESTIMATE."""
    count = count_floors("t", [building_detection()], grid_detections(2, 4), _facts(5), ["window"])
    assert count.status == "OK"
    assert count.tier == "ESTIMATE"
    assert (count.lower, count.upper) == (5, 5)
    assert "estimate" in count.reason


def test_agree_is_confident() -> None:
    count = count_floors("t", [building_detection()], grid_detections(5, 4), _facts(5), ["window"])
    assert count.status == "OK"
    assert count.tier == "CONFIDENT"


def test_neither_method_abstains() -> None:
    count = count_floors(
        "t",
        [building_detection()],
        grid_detections(2, 4),
        PhotoFacts(
            image_id="t",
            kind="elevation",
            vision_storeys_visible=None,
            confidence="high",
            reason="test",
        ),
        ["window"],
    )
    assert count.status == "CANT_VERIFY"
    assert "not enough evidence" in count.reason


def test_no_detections_at_all() -> None:
    count = count_floors("t", [], [], None, ["window"])
    assert count.status == "CANT_VERIFY"
    assert "no building found" in count.reason


def test_top_obscured_opens_upper() -> None:
    count = count_floors(
        "t", [building_detection()], grid_detections(5, 4), _facts(5, top_obscured=True), ["window"]
    )
    assert count.status == "OK"
    assert (count.lower, count.upper) == (5, None)


def test_low_confidence_rows_ignored() -> None:
    building = Box(x1=0, y1=0, x2=200, y2=620)
    good = grid_detections(5, 4, building=building)
    bad = [
        d.model_copy(update={"box": Box(x1=d.box.x1, y1=d.box.y1 + 320, x2=d.box.x2, y2=d.box.y2 + 320)})
        for d in grid_detections(3, 4, building=building, conf=0.2)
    ]
    count = count_floors("t", [building_detection(building)], good + bad, _facts(5), ["window"])
    assert count.status == "OK"
    assert (count.lower, count.upper, count.sam_rows) == (5, 5, 5)


def test_detections_outside_building_ignored() -> None:
    building = Box(x1=0, y1=0, x2=200, y2=250)
    inside = grid_detections(5, 4, building=building)
    outside = [
        d.model_copy(update={"box": Box(x1=d.box.x1 + 400, y1=d.box.y1, x2=d.box.x2 + 400, y2=d.box.y2)})
        for d in grid_detections(2, 4, building=building)
    ]
    count = count_floors("t", [building_detection(building)], inside + outside, _facts(5), ["window"])
    assert count.status == "OK"
    assert count.sam_rows == 5


def test_narrow_building_keeps_single_rows() -> None:
    narrow = Box(x1=0, y1=0, x2=46, y2=220)
    count = count_floors(
        "t",
        [building_detection(narrow)],
        grid_detections(5, 1, building=narrow),
        _facts(5),
        ["window"],
    )
    assert count.status == "OK"
    assert count.sam_rows == 5


def test_inferred_building_box() -> None:
    count = count_floors("t", [], grid_detections(5, 4), _facts(5), ["window"])
    assert count.status == "OK"
    assert count.building_box_inferred is True
    assert count.sam_pitch_estimate is None


def test_pitch_estimate_computed() -> None:
    building = Box(x1=0, y1=0, x2=400, y2=400)
    dets: list[Detection] = []
    for r, cy in enumerate([40, 120, 200, 280, 360]):
        for c in range(4):
            x1 = 8 + c * 46
            dets.append(
                Detection(
                    prompt="window",
                    frame=0,
                    object_id=r * 4 + c,
                    box=Box(x1=x1, y1=cy - 10, x2=x1 + 30, y2=cy + 10),
                    confidence=0.9,
                    image_w=640,
                    image_h=480,
                )
            )
    count = count_floors("t", [building_detection(building)], dets, _facts(5), ["window"])
    assert count.status == "OK"
    assert count.sam_pitch_estimate == 5


def test_jittered_rows_still_group() -> None:
    dets: list[Detection] = []
    for r in range(5):
        for c in range(4):
            jitter = ((r * 4 + c) % 5 - 2) * 2
            x1 = 8 + c * 46
            y1 = 12 + r * 60 + jitter
            dets.append(
                Detection(
                    prompt="window",
                    frame=0,
                    object_id=r * 4 + c,
                    box=Box(x1=x1, y1=y1, x2=x1 + 30, y2=y1 + 20),
                    confidence=0.9,
                    image_w=640,
                    image_h=480,
                )
            )
    building = Box(x1=0, y1=0, x2=200, y2=400)
    count = count_floors("t", [building_detection(building)], dets, _facts(5), ["window"])
    assert count.status == "OK"
    assert count.sam_rows == 5


def test_non_elevation_vision_ignored() -> None:
    count = count_floors(
        "t",
        [building_detection()],
        grid_detections(5, 4),
        _facts(5, kind="floor_level"),
        ["window"],
    )
    assert count.status == "CANT_VERIFY"
    assert "only one method" in count.reason


def test_lone_detection_dropped() -> None:
    building = Box(x1=0, y1=0, x2=200, y2=620)
    dets = grid_detections(5, 4, building=building)
    lone = Detection(
        prompt="window",
        frame=0,
        object_id=99,
        box=Box(x1=50, y1=560, x2=80, y2=580),
        confidence=0.9,
        image_w=640,
        image_h=480,
    )
    count = count_floors("t", [building_detection(building)], [*dets, lone], _facts(5), ["window"])
    assert count.status == "OK"
    assert count.sam_rows == 5


def test_all_elements_outside_building() -> None:
    """No SAM rows inside the box, but vision counts: ESTIMATE, not abstain."""
    building = Box(x1=500, y1=400, x2=600, y2=470)
    count = count_floors("t", [building_detection(building)], grid_detections(5, 4), _facts(5), ["window"])
    assert count.status == "OK"
    assert count.tier == "ESTIMATE"
    assert (count.lower, count.upper) == (5, 5)


def test_low_conf_building_box_inferred() -> None:
    count = count_floors(
        "t",
        [building_detection(conf=0.1)],
        grid_detections(5, 4),
        _facts(5),
        ["window"],
    )
    assert count.status == "OK"
    assert count.building_box_inferred is True
