"""Trace serialization: agent steps must survive a JSON round trip."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from stackcheck.config import Settings, model_for
from stackcheck.errors import FetchError, IngestError
from stackcheck.ingest.sources import register_upload
from stackcheck.llm.meta import MetaClient
from stackcheck.llm.registry import reader_client
from stackcheck.models import AgentStep, CostSummary, ProjectEvidence, Report, Verdict
from stackcheck.pipeline.run_check import (
    DIFFERENT_PROJECTS_MESSAGE,
    SourceRequest,
    _check_same_project,
    _dedupe_quarters,
    _load_upload,
    check_dir,
    run_check,
    write_json,
)
from tests.conftest import make_pdf
from tests.factories import make_quarter
from tests.fakes import FakeModelClient


@pytest.fixture(autouse=True)
def _fresh_fetch_state() -> None:
    from stackcheck.ingest import sources as _sources

    _sources._slots.clear()
    _sources._last_request.clear()
    _sources._blocked_until.clear()
    _sources._failures.clear()
    _sources.configure_remote_fetch(None)


def _step() -> AgentStep:
    return AgentStep(
        step=0,
        finding_id="R1_PHOTO_VS_CLAIM:all:MAIN:0",
        action="give_up",
        rationale="no better photo available",
        result="kept CANT_VERIFY",
    )


def test_trace_list_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    write_json(path, [_step().model_dump(mode="json")])
    assert json.loads(path.read_text(encoding="utf-8"))[0]["finding_id"].startswith("R1_")


def test_report_with_agent_steps_serializes() -> None:
    report = Report(
        check_id="chk_" + "0" * 10,
        project_label="T",
        created_at=datetime.now(UTC),
        verdict=Verdict(
            headline="No gaps found in what we could check",
            flags_high=0,
            flags_medium=0,
            flags_low=0,
            cant_verify=1,
            ok=0,
        ),
        findings=[],
        evidence=ProjectEvidence(project_label="T", quarters=[]),
        trace=[_step()],
        summary_en=None,
        costs=CostSummary(usd_total=Decimal("0"), by_model={}, calls=0, cache_hits=0),
        versions={},
        disclaimer="test",
    )
    back = Report.model_validate_json(report.model_dump_json())
    assert back.trace[0].action == "give_up"


async def test_unexpected_failure_gets_plain_note(
    tmp_path: Path, tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw internals (e.g. a serialization TypeError) never reach status.json."""
    import stackcheck.pipeline.run_check as rc

    async def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("Object of type AgentStep is not JSON serializable")

    monkeypatch.setattr(rc, "extract_filing", _boom)
    check_id = "chk_" + "a" * 10
    work = check_dir(tmp_settings, check_id)
    pdf_path = make_pdf(tmp_path / "q.pdf", [{"text": "Quarterly progress report. " * 20}])
    source = register_upload(pdf_path.read_bytes(), "q.pdf", work)
    with pytest.raises(RuntimeError):
        await run_check(
            check_id,
            "T",
            [SourceRequest(upload_id=source.doc_id)],
            tmp_settings,
            clients={"reader": object()},
        )
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error"].startswith("Something went wrong")
    assert "serializable" not in status["error"]


async def test_fetch_error_keeps_plain_message(tmp_path: Path, tmp_settings: Settings) -> None:
    """Authored FetchError text is already plain, so it stays visible."""
    check_id = "chk_" + "b" * 10
    work = check_dir(tmp_settings, check_id)
    with pytest.raises(FetchError):
        await run_check(
            check_id,
            "T",
            [SourceRequest(upload_id="0" * 16)],
            tmp_settings,
            clients={"reader": object()},
        )
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert "couldn't find that uploaded PDF" in status["error"]
    assert "/data/" not in status["error"]
    assert "Traceback" not in status["error"]


