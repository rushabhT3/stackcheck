import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
import respx

from stackcheck.config import Settings
from stackcheck.errors import FetchError, IngestError
from stackcheck.ingest import sources
from stackcheck.ingest.sources import fetch_pdf, register_upload, seed_fetch_entry
from tests.conftest import make_pdf

URL = "https://rera.bihar.gov.in/Progress_Doc/q.pdf"


def _pdf_bytes(tmp_path: Path) -> bytes:
    return make_pdf(tmp_path / "real.pdf", [{"text": "hello"}]).read_bytes()


@pytest.fixture(autouse=True)
def _fresh_spacing() -> None:
    sources._slots.clear()
    sources._last_request.clear()
    sources._blocked_until.clear()
    sources._failures.clear()
    sources.configure_remote_fetch(None)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "data_dir": tmp_path,
        "meta_api_key": "test",
        "baseten_api_key": "test",
        "fireworks_api_key": "test",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


async def _instant(secs: float) -> None:
    """No-op sleep so spacing never slows a test."""
    return None


@respx.mock
async def test_http_url_raises(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="https"):
        await fetch_pdf("http://rera.bihar.gov.in/q.pdf", _settings(tmp_path), tmp_path)


@respx.mock
async def test_disallowed_host_makes_no_request(tmp_path: Path) -> None:
    route = respx.get("https://evil.example/q.pdf").mock(
        return_value=httpx.Response(200, content=_pdf_bytes(tmp_path))
    )
    with pytest.raises(FetchError, match="isn't supported yet"):
        await fetch_pdf("https://evil.example/q.pdf", _settings(tmp_path), tmp_path)
    assert not route.called


@respx.mock
async def test_rejected_shapes_make_no_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for bad in [
        "https://rera.bihar.gov.in:8443/q.pdf",
        "https://user:pw@rera.bihar.gov.in/q.pdf",
        "https://rera.bihar.gov.in",
        "not a url",
    ]:
        route = respx.get(bad).mock(return_value=httpx.Response(200, content=_pdf_bytes(tmp_path)))
        with pytest.raises(FetchError, match="doesn't look like a link"):
            await fetch_pdf(bad, settings, tmp_path)
        assert not route.called


@respx.mock
async def test_ok_pdf_saved_with_hash_id(tmp_path: Path) -> None:
    body = _pdf_bytes(tmp_path)
    respx.get(URL).mock(return_value=httpx.Response(200, content=body))
    source = await fetch_pdf(URL, _settings(tmp_path), tmp_path)
    import hashlib

    assert source.doc_id == hashlib.sha256(body).hexdigest()[:16]
    assert source.origin == "url"
    assert source.page_count == 1
    assert (tmp_path / "sources" / f"{source.doc_id}.pdf").read_bytes() == body


@respx.mock
async def test_non_pdf_body_raises(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"<html>nope</html>"))
    with pytest.raises(FetchError, match="PDF"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path)


@respx.mock
async def test_oversize_body_raises(tmp_path: Path) -> None:
    body = _pdf_bytes(tmp_path) + b"x" * 2000
    respx.get(URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(FetchError, match="size"):
        await fetch_pdf(URL, _settings(tmp_path, fetch_max_bytes=1000), tmp_path)


@respx.mock
async def test_same_host_redirect_followed(tmp_path: Path) -> None:
    respx.get("https://rera.bihar.gov.in/old.pdf").mock(
        return_value=httpx.Response(302, headers={"location": "/Progress_Doc/q.pdf"})
    )
    respx.get(URL).mock(return_value=httpx.Response(200, content=_pdf_bytes(tmp_path)))
    source = await fetch_pdf("https://rera.bihar.gov.in/old.pdf", _settings(tmp_path), tmp_path)
    assert source.url == "https://rera.bihar.gov.in/old.pdf"


@respx.mock
async def test_other_host_redirect_raises(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"location": "https://evil.example/q.pdf"}))
    with pytest.raises(FetchError, match="host"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path)


@respx.mock
@respx.mock
async def test_cert_failure_message_is_plain(tmp_path: Path) -> None:
    respx.get(URL).mock(
        side_effect=httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    )
    with pytest.raises(FetchError, match=r"security certificate.*uploading"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path)


