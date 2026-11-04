"""Rule helpers and the eight deterministic checks. Pure: no I/O."""

import itertools
import re
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field

from stackcheck.models import (
    BlockProgress,
    Evidence,
    Financials,
    Finding,
    PageRef,
    PhotoRecord,
    ProjectEvidence,
    QuarterEvidence,
)
from stackcheck.vision.floors import ESTIMATE_FLAG_MARGIN
from stackcheck.vision.phash import hamming

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class RuleSettings(BaseModel):
    stamp_required_states: set[str] = Field(default_factory=set)
    r2_min_hash_bits: int = 12  # D23: below this a dhash is blank, not a match
    r2_max_hash_bits: int = 52  # D23: above this a dhash is inverted noise, not a match
    r2_min_photo_dim: int = 64  # D23: smaller photos are thumbnails, never compared


def quarter_start(quarter_end: date) -> date:
    """First day of the month two months before the quarter's month."""
    month = quarter_end.month - 2
    year = quarter_end.year
    if month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def to_inr(value: Decimal | None, unit: str | None) -> Decimal | None:
    """Money to INR. A lakh is 100,000, a crore is 10,000,000."""
    if value is None:
        return None
    name = (unit or "INR").upper()
    if name == "INR":
        return value
    if name in ("INR_LAKH", "LAKH"):
        return value * 100_000
    if name in ("INR_CRORE", "CRORE"):
        return value * 10_000_000
    raise ValueError(f"Unknown money unit: {unit!r}")


_BLOCK_PREFIX_RE = re.compile(r"(?i)^\s*block\s*[-–—:]?\s*")  # noqa: RUF001


def _block_core(label: str) -> str:
    """Block label without a leading 'Block' prefix, so 'Block A' shows once."""
    return _BLOCK_PREFIX_RE.sub("", label).strip() or label


def _block_display(label: str, project_name: str | None) -> tuple[str, str]:
    """Display form of a block label: 'the building' when the block is named after the project."""
    if project_name is not None and label == project_name:
        return "the building", "The building"
    core = _block_core(label)
    return f"block {core}", f"Block {core}"


def claimed_slabs(block: BlockProgress) -> int | None:
    """Slabs claimed: the count when given, else percent of sanctioned floors."""
    if block.slabs_claimed_count is not None:
        return block.slabs_claimed_count
    if block.slabs_claimed_percent is not None and block.floors_above_ground_sanctioned is not None:
        return round(block.slabs_claimed_percent / 100 * (block.floors_above_ground_sanctioned + 1))
    return None


def apply_user_floors(quarters: list[QuarterEvidence], floors_entered: int | None) -> None:
    """Fill missing sanctioned floors from the check form's number.

    The user counts every floor including the ground (5 for G+4) while the
    filing model counts above ground (G+4 -> 4), so the stored value is one
    less. Blocks whose filing already has floors are untouched.
    """
    if floors_entered is None or floors_entered < 1:
        return
    for quarter in quarters:
        for block in quarter.filing.blocks:
            if block.floors_above_ground_sanctioned is None:
                block.floors_above_ground_sanctioned = floors_entered - 1
                block.floors_entered_by_user = floors_entered


def user_floors_inputs(block: BlockProgress) -> dict[str, str]:
    """inputs entry when a percent claim was computed from user-entered floors."""
    entered = block.floors_entered_by_user
    if entered is None or block.slabs_claimed_count is not None or block.slabs_claimed_percent is None:
        return {}
    return {"floors_entered_by_you": str(entered)}


def photo_in_window(stamp: date | None, quarter_end: date) -> bool | None:
    """None when the stamp is missing, else whether it sits in the filing window."""
    if stamp is None:
        return None
    start = quarter_start(quarter_end) - timedelta(days=45)
    return start <= stamp <= quarter_end + timedelta(days=15)


_STAMP_EARLY_DAYS = 30  # a stamp older than this before the quarter is not current progress
_FILING_GRACE_DAYS = 2  # a stamp after filed_on plus this is impossible as printed


def _swap_day_month(stamp: date) -> date | None:
    """Read day and month swapped (4 Jun becomes 6 Apr); None when not a real date."""
    try:
        return date(stamp.year, stamp.day, stamp.month)
    except ValueError:
        return None


def _resolve_stamp(stamp: date, quarter_end: date, filed_on: date | None) -> tuple[date | None, bool]:
    """Effective stamp date, plus whether it came from a day/month swap.

    Returns (None, False) when the stamp is impossible as printed and no
    in-window swap exists: the date is unreadable, never a flag.
    """
    lower = quarter_start(quarter_end) - timedelta(days=_STAMP_EARLY_DAYS)
    upper = (
        (filed_on + timedelta(days=_FILING_GRACE_DAYS))
        if filed_on is not None
        else (quarter_end + timedelta(days=15))
    )
    if filed_on is not None and stamp > upper:
        swapped = _swap_day_month(stamp)
        if swapped is not None and lower <= swapped <= upper:
            return swapped, True
        return None, False
    return stamp, False


def format_inr(amount: Decimal) -> str:
    """Indian digit grouping: 10105126 becomes ₹1,01,05,126."""
    whole = int(amount)
    sign = "-" if whole < 0 else ""
    digits = str(abs(whole))
    if len(digits) <= 3:
        return f"{sign}₹{digits}"
    tail = digits[-3:]
    head = digits[:-3]
    groups = []
    while len(head) > 2:
        groups.append(head[-2:])
        head = head[:-2]
    groups.append(head)  # head is never empty: digits has 4+ chars and the loop keeps 1+
    return f"{sign}₹{','.join(reversed(groups))},{tail}"


def format_day(day: date) -> str:
    """31 Mar 2025."""
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _fin_inr(fin: Financials, field: str) -> Decimal | None:
    """One money field in INR, honouring the row's own unit override."""
    value = getattr(fin, field)
    if value is None:
        return None
    return to_inr(value, fin.unit_overrides.get(field, fin.currency_unit))


def _block_slabs(quarter: QuarterEvidence, block_label: str) -> int | None:
    """Claimed slabs for one block, or None when the block is missing."""
    for block in quarter.filing.blocks:
        if block.block_label == block_label:
            return claimed_slabs(block)
    return None


def _activity_key(name: str) -> str:
    return " ".join(name.lower().split())


_SNO_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\b")
_BLOCK_IN_NAME_RE = re.compile(r"(?i)^\s*block\s*[-–—]?\s*([A-Za-z](?:\s*-\s*\d+)?)")  # noqa: RUF001


def _normalize_block_label(label: str | None) -> str | None:
    """One key per block: 'Block A', 'BLOCK - A (ORION)', 'BLOCK-A' all become 'A'."""
    if label is None:
        return None
    text = re.sub(r"\(.*?\)", " ", label).upper()
    text = text.replace("BLOCK", " ")
    text = re.sub(r"[^A-Z0-9\-]", " ", text)
    for token in text.split():
        cleaned = re.sub(r"\s*-\s*", "-", token).strip("-")
        if re.fullmatch(r"[A-Z](-\d+)?", cleaned):
            return cleaned
    tokens = text.split()
    return tokens[0] if tokens else None


def _activity_block_from_name(name: str) -> str | None:
    """Block key when the activity name carries it ('Block A - Staircases...')."""
    match = _BLOCK_IN_NAME_RE.match(name)
    if match is None:
        return None
    return _normalize_block_label(match.group(1))


def _strip_sno(name: str) -> str:
    """Activity name without its leading S.NO."""
    return _SNO_RE.sub("", name).strip(" .")


def _strip_block_mention(text: str) -> str:
    """Remove a leading 'Block X' mention so names compare across spellings."""
    return _BLOCK_IN_NAME_RE.sub("", text).strip(" -–—:")  # noqa: RUF001


def _activity_base_name(name: str) -> str:
    """Comparable activity name: S.NO and block spelling removed, spacing collapsed."""
    once = _strip_block_mention(name)
    no_sno = _strip_sno(once)
    twice = _strip_block_mention(no_sno)
    return " ".join(twice.lower().split())


def _page_block_map(filing) -> dict[int, str | None]:
    """Page number to normalized block key from the filing's block refs.

    A page claimed by two blocks maps to None (ambiguous, never a match).
    """
    mapping: dict[int, str | None] = {}
    for block in filing.blocks:
        if block.ref is None:
            continue
        key = _normalize_block_label(block.block_label)
        page = block.ref.page
        if page in mapping and mapping[page] != key:
            mapping[page] = None
        else:
            mapping[page] = key
    return mapping


def _activity_block_key(activity, page_map: dict[int, str | None]) -> str | None:
    """Block key for one activity: stored row label, then name, then the page map."""
    stored = _normalize_block_label(getattr(activity, "block_label", None))
    if stored is not None:
        return stored
    from_name = _activity_block_from_name(activity.name)
    if from_name is not None:
        return from_name
    if activity.ref is not None:
        return page_map.get(activity.ref.page)
    return None


