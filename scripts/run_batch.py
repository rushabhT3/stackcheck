"""Run one live check per project from a manifest CSV and save results plus a summary.

Manifest columns: project_label, url, local_path. Rows are grouped by
project_label and each project gets one POST /api/checks with
sources [{"url": ...}]. The local_path column is accepted but not used:
the live server fetches the URLs itself.
"""

import argparse
import csv
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

STEP_NAMES = ["fetch", "read_filings", "photos", "floors", "rules", "investigate", "report"]

SUMMARY_COLUMNS = (
    "project",
    "check_id",
    "state",
    "quarters",
    "headline",
    "findings_ok",
    "findings_cant_verify",
    "flags_high",
    "flags_medium",
    "flags_low",
    "rule_counts",
    "cost_usd",
    "seconds_total",
    "seconds_fetch",
    "seconds_read_filings",
    "seconds_photos",
    "seconds_floors",
    "seconds_rules",
    "seconds_investigate",
    "seconds_report",
)

DEFAULT_RETRY_AFTER_S = 30.0
DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_TIMEOUT_S = 3600.0
MAX_RATE_LIMIT_RETRIES = 10


def read_manifest(path: Path) -> dict[str, list[str]]:
    """Group manifest rows by project label, keeping first-seen order."""
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        raise ValueError(f"Cannot open the manifest at {path}.") from None
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "project_label" not in reader.fieldnames:
            raise ValueError("The manifest needs a project_label column.")
        if "url" not in reader.fieldnames:
            raise ValueError("The manifest needs a url column.")
        grouped: dict[str, list[str]] = {}
        for row in reader:
            label = (row.get("project_label") or "").strip()
            url = (row.get("url") or "").strip()
            if not label or not url:
                continue
            grouped.setdefault(label, []).append(url)
    if not grouped:
        raise ValueError("No project rows with a download link were found in the manifest.")
    return grouped


def retry_after_s(response: httpx.Response, default_s: float = DEFAULT_RETRY_AFTER_S) -> float:
    """Read the Retry-After header in seconds, falling back to a default wait."""
    try:
        return max(0.0, float(response.headers.get("Retry-After", default_s)))
    except ValueError:
        return default_s


def request_with_backoff(
    client: httpx.Client,
    method: str,
    url: str,
    sleep: Callable[[float], None],
    payload: dict[str, object] | None = None,
) -> httpx.Response:
    """Send one request, waiting out each 429 reply before retrying."""
    for _ in range(MAX_RATE_LIMIT_RETRIES + 1):
        if method == "POST":
            response = client.post(url, json=payload, timeout=60.0)
        else:
            response = client.get(url, timeout=60.0)
        if response.status_code != 429:
            return response
        wait = retry_after_s(response)
        logger.info("Rate limited on %s, waiting %s seconds.", url, wait)
        sleep(wait)
    raise RuntimeError(f"Gave up after {MAX_RATE_LIMIT_RETRIES} rate-limited attempts on {url}.")