@respx.mock
def test_ssl_context_pins_one_extra_issuer() -> None:
    import ssl

    import certifi

    from stackcheck.ingest.sources import _ssl_context

    ctx = _ssl_context()
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname
    plain = ssl.create_default_context(cafile=certifi.where())
    pinned = ctx.cert_store_stats()["x509_ca"]
    assert pinned == plain.cert_store_stats()["x509_ca"] + 1


@respx.mock
async def test_plain_connect_error_becomes_plain_sentence(tmp_path: Path) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(FetchError, match="isn't letting us download"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path)


def _caused(outer: httpx.ConnectError, cause: BaseException) -> httpx.ConnectError:
    """Attach a cause the way httpx does when a transport raises (respx clears it)."""
    outer.__cause__ = cause
    return outer


def test_connection_sentence_names_reset() -> None:
    err = _caused(httpx.ConnectError("connection reset by peer"), ConnectionResetError(104, "reset"))
    assert sources._connection_sentence(err, "rera.bihar.gov.in").startswith(
        "The portal closed the connection (connection reset)."
    )


def test_connection_sentence_names_refused() -> None:
    err = _caused(httpx.ConnectError("[Errno 111] refused"), ConnectionRefusedError(111, "refused"))
    assert "connection refused" in sources._connection_sentence(err, "rera.bihar.gov.in")


def test_connection_sentence_unknown_stays_generic() -> None:
    err = httpx.ConnectError("mystery")
    assert sources._connection_sentence(err, "rera.bihar.gov.in") == sources.PORTAL_MESSAGE


@respx.mock
async def test_refusal_logs_status_and_host(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    respx.get(URL).mock(return_value=httpx.Response(403, content=b"denied"))
    with caplog.at_level("INFO"), pytest.raises(FetchError, match=r"answered 403 \(access refused\)"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path)
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "stackcheck.ingest.sources")
    assert "403" in ours
    assert "rera.bihar.gov.in" in ours


@respx.mock
async def test_timeout_logs_exception_type(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("read timed out"))
    with caplog.at_level("INFO"), pytest.raises(FetchError, match="did not answer within 30 seconds"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path)
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "stackcheck.ingest.sources")
    assert "ReadTimeout" in ours
    assert "rera.bihar.gov.in" in ours


async def test_connection_failure_log_names_region_and_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _refused(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        raise _caused(httpx.ConnectError("[Errno 111] refused"), ConnectionRefusedError(111, "refused"))

    _patch_get(monkeypatch, _refused)
    with caplog.at_level("INFO"), pytest.raises(FetchError, match="connection refused"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_instant, check_id="chk_z", region="mumbai")
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "stackcheck.ingest.sources")
    assert "mumbai" in ours
    assert "attempt 1 of 3" in ours
    assert "chk_z" in ours


@respx.mock
async def test_timeout_log_names_region_and_attempt(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("read timed out"))
    with caplog.at_level("INFO"), pytest.raises(FetchError, match="did not answer within 30 seconds"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path, check_id="chk_t", region="mumbai")
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "stackcheck.ingest.sources")
    assert "mumbai" in ours
    assert "attempt 1 of 3" in ours
    assert "chk_t" in ours


@respx.mock
async def test_403_gives_access_refused_sentence(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(403, content=b"denied"))
    with pytest.raises(FetchError, match=r"The portal answered 403 \(access refused\)"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_instant, monotonic=lambda: 0.0)


@respx.mock
async def test_429_gives_too_many_requests_sentence(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(429, content=b"slow"))
    with pytest.raises(FetchError, match=r"The portal answered 429 \(too many requests\)"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_instant, monotonic=lambda: 0.0)


@respx.mock
async def test_timeout_gives_no_answer_sentence(tmp_path: Path) -> None:
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("read timed out"))
    with pytest.raises(FetchError, match="The portal did not answer within 30 seconds"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_instant, monotonic=lambda: 0.0)


@respx.mock
async def test_open_breaker_gives_backoff_sentence_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sources._blocked_until["rera.bihar.gov.in"] = 10**9
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=_pdf_bytes(tmp_path)))
    with (
        caplog.at_level("INFO"),
        pytest.raises(FetchError, match=r"stopped asking .* 10 minutes after 3 failures"),
    ):
        await fetch_pdf(
            URL,
            _settings(tmp_path),
            tmp_path,
            check_id="chk_x",
            region="us-east",
            sleep=_instant,
            monotonic=lambda: 0.0,
        )
    assert not route.called
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "stackcheck.ingest.sources")
    assert URL in ours
    assert "us-east" in ours
    assert "chk_x" in ours


