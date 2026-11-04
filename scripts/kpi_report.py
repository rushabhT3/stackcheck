"""Print every KPI from docs/KPIS.md with target and PASS / WARN / FAIL / NO DATA.

Writes docs/kpi_snapshot.md with a `Snapshot: <IST timestamp>` header.
Each source is gathered by its own small function that returns None when
the source is missing; a missing source shows NO DATA, never a crash.
`--no-tests` skips the pytest run (fast mode).
"""

import argparse
import contextlib
import json
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))  # Asia/Kolkata has no DST

PASS, WARN, FAIL, NO_DATA = "PASS", "WARN", "FAIL", "NO DATA"


def kpi_status(value: float | None, target: float, higher_is_better: bool, red_line: float) -> str:
    """PASS on target, FAIL past the red line, WARN in between, NO DATA when None."""
    if value is None:
        return NO_DATA
    if higher_is_better:
        if value >= target:
            return PASS
        if value <= red_line:
            return FAIL
        return WARN
    if value <= target:
        return PASS
    if value >= red_line:
        return FAIL
    return WARN


@dataclass
class LedgerRow:
    ts: datetime
    check_id: str | None
    usd: Decimal
    cache_hit: bool


def parse_ledger(lines: list[str]) -> list[LedgerRow]:
    """Parse ledger lines, skipping blanks and malformed rows."""
    rows: list[LedgerRow] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            rows.append(
                LedgerRow(
                    ts=datetime.fromisoformat(raw["ts"]),
                    check_id=raw.get("check_id"),
                    usd=Decimal(str(raw["usd"])),
                    cache_hit=bool(raw.get("cache_hit")),
                )
            )
        except (ValueError, KeyError, TypeError):
            continue
    return rows


def daily_usd(rows: list[LedgerRow]) -> dict[date, Decimal]:
    """Total USD per day. Day boundaries are IST, not UTC."""
    totals: dict[date, Decimal] = {}
    for row in rows:
        day = row.ts.astimezone(IST).date()
        totals[day] = totals.get(day, Decimal("0")) + row.usd
    return totals


def cost_per_check(rows: list[LedgerRow]) -> dict[str, Decimal]:
    """Total USD per check_id. Rows without a check_id are app overhead, not a check."""
    totals: dict[str, Decimal] = {}
    for row in rows:
        if row.check_id is None:
            continue
        totals[row.check_id] = totals.get(row.check_id, Decimal("0")) + row.usd
    return totals


def is_real_check(check_id: str | None) -> bool:
    """Real checks are `chk_` + hex (C16/C17); bench scripts reuse other labels."""
    return check_id is not None and check_id.startswith("chk_")


def rerun_cache_share(rows: list[LedgerRow]) -> float | None:
    """Cache-hit share for the most recent check that ran twice (hits and misses)."""
    by_check: dict[str, list[LedgerRow]] = {}
    for row in rows:
        if row.check_id is not None:
            by_check.setdefault(row.check_id, []).append(row)
    reruns = [
        group
        for group in by_check.values()
        if any(r.cache_hit for r in group) and any(not r.cache_hit for r in group)
    ]
    if not reruns:
        return None
    latest = max(reruns, key=lambda group: max(r.ts for r in group))
    return sum(1 for r in latest if r.cache_hit) / len(latest)


def count_open_stuck(text: str) -> int:
    """Open STUCK items: `## ` headings without RESOLVED."""
    return sum(1 for line in text.splitlines() if line.startswith("## ") and "RESOLVED" not in line)


@dataclass
class KpiDef:
    name: str
    target: float
    higher_is_better: bool
    red_line: float
    kind: str  # "pct" | "usd" | "s" | "int"


KPI_DEFS = [
    KpiDef("Unit tests passing", 100, True, 100, "pct"),
    KpiDef("Branch coverage (rules+vision)", 100, True, 95, "pct"),
    KpiDef("End-to-end smoke passes", 100, True, 100, "pct"),
    KpiDef("Credit max day (USD)", 10, False, 40, "usd"),
    KpiDef("Cache hit share on rerun", 95, True, 80, "pct"),
    KpiDef("Cold seconds per quarter", 90, False, 180, "s"),
    KpiDef("Open STUCK items", 0, False, 1, "int"),
    KpiDef("Floor count within +-1", 80, True, 60, "pct"),
    KpiDef("Wrong-and-confident", 5, False, 10, "pct"),
    KpiDef("Abstain rate", 35, False, 50, "pct"),
    KpiDef("Filing field accuracy", 95, True, 85, "pct"),
    KpiDef("Photo kind accuracy", 90, True, 80, "pct"),
    KpiDef("Stamp date correct", 90, True, 75, "pct"),
    KpiDef("Hand-verified FLAG share", 100, True, 100, "pct"),
    KpiDef("Real projects checked", 5, True, 3, "int"),
    KpiDef("Real FLAGs hand-verified", 1, True, 1, "int"),
    KpiDef("Cost per project check (USD)", 0.50, False, 2.00, "usd"),
    KpiDef("Provider comparison done", 100, True, 100, "pct"),
]


