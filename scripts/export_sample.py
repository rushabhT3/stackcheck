"""Export one live report as a committable sample.

Downloads the report JSON, strips every media path (no image bytes in the
repo), keeps source URLs and page references, writes
`data/samples/<check_id>/report.json`, and registers the sample in
`data/samples.json` without duplicating an existing `check_id`.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx


def strip_report(report: dict) -> dict:
    """Null media paths in place. Page refs and source URLs are kept."""
    for quarter in report.get("evidence", {}).get("quarters", []):
        for photo in quarter.get("photos", []):
            photo["overlay_path"] = None
            if isinstance(photo.get("image"), dict):
                photo["image"]["path"] = ""
    for finding in report.get("findings", []):
        for evidence in finding.get("evidence", []):
            if "overlay_path" in evidence:
                evidence["overlay_path"] = None
    return report


def append_sample(samples_path: Path, entry: dict) -> list:
    """Append {check_id, project_label, headline} unless the check_id is listed."""
    samples: list = []
    if samples_path.exists():
        samples = json.loads(samples_path.read_text(encoding="utf-8"))
    if not any(s.get("check_id") == entry["check_id"] for s in samples):
        samples.append(entry)
        samples_path.write_text(json.dumps(samples, indent=2), encoding="utf-8")
    return samples


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("check_id", help="check id, also the folder name under data/samples/")
    parser.add_argument("--from-url", required=True, help="live report URL for this check")
    args = parser.parse_args(argv)
    response = httpx.get(args.from_url, timeout=120)
    response.raise_for_status()
    report = strip_report(response.json())
    dest = Path("data/samples") / args.check_id / "report.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {dest}")
    entry = {
        "check_id": report.get("check_id", args.check_id),
        "project_label": report.get("project_label", ""),
        "headline": report.get("headline", ""),
    }
    append_sample(Path("data/samples.json"), entry)
    print(f"Registered {entry['check_id']} in data/samples.json")


if __name__ == "__main__":
    sys.exit(main())
