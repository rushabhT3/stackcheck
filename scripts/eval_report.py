"""Aggregate the four Section C eval numbers into data/eval.json.

Reads only local files, spends nothing, and needs no STACKCHECK_LIVE flag:

(a) floor benchmark: reference counts in data/bench/labels.csv joined on file name
    with the recorded predictions in data/eval/bench_floors.json ("results" rows);
(b) extraction accuracy: reference counts in data/labels/<doc_id>.json against the
    extracted filings in data/eval/extracted/<doc_id>.json;
(c) flag precision per rule from data/review.csv
    (columns: project_label, rule, reviewer_verdict, quarter_end);
(d) backtest lead from data/backtest.csv
    (columns: project_label, action_date, action_type, order_url) joined with
    the earliest flag quarter per project from the review rows.

Hand-label schema for (b): {"doc_id", "quarter_end", "filed_on",
"slabs": {block: count}, "cumulative_received", "cumulative_spent",
"estimated_cost"}. Extracted files may use that same schema or a
QuarterFiling dump (quarter_end/filed_on at top level, slabs under
blocks[].slabs_claimed_count keyed by block_label, money under
financials.cumulative_received / cumulative_spent / estimated_total_cost).

Every reported number carries its n and a one-line method note. Any section
whose inputs are missing reports "not measured yet", never a zero.
"""

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stackcheck.vision.floors import ESTIMATE_TOLERANCE, MIN_ROWS  # noqa: E402

NOT_MEASURED = "not measured yet"
MEASURED = "measured"

EXTRACTION_FIELDS = (
    "quarter_end",
    "filed_on",
    "slabs",
    "cumulative_received",
    "cumulative_spent",
    "estimated_cost",
)


def _metric(value: float | int | None, n: int, method: str) -> dict:
    """One reported number with its sample size and method note."""
    return {"value": value, "n": n, "method": method}


def _not_measured(reason: str) -> dict:
    """Section envelope used when inputs are missing. Holds no numbers."""
    return {"status": NOT_MEASURED, "reason": reason}


