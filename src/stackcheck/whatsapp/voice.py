"""Voice notes: ffmpeg to WAV, then Muse Voice Transcribe. Failures stay vague."""

import asyncio
import logging

import httpx

from stackcheck.errors import StackCheckError

logger = logging.getLogger(__name__)

TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"  # Meta Model API, checked Nov 2026
MAX_MINUTES = 10
_WAV_BYTES_PER_MINUTE = 16000 * 2 * 60  # mono 16-bit PCM at 16 kHz


class VoiceError(StackCheckError):
    """The voice note could not be turned into text. The reason stays in the log."""


async def ogg_to_wav(data: bytes) -> bytes:
    """Opus bytes to mono 16-bit PCM WAV at 16 kHz. Rejects anything over 10 minutes."""
    if len(data) > MAX_MINUTES * _WAV_BYTES_PER_MINUTE:
        raise VoiceError("That voice note is too long.")
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            "-f",
            "wav",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VoiceError("Voice notes can't be processed here.") from exc
    out, err = await process.communicate(data)
    if process.returncode != 0:
        logger.warning("ffmpeg failed: %s", (err or b"")[:200])
        raise VoiceError("That voice note could not be understood.")
    if len(out) > (MAX_MINUTES + 1) * _WAV_BYTES_PER_MINUTE:
        raise VoiceError("That voice note is too long.")
    return out


async def transcribe_wav(base_url: str, api_key: str, wav: bytes) -> str:
    """Batch transcription over the Meta Model API. Returns plain text."""
    url = base_url.rstrip("/") + "/asr/transcribe"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("note.wav", wav, "audio/wav")},
                data={"model": TRANSCRIBE_MODEL},
            )
    except httpx.HTTPError as exc:
        logger.warning("Transcription request failed: %s", type(exc).__name__)
        raise VoiceError("That voice note could not be understood.") from exc
    if response.status_code != 200:
        logger.warning("Transcription refused: HTTP %s: %s", response.status_code, response.text[:200])
        raise VoiceError("That voice note could not be understood.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise VoiceError("That voice note could not be understood.") from exc
    for key in ("text", "transcript", "content"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    logger.warning("Transcription shape unknown: %s", str(payload)[:200])
    raise VoiceError("That voice note could not be understood.")
