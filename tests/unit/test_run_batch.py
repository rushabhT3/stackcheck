"""Unit tests for scripts/run_batch.py. No live calls; httpx.MockTransport only."""

import csv
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import run_batch


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["project_label", "url", "local_path"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def _status(state: str = "done") -> dict:
    return {
        "check_id": "chk_0123456789",
        "project_label": "Alpha",
        "state": state,
        "progress": 1.0,
        "error": None,
        "queue_position": None,
        "created_at": "2026-10-30T10:00:00+00:00",
        "updated_at": "2026-10-30T10:01:00+00:00",
        "steps": [
            {
                "name": name,
                "state": "done",
                "started_at": "2026-10-30T10:00:00+00:00",
                "finished_at": "2026-10-30T10:00:10+00:00",
                "note": None,
            }
            for name in run_batch.STEP_NAMES
        ],
    }


def _report() -> dict:
    return {
        "check_id": "chk_0123456789",
        "project_label": "Alpha",
        "created_at": "2026-10-30T10:01:00+00:00",
        "verdict": {
            "headline": "Gaps found: 1 needs a look.",
            "flags_high": 1,
            "flags_medium": 0,
            "flags_low": 0,
            "cant_verify": 1,
            "ok": 6,
        },
        "findings": [
            {"finding_id": "R1:x", "rule": "R1", "status": "OK"},
            {"finding_id": "R4:y", "rule": "R4", "status": "FLAG"},
            {"finding_id": "R4:z", "rule": "R4", "status": "CANT_VERIFY"},
        ],
        "evidence": {"quarters": [{}, {}]},
        "trace": [],
        "summary_en": None,
        "summary_hi": None,
        "costs": {"usd_total": "0.12", "by_model": {}, "calls": 3, "cache_hits": 1},
        "versions": {},
        "disclaimer": "A gap is a question, not proof.",
        "placeholder": False,
    }


def test_read_manifest_groups_two_projects_from_mixed_rows(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "manifest.csv",
        [
            {"project_label": "Alpha", "url": "https://example.com/a1.pdf", "local_path": ""},
            {"project_label": "Beta", "url": "https://example.com/b1.pdf", "local_path": ""},
            {"project_label": "Alpha", "url": "https://example.com/a2.pdf", "local_path": ""},
            {"project_label": "Beta", "url": "https://example.com/b2.pdf", "local_path": ""},
        ],
    )
    grouped = run_batch.read_manifest(manifest)
    assert grouped == {
        "Alpha": ["https://example.com/a1.pdf", "https://example.com/a2.pdf"],
        "Beta": ["https://example.com/b1.pdf", "https://example.com/b2.pdf"],
    }


def test_read_manifest_skips_empty_urls(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "manifest.csv",
        [
            {"project_label": "Alpha", "url": "", "local_path": "q1.pdf"},
            {"project_label": "Alpha", "url": "https://example.com/a1.pdf", "local_path": ""},
        ],
    )
    assert run_batch.read_manifest(manifest) == {"Alpha": ["https://example.com/a1.pdf"]}


def test_write_outputs_summary_csv_has_expected_columns(tmp_path: Path) -> None:
    row = run_batch.summarize_result("Alpha", _status(), _report())
    out_dir = tmp_path / "out"
    results_path, summary_path = run_batch.write_outputs(out_dir, [{"project": "Alpha"}], [row])
    assert results_path == out_dir / "results.json"
    assert json.loads(results_path.read_text(encoding="utf-8")) == [{"project": "Alpha"}]
    with summary_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == list(run_batch.SUMMARY_COLUMNS)
        rows = list(reader)
    assert len(rows) == 1
    got = rows[0]
    assert got["project"] == "Alpha"
    assert got["check_id"] == "chk_0123456789"
    assert got["quarters"] == "2"
    assert got["headline"] == "Gaps found: 1 needs a look."
    assert got["cost_usd"] == "0.12"
    assert got["seconds_fetch"] == "10.0"
    assert "R1" in got["rule_counts"] and "R4" in got["rule_counts"]


def test_create_check_backs_off_once_on_429_then_posts() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "busy"})
        assert json.loads(request.content) == {
            "project_label": "Alpha",
            "sources": [{"url": "https://example.com/a1.pdf"}],
        }
        return httpx.Response(202, json={"check_id": "chk_0123456789"})

    slept: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    check_id = run_batch.create_check(
        client,
        "http://live/",
        "Alpha",
        ["https://example.com/a1.pdf"],
        sleep=slept.append,
    )
    assert check_id == "chk_0123456789"
    assert slept == [2.0]
    assert calls["n"] == 2


def test_poll_until_done_stops_on_done() -> None:
    states = iter(["running", "done"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/checks/chk_0123456789"
        return httpx.Response(200, json=_status(next(states)))

    slept: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    status = run_batch.poll_until_done(
        client, "http://live", "chk_0123456789", sleep=slept.append, poll_interval_s=5.0
    )
    assert status["state"] == "done"
    assert slept == [5.0]