async def test_upload_resolves_from_store_without_precopy(tmp_path: Path, tmp_settings: Settings) -> None:
    """The API flow registers into the uploads store; fetch must work from there."""
    check_id = "chk_" + "c" * 10
    work = check_dir(tmp_settings, check_id)
    pdf_path = make_pdf(tmp_path / "q.pdf", [{"text": "A short hello with no tables at all. " * 10}])
    source = register_upload(pdf_path.read_bytes(), "q.pdf", tmp_settings.data_dir / "uploads")
    assert not (work / "sources").exists()
    with pytest.raises(IngestError, match="quarterly progress report"):
        await run_check(
            check_id,
            "T",
            [SourceRequest(upload_id=source.doc_id)],
            tmp_settings,
            clients={"reader": FakeModelClient('{"doc_id": "x"}')},
        )
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert [s["name"] for s in status["steps"] if s["state"] == "done"] == ["fetch"]
    assert "quarterly progress report" in status["error"]
    assert "/data/" not in status["error"]


def test_upload_selects_private_model(tmp_settings: Settings) -> None:
    """Uploads always go to Meta's private model, even on the open-weights reader (D07)."""
    settings = tmp_settings.model_copy(update={"extract_provider": "baseten"})
    assert model_for("upload", settings) == settings.private_model
    assert model_for("url", settings) == settings.reader_model
    client = reader_client(settings, "upload")
    assert isinstance(client, MetaClient)
    assert client.model == settings.private_model


async def test_load_upload_message_has_no_path(tmp_path: Path, tmp_settings: Settings) -> None:
    work = check_dir(tmp_settings, "chk_" + "d" * 10)
    with pytest.raises(FetchError) as exc:
        await _load_upload(work, "0" * 16)
    assert "/data/" not in str(exc.value)
    assert "work" not in str(exc.value)


def _filing_quarter(reg_no=None, name=None, promoter=None, url=None, quarter_end=None, filed_on=None):
    from datetime import date as _date

    quarter = make_quarter(quarter_end or _date(2025, 3, 31), filed_on=filed_on)
    quarter.filing.project_reg_no = reg_no
    quarter.filing.project_name = name
    quarter.filing.promoter_name = promoter
    if url is not None:
        quarter.source.url = url
    return quarter


def test_different_reg_numbers_fail_with_plain_sentence() -> None:
    from datetime import date as _date

    from stackcheck.errors import IngestError

    quarters = [
        _filing_quarter(reg_no="RERA/P/2020/123", quarter_end=_date(2024, 12, 31)),
        _filing_quarter(reg_no="RERA/P/2021/999", quarter_end=_date(2025, 3, 31)),
    ]
    with pytest.raises(IngestError) as exc:
        _check_same_project(quarters)
    assert str(exc.value) == DIFFERENT_PROJECTS_MESSAGE


def test_different_bihar_prefixes_fail() -> None:
    from stackcheck.errors import IngestError

    quarters = [
        _filing_quarter(url="https://rera.bihar.gov.in/Progress_Doc/RERAP08022024125722-104141030122025.pdf"),
        _filing_quarter(url="https://rera.bihar.gov.in/Progress_Doc/RERAP31201800101-202251050442023.pdf"),
    ]
    with pytest.raises(IngestError) as exc:
        _check_same_project(quarters)
    assert str(exc.value) == DIFFERENT_PROJECTS_MESSAGE


def test_matching_reg_numbers_pass() -> None:
    quarters = [_filing_quarter(reg_no="RERA-P-2020-123"), _filing_quarter(reg_no="RERA/P/2020/123")]
    assert _check_same_project(quarters) == "same"


def test_missing_everything_is_unsure_not_mixed() -> None:
    assert _check_same_project([_filing_quarter(), _filing_quarter()]) == "unsure"


def test_same_prefix_overrides_garbled_reg_number() -> None:
    """chk_bbdf77355f: one Bihar prefix plus a garbled reg note is one project."""
    from stackcheck.pipeline.run_check import _looks_like_reg_no

    assert not _looks_like_reg_no("BRERAP92324.I 1963 IR-673 I2OI9")
    quarters = [
        _filing_quarter(
            reg_no="BRERAP92324.I 1963 IR-673 I2OI9",
            url="https://rera.bihar.gov.in/Progress_Doc/RERAP04062019192324-101281906482021.pdf",
        ),
        _filing_quarter(url="https://rera.bihar.gov.in/Progress_Doc/RERAP04062019192324-107060956402021.pdf"),
    ]
    assert _check_same_project(quarters) == "same"


