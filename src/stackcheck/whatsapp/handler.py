"""WhatsApp intake: filings, site photos, voice notes and report questions.

The webhook route returns 200 at once; everything here runs in a background
task. Files from WhatsApp are user uploads, so they use the private reader
(D07) and only salted phone hashes are stored.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stackcheck.config import Settings
from stackcheck.errors import StackCheckError
from stackcheck.ingest.sources import (
    MAX_UPLOAD_BYTES,
    VIDEO_MAX_BYTES,
    register_upload,
    register_video_upload,
)
from stackcheck.llm.base import ModelClient
from stackcheck.llm.registry import reader_client
from stackcheck.llm.structured import ask_json
from stackcheck.models import DISCLAIMER, Report
from stackcheck.pipeline.run_check import VideoUpload, check_dir
from stackcheck.vision.floors import count_floors
from stackcheck.vision.frames import VideoError, probe_duration
from stackcheck.vision.phash import dhash_hex
from stackcheck.vision.sam import SamClient
from stackcheck.vision.sam_parse import parse_sam_output
from stackcheck.whatsapp.client import WhatsAppClient
from stackcheck.whatsapp.models import FlatMessage, WhatsAppUpdate, iter_messages
from stackcheck.whatsapp.photos import find_matching_photo
from stackcheck.whatsapp.store import SenderStore, sender_hash
from stackcheck.whatsapp.voice import ogg_to_wav

logger = logging.getLogger(__name__)

OFF_TOPIC = (
    "I only check RERA progress filings. Send a filing PDF, a progress photo, "
    "or a question about your report."
)
GOT_FILING = "Got the filing. Send more quarters of the same project, then send CHECK (or जाँच)."
CHECK_WORDS = {"check", "जाँच", "जांच"}
BUSY_SENDER = "You've started 3 checks in the last hour. Try again later."
CANT_TELL_PHOTO = "I can't tell from this photo."
IMAGE_CAP_BYTES = 8_000_000


def is_hindi(text: str) -> bool:
    """Devanagari block present, the sender's language is Hindi."""
    return any("\u0900" <= char <= "\u097f" for char in text)


def format_quarter_end(value: str | None) -> str:
    """ISO date to '31 Mar 2025'. Passes anything else through unchanged."""
    if not value:
        return "an unknown quarter"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y")
    except ValueError:
        return value


@dataclass
class WhatsAppDeps:
    """Collaborators. Tests swap every network or pipeline edge with a fake."""

    settings: Settings
    client: WhatsAppClient
    store: SenderStore
    submit_check: Callable[[str, list[str], VideoUpload | None], str]
    check_state: Callable[[str], tuple[str, str | None]]
    read_report: Callable[[str], Report | None]
    count_floors: Callable[[bytes], Awaitable[int | None]]
    answer_question: Callable[[Report, str, str], Awaitable[str]]
    transcribe: Callable[[bytes], Awaitable[str]]


async def handle_update(deps: WhatsAppDeps, update: WhatsAppUpdate) -> None:
    """One update, message by message. A bad message never stops the rest."""
    for message in iter_messages(update):
        try:
            if deps.store.is_duplicate(message.msg_id):
                continue
            await handle_message(deps, message)
        except Exception as exc:
            logger.exception("WhatsApp message failed: %s", type(exc).__name__)
            try:
                await deps.client.send_text(
                    message.sender, "Something went wrong on our side. Please try again."
                )
            except Exception:
                logger.warning("WhatsApp fallback reply failed too")


async def handle_message(deps: WhatsAppDeps, message: FlatMessage) -> None:
    """Route one message. Replies are always plain sentences."""
    secret = deps.settings.whatsapp_app_secret
    hashed = sender_hash(message.sender, secret)
    if message.kind == "text":
        await _handle_text(deps, hashed, message)
    elif message.kind == "document":
        await _handle_document(deps, hashed, message)
    elif message.kind == "image":
        await _handle_image(deps, hashed, message)
    elif message.kind == "audio":
        await _handle_audio(deps, hashed, message)
    elif message.kind == "video":
        await _handle_video(deps, hashed, message)
    else:
        await deps.client.send_text(message.sender, OFF_TOPIC)


