"""Mar 2025 page-11 probe: one live filing read with request/response capture.

Loads data/golden/pdfs/93aed199f53910be.pdf via ingest_pdf + register_upload,
wraps the repo's real reader client in a recording proxy, and saves the
request shape plus the FULL raw reply JSON to /tmp/p11_probe/ (never into the
repo: raw replies are document text and must not be committed).

Run once:  $env:STACKCHECK_LIVE=1; uv run python scripts/debug_p11.py
Cost: one filing read, about $0.10, paid and expected.
"""

import asyncio
import io
import json
import os
import re
import sys
from pathlib import Path

OUT_DIR = Path("/tmp/p11_probe")
PDF_PATH = Path("data/golden/pdfs/93aed199f53910be.pdf")


class RecordingProxy:
    """ModelClient-shaped proxy that saves request shape + full raw replies."""

    def __init__(self, inner, out_dir: Path) -> None:
        self._inner = inner
        self._out_dir = out_dir
        self._calls = 0

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model(self) -> str:
        return self._inner.model

    async def complete(self, *, system, text, images=(), purpose, check_id, **kwargs):
        from PIL import Image as PilImage

        self._calls += 1
        idx = self._calls
        img_infos = []
        for img in images:
            dims = "unknown"
            try:
                with PilImage.open(io.BytesIO(img.data)) as im:
                    dims = f"{im.width}x{im.height}"
            except Exception:
                pass
            img_infos.append({"mime": img.mime, "bytes": len(img.data), "dims": dims})
        shape = {
            "call": idx,
            "provider": self.provider,
            "model": self.model,
            "purpose": purpose,
            "check_id": check_id,
            "image_count": len(img_infos),
            "images": img_infos,
            "text_len": len(text),
            "page_markers": [int(n) for n in re.findall(r"\[\[PAGE (\d+)\]\]", text)],
            "code_sha": kwargs.get("code_sha"),
        }
        (self._out_dir / f"request_shape_{idx}.json").write_text(
            json.dumps(shape, indent=2), encoding="utf-8"
        )
        reply = await self._inner.complete(
            system=system,
            text=text,
            images=images,
            purpose=purpose,
            check_id=check_id,
            **kwargs,
        )
        (self._out_dir / f"raw_reply_{idx}.json").write_text(
            reply.model_dump_json(indent=2), encoding="utf-8"
        )
        return reply


def main() -> int:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this paid probe.")
    if not PDF_PATH.exists():
        raise SystemExit(f"Missing PDF: {PDF_PATH}")

    from stackcheck.config import get_settings
    from stackcheck.extract.filing import extract_filing
    from stackcheck.ingest.pdf import ingest_pdf
    from stackcheck.ingest.sources import register_upload
    from stackcheck.llm.registry import reader_client

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logging_config = {"level": "INFO"}
    import logging

    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    _ = logging_config

    settings = get_settings()
    client = reader_client(settings, "url")
    print(f"reader: provider={client.provider} model={client.model}")

    data = PDF_PATH.read_bytes()
    source = register_upload(data, PDF_PATH.name, Path("data/work"))
    stored = Path("data/work/sources") / f"{source.doc_id}.pdf"
    ingested = ingest_pdf(stored, source, Path("data/work") / source.doc_id)
    print(f"doc_id={source.doc_id} pages={len(ingested.pages)} scanned={ingested.is_scanned}")

    proxy = RecordingProxy(client, OUT_DIR)
    filing = asyncio.run(extract_filing(ingested, proxy, check_id="p11-probe", pdf_path=stored))
    (OUT_DIR / "filing.json").write_text(filing.model_dump_json(indent=2), encoding="utf-8")
    fin = filing.financials
    print("financials:")
    for field in (
        "received_this_quarter",
        "spent_this_quarter",
        "cumulative_received",
        "cumulative_spent",
        "estimated_total_cost",
        "net_balance",
        "currency_unit",
    ):
        print(f"  {field}={getattr(fin, field)!r}")
    print(f"  ref={fin.ref!r}")
    print(f"notes={filing.notes!r}")
    print(f"model calls recorded: {proxy._calls}")
    print(f"artifacts in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
