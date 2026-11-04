"""End-to-end check. Photos, floors and investigate all run; summary stays English-only."""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import pymupdf
from PIL import Image
from pydantic import BaseModel, model_validator

from stackcheck.config import Settings, model_for
from stackcheck.errors import FetchError, IngestError
from stackcheck.extract.filing import extract_filing
from stackcheck.extract.photos import describe_cached, select_photos
from stackcheck.ingest.pdf import ingest_pdf, render_page
from stackcheck.ingest.qpr import apply_qpr_slot, ingest_qpr, is_qpr_url, parse_qpr_slot
from stackcheck.ingest.sources import VIDEO_EXTENSIONS, fetch_pdf
from stackcheck.llm.base import ModelClient
from stackcheck.llm.registry import reader_client, vision_client
from stackcheck.models import (
    AgentStep,
    CheckStatus,
    Detection,
    ExtractedImage,
    Finding,
    IngestedDoc,
    PhotoRecord,
    ProjectEvidence,
    QuarterEvidence,
    QuarterFiling,
    Report,
    SourceDoc,
    StepStatus,
    VideoEvidence,
)
from stackcheck.pipeline.investigate import (
    FindingBudget,
    investigate_finding,
    needs_investigation,
)
from stackcheck.pipeline.reread import confirm_r4_flags
from stackcheck.report.build import build_report
from stackcheck.rules.checks import RuleSettings, apply_user_floors
from stackcheck.rules.engine import run_rules, run_rules_capped
from stackcheck.vision.contact_sheet import build_contact_sheet
from stackcheck.vision.floors import count_floors
from stackcheck.vision.frames import (
    VideoCountError,
    VideoError,
    VideoSampleError,
    VideoToolError,
    consensus,
    probe_duration,
    sample_sharp_frames,
    video_tools_available,
)
from stackcheck.vision.overlay import draw_floor_overlay, write_overlay_thumb
from stackcheck.vision.phash import dhash_hex
from stackcheck.vision.sam import SamClient
from stackcheck.vision.sam_parse import parse_sam_output

logger = logging.getLogger(__name__)

STEP_NAMES = ("fetch", "read_filings", "photos", "floors", "rules", "investigate", "report")

DIFFERENT_PROJECTS_MESSAGE = "These filings belong to different projects. Run one project at a time."
UNSURE_PROJECT_NOTE = "We couldn't confirm these filings belong to one project."
SIMILARITY_THRESHOLD = 0.85

_RERAP_RE = re.compile(r"RERAP\d+", re.IGNORECASE)
_REG_NO_RE = re.compile(r"[A-Z0-9][A-Z0-9/\-]{6,}")


def _normalize_identity(value: str | None) -> str | None:
    """Upper case with only letters and digits, so punctuation can't fake a mismatch."""
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())
    return cleaned or None


def _looks_like_reg_no(value: str | None) -> bool:
    """A printed registration identifier has no spaces or stray punctuation.

    OCR garbling ("BRERAP92324.I 1963 ...") fails this; a clean number passes.
    A value that fails is unreadable, never proof of a different project.
    """
    return value is not None and _REG_NO_RE.fullmatch(value.strip()) is not None


def _rerap_prefix(url: str | None) -> str | None:
    """The Bihar file prefix shared by one project's PDFs, if the URL carries one."""
    if not url:
        return None
    match = _RERAP_RE.search(url)
    return match.group(0).upper() if match else None


def _project_similarity(
    name_a: str | None, promoter_a: str | None, name_b: str | None, promoter_b: str | None
) -> float:
    """Mean name/promoter similarity on normalized text; 0 when anything is missing."""
    from difflib import SequenceMatcher

    a_name, a_promoter = _normalize_identity(name_a), _normalize_identity(promoter_a)
    b_name, b_promoter = _normalize_identity(name_b), _normalize_identity(promoter_b)
    if not a_name or not a_promoter or not b_name or not b_promoter:
        return 0.0
    name_score = SequenceMatcher(None, a_name, b_name).ratio()
    promoter_score = SequenceMatcher(None, a_promoter, b_promoter).ratio()
    return (name_score + promoter_score) / 2


def _check_same_project(quarters: list[QuarterEvidence]) -> Literal["same", "unsure"]:
    """Raise IngestError only for clear proof of different projects.

    The Bihar URL prefix is the truth when every filing carries the same one.
    Otherwise reg numbers count only when both look like printed identifiers;
    the fallback is name plus promoter similarity. Anything less certain runs
    with cross-quarter rules capped instead of failing.
    """
    if len(quarters) <= 1:
        return "same"
    regs = [q.filing.project_reg_no for q in quarters]
    prefixes = {_rerap_prefix(q.source.url) for q in quarters} - {None}
    usable_regs = [reg for reg in regs if _looks_like_reg_no(reg)]
    logger.info(
        "Project guard: regs=%s prefixes=%s names=%s",
        [_normalize_identity(reg) for reg in regs],
        sorted(prefixes),
        [_normalize_identity(q.filing.project_name) for q in quarters],
    )
    if len(prefixes) > 1:
        raise IngestError(DIFFERENT_PROJECTS_MESSAGE)
    if prefixes:
        return "same"
    if len({_normalize_identity(reg) for reg in usable_regs}) > 1:
        raise IngestError(DIFFERENT_PROJECTS_MESSAGE)
    if len(usable_regs) == len(quarters) and usable_regs:
        return "same"
    names = [q.filing.project_name for q in quarters]
    promoters = [q.filing.promoter_name for q in quarters]
    if all(names) and all(promoters):
        scores = [
            _project_similarity(names[0], promoters[0], name, promoter)
            for name, promoter in zip(names[1:], promoters[1:], strict=True)
        ]
        logger.info("Project guard similarity scores=%s", [round(score, 3) for score in scores])
        if scores and min(scores) >= SIMILARITY_THRESHOLD:
            return "same"
    return "unsure"


