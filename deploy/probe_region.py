"""Throwaway region probe: is rera.bihar.gov.in reachable from Modal regions?

Two functions, default region and Mumbai (ap-south), each do ONE direct GET
per URL with the same User-Agent, headers and timeout as the fetch code in
src/stackcheck/ingest/sources.py. No cache, no circuit breaker, no other
portal requests. Run: uv run --with modal modal run deploy/probe_region.py
"""

import modal

app = modal.App("stackcheck-region-probe")

image = modal.Image.debian_slim().pip_install("httpx", "certifi")

# Copied from src/stackcheck/ingest/sources.py (USER_AGENT, _timeout defaults).
USER_AGENT = "StackCheck/0.1 (hackathon research; contact via GitHub)"
TIMEOUT = {"timeout": 90.0, "connect": 10.0, "read": 30.0, "write": 30.0, "pool": 10.0}

URLS = [
    "https://rera.bihar.gov.in/Progress_Doc/RERAP03162019105734-110151142222022.pdf",
    (
        "https://rera.bihar.gov.in/QPR/QPR-Show.aspx"
        "?pr=RERAP08022024125722&ld=pan&pj=RERAP08022024125722-1&f=2025-2026&q=1"
    ),
]


def _probe(url: str) -> dict:
    import time

    import httpx

    started = time.monotonic()
    try:
        with httpx.Client(
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(**TIMEOUT),
        ) as client:
            response = client.get(url)
        elapsed = time.monotonic() - started
        row: dict = {
            "url": url,
            "status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "bytes": len(response.content),
            "seconds": round(elapsed, 1),
            "error": None,
        }
        if response.status_code != 200:
            row["body_head"] = response.text[:120]
        return row
    except Exception as exc:
        return {
            "url": url,
            "status": None,
            "content_type": None,
            "bytes": 0,
            "seconds": round(time.monotonic() - started, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }


@app.function(image=image, timeout=300)
def probe_default(url: str) -> dict:
    """One direct GET from the default region."""
    return _probe(url)


@app.function(image=image, region="ap-south", timeout=300)
def probe_mumbai(url: str) -> dict:
    """One direct GET from Mumbai."""
    return _probe(url)


@app.local_entrypoint()
def main() -> None:
    print(f"{'region':<9} {'status':<7} {'seconds':<8} {'bytes':<9} {'content-type':<28} url / error")
    for label, fn in (("default", probe_default), ("ap-south", probe_mumbai)):
        for url in URLS:
            row = fn.remote(url)
            detail = row["error"] or url
            print(
                f"{label:<9} {row['status']!s:<7} {row['seconds']:<8} "
                f"{row['bytes']:<9} {row['content_type']!s:<28} {detail}"
            )
            if row.get("body_head"):
                print(f"  body head: {row['body_head']!r}")
