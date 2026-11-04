"""Live golden check for Project E (Jun 2023, Sept 2023, Jun 2024, Sept 2024, Dec 2024, Mar 2025).

Reads the six portal PDFs with the real filing reader three times each,
cache disabled via STACKCHECK_FRESH=1, takes a per-value majority (match in
at least 2 of 3 runs), checks every golden activity percent and money value,
then runs the R4 and R7 rules on the voted filings and pins the brief end
state: R4 gives exactly the Block C 45-to-40 flag, and R7 gives exactly one
low flag with six distinct page refs.

Skipped unless STACKCHECK_LIVE=1. Every missing input (golden file, PDFs,
API key, cache-disable mechanism) produces a clear pytest.skip naming it.
"""

import asyncio
import inspect
import json
import os
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from stackcheck.config import get_settings
from stackcheck.errors import IngestError, ModelError
from stackcheck.extract.filing import extract_filing, normalize_activity_name
from stackcheck.ingest.pdf import ingest_pdf
from stackcheck.ingest.sources import register_upload
from stackcheck.llm.registry import reader_client
from stackcheck.models import ActivityProgress, ProjectEvidence, QuarterEvidence, QuarterFiling, SourceDoc
from stackcheck.rules.checks import (
    RuleSettings,
    _activity_base_name,
    _fin_inr,
    _normalize_block_label,
    r4_claim_regression,
    r7_arithmetic,
)

pytestmark = pytest.mark.live

if os.environ.get("STACKCHECK_LIVE") != "1":
    pytest.skip(
        "needs STACKCHECK_LIVE=1 for the paid Project E golden read",
        allow_module_level=True,
    )

DOC_IDS = (
    "94a422c0bb9abb02",
    "25145d09ce81fd15",
    "93aed199f53910be",
    "482a102bcd80a2ec",
    "fe8bfd11b4f01f85",
    "bba799b1121be64c",
)
MONEY_FIELDS = (
    "received_this_quarter",
    "spent_this_quarter",
    "net_balance",
    "cumulative_received",
    "cumulative_spent",
)
EXPECTED_R4_REFS = {("25145d09ce81fd15", 7), ("93aed199f53910be", 7)}
NULL_WORDS = {"", "-", "n/a", "na", "nil", "not applicable", "not started", "not reported", "none"}

_SNO_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_pdf(doc_id: str) -> Path | None:
    """First hit wins: data/golden/pdfs/, data/work/sources/, data/work/*/sources/."""
    root = _repo_root()
    candidates = [
        root / "data" / "golden" / "pdfs" / f"{doc_id}.pdf",
        root / "data" / "work" / "sources" / f"{doc_id}.pdf",
    ]
    candidates.extend(sorted((root / "data" / "work").glob(f"*/sources/{doc_id}.pdf")))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_golden() -> dict[str, Any]:
    """Golden answer key, or skip naming the missing file; fail on shape drift."""
    path = _repo_root() / "data" / "golden" / "project_e_golden.json"
    if not path.is_file():
        pytest.skip(f"no golden file: {path} was never delivered")
    try:
        golden = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        pytest.fail(f"golden file {path} does not parse: {exc}")
    if not isinstance(golden, dict):
        pytest.fail(f"golden shape differs; top-level is {type(golden).__name__}")
    for key in ("docs", "money_page_11_inr", "activities", "expected_r4", "expected_r7"):
        if key not in golden:
            pytest.fail(f"golden shape differs; missing {key!r}, top-level keys: {sorted(golden)}")
    return golden


def _sno(name: str) -> str | None:
    match = _SNO_RE.match(name or "")
    return match.group(1).rstrip(".") if match else None


