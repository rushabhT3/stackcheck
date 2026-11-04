"""Compare filing-reader providers on the same truth filings.

For each of meta, baseten, fireworks (skipped without a key): run
`extract_filing` on every truth filing, score with `eval_extract.score`,
collect ledger cost and wall time per filing. Errors record the provider
as FAILED and the table still prints for the rest. Prints a markdown table
and the default provider by the fixed tolerance rule. `--fresh` skips cache
reads so every call is real. Live: STACKCHECK_LIVE=1.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import TypedDict

from eval_extract import HELD_OUT_DOC_IDS, _find_pdf, score

from stackcheck.config import Settings, get_settings
from stackcheck.costs import total_usd
from stackcheck.extract.filing import extract_filing
from stackcheck.ingest.pdf import ingest_pdf
from stackcheck.ingest.sources import register_upload
from stackcheck.llm.registry import reader_client
from stackcheck.models import QuarterFiling

_PROVIDERS = ("meta", "baseten", "fireworks")
_TOLERANCE = 0.02


class ProviderResult(TypedDict):
    model: str
    accuracy: float
    cost_per_filing: float
    p50_seconds: float
    cache_used: bool


def _refusal(results: dict[str, ProviderResult]) -> str | None:
    """Why the rule must not choose, or None when the numbers are real."""
    if not results:
        return "no successful provider runs"
    cached = sorted(p for p, row in results.items() if row.get("cache_used"))
    if cached:
        return f"cost/p50 for {', '.join(cached)} came from cache; re-run with --fresh"
    return None


def choose_default(results: dict[str, ProviderResult]) -> str:
    """Fixed tolerance rule: open model within 2 points of meta wins if cheapest."""
    baseline = results.get("meta")
    opens = [p for p in ("baseten", "fireworks") if p in results]
    if baseline is None:
        if not opens:
            return "meta"
        return min(opens, key=lambda p: (results[p]["cost_per_filing"], results[p]["p50_seconds"]))
    candidates = [p for p in opens if results[p]["accuracy"] >= baseline["accuracy"] - _TOLERANCE]
    if not candidates:
        return "meta"
    return min(candidates, key=lambda p: (results[p]["cost_per_filing"], results[p]["p50_seconds"]))


def _api_key(settings: Settings, provider: str) -> str:
    return {
        "meta": settings.meta_api_key,
        "baseten": settings.baseten_api_key,
        "fireworks": settings.fireworks_api_key,
    }[provider]


def _model_name(settings: Settings, provider: str) -> str:
    return {
        "meta": settings.reader_model,
        "baseten": settings.baseten_model,
        "fireworks": settings.fireworks_model,
    }[provider]


def _truth_files() -> list[Path]:
    return sorted(p for p in Path("data/truth").glob("*.json") if not p.name.endswith(".template.json"))


def _ledger_lines(data_dir: Path) -> int:
    ledger = data_dir / "ledger.jsonl"
    if not ledger.exists():
        return 0
    return sum(1 for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())


def _ledger_cache_used(data_dir: Path, start: int, provider: str) -> bool:
    ledger = data_dir / "ledger.jsonl"
    if not ledger.exists():
        return False
    lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    return any(
        json.loads(line).get("provider") == provider and json.loads(line).get("cache_hit")
        for line in lines[start:]
    )


async def _run_provider(provider: str, truth_files: list[Path]) -> ProviderResult | None:
    base = get_settings()
    if not _api_key(base, provider):
        print(f"{provider}: SKIPPED (no key)")
        return None
    settings = base.model_copy(update={"extract_provider": provider})
    ledger_start = _ledger_lines(settings.data_dir)
    correct, total = 0, 0
    costs: list[float] = []
    seconds: list[float] = []
    first = True
    for truth_path in truth_files:
        truth = QuarterFiling.model_validate_json(truth_path.read_text(encoding="utf-8"))
        pdf_path = _find_pdf(truth.doc_id)
        data = pdf_path.read_bytes()
        source = register_upload(data, pdf_path.name, Path("data/work"))
        stored = Path("data/work/sources") / f"{source.doc_id}.pdf"
        ingested = ingest_pdf(stored, source, Path("data/work") / source.doc_id)
        client = reader_client(settings, "url")
        spent_before = total_usd(settings.data_dir)
        started = time.perf_counter()
        try:
            filing = await extract_filing(ingested, client, check_id=None, pdf_path=stored)
        except Exception as exc:
            if provider == "fireworks" and first and ("404" in str(exc) or "connect" in str(exc).lower()):
                print(f"fireworks: {exc}")
                print("Check the Fireworks quickstart and set FIREWORKS_BASE_URL.")
            raise
        first = False
        elapsed = time.perf_counter() - started
        cost = total_usd(settings.data_dir) - spent_before
        result = score(truth, filing)
        print(f"{provider} {truth_path.name}: {result['correct']}/{result['total']}")
        if truth.doc_id in HELD_OUT_DOC_IDS:
            print(f"{provider} held-out {truth_path.name}: {result['correct']}/{result['total']}")
            continue
        correct += result["correct"]
        total += result["total"]
        costs.append(float(cost))
        seconds.append(elapsed)
    return {
        "model": _model_name(base, provider),
        "accuracy": correct / total if total else 0.0,
        "cost_per_filing": sum(costs) / len(costs) if costs else 0.0,
        "p50_seconds": statistics.median(seconds) if seconds else 0.0,
        "cache_used": _ledger_cache_used(settings.data_dir, ledger_start, provider),
    }


async def _amain(truth_files: list[Path]) -> tuple[dict[str, ProviderResult], dict[str, str]]:
    results: dict[str, ProviderResult] = {}
    failures: dict[str, str] = {}
    for provider in _PROVIDERS:
        try:
            outcome = await _run_provider(provider, truth_files)
        except Exception as exc:
            print(f"{provider}: FAILED ({exc})")
            failures[provider] = str(exc)
            continue
        if outcome is not None:
            results[provider] = outcome
    return results, failures


def main(argv: list[str] | None = None) -> None:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this live comparison.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="skip cache reads so every call is real")
    args = parser.parse_args(argv)
    if args.fresh:
        os.environ["STACKCHECK_FRESH"] = "1"
    truth_files = _truth_files()
    if not truth_files:
        raise SystemExit("No truth files in data/truth/")
    results, failures = asyncio.run(_amain(truth_files))
    out = Path("data/eval/providers.json")
    out.write_text(json.dumps({"results": results, "failures": failures}, indent=2), encoding="utf-8")
    print("Wrote data/eval/providers.json")
    print("| provider | model | accuracy | cost per filing | p50 seconds |")
    print("|---|---|---|---|---|")
    for provider, row in results.items():
        print(
            f"| {provider} | {row['model']} | {row['accuracy']:.1%} "
            f"| ${row['cost_per_filing']:.4f} | {row['p50_seconds']:.0f} |"
        )
    for provider, error in failures.items():
        print(f"| {provider} | FAILED | {error} | | |")
    print("Note: filing_v5 was tuned on these truth filings (T05c); treat gaps as optimistic.")
    print("Note: Fireworks timed out once on Oct 29 before succeeding.")
    refusal = _refusal(results)
    if refusal is not None:
        print(f"Refusing to choose: {refusal}.")
        return
    chosen = choose_default(results)
    print(f"Default provider: {chosen}")
    print(f"Put this in .env and the Modal secret: STACKCHECK_EXTRACT_PROVIDER={chosen}")


if __name__ == "__main__":
    sys.exit(main())
