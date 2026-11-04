"""Parse SAM 3.1 box output into Detections. Pure: no I/O. Masks are ignored (D11)."""

import re

from stackcheck.errors import SamParseError
from stackcheck.models import Box, Detection

_LINE = re.compile(r"^<(\d+)f>(.*)$")
_RECORD = re.compile(r"(?:^|,)(\d+)<\|box;([^|]*)\|>")

_REQUIRED = ("x1", "y1", "x2", "y2", "w", "h")


def _parse_box(fields: str) -> tuple[dict[str, float], dict[str, int], float | None]:
    raw: dict[str, str] = {}
    for field in fields.split(";"):
        if not field:
            continue
        key, _, value = field.partition("=")
        raw[key] = value
    missing = [key for key in _REQUIRED if key not in raw]
    if missing:
        raise SamParseError(f"Box is missing {', '.join(missing)}: {fields}")
    try:
        floats = {key: float(raw[key]) for key in ("x1", "y1", "x2", "y2")}
        ints = {"w": int(raw["w"]), "h": int(raw["h"])}
        confidence = float(raw["c"]) if "c" in raw else None
    except ValueError:
        raise SamParseError(f"Box has non-numeric values: {fields}") from None
    return floats, ints, confidence


def parse_sam_output(text: str, prompt: str) -> list[Detection]:
    """Parse one SAM response into Detections for a single frame image."""
    detections: list[Detection] = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        match = _LINE.match(line)
        if match is None:
            raise SamParseError(f"line {lineno}: expected <Nf> frame marker: {line[:80]}")
        frame = int(match.group(1))
        for record in _RECORD.finditer(match.group(2)):
            floats, ints, confidence = _parse_box(record.group(2))
            detections.append(
                Detection(
                    prompt=prompt,
                    frame=frame,
                    object_id=int(record.group(1)),
                    box=Box(x1=floats["x1"], y1=floats["y1"], x2=floats["x2"], y2=floats["y2"]),
                    confidence=confidence,
                    image_w=ints["w"],
                    image_h=ints["h"],
                )
            )
    return detections
