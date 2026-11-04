"""HTTP API. Thin edge: validation and path safety here, everything else in the pipeline."""

import asyncio
import hashlib
import importlib.metadata
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from fastapi import BackgroundTasks, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse

from stackcheck.config import Settings, get_settings
from stackcheck.errors import FetchError, IngestError, StackCheckError
from stackcheck.ingest.sources import (
    VIDEO_EXTENSIONS,
    _allowed_hosts,
    register_upload,
    register_video_upload,
    validate_source_url,
)
from stackcheck.models import Report, SourceDoc
from stackcheck.pipeline.jobs import JobRunner, QueueFull, mark_interrupted
from stackcheck.pipeline.run_check import SourceRequest, VideoUpload, check_dir, run_check
from stackcheck.vision.frames import (
    VideoError,
    probe_duration,
    video_tool_versions,
    video_tools_available,
)
from stackcheck.whatsapp.client import WhatsAppClient, valid_signature
from stackcheck.whatsapp.handler import (
    WhatsAppDeps,
    default_answer,
    default_floor_count,
    default_transcribe,
    handle_update,
)
from stackcheck.whatsapp.models import WhatsAppUpdate
from stackcheck.whatsapp.store import SenderStore


def _whatsapp_deps(settings: Settings, runner: JobRunner) -> WhatsAppDeps:
    """Live WhatsApp collaborators. Tests monkeypatch this seam."""
    return WhatsAppDeps(
        settings=settings,
        client=WhatsAppClient(settings),
        store=SenderStore(
            settings.data_dir / "whatsapp" / "senders.json",
            ttl_days=settings.whatsapp_media_ttl_days,
            checks_per_hour=settings.whatsapp_checks_per_hour,
            uploads_dir=settings.data_dir / "uploads" / "sources",
        ),
        submit_check=lambda label, ids, video=None: runner.submit(
            label, [SourceRequest(upload_id=i) for i in ids], video=video
        ),
        check_state=lambda cid: _whatsapp_state(runner, cid),
        read_report=lambda cid: _load_report(settings, cid),
        count_floors=default_floor_count(settings),
        answer_question=default_answer(settings),
        transcribe=default_transcribe(settings),
    )


def _whatsapp_state(runner: JobRunner, check_id: str) -> tuple[str, str | None]:
    try:
        status = runner.status(check_id)
    except StackCheckError:
        return ("unknown", None)
    return (status.state, status.error)


async def _run_whatsapp_update(settings: Settings, runner: JobRunner, raw: bytes) -> None:
    try:
        update = WhatsAppUpdate.model_validate_json(raw)
        await handle_update(_whatsapp_deps(settings, runner), update)
    except Exception:
        logger.exception("WhatsApp background update failed")


logger = logging.getLogger(__name__)

try:
    APP_VERSION = importlib.metadata.version("stackcheck")
except importlib.metadata.PackageNotFoundError:
    APP_VERSION = "0.0.0+deploy"  # Modal copies source without distribution metadata
CHECK_ID_RE = re.compile(r"^chk_[0-9a-f]{10}$")
MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(png|jpe?g|webp)$")
_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
PHOTO_MAX_BYTES = 10_000_000


class CheckRequest(BaseModel):
    project_label: str = Field(min_length=1)
    sources: list[SourceRequest] = Field(default=[], max_length=24)
    floors_above_ground: int | None = Field(default=None, ge=1)
    video_upload_id: str | None = None
    video_block_label: str = "MAIN"
    video_same_project: bool = False
    video_credit: str | None = Field(default=None, max_length=200)
    photo_upload_ids: list[str] = Field(default=[], max_length=5)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _snake(name: str) -> str:
    out = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


BUSY_MESSAGE = "We're busy right now. Try again in a few minutes."


def _check_url(url: str, settings: Settings) -> str | None:
    """None when the URL may be fetched, else the plain-sentence rejection."""
    try:
        validate_source_url(url, _allowed_hosts(settings))
    except FetchError as exc:
        return str(exc)
    return None


def _upload_path(settings: Settings, upload_id: str) -> Path:
    return settings.data_dir / "uploads" / "sources" / f"{upload_id}.pdf"


