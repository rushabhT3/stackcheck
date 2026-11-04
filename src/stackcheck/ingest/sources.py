"""Fetch one user-given PDF URL at a time, politely. Never a crawler."""

import asyncio
import hashlib
import json
import logging
import random
import socket
import ssl
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlsplit

import certifi
import httpx
import pymupdf

from stackcheck.config import Settings
from stackcheck.errors import FetchError, IngestError, StackCheckError
from stackcheck.ingest.qpr import is_qpr_url, parse_qpr_html
from stackcheck.models import SourceDoc

logger = logging.getLogger(__name__)

USER_AGENT = "StackCheck/0.1 (hackathon research; contact via GitHub)"
MAX_REDIRECTS = 3
MAX_UPLOAD_BYTES = 30_000_000
VIDEO_MAX_BYTES = 50_000_000
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")

_last_request: dict[str, float] = {}
_blocked_until: dict[str, float] = {}
_failures: dict[str, list[tuple[float, str | None]]] = {}

FETCH_CACHE_TTL_S = 30 * 24 * 3600  # cached filings expire after 30 days
HOST_BACKOFF_S = 600.0  # no new requests to a refusing host for 10 minutes
FAIL_WINDOW_S = 300.0  # failures counted over the last 5 minutes
FAIL_THRESHOLD = 3  # block the host after this many refusing checks in the window
HOST_CONCURRENCY = 2  # at most 2 full downloads per host at a time
HOST_START_GAP_S = 1.0  # at least 1 s between starts to the same host
_RESET_RETRY_DELAYS = (2.0, 6.0)  # wait before retry 1 and retry 2 on a reset


class _HostSlot:
    """Per-host download slot: concurrency cap, start stagger and spacing."""

    def __init__(self) -> None:
        self.sem = asyncio.Semaphore(HOST_CONCURRENCY)
        self.lock = asyncio.Lock()
        self.last_start = 0.0


_slots: dict[str, _HostSlot] = {}

# Mumbai routing: when set, listed hosts fetch through this coroutine (which
# runs the GET from ap-south) instead of a local connection. Set at startup
# on Modal; None in local runs and tests unless configured.
_remote_fetch_one: Callable[[str, int], dict[str, Any]] | None = None


def configure_remote_fetch(
    fetcher: Callable[[str, int], dict[str, Any]] | None,
) -> None:
    """Route fetches for settings-listed hosts through a remote fetcher, or clear it."""
    global _remote_fetch_one
    _remote_fetch_one = fetcher


def _mumbai_hosts(settings: Settings) -> set[str]:
    return {h.strip().lower() for h in settings.fetch_mumbai_hosts.split(",") if h.strip()}


def _note_host_failure(host: str, now: float, check_id: str | None = None) -> bool:
    """Record one refusal; True when the host just earned a 10-minute block.

    Counts once per check, so one project with many bad files cannot lock
    out the next checks. Calls without a check id each count, as before.
    """
    recent = [(t, c) for t, c in _failures.get(host, []) if now - t < FAIL_WINDOW_S]
    if check_id is not None and any(c == check_id for _, c in recent):
        _failures[host] = recent
        return False
    recent.append((now, check_id))
    _failures[host] = recent
    if len(recent) >= FAIL_THRESHOLD:
        _blocked_until[host] = now + HOST_BACKOFF_S
        return True
    return False


def _clear_host_failures(host: str) -> None:
    """A success resets the refusal count for the host."""
    _failures.pop(host, None)


def _cause_chain(exc: BaseException) -> str:
    """One-line chain of exception types and messages, no bodies or secrets."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while isinstance(current, BaseException) and id(current) not in seen and len(parts) < 5:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {str(current)[:300]}")
        nxt = current.__cause__ or current.__context__
        current = nxt if isinstance(nxt, BaseException) else None
    return " <- ".join(parts)


def _chain_errno(exc: BaseException) -> int | None:
    """First OSError errno in the chain, if any."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        errno = getattr(current, "errno", None)
        if isinstance(errno, int):
            return errno
        nxt = current.__cause__ or current.__context__
        current = nxt if isinstance(nxt, BaseException) else None
    return None


