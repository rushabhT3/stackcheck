"""R2: the same photo must not appear in two different quarters."""

import io
from datetime import date

from PIL import Image as PILImage

from stackcheck.models import BlockProgress, Finding, ProjectEvidence
from stackcheck.rules.checks import RuleSettings, r2_reused_photo
from stackcheck.vision.phash import dhash_hex
from tests.factories import make_photo, make_project, make_quarter

Q1 = date(2025, 3, 31)
Q2 = date(2025, 6, 30)
SETTINGS = RuleSettings()
BASE = "a" * 16  # 32 bits set: a usable photo hash, never blank
NEAR = f"{int(BASE, 16) ^ 0x3F:016x}"  # distance 6: a lightly changed copy
FAR = f"{int(BASE, 16) ^ 0x7F:016x}"  # distance 7: too far to match
OTHER = f"{int(BASE, 16) ^ 0xFFFFF:016x}"  # distance 20: an unrelated photo


def _bands_png(width: int, splits: list[tuple[float, int]]) -> bytes:
    """Vertical bands; large low-frequency blocks survive a light resize."""
    img = PILImage.new("L", (width, 200), 0)
    px = img.load()
    assert px is not None
    for x in range(width):
        frac = x / width
        grey = splits[-1][1]
        for edge, value in splits:
            if frac < edge:
                grey = value
                break
        for y in range(200):
            px[x, y] = grey
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


THIRDS = [(1 / 3, 0), (2 / 3, 255), (1.0, 0)]
QUARTERS = [(0.25, 0), (0.5, 255), (0.75, 0), (1.0, 255)]


def _project(dhash_a: str | None, dhash_b: str | None, slabs_a: int = 3, slabs_b: int = 3) -> ProjectEvidence:
    a = make_quarter(Q1, slabs=slabs_a, photos=[make_photo("a", dhash=dhash_a, lower=3, upper=3)])
    b = make_quarter(Q2, slabs=slabs_b, photos=[make_photo("b", dhash=dhash_b, lower=3, upper=3)])
    return make_project([a, b])


def _run(project: ProjectEvidence) -> list[Finding]:
    return r2_reused_photo(project, SETTINGS)


def test_one_quarter() -> None:
    project = make_project([make_quarter(Q1, photos=[make_photo("a")])])
    findings = _run(project)
    assert len(findings) == 1
    assert findings[0].status == "CANT_VERIFY"


def test_distinct_hashes_ok() -> None:
    findings = _run(_project(BASE, OTHER))
    assert len(findings) == 1
    assert findings[0].status == "OK"


def test_distance_6_flags() -> None:
    findings = _run(_project(BASE, NEAR))
    assert len(findings) == 1
    assert findings[0].status == "FLAG"
    assert findings[0].severity == "medium"
    assert findings[0].title == "Same photo filed in 31 Mar 2025 and 30 Jun 2025"


def test_distance_7_no_flag() -> None:
    findings = _run(_project(BASE, FAR))
    assert [f.status for f in findings] == ["OK"]


def test_both_elevation_different_claims_high() -> None:
    findings = _run(_project(BASE, NEAR, slabs_a=3, slabs_b=5))
    assert findings[0].status == "FLAG"
    assert findings[0].severity == "high"


def test_reused_sentence_when_claims_differ() -> None:
    findings = _run(_project(BASE, NEAR, slabs_a=3, slabs_b=5))
    assert (
        "Same photo filed again; the claimed progress changed from 3 slabs to 5 slabs."
        in findings[0].explanation
    )


def test_reused_sentence_when_claims_match() -> None:
    findings = _run(_project(BASE, NEAR))
    assert (
        "Same photo filed again; the claimed progress is 3 slabs in both quarters." in findings[0].explanation
    )


def test_reused_evidence_notes_carry_captions() -> None:
    findings = _run(_project(BASE, NEAR, slabs_a=3, slabs_b=5))
    photos = [e for e in findings[0].evidence if e.kind == "photo"]
    assert len(photos) == 2
    assert "Quarter ending 30 Jun 2025" in photos[0].note
    assert "claimed 5 slabs" in photos[0].note
    assert "Hash distance 6" in photos[0].note
    assert "Quarter ending 31 Mar 2025" in photos[1].note
    assert "claimed 3 slabs" in photos[1].note
    assert "Hash distance 6" in photos[1].note


def test_floor_level_photo_medium() -> None:
    a = make_quarter(Q1, slabs=3, photos=[make_photo("a", dhash=BASE, lower=3, upper=3)])
    b = make_quarter(
        Q2,
        slabs=5,
        photos=[make_photo("b", kind="floor_level", dhash=NEAR, lower=3, upper=3)],
    )
    findings = _run(make_project([a, b]))
    assert findings[0].status == "FLAG"
    assert findings[0].severity == "medium"


