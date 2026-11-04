"""Generate three one-page CONSTRUCTED EXAMPLE filings for the site-video demo.

One demo video with a counted range [low, up] plus three filings shows all
three R9 branches: FLAG (claim 2+ floors above the video range), OK (claim
below the video range, consistent with later progress) and CANT_VERIFY
(no slab claim at all).

    uv run python scripts/make_constructed_filings.py --low 6 --up 7

Writes constructed_flag.pdf, constructed_ok.pdf and constructed_no_claim.pdf
into data/work/constructed/ (gitignored, never committed). Rerun with the
real [low, up] once the demo video's consensus is known, then upload each
PDF with the same video file (block MAIN) to make three demo checks.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf

MARKER = "CONSTRUCTED EXAMPLE"
PROJECT = "Project X (constructed example)"
QUARTER_END = "31 March 2025"
FILED_ON = "15 April 2025"
BLOCK = "MAIN"
SANCTIONED_FLOORS = 12
OUT_DIR = Path("data/work/constructed")


def _page_text(claim: int | None) -> str:
    lines = [
        f"{MARKER} - synthetic filing for the StackCheck demo. Not a real project.",
        f"Project name: {PROJECT}",
        "Registration number: CONSTRUCTED/0000/000000",
        f"Quarter ending: {QUARTER_END}",
        f"Date of filing: {FILED_ON}",
        f"Block {BLOCK} - floors above ground sanctioned: {SANCTIONED_FLOORS}",
        f"Construction progress in Block {BLOCK}:",
    ]
    if claim is None:
        lines.append("Slabs of Super Structure: no count reported for this quarter.")
    else:
        lines.append(f"Slabs of Super Structure completed: {claim} slabs")
    return "\n".join(lines)


def generate(out_dir: Path, low: int, up: int) -> list[Path]:
    """Write the three demo PDFs. Claims derive from the video range."""
    if low < 1:
        raise ValueError(f"video range lower bound must be >= 1, got {low}")
    if up < low:
        raise ValueError(f"video range upper {up} is below lower {low}")
    claims: dict[str, int | None] = {"flag": up + 2, "ok": low - 1, "no_claim": None}
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, claim in claims.items():
        path = out_dir / f"constructed_{name}.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 96), MARKER, fontsize=22)
        page.insert_textbox((72, 140, 540, 750), _page_text(claim), fontsize=11, align=0)
        doc.save(path)
        doc.close()
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate constructed demo filings.")
    parser.add_argument("--low", type=int, required=True, help="video range lower bound")
    parser.add_argument("--up", type=int, required=True, help="video range upper bound")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    written = generate(args.out_dir, args.low, args.up)
    for path in written:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
