"""bench_video rows and sheets without ffmpeg or live calls (pipeline stubbed)."""

import asyncio
from datetime import date
from pathlib import Path

from scripts import bench_video as bench_video_mod
from scripts.bench_video import VideoRow, bench_one
from stackcheck.config import Settings
from stackcheck.models import VideoEvidence
from tests.conftest import make_png


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "cache",
        meta_api_key="t",
        baseten_api_key="t",
        fireworks_api_key="t",
    )


async def _fake_run(upload, work, settings, check_id, clients, filing, credit=None):
    frames_dir = work / "video_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (frames_dir / f"frame_{i:02d}.png").write_bytes(make_png(32, 32, "stripes"))
    media = work / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / f"{upload.upload_id}-sheet.png").write_bytes(make_png(32, 32, "stripes"))
    return VideoEvidence(
        video_id=upload.upload_id,
        block_label="MAIN",
        tier="ESTIMATE",
        lower=6,
        upper=7,
        frames_used=3,
        frame_counts=[6, 6, 7],
        contact_sheet=f"media/{upload.upload_id}-sheet.png",
        uploaded_on=date.today(),
        credit=credit,
    )


def test_bench_one_writes_row_and_sheet(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bench_video_mod, "run_video_evidence", _fake_run)
    monkeypatch.setattr(bench_video_mod, "SHEETS_DIR", tmp_path / "sheets")
    monkeypatch.setattr(bench_video_mod, "WORK_ROOT", tmp_path / "work")
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "V01.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    row = VideoRow(
        id="V01",
        page_url="https://example.com/v",
        file_url="https://example.com/v.mp4",
        licence="CC0",
        author="A",
        reference_floors="7",
    )
    result = asyncio.run(bench_one(row, files_dir, _settings(tmp_path)))
    assert result["status"] == "OK"
    assert result["frames_kept"] == 5
    assert result["frame_counts"] == [6, 6, 7]
    assert result["tier"] == "ESTIMATE"
    assert (result["lower"], result["upper"]) == (6, 7)
    assert result["reference_floors"] == 7
    assert result["credit"] == "A, CC0, https://example.com/v"
    assert (tmp_path / "sheets" / "V01.png").is_file()


def test_bench_one_missing_file_and_blank_reference(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bench_video_mod, "run_video_evidence", _fake_run)
    monkeypatch.setattr(bench_video_mod, "WORK_ROOT", tmp_path / "work")
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    row = VideoRow(id="V09", page_url="", file_url="", licence="", author="", reference_floors="")
    result = asyncio.run(bench_one(row, files_dir, _settings(tmp_path)))
    assert result["status"] == "missing file"
    assert result["reference_floors"] is None
