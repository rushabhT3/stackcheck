"""Assemble the Report. Part A: English summary attempted, Hindi waits for T08/S2."""

import logging
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from pydantic import BaseModel

from stackcheck.config import Settings
from stackcheck.costs import summarize
from stackcheck.extract.prompts import load_prompt
from stackcheck.llm.base import ModelClient
from stackcheck.llm.structured import ask_json
from stackcheck.models import (
    DISCLAIMER,
    AgentStep,
    Finding,
    ProjectEvidence,
    Report,
    Verdict,
)

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"

COUNT_ONLY_HEADLINE = "Count only. No filing attached, so there was nothing to check against."


class SummaryOut(BaseModel):
    summary: str


def _app_version() -> str:
    try:
        return version("stackcheck")
    except PackageNotFoundError:
        return "dev"


def _prompt_versions() -> dict[str, str]:
    return {
        name: load_prompt(name).version for name in ("filing_v5", "photo_v1", "investigate_v1", "summary_v1")
    }


def _redact(report: Report) -> None:
    """Replace names in the report. Facts on disk keep the real values."""
    names: set[str] = set()
    for quarter in report.evidence.quarters:
        filing = quarter.filing
        for value in (filing.project_reg_no, filing.project_name, filing.promoter_name):
            if value:
                names.add(value)
        filing.project_reg_no = REDACTED if filing.project_reg_no else None
        filing.project_name = REDACTED if filing.project_name else None
        filing.promoter_name = REDACTED if filing.promoter_name else None
    if not names:
        return

    def display(text: str | None) -> str | None:
        if text is None:
            return None
        for name in names:
            text = text.replace(name, REDACTED)
        return text.replace("block [redacted]", "the building").replace("Block [redacted]", "The building")

    for finding in report.findings:
        if finding.block_label in names:
            finding.block_label = None
        finding.title = display(finding.title) or finding.title
        finding.explanation = display(finding.explanation) or finding.explanation
        for evidence in finding.evidence:
            evidence.quote = display(evidence.quote)
            evidence.value = display(evidence.value)


async def _summarize(
    headline: str, findings: list[Finding], reader: ModelClient, language: str
) -> str | None:
    prompt = load_prompt("summary_v1")
    lines = [
        f"({f.status}) {f.title}: {f.explanation}" for f in findings if f.inputs.get("guard") != "failed"
    ]
    text = prompt.fill(language=language, headline=headline, findings="\n".join(lines))
    try:
        out, _ = await ask_json(
            reader,
            prompt_name="summary_v1",
            system=prompt.system,
            text=text,
            schema=SummaryOut,
            purpose=f"summary_{language}",
            check_id=None,
        )
    except Exception as exc:
        logger.warning("%s summary failed, leaving it empty: %s", language, exc)
        return None
    return out.summary


async def build_report(
    *,
    check_id: str,
    project_label: str,
    evidence: ProjectEvidence,
    findings: list[Finding],
    trace: list[AgentStep],
    settings: Settings,
    reader: ModelClient | None = None,
) -> Report:
    """Build the Report. A failed summary never fails the check."""
    flags_high = sum(1 for f in findings if f.status == "FLAG" and f.severity == "high")
    flags_medium = sum(1 for f in findings if f.status == "FLAG" and f.severity == "medium")
    flags_low = sum(1 for f in findings if f.status == "FLAG" and f.severity == "low")
    cant_verify = sum(1 for f in findings if f.status == "CANT_VERIFY")
    ok = sum(1 for f in findings if f.status == "OK")
    count_only = not evidence.quarters and not findings and bool(evidence.photos or evidence.video)
    if count_only:
        headline = COUNT_ONLY_HEADLINE
    elif flags_high + flags_medium > 0:
        headline = "Gaps found"
    elif flags_low > 0:
        note = "note" if flags_low == 1 else "notes"
        headline = f"No major gaps. {flags_low} minor {note}."
    elif ok > 0:
        headline = "No gaps found in what we could check"
    else:
        headline = "Not enough data to check"
    languages = [lang.strip() for lang in settings.summary_languages.split(",") if lang.strip()]
    summaries: dict[str, str | None] = {}
    if reader is not None:
        for language in languages:
            summaries[language] = await _summarize(headline, findings, reader, language)
    report = Report(
        check_id=check_id,
        project_label=project_label,
        created_at=datetime.now(UTC),
        verdict=Verdict(
            headline=headline,
            flags_high=flags_high,
            flags_medium=flags_medium,
            flags_low=flags_low,
            cant_verify=cant_verify,
            ok=ok,
        ),
        findings=findings,
        evidence=evidence,
        trace=trace,
        summary_en=summaries.get("en"),
        summary_hi=summaries.get("hi"),
        costs=summarize(settings.data_dir, check_id),
        versions={"app": _app_version(), **_prompt_versions()}
        | ({"reader_model": reader.model} if reader is not None else {}),
        disclaimer=DISCLAIMER,
    )
    if settings.redact_names:
        _redact(report)
    return report
