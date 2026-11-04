"""WhatsApp intake: recorded webhook fixtures, fakes for every edge. No network."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient

from stackcheck.api.app import create_app
from stackcheck.config import Settings
from stackcheck.models import CostSummary, Finding, ProjectEvidence, Report, Verdict
from stackcheck.whatsapp.client import valid_signature
from stackcheck.whatsapp.handler import (
    OFF_TOPIC,
    WhatsAppDeps,
    handle_message,
    handle_update,
    is_hindi,
)
from stackcheck.whatsapp.models import WhatsAppUpdate, iter_messages
from stackcheck.whatsapp.store import SenderStore, sender_hash
from tests.conftest import make_pdf, make_png

SECRET = "s3cr3t"
PHONE = "15551234567"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        meta_api_key="test",
        whatsapp_app_secret=SECRET,
        whatsapp_verify_token="tok",
        whatsapp_token="tok",
        whatsapp_phone_number_id="42",
        public_base_url="https://site.test",
    )


class FakeClient:
    """Records sends; serves canned downloads. Never touches the network."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.files: dict[str, bytes] = {}
        self.uploads: list[bytes] = []

    async def send_text(self, to: str, body: str) -> None:
        self.sent.append((to, body))

    async def upload_media(self, data: bytes, mime_type: str) -> str:
        self.uploads.append(data)
        return "media-1"

    async def send_image(self, to: str, media_id: str, caption: str) -> None:
        self.sent.append((to, f"[image:{caption}]"))

    async def media_url(self, media_id: str) -> str:
        return f"https://x.test/{media_id}"

    async def download_media(self, url: str, cap_bytes: int) -> bytes:
        return self.files[url]


def _payload(kind: str, msg_id: str = "m1", **kw: object) -> dict:
    message: dict = {"from": PHONE, "id": msg_id, "timestamp": "1", "type": kind}
    if kind == "text":
        message["text"] = {"body": kw.get("body", "")}
    elif kind == "document":
        message["document"] = {"id": "doc1", "mime_type": "application/pdf", "filename": "q.pdf"}
    elif kind == "image":
        message["image"] = {"id": "img1", "mime_type": "image/jpeg"}
    elif kind == "audio":
        message["audio"] = {"id": "aud1", "mime_type": "audio/ogg; codecs=opus"}
    elif kind == "video":
        message["video"] = {"id": "vid1", "mime_type": "video/mp4"}
    elif kind == "status":
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {"changes": [{"value": {"messaging_product": "whatsapp", "statuses": [{"id": "m0"}]}}]}
            ],
        }
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messaging_product": "whatsapp", "messages": [message]}}]}],
    }


def _report() -> Report:
    return Report(
        check_id="chk_" + "0" * 10,
        project_label="T",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        verdict=Verdict(
            headline="Gaps found", flags_high=1, flags_medium=0, flags_low=0, cant_verify=0, ok=0
        ),
        findings=[
            Finding(
                finding_id="r1",
                rule="R1_PHOTO_VS_CLAIM",
                status="FLAG",
                severity="high",
                block_label="MAIN",
                title="T",
                explanation="E",
                evidence=[],
                inputs={},
            )
        ],
        evidence=ProjectEvidence(project_label="T", quarters=[]),
        trace=[],
        costs=CostSummary(usd_total=Decimal(0), by_model={}, calls=0, cache_hits=0),
        versions={},
        disclaimer="d",
    )


def _deps(tmp_path: Path, client: FakeClient, **overrides: object) -> WhatsAppDeps:
    settings = _settings(tmp_path)
    submitted: list = []
    kwargs: dict = {
        "settings": settings,
        "client": client,  # type: ignore[arg-type]
        "store": SenderStore(tmp_path / "wa.json"),
        "submit_check": lambda label, ids, video=None: (
            submitted.append((label, ids, video)) or "chk_" + "1" * 10
        ),
        "check_state": lambda cid: ("done", None),
        "read_report": lambda cid: _report(),
        "count_floors": lambda data: _never(),
        "answer_question": lambda report, q, lang: _never(),
        "transcribe": lambda wav: _never(),
    }
    kwargs.update(overrides)
    deps = WhatsAppDeps(**kwargs)  # type: ignore[arg-type]
    deps.submitted = submitted  # type: ignore[attr-defined]
    return deps


async def _never(*args: object) -> object:
    raise AssertionError("should not be called")


def test_signature() -> None:
    raw = b'{"a":1}'
    good = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert valid_signature(raw, good, SECRET) is True
    assert valid_signature(raw, "sha256=wrong", SECRET) is False
    assert valid_signature(raw, good, "") is False