async def _handle_text(deps: WhatsAppDeps, hashed: str, message: FlatMessage) -> None:
    body = message.body.strip()
    if body.lower() in CHECK_WORDS:
        deps.store.note_lang(hashed, "hi" if is_hindi(body) else "en")
        await _start_check(deps, hashed, message.sender)
        return
    report = _latest_report(deps, hashed)
    if report is None:
        await deps.client.send_text(message.sender, OFF_TOPIC)
        return
    lang = "hi" if is_hindi(body) else "en"
    deps.store.note_lang(hashed, lang)
    answer = await deps.answer_question(report, body, lang)
    await deps.client.send_text(message.sender, answer)


async def _handle_document(deps: WhatsAppDeps, hashed: str, message: FlatMessage) -> None:
    try:
        data = await deps.client.download_media(
            await deps.client.media_url(message.media_id), MAX_UPLOAD_BYTES + 1
        )
    except StackCheckError as exc:
        await deps.client.send_text(message.sender, str(exc))
        return
    name = message.filename or "filing.pdf"
    try:
        source = register_upload(data, name, deps.settings.data_dir / "uploads")
    except StackCheckError as exc:
        await deps.client.send_text(message.sender, str(exc))
        return
    deps.store.note_upload(hashed, source.doc_id)
    await deps.client.send_text(message.sender, GOT_FILING)


async def _handle_image(deps: WhatsAppDeps, hashed: str, message: FlatMessage) -> None:
    try:
        data = await deps.client.download_media(
            await deps.client.media_url(message.media_id), IMAGE_CAP_BYTES + 1
        )
    except StackCheckError as exc:
        await deps.client.send_text(message.sender, str(exc))
        return
    try:
        digest = dhash_hex(data)
    except Exception:
        await deps.client.send_text(message.sender, CANT_TELL_PHOTO)
        return
    hit = find_matching_photo(deps.settings.data_dir, digest)
    if hit is not None:
        await deps.client.send_text(
            message.sender,
            f"This photo matches the photo on page {hit.page} of the RERA filing "
            f"for the quarter ending {format_quarter_end(hit.quarter_end)}.",
        )
        return
    try:
        floors = await deps.count_floors(data)
    except Exception:
        floors = None
    if floors is None:
        await deps.client.send_text(message.sender, CANT_TELL_PHOTO)
    else:
        await deps.client.send_text(message.sender, f"This photo shows about {floors} floors.")


VIDEO_WHATSAPP_MAX_S = 40.0
GOT_VIDEO = "Got the video. Running the check against your pending filings now."
VIDEO_TOO_LONG = "That video is longer than 40 seconds. Send a shorter clip of the site."
VIDEO_NEEDS_FILING = "Send a filing PDF first, then the video of the site."


async def _handle_video(deps: WhatsAppDeps, hashed: str, message: FlatMessage) -> None:
    """A site video under 40 s plus pending filings runs a check at once."""
    try:
        data = await deps.client.download_media(
            await deps.client.media_url(message.media_id), VIDEO_MAX_BYTES + 1
        )
    except StackCheckError as exc:
        await deps.client.send_text(message.sender, str(exc))
        return
    name = message.filename or "site.mp4"
    try:
        source = register_video_upload(data, name, deps.settings.data_dir / "uploads")
    except StackCheckError:
        await deps.client.send_text(message.sender, "That file is not a video. Send an MP4 or WebM clip.")
        return
    stored = deps.settings.data_dir / "uploads" / "sources" / f"{source.doc_id}.mp4"
    for suffix in (".mp4", ".mov", ".webm"):
        candidate = deps.settings.data_dir / "uploads" / "sources" / f"{source.doc_id}{suffix}"
        if candidate.is_file():
            stored = candidate
            break
    try:
        seconds = await asyncio.to_thread(probe_duration, stored)
    except VideoError:
        await deps.client.send_text(message.sender, "We couldn't read that video. Try a shorter MP4.")
        return
    if seconds > VIDEO_WHATSAPP_MAX_S:
        await deps.client.send_text(message.sender, VIDEO_TOO_LONG)
        return
    pending = deps.store.take_pending(hashed)
    if not pending:
        deps.store.note_video(hashed, source.doc_id)
        await deps.client.send_text(message.sender, VIDEO_NEEDS_FILING)
        return
    if not deps.store.can_check(hashed):
        await deps.client.send_text(message.sender, BUSY_SENDER)
        return
    try:
        check_id = deps.submit_check(
            "WhatsApp check", pending, VideoUpload(upload_id=source.doc_id, block_label="MAIN")
        )
    except StackCheckError as exc:
        await deps.client.send_text(message.sender, str(exc))
        return
    deps.store.note_check(hashed, check_id)
    await deps.client.send_text(message.sender, f"{GOT_VIDEO} {progress_link(deps.settings, check_id)}")
    await _poll_and_report(deps, hashed, message.sender, check_id)


