"""Measure filing extraction accuracy against hand-labelled truth.

Template mode: `eval_extract.py --make-template <pdf path or doc_id>` writes
`data/truth/<doc_id>.template.json` for a human to correct and rename.
Eval mode (default): extracts every `data/truth/*.json` filing and scores it.
"""

import argparse
import json
import os
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

from stackcheck.config import get_settings
from stackcheck.costs import total_usd
from stackcheck.extract.filing import extract_filing, normalize_activity_name
from stackcheck.ingest.pdf import ingest_pdf
from stackcheck.ingest.sources import register_upload
from stackcheck.llm.registry import reader_client
from stackcheck.models import QuarterFiling

FINANCIAL_FIELDS = (
    "received_this_quarter",
    "spent_this_quarter",
    "cumulative_received",
    "cumulative_spent",
    "estimated_total_cost",
)


def _money_eq(first: object, second: object) -> bool:
    """Compare money through str() so float noise in Decimals never fails a match."""
    if first is None or second is None:
        return first is None and second is None
    return Decimal(str(first)) == Decimal(str(second))


def _activity_key(name: str) -> str | None:
    """Leading row key like 9.2 in "9.2 Lifts Wells", else None.

    Accepts a trailing period ("1. Excavation") and multi-level keys ("7.2.1").
    """
    match = re.match(r"(\d+(?:\.\d+)*)\.?\s+", name.strip())
    return match.group(1) if match else None


def _activities_match(truth_name: str, extracted_name: str) -> bool:
    truth_key, extracted_key = _activity_key(truth_name), _activity_key(extracted_name)
    if truth_key is not None and extracted_key is not None:
        return truth_key == extracted_key
    return normalize_activity_name(truth_name) == normalize_activity_name(extracted_name)


def score(truth: QuarterFiling, extracted: QuarterFiling) -> dict:
    """Score the labelled fields. Truth null matched by extracted null counts as correct."""
    fields: dict[str, bool] = {}
    left_null: list[str] = []
    wrong_value: list[str] = []

    def record(name: str, ok: bool, extracted_value: object) -> None:
        fields[name] = ok
        if not ok:
            (left_null if extracted_value is None else wrong_value).append(name)

    if truth.project_reg_no is not None:
        record(
            "project_reg_no",
            extracted.project_reg_no == truth.project_reg_no,
            extracted.project_reg_no,
        )
    record("quarter_end", extracted.quarter_end == truth.quarter_end, extracted.quarter_end)
    for block in truth.blocks:
        match = next((b for b in extracted.blocks if b.block_label == block.block_label), None)
        for field in ("floors_above_ground_sanctioned", "slabs_claimed_count", "slabs_claimed_percent"):
            name = f"blocks[{block.block_label}].{field}"
            value = getattr(match, field) if match is not None else None
            record(name, match is not None and value == getattr(block, field), value)
    for field in FINANCIAL_FIELDS:
        value = getattr(extracted.financials, field)
        record(f"financials.{field}", _money_eq(getattr(truth.financials, field), value), value)
    record(
        "financials.currency_unit",
        extracted.financials.currency_unit == truth.financials.currency_unit,
        extracted.financials.currency_unit,
    )
    for flag in (
        "has_architect_certificate",
        "has_engineer_certificate",
        "has_ca_certificate",
        "photo_section_present",
    ):
        record(flag, getattr(extracted, flag) == getattr(truth, flag), getattr(extracted, flag))
    for activity in truth.activities:
        key = normalize_activity_name(activity.name)
        name = f"activities[{key}].percent_done"
        match = next((a for a in extracted.activities if _activities_match(activity.name, a.name)), None)
        value = match.percent_done if match is not None else None
        record(name, match is not None and value == activity.percent_done, value)
    correct = sum(fields.values())
    return {
        "fields": fields,
        "correct": correct,
        "total": len(fields),
        "left_null": left_null,
        "wrong_value": wrong_value,
    }


def _find_pdf(doc_id: str) -> Path:
    found = sorted(Path("data/work").glob(f"**/sources/{doc_id}.pdf"))
    if found:
        return found[0]
    fallback = Path("data/uploads") / f"{doc_id}.pdf"
    if fallback.exists():
        return fallback
    raise SystemExit(f"No PDF for {doc_id} in data/work or data/uploads")


def _load_filing(pdf_path: Path, provider: str | None):
    settings = get_settings()
    if provider:
        settings = settings.model_copy(update={"extract_provider": provider})
    data = pdf_path.read_bytes()
    source = register_upload(data, pdf_path.name, Path("data/work"))
    stored = Path("data/work/sources") / f"{source.doc_id}.pdf"
    ingested = ingest_pdf(stored, source, Path("data/work") / source.doc_id)
    return ingested, stored, settings


