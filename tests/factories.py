"""Builders for vision test fixtures: detections arranged in neat grids."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal

from stackcheck.models import (
    ActivityProgress,
    BlockProgress,
    Box,
    Detection,
    ExtractedImage,
    Financials,
    FloorCount,
    PhotoFacts,
    PhotoKind,
    PhotoRecord,
    ProjectEvidence,
    QuarterEvidence,
    QuarterFiling,
    SourceDoc,
)

IMAGE_W = 640
IMAGE_H = 480
X0 = 8
Y0 = 12
X_GAP = 16
Y_GAP = 24


def building_detection(
    box: Box | None = None,
    conf: float | None = 0.9,
    prompt: str = "building",
) -> Detection:
    """One building detection; the default box spans a comfortable area."""
    return Detection(
        prompt=prompt,
        frame=0,
        object_id=0,
        box=box if box is not None else Box(x1=0, y1=0, x2=400, y2=400),
        confidence=conf,
        image_w=IMAGE_W,
        image_h=IMAGE_H,
    )


def grid_detections(
    rows: int,
    cols: int,
    building: Box | None = None,
    h: float = 20,
    w: float = 30,
    conf: float | None = 0.9,
    prompt: str = "window",
) -> list[Detection]:
    """Boxes in a regular grid that fits inside `building` (built to fit by default).

    Row pitch is `h + 24`, comfortably above the row-gap threshold, so each
    printed row groups into exactly one SAM row.
    """
    x_pitch = w + X_GAP
    y_pitch = h + Y_GAP
    dets = []
    for r in range(rows):
        for c in range(cols):
            x1 = X0 + c * x_pitch
            y1 = Y0 + r * y_pitch
            dets.append(
                Detection(
                    prompt=prompt,
                    frame=0,
                    object_id=r * cols + c,
                    box=Box(x1=x1, y1=y1, x2=x1 + w, y2=y1 + h),
                    confidence=conf,
                    image_w=IMAGE_W,
                    image_h=IMAGE_H,
                )
            )
    if building is None:
        building = Box(
            x1=0,
            y1=0,
            x2=X0 + (cols - 1) * x_pitch + w + X0,
            y2=Y0 + (rows - 1) * y_pitch + h + Y0,
        )
    assert all(
        building.x1 <= d.box.cx <= building.x2 and building.y1 <= d.box.cy <= building.y2 for d in dets
    ), "grid must fit inside the building box"
    return dets


def make_photo(
    image_id: str = "p1",
    kind: PhotoKind = "elevation",
    lower: int | None = None,
    upper: int | None = None,
    status: Literal["OK", "CANT_VERIFY"] = "OK",
    stamp: date | None = None,
    dhash: str | None = "0" * 16,
    block_guess: str | None = None,
    top_obscured: bool | None = False,
    confidence: Literal["low", "medium", "high"] = "high",
    width: int = 100,
    height: int = 100,
    tier: Literal["CONFIDENT", "ESTIMATE"] | None = None,
) -> PhotoRecord:
    """One photo with facts, a floor count and a perceptual hash."""
    facts = PhotoFacts(
        image_id=image_id,
        kind=kind,
        block_label_guess=block_guess,
        stamp_date=stamp,
        top_obscured=top_obscured,
        confidence=confidence,
        reason="test",
    )
    floors = FloorCount(
        image_id=image_id,
        status=status,
        tier=tier,
        lower=lower,
        upper=upper,
        reason="test",
    )
    image = ExtractedImage(
        image_id=image_id,
        doc_id="d",
        page=1,
        index=0,
        path=f"images/{image_id}.png",
        width=width,
        height=height,
        sha256="0" * 64,
    )
    return PhotoRecord(image=image, facts=facts, dhash=dhash, floors=floors)


def make_quarter(
    quarter_end: date | None,
    slabs: int | None = None,
    percent: float | None = None,
    floors: int | None = None,
    activities: dict[str, float | None] | None = None,
    fin: dict | None = None,
    photos: Sequence[PhotoRecord] = (),
    sha: str = "...",
    state: str | None = "Bihar",
    block: str = "MAIN",
    filed_on: date | None = None,
) -> QuarterEvidence:
    """One quarter of evidence: source, filing and photos."""
    doc_id = sha[:16]
    source = SourceDoc(
        doc_id=doc_id,
        origin="upload",
        filename=f"{doc_id}.pdf",
        sha256=sha,
        retrieved_at=datetime(2025, 1, 1),
        page_count=1,
    )
    filing = QuarterFiling(
        doc_id=doc_id,
        state=state,
        quarter_end=quarter_end,
        filed_on=filed_on,
        blocks=[
            BlockProgress(
                block_label=block,
                slabs_claimed_count=slabs,
                slabs_claimed_percent=percent,
                floors_above_ground_sanctioned=floors,
            )
        ],
        activities=[
            ActivityProgress(name=name, percent_done=value) for name, value in (activities or {}).items()
        ],
        financials=Financials(**fin) if fin else Financials(),
    )
    return QuarterEvidence(source=source, filing=filing, photos=list(photos))


def make_project(quarters: Sequence[QuarterEvidence], label: str = "TEST") -> ProjectEvidence:
    """A project from quarters in the order given."""
    return ProjectEvidence(project_label=label, quarters=list(quarters))