def _stem(word: str) -> str:
    """Drop one plural s so print singular/plural drift still matches."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


_GLUE_WORDS = frozenset({"and", "the", "with", "for", "from", "into", "per", "each", "all"})


def _words(text: str) -> list[str]:
    """Lowercase alphanumeric words, dropping glue words (print uses commas)."""
    return [
        _stem(word)
        for word in _WORD_RE.findall((text or "").lower())
        if len(word) > 2 and word not in _GLUE_WORDS
    ]


def _is_subsequence(short: list[str], long: list[str]) -> bool:
    """Every word of short appears in long in the same order."""
    rest = iter(long)
    return all(word in rest for word in short)


def _match_golden_row(filing: QuarterFiling, row: dict[str, Any]) -> ActivityProgress | None:
    """Find the voted row for one golden activity.

    Primary key is the printed S.NO within the (page, block) table. Tables
    with split rows shift printed numbers: Dec 2024's Wing-C table prints
    "7.1"/"7.2" so Staircases prints as 13 where the same table in Mar 2025
    prints 14. When no S.NO matches, fall back to the golden label's words
    in order against the S.NO-stripped row name. A printed S.NO only counts
    when the row name also carries the golden label's words, so a shifted
    number on an unrelated row never wins over the true row. Returns None
    when nothing or more than one row matches, so the caller fails loudly
    either way.
    """
    want_block = row["block"] or ""
    same_page_block = [
        activity
        for activity in filing.activities
        if activity.ref is not None
        and activity.ref.page == row["page"]
        and (_normalize_block_label(activity.block_label) or "") == want_block
    ]
    label_words = _words(str(row.get("label", "")))
    if not label_words:
        return None
    want_sno = str(row["item"])
    for activity in same_page_block:
        if _sno(activity.name) == want_sno and _is_subsequence(
            label_words, _words(_SNO_RE.sub("", activity.name or ""))
        ):
            return activity
    candidates = [
        activity
        for activity in same_page_block
        if _is_subsequence(label_words, _words(_SNO_RE.sub("", activity.name or "")))
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _golden_percent(value: Any) -> float | None:
    """Golden activity value to float, or None for status words."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if text in NULL_WORDS:
        return None
    try:
        return float(text)
    except ValueError:
        return None


async def _read_once(pdf_path: Path, data_dir: Path) -> QuarterFiling:
    """One real reader pass over a stored PDF."""
    settings = get_settings().model_copy(update={"data_dir": data_dir})
    work = data_dir / "work"
    data = await asyncio.to_thread(pdf_path.read_bytes)
    source = register_upload(data, pdf_path.name, work)
    stored = work / "sources" / f"{source.doc_id}.pdf"
    ingested = ingest_pdf(stored, source, work / source.doc_id)
    client = reader_client(settings, "url")
    return await extract_filing(ingested, client, check_id=None, pdf_path=stored)


def _activity_key(filing: QuarterFiling, activity: ActivityProgress) -> tuple | None:
    """(page, block key, base name) for matching across runs; None when unkeyable.

    Keyed by base name, not the printed S.NO: the reader invents dotted
    sub-numbers for split rows (Dec 2024 prints Stilt Floor as 7.1/7.2, so
    Staircases prints as 13 where Mar 2025 prints 14), which shifts every
    later number and splits one row's votes across two keys. Production R4
    keys rows the same way, by base name.
    """
    if activity.ref is None:
        return None
    block = _normalize_block_label(activity.block_label)
    base = _activity_base_name(activity.name or "")
    if block is None or not base:
        return None
    return (activity.ref.page, block, base)


def _vote(values: list, key: Any) -> Any:
    """Two of three runs must read the same value; otherwise the row goes inert.

    A None majority (or no majority at all) votes None so single-run noise
    can never flag. Golden rows that end up None fail loudly downstream in
    _check_golden_values, which names the golden expectation.
    """
    top, count = Counter(values).most_common(1)[0]
    if count < 2:
        return None
    return top


def _voted_filing(filings: list[QuarterFiling]) -> QuarterFiling:
    """Rebuild one filing from per-value majorities, keeping the real page refs.

    Tallies cover the union of row keys; a run missing the row votes None.
    Rows the majority drops (None) stay on the filing with percent None.
    """
    merged = filings[0].model_copy(deep=True)
    tallies: dict[tuple, list] = {}
    for filing in filings:
        present = set()
        for activity in filing.activities:
            key = _activity_key(filing, activity)
            if key is None or key in present:
                continue
            present.add(key)
            tallies.setdefault(key, []).append(activity.percent_done)
        for key in tallies:
            if key not in present:
                tallies[key].append(None)
    for key in tallies:
        while len(tallies[key]) < len(filings):
            tallies[key].append(None)
    voted = {key: _vote(values, key) for key, values in tallies.items()}
    for activity in merged.activities:
        key = _activity_key(merged, activity)
        if key is not None and key in voted:
            activity.percent_done = voted[key]
    seen = {_activity_key(merged, activity) for activity in merged.activities}
    for key, value in voted.items():
        if value is None or key in seen:
            continue
        source = next(
            (
                activity
                for filing in filings
                for activity in filing.activities
                if _activity_key(filing, activity) == key
            ),
            None,
        )
        if source is not None:
            clone = source.model_copy(deep=True)
            clone.percent_done = value
            merged.activities.append(clone)
    for field in (*MONEY_FIELDS, "estimated_total_cost"):
        inr_values = [_fin_inr(filing.financials, field) for filing in filings]
        value = _vote(inr_values, ("money", field))
        if value is None:
            merged.financials.currency_unit = merged.financials.currency_unit or "INR"
            setattr(merged.financials, field, None)
        else:
            merged.financials.currency_unit = "INR"
            merged.financials.unit_overrides = {}
            setattr(merged.financials, field, value)
    return merged