async def _handle_audio(deps: WhatsAppDeps, hashed: str, message: FlatMessage) -> None:
    try:
        data = await deps.client.download_media(
            await deps.client.media_url(message.media_id), MAX_UPLOAD_BYTES + 1
        )
        wav = await ogg_to_wav(data)
        text = await deps.transcribe(wav)
    except StackCheckError as exc:
        await deps.client.send_text(message.sender, str(exc))
        return
    report = _latest_report(deps, hashed)
    if report is None:
        await deps.client.send_text(message.sender, "Send a filing PDF first, then ask about it by voice.")
        return
    lang = "hi" if is_hindi(text) else "en"
    deps.store.note_lang(hashed, lang)
    answer = await deps.answer_question(report, text, lang)
    await deps.client.send_text(message.sender, answer)


def _latest_report(deps: WhatsAppDeps, hashed: str) -> Report | None:
    check_id = deps.store.latest_check(hashed)
    if check_id is None:
        return None
    return deps.read_report(check_id)


async def _start_check(deps: WhatsAppDeps, hashed: str, sender: str) -> None:
    pending = deps.store.take_pending(hashed)
    if not pending:
        await deps.client.send_text(sender, "Send at least one filing PDF first, then send CHECK.")
        return
    if not deps.store.can_check(hashed):
        await deps.client.send_text(sender, BUSY_SENDER)
        return
    video_id = deps.store.take_video(hashed)
    video = VideoUpload(upload_id=video_id, block_label="MAIN") if video_id else None
    try:
        check_id = deps.submit_check("WhatsApp check", pending, video)
    except StackCheckError as exc:
        await deps.client.send_text(sender, str(exc))
        return
    deps.store.note_check(hashed, check_id)
    await deps.client.send_text(sender, f"Checking now. {progress_link(deps.settings, check_id)}")
    await _poll_and_report(deps, hashed, sender, check_id)


_POLL_S = 20.0
_POLLS = 30


async def _poll_and_report(deps: WhatsAppDeps, hashed: str, sender: str, check_id: str) -> None:
    """Wait for the check, then send the headline, findings and evidence."""
    state = "running"
    for _ in range(_POLLS):
        await asyncio.sleep(_POLL_S)
        state, _ = deps.check_state(check_id)
        if state in ("done", "failed"):
            break
    if state != "done":
        await deps.client.send_text(sender, f"Still working on it. {progress_link(deps.settings, check_id)}")
        return
    report = deps.read_report(check_id)
    if report is None:
        await deps.client.send_text(
            sender, f"The check finished but the report is missing. {report_link(deps.settings, check_id)}"
        )
        return
    lang = deps.store.lang(hashed)
    summary = report.summary_hi if lang == "hi" and report.summary_hi else report.summary_en
    lines = [report.verdict.headline]
    if summary:
        lines.append(summary)
    else:
        for finding in [f for f in report.findings if f.status == "FLAG"][:3]:
            lines.append(f"{finding.title}: {finding.explanation}")
    lines.append(report_link(deps.settings, check_id))
    await deps.client.send_text(sender, "\n\n".join(lines))
    await _send_evidence(deps, sender, check_id, report)


