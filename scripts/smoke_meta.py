"""Live probe of the Meta Model API. Refuses to run unless STACKCHECK_LIVE=1."""

import asyncio
import io
import os
import sys
import time

import openai
from PIL import Image

from stackcheck.config import get_settings
from stackcheck.costs import total_usd
from stackcheck.llm.base import ImagePart
from stackcheck.llm.meta import MetaClient


def _half_image_png() -> bytes:
    img = Image.new("RGB", (64, 64))
    pixels = img.load()
    for y in range(64):
        for x in range(64):
            pixels[x, y] = (0, 0, 0) if x < 32 else (255, 255, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _usage_names(usage: object) -> list[str]:
    if hasattr(usage, "model_dump"):
        return sorted(usage.model_dump().keys())  # type: ignore[no-any-return]
    return sorted(vars(usage).keys())


def _is_400(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 400 or "400" in str(exc)


async def _developer_retry(system: str, text: str, model: str, api_key: str, base_url: str) -> None:
    """Fallback when `instructions` is rejected: system goes in as a developer message."""
    sdk = openai.AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=3, timeout=120)
    resp = await sdk.responses.create(
        model=model,
        input=[
            {"role": "developer", "content": system},
            {"role": "user", "content": [{"type": "input_text", "text": text}]},
        ],
    )
    print("DEVELOPER_RETRY_REPLY:", resp.output_text)
    print("DEVELOPER_RETRY_USAGE_FIELDS:", _usage_names(resp.usage))


async def _amain() -> None:
    settings = get_settings()
    client = MetaClient(settings, settings.reader_model, data_dir=settings.data_dir)

    system, text = "Reply with one word.", "Say OK."
    try:
        reply = await client.complete(system=system, text=text, purpose="smoke", check_id=None)
    except Exception as exc:
        if not _is_400(exc):
            raise
        message = str(exc).lower()
        if "instructions" in message:
            print("INSTRUCTIONS_PARAM: rejected")
            print("REASONING_PARAM: unknown (instructions failed first)")
            await _developer_retry(
                system, text, settings.reader_model, settings.meta_api_key, settings.meta_base_url
            )
            print("NOTE: switch meta.py to the developer-message form and record it in FACTS.")
            return
        if "reasoning" in message:
            print('REASONING_PARAM: rejected (set STACKCHECK_REASONING_EFFORT="")')
            settings.reasoning_effort = ""
            client = MetaClient(settings, settings.reader_model, data_dir=settings.data_dir)
            reply = await client.complete(system=system, text=text, purpose="smoke", check_id=None)
            print("INSTRUCTIONS_PARAM: accepted")
        else:
            raise
    else:
        print("REASONING_PARAM: accepted")
        print("INSTRUCTIONS_PARAM: accepted")

    print("TEXT_REPLY:", reply.text)
    print("TOKENS: in", reply.input_tokens, "cached", reply.cached_input_tokens, "out", reply.output_tokens)
    print("LEDGER_USD_TOTAL:", total_usd(settings.data_dir))

    image = ImagePart(data=_half_image_png(), mime="image/png")
    started = time.perf_counter()
    seen = await client.complete(
        system="Reply with one word.",
        text="Which half is white, left or right? One word.",
        images=[image],
        purpose="smoke",
        check_id=None,
    )
    print("IMAGE_REPLY:", seen.text, f"({time.perf_counter() - started:.1f}s)")


def main() -> None:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this live probe.")
    asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