def _fetch_cache_paths(settings: Settings, url: str) -> tuple[Path, Path]:
    """Meta JSON and PDF bytes for one exact URL, under the shared cache dir."""
    key = hashlib.sha256(url.encode()).hexdigest()
    base = settings.data_dir / "cache" / "fetch"
    return base / f"{key}.json", base / f"{key}.pdf"


def _read_fetch_cache(
    settings: Settings, url: str, now_utc: datetime
) -> tuple[bytes, dict[str, object]] | None:
    """Cached (bytes, meta) when the entry is fresh and the file matches its sha."""
    meta_path, body_path = _fetch_cache_paths(settings, url)
    if not meta_path.is_file() or not body_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(meta, dict) or meta.get("url") != url:
        return None
    try:
        first_fetched = datetime.fromisoformat(str(meta["retrieved_at"]))
    except (ValueError, KeyError):
        return None
    if first_fetched.tzinfo is None:
        return None
    if (now_utc - first_fetched).total_seconds() > FETCH_CACHE_TTL_S:
        return None
    try:
        body = body_path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(body).hexdigest() != meta.get("sha256"):
        return None
    return body, meta


def _write_fetch_cache(settings: Settings, url: str, body: bytes, source: SourceDoc) -> None:
    """Remember a successful download so a repeat check skips the network."""
    meta_path, body_path = _fetch_cache_paths(settings, url)
    try:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        meta_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "doc_id": source.doc_id,
                    "sha256": source.sha256,
                    "filename": source.filename,
                    "page_count": source.page_count,
                    "retrieved_at": source.retrieved_at.isoformat(),
                }
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Fetch cache write failed for %s: %s", url, exc)


def _allowed_hosts(settings: Settings) -> set[str]:
    return {h.strip().lower() for h in settings.allowed_hosts.split(",") if h.strip()}


BAD_SHAPE_MESSAGE = (
    "That doesn't look like a link to a PDF. Paste the full https:// link from your state's RERA website."
)
NOT_FOUND_MESSAGE = "We couldn't open that link. The file wasn't found. Check the link, or upload the PDF."
PORTAL_MESSAGE = "The RERA website isn't letting us download right now. Try again later, or upload the PDF."
NOT_A_PDF_MESSAGE = "That link doesn't point to a PDF file."


def _timeout(settings: Settings) -> httpx.Timeout:
    """Per-phase timeouts with an overall default. All four phases set."""
    return httpx.Timeout(
        settings.fetch_doc_timeout_s,
        connect=settings.fetch_connect_timeout_s,
        read=settings.fetch_read_timeout_s,
        write=settings.fetch_read_timeout_s,
        pool=10.0,
    )


def validate_source_url(url: str, allowed_hosts: set[str]) -> None:
    """Reject bad-shape and off-list URLs before any request. Plain sentences only."""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise FetchError(BAD_SHAPE_MESSAGE) from None
    if parts.scheme != "https":
        raise FetchError(BAD_SHAPE_MESSAGE)
    if parts.username is not None or parts.password is not None:
        raise FetchError(BAD_SHAPE_MESSAGE)
    try:
        port = parts.port
    except ValueError:
        raise FetchError(BAD_SHAPE_MESSAGE) from None
    if port is not None and port != 443:
        raise FetchError(BAD_SHAPE_MESSAGE)
    host = (parts.hostname or "").lower()
    if host not in allowed_hosts:
        sites = ", ".join(sorted(allowed_hosts))
        raise FetchError(f"That website isn't supported yet. Supported sites: {sites}.")
    if not parts.path:
        raise FetchError(BAD_SHAPE_MESSAGE)


_EXTRA_CA_PATH = Path(__file__).with_name("sectigo_r36_intermediate.pem")


