"""Run all nine rules in order, number finding ids, sort by severity then rule."""

from stackcheck.models import Finding, ProjectEvidence
from stackcheck.rules.checks import (
    RuleSettings,
    r1_photo_vs_claim,
    r2_reused_photo,
    r3_photo_date,
    r4_claim_regression,
    r5_copy_paste_quarter,
    r6_money_vs_progress,
    r7_arithmetic,
    r8_missing_quarter,
    r9_video_vs_claim,
)

_RULES = (
    r1_photo_vs_claim,
    r2_reused_photo,
    r3_photo_date,
    r4_claim_regression,
    r5_copy_paste_quarter,
    r6_money_vs_progress,
    r7_arithmetic,
    r8_missing_quarter,
    r9_video_vs_claim,
)

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3, "question": 3}


def _number_and_sort(findings: list[Finding]) -> list[Finding]:
    """Number finding ids per rule, quarter and block, then sort by severity."""
    counts: dict[str, int] = {}
    for finding in findings:
        quarter = finding.quarter_end.isoformat() if finding.quarter_end else "all"
        block = finding.block_label or "all"
        prefix = f"{finding.rule}:{quarter}:{block}"
        counts[prefix] = counts.get(prefix, 0) + 1
        finding.finding_id = f"{prefix}:{counts[prefix]}"
    return sorted(findings, key=lambda f: (_SEVERITY_RANK[f.severity], f.rule))


def run_rules(project: ProjectEvidence, rule_settings: RuleSettings) -> list[Finding]:
    """Run R1 to R9 in order, number finding ids, sort by severity then rule code."""
    findings: list[Finding] = []
    for rule in _RULES:
        findings.extend(rule(project, rule_settings))
    return _number_and_sort(findings)


_CROSS_QUARTER_RULES = (
    "R2_REUSED_PHOTO",
    "R4_CLAIM_REGRESSION",
    "R5_COPY_PASTE_QUARTER",
    "R8_MISSING_QUARTER",
)
CAPPED_EXPLANATION = "Quarters were not compared: the filings may belong to different projects."


def run_rules_capped(project: ProjectEvidence, rule_settings: RuleSettings) -> list[Finding]:
    """Rules for filings whose shared identity is unconfirmed.

    Each quarter runs on its own, so within-filing checks behave normally.
    Cross-quarter rules can only say CANT_VERIFY: they never compare filings.
    """
    findings: list[Finding] = []
    for quarter in project.quarters:
        single = ProjectEvidence(project_label=project.project_label, quarters=[quarter])
        for finding in run_rules(single, rule_settings):
            if finding.rule in _CROSS_QUARTER_RULES:
                finding.status = "CANT_VERIFY"  # type: ignore[assignment]
                finding.severity = "info"  # type: ignore[assignment]
                finding.explanation = CAPPED_EXPLANATION
            findings.append(finding)
    return _number_and_sort(findings)