async def _send_evidence(deps: WhatsAppDeps, sender: str, check_id: str, report: Report) -> None:
    """First overlay thumbnail, preferring a flagged finding's photo.

    Video contact sheets ride along: an R9 finding names the video id, and
    the sheet file carries it in its name, so it is picked the same way.
    """
    wanted: list[str] = []
    for finding in report.findings:
        if finding.status != "FLAG" and finding.severity != "question":
            continue
        for evidence in finding.evidence:
            if evidence.kind == "photo" and evidence.image_id:
                wanted.append(evidence.image_id)
    media = check_dir(deps.settings, check_id) / "media"
    candidates: list[Path] = []
    if media.is_dir():
        candidates = sorted(media.glob("*.overlay.thumb.jpg")) + sorted(media.glob("*-sheet.png"))
    if not candidates:
        return
    picked = next((p for p in candidates if any(w in p.name for w in wanted)), candidates[0])
    caption = (
        "Contact sheet from the site video."
        if picked.name.endswith("-sheet.png")
        else "Floor-count overlay from the check."
    )
    try:
        media_id = await deps.client.upload_media(picked.read_bytes(), "image/jpeg")
        await deps.client.send_image(sender, media_id, caption)
    except (OSError, StackCheckError) as exc:
        logger.warning("Evidence photo not sent: %s", exc)


def _base(settings: Settings) -> str:
    return settings.public_base_url.rstrip("/")


def progress_link(settings: Settings, check_id: str) -> str:
    base = _base(settings)
    if base:
        return f"Watch it here: {base}/#/check/{check_id}"
    return f"Your check id is {check_id}."


def report_link(settings: Settings, check_id: str) -> str:
    base = _base(settings)
    if base:
        return f"Full report: {base}/#/report/{check_id}"
    return f"Report id: {check_id}."


async def answer_from_report(reader: ModelClient, report: Report, question: str, lang: str) -> str:
    """Answer only from the report's facts, ending with the disclaimer sentence."""
    lines = [f"({finding.status}) {finding.title}: {finding.explanation}" for finding in report.findings]
    language = "Hindi (Devanagari)" if lang == "hi" else "English"
    system = (
        "You answer homebuyer questions using only the findings below. You never use "
        "outside facts and never accuse anyone. Reply in " + language + ". End with this "
        'sentence, translated if needed: "A gap is a question to ask the builder or '
        'RERA, not proof of wrongdoing."'
    )
    try:
        from stackcheck.report.build import SummaryOut

        out, _ = await ask_json(
            reader,
            prompt_name="whatsapp_answer",
            system=system,
            text="Findings:\n" + "\n".join(lines) + f"\n\nQuestion: {question}",
            schema=SummaryOut,
            purpose="whatsapp_answer",
            check_id=None,
        )
        return out.summary
    except Exception as exc:
        logger.warning("WhatsApp answer failed: %s", type(exc).__name__)
        headline = report.verdict.headline
        return f"{headline} {DISCLAIMER}"


async def sam_floor_count(settings: Settings, data: bytes) -> int | None:
    """SAM 3.1 floor count on raw image bytes. None when it can't tell."""
    tmp = settings.data_dir / "whatsapp" / "tmp.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    sam = SamClient(settings, data_dir=settings.data_dir)
    building_text = await sam.segment_image(tmp, "building", check_id=None)
    prompts = [p.strip() for p in settings.sam_prompts.split(",") if p.strip()]
    outputs = [await sam.segment_image(tmp, prompt, check_id=None) for prompt in prompts]
    elements = [d for output in outputs for d in parse_sam_output(output, "window")]
    counted = count_floors(
        "whatsapp", parse_sam_output(building_text, "building"), elements, None, ["building", *prompts]
    )
    tmp.unlink(missing_ok=True)
    if counted.status == "OK" and counted.lower is not None and counted.lower == counted.upper:
        return counted.lower
    return None


def default_transcribe(settings: Settings) -> Callable[[bytes], Awaitable[str]]:
    """Transcribe WAV bytes with the Meta Model API key."""

    async def _transcribe(wav: bytes) -> str:
        from stackcheck.whatsapp.voice import transcribe_wav

        return await transcribe_wav(settings.meta_base_url, settings.meta_api_key, wav)

    return _transcribe


def default_answer(settings: Settings) -> Callable[[Report, str, str], Awaitable[str]]:
    """Answer from report facts with the private reader."""

    async def _answer(report: Report, question: str, lang: str) -> str:
        reader = reader_client(settings, "upload")
        return await answer_from_report(reader, report, question, lang)

    return _answer


def default_floor_count(settings: Settings) -> Callable[[bytes], Awaitable[int | None]]:
    """SAM floor count with the deploy settings."""

    async def _count(data: bytes) -> int | None:
        return await sam_floor_count(settings, data)

    return _count
