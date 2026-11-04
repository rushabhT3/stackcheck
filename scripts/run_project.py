"""Run one project end to end: PDFs in, report.json out."""

import argparse
import asyncio
import csv
import logging
import os
from pathlib import Path

from stackcheck.config import get_settings
from stackcheck.ingest.sources import register_upload
from stackcheck.pipeline.jobs import new_check_id
from stackcheck.pipeline.run_check import SourceRequest, check_dir, run_check

logger = logging.getLogger(__name__)


def _from_csv(csv_path: Path, label: str) -> list[SourceRequest]:
    with csv_path.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("project_label") == label]
    if not rows:
        raise SystemExit(f"No rows for label {label!r} in {csv_path}")
    return [SourceRequest(url=row["url"]) for row in rows]


def _from_pdfs(work: Path, pdfs: list[str]) -> list[SourceRequest]:
    requests = []
    for name in pdfs:
        path = Path(name)
        source = register_upload(path.read_bytes(), path.name, work)
        requests.append(SourceRequest(upload_id=source.doc_id))
    return requests


def main() -> int:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this paid run.")
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Run one StackCheck project.")
    parser.add_argument("--label", required=True, help="Project label, e.g. 'Project A (Patna)'")
    parser.add_argument("--csv", default="data/sources.csv", help="Sources CSV for --label rows")
    parser.add_argument("--pdf", nargs="*", default=[], help="Local PDF files instead of --csv")
    args = parser.parse_args()
    settings = get_settings()
    check_id = new_check_id()
    work = check_dir(settings, check_id)
    requests = _from_pdfs(work, args.pdf) if args.pdf else _from_csv(Path(args.csv), args.label)
    report = asyncio.run(run_check(check_id, args.label, requests, settings))
    print(f"check_id: {report.check_id}")
    print(f"verdict: {report.verdict.verdict}")
    counts: dict[str, int] = {}
    for finding in report.findings:
        counts[finding.status] = counts.get(finding.status, 0) + 1
    print(f"findings: {counts}")
    elevations = [
        photo
        for quarter in report.evidence.quarters
        for photo in quarter.photos
        if photo.facts is not None and photo.facts.kind == "elevation"
    ]
    decided = [photo for photo in elevations if photo.floors is not None and photo.floors.status == "OK"]
    undecided = [photo for photo in elevations if photo not in decided]
    print(f"floors: {len(decided)} OK / {len(undecided)} CANT_VERIFY of {len(elevations)} elevation photos")
    for photo in undecided:
        reason = photo.floors.reason if photo.floors is not None else "no floor count"
        print(f"  CANT_VERIFY {photo.image.image_id}: {reason}")
    print(report.verdict.headline)
    print(work / "report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