def create_check(
    client: httpx.Client,
    base_url: str,
    project_label: str,
    urls: list[str],
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Create one check for a project and return its check id."""
    if not urls:
        raise ValueError(f"Project {project_label!r} has no download links in the manifest.")
    payload = {"project_label": project_label, "sources": [{"url": url} for url in urls]}
    response = request_with_backoff(client, "POST", f"{base_url}/api/checks", sleep, payload)
    if response.status_code != 202:
        raise RuntimeError(f"Could not start the check for {project_label!r}.")
    try:
        return str(response.json()["check_id"])
    except (ValueError, KeyError, TypeError):
        raise RuntimeError(f"The server reply for {project_label!r} had no check id.") from None


def poll_until_done(
    client: httpx.Client,
    base_url: str,
    check_id: str,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, object]:
    """Poll the check status until it reaches done or failed."""
    started = time.monotonic()
    while True:
        response = request_with_backoff(client, "GET", f"{base_url}/api/checks/{check_id}", sleep)
        if response.status_code != 200:
            raise RuntimeError(f"Could not read the status of {check_id}.")
        try:
            status = response.json()
        except ValueError:
            raise RuntimeError(f"The status reply for {check_id} was not valid JSON.") from None
        if not isinstance(status, dict) or status.get("state") not in ("done", "failed"):
            if not isinstance(status, dict):
                raise RuntimeError(f"The status reply for {check_id} had no state.")
            if time.monotonic() - started > timeout_s:
                raise RuntimeError(f"Timed out waiting for {check_id} to finish.")
            sleep(poll_interval_s)
            continue
        return status


def fetch_report(
    client: httpx.Client,
    base_url: str,
    check_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object] | None:
    """Fetch the finished report, or None when the server has none yet."""
    response = request_with_backoff(client, "GET", f"{base_url}/api/checks/{check_id}/report", sleep)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"Could not read the report for {check_id}.")
    try:
        report = response.json()
    except ValueError:
        raise RuntimeError(f"The report reply for {check_id} was not valid JSON.") from None
    return report if isinstance(report, dict) else None


def step_seconds(status: dict[str, object]) -> dict[str, float]:
    """Map each pipeline step name to its seconds, from started and finished times."""
    steps = status.get("steps")
    if not isinstance(steps, list):
        return {}
    seconds: dict[str, float] = {}
    for entry in steps:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        try:
            start = datetime.fromisoformat(str(entry.get("started_at")))
            end = datetime.fromisoformat(str(entry.get("finished_at")))
        except (ValueError, TypeError):
            continue
        seconds[name] = max(0.0, (end - start).total_seconds())
    return seconds


def summarize_result(
    project: str, status: dict[str, object], report: dict[str, object] | None
) -> dict[str, str]:
    """Build one summary.csv row from a finished status and its report."""
    verdict = report.get("verdict", {}) if isinstance(report, dict) else {}
    if not isinstance(verdict, dict):
        verdict = {}
    findings = report.get("findings", []) if isinstance(report, dict) else []
    if not isinstance(findings, list):
        findings = []
    evidence = report.get("evidence", {}) if isinstance(report, dict) else {}
    quarters = evidence.get("quarters", []) if isinstance(evidence, dict) else []
    costs = report.get("costs", {}) if isinstance(report, dict) else {}
    if not isinstance(costs, dict):
        costs = {}

    ok = sum(1 for item in findings if isinstance(item, dict) and item.get("status") == "OK")
    cant_verify = sum(
        1 for item in findings if isinstance(item, dict) and item.get("status") == "CANT_VERIFY"
    )
    per_rule: dict[str, int] = {}
    for item in findings:
        if not isinstance(item, dict):
            continue
        key = f"{item.get('rule')}:{item.get('status')}"
        per_rule[key] = per_rule.get(key, 0) + 1
    rule_counts = ";".join(f"{key}={per_rule[key]}" for key in sorted(per_rule))

    seconds = step_seconds(status)
    total = sum(seconds.get(name, 0.0) for name in STEP_NAMES)
    row: dict[str, str] = {
        "project": project,
        "check_id": str(status.get("check_id", "")),
        "state": str(status.get("state", "")),
        "quarters": str(len(quarters) if isinstance(quarters, list) else 0),
        "headline": str(verdict.get("headline", "")),
        "findings_ok": str(ok),
        "findings_cant_verify": str(cant_verify),
        "flags_high": str(verdict.get("flags_high", 0)),
        "flags_medium": str(verdict.get("flags_medium", 0)),
        "flags_low": str(verdict.get("flags_low", 0)),
        "rule_counts": rule_counts,
        "cost_usd": str(costs.get("usd_total", "0")),
        "seconds_total": str(total),
    }
    for name in STEP_NAMES:
        row[f"seconds_{name}"] = str(seconds.get(name, 0.0))
    return row


def write_outputs(
    out_dir: Path, results: list[dict[str, object]], rows: list[dict[str, str]]
) -> tuple[Path, Path]:
    """Write results.json and summary.csv into the batch directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    summary_path = out_dir / "summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(SUMMARY_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return results_path, summary_path


def run_batch(
    base_url: str,
    manifest: Path,
    out_root: Path,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> Path:
    """Run one check per project in order and save results plus a summary."""
    grouped = read_manifest(manifest)
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=60.0)
    assert client is not None
    out_dir = out_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results: list[dict[str, object]] = []
    rows: list[dict[str, str]] = []
    try:
        for project, urls in grouped.items():
            logger.info("Starting check for %s with %d sources.", project, len(urls))
            check_id = create_check(client, base_url, project, urls, sleep)
            status = poll_until_done(client, base_url, check_id, sleep, poll_interval_s)
            report = (
                fetch_report(client, base_url, check_id, sleep) if status.get("state") == "done" else None
            )
            results.append({"project": project, "check_id": check_id, "status": status, "report": report})
            rows.append(summarize_result(project, status, report))
    finally:
        if own_client:
            client.close()
    write_outputs(out_dir, results, rows)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python scripts/run_batch.py`."""
    parser = argparse.ArgumentParser(description="Run one live check per project in a manifest.")
    parser.add_argument("--base-url", required=True, help="Live server URL, e.g. https://host:8000")
    parser.add_argument("--manifest", required=True, help="Manifest CSV path")
    parser.add_argument("--out-root", default="data/batch", help="Directory for timestamped batches")
    parser.add_argument("--poll-interval-s", type=float, default=DEFAULT_POLL_INTERVAL_S)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    base_url = str(args.base_url).rstrip("/")
    try:
        out_dir = run_batch(
            base_url,
            Path(str(args.manifest)),
            Path(str(args.out_root)),
            poll_interval_s=float(args.poll_interval_s),
        )
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise SystemExit(str(exc)) from None
    print(str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