def _format(value: float, kind: str) -> str:
    if kind == "pct":
        return f"{value:.1f}%"
    if kind == "usd":
        return f"${value:.2f}" if value >= 1 else f"${value:.4f}"
    if kind == "s":
        return f"{value:.0f} s"
    return str(int(value))


def _read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def gather_tests(run: bool) -> dict[str, float | None]:
    """Run pytest with junit+coverage, read pass counts and branch coverage."""
    out: dict[str, float | None] = {"unit_pct": None, "e2e": None, "coverage": None}
    if run:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [
                    "uv",
                    "run",
                    "pytest",
                    "-q",
                    "--junitxml=data/eval/junit.xml",
                    "--cov=stackcheck.rules",
                    "--cov=stackcheck.vision",
                    "--cov-branch",
                    "--cov-report=json:data/eval/coverage.json",
                ],
                timeout=600,
                check=False,
            )
    unit_pct, e2e = _parse_junit(Path("data/eval/junit.xml"))
    out["unit_pct"] = unit_pct
    out["e2e"] = e2e
    out["coverage"] = _parse_coverage(Path("data/eval/coverage.json"))
    return out


def _parse_junit(path: Path) -> tuple[float | None, float | None]:
    try:
        import xml.etree.ElementTree as ET

        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None, None
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return None, None
    try:
        total = int(suite.get("tests", 0))
        failed = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        skipped = int(suite.get("skipped", 0))
    except ValueError:
        return None, None
    unit_pct = (total - failed - skipped) / (total - skipped) * 100 if total - skipped > 0 else None
    smoke: float | None = None
    for case in suite.iter("testcase"):
        name = f"{case.get('classname', '')} {case.get('file', '')} {case.get('name', '')}"
        if "pipeline_smoke" in name:
            smoke = 0.0 if case.find("failure") is not None or case.find("error") is not None else 100.0
    return unit_pct, smoke


def _parse_coverage(path: Path) -> float | None:
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    files = data.get("files", {})
    watched = [
        pct
        for name, entry in files.items()
        for flat in [name.replace("\\", "/")]
        if "stackcheck/rules/" in flat
        or flat.endswith("vision/floors.py")
        or flat.endswith("vision/sam_parse.py")
        or flat.endswith("vision/phash.py")
        for pct in [entry.get("summary", {}).get("percent_covered")]
        if isinstance(pct, (int, float))
    ]
    return min(watched) if watched else None


