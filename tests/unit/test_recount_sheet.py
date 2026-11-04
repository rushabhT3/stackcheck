"""Unit tests for scripts/recount_sheet.py. Synthetic fixtures only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from recount_sheet import build_page, main


def _write_labels(path: Path) -> None:
    path.write_text(
        "file,storeys_visible,top_obscured,notes\na.jpg,15,false,note one\nb.jpg,6,true,note two\n",
        encoding="utf-8",
    )


def test_page_lists_files_without_labels_or_model_output(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    _write_labels(labels)
    html = build_page(["a.jpg", "b.jpg"])
    assert "a.jpg" in html and "b.jpg" in html
    assert '"a.jpg"' in html
    for leak in ("15", "storeys_visible", "note one", "lower", "upper", "distance"):
        assert leak not in html
    assert 'type="number"' in html
    assert "cannot_tell" in html
    assert "counted_by" in html and "counted_on" in html
    assert "labels_human.csv" in html


def test_main_writes_recount_html(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    out = tmp_path / "recount.html"
    _write_labels(labels)
    assert main(["--labels", str(labels), "--out", str(out)]) == 0
    assert out.is_file()
    assert "images/a.jpg" in out.read_text(encoding="utf-8")


def test_main_copies_counted_video_sheets(tmp_path: Path) -> None:
    from recount_sheet import read_sheet_files

    labels = tmp_path / "labels.csv"
    out = tmp_path / "recount.html"
    sheets = tmp_path / "sheets_src"
    sheets.mkdir()
    (sheets / "V01.png").write_bytes(b"sheet")
    (sheets / "V04.png").write_bytes(b"skipped")
    _write_labels(labels)
    assert read_sheet_files(sheets) == ["sheets/V01.png"]
    assert main(["--labels", str(labels), "--sheets", str(sheets), "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "sheets/V01.png" in html
    assert "V04" not in html
    assert (tmp_path / "sheets" / "V01.png").is_file()
