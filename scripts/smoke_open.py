"""Live text-call probe of Baseten and Fireworks. Refuses to run unless STACKCHECK_LIVE=1."""

import asyncio
import os
import sys
import time

from stackcheck.config import get_settings
from stackcheck.llm.openai_compat import OpenAICompatClient


async def _probe(provider: str, key: str, base_url: str, model: str) -> None:
    from pathlib import Path

    if not key:
        print(f"{provider}: SKIPPED (no key)")
        return
    client = OpenAICompatClient(
        provider=provider,  # type: ignore[arg-type]
        base_url=base_url,
        api_key=key,
        model=model,
        data_dir=Path("data"),
    )
    started = time.perf_counter()
    reply = await client.complete(
        system="Reply with one word.", text="Say OK.", purpose="smoke", check_id=None
    )
    elapsed = time.perf_counter() - started
    print(f"{provider}: model={model} reply={reply.text!r} {elapsed:.1f}s")
    print(f"  tokens in={reply.input_tokens} out={reply.output_tokens}")


async def _amain() -> None:
    settings = get_settings()
    await _probe("baseten", settings.baseten_api_key, settings.baseten_base_url, settings.baseten_model)
    await _probe(
        "fireworks", settings.fireworks_api_key, settings.fireworks_base_url, settings.fireworks_model
    )


def main() -> None:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this live probe.")
    asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
