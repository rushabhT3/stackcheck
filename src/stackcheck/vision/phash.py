"""64-bit difference hash for spotting reused photos. Pure functions, no I/O."""

import io

from PIL import Image


def dhash_hex(image_bytes: bytes) -> str:
    """64-bit difference hash as 16 lowercase hex chars, row-major, MSB first."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = small.load()
        bits = 0
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | (1 if pixels[x, y] > pixels[x + 1, y] else 0)
    return f"{bits:016x}"


def hamming(first: str, second: str) -> int:
    """Bit distance between two hex hashes."""
    return (int(first, 16) ^ int(second, 16)).bit_count()