@respx.mock
async def test_status_error_log_has_url_region_and_check(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(URL).mock(return_value=httpx.Response(403, content=b"denied"))
    with caplog.at_level("INFO"), pytest.raises(FetchError, match=r"The portal answered 403"):
        await fetch_pdf(
            URL,
            _settings(tmp_path),
            tmp_path,
            check_id="chk_y",
            region="us-east",
            sleep=_instant,
            monotonic=lambda: 0.0,
        )
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "stackcheck.ingest.sources")
    assert URL in ours
    assert "us-east" in ours
    assert "chk_y" in ours


@respx.mock
async def test_repeat_url_served_from_cache_without_network(tmp_path: Path) -> None:
    body = _pdf_bytes(tmp_path)
    settings = _settings(tmp_path)
    calls: list[httpx.Request] = []
    respx.get(URL).mock(
        side_effect=lambda request: (calls.append(request), httpx.Response(200, content=body))[1]
    )
    first = await fetch_pdf(URL, settings, tmp_path / "work1")
    assert len(calls) == 1
    second = await fetch_pdf(URL, settings, tmp_path / "work2")
    assert len(calls) == 1
    assert second.doc_id == first.doc_id
    assert second.sha256 == first.sha256
    assert second.retrieved_at == first.retrieved_at
    assert (tmp_path / "work2" / "sources" / f"{first.doc_id}.pdf").read_bytes() == body