def _usable_photos(quarter: QuarterEvidence, block_label: str, quarter_end: date) -> list[PhotoRecord]:
    """Elevation photos with an OK count, matching the block, inside the window."""
    usable = []
    for photo in quarter.photos:
        facts = photo.facts
        floors = photo.floors
        if facts is None or floors is None:
            continue
        if facts.kind != "elevation" or floors.status != "OK" or floors.lower is None:
            continue
        guess = facts.block_label_guess
        if not (guess == block_label or (block_label == "MAIN" and guess is None)):
            continue
        if photo_in_window(facts.stamp_date, quarter_end) is False:
            continue
        usable.append(photo)
    return usable


def _photo_evidence(photo: PhotoRecord, note: str) -> Evidence:
    return Evidence(
        kind="photo",
        ref=PageRef(doc_id=photo.image.doc_id, page=photo.image.page),
        image_id=photo.image.image_id,
        overlay_path=photo.overlay_path,
        note=note,
    )


def _best_counted_photo(usable: list[PhotoRecord]) -> PhotoRecord:
    """Counted photo showing the most floors: highest upper bound, else highest lower."""
    with_upper = [p for p in usable if p.floors is not None and p.floors.upper is not None]
    if with_upper:
        return max(with_upper, key=lambda p: p.floors.upper)  # type: ignore[union-attr]
    below = [p for p in usable if p.floors is not None and p.floors.lower is not None]
    return max(below, key=lambda p: p.floors.lower)  # type: ignore[union-attr]


def _progress_text(slabs: int | None) -> str:
    """Claimed progress in words: a slab count, or no claim."""
    return f"{slabs} slabs" if slabs is not None else "no slab count"


def _r2_caption(when: date, quarter: QuarterEvidence, photo: PhotoRecord, block: str, distance: int) -> str:
    """Caption for an R2 photo: quarter, filing date, claim, distance. The page renders as a link."""
    filed = quarter.filing.filed_on
    filed_text = f"filed {format_day(filed)}" if filed is not None else "filed date not reported"
    return (
        f"Quarter ending {format_day(when)}, {filed_text}: "
        f"claimed {_progress_text(_block_slabs(quarter, block))}. "
        f"Hash distance {distance}."
    )


def _r1_from_estimate(
    quarter_end: date,
    block_label: str,
    shown: str,
    claimed: int,
    estimates: list[PhotoRecord],
    ref: PageRef | None,
    extra_inputs: dict[str, str],
) -> Finding:
    """R1 from ESTIMATE photos only. FLAG only at a gap of 3 or more floors.

    Vision alone is wrong by at most 2 on the 25 bench photos, so a gap of 3
    never fired on a correct claim there. Anything smaller is a question.
    """
    lowers = [p.floors.lower for p in estimates if p.floors is not None and p.floors.lower is not None]
    uppers = [p.floors.upper for p in estimates if p.floors is not None and p.floors.upper is not None]
    low, up = max(lowers), max(uppers)
    page_note = f" (page {ref.page})" if ref is not None else ""
    best = max(
        (p for p in estimates if p.floors is not None and p.floors.upper is not None),
        key=lambda p: p.floors.upper,  # type: ignore[union-attr]
    )
    if claimed - up >= ESTIMATE_FLAG_MARGIN:
        gap = claimed - up
        return Finding(
            finding_id="",
            rule="R1_PHOTO_VS_CLAIM",
            status="FLAG",
            severity="high",
            quarter_end=quarter_end,
            block_label=block_label,
            title=f"Photo shows about {up} floors (estimate), report claims {claimed} slabs",
            explanation=(
                f"The elevation photo shows about {up} floors (estimate); the report claims "
                f"{claimed} slabs{page_note}. A gap of {gap} floors needs an answer."
            ),
            evidence=[
                Evidence(
                    kind="quote",
                    ref=ref,
                    quote=f"Claimed slabs: {claimed}",
                    note="Slab claim copied from the filing",
                ),
                _photo_evidence(best, "Elevation photo filed for this quarter"),
                Evidence(
                    kind="computation",
                    value=f"{claimed} - {up} = {gap} floors short (estimate)",
                    note="Estimate range against the slab claim",
                ),
            ],
            inputs={
                "claimed_slabs": str(claimed),
                "photo_floors_lower": str(low),
                "photo_floors_upper": str(up),
                "gap_floors": str(gap),
                "photo_tier": "ESTIMATE",
                **extra_inputs,
            },
        )
    return Finding(
        finding_id="",
        rule="R1_PHOTO_VS_CLAIM",
        status="CANT_VERIFY",
        severity="info",
        quarter_end=quarter_end,
        block_label=block_label,
        title=f"About {up} floors in photo (estimate), report claims {claimed} slabs",
        explanation=(
            f"The elevation photo shows about {low} to {up} floors (estimate); the report claims "
            f"{claimed} slabs. The estimate is too rough to confirm or deny the claim, "
            "so this is a question to ask the builder."
        ),
        evidence=[_photo_evidence(best, "Elevation photo filed for this quarter")],
        inputs={
            "claimed_slabs": str(claimed),
            "photo_floors_lower": str(low),
            "photo_floors_upper": str(up),
            "photo_tier": "ESTIMATE",
            **extra_inputs,
        },
    )


def r1_photo_vs_claim(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]:
    """The builder's elevation photo must show about as many floors as claimed."""
    del settings_view
    findings = []
    for quarter in project.quarters:
        quarter_end = quarter.filing.quarter_end
        if quarter_end is None:
            findings.append(
                Finding(
                    finding_id="",
                    rule="R1_PHOTO_VS_CLAIM",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=None,
                    block_label=None,
                    title="Quarter has no end date",
                    explanation="A filing without a quarter end date can't be matched to its photos.",
                    evidence=[],
                    inputs={},
                )
            )
            continue
        for block in quarter.filing.blocks:
            label = block.block_label
            claimed = claimed_slabs(block)
            shown, shown_cap = _block_display(label, quarter.filing.project_name)
            if claimed is None:
                percent = block.slabs_claimed_percent
                if percent is not None:
                    title = f"No usable slab claim for {shown}"
                    explanation = (
                        f"The filing says {percent:g}% of the slabs are done, but it does not say "
                        "how many floors the building has, so we can't turn that into a floor count."
                    )
                elif any(a.percent_done is not None for a in quarter.filing.activities):
                    title = f"No usable slab claim for {shown}"
                    if block.floors_entered_by_user is not None:
                        explanation = "The filing gives a percent of work done but not the building's floors."
                    else:
                        explanation = (
                            "The filing gives a percent of work done but no floor count, "
                            "so the photos have nothing to be compared with."
                        )
                else:
                    title = f"No slab claim for {shown}"
                    explanation = f"The filing claims no slab count or percent for {shown}."
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R1_PHOTO_VS_CLAIM",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter_end,
                        block_label=label,
                        title=title,
                        explanation=explanation,
                        evidence=[],
                        inputs={},
                    )
                )
                continue
            usable = _usable_photos(quarter, label, quarter_end)
            confident = [p for p in usable if (p.floors.tier if p.floors else None) != "ESTIMATE"]
            estimates = [p for p in usable if p.floors is not None and p.floors.tier == "ESTIMATE"]
            if not confident and estimates:
                findings.append(
                    _r1_from_estimate(
                        quarter_end, label, shown, claimed, estimates, block.ref, user_floors_inputs(block)
                    )
                )
                continue
            usable = confident or usable
            if not usable:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R1_PHOTO_VS_CLAIM",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter_end,
                        block_label=label,
                        title=f"No usable elevation photo for {shown}",
                        explanation=(
                            f"{shown_cap} claims {claimed} slabs but no elevation photo "
                            "with an OK floor count covers it."
                        ),
                        evidence=[],
                        inputs={"claimed_slabs": str(claimed), **user_floors_inputs(block)},
                    )
                )
                continue
            lowers = []
            for photo in usable:
                assert photo.floors is not None and photo.floors.lower is not None
                lowers.append(photo.floors.lower)
            uppers = []
            for photo in usable:
                assert photo.floors is not None
                uppers.append(photo.floors.upper)
            low = max(lowers)
            up = None if any(u is None for u in uppers) else max(u for u in uppers if u is not None)
            if low >= claimed - 1:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R1_PHOTO_VS_CLAIM",
                        status="OK",
                        severity="info",
                        quarter_end=quarter_end,
                        block_label=label,
                        title=f"Photo floors cover the {claimed} claimed slabs",
                        explanation=(
                            f"The elevation photos show at least {low} floors; "
                            f"the filing claims {claimed} slabs."
                        ),
                        evidence=[
                            _photo_evidence(
                                _best_counted_photo(usable), "Elevation photo filed for this quarter"
                            )
                        ],
                        inputs={
                            "claimed_slabs": str(claimed),
                            "photo_floors_lower": str(low),
                            **user_floors_inputs(block),
                        },
                    )
                )
                continue
            if up is not None and up < claimed - 1:
                gap = (claimed - 1) - up
                ranked = []
                for candidate in usable:
                    # Usable photos always have a count; up existing means every upper is set.
                    assert candidate.floors is not None and candidate.floors.upper is not None
                    ranked.append((candidate.floors.upper, candidate))
                best = max(ranked, key=lambda item: item[0])[1]
                page_note = f" (page {block.ref.page})" if block.ref is not None else ""
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R1_PHOTO_VS_CLAIM",
                        status="FLAG",
                        severity="high" if gap >= 3 else "medium",
                        quarter_end=quarter_end,
                        block_label=label,
                        title=f"Photo shows {up} floors, report claims {claimed} slabs",
                        explanation=(
                            f"The elevation photo shows {up} floors; the report claims "
                            f"{claimed} slabs{page_note}. A gap of {gap} floors needs an answer."
                        ),
                        evidence=[
                            Evidence(
                                kind="quote",
                                ref=block.ref,
                                quote=f"Claimed slabs: {claimed}",
                                note="Slab claim copied from the filing",
                            ),
                            _photo_evidence(best, "Elevation photo filed for this quarter"),
                            Evidence(
                                kind="computation",
                                value=f"({claimed} - 1) - {up} = {gap} floors short",
                                note="Photo range against the slab claim",
                            ),
                        ],
                        inputs={
                            "claimed_slabs": str(claimed),
                            "photo_floors_lower": str(low),
                            "photo_floors_upper": str(up),
                            "gap_floors": str(gap),
                            **user_floors_inputs(block),
                        },
                    )
                )
                continue
            if up is None:  # noqa: SIM108 - the ternary does not fit in 110 columns
                detail = f"at least {low} floors with the top hidden"
            else:
                detail = f"{low} to {up} floors"
            findings.append(
                Finding(
                    finding_id="",
                    rule="R1_PHOTO_VS_CLAIM",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter_end,
                    block_label=label,
                    title="Photo range overlaps the claim",
                    explanation=(
                        f"The photos show {detail}; the report claims {claimed} slabs. "
                        "The range overlaps the claim, so this check can't verify it."
                    ),
                    evidence=[
                        _photo_evidence(_best_counted_photo(usable), "Elevation photo filed for this quarter")
                    ],
                    inputs={
                        "claimed_slabs": str(claimed),
                        "photo_floors_lower": str(low),
                        **user_floors_inputs(block),
                    },
                )
            )
    return findings