def _ssl_context() -> ssl.SSLContext:
    """certifi plus the pinned Sectigo R36 intermediate.

    rera.bihar.gov.in sometimes omits the intermediate from its handshake
    chain. Pinning it lets verification complete against the already-trusted
    R46 root. fetch_pdf only talks to allow-listed hosts, so this bundle is
    never used for arbitrary sites.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cadata=_EXTRA_CA_PATH.read_text(encoding="utf-8"))
    return ctx


def _chain(exc: BaseException) -> list[BaseException]:
    """The exception plus its causes and contexts, innermost last."""
    seen: list[BaseException] = []
    current: BaseException | None = exc
    for _ in range(10):
        if current is None:
            break
        seen.append(current)
        nxt = current.__cause__ or current.__context__
        current = nxt if isinstance(nxt, BaseException) else None
    return seen


def _connection_sentence(exc: BaseException, host: str) -> str:
    """Name a connection-level failure in plain words (no HTTP status seen).

    Falls back to PORTAL_MESSAGE when the cause is unknown.
    """
    suffix = "Try again later, or upload the PDF."
    chain = _chain(exc)
    if any(isinstance(e, ConnectionResetError | BrokenPipeError | ConnectionAbortedError) for e in chain):
        return f"The portal closed the connection (connection reset). {suffix}"
    if any(isinstance(e, ConnectionRefusedError) for e in chain):
        return f"The portal refused the connection (connection refused). {suffix}"
    if any(isinstance(e, socket.gaierror | socket.herror) for e in chain):
        return f"We couldn't reach {host} (address lookup failed). {suffix}"
    text = " ".join(str(e) for e in chain).lower()
    if "name resolution" in text or "nodename nor servname" in text or "name or service not known" in text:
        return f"We couldn't reach {host} (address lookup failed). {suffix}"
    if any(isinstance(e, ssl.SSLError) for e in chain):
        return f"The portal's secure connection failed before any download (TLS handshake). {suffix}"
    if "eof occurred" in text or "handshake failure" in text:
        return f"The portal's secure connection failed before any download (TLS handshake). {suffix}"
    return PORTAL_MESSAGE


def _is_cert_failure(exc: BaseException) -> bool:
    """True when the failure is certificate verification, however wrapped."""
    current: BaseException | None = exc
    for _ in range(10):
        if current is None:
            return False
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(current):
            return True
        nxt = current.__cause__ or current.__context__
        current = nxt if isinstance(nxt, BaseException) else None
    return False


def _is_reset(exc: BaseException) -> bool:
    """A reset/aborted connection, but never a certificate failure."""
    if _is_cert_failure(exc):
        return False
    return any(
        isinstance(e, ConnectionResetError | BrokenPipeError | ConnectionAbortedError) for e in _chain(exc)
    )


async def _paced_get(
    client: httpx.AsyncClient,
    host: str,
    url: str,
    settings: Settings,
    sleep: Callable[[float], Awaitable[None]],
    monotonic: Callable[[], float],
    slot: _HostSlot,
) -> httpx.Response:
    """One headers GET: starts 1 s apart per host, politeness spacing kept.

    The caller holds the host slot across the whole download, so at most
    two downloads per host run at once.
    """
    async with slot.lock:
        now = monotonic()
        wait = max(
            slot.last_start + HOST_START_GAP_S - now,
            _last_request.get(host, 0.0) + settings.fetch_min_interval_s - now,
            0.0,
        )
        if wait > 0:
            await sleep(wait)
        response = await client.get(url)
        slot.last_start = monotonic()
        _last_request[host] = monotonic()
        return response


class _RemoteTooLarge(Exception):
    """The Mumbai function declined a body over its return cap; fetch locally."""


async def _remote_single(
    host: str,
    url: str,
    cap_bytes: int,
    settings: Settings,
    check_id: str | None,
) -> httpx.Response:
    """One GET through the Mumbai fetcher. Transport trouble looks like a reset."""
    assert _remote_fetch_one is not None
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_remote_fetch_one, url, cap_bytes),
            timeout=settings.fetch_doc_timeout_s,
        )
    except TimeoutError as exc:
        raise httpx.ConnectTimeout(f"mumbai fetch timed out: {url}") from exc
    except Exception as exc:
        raise httpx.ConnectError(f"mumbai fetch failed for {url}: {exc}") from exc
    if result.get("too_large"):
        raise _RemoteTooLarge(f"mumbai declined {url} ({result.get('bytes')} bytes)")
    return httpx.Response(
        int(result.get("status", 0)),
        headers=result.get("headers") or {},
        content=result.get("body") or b"",
    )


async def _single_get(
    client: httpx.AsyncClient,
    host: str,
    url: str,
    settings: Settings,
    sleep: Callable[[float], Awaitable[None]],
    monotonic: Callable[[], float],
    check_id: str | None,
    cap_bytes: int,
    slot: _HostSlot,
) -> httpx.Response:
    """One headers GET, local or via Mumbai depending on settings."""
    if _remote_fetch_one is not None and host in _mumbai_hosts(settings):
        try:
            return await _remote_single(host, url, cap_bytes, settings, check_id)
        except _RemoteTooLarge as exc:
            logger.info("Fetch %s %s %s; trying the local connection", host, url, exc)
    return await _paced_get(client, host, url, settings, sleep, monotonic, slot)


async def _read_capped(response: httpx.Response, cap_bytes: int, url: str) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > cap_bytes:
            raise FetchError(f"Response size {size} exceeds the {cap_bytes} byte cap: {url}")
    return b"".join(chunks)


def _store(
    body: bytes,
    filename: str,
    origin: str,
    url: str | None,
    dest_dir: Path,
    error_cls: type[StackCheckError],
    suffix: str = ".pdf",
) -> SourceDoc:
    digest = hashlib.sha256(body).hexdigest()
    doc_id = digest[:16]
    sources_dir = dest_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / f"{doc_id}{suffix}").write_bytes(body)
    page_count = 0
    if suffix == ".pdf":
        try:
            with pymupdf.open(stream=body, filetype="pdf") as doc:
                page_count = doc.page_count
        except Exception as exc:
            raise error_cls(f"File is not a readable PDF: {filename}") from exc
    logger.info("Stored %s (%d bytes, %d pages)", doc_id, len(body), page_count)
    return SourceDoc(
        doc_id=doc_id,
        origin=origin,  # type: ignore[arg-type]
        url=url,
        filename=filename,
        sha256=digest,
        retrieved_at=datetime.now(UTC),
        page_count=page_count,
    )


async def _get_url(
    client: httpx.AsyncClient,
    host: str,
    url: str,
    settings: Settings,
    *,
    sleep: Callable[[float], Awaitable[None]],
    monotonic: Callable[[], float],
    check_id: str | None,
    cap_bytes: int,
    started: float,
    region: str = "local",
) -> bytes:
    """GET with redirects and mapped failures. Returns the body. Same sentences as fetch_pdf.

    A reset connection retries twice (2 s, then 6 s) before counting as a
    failure. At most two downloads per host run at once.
    """
    slot = _slots.setdefault(host, _HostSlot())
    async with slot.sem:
        for attempt in range(len(_RESET_RETRY_DELAYS) + 1):
            current = url
            try:
                for _ in range(MAX_REDIRECTS + 1):
                    response = await _single_get(
                        client,
                        host,
                        current,
                        settings,
                        sleep=sleep,
                        monotonic=monotonic,
                        check_id=check_id,
                        cap_bytes=cap_bytes,
                        slot=slot,
                    )
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError(f"Redirect without a location: {current}")
                        nxt = urljoin(current, location)
                        nparts = urlsplit(nxt)
                        if nparts.scheme != "https" or (nparts.hostname or "").lower() != host:
                            raise FetchError(f"Redirect leaves host {host}: {nxt}")
                        current = nxt
                        continue
                    if response.status_code != 200:
                        elapsed = monotonic() - started
                        logger.error(
                            "Fetch %s %s -> HTTP %s in %.1fs (region %s, check %s)",
                            host,
                            url,
                            response.status_code,
                            elapsed,
                            region,
                            check_id,
                        )
                        if response.status_code == 404:
                            raise FetchError(NOT_FOUND_MESSAGE)
                        if response.status_code in (403, 429):
                            blocked = _note_host_failure(host, monotonic(), check_id)
                            if blocked:
                                logger.error(
                                    "Fetch %s blocked for %.0fs after repeated refusals (check %s)",
                                    host,
                                    HOST_BACKOFF_S,
                                    check_id,
                                )
                            reason = "access refused" if response.status_code == 403 else "too many requests"
                            raise FetchError(
                                f"The portal answered {response.status_code} ({reason}). "
                                "Try again later, or upload the PDF."
                            )
                    return await _read_capped(response, cap_bytes, current)
                raise FetchError(f"Too many redirects: {url}")
            except (ssl.SSLError, httpx.ConnectError) as exc:
                if attempt < len(_RESET_RETRY_DELAYS) and _is_reset(exc):
                    delay = _RESET_RETRY_DELAYS[attempt] + random.uniform(0.0, 1.0)
                    logger.warning(
                        "Fetch %s %s reset, retrying in %.1fs (attempt %d, region %s, check %s)",
                        host,
                        url,
                        delay,
                        attempt + 2,
                        region,
                        check_id,
                    )
                    await sleep(delay)
                    continue
                elapsed = monotonic() - started
                logger.error(
                    "Fetch %s %s connection failed (%s, errno %s) in %.1fs "
                    "(region %s, attempt %d of %d, check %s): %s",
                    host,
                    url,
                    type(exc).__name__,
                    _chain_errno(exc),
                    elapsed,
                    region,
                    attempt + 1,
                    len(_RESET_RETRY_DELAYS) + 1,
                    check_id,
                    _cause_chain(exc),
                )
                if _is_cert_failure(exc):
                    raise FetchError(
                        f"Couldn't verify the security certificate of {host}. Try uploading the PDF instead."
                    ) from exc
                _note_host_failure(host, monotonic(), check_id)
                raise FetchError(_connection_sentence(exc, host)) from exc
            except httpx.TimeoutException as exc:
                elapsed = monotonic() - started
                logger.error(
                    "Fetch %s %s timed out in %.1fs (region %s, attempt %d of %d, check %s): %s",
                    host,
                    url,
                    elapsed,
                    region,
                    attempt + 1,
                    len(_RESET_RETRY_DELAYS) + 1,
                    check_id,
                    _cause_chain(exc),
                )
                _note_host_failure(host, monotonic(), check_id)
                raise FetchError(
                    f"The portal did not answer within {settings.fetch_read_timeout_s:.0f} seconds. "
                    "Try again later, or upload the PDF."
                ) from exc
    raise AssertionError("unreachable: retry loop always returns or raises")  # pragma: no cover


async def fetch_pdf(
    url: str,
    settings: Settings,
    dest_dir: Path,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    check_id: str | None = None,
    region: str = "local",
) -> SourceDoc:
    """Fetch one PDF URL. Cache, then politely, capped and timed.

    A successful download is cached by exact URL for 30 days; a repeat check
    reuses the stored bytes without touching the portal. After 3 connection
    failures to one host within 5 minutes, that host gets no new requests
    for 10 minutes and new checks fail at once.
    """
    allowed = _allowed_hosts(settings)
    validate_source_url(url, allowed)
    host = (urlsplit(url).hostname or "").lower()
    started = monotonic()
    if _blocked_until.get(host, 0.0) > monotonic():
        logger.error(
            "Fetch %s %s blocked after repeated refusals, failing fast in %.1fs (region %s, check %s)",
            host,
            url,
            monotonic() - started,
            region,
            check_id,
        )
        raise FetchError(
            f"We stopped asking for {HOST_BACKOFF_S / 60:.0f} minutes "
            f"after {FAIL_THRESHOLD} failures in a row. Try again later, or upload the PDF."
        )
    if is_qpr_url(url):
        return await _fetch_qpr(
            url,
            settings,
            dest_dir,
            sleep=sleep,
            monotonic=monotonic,
            check_id=check_id,
            region=region,
        )
    cached = await asyncio.to_thread(_read_fetch_cache, settings, url, datetime.now(UTC))
    if cached is not None:
        body, meta = cached
        doc_id = str(meta["doc_id"])
        sources_dir = dest_dir / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread((sources_dir / f"{doc_id}.pdf").write_bytes, body)
        elapsed = monotonic() - started
        logger.info(
            "Fetch %s from cache (%d bytes, first fetched %s) in %.1fs (check %s)",
            host,
            len(body),
            meta["retrieved_at"],
            elapsed,
            check_id,
        )
        return SourceDoc(
            doc_id=doc_id,
            origin="url",
            url=url,
            filename=str(meta["filename"]),
            sha256=str(meta["sha256"]),
            retrieved_at=datetime.fromisoformat(str(meta["retrieved_at"])),
            page_count=int(meta["page_count"]),  # type: ignore[arg-type]
        )
    async with httpx.AsyncClient(
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        timeout=_timeout(settings),
        verify=_ssl_context(),
    ) as client:
        body = await _get_url(
            client,
            host,
            url,
            settings,
            sleep=sleep,
            monotonic=monotonic,
            check_id=check_id,
            cap_bytes=settings.fetch_max_bytes,
            started=started,
            region=region,
        )
    if not body.startswith(b"%PDF"):
        raise FetchError(NOT_A_PDF_MESSAGE)
    filename = PurePosixPath(urlsplit(url).path).name or f"{hashlib.sha256(body).hexdigest()[:16]}.pdf"
    source = await asyncio.to_thread(_store, body, filename, "url", url, dest_dir, FetchError)
    _clear_host_failures(host)
    await asyncio.to_thread(_write_fetch_cache, settings, url, body, source)
    return source


QPR_IMAGE_CAP_BYTES = 8_000_000
QPR_NOT_A_PAGE_MESSAGE = (
    "That quarterly report link didn't open as a web page. Check the link, or upload the PDFs instead."
)


def _image_cache_source(url: str, filename: str, body: bytes, retrieved_at: datetime) -> SourceDoc:
    """Cache bookkeeping for one QPR photo, keyed by its exact URL."""
    digest = hashlib.sha256(body).hexdigest()
    return SourceDoc(
        doc_id=digest[:16],
        origin="url",
        url=url,
        filename=filename,
        sha256=digest,
        retrieved_at=retrieved_at,
        page_count=1,
    )


async def _fetch_qpr(
    url: str,
    settings: Settings,
    dest_dir: Path,
    *,
    sleep: Callable[[float], Awaitable[None]],
    monotonic: Callable[[], float],
    check_id: str | None,
    region: str = "local",
) -> SourceDoc:
    """Fetch a QPR page plus its /QPRimages/ photos. Same politeness and cache as PDFs.

    A photo that fails to download is skipped with a log line; the filing facts
    still come from the page, and the read step notes the missing photo.
    """
    host = (urlsplit(url).hostname or "").lower()
    started = monotonic()
    now = datetime.now(UTC)
    async with httpx.AsyncClient(
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        timeout=_timeout(settings),
        verify=_ssl_context(),
    ) as client:
        cached = await asyncio.to_thread(_read_fetch_cache, settings, url, now)
        if cached is not None:
            body, meta = cached
            retrieved_at = datetime.fromisoformat(str(meta["retrieved_at"]))
        else:
            body = await _get_url(
                client,
                host,
                url,
                settings,
                sleep=sleep,
                monotonic=monotonic,
                check_id=check_id,
                cap_bytes=settings.fetch_max_bytes,
                started=started,
                region=region,
            )
            retrieved_at = now
        if not body.lstrip().startswith(b"<"):
            raise FetchError(QPR_NOT_A_PAGE_MESSAGE)
        digest = hashlib.sha256(body).hexdigest()
        doc_id = digest[:16]
        sources_dir = dest_dir / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread((sources_dir / f"{doc_id}.html").write_bytes, body)
        try:
            page = parse_qpr_html(body.decode("utf-8", errors="replace"), url)
        except IngestError as exc:
            raise FetchError(str(exc)) from exc
        source = SourceDoc(
            doc_id=doc_id,
            origin="url",
            url=url,
            filename=PurePosixPath(urlsplit(url).path).name or f"{doc_id}.html",
            sha256=digest,
            retrieved_at=retrieved_at,
            page_count=1,
        )
        _clear_host_failures(host)
        if cached is None:
            await asyncio.to_thread(_write_fetch_cache, settings, url, body, source)
        kept = 0
        for index, photo in enumerate(page.photos):
            filename = f"{doc_id}-qpr{index}.jpg"
            target = sources_dir / filename
            img_cached = await asyncio.to_thread(_read_fetch_cache, settings, photo.url, now)
            if img_cached is not None:
                await asyncio.to_thread(target.write_bytes, img_cached[0])
                kept += 1
                continue
            try:
                img_body = await _get_url(
                    client,
                    host,
                    photo.url,
                    settings,
                    sleep=sleep,
                    monotonic=monotonic,
                    check_id=check_id,
                    cap_bytes=QPR_IMAGE_CAP_BYTES,
                    started=monotonic(),
                    region=region,
                )
            except FetchError as exc:
                logger.warning("QPR photo skipped %s (check %s): %s", photo.url, check_id, exc)
                continue
            if img_body[:2] != b"\xff\xd8" and img_body[:8] != b"\x89PNG\r\n\x1a\n":
                logger.warning("QPR photo skipped %s: not an image (check %s)", photo.url, check_id)
                continue
            await asyncio.to_thread(target.write_bytes, img_body)
            await asyncio.to_thread(
                _write_fetch_cache,
                settings,
                photo.url,
                img_body,
                _image_cache_source(photo.url, filename, img_body, retrieved_at),
            )
            kept += 1
        elapsed = monotonic() - started
        logger.info(
            "Fetch QPR page %s (%d bytes, %d of %d photos) in %.1fs (check %s)",
            host,
            len(body),
            kept,
            len(page.photos),
            elapsed,
            check_id,
        )
    return source


def register_upload(data: bytes, filename: str, dest_dir: Path) -> SourceDoc:
    """Accept an uploaded PDF after validating magic bytes and size."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(f"Upload larger than {MAX_UPLOAD_BYTES} bytes: {filename}")
    if not data.startswith(b"%PDF"):
        raise IngestError(f"Upload is not a PDF: {filename}")
    safe_name = PurePosixPath(filename.replace("\\", "/")).name or "upload.pdf"
    return _store(data, safe_name, "upload", None, dest_dir, IngestError)


