"""Background checks. Part A: submit, status, restart recovery; Part B fills the rest."""

import asyncio
import inspect
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from stackcheck.config import Settings
from stackcheck.errors import StackCheckError
from stackcheck.models import CheckStatus, Report
from stackcheck.pipeline.run_check import (
    SourceRequest,
    VideoUpload,
    _new_status,
    check_dir,
    run_check,
    write_json,
)

logger = logging.getLogger(__name__)


class QueueFull(StackCheckError):
    pass


def new_check_id() -> str:
    return "chk_" + secrets.token_hex(5)


class JobRunner:
    """Submit checks that run under a semaphore. Status lives in status.json."""

    def __init__(
        self,
        settings: Settings,
        *,
        run: Callable[..., Awaitable[Report]] = run_check,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self._settings = settings
        self._run = run
        self._on_finished = on_finished
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_checks)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running: set[str] = set()

    def submit(
        self,
        project_label: str,
        sources: list[SourceRequest],
        video: VideoUpload | None = None,
        floors_above_ground: int | None = None,
        photos: list[str] | None = None,
    ) -> str:
        """Start a check. Raises QueueFull when queued plus running exceed max_queue.

        The status record exists from this call, so a queued check behind a
        busy slot already polls 200 with state queued instead of 404.
        """
        unfinished = sum(1 for task in self._tasks.values() if not task.done())
        if unfinished >= self._settings.max_queue:
            raise QueueFull(f"Too many checks in flight: {unfinished}")
        check_id = new_check_id()
        write_json(check_dir(self._settings, check_id) / "status.json", _new_status(check_id, project_label))
        self._tasks[check_id] = asyncio.create_task(
            self._guard(check_id, project_label, sources, video, floors_above_ground, photos)
        )
        return check_id

    async def _guard(
        self,
        check_id: str,
        project_label: str,
        sources: list[SourceRequest],
        video: VideoUpload | None,
        floors_above_ground: int | None,
        photos: list[str] | None,
    ) -> None:
        started = time.perf_counter()
        async with self._semaphore:
            self._running.add(check_id)
            try:
                # The injected run seam keeps its 4-arg convention; video, user
                # floors and photos are optional extensions passed only when set.
                extra: dict[str, object] = {"video": video} if video is not None else {}
                if floors_above_ground is not None:
                    extra["floors_above_ground"] = floors_above_ground
                if photos is not None:
                    extra["photos"] = photos
                report = await asyncio.wait_for(
                    self._run(check_id, project_label, sources, self._settings, **extra),  # type: ignore[arg-type]
                    timeout=self._settings.check_timeout_s,
                )
                flags = _flag_count(report)
                logger.warning(
                    "Check %s finished in %.0fs, %d flags", check_id, time.perf_counter() - started, flags
                )
            except TimeoutError:
                logger.warning("Check %s exceeded the time budget", check_id)
            except Exception as exc:
                logger.exception("Check %s failed: %s", check_id, exc)
            finally:
                self._running.discard(check_id)
                await self._finish()

    async def _finish(self) -> None:
        """Run the finished callback off the event loop: it may block (volume commit)."""
        if self._on_finished is None:
            return
        callback = self._on_finished
        if inspect.iscoroutinefunction(callback):
            await callback()  # type: ignore[misc]
        else:
            await asyncio.to_thread(callback)

    def status(self, check_id: str) -> CheckStatus:
        """Latest status, read from this process or from status.json."""
        raw = check_dir(self._settings, check_id) / "status.json"
        if not raw.exists():
            raise StackCheckError(f"Unknown check_id: {check_id}")
        state = CheckStatus.model_validate(json.loads(raw.read_text(encoding="utf-8")))
        state.error = _safe_error(state.error)
        if state.state == "queued":
            line = [cid for cid, task in self._tasks.items() if not task.done() and cid not in self._running]
            if check_id in line:
                state.queue_position = line.index(check_id) + 1
        return state


def _flag_count(report: Any) -> int:
    """FLAG findings in a finished report; 0 when the run returned nothing usable."""
    verdict = getattr(report, "verdict", None)
    if verdict is None:
        return 0
    return int(verdict.flags_high) + int(verdict.flags_medium) + int(verdict.flags_low)


LEGACY_ERROR_FRAGMENTS = ("/data/", "Traceback", "was not registered", "interrupted by restart")


def _safe_error(text: str | None) -> str | None:
    """Map pre-fix stored error text to the generic sentence on read."""
    if text and any(fragment in text for fragment in LEGACY_ERROR_FRAGMENTS):
        return "Something went wrong while reading these filings. Try again, or upload the PDFs."
    return text


def mark_interrupted(settings: Settings) -> int:
    """Mark checks left running or queued by a restart as failed. Returns how many."""
    root = settings.data_dir / "work"
    if not root.exists():
        return 0
    marked = 0
    for path in root.glob("*/status.json"):
        try:
            status = CheckStatus.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
        if status.state not in ("running", "queued"):
            continue
        status.state = "failed"
        status.error = "The server restarted while this check was running. Start a new check."
        status.updated_at = datetime.now(UTC)
        write_json(path, status)
        marked += 1
    return marked
