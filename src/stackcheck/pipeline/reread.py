"""Second read of R4 activity flags. One page render plus one model call per page."""

import asyncio
import json
import logging
import math
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, Field

from stackcheck.extract.prompts import load_prompt
from stackcheck.ingest.pdf import render_page
from stackcheck.llm.base import ImagePart, ModelClient
from stackcheck.models import Finding, PageRef, ProjectEvidence

logger = logging.getLogger(__name__)

NEWER_NOTE = "Progress in the later filing"


class RereadRow(BaseModel):
    sno: str
    value: float = Field(ge=0, le=100)


class RereadPage(BaseModel):
    rows: list[RereadRow] = []


class _Target(NamedTuple):
    finding: Finding
    sno: str
    after: float


def _newer_ref(finding: Finding) -> PageRef | None:
    """The later filing's page ref: the evidence row the drop was read from."""
    fallback: PageRef | None = None
    for item in finding.evidence:
        if item.ref is None:
            continue
        fallback = item.ref
        if item.note == NEWER_NOTE:
            return item.ref
    return fallback


def _targets(findings: list[Finding]) -> dict[tuple[str, int], list[_Target]]:
    """Group confirmable drops by newer (doc_id, page). Pure: no I/O."""
    grouped: dict[tuple[str, int], list[_Target]] = {}
    for finding in findings:
        if finding.rule != "R4_CLAIM_REGRESSION" or finding.status != "FLAG":
            continue
        sno = finding.inputs.get("sno", "").strip()
        if not sno:
            continue
        try:
            after = float(finding.inputs["after"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(after):
            continue
        ref = _newer_ref(finding)
        if ref is None:
            continue
        grouped.setdefault((ref.doc_id, ref.page), []).append(_Target(finding, sno, after))
    return grouped


def _parse_rows(text: str) -> dict[str, float] | None:
    """The model's rows keyed by S.No, or None when the output is unusable."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        page = RereadPage.model_validate(json.loads(text[start : end + 1]))
    except ValueError:
        return None
    rows: dict[str, float] = {}
    for row in page.rows:
        key = row.sno.strip()
        if key and key not in rows:
            rows[key] = row.value
    return rows


def _downgrade(finding: Finding, *, sno: str, after: float, page: int, reread_value: float | None) -> None:
    """A disagreeing second read cannot verify: FLAG becomes CANT_VERIFY in place."""
    finding.status = "CANT_VERIFY"
    finding.severity = "info"
    finding.inputs["guard"] = "failed"
    if reread_value is None:
        finding.explanation = (
            f"A second read of page {page} found no row {sno} "
            f"(flagged {after:g}%), so this gap cannot be verified."
        )[:400]
    else:
        finding.explanation = (
            f"A second read of page {page} found {reread_value:g}% for row {sno} "
            f"instead of the flagged {after:g}%, so this gap cannot be verified."
        )[:400]


async def confirm_r4_flags(
    findings: list[Finding],
    project: ProjectEvidence,
    reader: ModelClient,
    check_dir: Path,
    check_id: str | None,
) -> list[Finding]:
    """Re-read each flagged R4 page once; downgrade drops the second read disputes.

    `project` carries the check context alongside the findings. Only activity
    FLAGs with sno/before/after inputs and a newer page ref are confirmable;
    everything else is returned untouched. Render and model errors are
    non-fatal: the original FLAG is kept.
    """
    _ = project
    grouped = _targets(findings)
    if not grouped:
        return findings
    prompt = load_prompt("reread_v1")
    for (doc_id, page), targets in sorted(grouped.items()):
        try:
            png = await asyncio.to_thread(render_page, check_dir / "sources" / f"{doc_id}.pdf", page)
            reply = await reader.complete(
                system=prompt.system,
                text=prompt.fill(doc_id=doc_id, page=page),
                images=[ImagePart(data=png, mime="image/png")],
                purpose="reread_r4",
                check_id=check_id,
            )
            rows = _parse_rows(reply.text)
        except Exception as exc:
            logger.error("Reread failed for %s doc=%s page=%s: %s", check_id, doc_id, page, exc)
            continue
        if rows is None:
            logger.error(
                "Reread failed for %s doc=%s page=%s: unparseable model output",
                check_id,
                doc_id,
                page,
            )
            continue
        for target in targets:
            value = rows.get(target.sno)
            if value is not None and value == target.after:
                continue
            _downgrade(
                target.finding,
                sno=target.sno,
                after=target.after,
                page=page,
                reread_value=value,
            )
    return findings
