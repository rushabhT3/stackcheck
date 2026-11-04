"""T06: dHash behavior on synthetic images."""

import io

from PIL import Image

from stackcheck.vision.phash import dhash_hex, hamming
from tests.conftest import make_png


def test_identical_bytes_hash_equal() -> None:
    raw = make_png(320, 240, "building")
    first, second = dhash_hex(raw), dhash_hex(raw)
    assert first == second
    assert len(first) == 16
    assert all(c in "0123456789abcdef" for c in first)


def test_near_duplicate_within_threshold() -> None:
    raw = make_png(320, 240, "building")
    with Image.open(io.BytesIO(raw)) as img:
        small = img.convert("RGB").resize((224, 168))
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=60)
    assert hamming(dhash_hex(raw), dhash_hex(buf.getvalue())) <= 6


def test_different_images_far_apart() -> None:
    stripes = dhash_hex(make_png(200, 200, "stripes"))
    checker = dhash_hex(make_png(200, 200, "checker"))
    assert hamming(stripes, checker) >= 20


def test_hamming_all_bits() -> None:
    assert hamming("0" * 16, "f" * 16) == 64
