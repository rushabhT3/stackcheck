"""The constructed demo filings carry the marker, project and slab claims."""

from pathlib import Path

import pymupdf

from scripts.make_constructed_filings import MARKER, PROJECT, generate


def test_constructed_filings_cover_three_r9_branches(tmp_path: Path) -> None:
    paths = generate(tmp_path, low=6, up=7)
    assert [p.name for p in paths] == [
        "constructed_flag.pdf",
        "constructed_ok.pdf",
        "constructed_no_claim.pdf",
    ]
    texts = {}
    for path in paths:
        with pymupdf.open(path) as doc:
            assert doc.page_count == 1
            texts[path.stem] = doc[0].get_text()
    for text in texts.values():
        assert MARKER in text
        assert PROJECT in text
    assert "9 slabs" in texts["constructed_flag"]
    assert "5 slabs" in texts["constructed_ok"]
    no_claim = texts["constructed_no_claim"]
    assert "no count reported" in no_claim
    assert "completed:" not in no_claim