def test_clean_reg_numbers_still_compared_without_prefix() -> None:
    from stackcheck.pipeline.run_check import _looks_like_reg_no

    assert _looks_like_reg_no("RERA/P/2020/123")
    assert _looks_like_reg_no("BRERAP12345/12/2021")
    quarters = [
        _filing_quarter(reg_no="RERA/P/2020/123"),
        _filing_quarter(reg_no="RERA/P/2021/999"),
    ]
    from stackcheck.errors import IngestError

    with pytest.raises(IngestError):
        _check_same_project(quarters)


def test_similar_names_pass_dissimilar_names_do_not() -> None:
    from stackcheck.pipeline.run_check import _project_similarity

    assert _project_similarity("Apogee Tower", "Apogee Builders", "APOGEE TOWER ", "Apogee Builder") >= 0.85
    assert _project_similarity("Apogee Tower", "Apogee Builders", "Palm Meadows", "Palm Ltd") < 0.85
    same = [
        _filing_quarter(name="Apogee Tower", promoter="Apogee Builders"),
        _filing_quarter(name="APOGEE TOWER", promoter="Apogee Builder"),
    ]
    assert _check_same_project(same) == "same"
    different = [
        _filing_quarter(name="Apogee Tower", promoter="Apogee Builders"),
        _filing_quarter(name="Palm Meadows", promoter="Palm Ltd"),
    ]
    assert _check_same_project(different) == "unsure"


@respx.mock
async def test_fetch_all_keeps_source_order(tmp_path: Path, tmp_settings: Settings) -> None:
    """Concurrent fetch still returns docs in source order."""
    from stackcheck.pipeline.run_check import _fetch_all

    settings = tmp_settings.model_copy(update={"fetch_min_interval_s": 0.0})
    urls = [f"https://rera.bihar.gov.in/Progress_Doc/q{i}.pdf" for i in range(3)]
    for i, url in enumerate(urls):
        body = make_pdf(tmp_path / f"q{i}.pdf", [{"text": "Quarterly progress report body."}]).read_bytes()
        respx.get(url).mock(return_value=httpx.Response(200, content=body))
    work = check_dir(settings, "chk_" + "f" * 10)
    docs, failures = await _fetch_all([SourceRequest(url=url) for url in urls], settings, work, None)
    assert [doc.url for doc in docs] == urls
    assert failures == []


@respx.mock
async def test_partial_fetch_returns_docs_and_failures(tmp_path: Path, tmp_settings: Settings) -> None:
    """One bad URL no longer fails the whole fetch; its error is collected."""
    from stackcheck.ingest import sources as _sources
    from stackcheck.pipeline.run_check import _fetch_all

    _sources.configure_remote_fetch(None)
    settings = tmp_settings.model_copy(update={"fetch_min_interval_s": 0.0})
    good = "https://rera.bihar.gov.in/Progress_Doc/good.pdf"
    bad = "https://rera.bihar.gov.in/Progress_Doc/bad.pdf"
    body = make_pdf(tmp_path / "good.pdf", [{"text": "Quarterly progress report body."}]).read_bytes()
    respx.get(good).mock(return_value=httpx.Response(200, content=body))
    respx.get(bad).mock(side_effect=httpx.ConnectError("reset by peer"))
    work = check_dir(settings, "chk_" + "f" * 10)
    docs, failures = await _fetch_all(
        [SourceRequest(url=good), SourceRequest(url=bad)], settings, work, "chk_" + "0" * 10
    )
    assert [doc.url for doc in docs] == [good]
    assert len(failures) == 1


def test_fetch_short_note_names_reset_batch() -> None:
    from stackcheck.errors import FetchError
    from stackcheck.pipeline.run_check import _fetch_short_note

    failures = [FetchError("The portal closed the connection (connection reset). Try again later.")]
    assert _fetch_short_note(failures, 3) == (
        "We could not fetch 1 of 3 quarters (connection reset). Add them to check them."
    )
    assert _fetch_short_note([], 2) is None


def test_fetch_short_note_generic_for_other_failures() -> None:
    from stackcheck.errors import FetchError
    from stackcheck.pipeline.run_check import _fetch_short_note

    failures = [FetchError("That link doesn't point to a PDF file.")]
    assert _fetch_short_note(failures, 1) == (
        "We could not fetch 1 of 1 quarters (not a PDF). Add them to check them."
    )