@respx.mock
async def test_expired_cache_entry_fetches_again(tmp_path: Path) -> None:
    import json
    from datetime import UTC, datetime, timedelta

    from stackcheck.ingest.sources import _fetch_cache_paths

    body = _pdf_bytes(tmp_path)
    settings = _settings(tmp_path)
    calls: list[httpx.Request] = []
    respx.get(URL).mock(
        side_effect=lambda request: (calls.append(request), httpx.Response(200, content=body))[1]
    )
    await fetch_pdf(URL, settings, tmp_path / "work1")
    meta_path, _ = _fetch_cache_paths(settings, URL)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["retrieved_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    await fetch_pdf(URL, settings, tmp_path / "work2", sleep=_instant, monotonic=lambda: 0.0)
    assert len(calls) == 2


@respx.mock
async def test_missing_cache_file_fetches_again(tmp_path: Path) -> None:
    from stackcheck.ingest.sources import _fetch_cache_paths

    body = _pdf_bytes(tmp_path)
    settings = _settings(tmp_path)
    calls: list[httpx.Request] = []
    respx.get(URL).mock(
        side_effect=lambda request: (calls.append(request), httpx.Response(200, content=body))[1]
    )
    await fetch_pdf(URL, settings, tmp_path / "work1")
    _, body_path = _fetch_cache_paths(settings, URL)
    body_path.unlink()
    await fetch_pdf(URL, settings, tmp_path / "work2", sleep=_instant, monotonic=lambda: 0.0)
    assert len(calls) == 2


@respx.mock
async def test_429_backs_off_after_three_failures(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(429, content=b"slow down"))
    settings = _settings(tmp_path)
    for n in range(3):
        with pytest.raises(FetchError, match=r"answered 429 \(too many requests\)"):
            await fetch_pdf(URL, settings, tmp_path / f"work{n}")
    assert route.call_count == 3
    with pytest.raises(FetchError, match=r"stopped asking .* 10 minutes after 3 failures"):
        await fetch_pdf(URL, settings, tmp_path / "work3")
    assert route.call_count == 3


@respx.mock
async def test_timeout_backs_off_after_three_failures(tmp_path: Path) -> None:
    route = respx.get(URL).mock(side_effect=httpx.ConnectTimeout("connect timed out"))
    settings = _settings(tmp_path)
    for n in range(3):
        with pytest.raises(FetchError, match="did not answer within 30 seconds"):
            await fetch_pdf(URL, settings, tmp_path / f"work{n}")
    assert route.call_count == 3
    with pytest.raises(FetchError, match=r"stopped asking .* 10 minutes after 3 failures"):
        await fetch_pdf(URL, settings, tmp_path / "work3")
    assert route.call_count == 3


@respx.mock
async def test_old_failures_outside_window_do_not_block(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(429, content=b"slow down"))
    settings = _settings(tmp_path)
    now = [1000.0]

    def clock() -> float:
        return now[0]

    for n in range(2):
        with pytest.raises(FetchError, match=r"answered 429 \(too many requests\)"):
            await fetch_pdf(URL, settings, tmp_path / f"a{n}", sleep=_instant, monotonic=clock)
    now[0] += 301.0
    for n in range(3):
        with pytest.raises(FetchError, match=r"answered 429 \(too many requests\)"):
            await fetch_pdf(URL, settings, tmp_path / f"b{n}", sleep=_instant, monotonic=clock)
    assert route.call_count == 5


@respx.mock
async def test_success_resets_failure_count(tmp_path: Path) -> None:
    body = _pdf_bytes(tmp_path)
    settings = _settings(tmp_path)
    calls: list[httpx.Request] = []
    states: list[object] = [
        httpx.Response(429, content=b"slow"),
        httpx.Response(429, content=b"slow"),
        httpx.Response(200, content=body),
        httpx.Response(429, content=b"slow"),
        httpx.Response(429, content=b"slow"),
        httpx.Response(429, content=b"slow"),
    ]

    def _next(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        reply = states.pop(0)
        assert isinstance(reply, httpx.Response)
        return reply

    other = "https://rera.bihar.gov.in/Progress_Doc/other.pdf"
    respx.get(URL).mock(side_effect=_next)
    respx.get(other).mock(side_effect=_next)
    reject = pytest.raises(FetchError, match=r"answered 429 \(too many requests\)")
    for n in range(2):
        with reject:
            await fetch_pdf(URL, settings, tmp_path / f"a{n}", sleep=_instant, monotonic=lambda: 0.0)
    await fetch_pdf(other, settings, tmp_path / "ok", sleep=_instant, monotonic=lambda: 0.0)
    for n in range(3):
        with reject:
            await fetch_pdf(URL, settings, tmp_path / f"b{n}", sleep=_instant, monotonic=lambda: 0.0)
    assert len(calls) == 6
    with pytest.raises(FetchError, match=r"stopped asking .* 10 minutes after 3 failures"):
        await fetch_pdf(URL, settings, tmp_path / "blocked", sleep=_instant, monotonic=lambda: 0.0)
    assert len(calls) == 6


def test_timeout_config_sets_all_phases(tmp_path: Path) -> None:
    from stackcheck.ingest.sources import _timeout

    config = _timeout(_settings(tmp_path))
    assert (config.connect, config.read, config.write, config.pool) == (10.0, 30.0, 30.0, 10.0)
    httpx.AsyncClient(timeout=config)


@respx.mock
async def test_slow_five_mb_response_still_downloads(tmp_path: Path) -> None:
    import io
    import os

    from PIL import Image

    noise = Image.frombytes("RGB", (1920, 1080), os.urandom(1920 * 1080 * 3))
    buf = io.BytesIO()
    noise.save(buf, format="PNG")
    body = make_pdf(tmp_path / "big.pdf", [{"text": "hello", "images": [buf.getvalue()]}]).read_bytes()
    assert len(body) > 4 * 1024 * 1024

    async def _slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})

    respx.get(URL).mock(side_effect=_slow)
    source = await fetch_pdf(URL, _settings(tmp_path), tmp_path)
    assert source.page_count == 1


@respx.mock
async def test_second_fetch_waits_for_interval(tmp_path: Path) -> None:
    other = "https://rera.bihar.gov.in/Progress_Doc/other.pdf"
    respx.get(URL).mock(return_value=httpx.Response(200, content=_pdf_bytes(tmp_path)))
    respx.get(other).mock(return_value=httpx.Response(200, content=_pdf_bytes(tmp_path)))
    sleeps: list[float] = []

    async def sleep(secs: float) -> None:
        sleeps.append(secs)

    now = [1000.0]

    def clock() -> float:
        return now[0]

    settings = _settings(tmp_path, fetch_min_interval_s=5.0)
    await fetch_pdf(URL, settings, tmp_path, sleep=sleep, monotonic=clock)
    now[0] += 0.5
    await fetch_pdf(other, settings, tmp_path, sleep=sleep, monotonic=clock)
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(4.5)


@respx.mock
async def test_user_agent_header(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=_pdf_bytes(tmp_path)))
    await fetch_pdf(URL, _settings(tmp_path), tmp_path)
    assert route.calls[0].request.headers["user-agent"] == (
        "StackCheck/0.1 (hackathon research; contact via GitHub)"
    )