def sniff_video_ext(data: bytes) -> str | None:
    """File extension for mp4/mov (ftyp) or webm (EBML); None when not a video."""
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return ".mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    return None


def register_video_upload(data: bytes, filename: str, dest_dir: Path) -> SourceDoc:
    """Accept an uploaded site video after validating magic bytes and size."""
    if len(data) > VIDEO_MAX_BYTES:
        raise IngestError(f"Upload larger than {VIDEO_MAX_BYTES} bytes: {filename}")
    ext = sniff_video_ext(data)
    if ext is None:
        raise IngestError(f"Upload is not a video: {filename}")
    safe_name = PurePosixPath(filename.replace("\\", "/")).name or f"upload{ext}"
    return _store(data, safe_name, "upload", None, dest_dir, IngestError, suffix=ext)


def seed_fetch_entry(settings: Settings, url: str, body: bytes, filename: str) -> SourceDoc:
    """Store local PDF bytes under a URL's fetch-cache key (Modal seed cache).

    Same bar as a download: portal-shaped URL, %PDF magic, the fetch size cap
    and readable pages. Origin stays "url", so seeded files use the
    open-weights reader and cite the portal link, without touching the portal.
    """
    validate_source_url(url, _allowed_hosts(settings))
    if len(body) > settings.fetch_max_bytes:
        raise FetchError(f"File is larger than {settings.fetch_max_bytes} bytes: {filename}")
    if not body.startswith(b"%PDF"):
        raise FetchError(NOT_A_PDF_MESSAGE)
    try:
        with pymupdf.open(stream=body, filetype="pdf") as doc:
            page_count = doc.page_count
    except Exception as exc:
        raise FetchError(f"File is not a readable PDF: {filename}") from exc
    digest = hashlib.sha256(body).hexdigest()
    safe_name = PurePosixPath(filename.replace("\\", "/")).name or "seed.pdf"
    source = SourceDoc(
        doc_id=digest[:16],
        origin="url",
        url=url,
        filename=safe_name,
        sha256=digest,
        retrieved_at=datetime.now(UTC),
        page_count=page_count,
    )
    _write_fetch_cache(settings, url, body, source)
    logger.info("Seeded fetch cache for %s (%d bytes, %d pages)", url, len(body), page_count)
    return source