def test_statuses_are_skipped() -> None:
    update = WhatsAppUpdate.model_validate(_payload("status"))
    assert iter_messages(update) == []


def test_is_hindi() -> None:
    assert is_hindi("जाँच") is True
    assert is_hindi("CHECK") is False


async def test_off_topic_when_no_report(tmp_path: Path) -> None:
    client = FakeClient()
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("text", body="hello?")))[0]
    await handle_message(_deps(tmp_path, client), message)
    assert client.sent == [(PHONE, OFF_TOPIC)]


async def test_question_uses_latest_report(tmp_path: Path) -> None:
    client = FakeClient()
    asked: list = []

    async def answer(report: Report, question: str, lang: str) -> str:
        asked.append((question, lang))
        return "A."

    deps = _deps(tmp_path, client, answer_question=answer)
    deps.store.note_check(sender_hash(PHONE, SECRET), "chk_" + "0" * 10)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("text", body="kya hua?")))[0]
    await handle_message(deps, message)
    assert asked == [("kya hua?", "en")]
    assert client.sent[-1] == (PHONE, "A.")


async def test_check_flow_sends_progress_and_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stackcheck.whatsapp.handler as handler

    monkeypatch.setattr(handler, "_POLL_S", 0)
    client = FakeClient()
    deps = _deps(tmp_path, client)
    deps.store.note_upload(sender_hash(PHONE, SECRET), "up1")
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("text", msg_id="c1", body="CHECK")))[0]
    await handle_message(deps, message)
    assert deps.submitted == [("WhatsApp check", ["up1"], None)]  # type: ignore[attr-defined]
    bodies = [body for _, body in client.sent]
    assert any("https://site.test/#/check/chk_" + "1" * 10 in b for b in bodies)
    assert any("Gaps found" in b for b in bodies)
    assert any("https://site.test/#/report/chk_" + "1" * 10 in b for b in bodies)


async def test_check_without_filing_asks_first(tmp_path: Path) -> None:
    client = FakeClient()
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("text", body="CHECK")))[0]
    await handle_message(_deps(tmp_path, client), message)
    assert "filing PDF" in client.sent[-1][1]


async def test_duplicate_message_sent_once(tmp_path: Path) -> None:
    client = FakeClient()
    deps = _deps(tmp_path, client)
    update = WhatsAppUpdate.model_validate(_payload("text", body="hello?"))
    await handle_update(deps, update)
    await handle_update(deps, update)
    assert len(client.sent) == 1


async def test_pdf_upload_flows_into_pending(tmp_path: Path) -> None:
    client = FakeClient()
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": "hello"}]).read_bytes()
    client.files["https://x.test/doc1"] = pdf
    deps = _deps(tmp_path, client)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("document")))[0]
    await handle_message(deps, message)
    assert "Got the filing" in client.sent[-1][1]
    assert deps.store.pending_count(sender_hash(PHONE, SECRET)) == 1


async def test_non_pdf_upload_refused_plainly(tmp_path: Path) -> None:
    client = FakeClient()
    client.files["https://x.test/doc1"] = b"not a pdf"
    deps = _deps(tmp_path, client)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("document")))[0]
    await handle_message(deps, message)
    assert "pdf" in client.sent[-1][1].lower()
    assert deps.store.pending_count(sender_hash(PHONE, SECRET)) == 0


async def test_image_match_replies_with_page(tmp_path: Path) -> None:
    from stackcheck.vision.phash import dhash_hex

    png = make_png(720, 1280, "building")
    digest = dhash_hex(png)
    facts_dir = tmp_path / "work" / ("chk_" + "2" * 10) / "facts"
    facts_dir.mkdir(parents=True)
    (facts_dir / "p1.photo.json").write_text(
        json.dumps({"image": {"image_id": "p1", "doc_id": "d" * 16, "page": 14}, "dhash": digest}),
        encoding="utf-8",
    )
    (facts_dir / ("d" * 16 + ".filing.json")).write_text(
        json.dumps({"quarter_end": "2025-03-31"}), encoding="utf-8"
    )
    client = FakeClient()
    client.files["https://x.test/img1"] = png
    deps = _deps(tmp_path, client)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("image")))[0]
    await handle_message(deps, message)
    assert client.sent[-1] == (
        PHONE,
        "This photo matches the photo on page 14 of the RERA filing for the quarter ending 31 Mar 2025.",
    )


async def test_image_without_match_counts_floors(tmp_path: Path) -> None:
    client = FakeClient()
    client.files["https://x.test/img1"] = make_png(720, 1280, "building")

    async def count(data: bytes) -> int | None:
        return 7

    deps = _deps(tmp_path, client, count_floors=count)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("image")))[0]
    await handle_message(deps, message)
    assert client.sent[-1] == (PHONE, "This photo shows about 7 floors.")