def r4_claim_regression(project: ProjectEvidence, rule_settings: RuleSettings) -> list[Finding]:
    """Later filings must not claim less progress than earlier ones.

    Activity rows compare only inside the same single-block filing, keyed by
    S.NO. Every FLAG carries the page reference of both quarters; a drop
    without both refs is CANT_VERIFY, never a flag.
    """
    del rule_settings  # R4 needs no settings; the engine still passes them to every rule.
    dated = sorted(
        (q for q in project.quarters if q.filing.quarter_end is not None),
        key=lambda q: q.filing.quarter_end,  # type: ignore[arg-type]
    )
    findings: list[Finding] = []
    compared = False
    for older, newer in itertools.pairwise(dated):
        newer_blocks = {_normalize_block_label(b.block_label): b for b in newer.filing.blocks}
        for block in older.filing.blocks:
            other = newer_blocks.get(_normalize_block_label(block.block_label))
            if other is None:
                continue
            before, after = claimed_slabs(block), claimed_slabs(other)
            if before is None or after is None:
                continue
            compared = True
            if after >= before:
                continue
            quarter = newer.filing.quarter_end
            assert quarter is not None
            if block.ref is None or other.ref is None:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R4_CLAIM_REGRESSION",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter,
                        block_label=block.block_label,
                        title=f"Claimed slabs went down from {before} to {after} (page ref missing)",
                        explanation=(
                            f"Quarter ending {quarter.isoformat()} claims {after} slabs for "
                            f"{block.block_label}; the previous quarter "
                            f"({older.filing.quarter_end}) claimed {before}. "
                            "A page reference is missing, so this can't be verified."
                        ),
                        evidence=[],
                        inputs={"slabs_before": str(before), "slabs_after": str(after)},
                    )
                )
                continue
            findings.append(
                Finding(
                    finding_id="",
                    rule="R4_CLAIM_REGRESSION",
                    status="FLAG",
                    severity="high",
                    quarter_end=quarter,
                    block_label=block.block_label,
                    title=f"Claimed slabs went down from {before} to {after}",
                    explanation=(
                        f"Quarter ending {quarter.isoformat()} claims {after} slabs for "
                        f"{block.block_label}; the previous quarter "
                        f"({older.filing.quarter_end}) claimed {before}. A later report "
                        "claims less work done than an earlier one."
                    ),
                    evidence=[
                        Evidence(
                            kind="quote",
                            ref=block.ref,
                            quote=f"Claimed slabs: {before}",
                            note="Slab claim in the earlier filing",
                        ),
                        Evidence(
                            kind="quote",
                            ref=other.ref,
                            quote=f"Claimed slabs: {after}",
                            note="Slab claim in the later filing",
                        ),
                    ],
                    inputs={"slabs_before": str(before), "slabs_after": str(after)},
                )
            )
        older_map = _page_block_map(older.filing)
        newer_map = _page_block_map(newer.filing)

        def collect(
            quarter: QuarterEvidence, page_map: dict[int, str | None]
        ) -> tuple[dict[tuple[str, str], tuple[float, PageRef | None, str]], set[tuple[str, str]]]:
            rows: dict[tuple[str, str], tuple[float, PageRef | None, str]] = {}
            dupes: set[tuple[str, str]] = set()
            for activity in quarter.filing.activities:
                if activity.percent_done is None:
                    continue
                base = _activity_base_name(activity.name)
                if not base:
                    continue
                key = _activity_block_key(activity, page_map)
                if key is None:
                    continue
                item = (key, base)
                if item in rows and rows[item][0] != activity.percent_done:
                    dupes.add(item)
                    continue
                rows.setdefault(item, (activity.percent_done, activity.ref, activity.name))
            return rows, dupes

        older_rows, older_dupes = collect(older, older_map)
        newer_rows, newer_dupes = collect(newer, newer_map)
        older_missing = any(
            _activity_block_key(a, older_map) is None
            for a in older.filing.activities
            if a.percent_done is not None and _activity_base_name(a.name)
        )
        newer_missing = any(
            _activity_block_key(a, newer_map) is None
            for a in newer.filing.activities
            if a.percent_done is not None and _activity_base_name(a.name)
        )
        if older_missing or newer_missing:
            compared = True
            quarter = newer.filing.quarter_end
            assert quarter is not None
            findings.append(
                Finding(
                    finding_id="",
                    rule="R4_CLAIM_REGRESSION",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter,
                    block_label=None,
                    title="Activity block could not be determined",
                    explanation=(
                        "Some activity rows have no block key on either filing, "
                        "so a drop can't be verified. A question to ask the "
                        "builder or RERA."
                    ),
                    evidence=[],
                    inputs={},
                )
            )
        drops: list[tuple[tuple[str, str], float, PageRef | None, float, PageRef | None, str]] = []
        for item in sorted(set(older_rows) & set(newer_rows)):
            block_key, _base = item
            if item in older_dupes or item in newer_dupes:
                compared = True
                quarter = newer.filing.quarter_end
                assert quarter is not None
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R4_CLAIM_REGRESSION",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter,
                        block_label=block_key,
                        title=f"Block {block_key} activity has conflicting reads",
                        explanation=(
                            f"Block {block_key} activity was read with different values in one filing, "
                            "so a drop can't be verified. A question to ask the builder or RERA."
                        ),
                        evidence=[],
                        inputs={"block": block_key},
                    )
                )
                continue
            before, before_ref, _ = older_rows[item]
            after, after_ref, after_name = newer_rows[item]
            compared = True
            if before - after <= 2:
                continue
            drops.append((item, before, before_ref, after, after_ref, after_name))
        page_counts: dict[int, int] = {}
        for _, _, _, _, newer_ref, _ in drops:
            if newer_ref is not None:
                page_counts[newer_ref.page] = page_counts.get(newer_ref.page, 0) + 1
        for item, before, before_ref, after, after_ref, after_name in drops:
            block_key, _ = item
            quarter = newer.filing.quarter_end
            assert quarter is not None
            refs = [
                Evidence(
                    kind="quote",
                    ref=ref,
                    quote=f"{value:g}%",
                    note=note,
                )
                for ref, value, note in (
                    (before_ref, before, "Progress in the earlier filing"),
                    (after_ref, after, "Progress in the later filing"),
                )
                if ref is not None
            ]
            if before == 100 and after <= 70:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R4_CLAIM_REGRESSION",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter,
                        block_label=block_key,
                        title=(f"Block {block_key} activity was 100% then {after:g}%, needs a second read"),
                        explanation=(
                            f"{after_name}: reported progress was 100% then {after:g}% "
                            "between two filings. A finished item reread this low looks "
                            "like a read error, so this can't be verified. "
                            "Needs a second read of the page."
                        ),
                        evidence=refs,
                        inputs={
                            "guard": "failed",
                            "block": block_key,
                            "before": str(before),
                            "after": str(after),
                        },
                    )
                )
                continue
            if after_ref is not None and page_counts.get(after_ref.page, 0) >= 2:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R4_CLAIM_REGRESSION",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter,
                        block_label=block_key,
                        title=(
                            f"Block {block_key} page {after_ref.page} reads disagree, needs a second read"
                        ),
                        explanation=(
                            f"{page_counts[after_ref.page]} activity drops share page "
                            f"{after_ref.page} in the later filing. The reads disagree, "
                            "so none of them can be verified. "
                            "Needs a second read of the page."
                        ),
                        evidence=refs,
                        inputs={
                            "guard": "failed",
                            "block": block_key,
                            "page": str(after_ref.page),
                            "before": str(before),
                            "after": str(after),
                        },
                    )
                )
                continue
            if before_ref is None or after_ref is None:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R4_CLAIM_REGRESSION",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter,
                        block_label=block_key,
                        title=f"Block {block_key} activity may have gone down (page ref missing)",
                        explanation=(
                            f"Block {block_key} activity reads {before:g}% then {after:g}%, but a page "
                            "reference is missing, so this can't be verified. "
                            "A question to ask the builder or RERA."
                        ),
                        evidence=[],
                        inputs={"block": block_key, "before": str(before), "after": str(after)},
                    )
                )
                continue
            before_text = f"{before:g}"
            after_text = f"{after:g}"
            findings.append(
                Finding(
                    finding_id="",
                    rule="R4_CLAIM_REGRESSION",
                    status="FLAG",
                    severity="medium",
                    quarter_end=quarter,
                    block_label=block_key,
                    title=f"Block {block_key} activity went down from {before_text}% to {after_text}%",
                    explanation=(
                        f"{after_name}: reported progress fell from {before_text}% to "
                        f"{after_text}% between two filings. A question to ask the "
                        "builder or RERA."
                    ),
                    evidence=[
                        Evidence(
                            kind="quote",
                            ref=before_ref,
                            quote=f"{before_text}%",
                            note="Progress in the earlier filing",
                        ),
                        Evidence(
                            kind="quote",
                            ref=after_ref,
                            quote=f"{after_text}%",
                            note="Progress in the later filing",
                        ),
                    ],
                    inputs={"block": block_key, "before": str(before), "after": str(after)},
                )
            )
    if not compared:
        return [
            Finding(
                finding_id="",
                rule="R4_CLAIM_REGRESSION",
                status="CANT_VERIFY",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="Only one dated filing to compare",
                explanation="Regression needs two dated filings for the same block.",
                evidence=[],
                inputs={},
            )
        ]
    if not findings:
        return [
            Finding(
                finding_id="",
                rule="R4_CLAIM_REGRESSION",
                status="OK",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="Claimed progress did not go down",
                explanation="Every later filing claims at least as much as the earlier one.",
                evidence=[],
                inputs={},
            )
        ]
    return findings