def test_fetch_short_note_carries_each_cause() -> None:
    from stackcheck.errors import FetchError
    from stackcheck.pipeline.run_check import _fetch_short_note

    cases = [
        ("The portal answered 403 (access refused). Try again later.", "portal refused (HTTP 403)"),
        ("The portal answered 429 (too many requests). Try again later.", "portal refused (HTTP 429)"),
        ("The portal did not answer within 30 seconds. Try again later.", "timed out"),
        (
            "Couldn't verify the security certificate of h. Try uploading the PDF instead.",
            "certificate could not be verified",
        ),
        ("We couldn't open that link. The file wasn't found. Check the link.", "not found (HTTP 404)"),
        ("Response size 1 exceeds the 2 byte cap: u", "file too large"),
        (
            "We stopped asking for 10 minutes after 3 failures in a row. Try again.",
            "paused after repeated failures",
        ),
        ("Something else entirely.", "download failed"),
    ]
    for message, reason in cases:
        assert _fetch_short_note([FetchError(message)], 8) == (
            f"We could not fetch 1 of 8 quarters ({reason}). Add them to check them."
        )


@respx.mock
async def test_all_urls_fail_raises_first_error(tmp_path: Path, tmp_settings: Settings) -> None:
    """A check with nothing fetched still fails instead of an empty report."""
    from stackcheck.pipeline.run_check import run_check

    urls = [f"https://rera.bihar.gov.in/Progress_Doc/m{i}.pdf" for i in range(2)]
    for url in urls:
        respx.get(url).mock(return_value=httpx.Response(404, content=b"gone"))
    with pytest.raises(FetchError, match="wasn't found"):
        await run_check("chk_" + "0" * 10, "T", [SourceRequest(url=url) for url in urls], tmp_settings)


def test_duplicate_quarter_keeps_both_filings_with_notice() -> None:
    """Two different filings, one quarter end: show both, never drop one silently."""
    from datetime import date as _date

    early = _filing_quarter(quarter_end=_date(2025, 3, 31), filed_on=_date(2025, 4, 13))
    late = _filing_quarter(quarter_end=_date(2025, 3, 31), filed_on=_date(2025, 4, 15))
    kept = _dedupe_quarters([early, late])
    assert len(kept) == 2
    for quarter in kept:
        assert any("Two filings state the quarter ending 2025-03-31" in note for note in quarter.filing.notes)


def _photo_facts_json(image_id: str = "x") -> str:
    import json as _json

    return _json.dumps(
        {
            "image_id": image_id,
            "kind": "document_or_logo",
            "confidence": "high",
            "reason": "test scan",
        }
    )


