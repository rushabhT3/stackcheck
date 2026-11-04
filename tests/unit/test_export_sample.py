"""Unit tests for the T15 sample exporter. No live calls."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from export_sample import append_sample, strip_report


def _report() -> dict:
    return {
        "check_id": "chk_abc",
        "project_label": "Project A",
        "headline": "Gaps found",
        "evidence": {
            "quarters": [
                {
                    "source": {"url": "https://example.com/q.pdf"},
                    "photos": [
                        {
                            "image": {"image_id": "x", "path": "images/x.png"},
                            "overlay_path": "media/x.overlay.png",
                        }
                    ],
                }
            ]
        },
        "findings": [
            {
                "finding_id": "R1:all:MAIN:0",
                "evidence": [
                    {"kind": "quote", "ref": {"doc_id": "d", "page": 3}, "overlay_path": None},
                    {"kind": "photo", "image_id": "x", "overlay_path": "media/x.overlay.png"},
                ],
            }
        ],
    }


def test_strip_removes_media_paths_keeps_refs() -> None:
    stripped = strip_report(_report())
    photo = stripped["evidence"]["quarters"][0]["photos"][0]
    assert photo["overlay_path"] is None
    assert photo["image"]["path"] == ""
    assert stripped["evidence"]["quarters"][0]["source"]["url"] == "https://example.com/q.pdf"
    quote, snap = stripped["findings"][0]["evidence"]
    assert quote["ref"] == {"doc_id": "d", "page": 3}
    assert snap["overlay_path"] is None


def test_append_sample_no_duplicates(tmp_path: Path) -> None:
    samples = tmp_path / "samples.json"
    entry = {"check_id": "chk_abc", "project_label": "Project A", "headline": "Gaps found"}
    assert append_sample(samples, entry) == [entry]
    assert append_sample(samples, entry) == [entry]
    assert json.loads(samples.read_text(encoding="utf-8")) == [entry]
    other = {"check_id": "chk_def", "project_label": "B", "headline": "No gaps"}
    assert append_sample(samples, other) == [entry, other]