_R2_SITE_KINDS = ("elevation", "floor_level", "site_other")
_R2_MIN_CONFIDENCE = ("medium", "high")


def _r2_usable(photo: PhotoRecord, settings_view: RuleSettings) -> bool:
    """Only real site photos with a textured hash can count as reused.

    Blank scans, icons and thumbnails share hashes with each other, so they
    are never compared: kind and confidence first, then hash bit count, then size.
    """
    facts = photo.facts
    if facts is None or facts.kind not in _R2_SITE_KINDS:
        return False
    if facts.confidence not in _R2_MIN_CONFIDENCE:
        return False
    if not photo.dhash:
        return False
    bits = bin(int(photo.dhash, 16)).count("1")
    if bits < settings_view.r2_min_hash_bits or bits > settings_view.r2_max_hash_bits:
        return False
    return min(photo.image.width, photo.image.height) >= settings_view.r2_min_photo_dim


def r2_reused_photo(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]:
    """The same photo must not appear in two different quarters."""
    dated = sorted(
        (q for q in project.quarters if q.filing.quarter_end is not None),
        key=lambda q: q.filing.quarter_end,  # type: ignore[arg-type]
    )
    eligible = []
    for quarter in dated:
        for photo in quarter.photos:
            if _r2_usable(photo, settings_view):
                eligible.append((quarter.filing.quarter_end, quarter, photo))
    if len({when for when, _, _ in eligible}) < 2:
        return [
            Finding(
                finding_id="",
                rule="R2_REUSED_PHOTO",
                status="CANT_VERIFY",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="Fewer than two quarters with photos",
                explanation="Reused photos can only be spotted across two dated quarters with photos.",
                evidence=[],
                inputs={},
            )
        ]
    by_newer: dict[str, list[tuple[date, QuarterEvidence, PhotoRecord, int]]] = {}
    for first in range(len(eligible)):
        for second in range(first + 1, len(eligible)):
            when_a, _, photo_a = eligible[first]
            when_b, _, photo_b = eligible[second]
            if when_a == when_b or photo_a.dhash is None or photo_b.dhash is None:
                continue
            assert when_a is not None and when_b is not None
            distance = hamming(photo_a.dhash, photo_b.dhash)
            if distance > 6:
                continue
            older, newer = (
                (eligible[first], eligible[second])
                if when_a < when_b
                else (eligible[second], eligible[first])
            )
            by_newer.setdefault(newer[2].image.image_id, []).append(
                (older[0], older[1], older[2], distance)  # type: ignore[arg-type]
            )
    findings = []
    for when_new, quarter_new, photo_new in eligible:
        matches = by_newer.get(photo_new.image.image_id)
        if not matches:
            continue
        assert photo_new.facts is not None
        block = photo_new.facts.block_label_guess or "MAIN"
        slabs_new = _block_slabs(quarter_new, block)
        best = min(matches, key=lambda m: (m[3], m[0].isoformat() if m[0] else ""))
        when_old, quarter_old, photo_old, _ = best
        assert when_old is not None and when_new is not None
        assert photo_old.facts is not None
        both_elevation = photo_old.facts.kind == "elevation" and photo_new.facts.kind == "elevation"
        slabs_old = _block_slabs(quarter_old, block)
        differs = slabs_old is not None and slabs_new is not None and slabs_old != slabs_new
        if differs:
            change = (
                "Same photo filed again; the claimed progress changed "
                f"from {_progress_text(slabs_old)} to {_progress_text(slabs_new)}."
            )
        elif slabs_old == slabs_new and slabs_new is not None:
            change = (
                "Same photo filed again; the claimed progress is "
                f"{_progress_text(slabs_new)} in both quarters."
            )
        else:
            change = "Same photo filed again; the claimed progress can't be compared."
        old_list = ", ".join(
            f"{photo.image.image_id} ({format_day(when)})"  # type: ignore[arg-type]
            for when, _, photo, _ in sorted(matches, key=lambda m: (m[3], str(m[0])))
        )
        findings.append(
            Finding(
                finding_id="",
                rule="R2_REUSED_PHOTO",
                status="FLAG",
                severity="high" if both_elevation and differs else "medium",
                quarter_end=when_new,
                block_label=block,
                title=(
                    f"Same photo filed in {format_day(when_old)} and {format_day(when_new)}"
                    if len(matches) == 1
                    else f"Same photo filed in {format_day(when_new)} as in earlier quarters"
                ),
                explanation=(
                    f"The photo {photo_new.image.image_id} matches {old_list} "
                    f"(closest hash distance {best[3]}). "
                    "One photo can't show two quarters of progress. " + change
                ),
                evidence=[
                    _photo_evidence(
                        photo_new,
                        _r2_caption(when_new, quarter_new, photo_new, block, best[3]),
                    )
                ]
                + [
                    _photo_evidence(photo, _r2_caption(when, quarter, photo, block, distance))
                    for when, quarter, photo, distance in sorted(matches, key=lambda m: (m[3], str(m[0])))
                ],
                inputs={
                    "dhash_new": photo_new.dhash or "",
                    "matches": "; ".join(
                        f"{photo.image.image_id}:{distance}" for _, _, photo, distance in matches
                    ),
                },
            )
        )
    if not findings:
        return [
            Finding(
                finding_id="",
                rule="R2_REUSED_PHOTO",
                status="OK",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="No photo appears in more than one quarter",
                explanation="Every photo's hash is unique to its quarter.",
                evidence=[],
                inputs={},
            )
        ]
    return findings


