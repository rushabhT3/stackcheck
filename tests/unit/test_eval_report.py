"""Unit tests for scripts/eval_report.py. Synthetic fixtures only, no live calls."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from eval_report import (
    backtest_section,
    build_report,
    earliest_flags,
    extraction_section,
    flags_section,
    floor_section,
    golden_section,
    load_review_rows,
    main,
    summarize_backtest,
    summarize_flags,
)


def _write_golden_json(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "date": "2026-11-03",
                "activities_matched": 17,
                "activities_total": 18,
                "money_matched": 6,
                "money_total": 6,
                "known_gap": "mar_2025 B-2 item 9",
                "rule_pins": {"r4": "pass", "r7": "pass"},
                "method": "fresh 3x reads, 2/3 majority",
            }
        ),
        encoding="utf-8",
    )


def test_golden_section_missing_file_is_not_measured(tmp_path: Path) -> None:
    assert golden_section(tmp_path / "nope.json")["status"] == "not measured yet"


def test_golden_section_reports_gap_openly(tmp_path: Path) -> None:
    payload_path = tmp_path / "golden_project_e.json"
    _write_golden_json(payload_path)
    section = golden_section(payload_path)
    assert section["status"] == "measured"
    assert section["activities_matched"] == 17
    assert section["activities_total"] == 18
    assert "item 9" in section["known_gap"]


def _write_labels_csv(path: Path) -> None:
    path.write_text(
        "file,storeys_visible,top_obscured,notes\n"
        "a.jpg,5,false,\n"
        "b.jpg,8,false,\n"
        "c.jpg,3,false,\n"
        "d.jpg,6,false,\n",
        encoding="utf-8",
    )


def _write_bench_json(path: Path) -> None:
    payload = {
        "results": [
            {"file": "a.jpg", "status": "OK", "lower": 5, "upper": 5, "distance": 0, "within1": True},
            {"file": "b.jpg", "status": "OK", "lower": 7, "upper": 8, "distance": 1, "within1": True},
            {"file": "c.jpg", "status": "OK", "lower": 6, "upper": 6, "distance": 3, "within1": False},
            {
                "file": "d.jpg",
                "status": "CANT_VERIFY",
                "lower": None,
                "upper": None,
                "distance": None,
                "within1": None,
            },
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_floor_rates_from_labels_and_bench(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    bench = tmp_path / "bench.json"
    _write_labels_csv(labels)
    _write_bench_json(bench)
    section = floor_section(labels, bench)
    assert section["status"] == "measured"
    assert section["n"] == 4
    assert section["exact"]["value"] == 1 / 3
    assert section["exact"]["n"] == 3
    assert section["within1"]["value"] == 2 / 3
    assert section["within1"]["n"] == 3
    assert section["abstain"]["value"] == 1 / 4
    assert section["abstain"]["n"] == 4
    assert section["wrong_when_confident"]["value"] == 1 / 3
    assert section["wrong_when_confident"]["n"] == 3
    for key in ("exact", "within1", "abstain", "wrong_when_confident"):
        assert isinstance(section[key]["method"], str) and section[key]["method"]


def _write_tiered_bench_json(path: Path) -> None:
    payload = {
        "results": [
            {
                "file": "a.jpg",
                "status": "OK",
                "tier": "CONFIDENT",
                "lower": 5,
                "upper": 5,
                "sam_rows": 5,
                "vision": 5,
                "distance": 0,
                "within1": True,
            },
            {
                "file": "b.jpg",
                "status": "OK",
                "tier": "ESTIMATE",
                "lower": 7,
                "upper": 9,
                "sam_rows": 7,
                "vision": 9,
                "distance": None,
                "within1": None,
            },
            {
                "file": "c.jpg",
                "status": "CANT_VERIFY",
                "tier": None,
                "lower": None,
                "upper": None,
                "sam_rows": 9,
                "vision": 5,
                "distance": None,
                "within1": None,
            },
            {
                "file": "d.jpg",
                "status": "CANT_VERIFY",
                "tier": None,
                "lower": None,
                "upper": None,
                "sam_rows": 0,
                "vision": 6,
                "distance": None,
                "within1": None,
            },
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_floor_tiers_split_confident_estimate_cant_tell(tmp_path: Path) -> None:
    """F1 tiers: b is an estimate, c disagrees by 4 (can't tell), d is vision-only (estimate)."""
    labels = tmp_path / "labels.csv"
    bench = tmp_path / "bench.json"
    _write_labels_csv(labels)
    _write_tiered_bench_json(bench)
    section = floor_section(labels, bench)
    tiers = section["tiers"]
    assert tiers["confident"]["value"] == 1 / 4
    assert tiers["estimate"]["value"] == 2 / 4
    assert tiers["cant_tell"]["value"] == 1 / 4
    assert tiers["confident"]["n"] == tiers["estimate"]["n"] == tiers["cant_tell"]["n"] == 4
    assert section["abstain"]["value"] == 1 / 4
    assert section["wrong_when_confident"]["n"] == 1
    assert section["within1"]["n"] == 1


def test_floor_section_missing_inputs_not_measured(tmp_path: Path) -> None:
    section = floor_section(tmp_path / "nope.csv", tmp_path / "nope.json")
    assert section["status"] == "not measured yet"
    for key in ("exact", "within1", "abstain", "wrong_when_confident"):
        assert section[key]["value"] is None


def test_floor_section_empty_join_not_measured(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text("file,storeys_visible,top_obscured,notes\n", encoding="utf-8")
    bench = tmp_path / "bench.json"
    bench.write_text(json.dumps({"results": []}), encoding="utf-8")
    section = floor_section(labels, bench)
    assert section["status"] == "not measured yet"


def _write_hand(hand_dir: Path) -> None:
    hand_dir.mkdir(parents=True, exist_ok=True)
    (hand_dir / "d1.json").write_text(
        json.dumps(
            {
                "doc_id": "d1",
                "quarter_end": "2025-03-31",
                "filed_on": "2025-04-15",
                "slabs": {"MAIN": 3},
                "cumulative_received": "66000",
                "cumulative_spent": None,
                "estimated_cost": "8824170",
            }
        ),
        encoding="utf-8",
    )
    (hand_dir / "d2.json").write_text(
        json.dumps(
            {
                "doc_id": "d2",
                "quarter_end": "2025-06-30",
                "filed_on": "2025-07-10",
                "slabs": {"A": 2, "B": 5},
                "cumulative_received": "100",
                "cumulative_spent": None,
                "estimated_cost": "50",
            }
        ),
        encoding="utf-8",
    )


def _write_extracted(extracted_dir: Path) -> None:
    extracted_dir.mkdir(parents=True, exist_ok=True)
    (extracted_dir / "d1.json").write_text(
        json.dumps(
            {
                "doc_id": "d1",
                "quarter_end": "2025-03-31",
                "filed_on": "2025-04-15",
                "blocks": [{"block_label": "MAIN", "slabs_claimed_count": 3}],
                "financials": {
                    "cumulative_received": 66000,
                    "cumulative_spent": None,
                    "estimated_total_cost": 8824170,
                },
            }
        ),
        encoding="utf-8",
    )
    (extracted_dir / "d2.json").write_text(
        json.dumps(
            {
                "doc_id": "d2",
                "quarter_end": "2025-03-31",
                "filed_on": "2025-07-10",
                "blocks": [
                    {"block_label": "A", "slabs_claimed_count": 2},
                    {"block_label": "B", "slabs_claimed_count": 4},
                ],
                "financials": {
                    "cumulative_received": 100,
                    "cumulative_spent": 7,
                    "estimated_total_cost": None,
                },
            }
        ),
        encoding="utf-8",
    )


def test_extraction_per_field_accuracy(tmp_path: Path) -> None:
    hand_dir = tmp_path / "labels"
    extracted_dir = tmp_path / "extracted"
    _write_hand(hand_dir)
    _write_extracted(extracted_dir)
    section = extraction_section(hand_dir, extracted_dir)
    assert section["status"] == "measured"
    assert section["n_docs"] == 2
    fields = section["fields"]
    assert fields["quarter_end"]["value"] == 1 / 2
    assert fields["quarter_end"]["n"] == 2
    assert fields["filed_on"]["value"] == 1.0
    assert fields["filed_on"]["n"] == 2
    assert fields["slabs"]["value"] == 2 / 3
    assert fields["slabs"]["n"] == 3
    assert fields["cumulative_received"]["value"] == 1.0
    assert fields["cumulative_received"]["n"] == 2
    assert fields["cumulative_spent"]["value"] is None
    assert fields["cumulative_spent"]["n"] == 0
    assert fields["estimated_cost"]["value"] == 1 / 2
    assert fields["estimated_cost"]["n"] == 2
    for field in fields.values():
        assert isinstance(field["method"], str) and field["method"]


def test_extraction_missing_dirs_not_measured(tmp_path: Path) -> None:
    section = extraction_section(tmp_path / "nope", tmp_path / "nope2")
    assert section["status"] == "not measured yet"


def _write_review(path: Path) -> None:
    path.write_text(
        "project_label,rule,reviewer_verdict,quarter_end\n"
        "P1,R1,true,2024-12-31\n"
        "P1,R1,true,2025-03-31\n"
        "P1,R1,false,2025-03-31\n"
        "P1,R1,unclear,2025-06-30\n"
        "P1,R4,unclear,2025-03-31\n",
        encoding="utf-8",
    )


def test_flag_precision_keeps_unclear_out(tmp_path: Path) -> None:
    review = tmp_path / "review.csv"
    _write_review(review)
    rows = load_review_rows(review)
    assert rows is not None
    summary = summarize_flags(rows)
    assert summary["R1"]["true"] == 2
    assert summary["R1"]["false"] == 1
    assert summary["R1"]["unclear"] == 1
    assert summary["R1"]["precision"]["value"] == 2 / 3
    assert summary["R1"]["precision"]["n"] == 3
    assert summary["R4"]["precision"]["value"] is None
    assert summary["R4"]["precision"]["n"] == 0
    section = flags_section(review)
    assert section["status"] == "measured"
    assert section["rules"]["R1"]["unclear"] == 1
    assert isinstance(section["rules"]["R1"]["precision"]["method"], str)


def test_flags_missing_file_not_measured(tmp_path: Path) -> None:
    assert flags_section(tmp_path / "nope.csv")["status"] == "not measured yet"


def test_flags_reads_r4_person_checked_file(tmp_path: Path) -> None:
    """First filled review: R4 1 true of 3, reviewer is a person reading PDF pages."""
    review = tmp_path / "review_r4_projectE.csv"
    review.write_text(
        "project_label,rule,reviewer_verdict,quarter_end,reviewer_note,reviewed_on\n"
        "Project E,R4_CLAIM_REGRESSION,false,2024-12-31,person-checked read of the PDF pages,2026-11-02\n"
        "Project E,R4_CLAIM_REGRESSION,false,2025-03-31,person-checked read of the PDF pages,2026-11-02\n"
        "Project E,R4_CLAIM_REGRESSION,true,2025-03-31,person-checked read of the PDF pages,2026-11-02\n",
        encoding="utf-8",
    )
    section = flags_section(review)
    assert section["status"] == "measured"
    assert section["rules"]["R4_CLAIM_REGRESSION"]["true"] == 1
    assert section["rules"]["R4_CLAIM_REGRESSION"]["false"] == 2
    assert section["rules"]["R4_CLAIM_REGRESSION"]["precision"]["n"] == 3
    assert section["rules"]["R4_CLAIM_REGRESSION"]["precision"]["value"] == 1 / 3


def _write_backtest(path: Path) -> None:
    path.write_text(
        "project_label,action_date,action_type,order_url\n"
        "P1,2025-09-15,suspension,https://example.com/o1\n"
        "P2,2025-06-01,notice,https://example.com/o2\n",
        encoding="utf-8",
    )


def test_backtest_lead_quarters_and_not_flagged(tmp_path: Path) -> None:
    review = tmp_path / "review.csv"
    backtest = tmp_path / "backtest.csv"
    _write_review(review)
    _write_backtest(backtest)
    rows = load_review_rows(review)
    assert rows is not None
    assert earliest_flags(rows) == {"P1": "2024-12-31"}
    section = backtest_section(backtest, earliest_flags(rows))
    assert section["status"] == "measured"
    by_project = {entry["project_label"]: entry for entry in section["projects"]}
    assert by_project["P1"]["earliest_flag"] == "2024-12-31"
    assert by_project["P1"]["quarters_before"]["value"] == 3
    assert by_project["P1"]["status"] == "flagged"
    assert by_project["P2"]["status"] == "not flagged"
    assert by_project["P2"]["quarters_before"]["value"] is None
    assert isinstance(by_project["P1"]["quarters_before"]["method"], str)


def test_backtest_flag_after_action_is_not_flagged() -> None:
    entries = summarize_backtest(
        [
            {
                "project_label": "P9",
                "action_date": "2025-03-15",
                "action_type": "notice",
                "order_url": "https://example.com/o9",
            }
        ],
        {"P9": "2025-06-30"},
    )
    assert entries[0]["status"] == "not flagged"
    assert entries[0]["quarters_before"]["value"] is None


def test_backtest_missing_inputs_not_measured(tmp_path: Path) -> None:
    assert backtest_section(tmp_path / "nope.csv", {"P1": "2024-12-31"})["status"] == "not measured yet"
    backtest = tmp_path / "backtest.csv"
    _write_backtest(backtest)
    assert backtest_section(backtest, {})["status"] == "not measured yet"


def test_main_writes_eval_json_with_date_and_sections(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    bench = tmp_path / "bench.json"
    _write_labels_csv(labels)
    _write_bench_json(bench)
    hand_dir = tmp_path / "labels"
    extracted_dir = tmp_path / "extracted"
    _write_hand(hand_dir)
    _write_extracted(extracted_dir)
    review = tmp_path / "review.csv"
    backtest = tmp_path / "backtest.csv"
    _write_review(review)
    _write_backtest(backtest)
    out = tmp_path / "eval.json"
    assert (
        main(
            [
                "--labels",
                str(labels),
                "--bench",
                str(bench),
                "--hand-labels",
                str(hand_dir),
                "--extracted",
                str(extracted_dir),
                "--review",
                str(review),
                "--backtest",
                str(backtest),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(report["date"], str) and report["date"]
    assert report["floors"]["status"] == "measured"
    assert report["extraction"]["fields"]["slabs"]["n"] == 3
    assert report["flags"]["rules"]["R1"]["precision"]["value"] == 2 / 3
    assert report["backtest"]["projects"][0]["quarters_before"]["value"] == 3


def _write_bench_video(path: Path) -> None:
    payload = {
        "videos": [
            {"id": "V01", "lower": 17, "upper": 18, "reference_floors": 17},
            {"id": "V04", "lower": 5, "upper": 9, "reference_floors": 12},
            {"id": "V08", "lower": 6, "upper": 7, "reference_floors": None},
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_video_section_scores_only_referenced(tmp_path: Path) -> None:
    from eval_report import video_section

    bench_video = tmp_path / "bench_video.json"
    _write_bench_video(bench_video)
    section = video_section(bench_video)
    assert section["status"] == "measured"
    assert section["n"] == 2
    assert section["within1"]["value"] == 1 / 2
    assert section["within1"]["n"] == 2


def test_video_section_not_measured_without_file(tmp_path: Path) -> None:
    from eval_report import video_section

    section = video_section(tmp_path / "missing.json")
    assert section["status"] == "not measured yet"
    assert section["n"] == 0


def test_video_section_reports_public_off_by(tmp_path: Path) -> None:
    from eval_report import video_section

    bench_video = tmp_path / "bench_video.json"
    bench_video.write_text(
        json.dumps(
            {
                "videos": [
                    {"id": "V07", "lower": 30, "upper": 30, "public_reference": 25},
                    {"id": "V08", "lower": 7, "upper": 8, "public_reference": [9, 10]},
                    {"id": "V01", "lower": None, "upper": None, "public_reference": 25},
                    {"id": "V02", "lower": 6, "upper": 7},
                ]
            }
        ),
        encoding="utf-8",
    )
    section = video_section(bench_video)
    by_id = {video["id"]: video for video in section["videos"]}
    assert by_id["V07"]["off_by"] == 5
    assert by_id["V08"]["off_by"] == 1
    assert "V01" not in by_id
    assert "V02" not in by_id


def test_video_section_counts_every_public_reference(tmp_path: Path) -> None:
    """V06 + V07 + V08 all have references, so n=3 and each row shows."""
    from eval_report import video_section

    bench_video = tmp_path / "bench_video.json"
    bench_video.write_text(
        json.dumps(
            {
                "videos": [
                    {"id": "V06", "lower": 17, "upper": 18, "reference_floors": 17},
                    {"id": "V07", "lower": 30, "upper": 30, "public_reference": 25},
                    {"id": "V08", "lower": 7, "upper": 8, "public_reference": [9, 10]},
                ]
            }
        ),
        encoding="utf-8",
    )
    section = video_section(bench_video)
    assert section["n"] == 3
    by_id = {video["id"]: video for video in section["videos"]}
    assert set(by_id) == {"V06", "V07", "V08"}
    assert by_id["V06"]["off_by"] == 0
    assert by_id["V07"]["off_by"] == 5
    assert by_id["V08"]["off_by"] == 1


def test_build_report_carries_date_and_sections(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    bench = tmp_path / "bench.json"
    _write_labels_csv(labels)
    _write_bench_json(bench)
    hand_dir = tmp_path / "labels"
    extracted_dir = tmp_path / "extracted"
    _write_hand(hand_dir)
    _write_extracted(extracted_dir)
    review = tmp_path / "review.csv"
    backtest = tmp_path / "backtest.csv"
    _write_review(review)
    _write_backtest(backtest)
    report = build_report(labels, bench, hand_dir, extracted_dir, review, backtest, "2026-10-30")
    assert report["date"] == "2026-10-30"
    assert report["floors"]["n"] == 4
    assert report["extraction"]["n_docs"] == 2
    assert report["flags"]["rules"]["R1"]["precision"]["n"] == 3
    assert len(report["backtest"]["projects"]) == 2


def _write_human_csv(path: Path, counted_by: str = "Rushabh", counted_on: str = "2026-11-02") -> None:
    path.write_text(
        "image_id,floors,cannot_tell,counted_by,counted_on\n"
        f"a.jpg,5,,{counted_by},{counted_on}\n"
        f"b.jpg,9,,{counted_by},{counted_on}\n"
        f"c.jpg,3,,{counted_by},{counted_on}\n"
        f"d.jpg,,,,\n",
        encoding="utf-8",
    )


def test_floor_reference_is_ai_second_reader_by_default(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    bench = tmp_path / "bench.json"
    _write_labels_csv(labels)
    _write_bench_json(bench)
    section = floor_section(labels, bench)
    assert section["reference"]["kind"] == "ai"
    assert "second reader" in section["reference"]["note"]
    assert "has not checked" in section["reference"]["note"]
    assert "counted by" not in section["reference"]["note"]
    assert section["disagreements"] is None


def test_floor_reference_uses_human_file_when_counted(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    bench = tmp_path / "bench.json"
    human = tmp_path / "labels_human.csv"
    _write_labels_csv(labels)
    _write_bench_json(bench)
    _write_human_csv(human)
    section = floor_section(labels, bench, human)
    assert section["reference"]["kind"] == "human"
    assert "Rushabh" in section["reference"]["note"]
    assert "2026-11-02" in section["reference"]["note"]
    assert section["disagreements"]["n"] == 3
    assert section["disagreements"]["value"] == 2 / 3


def test_floor_reference_ignores_human_file_without_counter(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    bench = tmp_path / "bench.json"
    human = tmp_path / "labels_human.csv"
    _write_labels_csv(labels)
    _write_bench_json(bench)
    _write_human_csv(human, counted_by="", counted_on="")
    section = floor_section(labels, bench, human)
    assert section["reference"]["kind"] == "ai"
    assert "counted by" not in section["reference"]["note"]


def test_eval_json_matches_accuracy_page_contract(tmp_path: Path) -> None:
    """The #/accuracy page reads date + four sections; every number has value/n/method."""
    missing = tmp_path / "nope"
    report = build_report(missing, missing, missing, missing, missing, missing, "2026-10-30")
    assert report["date"] == "2026-10-30"
    assert report["floors"]["reference"]["kind"] == "ai"
    assert report["floors"]["disagreements"] is None
    for section in ("floors", "extraction", "flags", "backtest"):
        assert report[section]["status"] in ("measured", "not measured yet")
    for key in ("exact", "within1", "abstain", "wrong_when_confident"):
        assert set(report["floors"][key]) == {"value", "n", "method"}
    for field in ("quarter_end", "filed_on", "slabs"):
        assert set(report["extraction"]["fields"][field]) == {"value", "n", "method"}


def test_flags_section_merges_review_directory(tmp_path: Path) -> None:
    """Every review_*.csv plus review.csv in a directory concatenates."""
    review_dir = tmp_path / "reviews"
    review_dir.mkdir()
    (review_dir / "review_r4_projectE.csv").write_text(
        "project_label,rule,reviewer_verdict,quarter_end\n"
        "Project E,R4_CLAIM_REGRESSION,true,2025-03-31\n"
        "Project E,R4_CLAIM_REGRESSION,false,2025-03-31\n",
        encoding="utf-8",
    )
    (review_dir / "review_flags_projectE_run3.csv").write_text(
        "project_label,rule,reviewer_verdict,quarter_end\n"
        "Project E,R7_ARITHMETIC,false,2025-03-31\n"
        "Project E,R7_ARITHMETIC,true,2025-03-31\n",
        encoding="utf-8",
    )
    rows = load_review_rows(review_dir)
    assert rows is not None and len(rows) == 4
    section = flags_section(review_dir)
    assert section["status"] == "measured"
    assert section["rules"]["R4_CLAIM_REGRESSION"]["true"] == 1
    assert section["rules"]["R4_CLAIM_REGRESSION"]["false"] == 1
    assert section["rules"]["R7_ARITHMETIC"]["true"] == 1
    assert section["rules"]["R7_ARITHMETIC"]["false"] == 1


def test_flags_shape_like_projectE_run3_precisions(tmp_path: Path) -> None:
    """5-row shape of review_flags_projectE_run3.csv: R4 1 true of 4, R7 0 of 1."""
    review = tmp_path / "review_flags_projectE_run3.csv"
    review.write_text(
        "project_label,rule,reviewer_verdict,quarter_end\n"
        "Project E,R4_CLAIM_REGRESSION,true,2024-12-31\n"
        "Project E,R4_CLAIM_REGRESSION,false,2025-03-31\n"
        "Project E,R4_CLAIM_REGRESSION,false,2025-03-31\n"
        "Project E,R4_CLAIM_REGRESSION,false,2025-03-31\n"
        "Project E,R7_ARITHMETIC,false,2025-03-31\n",
        encoding="utf-8",
    )
    section = flags_section(review)
    assert section["status"] == "measured"
    r4 = section["rules"]["R4_CLAIM_REGRESSION"]
    assert r4["precision"]["value"] == 1 / 4
    assert r4["precision"]["n"] == 4
    r7 = section["rules"]["R7_ARITHMETIC"]
    assert r7["precision"]["value"] == 0
    assert r7["precision"]["n"] == 1


def test_flags_missing_file_still_reads_r4_fallback(tmp_path: Path) -> None:
    """A single missing review path falls back to the sibling review_r4 file."""
    (tmp_path / "review_r4_projectE.csv").write_text(
        "project_label,rule,reviewer_verdict,quarter_end\nProject E,R4_CLAIM_REGRESSION,true,2025-03-31\n",
        encoding="utf-8",
    )
    section = flags_section(tmp_path / "review.csv")
    assert section["status"] == "measured"
    assert section["rules"]["R4_CLAIM_REGRESSION"]["true"] == 1


def test_main_all_missing_inputs_writes_not_measured(tmp_path: Path) -> None:
    out = tmp_path / "eval.json"
    missing = str(tmp_path / "nope")
    assert (
        main(
            [
                "--labels",
                missing,
                "--bench",
                missing,
                "--hand-labels",
                missing,
                "--extracted",
                missing,
                "--review",
                missing + ".csv",
                "--backtest",
                missing + ".csv",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["date"]
    for key in ("floors", "extraction", "flags", "backtest"):
        assert report[key]["status"] == "not measured yet"