def _check_golden_values(golden: dict[str, Any], voted: dict[str, QuarterFiling]) -> int:
    """Every golden activity percent and money value must match the voted filing."""
    checked = 0
    money = golden["money_page_11_inr"]
    for doc_key, docs_entry in golden["docs"].items():
        doc_id = docs_entry["doc_id"]
        filing = voted[doc_id]
        if filing.quarter_end is None:
            pytest.fail(f"{doc_id}: reader left quarter_end null")
        for row in golden["activities"]:
            if row["doc"] != doc_key:
                continue
            if row.get("known_gap"):
                print(f"known gap (excluded from vote): {doc_id} {row['block']} item {row['item']}")
                continue
            want = _golden_percent(row["value"])
            match = _match_golden_row(filing, row)
            label = f"{doc_id} p{row['page']} {row['block']} item {row['item']} ({row['label']})"
            if match is None:
                have = sorted(
                    f"p{a.ref.page} {a.block_label} {_sno(a.name)} {normalize_activity_name(a.name)}"
                    for a in filing.activities
                    if a.ref is not None
                )
                pytest.fail(f"{label}: no voted read on that row; voted rows: {have}")
            if match.percent_done != want:
                pytest.fail(f"{label}: voted {match.percent_done}, golden {row['value']!r}")
            checked += 1
        for field in MONEY_FIELDS:
            got = _fin_inr(filing.financials, field)
            if got != Decimal(money[doc_key][field]):
                pytest.fail(f"{doc_id}: money {field} voted {got}, golden {money[doc_key][field]}")
            checked += 1
        lakh = Decimal(money[doc_key]["estimated_total_cost_lakh"]) * Decimal(100000)
        if _fin_inr(filing.financials, "estimated_total_cost") != lakh:
            pytest.fail(
                f"{doc_id}: estimated voted {_fin_inr(filing.financials, 'estimated_total_cost')}, "
                f"golden {lakh} (lakh {money[doc_key]['estimated_total_cost_lakh']})"
            )
        checked += 1
    return checked


def _project_from_voted(voted: dict[str, QuarterFiling]) -> ProjectEvidence:
    """ProjectEvidence around the voted filings, oldest quarter first."""
    quarters: list[QuarterEvidence] = []
    for doc_id in DOC_IDS:
        filing = voted[doc_id]
        pages = [
            ref.page
            for ref in (
                [b.ref for b in filing.blocks] + [a.ref for a in filing.activities] + [filing.financials.ref]
            )
            if ref is not None
        ]
        source = SourceDoc(
            doc_id=filing.doc_id,
            origin="upload",
            filename=f"{doc_id}.pdf",
            sha256=(doc_id * 4)[:64],
            retrieved_at=datetime(2025, 1, 1),
            page_count=max(pages or [1]),
        )
        quarters.append(QuarterEvidence(source=source, filing=filing, photos=[]))
    quarters.sort(key=lambda quarter: (quarter.filing.quarter_end is None, quarter.filing.quarter_end))
    return ProjectEvidence(project_label="Project E (live golden)", quarters=quarters)


def _check_r4(project: ProjectEvidence) -> list[Any]:
    """Exactly one R4 FLAG: Block C item 14, 45 to 40, Dec p7 and Mar p7."""
    flags = [f for f in r4_claim_regression(project, RuleSettings()) if f.status == "FLAG"]
    if len(flags) != 1:
        detail = [f"{f.block_label} {f.title} {f.explanation}" for f in flags]
        pytest.fail(f"R4: found {len(flags)} FLAGs, the brief needs exactly one: {detail}")
    flag = flags[0]
    refs = {(entry.ref.doc_id, entry.ref.page) for entry in flag.evidence if entry.ref is not None}
    if refs != EXPECTED_R4_REFS:
        pytest.fail(f"R4: flag refs {sorted(refs)} do not match {sorted(EXPECTED_R4_REFS)}")
    if flag.block_label != "C" or "45" not in flag.explanation or "40" not in flag.explanation:
        pytest.fail(f"R4: flag is not the Block C 45-to-40 fall: {flag.explanation!r}")
    return flags