def _upload_stored(settings: Settings, upload_id: str) -> Path | None:
    """The stored upload whatever its extension (pdf, video, photo), else None."""
    for suffix in (".pdf", *VIDEO_EXTENSIONS, *PHOTO_EXTENSIONS):
        candidate = settings.data_dir / "uploads" / "sources" / f"{upload_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _sniff_photo_ext(data: bytes) -> str | None:
    """File extension for JPEG, PNG or WebP magic bytes; None when not a photo."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _register_photo_upload(data: bytes, filename: str, dest_dir: Path) -> SourceDoc:
    """Accept an uploaded site photo after validating magic bytes and size."""
    if len(data) > PHOTO_MAX_BYTES:
        raise IngestError(f"Upload larger than {PHOTO_MAX_BYTES} bytes: {filename}")
    ext = _sniff_photo_ext(data)
    if ext is None:
        raise IngestError(f"Upload is not a photo: {filename}")
    digest = hashlib.sha256(data).hexdigest()
    doc_id = digest[:16]
    sources_dir = dest_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / f"{doc_id}{ext}").write_bytes(data)
    safe_name = PurePosixPath(filename.replace("\\", "/")).name or f"upload{ext}"
    logger.info("Stored photo %s (%d bytes)", doc_id, len(data))
    return SourceDoc(
        doc_id=doc_id,
        origin="upload",
        url=None,
        filename=safe_name,
        sha256=digest,
        retrieved_at=datetime.now(UTC),
        page_count=0,
    )


def _load_report(settings: Settings, check_id: str) -> Report | None:
    path = check_dir(settings, check_id) / "report.json"
    if not path.exists():
        return None
    return Report.model_validate_json(path.read_text(encoding="utf-8"))


def _resolve_media(settings: Settings, check_id: str, name: str) -> Path | None:
    base = (Path(settings.data_dir) / "work" / check_id / "media").resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path


def _load_samples(settings: Settings) -> Any:
    try:
        return json.loads(Path(settings.samples_file).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def _load_eval(settings: Settings) -> Any:
    try:
        return json.loads((settings.data_dir / "eval.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"measured": False}


class _CachedStatic(StaticFiles):
    """Hashed /assets files: immutable for a year. index.html stays no-cache."""

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _static_target(settings: Settings, path: str) -> Path | str | None:
    """File to serve, 'frontend' for the not-built text, or None for 404.

    Only extension-less routes fall back to the SPA shell; unknown files 404.
    """
    dist_dir = Path(settings.web_dist_dir)
    if not dist_dir.exists():
        return "frontend" if path == "" else None
    base = dist_dir.resolve()
    if path:
        candidate = (base / path).resolve()
        if candidate.is_relative_to(base) and candidate.is_file():
            return candidate
        if "." in PurePosixPath(path).name:
            return None
    index = base / "index.html"
    return index if index.is_file() else None


def _client_ip(request: Request) -> tuple[str, str]:
    """Client IP plus which header it came from. Prefers forwarded headers."""
    for header in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        raw = request.headers.get(header)
        if raw:
            return raw.split(",")[0].strip(), header
    client = request.client
    return (client.host if client else "unknown"), "direct"


def _is_active(runner: Any, check_id: str) -> bool:
    try:
        return runner.status(check_id).state in ("queued", "running")
    except StackCheckError:
        return True


def _ip_allowed(hits: list[float], active: int, now: float, hourly_max: int, active_max: int) -> bool:
    """Per-client limits: at most active_max live checks, hourly_max new per hour."""
    recent = [t for t in hits if now - t < 3600]
    return active < active_max and len(recent) < hourly_max


async def _run_job(
    check_id: str,
    project_label: str,
    sources: list[SourceRequest],
    settings: Settings,
    video: VideoUpload | None = None,
    photos: list[str] | None = None,
) -> Report:
    """Copy uploaded PDFs, video and photos into the check folder, then run the pipeline."""
    work = check_dir(settings, check_id)
    dest = work / "sources"
    dest.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if source.upload_id is not None:
            name = f"{source.upload_id}.pdf"
            stored = settings.data_dir / "uploads" / "sources" / name
            if not stored.exists() and not (dest / name).exists():
                raise FetchError("We couldn't find that uploaded PDF. Upload it again and retry.")
            if stored.exists():
                data = await asyncio.to_thread(stored.read_bytes)
                await asyncio.to_thread((dest / name).write_bytes, data)
    if video is not None:
        stored_video = _upload_stored(settings, video.upload_id)
        if stored_video is None:
            raise FetchError("We couldn't open the uploaded video on our side.")
        data = await asyncio.to_thread(stored_video.read_bytes)
        await asyncio.to_thread((dest / f"{video.upload_id}{stored_video.suffix}").write_bytes, data)
    if photos:
        for upload_id in photos:
            stored_photo = _upload_stored(settings, upload_id)
            if stored_photo is None:
                raise FetchError("We couldn't find that uploaded photo. Upload it again and retry.")
            data = await asyncio.to_thread(stored_photo.read_bytes)
            await asyncio.to_thread((dest / f"{upload_id}{stored_photo.suffix}").write_bytes, data)
    if photos is not None:
        return await run_check(check_id, project_label, sources, settings, video=video, photos=photos)
    return await run_check(check_id, project_label, sources, settings, video=video)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    if app.state.runner is None:
        app.state.runner = JobRunner(app.state.settings, run=_run_job)
        mark_interrupted(app.state.settings)
    yield


def create_app(settings: Settings | None = None, runner: JobRunner | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and a fake runner."""
    if not logging.getLogger().handlers:
        # Modal runs the ASGI app with no log config: default to INFO on stderr
        # so step timings and fetch causes reach the app logs. Uvicorn and
        # pytest install their own handlers, so this branch never fires there.
        logging.basicConfig(level=logging.INFO)
    resolved = settings or get_settings()
    app = FastAPI(title="StackCheck", lifespan=_lifespan)
    app.state.settings = resolved
    app.state.runner = runner
    app.state.ip_hits = {}

    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
            " img-src 'self' data: blob:; font-src 'self'; connect-src 'self';"
            " object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(StackCheckError)
    async def _stackcheck_error(request: Request, exc: StackCheckError) -> JSONResponse:
        if isinstance(exc, QueueFull):
            return _error(429, "queue_full", BUSY_MESSAGE)
        return _error(400, _snake(type(exc).__name__), str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.info("Validation failed: %s", exc.errors())
        return _error(422, "validation_error", "The request body did not pass validation.")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error(404, "not_found", "Not found.")
        return _error(exc.status_code, f"http_{exc.status_code}", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unknown(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error: %s", exc)
        return _error(500, "internal_error", "Something went wrong.")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": APP_VERSION,
            "video_tools": video_tools_available(),
            "video_tool_versions": video_tool_versions(),
        }

    @app.post("/api/uploads")
    async def uploads(request: Request, file: Annotated[UploadFile, File(...)]) -> Any:
        settings = request.app.state.settings
        filename = file.filename or "upload.pdf"
        lowered = filename.lower()
        is_video = (file.content_type or "").startswith("video/") or lowered.endswith(VIDEO_EXTENSIONS)
        is_photo = (file.content_type or "").startswith("image/") or lowered.endswith(PHOTO_EXTENSIONS)
        if not is_video and not is_photo:
            if file.content_type != "application/pdf" and not lowered.endswith(".pdf"):
                return _error(400, "not_a_pdf", "Uploads must be PDF files.")
            cap = settings.fetch_max_bytes
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > cap:
                    return _error(413, "upload_too_large", f"Uploads are limited to {cap} bytes.")
            data = b"".join(chunks)
            if not data.startswith(b"%PDF"):
                return _error(400, "not_a_pdf", "The uploaded file is not a PDF.")
            source = await asyncio.to_thread(register_upload, data, filename, settings.data_dir / "uploads")
            return {"upload_id": source.doc_id, "filename": source.filename, "sha256": source.sha256}
        if is_photo:
            cap = PHOTO_MAX_BYTES
            chunks = []
            size = 0
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > cap:
                    return _error(413, "upload_too_large", f"Photo uploads are limited to {cap} bytes.")
            data = b"".join(chunks)
            try:
                source = await asyncio.to_thread(
                    _register_photo_upload, data, filename, settings.data_dir / "uploads"
                )
            except StackCheckError as exc:
                return _error(400, "not_a_photo", str(exc))
            return {
                "upload_id": source.doc_id,
                "filename": source.filename,
                "sha256": source.sha256,
                "kind": "photo",
            }
        cap = settings.video_max_bytes
        chunks = []
        size = 0
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > cap:
                return _error(413, "upload_too_large", f"Video uploads are limited to {cap} bytes.")
        data = b"".join(chunks)
        try:
            source = await asyncio.to_thread(
                register_video_upload, data, filename, settings.data_dir / "uploads"
            )
        except StackCheckError as exc:
            return _error(400, "not_a_video", str(exc))
        stored = settings.data_dir / "uploads" / "sources" / f"{source.doc_id}.mp4"
        for suffix in VIDEO_EXTENSIONS:
            candidate = settings.data_dir / "uploads" / "sources" / f"{source.doc_id}{suffix}"
            if candidate.is_file():
                stored = candidate
                break
        try:
            seconds = await asyncio.to_thread(probe_duration, stored)
        except VideoError:
            return _error(400, "video_unreadable", "We couldn't read that video. Try a shorter MP4.")
        if seconds > settings.video_max_seconds:
            return _error(
                400,
                "video_too_long",
                f"Videos must be {settings.video_max_seconds:.0f} seconds or shorter.",
            )
        return {
            "upload_id": source.doc_id,
            "filename": source.filename,
            "sha256": source.sha256,
            "kind": "video",
        }

    @app.post("/api/checks", status_code=202)
    async def create_check(body: CheckRequest, request: Request) -> Any:
        settings = request.app.state.settings
        label = " ".join(body.project_label.split())
        if not label:
            return _error(422, "validation_error", "Enter a project name.")
        if len(label) > 80:
            return _error(422, "validation_error", "Project name must be 80 characters or fewer.")
        if not body.sources and not body.photo_upload_ids and body.video_upload_id is None:
            return _error(
                422, "validation_error", "Enter a project name and at least one filing, photo or video."
            )
        for source in body.sources:
            if source.url is not None:
                problem = _check_url(source.url, settings)
                if problem is not None:
                    return _error(400, "host_not_allowed", problem)
            elif source.upload_id is not None:
                stored = await asyncio.to_thread(_upload_path, settings, source.upload_id)
                if not await asyncio.to_thread(stored.exists):
                    return _error(404, "upload_not_found", f"Unknown upload {source.upload_id}.")
        video: VideoUpload | None = None
        if body.video_upload_id is not None:
            # The confirmation only makes sense against a filing; video-only
            # and photo-only checks skip it. Filing checks keep the 400.
            if body.sources and not body.video_same_project:
                return _error(
                    400,
                    "validation_error",
                    "Confirm the video shows the same project and block as the filing.",
                )
            stored_video = await asyncio.to_thread(_upload_stored, settings, body.video_upload_id)
            if stored_video is None or stored_video.suffix not in VIDEO_EXTENSIONS:
                return _error(404, "upload_not_found", f"Unknown upload {body.video_upload_id}.")
            block = " ".join(body.video_block_label.split()) or "MAIN"
            credit = (body.video_credit or "").strip() or None
            video = VideoUpload(upload_id=body.video_upload_id, block_label=block[:80], credit=credit)
        photos: list[str] = []
        for upload_id in body.photo_upload_ids:
            stored_photo = await asyncio.to_thread(_upload_stored, settings, upload_id)
            if stored_photo is None or stored_photo.suffix not in PHOTO_EXTENSIONS:
                return _error(404, "upload_not_found", f"Unknown upload {upload_id}.")
            photos.append(upload_id)
        ip, via = _client_ip(request)
        logger.info("Check request from %s via %s", ip, via)
        now = time.time()
        hits: dict[str, list[tuple[str, float]]] = request.app.state.ip_hits
        entries = [(cid, ts) for cid, ts in hits.get(ip, []) if now - ts < 3600]
        active = sum(1 for cid, _ in entries if _is_active(request.app.state.runner, cid))
        if not _ip_allowed(
            [ts for _, ts in entries],
            active,
            now,
            settings.per_ip_hourly_max,
            settings.per_ip_active_max,
        ):
            return _error(429, "queue_full", BUSY_MESSAGE)
        runner = request.app.state.runner
        # Only runners with photo support take the photos kwarg; without photos the call is unchanged.
        extra: dict[str, Any] = {"photos": photos} if photos else {}
        check_id = runner.submit(
            label, list(body.sources), video=video, floors_above_ground=body.floors_above_ground, **extra
        )
        entries.append((check_id, now))
        hits[ip] = entries
        return {"check_id": check_id}

    @app.get("/api/checks/{check_id}")
    async def get_status(check_id: str, request: Request) -> Any:
        runner = request.app.state.runner
        try:
            return runner.status(check_id)
        except StackCheckError:
            return _error(404, "check_not_found", f"Unknown check {check_id}.")

    @app.get("/api/checks/{check_id}/report")
    async def get_report(check_id: str, request: Request) -> Any:
        settings = request.app.state.settings
        runner = request.app.state.runner
        try:
            state = runner.status(check_id)
        except StackCheckError:
            return _error(404, "check_not_found", f"Unknown check {check_id}.")
        if state.state != "done":
            return _error(404, "report_not_ready", f"Report for {check_id} is not ready.")
        report = await asyncio.to_thread(_load_report, settings, check_id)
        if report is None:
            return _error(404, "report_not_ready", f"Report for {check_id} is not ready.")
        return report

    @app.get("/api/samples")
    async def samples(request: Request) -> Any:
        settings = request.app.state.settings
        return await asyncio.to_thread(_load_samples, settings)

    @app.get("/api/eval")
    async def get_eval(request: Request) -> Any:
        settings = request.app.state.settings
        return await asyncio.to_thread(_load_eval, settings)

    @app.get("/webhooks/whatsapp")
    async def whatsapp_verify(request: Request) -> Any:
        settings = request.app.state.settings
        params = request.query_params
        if (
            params.get("hub.mode") == "subscribe"
            and settings.whatsapp_verify_token
            and params.get("hub.verify_token") == settings.whatsapp_verify_token
        ):
            return PlainTextResponse(params.get("hub.challenge", ""))
        return _error(403, "verification_failed", "That verification did not match.")

    @app.post("/webhooks/whatsapp")
    async def whatsapp_incoming(request: Request, background: BackgroundTasks) -> Any:
        settings = request.app.state.settings
        raw = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not valid_signature(raw, signature, settings.whatsapp_app_secret):
            return _error(403, "bad_signature", "That signature did not match.")
        try:
            update = WhatsAppUpdate.model_validate_json(raw)
        except ValueError:
            logger.warning("WhatsApp payload not parsed; ignored")
            return {"ok": True}
        background.add_task(
            _run_whatsapp_update,
            request.app.state.settings,
            request.app.state.runner,
            update.model_dump_json().encode(),
        )
        return {"ok": True}

    @app.get("/media/{check_id}/{name}")
    async def media(check_id: str, name: str, request: Request) -> Any:
        settings = request.app.state.settings
        if not CHECK_ID_RE.fullmatch(check_id) or not MEDIA_NAME_RE.fullmatch(name):
            return _error(404, "media_not_found", "No such image.")
        path = await asyncio.to_thread(_resolve_media, settings, check_id, name)
        if path is None:
            return _error(404, "media_not_found", "No such image.")
        suffix = path.suffix.lower().lstrip(".")
        return FileResponse(path, media_type=_MEDIA_TYPES.get(suffix, "application/octet-stream"))

    dist = Path(resolved.web_dist_dir)
    if (dist / "assets").exists():
        app.mount("/assets", _CachedStatic(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def fallback(path: str, request: Request) -> Any:
        settings = request.app.state.settings
        if path.startswith("api/") or path.startswith("media/"):
            return _error(404, "not_found", "Not found.")
        target = await asyncio.to_thread(_static_target, settings, path)
        if target == "frontend":
            return PlainTextResponse("Frontend not built")
        if target is None or isinstance(target, str):
            return _error(404, "not_found", "Not found.")
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    return app