def r3_photo_date(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]:
    """Date stamps must fall inside the quarter the photo is filed for."""
    findings = []
    for quarter in project.quarters:
        quarter_end = quarter.filing.quarter_end
        if quarter_end is None:
            findings.append(
                Finding(
                    finding_id="",
                    rule="R3_PHOTO_DATE",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=None,
                    block_label=None,
                    title="Quarter has no end date",
                    explanation=(
                        "A filing without a quarter end date gives stamps nothing to be checked against."
                    ),
                    evidence=[],
                    inputs={},
                )
            )
            continue
        if not quarter.photos:
            findings.append(
                Finding(
                    finding_id="",
                    rule="R3_PHOTO_DATE",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="No photos filed for this quarter",
                    explanation="With no photos, stamp dates can't be checked for this quarter.",
                    evidence=[],
                    inputs={},
                )
            )
            continue
        flagged = False
        readable = 0
        unusable: list[str] = []
        resolved: list[str] = []
        new: list[Finding] = []
        lower = quarter_start(quarter_end) - timedelta(days=_STAMP_EARLY_DAYS)
        filed_on = quarter.filing.filed_on
        for photo in quarter.photos:
            if photo.facts is None or photo.facts.kind == "document_or_logo":
                continue
            stamp = photo.facts.stamp_date
            if stamp is None:
                continue
            effective, swapped = _resolve_stamp(stamp, quarter_end, filed_on)
            if effective is None:
                unusable.append(f"{photo.image.image_id}: {stamp.isoformat()}")
                tried = _swap_day_month(stamp)
                photo.facts.stamp_date_resolved = None
                photo.facts.stamp_date_status = "impossible" if tried is not None else "unreadable"
                continue
            readable += 1
            if swapped:
                photo.facts.stamp_date_resolved = effective
                photo.facts.stamp_date_status = "swapped_low_confidence"
                resolved.append(
                    f"{photo.image.image_id}: {stamp.isoformat()} read as "
                    f"{effective.isoformat()} (low confidence)"
                )
                continue
            photo.facts.stamp_date_resolved = effective
            photo.facts.stamp_date_status = "ok"
            if effective < lower:
                dated = format_day(effective)
                ending = format_day(quarter_end)
                new.append(
                    Finding(
                        finding_id="",
                        rule="R3_PHOTO_DATE",
                        status="FLAG",
                        severity="medium",
                        quarter_end=quarter_end,
                        block_label=None,
                        title=f"Photo dated {dated} filed for quarter ending {ending}",
                        explanation=(
                            f"The stamp on {photo.image.image_id} reads {dated}, well before "
                            f"the quarter ending {ending}, so it is not current progress."
                        ),
                        evidence=[_photo_evidence(photo, "Photo with a stamp older than the quarter")],
                        inputs={"stamp_date": effective.isoformat()},
                    )
                )
                flagged = True
        non_docs = [
            photo
            for photo in quarter.photos
            if photo.facts is not None and photo.facts.kind != "document_or_logo"
        ]
        suffix = ""
        if resolved:
            suffix += f" {len(resolved)} photo date(s) read with day and month swapped (low confidence)."
        if unusable:
            suffix += f" {len(unusable)} photo(s) had dates after the filing date; we could not use them."
        extra_inputs: dict[str, str] = {}
        if resolved:
            extra_inputs["resolved_stamp_dates"] = "; ".join(resolved)
        if unusable:
            extra_inputs["unusable_stamp_dates"] = "; ".join(unusable)
        if readable == 0:
            findings.append(
                Finding(
                    finding_id="",
                    rule="R3_PHOTO_DATE",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="No dated site photos to check",
                    explanation=(
                        "No site photo carries a usable date stamp, "
                        "so timing can't be checked for this quarter." + suffix
                    ),
                    evidence=[],
                    inputs={"photos": str(len(non_docs)), **extra_inputs},
                )
            )
            continue
        if extra_inputs and new:
            new[0].inputs = {**new[0].inputs, **extra_inputs}
        findings.extend(new)
        missing = sum(1 for photo in non_docs if photo.facts is not None and photo.facts.stamp_date is None)
        missing += len(unusable)
        required = quarter.filing.state in settings_view.stamp_required_states
        if required and non_docs and missing > len(non_docs) / 2:
            findings.append(
                Finding(
                    finding_id="",
                    rule="R3_PHOTO_DATE",
                    status="FLAG",
                    severity="low",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="Most photos have no readable date stamp",
                    explanation=(
                        f"{missing} of {len(non_docs)} photos carry no readable date stamp, "
                        "so their timing can't be checked." + suffix
                    ),
                    evidence=[
                        _photo_evidence(photo, "Photo with no readable date stamp")
                        for photo in non_docs
                        if photo.facts is not None and photo.facts.stamp_date is None
                    ]
                    + [
                        _photo_evidence(photo, "Photo with an unusable date stamp")
                        for photo in non_docs
                        if photo.image.image_id in {u.split(":")[0] for u in unusable}
                    ],
                    inputs={"unstamped": str(missing), "photos": str(len(non_docs)), **extra_inputs},
                )
            )
            flagged = True
        if not flagged:
            findings.append(
                Finding(
                    finding_id="",
                    rule="R3_PHOTO_DATE",
                    status="OK",
                    severity="info",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="Photo dates line up with the quarter",
                    explanation=("Every stamped photo falls inside its quarter's filing window." + suffix),
                    evidence=[],
                    inputs=dict(extra_inputs),
                )
            )
    return findings


def r5_copy_paste_quarter(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]:
    """A quarter must not repeat the previous quarter's figures."""
    del settings_view
    dated = sorted(
        (q for q in project.quarters if q.filing.quarter_end is not None),
        key=lambda q: q.filing.quarter_end,  # type: ignore[arg-type]
    )
    findings = []
    compared = False
    for older, newer in itertools.pairwise(dated):
        quarter_end = newer.filing.quarter_end
        assert quarter_end is not None
        if older.source.sha256 == newer.source.sha256:
            compared = True
            findings.append(
                Finding(
                    finding_id="",
                    rule="R5_COPY_PASTE_QUARTER",
                    status="FLAG",
                    severity="high",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="Identical report filed for two quarters",
                    explanation=(
                        "The two filings are byte-for-byte the same file, "
                        "so the later quarter reports nothing new."
                    ),
                    evidence=[
                        Evidence(
                            kind="computation",
                            value=newer.source.sha256[:16],
                            note="Both quarters share one file hash",
                        )
                    ],
                    inputs={"sha256": newer.source.sha256},
                )
            )
            continue
        old_rows = {
            _activity_key(a.name): (a.percent_done, a.ref)
            for a in older.filing.activities
            if a.percent_done is not None
        }
        new_rows = {
            _activity_key(a.name): (a.percent_done, a.ref)
            for a in newer.filing.activities
            if a.percent_done is not None
        }
        shared = [name for name in new_rows if name in old_rows]
        if len(shared) < 5:
            continue
        compared = True
        same = sum(1 for name in shared if new_rows[name][0] == old_rows[name][0])
        if same / len(shared) < 0.9:
            continue
        anchored = [
            name
            for name in shared
            if new_rows[name][0] == old_rows[name][0]
            and (new_rows[name][0] or 0) != 0
            and new_rows[name][1] is not None
            and old_rows[name][1] is not None
        ]
        if len(anchored) < 2:
            findings.append(
                Finding(
                    finding_id="",
                    rule="R5_COPY_PASTE_QUARTER",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="Repeated figures lack page references",
                    explanation=(
                        f"{same} of {len(shared)} shared activities repeat last quarter's "
                        "percentages, but fewer than two non-zero repeats carry a page "
                        "reference on both quarters, so copy-paste can't be verified."
                    ),
                    evidence=[],
                    inputs={"shared": str(len(shared)), "identical": str(same)},
                )
            )
            continue
        first = anchored[0]
        fin_old = older.filing.financials
        fin_new = newer.filing.financials
        received_old = _fin_inr(fin_old, "cumulative_received")
        spent_old = _fin_inr(fin_old, "cumulative_spent")
        received_new = _fin_inr(fin_new, "cumulative_received")
        spent_new = _fin_inr(fin_new, "cumulative_spent")
        spent_this = _fin_inr(fin_new, "spent_this_quarter")
        page_evidence = [
            Evidence(
                kind="quote",
                ref=old_rows[first][1],
                quote=f"Repeated figure in the earlier filing: {first}",
                note="Earlier quarter page for a repeated activity",
            ),
            Evidence(
                kind="quote",
                ref=new_rows[first][1],
                quote=f"Repeated figure in the later filing: {first}",
                note="Later quarter page for a repeated activity",
            ),
        ]
        if (
            received_old is not None
            and spent_old is not None
            and received_old == received_new
            and spent_old == spent_new
        ):
            findings.append(
                Finding(
                    finding_id="",
                    rule="R5_COPY_PASTE_QUARTER",
                    status="FLAG",
                    severity="medium",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="Quarter repeats the previous quarter's figures",
                    explanation=(
                        f"{same} of {len(shared)} shared activities report the same percentages, "
                        "and the cumulative money figures are identical too."
                    ),
                    evidence=[
                        Evidence(
                            kind="computation",
                            value=f"{same}/{len(shared)} activities unchanged",
                            note="Shared activities with identical percentages",
                        ),
                        *page_evidence,
                    ],
                    inputs={"shared": str(len(shared)), "identical": str(same)},
                )
            )
        elif spent_this is not None and spent_this > Decimal(0):
            pages = sorted(
                {
                    ref.page
                    for name in anchored
                    for ref in (old_rows[name][1], new_rows[name][1])
                    if ref is not None
                }
            )
            pages_str = f"page {pages[0]}" if len(pages) == 1 else f"pages {', '.join(str(p) for p in pages)}"
            findings.append(
                Finding(
                    finding_id="",
                    rule="R5_COPY_PASTE_QUARTER",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter_end,
                    block_label=None,
                    title=f"Shared section repeats last quarter ({pages_str})",
                    explanation=(
                        f"The quarter reports {format_inr(spent_this)} spent while {same} of "
                        f"{len(shared)} shared activities repeat last quarter's percentages "
                        f"on {pages_str}. Only the repeated section on {pages_str} was "
                        "compared; construction and other pages were not compared, "
                        "so copy-paste can't be verified."
                    ),
                    evidence=[],
                    inputs={"spent_this_quarter_inr": str(spent_this)},
                )
            )
    if not compared:
        return [
            Finding(
                finding_id="",
                rule="R5_COPY_PASTE_QUARTER",
                status="CANT_VERIFY",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="Nothing comparable across quarters",
                explanation="Copy-paste needs two dated filings with at least five shared activities.",
                evidence=[],
                inputs={},
            )
        ]
    if not findings:
        return [
            Finding(
                finding_id="",
                rule="R5_COPY_PASTE_QUARTER",
                status="OK",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="Each quarter reports its own figures",
                explanation="Shared activities move between quarters, so nothing looks copied.",
                evidence=[],
                inputs={},
            )
        ]
    return findings


