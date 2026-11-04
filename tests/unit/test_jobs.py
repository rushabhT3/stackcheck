"""Unit tests for the background job runner. Fake run functions, no network."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackcheck.config import Settings
from stackcheck.errors import StackCheckError
from stackcheck.models import CheckStatus, Report, StepStatus
from stackcheck.pipeline.jobs import JobRunner, QueueFull, mark_interrupted, new_check_id
from stackcheck.pipeline.run_check import check_dir, write_json


def _write_status(work: Path, check_id: str, state: str) -> None:
    now = datetime.now(UTC)
    status = CheckStatus(
        check_id=check_id,
        project_label="TEST",
        state=state,  # type: ignore[arg-type]
        steps=[StepStatus(name="fetch", state="done" if state == "done" else "running")],
        progress=1.0 if state == "done" else 0.5,
        created_at=now,
        updated_at=now,
    )
    write_json(work / "status.json", status)


async def test_submit_returns_id_and_lifecycle(tmp_settings: Settings) -> None:

    async def fake_run(check_id: str, project_label: str, sources: list, settings: Settings) -> None:
        work = check_dir(settings, check_id)
        _write_status(work, check_id, "queued")
        await asyncio.sleep(0.05)
        _write_status(work, check_id, "running")
        await asyncio.sleep(0.05)
        _write_status(work, check_id, "done")

    runner = JobRunner(tmp_settings, run=fake_run)  # type: ignore[arg-type]
    check_id = runner.submit("TEST", [])
    assert check_id.startswith("chk_") and len(check_id) == 14
    observed: list[str] = []
    for _ in range(200):
        try:
            state = runner.status(check_id).state
            if not observed or observed[-1] != state:
                observed.append(state)
            if state == "done":
                break
        except StackCheckError:
            pass
        await asyncio.sleep(0.01)
    assert observed == ["queued", "running", "done"]
    await asyncio.gather(*runner._tasks.values())


async def test_second_job_waits(tmp_settings: Settings) -> None:
    settings = tmp_settings.model_copy(update={"max_concurrent_checks": 1})
    gate = asyncio.Event()
    starts: list[str] = []
    ends: list[str] = []

    async def fake_run(check_id: str, project_label: str, sources: list, settings: Settings) -> None:
        starts.append(check_id)
        if len(starts) == 1:
            await gate.wait()
        ends.append(check_id)

    done = 0

    def finished() -> None:
        nonlocal done
        done += 1

    runner = JobRunner(settings, run=fake_run, on_finished=finished)  # type: ignore[arg-type]
    first = runner.submit("A", [])
    second = runner.submit("B", [])
    await asyncio.sleep(0.1)
    assert starts == [first]
    gate.set()
    for _ in range(200):
        if done == 2:
            break
        await asyncio.sleep(0.01)
    assert starts == [first, second]
    assert ends == [first, second]
    await asyncio.gather(*runner._tasks.values())


async def test_queued_status_carries_position(tmp_settings: Settings) -> None:
    """Waiting checks report their 1-based place in line; the running one has none."""
    settings = tmp_settings.model_copy(update={"max_concurrent_checks": 1, "max_queue": 5})
    gate = asyncio.Event()

    async def fake_run(check_id: str, project_label: str, sources: list, settings: Settings) -> None:
        await gate.wait()

    runner = JobRunner(settings, run=fake_run)  # type: ignore[arg-type]
    first = runner.submit("A", [])
    second = runner.submit("B", [])
    third = runner.submit("C", [])
    await asyncio.sleep(0.1)
    try:
        assert runner.status(first).queue_position is None
        assert runner.status(second).queue_position == 1
        assert runner.status(third).queue_position == 2
    finally:
        gate.set()
        await asyncio.gather(*runner._tasks.values(), return_exceptions=True)


async def test_queue_full(tmp_settings: Settings) -> None:
    settings = tmp_settings.model_copy(update={"max_concurrent_checks": 1, "max_queue": 1})
    gate = asyncio.Event()

    async def fake_run(check_id: str, project_label: str, sources: list, settings: Settings) -> None:
        await gate.wait()

    runner = JobRunner(settings, run=fake_run)  # type: ignore[arg-type]
    runner.submit("A", [])
    try:
        with pytest.raises(QueueFull):
            runner.submit("B", [])
    finally:
        gate.set()
        await asyncio.gather(*runner._tasks.values(), return_exceptions=True)


async def test_on_finished_called_once_per_job(tmp_settings: Settings) -> None:
    calls = 0

    async def fake_run(check_id: str, project_label: str, sources: list, settings: Settings) -> Report:  # type: ignore[return]
        if project_label == "BAD":
            raise RuntimeError("boom")
        return None  # type: ignore[return-value]

    def finished() -> None:
        nonlocal calls
        calls += 1

    runner = JobRunner(tmp_settings, run=fake_run, on_finished=finished)  # type: ignore[arg-type]
    runner.submit("OK", [])
    runner.submit("BAD", [])
    await asyncio.gather(*runner._tasks.values(), return_exceptions=True)
    assert calls == 2


def test_mark_interrupted(tmp_settings: Settings) -> None:
    root = tmp_settings.data_dir / "work"
    running_dir = root / "chk_running"
    running_dir.mkdir(parents=True)
    now = datetime.now(UTC).isoformat()
    (running_dir / "status.json").write_text(
        json.dumps(
            {
                "check_id": "chk_running",
                "project_label": "X",
                "state": "running",
                "steps": [],
                "progress": 0.2,
                "created_at": now,
                "updated_at": now,
            }
        ),
        encoding="utf-8",
    )
    done_dir = root / "chk_done"
    done_dir.mkdir(parents=True)
    (done_dir / "status.json").write_text(
        json.dumps(
            {
                "check_id": "chk_done",
                "project_label": "X",
                "state": "done",
                "steps": [],
                "progress": 1.0,
                "created_at": now,
                "updated_at": now,
            }
        ),
        encoding="utf-8",
    )
    assert mark_interrupted(tmp_settings) == 1
    status = json.loads((running_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error"] == "The server restarted while this check was running. Start a new check."


def test_mark_interrupted_covers_queued(tmp_settings: Settings) -> None:
    """A restart also fails checks that never got a slot: they would poll queued forever."""
    root = tmp_settings.data_dir / "work"
    queued_dir = root / "chk_queued"
    queued_dir.mkdir(parents=True)
    now = datetime.now(UTC).isoformat()
    (queued_dir / "status.json").write_text(
        json.dumps(
            {
                "check_id": "chk_queued",
                "project_label": "X",
                "state": "queued",
                "steps": [],
                "progress": 0.0,
                "created_at": now,
                "updated_at": now,
            }
        ),
        encoding="utf-8",
    )
    assert mark_interrupted(tmp_settings) == 1
    status = json.loads((queued_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"


def test_new_check_id_format() -> None:
    assert new_check_id().startswith("chk_")
    assert len(new_check_id()) == 14


async def test_submit_carries_video_to_run(tmp_settings: Settings) -> None:
    """A check submitted with a video passes it to the run function."""
    from stackcheck.pipeline.run_check import VideoUpload

    seen: dict = {}

    async def fake_run(
        check_id: str, project_label: str, sources: list, settings: Settings, video=None
    ) -> None:
        seen["video"] = video

    runner = JobRunner(tmp_settings, run=fake_run)  # type: ignore[arg-type]
    runner.submit("T", [], video=VideoUpload(upload_id="v" * 16, block_label="A"))
    await asyncio.gather(*runner._tasks.values())
    assert seen["video"] is not None and seen["video"].upload_id == "v" * 16


async def test_submit_writes_queued_status_before_slot_frees(tmp_settings: Settings) -> None:
    """A check queued behind a busy slot already has a status record: state queued."""
    settings = tmp_settings.model_copy(update={"max_concurrent_checks": 1})
    gate = asyncio.Event()

    async def fake_run(check_id: str, project_label: str, sources: list, settings: Settings) -> None:
        await gate.wait()

    runner = JobRunner(settings, run=fake_run)  # type: ignore[arg-type]
    first = runner.submit("A", [])
    second = runner.submit("B", [])
    try:
        assert runner.status(first).state in ("queued", "running")
        assert runner.status(second).state == "queued"
    finally:
        gate.set()
        await asyncio.gather(*runner._tasks.values(), return_exceptions=True)


async def test_on_finished_runs_off_the_event_loop(tmp_settings: Settings) -> None:
    """volume.commit is blocking: it must run in a thread so polls stay fast."""
    import threading
    import time

    async def fake_run(check_id: str, project_label: str, sources: list, settings: Settings) -> Report:  # type: ignore[return]
        return None  # type: ignore[return-value]

    loop_ident = threading.get_ident()
    finished_idents: list[int] = []

    def finished() -> None:
        time.sleep(0.01)
        finished_idents.append(threading.get_ident())

    runner = JobRunner(tmp_settings, run=fake_run, on_finished=finished)  # type: ignore[arg-type]
    runner.submit("OK", [])
    await asyncio.gather(*runner._tasks.values())
    assert finished_idents and all(ident != loop_ident for ident in finished_idents)


async def test_guard_logs_finish_and_failure_traceback(tmp_settings: Settings, caplog) -> None:
    """Finish lines carry elapsed time and flags; failures carry the traceback."""
    import logging

    from stackcheck.models import ProjectEvidence
    from stackcheck.report.build import build_report

    async def ok_run(check_id: str, project_label: str, sources: list, settings: Settings) -> Report:
        return await build_report(
            check_id=check_id,
            project_label=project_label,
            evidence=ProjectEvidence(project_label=project_label, quarters=[]),
            findings=[],
            trace=[],
            settings=settings,
        )

    async def bad_run(check_id: str, project_label: str, sources: list, settings: Settings) -> Report:
        raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        runner = JobRunner(tmp_settings, run=ok_run)  # type: ignore[arg-type]
        runner.submit("OK", [])
        await asyncio.gather(*runner._tasks.values())
    assert "finished in" in caplog.text and "0 flags" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        runner = JobRunner(tmp_settings, run=bad_run)  # type: ignore[arg-type]
        runner.submit("BAD", [])
        await asyncio.gather(*runner._tasks.values(), return_exceptions=True)
    assert "Traceback" in caplog.text
