"""Write review.csv for a C1 batch dir: one row per finding for human review.

Batch layout (as the C1 batch step stores it):

    <batch>/results.json                # [{"check_id": ..., "project_label": ...}, ...]
    <batch>/<check_id>/report.json      # the check report JSON from run_check

Any other ``*.json`` file under ``<batch>`` that parses as a ``Report``
is also picked up, so a layout change in C1 does not silently drop checks.

Each finding becomes one CSV row with ``check_id``, ``project_label``,
``rule``, ``status``, ``title``, ``explanation``, ``values`` (every
evidence value with its document URL and page as ``url#page=N``),
``images`` (image file names from finding evidence ``image_id`` /
``overlay_path`` basenames), and the empty ``reviewer_verdict``
(``true``, ``false`` or ``unclear``) and ``reviewer_note`` columns the
reviewer fills in.
"""

import argparse
import csv
import json
import logging
from pathlib import Path

from stackcheck.models import Evidence, Finding, Report

logger = logging.getLogger(__name__)

COLUMNS = [
    "check_id",
    "project_label",
    "rule",
    "status",
    "title",
    "explanation",
    "values",
    "images",
    "reviewer_verdict",
    "reviewer_note",
]


def _doc_bases(report: Report) -> dict[str, str]:
    """Map doc_id to its display base: source URL, else filename, else doc_id."""
    bases: dict[str, str] = {}
    for quarter in report.evidence.quarters:
        source = quarter.source
        bases[source.doc_id] = source.url or source.filename or source.doc_id
    return bases


def _value_part(item: Evidence, bases: dict[str, str]) -> str | None:
    """One evidence value with its document URL and page, or None without a value."""
    if item.value is None:
        return None
    if item.ref is None:
        return item.value
    base = bases.get(item.ref.doc_id, item.ref.doc_id)
    return f"{item.value} @ {base}#page={item.ref.page}"


def format_values(finding: Finding, bases: dict[str, str]) -> str:
    """Join every evidence value with its document URL and page as url#page=N."""
    return "; ".join(part for part in (_value_part(item, bases) for item in finding.evidence) if part)


def image_names(finding: Finding) -> str:
    """Image file names from finding evidence image_id / overlay_path basenames."""
    names: list[str] = []
    for item in finding.evidence:
        for raw in (item.image_id, item.overlay_path):
            if raw:
                name = Path(raw).name
                if name and name not in names:
                    names.append(name)
    return "; ".join(names)


def finding_row(report: Report, finding: Finding) -> dict[str, str]:
    """One review.csv row for a single finding."""
    bases = _doc_bases(report)
    return {
        "check_id": report.check_id,
        "project_label": report.project_label,
        "rule": finding.rule,
        "status": finding.status,
        "title": finding.title,
        "explanation": finding.explanation,
        "values": format_values(finding, bases),
        "images": image_names(finding),
        "reviewer_verdict": "",
        "reviewer_note": "",
    }


def _take_report(raw: object, seen: set[str], reports: list[Report], where: object) -> None:
    """Validate one embedded or file report dict, skipping repeats and junk."""
    if not isinstance(raw, dict):
        return
    try:
        report = Report.model_validate(raw)
    except ValueError as exc:
        logger.warning("Skipping %s: not a check report (%s)", where, exc)
        return
    if report.check_id in seen:
        return
    seen.add(report.check_id)
    reports.append(report)


def load_reports(batch: Path) -> list[Report]:
    """Load every check report in the batch dir, in check_id order."""
    reports: list[Report] = []
    seen: set[str] = set()
    candidates: list[Path] = []
    results_path = batch / "results.json"
    if results_path.exists():
        entries = json.loads(results_path.read_text(encoding="utf-8"))
        items = entries if isinstance(entries, list) else entries.get("checks", [])
        for entry in items:
            if not isinstance(entry, dict) or not entry.get("check_id"):
                continue
            # C1 embeds each report in its results.json entry; files are the fallback.
            if "report" in entry:
                _take_report(entry["report"], seen, reports, results_path)
                continue
            candidates.append(batch / str(entry["check_id"]) / "report.json")
    for path in sorted(batch.rglob("*.json")):
        if path.name in ("results.json", "review.csv", "status.json"):
            continue
        if path not in candidates:
            candidates.append(path)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("Skipping %s: not a check report (%s)", path, exc)
            continue
        _take_report(raw, seen, reports, path)
    reports.sort(key=lambda report: report.check_id)
    return reports


def write_review_csv(batch: Path) -> Path:
    """Write one review row per finding in the batch. Returns the CSV path."""
    if not batch.is_dir():
        raise SystemExit(f"Batch dir not found: {batch}")
    reports = load_reports(batch)
    dest = batch / "review.csv"
    with dest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for report in reports:
            for finding in report.findings:
                writer.writerow(finding_row(report, finding))
    total = sum(len(report.findings) for report in reports)
    logger.info("Wrote %s: %d checks, %d findings", dest, len(reports), total)
    print(f"Wrote {dest}: {len(reports)} checks, {total} findings")
    return dest


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write review.csv for a C1 batch dir.")
    parser.add_argument("--batch", required=True, type=Path, help="C1 batch directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``review_sheet.py --batch <dir>`` writes review.csv."""
    args = _parse_args(argv)
    write_review_csv(args.batch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
