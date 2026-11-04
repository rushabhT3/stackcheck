"""Unit tests for scripts/fetch_video_bench.py. No network; respx mocks."""

import asyncio
import csv
import sys
from pathlib import Path

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from fetch_video_bench import extension_for, fetch_all, fetch_one, read_manifest


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "videos.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "page_url", "file_url", "licence", "author", "reference_floors"])
        writer.writerow(["V01", "https://x.test/v01", "https://cdn.test/v01.mp4", "CC0", "A", "17"])
        writer.writerow(["V02", "https://x.test/v02", "https://cdn.test/v02.webm", "CC-BY", "B", ""])
    return path


def test_read_manifest(tmp_path: Path) -> None:
    rows = read_manifest(_manifest(tmp_path))
    assert [row.id for row in rows] == ["V01", "V02"]
    assert rows[0].reference_floors == "17"


def test_extension_for() -> None:
    assert extension_for("video/mp4", "https://c/x") == ".mp4"
    assert extension_for("video/quicktime", "https://c/x.mov") == ".mov"
    assert extension_for("video/webm", "https://c/x") == ".webm"
    assert extension_for("application/octet-stream", "https://c/x.mp4") == ".mp4"


@respx.mock
async def test_fetch_one_streams_and_hashes(tmp_path: Path) -> None:
    body = b"0" * 100
    respx.get("https://cdn.test/v01.mp4").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "video/mp4"})
    )
    async with httpx.AsyncClient() as client:
        entry = await fetch_one(client, "V01", "https://cdn.test/v01.mp4", tmp_path)
    assert entry.id == "V01"
    assert entry.path == tmp_path / "V01.mp4"
    assert entry.path.read_bytes() == body
    import hashlib

    assert entry.sha256 == hashlib.sha256(body).hexdigest()


@respx.mock
async def test_fetch_one_rejects_html(tmp_path: Path) -> None:
    respx.get("https://cdn.test/v02.webm").mock(
        return_value=httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="not a video"):
            await fetch_one(client, "V02", "https://cdn.test/v02.webm", tmp_path)
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


@respx.mock
async def test_fetch_all_skips_failures_and_writes_lock(tmp_path: Path) -> None:
    respx.get("https://cdn.test/v01.mp4").mock(
        return_value=httpx.Response(200, content=b"video-bytes", headers={"content-type": "video/mp4"})
    )
    respx.get("https://cdn.test/v02.webm").mock(return_value=httpx.Response(403))
    files_dir = tmp_path / "files"
    entries, skipped = await fetch_all(_manifest(tmp_path), files_dir)
    assert [entry.id for entry in entries] == ["V01"]
    assert skipped == ["V02"]
    lock = files_dir.parent / "videos.lock.csv"
    assert lock.is_file()


def test_trim_helper_contract(tmp_path: Path) -> None:
    from fetch_video_bench import trim_first_seconds

    src = tmp_path / "V06.mp4"
    src.write_bytes(b"fake")
    with pytest.raises(ValueError, match=r"ffmpeg|not found|failed"):
        trim_first_seconds(src, 40)