def _mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def test_video_kind_parsed() -> None:
    (message,) = iter_messages(WhatsAppUpdate.model_validate(_payload("video")))
    assert message.kind == "video"
    assert message.media_id == "vid1"


async def test_video_without_filing_asks_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stackcheck.whatsapp.handler as handler

    monkeypatch.setattr(handler, "probe_duration", lambda path: 30.0)
    client = FakeClient()
    client.files["https://x.test/vid1"] = _mp4_bytes()
    deps = _deps(tmp_path, client)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("video")))[0]
    await handle_message(deps, message)
    assert "filing PDF" in client.sent[-1][1]
    assert deps.submitted == []  # type: ignore[attr-defined]


async def test_video_with_filing_runs_check_with_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stackcheck.whatsapp.handler as handler

    monkeypatch.setattr(handler, "_POLL_S", 0)
    monkeypatch.setattr(handler, "probe_duration", lambda path: 30.0)
    client = FakeClient()
    client.files["https://x.test/vid1"] = _mp4_bytes()
    deps = _deps(tmp_path, client)
    deps.store.note_upload(sender_hash(PHONE, SECRET), "up1")
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("video", msg_id="v1")))[0]
    await handle_message(deps, message)
    [(label, ids, video)] = deps.submitted  # type: ignore[attr-defined]
    assert (label, ids) == ("WhatsApp check", ["up1"])
    assert video is not None and video.block_label == "MAIN"
    bodies = [body for _, body in client.sent]
    assert any("Gaps found" in body for body in bodies)


async def test_video_too_long_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stackcheck.whatsapp.handler as handler

    monkeypatch.setattr(handler, "probe_duration", lambda path: 41.0)
    client = FakeClient()
    client.files["https://x.test/vid1"] = _mp4_bytes()
    deps = _deps(tmp_path, client)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("video")))[0]
    await handle_message(deps, message)
    assert "40 seconds" in client.sent[-1][1]


async def test_video_bad_magic_refused(tmp_path: Path) -> None:
    client = FakeClient()
    client.files["https://x.test/vid1"] = b"not a video"
    deps = _deps(tmp_path, client)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("video")))[0]
    await handle_message(deps, message)
    assert "video" in client.sent[-1][1].lower()


async def test_send_evidence_prefers_contact_sheet(tmp_path: Path) -> None:
    from stackcheck.models import Evidence
    from stackcheck.whatsapp.handler import _send_evidence

    check_id = "chk_" + "s" * 10
    media = tmp_path / "work" / check_id / "media"
    media.mkdir(parents=True)
    (media / "abc-sheet.png").write_bytes(make_png(100, 80, "building"))
    client = FakeClient()
    deps = _deps(tmp_path, client)
    report = _report()
    report.findings.append(
        Finding(
            finding_id="r9",
            rule="R9_VIDEO_VS_CLAIM",
            status="FLAG",
            severity="high",
            block_label="MAIN",
            title="T",
            explanation="E",
            evidence=[
                Evidence(
                    kind="photo",
                    image_id="abc",
                    overlay_path="media/abc-sheet.png",
                    note="Contact sheet",
                )
            ],
            inputs={},
        )
    )
    await _send_evidence(deps, PHONE, check_id, report)
    assert client.sent[-1] == (PHONE, "[image:Contact sheet from the site video.]")


async def test_send_evidence_prefers_question_sheet(tmp_path: Path) -> None:
    from stackcheck.models import Evidence
    from stackcheck.whatsapp.handler import _send_evidence

    check_id = "chk_" + "s" * 10
    media = tmp_path / "work" / check_id / "media"
    media.mkdir(parents=True)
    (media / "abc-sheet.png").write_bytes(make_png(100, 80, "building"))
    (media / "other.overlay.thumb.jpg").write_bytes(make_png(10, 10, "stripes"))
    client = FakeClient()
    deps = _deps(tmp_path, client)
    report = _report()
    report.findings.append(
        Finding(
            finding_id="r9",
            rule="R9_VIDEO_VS_CLAIM",
            status="CANT_VERIFY",
            severity="question",
            block_label="MAIN",
            title="T",
            explanation="E",
            evidence=[
                Evidence(
                    kind="photo",
                    image_id="abc",
                    overlay_path="media/abc-sheet.png",
                    note="Contact sheet",
                )
            ],
            inputs={},
        )
    )
    await _send_evidence(deps, PHONE, check_id, report)
    assert client.sent[-1] == (PHONE, "[image:Contact sheet from the site video.]")


