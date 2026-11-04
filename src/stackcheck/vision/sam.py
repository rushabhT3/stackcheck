import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any

import openai

from stackcheck.cache import cache_key, get_or_compute
from stackcheck.config import Settings
from stackcheck.costs import check_budget, record
from stackcheck.errors import ConfigError

logger = logging.getLogger(__name__)

_PROMPT = re.compile(r"^[a-z]+( [a-z]+){0,5}$")


def check_prompt(prompt: str) -> str:
    """SAM wants one short noun phrase, never a question."""
    if not _PROMPT.match(prompt):
        raise ValueError(f"Prompt must be 1 to 6 lowercase words, a noun phrase: {prompt!r}")
    return prompt


def sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    raise ValueError("Only PNG and JPEG images are supported")


class SamClient:
    """SAM 3.1 over the Responses API, streaming. Every call is cached and ledgered."""

    def __init__(self, settings: Settings, *, data_dir: Path, sdk: Any | None = None) -> None:
        if not settings.meta_api_key:
            raise ConfigError("Set MODEL_API_KEY in .env")
        self._settings = settings
        self._data_dir = data_dir
        self.sdk = (
            sdk
            if sdk is not None
            else openai.AsyncOpenAI(
                base_url=settings.meta_base_url, api_key=settings.meta_api_key, max_retries=3, timeout=120
            )
        )

    def _data_url(self, data: bytes) -> str:
        import base64

        return f"data:{sniff_mime(data)};base64," + base64.b64encode(data).decode("ascii")

    async def _file_id(self, data: bytes, name: str) -> str:
        sha = hashlib.sha256(data).hexdigest()
        parts = {"provider": "meta", "kind": "file_upload", "sha": sha}

        async def compute() -> dict:
            resp = await self.sdk.files.create(file=(name, data, sniff_mime(data)), purpose="user_data")
            return {"file_id": resp.id}

        value, _ = await get_or_compute(parts, compute, data_dir=self._data_dir)
        return str(value["file_id"])

    async def segment_image(self, image_path: Path, prompt: str, *, check_id: str | None) -> str:
        """Run one prompt on one image. Returns the raw output text."""
        check_prompt(prompt)
        started = time.perf_counter()
        data = await asyncio.to_thread(image_path.read_bytes)
        sha = hashlib.sha256(data).hexdigest()
        mode = self._settings.sam_image_mode
        if mode == "file_id":
            image_part: dict[str, Any] = {
                "type": "input_image",
                "file_id": await self._file_id(data, image_path.name),
            }
        else:
            image_part = {"type": "input_image", "image_url": self._data_url(data)}
        parts = {
            "provider": "meta",
            "model": self._settings.sam_model,
            "prompt": prompt,
            "image_sha": sha,
            "mode": mode,
        }

        async def compute() -> dict:
            check_budget(self._settings)
            stream = await self.sdk.responses.create(
                model=self._settings.sam_model,
                input=[
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}, image_part],
                    }
                ],
                stream=True,
                metadata={"mask_encoding": "one_bit"},
            )
            output_text = "".join(
                [event.delta async for event in stream if event.type == "response.output_text.delta"]
            )
            return {"output_text": output_text}

        purpose = f"sam:{prompt}"
        try:
            value, hit = await get_or_compute(parts, compute, data_dir=self._data_dir)
        except Exception as exc:
            record(
                provider="meta",
                model=self._settings.sam_model,
                purpose=purpose,
                check_id=check_id,
                error=f"{type(exc).__name__}: {exc}"[:300],
                data_dir=self._data_dir,
            )
            logger.error(
                "SAM call failed purpose=%s model=%s check_id=%s image=%s: %s %s",
                purpose,
                self._settings.sam_model,
                check_id,
                image_path.name,
                type(exc).__name__,
                exc,
            )
            raise
        if not hit:
            logger.info(
                "SAM cache miss purpose=sam:%s model=%s key=%s image_sha=%s prompt=%s mode=%s",
                prompt,
                self._settings.sam_model,
                cache_key(parts),
                sha,
                prompt,
                mode,
            )
        if hit:
            record(
                provider="meta",
                model=self._settings.sam_model,
                purpose=purpose,
                check_id=check_id,
                units=0,
                cache_hit=True,
                data_dir=self._data_dir,
            )
        else:
            record(
                provider="meta",
                model=self._settings.sam_model,
                purpose=purpose,
                check_id=check_id,
                units=1,
                data_dir=self._data_dir,
            )
        elapsed = time.perf_counter() - started
        logger.info(
            "SAM %s on %s: %d chars in %.1fs (hit=%s, check %s)",
            prompt,
            image_path.name,
            len(value["output_text"]),
            elapsed,
            hit,
            check_id,
        )
        return str(value["output_text"])