def _dedupe_quarters(quarters: list[QuarterEvidence]) -> list[QuarterEvidence]:
    """Same quarter end twice: keep both filings, each carrying the same notice.

    Two filings can state one quarter end with different figures (a re-filing,
    or pages that disagree like Project F's December 2024 page 1 against its
    March 2025 page 2). Dropping one silently hides exactly the conflict a
    check exists to surface, so both stay and pairwise rules compare them.
    """
    counts: dict[date, int] = {}
    for quarter in quarters:
        if quarter.filing.quarter_end is not None:
            counts[quarter.filing.quarter_end] = counts.get(quarter.filing.quarter_end, 0) + 1
    for quarter in quarters:
        quarter_end = quarter.filing.quarter_end
        if quarter_end is not None and counts[quarter_end] > 1:
            quarter.filing.notes.append(
                f"Two filings state the quarter ending {quarter_end.isoformat()}; both are shown."
            )
    return quarters


# Errors authored as plain sentences, safe to show users. Anything else
# (SDK internals, serialization errors) gets a generic note; the raw text
# stays in the server log only.
_USER_SAFE_ERRORS = (FetchError, IngestError)
_GENERIC_FAILURE_NOTE = "Something went wrong while reading these filings. Try again, or upload the PDFs."


class SourceRequest(BaseModel):
    url: str | None = None
    upload_id: str | None = None  # doc_id of a PDF already registered in sources/

    @model_validator(mode="after")
    def _exactly_one(self) -> "SourceRequest":
        if (self.url is None) == (self.upload_id is None):
            raise ValueError("exactly one of url and upload_id is required")
        return self


class VideoUpload(BaseModel):
    """One user-uploaded site video for the check, with the filer's confirmation."""

    upload_id: str
    block_label: str = "MAIN"
    credit: str | None = None  # bench/demo source credit, shown under the video


SAME_FILE_NOTE = "You added the same file twice, so we used it once."

FETCH_CONCURRENCY = 4

PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _fetch_reason(error: FetchError) -> str:
    """Short cause for one failed fetch, read off the stable user sentence."""
    message = str(error)
    if "connection reset" in message.lower():
        return "connection reset"
    match = re.search(r"The portal answered (\d{3})", message)
    if match:
        return f"portal refused (HTTP {match.group(1)})"
    if "did not answer within" in message:
        return "timed out"
    if "security certificate" in message:
        return "certificate could not be verified"
    if "wasn't found" in message:
        return "not found (HTTP 404)"
    if "doesn't point to a PDF" in message:
        return "not a PDF"
    if "exceeds the" in message and "byte cap" in message:
        return "file too large"
    if "stopped asking for" in message:
        return "paused after repeated failures"
    return "download failed"


def _fetch_short_note(failures: list[FetchError], total: int) -> str | None:
    """One plain report line for sources that never arrived. None when all did."""
    if not failures:
        return None
    reason = _fetch_reason(failures[0])
    return f"We could not fetch {len(failures)} of {total} quarters ({reason}). Add them to check them."


async def _fetch_all(
    sources: list[SourceRequest],
    settings: Settings,
    work: Path,
    check_id: str | None,
) -> tuple[list[SourceDoc], list[FetchError]]:
    """Fetch every source, up to 4 at once. Order of docs matches sources.

    One bad URL no longer fails the whole fetch: its error is collected in
    failures while the PDFs that arrived still run. The per-host lock and
    spacing inside fetch_pdf stay in charge of politeness, so same-host
    filings still go one at a time.
    """

    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def _one(request: SourceRequest) -> SourceDoc | FetchError:
        async with semaphore:
            try:
                if request.url is not None:
                    try:
                        return await asyncio.wait_for(
                            fetch_pdf(request.url, settings, work, check_id=check_id),
                            timeout=settings.fetch_doc_timeout_s,
                        )
                    except TimeoutError:
                        raise FetchError(
                            "That download took too long. Try again or upload the PDF instead."
                        ) from None
                assert request.upload_id is not None
                uploads_dir = settings.data_dir / "uploads" / "sources"
                return await _load_upload(work, request.upload_id, uploads_dir)
            except FetchError as exc:
                return exc

    docs: list[SourceDoc] = []
    failures: list[FetchError] = []
    for result in await asyncio.gather(*[_one(request) for request in sources]):
        if isinstance(result, FetchError):
            failures.append(result)
        else:
            docs.append(result)
    return docs, failures