def test_document_photos_ignored() -> None:
    a = make_quarter(
        Q1,
        photos=[
            make_photo("doc", kind="document_or_logo", dhash=BASE),
            make_photo("a", dhash=BASE, lower=3, upper=3),
        ],
    )
    b = make_quarter(
        Q2,
        photos=[
            make_photo("doc2", kind="document_or_logo", dhash=BASE),
            make_photo("b", dhash=OTHER, lower=3, upper=3),
        ],
    )
    findings = _run(make_project([a, b]))
    assert [f.status for f in findings] == ["OK"]


def test_same_photo_twice_in_one_quarter_no_flag() -> None:
    a = make_quarter(
        Q1,
        photos=[
            make_photo("a1", dhash=BASE, lower=3, upper=3),
            make_photo("a2", dhash=BASE, lower=3, upper=3),
        ],
    )
    b = make_quarter(Q2, photos=[make_photo("b", dhash=OTHER, lower=3, upper=3)])
    findings = _run(make_project([a, b]))
    assert [f.status for f in findings] == ["OK"]


def test_undated_quarter_skipped() -> None:
    a = make_quarter(Q1, photos=[make_photo("a", dhash=BASE, lower=3, upper=3)])
    b = make_quarter(None, photos=[make_photo("b", dhash=BASE, lower=3, upper=3)])
    findings = _run(make_project([a, b]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_unknown_block_gives_medium() -> None:
    a = make_quarter(Q1, slabs=3, photos=[make_photo("a", dhash=BASE, block_guess="Annex")])
    b = make_quarter(Q2, slabs=5, photos=[make_photo("b", dhash=NEAR, block_guess="Annex")])
    findings = _run(make_project([a, b]))
    assert findings[0].status == "FLAG"
    assert findings[0].severity == "medium"


def test_blocks_before_match_are_skipped() -> None:
    a = make_quarter(Q1, slabs=3, photos=[make_photo("a", dhash=BASE)])
    a.filing.blocks.append(BlockProgress(block_label="Annex", slabs_claimed_count=9))
    a.filing.blocks.reverse()
    b = make_quarter(Q2, slabs=5, photos=[make_photo("b", dhash=NEAR)])
    findings = _run(make_project([a, b]))
    assert findings[0].status == "FLAG"
    assert findings[0].severity == "high"


def test_blank_images_never_match() -> None:
    """Two completely black scans share a hash but must not count as reused."""
    black = dhash_hex(_solid_png(0))
    assert black == "0" * 16
    findings = _run(_project(black, black))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_flat_white_images_never_match() -> None:
    white = dhash_hex(_solid_png(255))
    assert bin(int(white, 16)).count("1") < 12
    findings = _run(_project(white, white))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_low_confidence_photos_never_match() -> None:
    a = make_quarter(Q1, photos=[make_photo("a", dhash=BASE, confidence="low")])
    b = make_quarter(Q2, photos=[make_photo("b", dhash=BASE, confidence="low")])
    findings = _run(make_project([a, b]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_tiny_photos_never_match() -> None:
    a = make_quarter(Q1, photos=[make_photo("a", dhash=BASE, width=40, height=40)])
    b = make_quarter(Q2, photos=[make_photo("b", dhash=BASE, width=40, height=40)])
    findings = _run(make_project([a, b]))
    assert [f.status for f in findings] == ["CANT_VERIFY"]


def test_medium_confidence_still_matches() -> None:
    a = make_quarter(Q1, photos=[make_photo("a", dhash=BASE, confidence="medium")])
    b = make_quarter(Q2, photos=[make_photo("b", dhash=NEAR, confidence="medium")])
    findings = _run(make_project([a, b]))
    assert [f.status for f in findings] == ["FLAG"]


def test_resized_copy_still_matches() -> None:
    """A real photo and a lightly resized copy share a hash within tolerance."""
    raw = _bands_png(200, THIRDS)
    small = PILImage.open(io.BytesIO(raw)).resize((180, 180))
    buf = io.BytesIO()
    small.save(buf, format="PNG")
    first = dhash_hex(raw)
    second = dhash_hex(buf.getvalue())
    assert bin(int(first, 16)).count("1") >= 12
    from stackcheck.vision.phash import hamming

    assert hamming(first, second) <= 6
    findings = _run(_project(first, second))
    assert [f.status for f in findings] == ["FLAG"]


def test_unrelated_photos_do_not_match() -> None:
    first = dhash_hex(_bands_png(200, THIRDS))
    second = dhash_hex(_bands_png(200, QUARTERS))
    from stackcheck.vision.phash import hamming

    assert hamming(first, second) > 6
    findings = _run(_project(first, second))
    assert [f.status for f in findings] == ["OK"]


def test_one_card_per_newer_photo() -> None:
    """One photo matching three older ones yields one finding, not three."""
    a = make_quarter(
        Q1,
        photos=[make_photo(f"a{i}", dhash=BASE) for i in range(3)],
    )
    b = make_quarter(Q2, photos=[make_photo("b", dhash=BASE)])
    findings = _run(make_project([a, b]))
    assert len(findings) == 1
    assert findings[0].status == "FLAG"
    assert len([e for e in findings[0].evidence if e.kind == "photo"]) == 4


def _solid_png(grey: int) -> bytes:
    img = PILImage.new("L", (200, 200), grey)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
