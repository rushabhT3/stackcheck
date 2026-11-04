import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import openai

from stackcheck.cache import cache_key, get_or_compute
from stackcheck.config import Settings
from stackcheck.costs import check_budget, record
from stackcheck.errors import ConfigError, ModelError
from stackcheck.llm.base import ImagePart, ModelReply, to_data_url

logger = logging.getLogger(__name__)

EMPTY_REPLY_ATTEMPTS = 3


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class OpenAICompatClient:
    """Muse Spark Open over OpenAI-compatible Chat Completions. Cached and ledgered."""

    def __init__(
        self,
        provider: Literal["baseten", "fireworks"],
        base_url: str,
        api_key: str,
        model: str,
        *,
        data_dir: Path,
        sdk: Any | None = None,
    ) -> None:
        if not api_key:
            raise ConfigError(f"Set {provider.upper()}_API_KEY in .env")
        self.provider = provider
        self.model = model
        self._base_url = base_url
        self._data_dir = data_dir
        self.sdk = (
            sdk
            if sdk is not None
            else openai.AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=3, timeout=120)
        )

    async def complete(
        self,
        *,
        system: str,
        text: str,
        images: Sequence[ImagePart] = (),
        purpose: str,
        check_id: str | None,
        code_sha: str | None = None,
    ) -> ModelReply:
        parts: dict[str, object] = {
            "provider": self.provider,
            "model": self.model,
            "system_sha": _sha_text(system),
            "text_sha": _sha_text(text),
            "image_shas": [_sha_bytes(img.data) for img in images],
            "params": {"temperature": 0.0},
        }
        if code_sha is not None:
            parts["code_sha"] = code_sha

        async def compute() -> dict:
            # Same env-driven budgets as everywhere else, anchored at this client's ledger.
            check_budget(Settings(data_dir=self._data_dir))
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            content.extend({"type": "image_url", "image_url": {"url": to_data_url(img)}} for img in images)
            resp = None
            for _ in range(EMPTY_REPLY_ATTEMPTS):
                try:
                    resp = await self.sdk.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": content},
                        ],
                        temperature=0.0,
                    )
                except Exception as exc:
                    raise ModelError(f"{self.provider}/{self.model}: {exc}") from exc
                if resp.choices[0].message.content:
                    break
            else:
                raise ModelError(f"{self.provider}/{self.model}: empty reply")
            assert resp is not None
            usage = resp.usage
            cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
            content = resp.choices[0].message.content
            reply = ModelReply(
                text=content,
                provider=self.provider,
                model=self.model,
                input_tokens=usage.prompt_tokens,
                cached_input_tokens=int(cached),
                output_tokens=usage.completion_tokens,
                cache_hit=False,
            )
            return reply.model_dump(mode="json")

        try:
            value, hit = await get_or_compute(parts, compute, data_dir=self._data_dir)
        except Exception as exc:
            record(
                provider=self.provider,
                model=self.model,
                purpose=purpose,
                check_id=check_id,
                error=f"{type(exc).__name__}: {exc}"[:300],
                data_dir=self._data_dir,
            )
            logger.error(
                "Model call failed purpose=%s model=%s check_id=%s: %s %s",
                purpose,
                self.model,
                check_id,
                type(exc).__name__,
                exc,
            )
            raise
        if not hit:
            logger.info(
                "Model cache miss purpose=%s model=%s key=%s system_sha=%s text_sha=%s "
                "image_shas=%s params=%s",
                purpose,
                self.model,
                cache_key(parts),
                parts["system_sha"],
                parts["text_sha"],
                parts["image_shas"],
                parts["params"],
            )
        if hit:
            record(
                provider=self.provider,
                model=self.model,
                purpose=purpose,
                check_id=check_id,
                cache_hit=True,
                data_dir=self._data_dir,
            )
            return ModelReply.model_validate(value).model_copy(update={"cache_hit": True})
        reply = ModelReply.model_validate(value)
        record(
            provider=self.provider,
            model=self.model,
            purpose=purpose,
            check_id=check_id,
            input_tokens=reply.input_tokens,
            cached_input_tokens=reply.cached_input_tokens,
            output_tokens=reply.output_tokens,
            data_dir=self._data_dir,
        )
        return reply