def _normalize_url(url: str) -> str:
    """Lowercase scheme and host, drop the default port, fragment and trailing slash."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if (parts.scheme == "https" and parts.port == 443) or (parts.scheme == "http" and parts.port == 80):
        host_port = host
    elif parts.port is not None:
        host_port = f"{host}:{parts.port}"
    else:
        host_port = host
    path = parts.path.rstrip("/") or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{host_port}{path}{query}"


def _dedupe_sources(
    sources: list[SourceRequest],
) -> tuple[list[SourceRequest], bool]:
    """Collapse identical URLs and uploads before the pipeline. Returns (kept, dropped_any)."""
    seen: set[tuple[str, str]] = set()
    kept: list[SourceRequest] = []
    for source in sources:
        if source.url is not None:
            key: tuple[str, str] = ("url", _normalize_url(source.url))
        elif source.upload_id is not None:
            key = ("upload", source.upload_id)
        else:  # pragma: no cover - the endpoint rejects sourceless requests
            key = ("none", str(len(kept)))
        if key in seen:
            continue
        seen.add(key)
        kept.append(source)
    return kept, len(kept) != len(sources)


def check_dir(settings: Settings, check_id: str) -> Path:
    """Check folder. Callers pre-register uploads into <check_dir>/sources/."""
    return settings.data_dir / "work" / check_id


def write_json(path: Path, payload: object) -> None:
    """Atomic JSON write: temp file plus replace, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if isinstance(payload, BaseModel):
        tmp.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    else:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _new_status(check_id: str, project_label: str) -> CheckStatus:
    now = datetime.now(UTC)
    return CheckStatus(
        check_id=check_id,
        project_label=project_label,
        state="queued",
        steps=[StepStatus(name=name, state="pending") for name in STEP_NAMES],
        progress=0.0,
        created_at=now,
        updated_at=now,
    )


def _mark(status: CheckStatus, work: Path, name: str, state: str, note: str | None = None) -> None:
    now = datetime.now(UTC)
    for step in status.steps:
        if step.name == name:
            if state == "running" and step.state == "pending":
                step.started_at = now
            if state in ("done", "failed", "skipped"):
                step.finished_at = now
                if step.started_at is not None:
                    elapsed = (now - step.started_at).total_seconds()
                    logger.info(
                        "Step %s for %s %s in %.1fs",
                        name,
                        status.check_id,
                        state,
                        elapsed,
                    )
            step.state = state  # type: ignore[assignment]
            step.note = note
    finished = sum(1 for step in status.steps if step.state in ("done", "skipped"))
    status.progress = finished / len(status.steps)
    status.updated_at = now
    write_json(work / "status.json", status)


async def _load_upload(work: Path, upload_id: str, uploads_dir: Path | None = None) -> SourceDoc:
    """Read an upload into SourceDoc. Falls back to the uploads store and copies it in."""
    pdf_path = work / "sources" / f"{upload_id}.pdf"
    if not pdf_path.exists() and uploads_dir is not None:
        stored = uploads_dir / f"{upload_id}.pdf"
        if stored.exists():
            body = stored.read_bytes()
            if hashlib.sha256(body).hexdigest()[:16] == upload_id:
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                pdf_path.write_bytes(body)
    if not pdf_path.exists():
        raise FetchError("We couldn't find that uploaded PDF. Upload it again and retry.")
    body = await asyncio.to_thread(pdf_path.read_bytes)
    digest = hashlib.sha256(body).hexdigest()
    if digest[:16] != upload_id:
        raise IngestError("We couldn't find that uploaded PDF. Upload it again and retry.")
    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count
    return SourceDoc(
        doc_id=upload_id,
        origin="upload",
        url=None,
        filename=f"{upload_id}.pdf",
        sha256=digest,
        retrieved_at=datetime.now(UTC),
        page_count=page_count,
    )


def _has_report_content(quarters: list[QuarterEvidence]) -> bool:
    """True when at least one filing has blocks, activities or money figures."""
    for quarter in quarters:
        filing = quarter.filing
        if filing.blocks or filing.activities:
            return True
        money = (
            filing.financials.received_this_quarter,
            filing.financials.spent_this_quarter,
            filing.financials.cumulative_received,
            filing.financials.cumulative_spent,
            filing.financials.estimated_total_cost,
        )
        if any(value is not None for value in money):
            return True
    return False


async def _segment(
    sam: SamClient, semaphore: asyncio.Semaphore, path: Path, prompt: str, check_id: str | None
) -> str:
    """One SAM call, capped with the other in-flight calls for this check."""
    async with semaphore:
        return await sam.segment_image(path, prompt, check_id=check_id)