def golden_section(golden_path: Path) -> dict:
    """Project E golden check: 18 activity rows and money on 6 real filings.

    Reads data/eval/golden_project_e.json written after a green golden run.
    The one known_gap row is reported, never hidden.
    """
    try:
        payload = json.loads(golden_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _not_measured("Golden Project E check has not finished yet.")
    return {
        "status": MEASURED,
        "activities_matched": payload["activities_matched"],
        "activities_total": payload["activities_total"],
        "money_matched": payload["money_matched"],
        "money_total": payload["money_total"],
        "known_gap": payload["known_gap"],
        "rule_pins": payload["rule_pins"],
        "method": payload["method"],
        "date": payload["date"],
    }


@dataclass
class FloorRow:
    file: str
    label: int
    status: str
    distance: int | None
    within1: bool | None
    tier: str | None = None
    sam_rows: int | None = None
    vision: int | None = None


def _tier_of(row: FloorRow) -> str:
    """F1 tier for one bench row. Legacy rows without a tier re-derive it.

    The ESTIMATE branches of count_floors are pure functions of sam_rows and
    the vision count, so rows recorded before tiers existed classify exactly:
    vision-only (SAM under MIN_ROWS) or methods differing by exactly 2.
    """
    if row.tier == "ESTIMATE":
        return "estimate"
    if row.tier == "CONFIDENT":
        return "confident"
    if row.status == "OK":
        return "confident"
    if row.vision is not None and (
        row.sam_rows is None
        or row.sam_rows < MIN_ROWS
        or abs(row.sam_rows - row.vision) == ESTIMATE_TOLERANCE
    ):
        return "estimate"
    return "cant_tell"


def load_floor_rows(labels_path: Path, bench_path: Path) -> list[FloorRow] | None:
    """Join reference counts with recorded predictions on file name.

    Returns None when either input file is missing or unreadable; rows whose
    label has no matching prediction are dropped and counted in the log.
    """
    if not labels_path.is_file() or not bench_path.is_file():
        return None
    try:
        with labels_path.open(encoding="utf-8") as fh:
            labels = {
                (row.get("file") or "").strip(): (row.get("storeys_visible") or "").strip()
                for row in csv.DictReader(fh)
            }
    except (OSError, csv.Error) as exc:
        logger.warning("Unreadable labels %s: %s", labels_path, exc)
        return None
    try:
        predictions = {
            str(row.get("file")): row
            for row in json.loads(bench_path.read_text(encoding="utf-8")).get("results", [])
        }
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("Unreadable bench results %s: %s", bench_path, exc)
        return None
    joined: list[FloorRow] = []
    dropped = 0
    for file, storeys in labels.items():
        if not file or not storeys:
            continue
        pred = predictions.get(file)
        if pred is None:
            dropped += 1
            continue
        try:
            sam = pred.get("sam_rows")
            vis = pred.get("vision")
            tier = pred.get("tier")
            joined.append(
                FloorRow(
                    file=file,
                    label=int(storeys),
                    status=str(pred.get("status")),
                    distance=None if pred.get("distance") is None else int(pred["distance"]),
                    within1=None if pred.get("within1") is None else bool(pred["within1"]),
                    tier=str(tier) if tier in ("CONFIDENT", "ESTIMATE") else None,
                    sam_rows=int(sam) if isinstance(sam, int) else None,
                    vision=int(vis) if isinstance(vis, int) else None,
                )
            )
        except (ValueError, TypeError):
            dropped += 1
    logger.info(
        "Floor join: %d rows from %s, %d labels without predictions", len(joined), bench_path, dropped
    )
    return joined


AI_REFERENCE_NOTE = (
    "Floor counts were read from each photo by Claude, an AI model used as a second reader. "
    "A person has not checked them yet."
)


def load_human_labels(
    human_path: Path,
) -> tuple[dict[str, int], set[str], set[str], set[str]]:
    """Map bench file to person-counted floors, plus who counted and when.

    Returns empty floors when the file is absent, unreadable, or has no
    counted_by name filled in: the person sentence must never appear then.
    """
    floors: dict[str, int] = {}
    unreadable: set[str] = set()
    by: set[str] = set()
    on: set[str] = set()
    if not human_path.is_file():
        return floors, unreadable, by, on
    try:
        text = human_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.warning("Skipping unreadable %s: %s", human_path, exc)
        return floors, unreadable, by, on
    for row in csv.DictReader(text.splitlines()):
        name = (row.get("image_id") or "").strip()
        if not name:
            continue
        if (row.get("cannot_tell") or "").strip():
            unreadable.add(name)
            continue
        try:
            floors[name] = int((row.get("floors") or "").strip())
        except ValueError:
            continue
        counter = (row.get("counted_by") or "").strip()
        day = (row.get("counted_on") or "").strip()
        if counter:
            by.add(counter)
        if day:
            on.add(day)
    if not by:
        return {}, set(), set(), set()
    return floors, unreadable, by, on


def _load_pred_bounds(bench_path: Path) -> dict[str, tuple[str, int | None, int | None]]:
    """Map bench file to (status, lower, upper) for recomputing against a new reference."""
    try:
        payload = json.loads(bench_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Skipping unreadable %s: %s", bench_path, exc)
        return {}
    bounds: dict[str, tuple[str, int | None, int | None]] = {}
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return bounds
    for item in results:
        if not isinstance(item, dict) or not item.get("file"):
            continue
        lower = item.get("lower")
        upper = item.get("upper")
        bounds[str(item["file"])] = (
            str(item.get("status")),
            int(lower) if isinstance(lower, int) else None,
            int(upper) if isinstance(upper, int) else None,
        )
    return bounds


def _distance_to(human: int, lower: int | None, upper: int | None) -> tuple[int, bool] | None:
    """Distance of a person count to a predicted range; None when unscorable."""
    if lower is None or upper is None:
        return None
    if lower <= human <= upper:
        return 0, True
    gap = min(abs(lower - human), abs(upper - human))
    return gap, gap <= 1


def floor_section(labels_path: Path, bench_path: Path, human_path: Path | None = None) -> dict:
    """Benchmark rates over the joined bench rows. Not measured without inputs."""
    rows = load_floor_rows(labels_path, bench_path)
    human_floors: dict[str, int] = {}
    human_unreadable: set[str] = set()
    by: set[str] = set()
    on: set[str] = set()
    disagreements: dict | None = None
    reference = {"kind": "ai", "note": AI_REFERENCE_NOTE}
    if human_path is not None:
        human_floors, human_unreadable, by, on = load_human_labels(human_path)
    if human_floors:
        bounds = _load_pred_bounds(bench_path)
        kept: list[FloorRow] = []
        compared = 0
        disagreed = 0
        for row in rows:
            if row.file in human_unreadable:
                continue
            if row.status == "OK" and row.file in human_floors:
                scored = _distance_to(human_floors[row.file], *bounds.get(row.file, ("", None, None))[1:])
                if scored is None:
                    continue
                row.distance, row.within1 = scored
                compared += 1
                if row.distance and row.distance > 0:
                    disagreed += 1
            kept.append(row)
        rows = kept
        names = ", ".join(sorted(by))
        days = f" ({', '.join(sorted(on))})" if on else ""
        reference = {
            "kind": "human",
            "note": f"Floor counts were counted by {names} from each photo{days}.",
        }
        disagreements = _metric(
            disagreed / compared if compared else None,
            compared,
            "Share of compared photos where the person count differs from the AI reader count.",
        )
    if not rows:
        return {
            **_not_measured("no labelled bench rows with predictions"),
            "exact": _metric(None, 0, "No confident counts to score."),
            "within1": _metric(None, 0, "No decided counts to score."),
            "abstain": _metric(None, 0, "No labelled bench rows."),
            "wrong_when_confident": _metric(None, 0, "No labelled bench rows."),
            "tiers": None,
            "reference": {"kind": "ai", "note": AI_REFERENCE_NOTE},
            "disagreements": None,
        }
    tiers_of = {row.file: _tier_of(row) for row in rows}
    confident = [row for row in rows if tiers_of[row.file] == "confident" and row.distance is not None]
    decided = [row for row in rows if row.status == "OK" and row.within1 is not None]
    estimate_n = sum(1 for row in rows if tiers_of[row.file] == "estimate")
    cant_tell_n = sum(1 for row in rows if tiers_of[row.file] == "cant_tell")
    exact = sum(1 for row in confident if row.distance == 0)
    within1 = sum(1 for row in decided if row.within1)
    wrong = sum(1 for row in confident if row.distance is not None and row.distance >= 2)
    return {
        "status": MEASURED,
        "n": len(rows),
        "exact": _metric(
            exact / len(confident) if confident else None,
            len(confident),
            "Share of confident counts exactly equal to the reference count.",
        ),
        "within1": _metric(
            within1 / len(decided) if decided else None,
            len(decided),
            "Share of decided counts within one floor of the reference count.",
        ),
        "abstain": _metric(
            cant_tell_n / len(rows),
            len(rows),
            "Share of labelled photos with no usable count (can't tell). Estimates count as answered.",
        ),
        "wrong_when_confident": _metric(
            wrong / len(confident) if confident else None,
            len(confident),
            "Share of confident counts 2+ floors off.",
        ),
        "tiers": {
            "confident": _metric(
                (len(rows) - estimate_n - cant_tell_n) / len(rows),
                len(rows),
                "Share of labelled photos with a confident count.",
            ),
            "estimate": _metric(
                estimate_n / len(rows),
                len(rows),
                "Share of labelled photos with an estimate only.",
            ),
            "cant_tell": _metric(
                cant_tell_n / len(rows),
                len(rows),
                "Share of labelled photos with no usable count.",
            ),
        },
        "reference": reference,
        "disagreements": disagreements,
    }


def _read_json_dir(path: Path) -> dict[str, dict]:
    """Map stem to parsed JSON object for every *.json file in a directory."""
    docs: dict[str, dict] = {}
    if not path.is_dir():
        return docs
    for file in sorted(path.glob("*.json")):
        try:
            parsed = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable %s: %s", file, exc)
            continue
        if isinstance(parsed, dict):
            docs[file.stem] = parsed
    return docs


def _filing_field(filing: dict, field: str) -> object:
    """Read one of the six scored fields from either supported schema."""
    if field in ("quarter_end", "filed_on"):
        return filing.get(field)
    if field == "slabs":
        if isinstance(filing.get("slabs"), dict):
            return filing["slabs"]
        slabs: dict[str, object] = {}
        blocks = filing.get("blocks")
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get("block_label") is not None:
                    slabs[str(block["block_label"])] = block.get("slabs_claimed_count")
        return slabs
    money_key = {
        "cumulative_received": "cumulative_received",
        "cumulative_spent": "cumulative_spent",
        "estimated_cost": "estimated_total_cost",
    }[field]
    if money_key in filing:
        return filing.get(money_key)
    financials = filing.get("financials")
    if isinstance(financials, dict):
        return financials.get(money_key)
    return None


def _values_equal(hand: object, extracted: object) -> bool:
    """Dates compare as strings; money compares through Decimal(str())."""
    if extracted is None:
        return False
    try:
        return Decimal(str(hand)) == Decimal(str(extracted))
    except InvalidOperation:
        return str(hand).strip() == str(extracted).strip()


def extraction_section(hand_dir: Path, extracted_dir: Path) -> dict:
    """Per-field accuracy over hand-labelled docs. Null hand values are skipped."""
    hand_docs = _read_json_dir(hand_dir)
    extracted_docs = _read_json_dir(extracted_dir)
    paired = sorted(set(hand_docs) & set(extracted_docs))
    if not paired:
        return {
            **_not_measured("no reference counts paired with extracted filings"),
            "n_docs": 0,
            "fields": {field: _metric(None, 0, "No paired reference counts.") for field in EXTRACTION_FIELDS},
        }
    correct: dict[str, int] = dict.fromkeys(EXTRACTION_FIELDS, 0)
    total: dict[str, int] = dict.fromkeys(EXTRACTION_FIELDS, 0)
    for doc_id in paired:
        hand = hand_docs[doc_id]
        extracted = extracted_docs[doc_id]
        for field in ("quarter_end", "filed_on"):
            if hand.get(field) is None:
                continue
            total[field] += 1
            if str(hand[field]).strip() == str(_filing_field(extracted, field)).strip():
                correct[field] += 1
        hand_slabs = hand.get("slabs")
        if isinstance(hand_slabs, dict):
            got_slabs = _filing_field(extracted, "slabs")
            got_map = got_slabs if isinstance(got_slabs, dict) else {}
            for block, want in hand_slabs.items():
                if want is None:
                    continue
                total["slabs"] += 1
                if got_map.get(block) == want or (
                    got_map.get(block) is not None and _values_equal(want, got_map.get(block))
                ):
                    correct["slabs"] += 1
        for field in ("cumulative_received", "cumulative_spent", "estimated_cost"):
            if hand.get(field) is None:
                continue
            total[field] += 1
            if _values_equal(hand[field], _filing_field(extracted, field)):
                correct[field] += 1
    methods = {
        "quarter_end": "Share of paired docs with matching quarter_end; null reference counts skipped.",
        "filed_on": "Share of paired docs with matching filed_on; null reference counts skipped.",
        "slabs": "Share of hand-labelled (doc, block) pairs with matching slabs_claimed_count.",
        "cumulative_received": "Share of paired docs matching; money compares via Decimal(str()).",
        "cumulative_spent": "Share of paired docs matching; money compares via Decimal(str()).",
        "estimated_cost": "Share of paired docs matching; money compares via Decimal(str()).",
    }
    return {
        "status": MEASURED,
        "n_docs": len(paired),
        "hand_without_extraction": sorted(set(hand_docs) - set(extracted_docs)),
        "extracted_without_hand": sorted(set(extracted_docs) - set(hand_docs)),
        "fields": {
            field: _metric(
                correct[field] / total[field] if total[field] else None, total[field], methods[field]
            )
            for field in EXTRACTION_FIELDS
        },
    }


def _review_files(review_dir: Path) -> list[Path]:
    """Every review_*.csv plus review.csv inside a directory, sorted by name."""
    found = {path for pattern in ("review_*.csv", "review.csv") for path in review_dir.glob(pattern)}
    return sorted(found)


def load_review_rows(review_path: Path) -> list[dict] | None:
    """Parse review rows from one file, or every review CSV in a directory merged.

    A directory concatenates review_*.csv and review.csv rows in sorted file
    order. A single file keeps the old behavior: None when missing.
    """
    if review_path.is_dir():
        rows: list[dict] = []
        for file in _review_files(review_path):
            try:
                with file.open(encoding="utf-8") as fh:
                    rows.extend(csv.DictReader(fh))
            except (OSError, csv.Error) as exc:
                logger.warning("Unreadable review %s: %s", file, exc)
        return rows or None
    if not review_path.is_file():
        return None
    try:
        with review_path.open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error) as exc:
        logger.warning("Unreadable review %s: %s", review_path, exc)
        return None


def summarize_flags(rows: list[dict]) -> dict[str, dict]:
    """Per-rule true/false/unclear counts plus precision. Unclear never enters precision."""
    counts: dict[str, dict[str, int]] = {}
    skipped = 0
    for row in rows:
        rule = (row.get("rule") or "").strip()
        verdict = (row.get("reviewer_verdict") or "").strip().lower()
        if not rule or verdict not in ("true", "false", "unclear"):
            skipped += 1
            continue
        counts.setdefault(rule, {"true": 0, "false": 0, "unclear": 0})
        counts[rule][verdict] += 1
    summary: dict[str, dict] = {}
    for rule in sorted(counts):
        decided = counts[rule]["true"] + counts[rule]["false"]
        summary[rule] = {
            **counts[rule],
            "precision": _metric(
                counts[rule]["true"] / decided if decided else None,
                decided,
                "Reviewer true flags over true+false; unclear kept out of the denominator.",
            ),
        }
    summary["_skipped_rows"] = skipped  # type: ignore[assignment]
    return summary


REVIEWER_NOTE = "Reviewer is a person-checked read of the PDF pages, not a model."


def _review_meta(rows: list[dict]) -> tuple[str | None, str | None]:
    """First reviewer note and latest reviewed_on date in the rows."""
    note: str | None = None
    latest: str | None = None
    for row in rows:
        text = (row.get("reviewer_note") or "").strip()
        if text and note is None:
            note = text
        day = (row.get("reviewed_on") or "").strip()
        if day and (latest is None or day > latest):
            latest = day
    if note and "person-checked" not in note.lower():
        note = f"{note} {REVIEWER_NOTE}"
    elif note is None:
        note = REVIEWER_NOTE
    return note, latest


def flags_section(review_path: Path) -> dict:
    """Flag precision per rule. Not measured when no reviewed rows exist.

    Accepts a directory (every review_*.csv plus review.csv merged) or one
    file (unchanged, with the review_r4 fallback for a missing single path).
    """
    rows = load_review_rows(review_path)
    if rows is None and not review_path.is_dir():
        fallback = review_path.parent / "review_r4_projectE.csv"
        if fallback != review_path:
            rows = load_review_rows(fallback)
    if rows is None:
        return {**_not_measured("no filled review.csv"), "rules": {}}
    summary = summarize_flags(rows)
    rules = {rule: entry for rule, entry in summary.items() if not rule.startswith("_")}
    if not rules:
        return {**_not_measured("no reviewed rows in review.csv"), "rules": {}}
    note, latest = _review_meta(rows)
    return {
        "status": MEASURED,
        "n_rows": sum(entry["true"] + entry["false"] + entry["unclear"] for entry in rules.values()),
        "skipped_rows": summary["_skipped_rows"],
        "rules": rules,
        "reviewer_note": note,
        "reviewed_on": latest,
    }


def earliest_flags(rows: list[dict]) -> dict[str, str]:
    """Earliest flag quarter_end per project over every reviewed row with a date."""
    earliest: dict[str, str] = {}
    for row in rows:
        project = (row.get("project_label") or "").strip()
        quarter = (row.get("quarter_end") or "").strip()
        if not project or not quarter:
            continue
        if project not in earliest or quarter < earliest[project]:
            earliest[project] = quarter
    return earliest


def _quarter_index(day: date) -> int:
    """Quarter ordinal so differences count quarter boundaries crossed."""
    return day.year * 4 + (day.month - 1) // 3


def summarize_backtest(actions: list[dict], flags: dict[str, str]) -> list[dict]:
    """Per-action earliest flag and lead in quarters, or not flagged."""
    entries: list[dict] = []
    for action in sorted(actions, key=lambda row: row.get("project_label", "")):
        project = action.get("project_label", "")
        flag_quarter = flags.get(project)
        n_flags = 1  # each lead compares one earliest-flag quarter with one action quarter
        if flag_quarter is None:
            entries.append(
                {
                    **action,
                    "earliest_flag": None,
                    "quarters_before": _metric(None, 0, "No flag quarter on or before the action quarter."),
                    "status": "not flagged",
                }
            )
            continue
        lead = _quarter_index(date.fromisoformat(action["action_date"])) - _quarter_index(
            date.fromisoformat(flag_quarter)
        )
        if lead < 0:
            entries.append(
                {
                    **action,
                    "earliest_flag": flag_quarter,
                    "quarters_before": _metric(
                        None, n_flags, "The first flag appeared after the regulator action."
                    ),
                    "status": "not flagged",
                }
            )
        else:
            entries.append(
                {
                    **action,
                    "earliest_flag": flag_quarter,
                    "quarters_before": _metric(
                        lead,
                        n_flags,
                        "Quarters between the earliest flag quarter_end and the action quarter.",
                    ),
                    "status": "flagged",
                }
            )
    return entries


def backtest_section(backtest_path: Path, flags: dict[str, str]) -> dict:
    """Backtest joins need both the action list and flag quarters from review rows."""
    if not backtest_path.is_file():
        return {**_not_measured("no backtest.csv"), "projects": []}
    try:
        with backtest_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error) as exc:
        logger.warning("Unreadable backtest %s: %s", backtest_path, exc)
        return {**_not_measured(f"unreadable backtest.csv: {exc}"), "projects": []}
    actions: list[dict] = []
    skipped = 0
    for row in rows:
        project = (row.get("project_label") or "").strip()
        action_date = (row.get("action_date") or "").strip()
        try:
            date.fromisoformat(action_date)
        except ValueError:
            skipped += 1
            continue
        if not project:
            skipped += 1
            continue
        actions.append(
            {
                "project_label": project,
                "action_date": action_date,
                "action_type": (row.get("action_type") or "").strip(),
                "order_url": (row.get("order_url") or "").strip(),
            }
        )
    if not actions:
        return {**_not_measured("no action rows in backtest.csv"), "projects": []}
    if not flags:
        return {**_not_measured("no flag quarters from review.csv"), "projects": []}
    logger.info("Backtest: %d actions, %d projects with flags", len(actions), len(flags))
    return {
        "status": MEASURED,
        "n_actions": len(actions),
        "skipped_rows": skipped,
        "projects": summarize_backtest(actions, flags),
    }