@respx.mock
async def test_seed_entry_reuses_url_without_network(tmp_path: Path) -> None:
    """Seeded URL bytes serve a later fetch with zero network calls."""
    body = _pdf_bytes(tmp_path)
    source = seed_fetch_entry(_settings(tmp_path), URL, body, "q.pdf")
    assert source.url == URL
    cached = await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_instant)
    assert cached.doc_id == source.doc_id
    assert cached.retrieved_at == source.retrieved_at


def test_seed_entry_rejects_non_pdf(tmp_path: Path) -> None:
    with pytest.raises(FetchError):
        seed_fetch_entry(_settings(tmp_path), URL, b"not a pdf", "x.pdf")


def test_seed_entry_rejects_oversize(tmp_path: Path) -> None:
    body = b"%PDF-1.4\n" + b"0" * 2000
    with pytest.raises(FetchError):
        seed_fetch_entry(_settings(tmp_path, fetch_max_bytes=1000), URL, body, "big.pdf")


def _recording_sleep(calls: list[float]) -> Callable[[float], Awaitable[None]]:
    async def _sleep(secs: float) -> None:
        calls.append(secs)

    return _sleep


def _reset_chain() -> httpx.ConnectError:
    return _caused(httpx.ConnectError("connection reset by peer"), ConnectionResetError(104, "reset"))


def _patch_get(monkeypatch: pytest.MonkeyPatch, handler: Callable[..., Awaitable[httpx.Response]]) -> None:
    """Replace AsyncClient.get so transport-level causes survive (respx drops them)."""
    monkeypatch.setattr(httpx.AsyncClient, "get", handler)


@respx.mock
async def test_reset_retried_twice_then_success_skips_breaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _pdf_bytes(tmp_path)
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=body))
    real_get = httpx.AsyncClient.get
    sleeps: list[float] = []
    calls = 0

    async def _flaky(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise _reset_chain()
        return await real_get(self, url, **kwargs)

    _patch_get(monkeypatch, _flaky)
    source = await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_recording_sleep(sleeps))
    assert source.page_count == 1
    assert route.call_count == 1
    assert calls == 3
    assert len(sleeps) == 2
    assert 2.0 <= sleeps[0] < 3.0
    assert 6.0 <= sleeps[1] < 7.0
    assert sources._blocked_until == {}


async def test_reset_three_times_counts_one_breaker_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def _always_reset(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise _reset_chain()

    _patch_get(monkeypatch, _always_reset)
    for _ in range(3):
        with pytest.raises(FetchError, match="closed the connection"):
            await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_instant)
    assert calls == 9
    assert len(sources._failures.get("rera.bihar.gov.in", [])) == 3
    with pytest.raises(FetchError, match=r"stopped asking .* 10 minutes after 3 failures"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_instant)
    assert calls == 9


async def test_refused_connection_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def _refused(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise _caused(httpx.ConnectError("[Errno 111] refused"), ConnectionRefusedError(111, "refused"))

    _patch_get(monkeypatch, _refused)
    with pytest.raises(FetchError, match="connection refused"):
        await fetch_pdf(URL, _settings(tmp_path), tmp_path, sleep=_instant)
    assert calls == 1


@respx.mock
async def test_two_same_host_starts_stagger_by_one_second(tmp_path: Path) -> None:
    body = _pdf_bytes(tmp_path)
    url2 = "https://rera.bihar.gov.in/Progress_Doc/r.pdf"
    respx.get(URL).mock(return_value=httpx.Response(200, content=body))
    respx.get(url2).mock(return_value=httpx.Response(200, content=body))
    now = [1000.0]
    sleeps: list[float] = []
    settings = _settings(tmp_path, fetch_min_interval_s=0.0)
    await fetch_pdf(URL, settings, tmp_path / "a", sleep=_recording_sleep(sleeps), monotonic=lambda: now[0])
    await fetch_pdf(url2, settings, tmp_path / "b", sleep=_recording_sleep(sleeps), monotonic=lambda: now[0])
    assert 1.0 in sleeps


@respx.mock
async def test_same_host_caps_two_concurrent_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _time

    import stackcheck.ingest.sources as _sources

    body = _pdf_bytes(tmp_path)
    active = 0
    peak = 0
    release = asyncio.Event()

    async def _slow_body(response: httpx.Response, cap: int, url: str) -> bytes:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await release.wait()
        active -= 1
        return body

    monkeypatch.setattr(_sources, "_read_capped", _slow_body)
    urls = [f"https://rera.bihar.gov.in/Progress_Doc/q{i}.pdf" for i in range(3)]
    for url in urls:
        respx.get(url).mock(return_value=httpx.Response(200, content=body))
    settings = _settings(tmp_path, fetch_min_interval_s=0.0)
    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(
                _sources._get_url(
                    client,
                    "rera.bihar.gov.in",
                    url,
                    settings,
                    sleep=_instant,
                    monotonic=_time.monotonic,
                    check_id=None,
                    cap_bytes=30_000_000,
                    started=0.0,
                    region="ap-south",
                )
            )
            for url in urls
        ]
        await asyncio.sleep(0.2)
        assert peak == 2
        release.set()
        bodies = await asyncio.gather(*tasks)
    assert bodies == [body, body, body]