def _video_source_path(work: Path, upload_id: str, uploads_dir: Path | None = None) -> Path | None:
    """The staged video; else the uploads store copy, verified by hash and staged in.

    Same storage code as PDFs (`_load_upload`): the API flow may register into
    the uploads store without a staging copy (Modal runs `run_check` directly),
    so the loader falls back to the store instead of failing.
    """
    for suffix in VIDEO_EXTENSIONS:
        staged = work / "sources" / f"{upload_id}{suffix}"
        if staged.is_file():
            logger.info("Video %s staged at %s", upload_id, staged)
            return staged
    if uploads_dir is not None:
        for suffix in VIDEO_EXTENSIONS:
            stored = uploads_dir / f"{upload_id}{suffix}"
            logger.info("Video %s store lookup %s exists=%s", upload_id, stored, stored.is_file())
            if not stored.is_file():
                continue
            body = stored.read_bytes()
            if hashlib.sha256(body).hexdigest()[:16] != upload_id:
                continue
            staged = work / "sources" / stored.name
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(body)
            logger.info("Video %s staged from the uploads store", upload_id)
            return staged
    logger.info("Video %s not found under %s", upload_id, work / "sources")
    return None


def _photo_source_path(work: Path, upload_id: str, uploads_dir: Path | None = None) -> Path | None:
    """The staged site photo; else the uploads store copy, verified by hash and staged in.

    Same storage code as videos (`_video_source_path`): the API flow may
    register into the uploads store without a staging copy, so the loader
    falls back to the store instead of failing.
    """
    for suffix in PHOTO_EXTENSIONS:
        staged = work / "sources" / f"{upload_id}{suffix}"
        if staged.is_file():
            return staged
    if uploads_dir is not None:
        for suffix in PHOTO_EXTENSIONS:
            stored = uploads_dir / f"{upload_id}{suffix}"
            if not stored.is_file():
                continue
            body = stored.read_bytes()
            if hashlib.sha256(body).hexdigest()[:16] != upload_id:
                continue
            staged = work / "sources" / stored.name
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(body)
            return staged
    return None


async def _ingest_site_photo(
    upload_id: str,
    order: int,
    work: Path,
    settings: Settings,
    images_dir: Path,
    *,
    check_id: str | None,
    clients: Mapping[str, Any] | None,
) -> PhotoRecord:
    """One uploaded site photo as a PhotoRecord. Raises when unreadable.

    User photos carry no camera stamp to read and no filing block to guess,
    so the filing context is a stub. The photo runs the same vision floor
    estimate as filing and video photos (private model, D07) through the
    facts cache and the cost ledger, plus the SAM count path with the same
    tiers as filing photos.
    """
    src = _photo_source_path(work, upload_id, settings.data_dir / "uploads" / "sources")
    if src is None:
        raise IngestError("We couldn't find that uploaded photo. Upload it again and retry.")
    raw = await asyncio.to_thread(src.read_bytes)
    try:
        with Image.open(BytesIO(raw)) as img:
            width, height = img.size
            buf = BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            png = buf.getvalue()
    except Exception as exc:
        raise IngestError("That file is not a photo we can open.") from exc
    await asyncio.to_thread((images_dir / f"{upload_id}.png").write_bytes, png)
    image = ExtractedImage(
        image_id=upload_id,
        doc_id=upload_id,
        page=0,
        index=order,
        path=f"images/{upload_id}.png",
        width=width,
        height=height,
        sha256=hashlib.sha256(png).hexdigest(),
    )
    vision = (
        clients["vision"]
        if clients is not None and "vision" in clients
        else vision_client(settings, "upload")
    )
    facts = await describe_cached(
        image,
        images_dir / f"{upload_id}.png",
        QuarterFiling(doc_id=upload_id),
        vision,
        check_id=check_id,
        data_dir=settings.data_dir,
        model=model_for("upload", settings),
    )
    return PhotoRecord(image=image, facts=facts, dhash=dhash_hex(png), floors=None, overlay_path=None)


def _video_skip_note(exc: Exception) -> str:
    """User sentence for a skipped video, naming the failing stage."""
    if isinstance(exc, VideoToolError):
        return "Video skipped: our server could not open video files just now."
    if isinstance(exc, VideoSampleError):
        return "Video skipped: we could not sample frames from it."
    if isinstance(exc, VideoCountError):
        return "Video skipped: we could not count floors in the frames."
    if isinstance(exc, (FetchError, VideoError)):
        return "Video skipped: this file is not a video we can open."
    return "Video skipped: it could not be read."