async def test_unsure_project_runs_capped_without_mixing(
    tmp_path: Path, tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No shared identity: the check runs, cross-quarter rules stay CANT_VERIFY."""
    from datetime import date as _date

    import stackcheck.pipeline.run_check as rc
    from stackcheck.models import ActivityProgress, QuarterFiling

    def _bare_filing(doc, end):
        return QuarterFiling(
            doc_id=doc.source.doc_id,
            quarter_end=end,
            activities=[ActivityProgress(name="9.1 Slabs", percent_done=50.0)],
        )

    async def _filing(doc, reader, *, check_id, pdf_path):
        text = doc.pages[0].text if doc.pages else ""
        end = _date(2024, 12, 31) if text.startswith("Alpha") else _date(2025, 3, 31)
        return _bare_filing(doc, end)

    monkeypatch.setattr(rc, "extract_filing", _filing)
    check_id = "chk_" + "e" * 10
    work = check_dir(tmp_settings, check_id)
    ids = []
    for name, filler in (("a.pdf", "Alpha"), ("b.pdf", "Beta")):
        pdf = make_pdf(tmp_path / name, [{"text": f"{filler} quarterly progress report. " * 20}])
        ids.append(register_upload(pdf.read_bytes(), name, tmp_settings.data_dir / "uploads").doc_id)
    vision = FakeModelClient([_photo_facts_json(), _photo_facts_json()])
    report = await run_check(
        check_id,
        "T",
        [SourceRequest(upload_id=i) for i in ids],
        tmp_settings,
        clients={"reader": FakeModelClient('{"summary": "x"}'), "vision": vision},
    )
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done"
    cross = [f for f in report.findings if f.rule in ("R2_REUSED_PHOTO", "R8_MISSING_QUARTER")]
    assert cross
    assert {f.status for f in cross} == {"CANT_VERIFY"}
    assert all("not compared" in f.explanation for f in cross)
    assert not [f for f in report.findings if f.status == "FLAG"]


async def test_same_upload_twice_runs_once_with_plain_line(
    tmp_path: Path, tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chk_941902fc: the same file twice is used once, with a plain line saying so."""
    from datetime import date as _date

    import stackcheck.pipeline.run_check as rc
    from stackcheck.models import QuarterFiling

    async def _filing(doc, reader, *, check_id, pdf_path):
        from stackcheck.models import ActivityProgress

        return QuarterFiling(
            doc_id=doc.source.doc_id,
            quarter_end=_date(2025, 3, 31),
            project_name="Demo Project",
            promoter_name="Demo Builders",
            activities=[ActivityProgress(name="9.1 Slabs", percent_done=50.0)],
        )

    monkeypatch.setattr(rc, "extract_filing", _filing)
    check_id = "chk_" + "f" * 10
    work = check_dir(tmp_settings, check_id)
    pdf = make_pdf(tmp_path / "q.pdf", [{"text": "Quarterly progress report. " * 20}])
    upload_id = register_upload(pdf.read_bytes(), "q.pdf", tmp_settings.data_dir / "uploads").doc_id
    vision = FakeModelClient([_photo_facts_json(), _photo_facts_json()])
    report = await run_check(
        check_id,
        "T",
        [SourceRequest(upload_id=upload_id), SourceRequest(upload_id=upload_id)],
        tmp_settings,
        clients={"reader": FakeModelClient('{"summary": "x"}'), "vision": vision},
    )
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done"
    assert len(report.evidence.quarters) == 1
    notes = report.evidence.quarters[0].filing.notes
    assert "You added the same file twice, so we used it once." in notes


async def test_video_evidence_estimate_range_and_sheet(
    tmp_path: Path, tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V1 pipeline: four agreeing frames give an ESTIMATE range and a contact sheet.

    Video tiers cap at ESTIMATE: frame agreement is not accuracy (Nov 2 policy).
    """
    import stackcheck.pipeline.run_check as rc
    from stackcheck.models import QuarterFiling
    from stackcheck.pipeline.run_check import VideoUpload, run_video_evidence
    from tests.conftest import make_png

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    paths = []
    for i in range(4):
        path = frames_dir / f"frame_{i:02d}.png"
        path.write_bytes(make_png(100, 80, "building"))
        paths.append(path)

    async def _sampler(video: Path, work_dir: Path, duration: float, keep: int = 8):
        return [(path, 100.0 - i) for i, path in enumerate(paths)]

    async def _probe(video: Path) -> float:
        return 30.0

    monkeypatch.setattr(rc, "sample_sharp_frames", _sampler)
    monkeypatch.setattr(rc, "video_tools_available", lambda: True)
    monkeypatch.setattr(rc, "probe_duration", _probe)
    facts_json = json.dumps(
        {
            "image_id": "x",
            "kind": "elevation",
            "vision_storeys_visible": 6,
            "confidence": "high",
            "reason": "test",
        }
    )
    vision = FakeModelClient([facts_json] * 4)

    class _Sam:
        async def segment_image(self, path: Path, prompt: str, check_id: str | None) -> str:
            if prompt == "building":
                return "<0f>1<|box;x1=10;y1=10;x2=90;y2=70;w=100;h=80;c=0.9|>"
            return ""

    check_id = "chk_" + "v" * 10
    work = check_dir(tmp_settings, check_id)
    (work / "sources").mkdir(parents=True, exist_ok=True)
    (work / "sources" / ("v" * 16 + ".mp4")).write_bytes(b"\x00" * 16)
    upload = VideoUpload(upload_id="v" * 16, block_label="MAIN")
    evidence = await run_video_evidence(
        upload,
        work,
        tmp_settings,
        check_id,
        {"vision": vision, "sam": _Sam()},
        QuarterFiling(doc_id="v"),
    )
    assert evidence.tier == "ESTIMATE"
    assert (evidence.lower, evidence.upper) == (6, 6)
    assert evidence.frames_used == 4
    assert evidence.frame_counts == [6, 6, 6, 6]
    assert evidence.contact_sheet == f"media/{'v' * 16}-sheet.png"
    assert (work / "media" / ("v" * 16 + "-sheet.png")).is_file()