@respx.mock
async def test_breaker_counts_once_per_check(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(429, content=b"slow"))
    settings = _settings(tmp_path)
    for check, work in (("A", "a"), ("A", "b"), ("B", "c"), ("C", "d")):
        with pytest.raises(FetchError, match=r"answered 429 \(too many requests\)"):
            await fetch_pdf(URL, settings, tmp_path / work, sleep=_instant, check_id=f"chk-{check * 10}")
    assert route.call_count == 4
    with pytest.raises(FetchError, match=r"stopped asking .* 10 minutes after 3 failures"):
        await fetch_pdf(URL, settings, tmp_path / "e", sleep=_instant, check_id=f"chk-{'D' * 10}")
    assert route.call_count == 4


@respx.mock
async def test_routed_host_uses_remote_fetcher(tmp_path: Path) -> None:
    body = _pdf_bytes(tmp_path)
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=body))

    def _remote(url: str, cap_bytes: int) -> dict:
        return {"status": 200, "headers": {"content-type": "application/pdf"}, "body": body}

    sources.configure_remote_fetch(_remote)
    settings = _settings(tmp_path, fetch_mumbai_hosts="rera.bihar.gov.in")
    source = await fetch_pdf(URL, settings, tmp_path, sleep=_instant)
    assert source.page_count == 1
    assert not route.called


@respx.mock
async def test_remote_too_large_falls_back_to_local(tmp_path: Path) -> None:
    body = _pdf_bytes(tmp_path)
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=body))

    def _remote(url: str, cap_bytes: int) -> dict:
        return {"too_large": True, "bytes": 9_000_000}

    sources.configure_remote_fetch(_remote)
    settings = _settings(tmp_path, fetch_mumbai_hosts="rera.bihar.gov.in")
    source = await fetch_pdf(URL, settings, tmp_path, sleep=_instant)
    assert source.page_count == 1
    assert route.called


@respx.mock
async def test_unrouted_host_ignores_remote_fetcher(tmp_path: Path) -> None:
    body = _pdf_bytes(tmp_path)
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=body))

    def _remote(url: str, cap_bytes: int) -> dict:
        raise AssertionError("must not be called")

    sources.configure_remote_fetch(_remote)
    settings = _settings(tmp_path, fetch_mumbai_hosts="other.example")
    await fetch_pdf(URL, settings, tmp_path, sleep=_instant)
    assert route.called


@respx.mock
async def test_remote_404_maps_to_not_found(tmp_path: Path) -> None:
    def _remote(url: str, cap_bytes: int) -> dict:
        return {"status": 404, "headers": {}, "body": b""}

    sources.configure_remote_fetch(_remote)
    settings = _settings(tmp_path, fetch_mumbai_hosts="rera.bihar.gov.in")
    with pytest.raises(FetchError, match="wasn't found"):
        await fetch_pdf(URL, settings, tmp_path, sleep=_instant)


@respx.mock
async def test_remote_transport_failure_maps_to_portal_sentence(tmp_path: Path) -> None:
    def _remote(url: str, cap_bytes: int) -> dict:
        raise httpx.ConnectError("mumbai down")

    sources.configure_remote_fetch(_remote)
    settings = _settings(tmp_path, fetch_mumbai_hosts="rera.bihar.gov.in")
    with pytest.raises(FetchError, match="isn't letting us download"):
        await fetch_pdf(URL, settings, tmp_path, sleep=_instant)


async def test_register_upload_rejects_non_pdf(tmp_path: Path) -> None:
    with pytest.raises(IngestError):
        register_upload(b"not a pdf", "q.pdf", tmp_path)