async def run_video_evidence(
    upload: VideoUpload,
    work: Path,
    settings: Settings,
    check_id: str | None,
    clients: Mapping[str, Any] | None,
    filing: QuarterFiling,
    credit: str | None = None,
) -> VideoEvidence:
    """Sample a site video, count floors per frame, agree a range, draw a sheet.

    D07: the video is a user upload, so frames use the private model. Raises
    VideoToolError when ffmpeg/ffprobe is missing, VideoError when the file is
    bad, FetchError when the file is gone.
    """
    if not video_tools_available():
        raise VideoToolError("video tools missing on this server (ffmpeg/ffprobe)")
    path = _video_source_path(work, upload.upload_id, settings.data_dir / "uploads" / "sources")
    if path is None:
        raise FetchError("We couldn't open the uploaded video on our side.")
    media_dir = work / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    try:
        duration = await asyncio.to_thread(probe_duration, path)
    except VideoError:
        logger.error("Video probe failed for %s (upload %s)", path, upload.upload_id)
        raise
    frames = await sample_sharp_frames(path, work / "video_frames", duration)
    if clients is not None and "sam" in clients:
        sam = clients["sam"]
    else:
        sam = SamClient(settings, data_dir=settings.data_dir)
    if clients is not None and "vision" in clients:
        vision = clients["vision"]
    else:
        vision = vision_client(settings, "upload")
    model = model_for("upload", settings)
    semaphore = asyncio.Semaphore(4)
    prompts = [p.strip() for p in settings.sam_prompts.split(",") if p.strip()]
    counted: list[tuple[Path, Any]] = []

    async def _frame_one(index: int, frame_path: Path) -> tuple[Path, Any]:
        raw = await asyncio.to_thread(frame_path.read_bytes)
        with Image.open(BytesIO(raw)) as img:
            width, height = img.size
        image = ExtractedImage(
            image_id=f"{upload.upload_id}-f{index}",
            doc_id=upload.upload_id,
            page=0,
            index=index,
            path=str(frame_path),
            width=width,
            height=height,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        facts = await describe_cached(
            image,
            frame_path,
            filing,
            vision,
            check_id=check_id,
            data_dir=settings.data_dir,
            model=model,
        )
        building_text = await _segment(sam, semaphore, frame_path, "building", check_id)
        building_dets = parse_sam_output(building_text, "building")
        elements = []
        for prompt in prompts:
            output = await _segment(sam, semaphore, frame_path, prompt, check_id)
            elements.extend(parse_sam_output(output, prompt))
        count = count_floors(image.image_id, building_dets, elements, facts, ["building", *prompts])
        return frame_path, count

    for index, (frame_path, _sharpness) in enumerate(frames):
        try:
            counted.append(await _frame_one(index, frame_path))
        except Exception as exc:
            logger.error(
                "Video frame %d failed for %s (%s): %s",
                index,
                check_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
    if not counted:
        raise VideoCountError(f"No frames could be counted in {path.name}.")
    result = consensus([count for _, count in counted])
    sheet_frames = [(frame_path, count) for frame_path, count in counted if count.status == "OK"]
    if not sheet_frames:
        sheet_frames = [(frame_path, count) for frame_path, count in counted]
    sheet_path = media_dir / f"{upload.upload_id}-sheet.png"
    sheet_rel: str | None = f"media/{upload.upload_id}-sheet.png"
    try:
        await asyncio.to_thread(build_contact_sheet, sheet_frames[:4], sheet_path, caption=credit)
    except Exception as exc:
        logger.warning("Video contact sheet failed for %s: %s", check_id, exc)
        sheet_rel = None
    votes = [
        count.lower
        for _, count in counted
        if count.status == "OK"
        and count.lower is not None
        and (count.upper is None or count.lower == count.upper)
    ]
    # Video tier policy (Nov 2, bench vs public records): frame agreement is
    # not accuracy, so video tiers cap at ESTIMATE. CONFIDENT returns only
    # with person references for at least 5 clips plus measured error.
    tier = "ESTIMATE" if result.tier in ("CONFIDENT", "ESTIMATE") else None
    return VideoEvidence(
        video_id=upload.upload_id,
        block_label=upload.block_label,
        tier=tier,
        lower=result.lower,
        upper=result.upper,
        frames_used=result.frames_used,
        frame_counts=votes,
        contact_sheet=sheet_rel,
        uploaded_on=date.today(),
        credit=credit,
    )


async def run_check(
    check_id: str,
    project_label: str,
    sources: list[SourceRequest],
    settings: Settings,
    *,
    clients: Mapping[str, Any] | None = None,
    video: VideoUpload | None = None,
    floors_above_ground: int | None = None,
    photos: list[str] | None = None,
) -> Report:
    """Fetch PDFs to report.json. `clients` ({reader, vision, sam}) is for tests.

    `photos` holds uploaded site photo ids in user order. With no filings the
    check counts the site photos only and the filing rules see zero quarters.
    """
    work = check_dir(settings, check_id)
    status = _new_status(check_id, project_label)
    status.state = "running"
    write_json(work / "status.json", status)
    current = ""
    try:
        current = "fetch"
        _mark(status, work, current, "running")
        sources, duplicate_source = _dedupe_sources(sources)
        duplicate_input = duplicate_source
        docs, fetch_failures = await _fetch_all(sources, settings, work, check_id)
        fetch_note = _fetch_short_note(fetch_failures, len(sources))
        if not docs and not (photos or video is not None):
            if fetch_failures:
                raise fetch_failures[0]
            raise IngestError("Add at least one filing, photo or video to check.")
        _mark(status, work, current, "done", fetch_note)

        current = "read_filings"
        _mark(status, work, current, "running")
        model_sem = asyncio.Semaphore(4)

        async def _read_one(index: int, source: SourceDoc) -> tuple[int, IngestedDoc, QuarterEvidence]:
            if source.url is not None and is_qpr_url(source.url):
                # Quarterly report web page: deterministic parse, no reader call.
                html_path = work / "sources" / f"{source.doc_id}.html"
                doc, filing = await asyncio.to_thread(ingest_qpr, html_path, source, work)
                write_json(work / "ingest" / f"{source.doc_id}.json", doc)
                write_json(work / "facts" / f"{source.doc_id}.filing.json", filing)
                return index, doc, QuarterEvidence(source=source, filing=filing, photos=[])
            pdf_path = work / "sources" / f"{source.doc_id}.pdf"
            doc = await asyncio.to_thread(ingest_pdf, pdf_path, source, work)
            write_json(work / "ingest" / f"{source.doc_id}.json", doc)
            async with model_sem:
                reader = clients["reader"] if clients is not None else reader_client(settings, source.origin)
                filing = await extract_filing(doc, reader, check_id=check_id, pdf_path=pdf_path)
            write_json(work / "facts" / f"{source.doc_id}.filing.json", filing)
            return index, doc, QuarterEvidence(source=source, filing=filing, photos=[])

        read_out = await asyncio.gather(*[_read_one(i, s) for i, s in enumerate(docs)])
        ordered = sorted(read_out, key=lambda row: row[0])
        seen_sha: set[str] = set()
        triples: list[tuple[int, IngestedDoc, QuarterEvidence]] = []
        for row in ordered:
            if row[1].source.sha256 in seen_sha:
                duplicate_input = True
                continue
            seen_sha.add(row[1].source.sha256)
            triples.append(row)
        ingested_docs = [doc for _, doc, _ in triples]
        quarters = [quarter for _, _, quarter in triples]
        same_project = _check_same_project(quarters)
        quarters = _dedupe_quarters(quarters)
        if duplicate_input and quarters:
            quarters[0].filing.notes.append(SAME_FILE_NOTE)
        if fetch_note is not None and quarters:
            quarters[0].filing.notes.append(fetch_note)
        if same_project == "unsure":
            for quarter in quarters:
                quarter.filing.notes.append(UNSURE_PROJECT_NOTE)
        if docs and not _has_report_content(quarters):
            raise IngestError(
                "We couldn't find a quarterly progress report in this file. "
                "Upload the builder's QPR PDF for one quarter."
            )
        _mark(status, work, current, "done")

        current = "photos"
        _mark(status, work, current, "running")
        images_dir = work / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        async def _photo_one(
            source: SourceDoc, quarter: QuarterEvidence, image: ExtractedImage
        ) -> tuple[QuarterEvidence, PhotoRecord | None, str | None]:
            pdf_path = work / "sources" / f"{source.doc_id}.pdf"
            vision = (
                clients["vision"]
                if clients is not None and "vision" in clients
                else vision_client(settings, source.origin)
            )
            try:
                if image.from_page_render or not image.sha256:
                    png = await asyncio.to_thread(render_page, pdf_path, image.page)
                    with Image.open(BytesIO(png)) as img:
                        width, height = img.size
                    image = image.model_copy(
                        update={
                            "width": width,
                            "height": height,
                            "sha256": hashlib.sha256(png).hexdigest(),
                        }
                    )
                else:
                    png = await asyncio.to_thread((images_dir / f"{image.image_id}.png").read_bytes)
                (images_dir / f"{image.image_id}.png").write_bytes(png)
                async with model_sem:
                    facts = await describe_cached(
                        image,
                        images_dir / f"{image.image_id}.png",
                        quarter.filing,
                        vision,
                        check_id=check_id,
                        data_dir=settings.data_dir,
                        model=model_for(source.origin, settings),
                    )
                slot = parse_qpr_slot(image.image_id)
                if slot is not None:
                    facts = apply_qpr_slot(facts, slot)
                record = PhotoRecord(
                    image=image, facts=facts, dhash=dhash_hex(png), floors=None, overlay_path=None
                )
            except Exception as exc:
                logger.warning("Photo %s skipped: %s", image.image_id, exc)
                return quarter, None, f"Photo {image.image_id} skipped: {exc}"
            write_json(work / "facts" / f"{image.image_id}.photo.json", record)
            return quarter, record, None

        photo_tasks = [
            _photo_one(source, quarter, image)
            for source, quarter, doc in zip(docs, quarters, ingested_docs, strict=True)
            for image in select_photos(doc)
        ]
        for quarter, record, note in await asyncio.gather(*photo_tasks):
            if note is not None:
                quarter.filing.notes.append(note)
            if record is not None:
                quarter.photos.append(record)
        site_photos: list[PhotoRecord] = []
        for order, upload_id in enumerate(dict.fromkeys(photos or [])):
            try:
                site_record = await _ingest_site_photo(
                    upload_id, order, work, settings, images_dir, check_id=check_id, clients=clients
                )
            except Exception as exc:
                logger.warning("Site photo %s skipped: %s", upload_id, exc)
                if quarters:
                    quarters[0].filing.notes.append(f"Site photo {upload_id} skipped: {exc}")
                continue
            site_photos.append(site_record)
            write_json(work / "facts" / f"{site_record.image.image_id}.photo.json", site_record)
        _mark(status, work, current, "done")

        current = "floors"
        _mark(status, work, current, "running")
        media_dir = work / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        if clients is not None and "sam" in clients:
            sam = clients["sam"]
        else:
            sam = SamClient(settings, data_dir=settings.data_dir)
        semaphore = asyncio.Semaphore(4)
        prompts = [p.strip() for p in settings.sam_prompts.split(",") if p.strip()]
        floor_ctx: dict[str, tuple[list[Detection], dict[str, list[Detection]], list[str]]] = {}

        async def _floors_one(
            quarter: QuarterEvidence | None, photo: PhotoRecord
        ) -> tuple[QuarterEvidence | None, str | None]:
            try:
                path = images_dir / f"{photo.image.image_id}.png"
                building_text = await _segment(sam, semaphore, path, "building", check_id)
                building_dets = parse_sam_output(building_text, "building")
                element_dets: dict[str, list[Detection]] = {}
                outputs = await asyncio.gather(
                    *[_segment(sam, semaphore, path, prompt, check_id) for prompt in prompts]
                )
                for prompt, output in zip(prompts, outputs, strict=True):
                    element_dets[prompt] = parse_sam_output(output, prompt)
                elements = [d for dets in element_dets.values() for d in dets]
                count = count_floors(
                    photo.image.image_id, building_dets, elements, photo.facts, ["building", *prompts]
                )
                overlay_path = media_dir / f"{photo.image.image_id}.overlay.png"
                await asyncio.to_thread(
                    draw_floor_overlay, path, count, building_dets + elements, overlay_path
                )
                try:
                    await asyncio.to_thread(write_overlay_thumb, overlay_path)
                except Exception as exc:
                    logger.warning("Thumb failed for %s: %s", photo.image.image_id, exc)
                photo.floors = count
                photo.overlay_path = f"media/{photo.image.image_id}.overlay.png"
                floor_ctx[photo.image.image_id] = (building_dets, element_dets, prompts)
                write_json(work / "facts" / f"{photo.image.image_id}.photo.json", photo)
            except Exception as exc:
                logger.warning("Floor count failed for %s: %s", photo.image.image_id, exc)
                return quarter, f"Photo {photo.image.image_id} floor count failed: {exc}"
            return quarter, None

        floor_tasks = [
            _floors_one(quarter, photo)
            for quarter in quarters
            for photo in quarter.photos
            if photo.facts is not None and photo.facts.kind == "elevation"
        ]
        site_anchor = quarters[0] if quarters else None
        floor_tasks.extend(_floors_one(site_anchor, photo) for photo in site_photos)
        for quarter, note in await asyncio.gather(*floor_tasks):
            if note is not None:
                if quarter is not None:
                    quarter.filing.notes.append(note)
                else:
                    logger.warning("Site photo floor note with no filing: %s", note)
        video_evidence: VideoEvidence | None = None
        if video is not None:
            try:
                filing_for_video = quarters[-1].filing if quarters else QuarterFiling(doc_id=video.upload_id)
                video_evidence = await run_video_evidence(
                    video, work, settings, check_id, clients, filing_for_video, credit=video.credit
                )
            except Exception as exc:
                logger.error(
                    "Video step failed for %s (upload %s, %s): %s",
                    check_id,
                    video.upload_id,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                if quarters:
                    quarters[0].filing.notes.append(_video_skip_note(exc))
        _mark(status, work, current, "done")

        current = "rules"
        _mark(status, work, current, "running")
        quarters.sort(key=lambda q: (q.filing.quarter_end is None, q.filing.quarter_end))
        if site_photos and quarters:
            dated = [q for q in quarters if q.filing.quarter_end is not None]
            latest = (
                max(dated, key=lambda q: q.filing.quarter_end)  # type: ignore[arg-type]
                if dated
                else quarters[-1]
            )
            latest.photos.extend(site_photos)
        apply_user_floors(quarters, floors_above_ground)
        evidence = ProjectEvidence(
            project_label=project_label,
            quarters=quarters,
            video=video_evidence,
            photos=site_photos,
            floors_above_ground=floors_above_ground,
        )
        rule_settings = RuleSettings(
            stamp_required_states={s.strip() for s in settings.stamp_required_states.split(",") if s.strip()}
        )
        rules_fn = run_rules_capped if same_project == "unsure" else run_rules
        findings = rules_fn(evidence, rule_settings) if quarters else []
        write_json(work / "evidence.json", evidence)
        write_json(work / "findings.json", [f.model_dump(mode="json") for f in findings])
        _mark(status, work, current, "done")
        try:
            if clients is not None:
                reread_reader: ModelClient | None = clients["reader"]
            elif docs:
                reread_reader = reader_client(settings, docs[0].origin)
            else:
                reread_reader = None
            if reread_reader is not None:
                findings = await confirm_r4_flags(findings, evidence, reread_reader, work, check_id)
                write_json(work / "findings.json", [f.model_dump(mode="json") for f in findings])
        except Exception:
            logger.exception("Reread step failed for %s", check_id)
            _mark(
                status,
                work,
                current,
                "done",
                "Second read unavailable; flags kept without a second look.",
            )

        current = "investigate"
        _mark(status, work, current, "running")
        investigate_started = time.perf_counter()
        trace: list[AgentStep] = []
        try:
            crops_dir = work / "crops"
            investigate_sem = asyncio.Semaphore(2)

            async def _investigate_one(
                finding: Finding,
                quarter: QuarterEvidence,
                block: str,
                photo: PhotoRecord,
                ctx: tuple[list[Detection], dict[str, list[Detection]], list[str]],
                shared: FindingBudget,
            ) -> list[AgentStep]:
                async with investigate_sem:
                    building_dets, element_dets, tried = ctx
                    if clients is not None and "vision" in clients:
                        investigator_vision = clients["vision"]
                    else:
                        investigator_vision = vision_client(settings, quarter.source.origin)
                    steps = await investigate_finding(
                        finding=finding,
                        quarter=quarter,
                        block_label=block,
                        photo=photo,
                        building_dets=building_dets,
                        element_dets=element_dets,
                        tried_prompts=tried,
                        sam=sam,
                        vision=investigator_vision,
                        settings=settings,
                        check_id=check_id,
                        images_dir=images_dir,
                        media_dir=media_dir,
                        crops_dir=crops_dir,
                        shared=shared,
                    )
                    write_json(work / "facts" / f"{photo.image.image_id}.photo.json", photo)
                    return steps

            async def _investigate_finding(
                finding: Finding,
                quarter: QuarterEvidence,
                block: str,
                jobs: list[tuple[PhotoRecord, tuple[list[Detection], dict[str, list[Detection]], list[str]]]],
            ) -> list[AgentStep]:
                """One finding at a time: photos run in order sharing one budget.

                A give_up, decision or stop on any photo closes the finding, so
                the trace holds no repeats and exactly one terminal line.
                """
                shared = FindingBudget()
                out: list[AgentStep] = []
                for photo, ctx in jobs:
                    if shared.closed:
                        break
                    out.extend(await _investigate_one(finding, quarter, block, photo, ctx, shared))
                return out

            investigate_tasks = []
            for finding in findings:
                if finding.rule != "R1_PHOTO_VS_CLAIM":
                    continue
                if finding.status != "CANT_VERIFY" or finding.quarter_end is None:
                    continue
                quarter = next(q for q in quarters if q.filing.quarter_end == finding.quarter_end)
                block = finding.block_label or "MAIN"
                jobs = []
                for photo in quarter.photos:
                    if not needs_investigation(finding, photo):
                        continue
                    ctx = floor_ctx.get(photo.image.image_id)
                    if ctx is None:
                        continue
                    jobs.append((photo, ctx))
                if jobs:
                    investigate_tasks.append(_investigate_finding(finding, quarter, block, jobs))
            for steps in await asyncio.gather(*investigate_tasks):
                for step in steps:
                    step.step += len(trace)
                trace.extend(steps)
            if trace:
                findings = rules_fn(evidence, rule_settings)
                write_json(work / "evidence.json", evidence)
                write_json(work / "findings.json", [f.model_dump(mode="json") for f in findings])
            write_json(work / "trace.json", [s.model_dump(mode="json") for s in trace])
            logger.info(
                "Investigate for %s: %d steps in %.1fs",
                check_id,
                len(trace),
                time.perf_counter() - investigate_started,
            )
            _mark(status, work, current, "done")
        except Exception:
            logger.exception("Investigate step failed for %s", check_id)
            trace = []
            _mark(
                status,
                work,
                current,
                "failed",
                "Investigator unavailable; report built without an agent trace.",
            )

        current = "report"
        _mark(status, work, current, "running")
        if clients is not None:
            summary_reader: ModelClient | None = clients["reader"]
        elif docs:
            summary_reader = reader_client(settings, docs[0].origin)
        else:
            summary_reader = None
        report = await build_report(
            check_id=check_id,
            project_label=project_label,
            evidence=evidence,
            findings=findings,
            trace=trace,
            settings=settings,
            reader=summary_reader,
        )
        write_json(work / "report.json", report)
        _mark(status, work, current, "done")
        status.state = "done"
        write_json(work / "status.json", status)
        return report
    except TimeoutError:
        minutes = max(1, round(settings.check_timeout_s / 60))
        logger.warning("Check %s exceeded the time budget in %s", check_id, current)
        note = f"This check took longer than {minutes} minutes. Try fewer quarters."
        if current:
            _mark(status, work, current, "failed", note)
        status.state = "failed"
        status.error = note
        write_json(work / "status.json", status)
        raise
    except Exception as exc:
        logger.warning("Check %s failed in %s: %s", check_id, current, exc)
        note = str(exc) if isinstance(exc, _USER_SAFE_ERRORS) else _GENERIC_FAILURE_NOTE
        if current:
            _mark(status, work, current, "failed", note)
        status.state = "failed"
        status.error = note
        write_json(work / "status.json", status)
        raise