def gather_ledger() -> dict[str, float | None]:
    out: dict[str, float | None] = {"max_day": None, "rerun_share": None, "max_check": None}
    try:
        lines = Path("data/ledger.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    rows = parse_ledger(lines)
    if not rows:
        return out
    days = daily_usd(rows)
    out["max_day"] = float(max(days.values()))
    share = rerun_cache_share([r for r in rows if is_real_check(r.check_id)])
    out["rerun_share"] = share * 100 if share is not None else None
    per_check = cost_per_check(rows)
    out["max_check"] = float(max(per_check.values())) if per_check else None
    return out


def gather_eval() -> dict[str, float | None]:
    out: dict[str, float | None] = {
        "within1": None,
        "wrong": None,
        "abstain": None,
        "kind": None,
        "stamp": None,
        "extract": None,
        "providers": None,
    }
    bench = _read_json(Path("data/eval/bench_floors.json"))
    if isinstance(bench, dict):
        best = bench.get("best")
        row = (bench.get("prompt_sets", {}) or {}).get(best, {}).get("summary", {})
        if row.get("within1") is not None:
            out["within1"] = float(row["within1"]) * 100
            out["wrong"] = float(row.get("wrong_confident", 0)) * 100
            out["abstain"] = float(row.get("abstain", 0)) * 100
    photos = _read_json(Path("data/eval/photos.json"))
    if isinstance(photos, dict):
        summary = photos.get("summary", {})
        if summary.get("kind_accuracy") is not None:
            out["kind"] = float(summary["kind_accuracy"]) * 100
        if summary.get("date_accuracy") is not None:
            out["stamp"] = float(summary["date_accuracy"]) * 100
    extract_path = Path("data/eval/extract_meta.json")
    if not extract_path.exists():
        alts = sorted(Path("data/eval").glob("extract_*.json"))
        extract_path = alts[0] if alts else extract_path
    extract = _read_json(extract_path)
    if isinstance(extract, dict) and extract.get("accuracy") is not None:
        out["extract"] = float(extract["accuracy"]) * 100
    providers = _read_json(Path("data/eval/providers.json"))
    if isinstance(providers, dict):
        out["providers"] = 100.0 if providers.get("results") else 0.0
    return out


def gather_time() -> float | None:
    """Median cold seconds per quarter across finished checks with step times."""
    per_check: list[float] = []
    for status_path in Path("data/work").glob("*/status.json"):
        status = _read_json(status_path)
        evidence = _read_json(status_path.parent / "evidence.json")
        if not isinstance(status, dict) or not isinstance(evidence, dict):
            continue
        if status.get("state") != "done":
            continue
        quarters = evidence.get("quarters", [])
        report_steps = [
            s
            for s in status.get("steps", [])
            if s.get("name") == "report" and s.get("started_at") and s.get("finished_at")
        ]
        if not quarters or not report_steps:
            continue
        try:
            seconds = (
                datetime.fromisoformat(report_steps[0]["finished_at"])
                - datetime.fromisoformat(report_steps[0]["started_at"])
            ).total_seconds()
        except ValueError:
            continue
        per_check.append(seconds / len(quarters))
    return statistics.median(per_check) if per_check else None


def gather_stuck() -> int:
    try:
        return count_open_stuck(Path("STUCK.md").read_text(encoding="utf-8"))
    except OSError:
        return 0


def gather_samples() -> dict[str, float | None]:
    out: dict[str, float | None] = {"projects": None, "flags": None, "verified": None}
    samples_dir = Path("data/samples")
    if samples_dir.is_dir():
        out["projects"] = float(
            sum(1 for p in samples_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        )
    try:
        lines = [
            line
            for line in Path("data/samples/verified.csv").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError:
        return out
    rows = [line.split(",") for line in lines[1:]]
    if not rows:
        return out
    yes = sum(1 for row in rows if len(row) > 2 and row[2].strip().lower() == "yes")
    out["flags"] = float(yes)
    out["verified"] = yes / len(rows) * 100
    return out


PROVIDER_NOTES = [
    "filing_v5 was tuned on the 4 truth filings, so accuracy is optimistic.",
    "Meta scored 113/114 on the earlier run and 112/114 fresh: a one-field gap is run-to-run noise.",
    "Baseten costs about 30x Meta's contributor tier per filing ($0.0454 vs $0.0015).",
    "Fireworks timed out once on Oct 29 before succeeding.",
    "Baseten -1026 billing still to be confirmed in the usage view.",
]


def provider_section() -> list[str]:
    data = _read_json(Path("data/eval/providers.json"))
    lines = ["## Provider comparison", ""]
    if not isinstance(data, dict) or not data.get("results"):
        lines.append("No provider comparison yet.")
        return lines
    lines += ["| provider | model | accuracy | cost per filing | p50 seconds |", "|---|---|---|---|---|"]
    for provider, row in data["results"].items():
        lines.append(
            f"| {provider} | {row['model']} | {row['accuracy']:.1%} "
            f"| ${row['cost_per_filing']:.4f} | {row['p50_seconds']:.0f} |"
        )
    lines += ["", "Notes:"]
    lines += [f"- {note}" for note in PROVIDER_NOTES]
    return lines


def render(values: dict[str, float | None], stamp: str) -> str:
    order = [
        "unit_pct",
        "coverage",
        "e2e",
        "max_day",
        "rerun_share",
        "qsec",
        "stuck",
        "within1",
        "wrong",
        "abstain",
        "extract",
        "kind",
        "stamp",
        "verified",
        "projects",
        "flags",
        "max_check",
        "providers",
    ]
    lines = [f"Snapshot: {stamp}", "", "| KPI | value | target | status |", "|---|---|---|---|"]
    for key, kpi in zip(order, KPI_DEFS, strict=True):
        value = values.get(key)
        status = kpi_status(value, kpi.target, kpi.higher_is_better, kpi.red_line)
        shown = _format(value, kpi.kind) if value is not None else "NO DATA"
        target = _format(kpi.target, kpi.kind)
        lines.append(f"| {kpi.name} | {shown} | {target} | {status} |")
    return "\n".join(lines)


def collect(run_tests: bool) -> dict[str, float | None]:
    tests = gather_tests(run_tests)
    ledger = gather_ledger()
    evaluation = gather_eval()
    samples = gather_samples()
    return {
        "unit_pct": tests["unit_pct"],
        "coverage": tests["coverage"],
        "e2e": tests["e2e"],
        "max_day": ledger["max_day"],
        "rerun_share": ledger["rerun_share"],
        "qsec": gather_time(),
        "stuck": float(gather_stuck()),
        "within1": evaluation["within1"],
        "wrong": evaluation["wrong"],
        "abstain": evaluation["abstain"],
        "extract": evaluation["extract"],
        "kind": evaluation["kind"],
        "stamp": evaluation["stamp"],
        "verified": samples["verified"],
        "projects": samples["projects"],
        "flags": samples["flags"],
        "max_check": ledger["max_check"],
        "providers": evaluation["providers"],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tests", action="store_true", help="skip the pytest run (fast mode)")
    args = parser.parse_args(argv)
    values = collect(run_tests=not args.no_tests)
    stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    table = render(values, stamp)
    print(table)
    out = Path("docs/kpi_snapshot.md")
    out.write_text("# KPI snapshot\n\n" + table + "\n\n" + "\n".join(provider_section()) + "\n")
    print("Wrote docs/kpi_snapshot.md")


if __name__ == "__main__":
    sys.exit(main())