def _check_r7(project: ProjectEvidence) -> list[Any]:
    """Exactly one low R7 net-amount FLAG with six distinct page refs."""
    flags = [
        f for f in r7_arithmetic(project, RuleSettings()) if f.status == "FLAG" and "Net amount" in f.title
    ]
    if len(flags) != 1:
        pytest.fail(f"R7: found {len(flags)} net-amount FLAGs, the brief needs exactly one")
    flag = flags[0]
    if flag.severity != "low":
        pytest.fail(f"R7: net-amount flag severity is {flag.severity}, the brief needs low")
    refs = {(entry.ref.doc_id, entry.ref.page) for entry in flag.evidence if entry.ref is not None}
    if len(refs) != 6:
        pytest.fail(f"R7: net-amount flag has {len(refs)} distinct page refs, needs six: {sorted(refs)}")
    for doc_id, page in (
        ("482a102bcd80a2ec", 10),
        ("fe8bfd11b4f01f85", 10),
        ("bba799b1121be64c", 11),
        ("94a422c0bb9abb02", 11),
        ("25145d09ce81fd15", 11),
        ("93aed199f53910be", 11),
    ):
        if (doc_id, page) not in refs:
            pytest.fail(f"R7: net-amount flag is missing ref {(doc_id, page)}: {sorted(refs)}")
    if "A question to ask the builder or RERA" not in flag.explanation:
        pytest.fail(f"R7: flag is not worded as a question: {flag.explanation!r}")
    return flags


_MUST_NOT_RE = re.compile(r"(?:Block\s+([A-Za-z0-9-]+))?.*?(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)")


def _check_must_not_flag(golden: dict[str, Any], flags: list[Any]) -> None:
    """No flag may match a golden must_not_flag entry."""
    r4_flags = [f for f in flags if f.rule == "R4_CLAIM_REGRESSION"]
    for entry in golden["expected_r4"]["must_not_flag"]:
        match = _MUST_NOT_RE.search(str(entry))
        if match is None:
            pytest.fail(f"must_not_flag entry has no from/to numbers: {entry!r}")
        block, old, new = match.group(1), match.group(2), match.group(3)
        matched = [
            flag
            for flag in r4_flags
            if (block is None or flag.block_label == block)
            and old in flag.explanation
            and new in flag.explanation
        ]
        if matched:
            pytest.fail(f"flagged despite golden must_not_flag {entry!r}: {matched[0].title!r}")


async def test_project_e_golden_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three real reads per PDF, majority vote, golden compare, R4/R7 end state."""
    golden = _load_golden()
    pdfs = {doc_id: _resolve_pdf(doc_id) for doc_id in DOC_IDS}
    missing = sorted(doc_id for doc_id, pdf in pdfs.items() if pdf is None)
    if missing:
        pytest.skip(f"no Project E PDF for doc ids: {', '.join(missing)}")
    settings = get_settings()
    keys = {
        "meta": settings.meta_api_key,
        "baseten": settings.baseten_api_key,
        "fireworks": settings.fireworks_api_key,
    }
    if not keys[settings.extract_provider]:
        pytest.skip(f"no API key for provider {settings.extract_provider!r}")
    import stackcheck.cache as cache_module

    if "STACKCHECK_FRESH" not in inspect.getsource(cache_module.get_or_compute):
        pytest.skip("cache-disable mechanism absent: STACKCHECK_FRESH is not honored by the cache")
    monkeypatch.setenv("STACKCHECK_FRESH", "1")
    voted: dict[str, QuarterFiling] = {}
    for doc_id in DOC_IDS:
        filings = []
        for _run in range(3):
            for attempt in range(3):
                try:
                    filing = await _read_once(pdfs[doc_id], tmp_path / "live-golden")
                    break
                except (ModelError, IngestError):
                    # extract_filing wraps transient ModelErrors in IngestError,
                    # so the read retry must catch both.
                    if attempt == 2:
                        raise
            filings.append(filing)
            if filing.doc_id != doc_id:
                pytest.fail(f"PDF {pdfs[doc_id]} reads as {filing.doc_id}, not {doc_id}")
        voted[doc_id] = _voted_filing(filings)
    rows = _check_golden_values(golden, voted)
    project = _project_from_voted(voted)
    flags = _check_r4(project) + _check_r7(project)
    _check_must_not_flag(golden, flags)
    print(f"\ngolden rows matched: {rows}")