def _ref_bounds(video: dict) -> tuple[int, int] | None:
    """Reference bounds from public_reference (preferred) or reference_floors."""
    ref = video.get("public_reference")
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return (ref, ref)
    if isinstance(ref, list) and len(ref) == 2:
        try:
            low_ref, high_ref = int(ref[0]), int(ref[1])
        except (TypeError, ValueError):
            return None
        return (min(low_ref, high_ref), max(low_ref, high_ref))
    try:
        single = int(video.get("reference_floors"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return (single, single)


def _public_off_by(video: dict) -> dict | None:
    """Off-by between the measured range and a public record, else None.

    The gap between the nearest ends of the two ranges; 0 only when the
    ranges overlap. Videos without any reference, or with an unreadable
    range, give None.
    """
    try:
        low = int(video["lower"])
        up = int(video["upper"])
    except (TypeError, ValueError):
        return None
    bounds = _ref_bounds(video)
    if bounds is None:
        return None
    ref = video.get("public_reference")
    if ref is None:
        try:
            ref = int(video.get("reference_floors"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    if low <= bounds[1] and bounds[0] <= up:
        off_by = 0
    elif up < bounds[0]:
        off_by = bounds[0] - up
    else:
        off_by = low - bounds[1]
    return {
        "id": video.get("id"),
        "off_by": off_by,
        "bench_low": low,
        "bench_high": up,
        "reference": ref,
        "source": video.get("public_source"),
    }


def video_section(bench_video_path: Path) -> dict:
    """Floor counts on video against public-record references.

    Every video with a reference (reference_floors or public_reference) and
    a readable range counts (V06, V07, V08 give n=3). A video scores when its
    off-by gap is at most 1. References come from public records, not a
    person recount.
    """
    try:
        payload = json.loads(bench_video_path.read_text(encoding="utf-8"))
    except OSError:
        payload = {}
    videos = payload.get("videos", [])
    details = [
        detail
        for video in videos
        if isinstance(video, dict)
        for detail in [_public_off_by(video)]
        if detail is not None
    ]
    if not details:
        return {
            **_not_measured("no video reference counts yet"),
            "n": 0,
            "within1": _metric(None, 0, "No referenced videos to score."),
            "videos": [],
        }
    hits = sum(1 for detail in details if detail["off_by"] <= 1)
    return {
        "status": MEASURED,
        "n": len(details),
        "within1": _metric(
            hits / len(details),
            len(details),
            "Share of referenced videos with a range gap of at most one floor "
            "from the public record. References come from public records, "
            "not a person recount.",
        ),
        "videos": details,
    }


def build_report(
    labels_path: Path,
    bench_path: Path,
    hand_dir: Path,
    extracted_dir: Path,
    review_path: Path,
    backtest_path: Path,
    report_date: str,
    human_path: Path | None = None,
    bench_video_path: Path | None = None,
    golden_path: Path | None = None,
) -> dict:
    """Assemble the sections. Review rows load once for flags and backtest."""
    review_rows = load_review_rows(review_path)
    flags = earliest_flags(review_rows) if review_rows else {}
    return {
        "date": report_date,
        "floors": floor_section(labels_path, bench_path, human_path),
        "floors_video": video_section(bench_video_path or Path("data/eval/bench_video.json")),
        "extraction": extraction_section(hand_dir, extracted_dir),
        "golden": golden_section(golden_path or Path("data/eval/golden_project_e.json")),
        "flags": flags_section(review_path),
        "backtest": backtest_section(backtest_path, flags),
    }


def main(argv: list[str] | None = None) -> int:
    """Write the eval report JSON. Missing inputs become not-measured sections."""
    parser = argparse.ArgumentParser(description="Aggregate Section C eval numbers.")
    parser.add_argument("--labels", default="data/bench/labels.csv")
    parser.add_argument("--bench", default="data/eval/bench_floors.json")
    parser.add_argument("--hand-labels", default="data/labels")
    parser.add_argument("--extracted", default="data/eval/extracted")
    parser.add_argument("--review", default="data")
    parser.add_argument("--backtest", default="data/backtest.csv")
    parser.add_argument("--human-labels", default="data/bench/labels_human.csv")
    parser.add_argument("--golden", default="data/eval/golden_project_e.json")
    parser.add_argument("--out", default="data/eval.json")
    args = parser.parse_args(argv)
    report = build_report(
        Path(args.labels),
        Path(args.bench),
        Path(args.hand_labels),
        Path(args.extracted),
        Path(args.review),
        Path(args.backtest),
        date.today().isoformat(),
        Path(args.human_labels),
        golden_path=Path(args.golden),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for key in ("floors", "extraction", "golden", "flags", "backtest"):
        print(f"{key}: {report[key]['status']}")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