def _progress_share(quarter: QuarterEvidence) -> float | None:
    """Share of work done: mean activity percent, else slabs over sanctioned floors."""
    share, _ = _progress_share_with_method(quarter)
    return share


def _progress_share_with_method(quarter: QuarterEvidence) -> tuple[float | None, str | None]:
    """Work share plus how it was computed, so the finding can say so."""
    values = [a.percent_done for a in quarter.filing.activities if a.percent_done is not None]
    if len(values) >= 3:
        return sum(values) / len(values) / 100, f"average of {len(values)} activity percentages"
    main = next((b for b in quarter.filing.blocks if b.block_label == "MAIN"), None)
    if main is None:
        return None, None
    slabs = claimed_slabs(main)
    floors = main.floors_above_ground_sanctioned
    if slabs is None or floors is None:
        return None, None
    return slabs / (floors + 1), f"slab claim {slabs} of {floors + 1} floors (block MAIN)"


_CRORE_RUPEES = Decimal(10_000_000)  # Rs 1 crore: plausibility gates need an estimate this big
_LAKH_RUPEES = Decimal(100_000)  # Rs 1 lakh: money figures under this are implausible reads


def r6_money_vs_progress(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]:
    """Money spent must not run far ahead of work done."""
    del settings_view
    dated = sorted(
        (q for q in project.quarters if q.filing.quarter_end is not None),
        key=lambda q: q.filing.quarter_end,  # type: ignore[arg-type]
        reverse=True,
    )
    for quarter in dated:
        quarter_end = quarter.filing.quarter_end
        assert quarter_end is not None
        fin = quarter.filing.financials
        spent = _fin_inr(fin, "cumulative_spent")
        total = _fin_inr(fin, "estimated_total_cost")
        if spent is None or total is None or total <= 0:
            continue
        if total >= _CRORE_RUPEES and spent < _LAKH_RUPEES:
            return [
                Finding(
                    finding_id="",
                    rule="R6_MONEY_VS_PROGRESS",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="Money figures look implausible, needs a second read",
                    explanation=(
                        f"Estimated total cost is {format_inr(total)} but cumulative spend is "
                        f"{format_inr(spent)}. A spend under Rs 1,00,000 against a crore-scale "
                        "estimate looks like a read error, so it can't be compared with work "
                        "done. Needs a second read of the page."
                    ),
                    evidence=[
                        Evidence(
                            kind="number",
                            ref=fin.ref,
                            value=(f"cumulative spent {format_inr(spent)} of estimated {format_inr(total)}"),
                            note="Cumulative spend against estimated total",
                        )
                    ]
                    if fin.ref is not None
                    else [],
                    inputs={
                        "guard": "failed",
                        "cumulative_spent": str(spent),
                        "estimated_total_cost": str(total),
                    },
                )
            ]
        progress, method = _progress_share_with_method(quarter)
        if progress is None:
            continue
        spent_share = float(spent / total)
        if spent_share - progress > 0.25:
            if fin.ref is None:
                return [
                    Finding(
                        finding_id="",
                        rule="R6_MONEY_VS_PROGRESS",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter_end,
                        block_label=None,
                        title="Money comparison can't be traced to a page",
                        explanation=(
                            "Spending looks ahead of work done, but the filing has no page "
                            "reference for its money table."
                        ),
                        evidence=[],
                        inputs={
                            "spent_share": f"{spent_share:.3f}",
                            "progress_share": f"{progress:.3f}",
                        },
                    )
                ]
            return [
                Finding(
                    finding_id="",
                    rule="R6_MONEY_VS_PROGRESS",
                    status="FLAG",
                    severity="low",
                    quarter_end=quarter_end,
                    block_label=None,
                    title="Money spent runs ahead of work done",
                    explanation=(
                        f"Spent {spent_share:.0%} of the estimated {format_inr(total)} while work "
                        f"done is {progress:.0%} ({method}). Land and approval costs can explain "
                        "early spending."
                    ),
                    evidence=[
                        Evidence(
                            kind="computation",
                            ref=fin.ref,
                            value=f"{spent_share:.0%} spent vs {progress:.0%} done",
                            note="Cumulative spend against estimated total and work share",
                        )
                    ],
                    inputs={
                        "spent_share": f"{spent_share:.3f}",
                        "progress_share": f"{progress:.3f}",
                    },
                )
            ]
        direction = "trails" if spent_share < progress else "is in line with"
        return [
            Finding(
                finding_id="",
                rule="R6_MONEY_VS_PROGRESS",
                status="OK",
                severity="info",
                quarter_end=quarter_end,
                block_label=None,
                title=f"Money spent {direction} work done",
                explanation=(
                    f"Spent {spent_share:.0%} of the estimated cost while work done is {progress:.0%} "
                    f"({method})."
                ),
                evidence=[
                    Evidence(
                        kind="number",
                        ref=fin.ref,
                        value=f"{spent_share:.0%} spent of estimated {format_inr(total)}",
                        note="Cumulative spend against estimated total",
                    )
                ]
                if fin.ref is not None
                else [],
                inputs={
                    "spent_share": f"{spent_share:.3f}",
                    "progress_share": f"{progress:.3f}",
                },
            )
        ]
    return [
        Finding(
            finding_id="",
            rule="R6_MONEY_VS_PROGRESS",
            status="CANT_VERIFY",
            severity="info",
            quarter_end=None,
            block_label=None,
            title="Money or progress figures missing",
            explanation="The check needs cumulative spend, the estimated total and a work share.",
            evidence=[],
            inputs={},
        )
    ]