async def test_image_without_match_and_no_count(tmp_path: Path) -> None:
    client = FakeClient()
    client.files["https://x.test/img1"] = make_png(720, 1280, "building")

    async def count(data: bytes) -> int | None:
        return None

    deps = _deps(tmp_path, client, count_floors=count)
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("image")))[0]
    await handle_message(deps, message)
    assert client.sent[-1][1] == "I can't tell from this photo."


async def test_audio_answers_from_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stackcheck.whatsapp.handler as handler

    monkeypatch.setattr(handler, "ogg_to_wav", lambda data: _wav())
    client = FakeClient()

    async def transcribe(wav: bytes) -> str:
        return "kya pragati hai"

    async def answer(report: Report, question: str, lang: str) -> str:
        assert lang == "en"
        return "A."

    async def _wav() -> bytes:
        return b"wav"

    deps = _deps(tmp_path, client, transcribe=transcribe, answer_question=answer)
    deps.store.note_check(sender_hash(PHONE, SECRET), "chk_" + "0" * 10)
    client.files["https://x.test/aud1"] = b"ogg"
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("audio")))[0]
    await handle_message(deps, message)
    assert client.sent[-1] == (PHONE, "A.")


async def test_old_unreferenced_uploads_swept(tmp_path: Path) -> None:
    import os
    import time

    uploads = tmp_path / "uploads" / "sources"
    uploads.mkdir(parents=True)
    old = uploads / "deadbeef00000001.pdf"
    old.write_bytes(b"%PDF-old")
    ancient = time.time() - 8 * 24 * 3600
    os.utime(old, (ancient, ancient))
    kept = uploads / "live000000000001.pdf"
    kept.write_bytes(b"%PDF-live")
    store = SenderStore(tmp_path / "wa.json", uploads_dir=uploads)
    store.note_upload(sender_hash(PHONE, SECRET), "live000000000001")
    store2 = SenderStore(tmp_path / "wa.json", uploads_dir=uploads)
    assert not old.exists()
    assert kept.exists()
    assert store2.pending_count(sender_hash(PHONE, SECRET)) == 1


async def test_sender_rate_limit(tmp_path: Path) -> None:
    client = FakeClient()
    deps = _deps(tmp_path, client)
    hashed = sender_hash(PHONE, SECRET)
    for _ in range(3):
        deps.store.note_check(hashed, "chk_" + "0" * 10)
    deps.store.note_upload(hashed, "up1")
    message = iter_messages(WhatsAppUpdate.model_validate(_payload("text", body="CHECK")))[0]
    await handle_message(deps, message)
    assert "3 checks" in client.sent[-1][1]
    assert not getattr(deps, "submitted", [])


@respx.mock
async def test_transcribe_posts_expected_shape(tmp_path: Path) -> None:
    import httpx

    from stackcheck.whatsapp.voice import TRANSCRIBE_MODEL, transcribe_wav

    route = respx.post("https://api.meta.ai/v1/asr/transcribe").mock(
        return_value=httpx.Response(200, json={"text": "namaste"})
    )
    assert await transcribe_wav("https://api.meta.ai/v1", "k", b"wav") == "namaste"
    assert route.called
    assert TRANSCRIBE_MODEL == "muse-voice-transcribe-1.0"


async def test_ogg_garbage_raises_voice_error() -> None:
    from stackcheck.whatsapp.voice import VoiceError, ogg_to_wav

    with pytest.raises(VoiceError):
        await ogg_to_wav(b"ogg")


class _Runner:
    def submit(self, project_label: str, sources: list) -> str:
        return "chk_" + "3" * 10

    def status(self, check_id: str) -> object:
        raise AssertionError("should not be called")


def _route_client(tmp_path: Path) -> TestClient:
    settings = _settings(tmp_path)
    return TestClient(create_app(settings=settings, runner=_Runner()))  # type: ignore[arg-type]


def test_verify_handshake(tmp_path: Path) -> None:
    client = _route_client(tmp_path)
    response = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=tok&hub.challenge=abc")
    assert response.status_code == 200
    assert response.text == "abc"


def test_verify_handshake_wrong_token(tmp_path: Path) -> None:
    client = _route_client(tmp_path)
    response = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=no&hub.challenge=abc")
    assert response.status_code == 403


def test_incoming_bad_signature(tmp_path: Path) -> None:
    client = _route_client(tmp_path)
    response = client.post("/webhooks/whatsapp", content=b"{}", headers={"X-Hub-Signature-256": "sha256=x"})
    assert response.status_code == 403


def test_incoming_status_only_ok(tmp_path: Path) -> None:
    import json as _json

    client = _route_client(tmp_path)
    raw = _json.dumps(_payload("status")).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": sig})
    assert response.status_code == 200
