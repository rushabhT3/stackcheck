"""Modal deployment: one warm container serving the FastAPI app, data on a Volume."""

from __future__ import annotations

from typing import TYPE_CHECKING

import modal

if TYPE_CHECKING:
    from stackcheck.config import Settings

app = modal.App("stackcheck")
volume = modal.Volume.from_name("stackcheck-data", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.13")  # try "3.15" first; see step 3
    .apt_install("ffmpeg")  # WhatsApp voice notes: ogg opus to WAV
    .uv_sync()  # installs from pyproject.toml + uv.lock
    # Default ignore is NON_PYTHON_FILES; that drops the prompt .md files and the
    # .pem the fetcher reads. Keep everything except bytecode caches instead.
    .add_local_python_source("stackcheck", ignore=["**/__pycache__", "**/*.pyc"])
    .add_local_dir("web/dist", remote_path="/app/web/dist")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("stackcheck-keys")],
    volumes={"/data": volume},
    min_containers=1,
    max_containers=1,  # jobs live in one process; never split them across containers
    scaledown_window=900,
    timeout=900,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    from pathlib import Path

    import stackcheck.extract.prompts as _prompts
    import stackcheck.ingest.sources as _sources
    from stackcheck.api.app import create_app
    from stackcheck.config import Settings
    from stackcheck.pipeline.jobs import JobRunner

    prompts_dir = Path(_prompts.__file__).parent / "prompts"
    needed = [Path(_sources.__file__).with_name("sectigo_r36_intermediate.pem")] + [
        prompts_dir / name
        for name in ("filing_v5.md", "photo_v1.md", "investigate_v1.md", "summary_v1.md", "reread_v1.md")
    ]
    missing = [str(p) for p in needed if not p.is_file()]
    if missing:
        raise RuntimeError(f"StackCheck package data missing on the image: {', '.join(missing)}")

    settings = Settings(
        data_dir="/data",
        samples_file="/data/samples.json",
        web_dist_dir="/app/web/dist",
        redact_names=True,
        summary_languages="en,hi",
        public_base_url="https://rushabhtrivedi03--stackcheck-web.us-east.modal.run",
    )
    if settings.fetch_mumbai_hosts.strip():
        from stackcheck.ingest.sources import configure_remote_fetch

        def _via_mumbai(url: str, cap_bytes: int) -> dict:
            return fetch_mumbai.remote(url, cap_bytes)

        configure_remote_fetch(_via_mumbai)
    runner = JobRunner(settings, on_finished=volume.commit)
    return create_app(settings, runner)


REMOTE_BODY_CAP = 8_000_000  # bodies travel back through .remote(); larger ones fetch locally


@app.function(image=image, region="ap-south", timeout=300)
def fetch_mumbai(url: str, cap_bytes: int) -> dict:
    """One direct GET from Mumbai with the same client config as fetch_pdf.

    No cache, no breaker, one request only. Returns {status, headers, body},
    or {too_large: True} when the body exceeds cap_bytes (the caller then
    tries the local connection instead). If Modal ever rejects a body of this
    size, the fetch logs "mumbai fetch failed" and the portal sentence is shown.
    """
    import httpx

    from stackcheck.config import Settings
    from stackcheck.ingest.sources import USER_AGENT, _ssl_context, _timeout

    settings = Settings(data_dir="/data")
    cap = min(cap_bytes, REMOTE_BODY_CAP)
    with httpx.Client(
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        timeout=_timeout(settings),
        verify=_ssl_context(),
    ) as client:
        response = client.get(url)
    body = response.content
    if response.status_code == 200 and len(body) > cap:
        return {"too_large": True, "bytes": len(body)}
    return {
        "status": response.status_code,
        "headers": dict(response.headers),
        "body": body,
    }


def _seed_settings() -> Settings:
    from stackcheck.config import Settings

    return Settings(
        data_dir="/data",
        samples_file="/data/samples.json",
        web_dist_dir="/app/web/dist",
        redact_names=True,
        summary_languages="en,hi",
        public_base_url="https://rushabhtrivedi03--stackcheck-web.us-east.modal.run",
    )


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("stackcheck-keys")],
    volumes={"/data": volume},
    timeout=600,
)
def seed_cache(manifest_path: str, base_dir: str) -> None:
    """Store local PDFs under their portal URLs so checks run without fetching.

    Manifest CSV columns: url, local_path. Put the files on the Volume first
    with `modal volume put`, then run this. Each row is validated like a
    download (%PDF magic, size cap, readable pages) and served to later
    checks as origin "url". Prints one line per row and commits at the end.
    """
    import csv
    from pathlib import Path

    from stackcheck.errors import StackCheckError
    from stackcheck.ingest.sources import seed_fetch_entry

    settings = _seed_settings()
    base = Path(base_dir)
    ok, failed = 0, 0
    with Path(manifest_path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            url = (row.get("url") or "").strip()
            local = (row.get("local_path") or "").strip()
            if not url or not local:
                print(f"SKIP empty row: {row}")
                failed += 1
                continue
            try:
                body = (base / local).read_bytes()
                source = seed_fetch_entry(settings, url, body, Path(local).name)
            except (OSError, StackCheckError) as exc:
                print(f"FAIL {url}: {exc}")
                failed += 1
                continue
            print(f"OK {url}: {source.doc_id} ({len(body)} bytes, {source.page_count} pages)")
            ok += 1
    volume.commit()
    print(f"seed_cache done: {ok} ok, {failed} failed")