def r7_arithmetic(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]:
    """Money columns must add up within a filing and across quarters."""
    del settings_view
    findings: list[Finding] = []
    checked = False
    money_pages: list[Evidence] = []
    seen_pages: set[tuple[str, int]] = set()
    net_firings: list[tuple[date | None, Financials, Decimal, Decimal, Decimal]] = []
    implausible_ids: set[int] = set()
    for quarter in project.quarters:
        fin = quarter.filing.financials
        cum_spent = _fin_inr(fin, "cumulative_spent")
        this_spent = _fin_inr(fin, "spent_this_quarter")
        cum_received = _fin_inr(fin, "cumulative_received")
        this_received = _fin_inr(fin, "received_this_quarter")
        if (
            cum_spent is not None
            or this_spent is not None
            or cum_received is not None
            or this_received is not None
        ):
            checked = True
            if fin.ref is not None and (fin.ref.doc_id, fin.ref.page) not in seen_pages:
                seen_pages.add((fin.ref.doc_id, fin.ref.page))
                money_pages.append(
                    Evidence(
                        kind="number",
                        ref=fin.ref,
                        value="; ".join(
                            [
                                f"cumulative spent {format_inr(cum_spent)}"
                                if cum_spent is not None
                                else "spent not reported",
                                f"received {format_inr(cum_received)}"
                                if cum_received is not None
                                else "received not reported",
                            ]
                        ),
                        note="Money table checked",
                    )
                )
        net = _fin_inr(fin, "net_balance")
        estimated = _fin_inr(fin, "estimated_total_cost")
        tiny = next(
            (
                figure
                for figure in (cum_received, cum_spent, net)
                if figure is not None and figure < _LAKH_RUPEES
            ),
            None,
        )
        if estimated is not None and estimated >= _CRORE_RUPEES and tiny is not None:
            checked = True
            implausible_ids.add(id(quarter))
            findings.append(
                Finding(
                    finding_id="",
                    rule="R7_ARITHMETIC",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter.filing.quarter_end,
                    block_label=None,
                    title="Money figures look implausible, needs a second read",
                    explanation=(
                        f"Estimated total cost is {format_inr(estimated)} but a reported "
                        f"money figure is {format_inr(tiny)}. A figure under Rs 1,00,000 "
                        "against a crore-scale estimate looks like a read error, so no "
                        "money arithmetic can be checked for this quarter. "
                        "Needs a second read of the page."
                    ),
                    evidence=[
                        Evidence(
                            kind="number",
                            ref=fin.ref,
                            value="; ".join(
                                [
                                    f"cumulative spent {format_inr(cum_spent)}"
                                    if cum_spent is not None
                                    else "spent not reported",
                                    f"received {format_inr(cum_received)}"
                                    if cum_received is not None
                                    else "received not reported",
                                    f"net {format_inr(net)}" if net is not None else "net not reported",
                                ]
                            ),
                            note="Money table checked",
                        )
                    ]
                    if fin.ref is not None
                    else [],
                    inputs={
                        "guard": "failed",
                        "estimated_total_cost": str(estimated),
                        "cumulative_received": str(cum_received),
                        "cumulative_spent": str(cum_spent),
                        "net_balance": str(net),
                    },
                )
            )
            continue
        problems = []
        if cum_spent is not None and this_spent is not None and cum_spent < this_spent:
            problems.append(
                f"cumulative spent {format_inr(cum_spent)} is less than spent this quarter "
                f"{format_inr(this_spent)}"
            )
        if cum_received is not None and this_received is not None and cum_received < this_received:
            problems.append(
                f"cumulative received {format_inr(cum_received)} is less than received this quarter "
                f"{format_inr(this_received)}"
            )
        if problems:
            if fin.ref is None:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R7_ARITHMETIC",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter.filing.quarter_end,
                        block_label=None,
                        title="Money columns can't be traced to a page",
                        explanation=(
                            "; ".join(problems) + ". The filing has no page reference for its money table, "
                            "so the columns can't be checked against the page."
                        ),
                        evidence=[],
                        inputs={
                            "cumulative_spent": str(cum_spent),
                            "spent_this_quarter": str(this_spent),
                            "cumulative_received": str(cum_received),
                            "received_this_quarter": str(this_received),
                        },
                    )
                )
            else:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R7_ARITHMETIC",
                        status="FLAG",
                        severity="low",
                        quarter_end=quarter.filing.quarter_end,
                        block_label=None,
                        title="Money columns do not add up",
                        explanation="; ".join(problems) + ".",
                        evidence=[
                            Evidence(
                                kind="computation",
                                ref=fin.ref,
                                value="; ".join(problems),
                                note="Within-filing money columns",
                            )
                        ],
                        inputs={
                            "cumulative_spent": str(cum_spent),
                            "spent_this_quarter": str(this_spent),
                            "cumulative_received": str(cum_received),
                            "received_this_quarter": str(this_received),
                        },
                    )
                )
        figures_repeated = (
            net is not None
            and cum_received is not None
            and cum_spent is not None
            and (net in (cum_received, cum_spent) or cum_received == cum_spent)
        )
        net_guard_failed = False
        if figures_repeated:
            net_guard_failed = True
            findings.append(
                Finding(
                    finding_id="",
                    rule="R7_ARITHMETIC",
                    status="CANT_VERIFY",
                    severity="info",
                    quarter_end=quarter.filing.quarter_end,
                    block_label=None,
                    title="Money figures look copied, needs a second read",
                    explanation=(
                        f"This quarter's money figures repeat a value exactly "
                        f"(net {format_inr(net)}, collected {format_inr(cum_received)}, "
                        f"spent {format_inr(cum_spent)}), which looks like a copying slip. "
                        "Needs a second read of the page."
                    ),
                    evidence=[
                        Evidence(
                            kind="computation",
                            ref=fin.ref,
                            value=(
                                f"net {format_inr(net)} vs collected "
                                f"{format_inr(cum_received)} vs spent {format_inr(cum_spent)}"
                            ),
                            note="Repeated money figure in this quarter",
                        )
                    ]
                    if fin.ref is not None
                    else [],
                    inputs={
                        "guard": "failed",
                        "net_balance": str(net),
                        "cumulative_received": str(cum_received),
                        "cumulative_spent": str(cum_spent),
                    },
                )
            )
        if (
            net is not None
            and cum_received is not None
            and cum_spent is not None
            and estimated is not None
            and estimated > 0
        ):
            checked = True
            expected = cum_received - cum_spent
            if not net_guard_failed and abs(net - expected) > estimated * Decimal("0.02"):
                net_firings.append((quarter.filing.quarter_end, fin, net, cum_received, cum_spent))
    if net_firings:
        ref_firings = [firing for firing in net_firings if firing[1].ref is not None]
        for quarter_end, fin, net, cum_received, cum_spent in net_firings:
            if fin.ref is None:
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R7_ARITHMETIC",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter_end,
                        block_label=None,
                        title="Net amount can't be traced to a page",
                        explanation=(
                            "The filing's net amount can't be reconciled because the money "
                            "table has no page reference."
                        ),
                        evidence=[],
                        inputs={
                            "net_balance": str(net),
                            "cumulative_received": str(cum_received),
                            "cumulative_spent": str(cum_spent),
                        },
                    )
                )
        if ref_firings:
            latest = max(ref_firings, key=lambda firing: firing[0] or date.min)
            latest_end, latest_fin, latest_net, latest_received, latest_spent = latest
            latest_expected = latest_received - latest_spent
            latest_ref = latest_fin.ref
            assert latest_end is not None and latest_ref is not None
            if len(ref_firings) == 1:
                explanation = (
                    f"The filing's net amount is {format_inr(latest_net)}. "
                    f"Collected minus spent is {format_inr(latest_expected)}. "
                    "A question to ask the builder or RERA: where did the difference "
                    "come from (promoter funds, a loan, another account)? "
                    "Not proof of wrongdoing."
                )
            else:
                explanation = (
                    f"Net amounts do not reconcile in {len(ref_firings)} quarters. "
                    f"Latest quarter ending {latest_end.isoformat()}: "
                    f"net amount is {format_inr(latest_net)}. "
                    f"Collected minus spent is {format_inr(latest_expected)}. "
                    "A question to ask the builder or RERA: where did the difference "
                    "come from (promoter funds, a loan, another account)? "
                    "Not proof of wrongdoing."
                )
            findings.append(
                Finding(
                    finding_id="",
                    rule="R7_ARITHMETIC",
                    status="FLAG",
                    severity="low",
                    quarter_end=max(firing[0] for firing in net_firings if firing[0] is not None),
                    block_label=None,
                    title="Net amount doesn't match collected minus spent",
                    explanation=explanation,
                    evidence=[
                        Evidence(
                            kind="quote",
                            ref=latest_ref,
                            quote=f"Net amount: {format_inr(latest_net)}",
                            note="Net amount in the filing",
                        )
                    ]
                    + [
                        Evidence(
                            kind="computation",
                            ref=firing_fin.ref,
                            value=(
                                f"{format_inr(firing_received)} - {format_inr(firing_spent)} = "
                                f"{format_inr(firing_received - firing_spent)} "
                                f"vs {format_inr(firing_net)} ({firing_end.isoformat()})"
                                if firing_end is not None
                                else f"{format_inr(firing_received)} - "
                                f"{format_inr(firing_spent)} = "
                                f"{format_inr(firing_received - firing_spent)} "
                                f"vs {format_inr(firing_net)}"
                            ),
                            note="Collected minus spent against the net amount",
                        )
                        for firing_end, firing_fin, firing_net, firing_received, firing_spent in sorted(
                            ref_firings, key=lambda firing: firing[0] or date.min
                        )
                    ],
                    inputs={
                        "net_balance": str(latest_net),
                        "cumulative_received": str(latest_received),
                        "cumulative_spent": str(latest_spent),
                        "quarters": str(len(ref_firings)),
                    },
                )
            )
    dated = sorted(
        (q for q in project.quarters if q.filing.quarter_end is not None),
        key=lambda q: q.filing.quarter_end,  # type: ignore[arg-type]
    )
    for older, newer in itertools.pairwise(dated):
        if id(older) in implausible_ids or id(newer) in implausible_ids:
            continue
        quarter_end = newer.filing.quarter_end
        assert quarter_end is not None
        old_fin = older.filing.financials
        new_fin = newer.filing.financials
        for label, cum_field, this_field in (
            ("spent", "cumulative_spent", "spent_this_quarter"),
            ("received", "cumulative_received", "received_this_quarter"),
        ):
            cum_before = _fin_inr(old_fin, cum_field)
            cum_after = _fin_inr(new_fin, cum_field)
            this_quarter = _fin_inr(new_fin, this_field)
            if cum_before is None or cum_after is None or this_quarter is None:
                continue
            checked = True
            if cum_after != cum_before + this_quarter:
                inputs = {
                    "guard": "failed",
                    f"cumulative_{label}_before": str(cum_before),
                    f"{label}_this_quarter": str(this_quarter),
                    f"cumulative_{label}_after": str(cum_after),
                }
                old_evidence = (
                    [
                        Evidence(
                            kind="number",
                            ref=old_fin.ref,
                            value=f"cumulative {label} {format_inr(cum_before)}",
                            note="Previous quarter cumulative",
                        )
                    ]
                    if old_fin.ref is not None
                    else []
                )
                new_evidence = (
                    [
                        Evidence(
                            kind="number",
                            ref=new_fin.ref,
                            value=(
                                f"cumulative {label} {format_inr(cum_after)}, "
                                f"this quarter {format_inr(this_quarter)}"
                            ),
                            note="New cumulative and this quarter",
                        ),
                        Evidence(
                            kind="computation",
                            ref=new_fin.ref,
                            value=(
                                f"{format_inr(cum_before)} + {format_inr(this_quarter)} = "
                                f"{format_inr(cum_before + this_quarter)} "
                                f"vs {format_inr(cum_after)}"
                            ),
                            note="Previous cumulative plus this quarter against the new cumulative",
                        ),
                    ]
                    if new_fin.ref is not None
                    else []
                )
                findings.append(
                    Finding(
                        finding_id="",
                        rule="R7_ARITHMETIC",
                        status="CANT_VERIFY",
                        severity="info",
                        quarter_end=quarter_end,
                        block_label=None,
                        title=f"Cumulative {label} needs a second read of the page",
                        explanation=(
                            f"Cumulative {label} moved from {format_inr(cum_before)} to "
                            f"{format_inr(cum_after)} but this quarter reports "
                            f"{format_inr(this_quarter)}: {format_inr(cum_before)} + "
                            f"{format_inr(this_quarter)} = "
                            f"{format_inr(cum_before + this_quarter)}, not "
                            f"{format_inr(cum_after)}. Needs a second read of the page."
                        ),
                        evidence=old_evidence + new_evidence,
                        inputs=inputs,
                    )
                )
    if not checked:
        return [
            Finding(
                finding_id="",
                rule="R7_ARITHMETIC",
                status="CANT_VERIFY",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="No money figures to add up",
                explanation="No filing carries money figures, so arithmetic can't be checked.",
                evidence=[],
                inputs={},
            )
        ]
    if not findings:
        return [
            Finding(
                finding_id="",
                rule="R7_ARITHMETIC",
                status="OK",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="Money columns add up",
                explanation="Cumulative totals match the reported quarterly figures exactly.",
                evidence=money_pages,
                inputs={},
            )
        ]
    return findings


