"""R4 second read: a re-rendered page either confirms the flagged drop or it cannot verify."""

import json
import logging
from datetime import date
from pathlib import Path

from stackcheck.models import Evidence, Finding, PageRef
from stackcheck.pipeline.reread import confirm_r4_flags
from tests.conftest import make_pdf
from tests.factories import make_project, make_quarter
from tests.fakes import FakeModelClient

Q2 = date(2025, 6, 30)
NEWER_DOC = "n" * 16
OLDER_DOC = "o" * 16


def _flag(
    sno: str,
    before: float,
    after: float,
    *,
    doc_id: str = NEWER_DOC,
    page: int = 7,
    rule: str = "R4_CLAIM_REGRESSION",
    status: str = "FLAG",
    severity: str = "medium",
) -> Finding:
    return Finding(
        finding_id="",
        rule=rule,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        quarter_end=Q2,
        block_label="C",
        title=f"Block C activity went down from {before:g}% to {after:g}%",
        explanation=f"Staircases: reported progress fell from {before:g}% to {after:g}%.",
        evidence=[
            Evidence(
                kind="quote",
                ref=PageRef(doc_id=OLDER_DOC, page=6),
                quote=f"{before:g}%",
                note="Progress in the earlier filing",
            ),
            Evidence(
                kind="quote",
                ref=PageRef(doc_id=doc_id, page=page),
                quote=f"{after:g}%",
                note="Progress in the later filing",
            ),
        ],
        inputs={"sno": sno, "before": str(before), "after": str(after)},
    )


def _project() -> object:
    return make_project([make_quarter(Q2)])


def _reread_json(rows: list[tuple[str, float]]) -> str:
    return json.dumps({"rows": [{"sno": sno, "value": value} for sno, value in rows]})


async def test_agree_keeps_flag(tmp_path: Path) -> None:
    """A matching second read leaves the FLAG alone; the page is rendered for real."""
    check_dir = tmp_path / "work"
    (check_dir / "sources").mkdir(parents=True)
    make_pdf(
        check_dir / "sources" / f"{NEWER_DOC}.pdf",
        [{"text": f"progress table page {n} " * 30} for n in range(1, 8)],
    )
    reader = FakeModelClient(_reread_json([("14", 40.0)]))
    findings = [_flag("14", 45.0, 40.0)]
    out = await confirm_r4_flags(findings, _project(), reader, check_dir, "chk_test")
    assert out[0].status == "FLAG"
    assert out[0].severity == "medium"
    assert len(reader.calls) == 1
    assert reader.calls[0]["images"]


async def test_disagree_downgrades_with_guard(tmp_path: Path, monkeypatch: object) -> None:
    """100 read as 40: the second read disagrees, so the finding cannot verify."""
    import stackcheck.pipeline.reread as reread

    monkeypatch.setattr(reread, "render_page", lambda *args, **kwargs: b"fake-png")  # type: ignore[attr-defined]
    reader = FakeModelClient(_reread_json([("14", 100.0)]))
    findings = [_flag("14", 45.0, 40.0)]
    out = await confirm_r4_flags(findings, _project(), reader, tmp_path, "chk_test")
    assert (out[0].status, out[0].severity) == ("CANT_VERIFY", "info")
    assert out[0].inputs["guard"] == "failed"
    assert out[0].inputs["after"] == "40.0"
    assert "40" in out[0].explanation and "100" in out[0].explanation
    refs = [e.ref for e in out[0].evidence if e.ref is not None]
    assert sorted((r.doc_id, r.page) for r in refs) == [(NEWER_DOC, 7), (OLDER_DOC, 6)]


async def test_missing_row_downgrades(tmp_path: Path, monkeypatch: object) -> None:
    """The second read has no such row: nothing confirms the flagged value."""
    import stackcheck.pipeline.reread as reread

    monkeypatch.setattr(reread, "render_page", lambda *args, **kwargs: b"fake-png")  # type: ignore[attr-defined]
    reader = FakeModelClient(_reread_json([("13", 40.0)]))
    findings = [_flag("14", 45.0, 40.0)]
    out = await confirm_r4_flags(findings, _project(), reader, tmp_path, "chk_test")
    assert (out[0].status, out[0].severity) == ("CANT_VERIFY", "info")
    assert out[0].inputs["guard"] == "failed"
    assert "14" in out[0].explanation


async def test_two_drops_same_page_cost_one_call(tmp_path: Path, monkeypatch: object) -> None:
    """One page render and one model call cover every drop on that page."""
    import stackcheck.pipeline.reread as reread

    renders: list[tuple[object, ...]] = []

    def _render(pdf_path: object, page: int) -> bytes:
        renders.append((pdf_path, page))
        return b"fake-png"

    monkeypatch.setattr(reread, "render_page", _render)  # type: ignore[attr-defined]
    reader = FakeModelClient(_reread_json([("14", 40.0), ("15", 100.0)]))
    findings = [_flag("14", 45.0, 40.0), _flag("15", 50.0, 30.0)]
    out = await confirm_r4_flags(findings, _project(), reader, tmp_path, "chk_test")
    assert len(reader.calls) == 1
    assert len(renders) == 1
    assert out[0].status == "FLAG"
    assert out[1].status == "CANT_VERIFY"


async def test_model_error_keeps_flag(tmp_path: Path, monkeypatch: object, caplog: object) -> None:
    """An infra error is not evidence of a misread: the FLAG stays."""
    import stackcheck.pipeline.reread as reread

    monkeypatch.setattr(reread, "render_page", lambda *args, **kwargs: b"fake-png")  # type: ignore[attr-defined]

    class _Boom:
        provider = "meta"
        model = "boom"

        async def complete(self, **kwargs: object) -> object:
            raise RuntimeError("reader down")

    findings = [_flag("14", 45.0, 40.0)]
    with caplog.at_level(logging.ERROR, logger="stackcheck.pipeline.reread"):
        out = await confirm_r4_flags(findings, _project(), _Boom(), tmp_path, "chk_boom")
    assert out[0].status == "FLAG"
    assert any("chk_boom" in (r.message) and NEWER_DOC in (r.message) for r in caplog.records)


async def test_no_r4_flags_means_no_calls(tmp_path: Path, monkeypatch: object) -> None:
    """Slab flags and OK findings never trigger a render or a model call."""
    import stackcheck.pipeline.reread as reread

    def _boom_render(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("no PDF reads expected")

    monkeypatch.setattr(reread, "render_page", _boom_render)  # type: ignore[attr-defined]

    class _Boom:
        provider = "meta"
        model = "boom"

        async def complete(self, **kwargs: object) -> object:
            raise AssertionError("no model calls expected")

    slab = _flag("14", 5, 4)
    slab.inputs = {"slabs_before": "5", "slabs_after": "4"}
    ok = _flag("14", 45.0, 40.0, status="OK", severity="info")
    findings = [slab, ok]
    out = await confirm_r4_flags(findings, _project(), _Boom(), tmp_path, "chk_test")
    assert [f.status for f in out] == ["FLAG", "OK"]
    assert "guard" not in out[0].inputs