async def _extract(ingested, stored: Path, settings, check_id: str | None) -> QuarterFiling:
    client = reader_client(settings, "url")
    return await extract_filing(ingested, client, check_id=check_id, pdf_path=stored)


def _make_template(src: str, provider: str | None) -> None:
    import asyncio

    path = Path(src)
    pdf_path = path if path.exists() else _find_pdf(src)
    ingested, stored, settings = _load_filing(pdf_path, provider)
    filing = asyncio.run(_extract(ingested, stored, settings, None))
    filing.project_name = None
    filing.promoter_name = None
    filing.project_reg_no = None
    out = Path("data/truth") / f"{ingested.source.doc_id}.template.json"
    out.write_text(filing.model_dump_json(indent=2), encoding="utf-8")
    print(f"Wrote {out}. Correct every field by hand, set names to null, rename to <doc_id>.json.")


HELD_OUT_DOC_IDS = {"a85d8537d40d930a"}  # labelled only after the prompt freeze


def _evaluate(provider: str | None) -> None:
    import asyncio

    settings = get_settings()
    if provider:
        settings = settings.model_copy(update={"extract_provider": provider})
    truth_files = sorted(
        p for p in Path("data/truth").glob("*.json") if not p.name.endswith(".template.json")
    )
    if not truth_files:
        raise SystemExit("No truth files in data/truth/")
    used_provider = provider or settings.extract_provider
    per_file: dict[str, dict] = {}
    correct, total = 0, 0
    miss_null: list[str] = []
    miss_wrong: list[str] = []
    held_out: dict[str, dict] = {}
    started = time.perf_counter()
    spent_before = total_usd(settings.data_dir)
    for truth_path in truth_files:
        truth = QuarterFiling.model_validate_json(truth_path.read_text(encoding="utf-8"))
        pdf_path = _find_pdf(truth.doc_id)
        data = pdf_path.read_bytes()
        source = register_upload(data, pdf_path.name, Path("data/work"))
        stored = Path("data/work/sources") / f"{source.doc_id}.pdf"
        ingested = ingest_pdf(stored, source, Path("data/work") / source.doc_id)
        filing = asyncio.run(_extract(ingested, stored, settings, None))
        result = score(truth, filing)
        per_file[truth_path.name] = result
        print(f"{truth_path.name}: {result['correct']}/{result['total']}")
        if truth.doc_id in HELD_OUT_DOC_IDS:
            held_out[truth_path.name] = result
            continue
        correct += result["correct"]
        total += result["total"]
        miss_null.extend(f"{truth_path.name}:{name}" for name in result["left_null"])
        miss_wrong.extend(f"{truth_path.name}:{name}" for name in result["wrong_value"])
    seconds = time.perf_counter() - started
    cost = total_usd(settings.data_dir) - spent_before
    accuracy = correct / total if total else 0.0
    print(f"\nMisses left null ({len(miss_null)}):")
    for name in miss_null:
        print(f"  {name}")
    print(f"Misses wrong value ({len(miss_wrong)}):")
    for name in miss_wrong:
        print(f"  {name}")
    print(f"\nOverall (excluding held-out): {correct}/{total} = {accuracy:.1%}", end="")
    print(f" in {seconds:.0f}s, ledger cost ${cost}")
    for name, result in held_out.items():
        print(f"Held-out {name}: {result['correct']}/{result['total']}")
    out = Path("data/eval") / f"extract_{used_provider}.json"
    out.write_text(
        json.dumps(
            {
                "accuracy": accuracy,
                "correct": correct,
                "total": total,
                "left_null": miss_null,
                "wrong_value": miss_wrong,
                "held_out": held_out,
                "cost_usd": str(cost),
                "seconds": round(seconds),
                "files": per_file,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {out}")


def main(argv: list[str] | None = None) -> None:
    if os.environ.get("STACKCHECK_LIVE") != "1":
        raise SystemExit("Refuses to run: set STACKCHECK_LIVE=1 for this paid eval.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--make-template", default=None, help="PDF path or doc_id")
    parser.add_argument("--provider", default=None, help="meta, baseten or fireworks")
    args = parser.parse_args(argv)
    if args.make_template:
        _make_template(args.make_template, args.provider)
    else:
        _evaluate(args.provider)


if __name__ == "__main__":
    sys.exit(main())