def _add_quarter(day: date) -> date:
    """Next calendar quarter end after a Mar/Jun/Sep/Dec date."""
    month = day.month + 3
    year = day.year
    if month > 12:
        month -= 12
        year += 1
    return date(year, month, 31 if month in (3, 12) else 30)


def r8_missing_quarter(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]:
    """Every quarter between the first and last filing must exist."""
    del settings_view
    ends = sorted({q.filing.quarter_end for q in project.quarters if q.filing.quarter_end is not None})
    if len(ends) < 2:
        return [
            Finding(
                finding_id="",
                rule="R8_MISSING_QUARTER",
                status="CANT_VERIFY",
                severity="info",
                quarter_end=None,
                block_label=None,
                title="Fewer than two dated filings",
                explanation="A gap needs two dated filings to span.",
                evidence=[],
                inputs={},
            )
        ]
    expected = []
    cursor = ends[0]
    while cursor <= ends[-1]:
        expected.append(cursor)
        cursor = _add_quarter(cursor)
    missing = [day for day in expected if day not in ends]
    if missing:
        span = len(expected) - 1
        quarters_word = "quarter" if span == 1 else "quarters"
        return [
            Finding(
                finding_id="",
                rule="R8_MISSING_QUARTER",
                status="CANT_VERIFY",
                severity="info",
                quarter_end=ends[-1],
                block_label=None,
                title="Can't check for missing quarters",
                explanation=(
                    f"These filings run from {format_day(ends[0])} to {format_day(ends[-1])} "
                    f"({span} {quarters_word} apart). Add the quarters in between to check them."
                ),
                evidence=[],
                inputs={"missing": str(len(missing))},
            )
        ]
    return [
        Finding(
            finding_id="",
            rule="R8_MISSING_QUARTER",
            status="OK",
            severity="info",
            quarter_end=None,
            block_label=None,
            title="No quarter missing",
            explanation="Every quarter between the first and last filing has a report.",
            evidence=[],
            inputs={},
        )
    ]


def r9_video_vs_claim(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]:
    """The site video must show about as many floors as the filing claims.

    One direction only: floors only go up, so a video showing more than the
    latest claim is consistent with later progress, never a flag. A video
    never flags on its own (Nov 2 policy: frame agreement is not accuracy):
    a range sitting 2+ floors under the claim asks a question instead.
    Anything else CANT_VERIFY. No video, no claim, or no usable video range
    is CANT_VERIFY with the reason named.
    """
    del settings_view
    video = project.video
    if video is None:
        return []
    block = video.block_label
    dated: list[tuple[date, int, str | None]] = []
    for quarter in project.quarters:
        if quarter.filing.quarter_end is None:
            continue
        for quarter_block in quarter.filing.blocks:
            if quarter_block.block_label != block:
                continue
            slabs = claimed_slabs(quarter_block)
            if slabs is not None:
                mark = user_floors_inputs(quarter_block).get("floors_entered_by_you")
                dated.append((quarter.filing.quarter_end, slabs, mark))
    if video.lower is None or video.upper is None:
        if video.frame_counts:
            explanation = "The video frames did not agree on a floor count, so it can't be checked."
        else:
            explanation = "We could not find a building in the frames."
        return [
            Finding(
                finding_id="",
                rule="R9_VIDEO_VS_CLAIM",
                status="CANT_VERIFY",
                severity="info",
                quarter_end=None,
                block_label=block,
                title="Video gave no usable floor range",
                explanation=explanation,
                evidence=[],
                inputs={},
            )
        ]
    low, up = video.lower, video.upper
    if not dated:
        return [
            Finding(
                finding_id="",
                rule="R9_VIDEO_VS_CLAIM",
                status="CANT_VERIFY",
                severity="info",
                quarter_end=None,
                block_label=block,
                title=f"No slab claim for {block}",
                explanation=(
                    f"The filing claims no slab count for {block}, so the video has nothing to check against."
                ),
                evidence=[],
                inputs={"video_floors_lower": str(low), "video_floors_upper": str(up)},
            )
        ]
    quarter_end, claimed, entered = max(dated, key=lambda item: item[0])
    span = f"{low} to {up}" if low != up else f"{low}"
    sheet_evidence = (
        [
            Evidence(
                kind="photo",
                image_id=video.video_id,
                overlay_path=video.contact_sheet,
                note="Contact sheet of the clearest frames with counted rows",
            )
        ]
        if video.contact_sheet
        else []
    )
    base_inputs = {
        "video_floors_lower": str(low),
        "video_floors_upper": str(up),
        "claimed_slabs": str(claimed),
    }
    if entered is not None:
        base_inputs["floors_entered_by_you"] = entered
    if up <= claimed - 2:
        gap = claimed - up
        return [
            Finding(
                finding_id="",
                rule="R9_VIDEO_VS_CLAIM",
                status="CANT_VERIFY",
                severity="question",
                quarter_end=quarter_end,
                block_label=block,
                title=f"Video shows about {up} floors, report claims {claimed} slabs",
                explanation=(
                    f"Your video shows about {span} floors. The filing for the quarter "
                    f"ending {quarter_end.isoformat()} says {claimed} slabs were done. "
                    "A question to ask the builder or RERA, not proof of wrongdoing."
                ),
                evidence=sheet_evidence,
                inputs={**base_inputs, "gap_floors": str(gap)},
            )
        ]
    if low > claimed:
        return [
            Finding(
                finding_id="",
                rule="R9_VIDEO_VS_CLAIM",
                status="OK",
                severity="info",
                quarter_end=quarter_end,
                block_label=block,
                title=f"Video covers the {claimed} claimed slabs",
                explanation=(
                    f"Your video shows about {span} floors; the filing for the quarter "
                    f"ending {quarter_end.isoformat()} says {claimed} slabs were done. "
                    "That is consistent with later progress."
                ),
                evidence=sheet_evidence,
                inputs=base_inputs,
            )
        ]
    return [
        Finding(
            finding_id="",
            rule="R9_VIDEO_VS_CLAIM",
            status="CANT_VERIFY",
            severity="info",
            quarter_end=quarter_end,
            block_label=block,
            title=f"Video range overlaps the {claimed} claimed slabs",
            explanation=(
                f"Your video shows about {span} floors; the filing for the quarter "
                f"ending {quarter_end.isoformat()} says {claimed} slabs were done. "
                "The range overlaps the claim, so this check can't verify it."
            ),
            evidence=sheet_evidence,
            inputs=base_inputs,
        )
    ]
