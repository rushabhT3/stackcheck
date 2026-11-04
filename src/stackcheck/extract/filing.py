"""QPR text into a QuarterFiling. The model copies; this module validates and merges."""

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from stackcheck.errors import IngestError, ModelError
from stackcheck.extract.prompts import load_prompt
from stackcheck.ingest.pdf import render_page
from stackcheck.llm.base import ImagePart, ModelClient, ModelReply
from stackcheck.llm.structured import ask_json
from stackcheck.models import BlockProgress, IngestedDoc, PageRef, QuarterFiling

logger = logging.getLogger(__name__)

MAX_TEXT_PAGES = 60
SCAN_BATCH_PAGES = 8
MAX_TABLE_PAGES = 4

TABLE_HEADINGS = ("construction progress", "financial progress")

MONEY_LABELS = (
    "amount received",
    "actual cost incurred",
    "net amount",
    "estimated cost",
    "cumulative fund",
    "cumulative expenditure",
    "total expenditure",
)

IMAGE_STUB = "(page content in the attached image for this page; read every value from the image)"

GARBLE_TOKEN = re.compile(r"o/[oaA]\b|/[oO]\b")
GARBLE_TOKEN_MIN = 3
DROPPED_LAYER_DPI = 200
MAX_TEXT_IMAGES = 8


def _edit_within(first: str, second: str, limit: int) -> bool:
    """True when the edit distance fits; inputs are short single words."""
    if abs(len(first) - len(second)) > limit:
        return False
    previous = list(range(len(second) + 1))
    for i, char in enumerate(first, 1):
        current = [i]
        for j, other in enumerate(second, 1):
            cost = 0 if char == other else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1] <= limit


def _has_fuzzy_words(text: str, phrase: str, limit: int = 2) -> bool:
    """Every word of phrase has a close word in text (OCR letter noise)."""
    words = re.findall(r"[a-z]+", text.lower())
    return all(any(_edit_within(word, target, limit) for word in words) for target in phrase.split())


def _table_pages(doc: IngestedDoc) -> list[int]:
    """Pages holding the progress tables, at most 4.

    Exact headings first; OCR-garbled headings match fuzzily; a page with
    two money labels counts as the money page even when its heading is gone.
    """
    found = []
    for item in doc.pages:
        lowered = item.text.lower()
        if any(heading in lowered for heading in TABLE_HEADINGS):
            found.append(item.page)
            continue
        if _has_fuzzy_words(item.text, "construction progress") or _has_fuzzy_words(
            item.text, "financial progress"
        ):
            found.append(item.page)
            continue
        if sum(1 for label in MONEY_LABELS if label in lowered) >= 2:
            found.append(item.page)
    return found[:MAX_TABLE_PAGES]


def normalize_activity_name(name: str) -> str:
    """Strip and collapse whitespace so the same activity merges across batches."""
    return " ".join(name.split())


def _page_kind(text: str) -> str | None:
    """Activity or money page by its heading; None for cover, narrative, certificates."""
    if _has_fuzzy_words(text, "percentage of actual work done"):
        return "activity"
    lowered = text.lower()
    if sum(1 for label in MONEY_LABELS if label in lowered) >= 2:
        return "money"
    return None


def _digit_letter_token(token: str) -> bool:
    """A number with OCR letter swaps (s,97,,46,O93,OO); clean money has none."""
    return any(char.isdigit() for char in token) and any(char.isalpha() for char in token) and "," in token


def _layer_garbled(text: str) -> str | None:
    """Reason when a scanned page's text layer is OCR scramble, else None.

    Two scramble families: percent tokens with o/o fragments (70Ao/o,
    700o/o, 101o/a) and money tokens with letter swaps (s,97,,46,O93,OO).
    Clean tokens (100%, and/or, Flat/Premises, 1,81,50,755) match neither.
    """
    tokens = text.split()
    slash = [
        token for token in tokens if any(char.isdigit() for char in token) and GARBLE_TOKEN.search(token)
    ]
    if len(slash) >= GARBLE_TOKEN_MIN:
        return f"slash-o garble: {len(slash)} tokens e.g. {', '.join(slash[:3])}"
    letters = [token for token in tokens if _digit_letter_token(token)]
    if len(letters) >= GARBLE_TOKEN_MIN:
        return f"digit-letter garble: {len(letters)} tokens e.g. {', '.join(letters[:3])}"
    return None


def _select_images(table_pages: set[int], dropped: dict[int, tuple[str, str]]) -> tuple[set[int], list[int]]:
    """Cap page images at 8: dropped money first, then table, then activity.

    Cuts fall on the highest activity page numbers first. Returns the
    selected pages and the cut pages in ascending order.
    """
    money = sorted(n for n, (kind, _reason) in dropped.items() if kind == "money")
    activity = sorted(n for n, (kind, _reason) in dropped.items() if kind != "money")
    ordered = money + sorted(table_pages) + activity
    if len(ordered) <= MAX_TEXT_IMAGES:
        return set(ordered), []
    return set(ordered[:MAX_TEXT_IMAGES]), sorted(ordered[MAX_TEXT_IMAGES:])


def _keep_page(page: int, page_count: int) -> bool:
    return 1 <= page <= page_count


def _clean_ref(ref: PageRef | None, page_count: int) -> PageRef | None:
    if ref is None or not _keep_page(ref.page, page_count):
        return None
    return ref


def _first(*values: object) -> object:
    for value in values:
        if value is not None:
            return value
    return None


def _merge(batches: list[QuarterFiling]) -> QuarterFiling:
    """Scalar fields: first non-null in page order. Lists: concatenated, deduplicated."""
    merged = QuarterFiling(doc_id=batches[0].doc_id)
    for field in (
        "project_reg_no",
        "project_name",
        "promoter_name",
        "state",
        "quarter_end",
        "filed_on",
        "has_architect_certificate",
        "has_engineer_certificate",
        "has_ca_certificate",
        "photo_section_present",
    ):
        setattr(merged, field, _first(*(getattr(f, field) for f in batches)))
    for field in (
        "received_this_quarter",
        "spent_this_quarter",
        "cumulative_received",
        "cumulative_spent",
        "estimated_total_cost",
        "net_balance",
        "currency_unit",
    ):
        setattr(merged.financials, field, _first(*(getattr(f.financials, field) for f in batches)))
    merged.financials.ref = _first(*(f.financials.ref for f in batches if f.financials.ref is not None))
    for filing in batches:
        for key, unit in (filing.financials.unit_overrides or {}).items():
            merged.financials.unit_overrides.setdefault(key, unit)
    seen_blocks: set[str] = set()
    for filing in batches:
        for block in filing.blocks:
            if block.block_label not in seen_blocks:
                seen_blocks.add(block.block_label)
                merged.blocks.append(block)
    seen_activities: set[str] = set()
    for filing in batches:
        for activity in filing.activities:
            key = normalize_activity_name(activity.name)
            if key not in seen_activities:
                seen_activities.add(key)
                merged.activities.append(activity)
    seen_notes: set[str] = set()
    for filing in batches:
        for note in filing.notes:
            if note not in seen_notes:
                seen_notes.add(note)
                merged.notes.append(note)
    for filing in batches:
        for key, ref in filing.refs.items():
            merged.refs.setdefault(key, ref)
    return merged


VALUE_NOT_FOUND_NOTE = "value not found on cited page"

SCANNED_NOTE = "scanned page text unreliable, values kept without page match"

_CODE_SHA_CACHE: dict[str, str] = {}


def code_sha_of(path: Path | str) -> str:
    """Live sha256 of a source file, cached per path. Pins the model cache key."""
    key = str(path)
    cached = _CODE_SHA_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    _CODE_SHA_CACHE[key] = digest
    return digest


def _filing_code_sha() -> str:
    return code_sha_of(Path(__file__))


class _CodeShaClient:
    """Wraps a ModelClient so every ask_json call carries this file's sha.

    `ask_json` builds the `complete` call itself, so the sha is injected here
    instead of at each call site; both the first attempt and the repair share it.
    """

    def __init__(self, inner: ModelClient) -> None:
        self._inner = inner

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model(self) -> str:
        return self._inner.model

    async def complete(
        self,
        *,
        system: str,
        text: str,
        images: Sequence[ImagePart] = (),
        purpose: str,
        check_id: str | None,
        **kwargs: object,
    ) -> ModelReply:
        return await self._inner.complete(
            system=system,
            text=text,
            images=images,
            purpose=purpose,
            check_id=check_id,
            code_sha=_filing_code_sha(),
            **kwargs,  # type: ignore[arg-type]
        )


def _normalize_number(value: object) -> str:
    """Strip grouping and padding so 1,81,50,755 matches 18150755 and 40.0 matches 40%."""
    text = str(value).replace(",", "").replace(" ", "")
    while text.endswith("/-"):
        text = text[:-2]
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


ZERO_MARKERS = ("nocollection", "nil", "=00")

MONEY_FALLBACK_LABELS = {
    "received_this_quarter": r"Amount\s+Received\s+during\s+the\s+quarter",
    "spent_this_quarter": r"Actual\s+Cost\s+Incurred\s+during\s+the\s+quarter",
    "cumulative_received": (
        r"Cumulative\s+Fund\s+Collected" r"(?:\s+till\s+the\s+end\s+of\s+(?:the\s+)?quarter)?"
    ),
    "cumulative_spent": (
        r"Cumulative\s+Expenditure(?:\s+done)?" r"(?:\s+till\s+the\s+end\s+of\s+(?:the\s+)?quarter)?"
    ),
    "net_balance": r"Net\s+Amount\s+at\s+(?:the\s+)?end\s+of\s+the\s+quarter",
}

_MONEY_FALLBACK_RES = {
    field: re.compile(pattern, re.IGNORECASE) for field, pattern in MONEY_FALLBACK_LABELS.items()
}
_MONEY_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{2,3})+")
_FALLBACK_WINDOW = 150
_RUN_CHAR_RE = re.compile(r"[0-9A-Za-z,.]")


def _clean_run_digits(text: str, start: int, end: int) -> str | None:
    """Digits of the maximal surrounding number run, or None when corrupted.

    Letters interleaved in the digit run ("14,9a,74,357.OO") mean the OCR
    layer merged or misread glyphs, so the matched fragment ("74357") is not
    a trustworthy number. A clean run ("8,60,86,438.OO") keeps its digits.
    """
    left = start
    while left > 0 and _RUN_CHAR_RE.match(text[left - 1]):
        left -= 1
    right = end
    while right < len(text) and _RUN_CHAR_RE.match(text[right]):
        right += 1
    denoised = "".join(ch for ch in text[left:right] if not ch.isalpha() and ch != " ")
    integer = denoised.lstrip(".").split(".")[0].replace(",", "")
    candidate = "".join(ch for ch in text[start:end] if ch.isdigit())
    if not integer or integer != candidate:
        return None
    return candidate


def _fallback_money(field: str, texts: dict[int, str]) -> tuple[Decimal | None, int | None]:
    """Section-IX label read: first clean Indian-grouped number after the label wins."""
    pattern = _MONEY_FALLBACK_RES.get(field)
    if pattern is None:
        return None, None
    for page in sorted(texts):
        match = pattern.search(texts[page])
        if match is None:
            continue
        window = texts[page][match.end() : match.end() + _FALLBACK_WINDOW]
        for candidate in _MONEY_NUMBER_RE.finditer(window):
            digits = _clean_run_digits(window, candidate.start(), candidate.end())
            if digits is not None and len(digits) >= 4:
                return Decimal(digits), page
    return None, None


def _normalize_block_label(label: str | None) -> str | None:
    """One key per block, mirroring the R4 rule: 'Block A' and 'BLOCK-A' are 'A'."""
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


def _value_on_page(value: object, page_text: str | None) -> bool:
    if page_text is None:
        return False
    needle = _normalize_number(value)
    if not needle:
        return False
    haystack = page_text.replace(",", "").replace(" ", "")
    if re.search(r"(?<!\d)" + re.escape(needle) + r"(?!\d)", haystack):
        return True
    return needle == "0" and any(marker in haystack.lower() for marker in ZERO_MARKERS)


def _post_process(
    filing: QuarterFiling,
    doc: IngestedDoc,
    image_only_pages: frozenset[int] | set[int] = frozenset(),
) -> QuarterFiling:
    """Fix what the model cannot be trusted with: ids, page ranges, name shapes, invented numbers.

    Pages in image_only_pages were sent to the reader as images instead of
    text, so values citing them skip the text match and are kept as read.
    """
    doc_id = doc.source.doc_id
    # Scanned PDFs render OCR text too unreliable to verify against, so a page-text
    # mismatch keeps the model value instead of nulling it. One note covers the filing.
    is_scanned = doc.is_scanned
    scanned_mismatches = 0
    page_count = doc.source.page_count
    texts = {item.page: item.text for item in doc.pages}
    filing.doc_id = doc_id
    filing.refs = {key: ref for key, ref in filing.refs.items() if _keep_page(ref.page, page_count)}
    for block in filing.blocks:
        block.ref = _clean_ref(block.ref, page_count)
        normalized = _normalize_block_label(block.block_label)
        if normalized is not None:
            block.block_label = normalized
    merged_blocks: dict[str, BlockProgress] = {}
    for block in filing.blocks:
        key = block.block_label
        if key not in merged_blocks:
            merged_blocks[key] = block
            continue
        keep = merged_blocks[key]
        if keep.floors_above_ground_sanctioned is None:
            keep.floors_above_ground_sanctioned = block.floors_above_ground_sanctioned
        if keep.slabs_claimed_count is None:
            keep.slabs_claimed_count = block.slabs_claimed_count
        if keep.slabs_claimed_percent is None:
            keep.slabs_claimed_percent = block.slabs_claimed_percent
        if keep.ref is None:
            keep.ref = block.ref
    filing.blocks = list(merged_blocks.values())
    labels_by_page: dict[int, list[str]] = {}
    for block in filing.blocks:
        if block.ref is not None:
            labels_by_page.setdefault(block.ref.page, []).append(block.block_label)
    page_to_block = {page: labels[0] if len(labels) == 1 else None for page, labels in labels_by_page.items()}
    for activity in filing.activities:
        activity.name = normalize_activity_name(activity.name)
        activity.ref = _clean_ref(activity.ref, page_count)
        if activity.block_label is None and activity.ref is not None:
            activity.block_label = page_to_block.get(activity.ref.page)
        activity.block_label = _normalize_block_label(activity.block_label)
    filing.financials.ref = _clean_ref(filing.financials.ref, page_count)
    filing.notes = filing.notes[:5]

    def check(value: object, ref: PageRef | None) -> object:
        nonlocal scanned_mismatches
        if value is None:
            return None
        if ref is not None and ref.page in image_only_pages:
            return value
        page_text = texts.get(ref.page) if ref is not None else None
        if page_text is not None and _value_on_page(value, page_text):
            return value
        if is_scanned:
            scanned_mismatches += 1
            return value
        filing.notes.append(VALUE_NOT_FOUND_NOTE)
        return None

    for block in filing.blocks:
        block.floors_above_ground_sanctioned = check(block.floors_above_ground_sanctioned, block.ref)
        block.slabs_claimed_count = check(block.slabs_claimed_count, block.ref)
        block.slabs_claimed_percent = check(block.slabs_claimed_percent, block.ref)
    for activity in filing.activities:
        activity.percent_done = check(activity.percent_done, activity.ref)
    for field in (
        "received_this_quarter",
        "spent_this_quarter",
        "cumulative_received",
        "cumulative_spent",
        "estimated_total_cost",
        "net_balance",
    ):
        value = getattr(filing.financials, field)
        ref = filing.financials.ref
        if ref is not None and ref.page in image_only_pages:
            continue
        page_text = texts.get(ref.page) if ref is not None else None
        if value is not None and page_text is not None and _value_on_page(value, page_text):
            continue
        if value is not None and is_scanned:
            scanned_mismatches += 1
            continue
        recovered, found_page = _fallback_money(field, texts)
        if recovered is not None:
            setattr(filing.financials, field, recovered)
            if filing.financials.ref is None and found_page is not None:
                filing.financials.ref = PageRef(doc_id=doc_id, page=found_page)
            continue
        if value is not None:
            filing.notes.append(VALUE_NOT_FOUND_NOTE)
        setattr(filing.financials, field, None)
    if is_scanned and scanned_mismatches and SCANNED_NOTE not in filing.notes:
        filing.notes.append(SCANNED_NOTE)
    unmatched = sum(1 for note in filing.notes if note == VALUE_NOT_FOUND_NOTE)
    if unmatched == 1:
        filing.notes = [note for note in filing.notes if note != VALUE_NOT_FOUND_NOTE]
        filing.notes.append("1 value could not be matched on the cited page")
    elif unmatched > 1:
        filing.notes = [note for note in filing.notes if note != VALUE_NOT_FOUND_NOTE]
        filing.notes.append(f"{unmatched} values could not be matched on the cited page")
    for ref in (
        [block.ref for block in filing.blocks]
        + [activity.ref for activity in filing.activities]
        + [filing.financials.ref]
        + list(filing.refs.values())
    ):
        if ref is not None:
            ref.doc_id = doc_id
    return filing


def _reader_sentence(exc: ModelError) -> str:
    """One plain sentence for a reader failure. The raw error stays in the log only."""
    message = str(exc).lower()
    if "too many" in message and "image" in message:
        return (
            "This filing has more pages than the reader can take at once. "
            "Upload it in two parts, or try fewer pages."
        )
    if "timeout" in message or "timed out" in message:
        return "The reader timed out on this filing. Try again, or upload the PDF."
    if "429" in message or "rate limit" in message or "rate-limit" in message:
        return "The reader is busy right now. Try again in a few minutes."
    return "The reader couldn't read this filing. Try uploading the PDF instead."


async def extract_filing(
    doc: IngestedDoc,
    client: ModelClient,
    *,
    check_id: str | None,
    pdf_path: Path | None = None,
) -> QuarterFiling:
    """Read one filing. Scanned PDFs need `pdf_path` so pages can be rendered."""
    try:
        return await _extract_filing(doc, client, check_id=check_id, pdf_path=pdf_path)
    except ModelError as exc:
        logger.error(
            "Read failed for %s doc=%s url=%s: %s: %s",
            check_id,
            doc.source.doc_id,
            doc.source.url,
            type(exc).__name__,
            exc,
        )
        raise IngestError(_reader_sentence(exc)) from exc


async def _extract_filing(
    doc: IngestedDoc,
    client: ModelClient,
    *,
    check_id: str | None,
    pdf_path: Path | None = None,
) -> QuarterFiling:
    """Read one filing. Scanned PDFs need `pdf_path` so pages can be rendered."""
    prompt = load_prompt("filing_v5")
    reader = _CodeShaClient(client)
    if not doc.is_scanned:
        table_pages = set(_table_pages(doc)) if pdf_path is not None else set()
        dropped: dict[int, tuple[str, str]] = {}
        if pdf_path is not None:
            for item in doc.pages[:MAX_TEXT_PAGES]:
                if item.page in table_pages:
                    continue
                kind = _page_kind(item.text)
                if kind is None:
                    continue
                reason = _layer_garbled(item.text)
                if reason is None:
                    logger.info(
                        "extract_filing layer doc=%s page %d %s layer kept: clean",
                        doc.source.doc_id,
                        item.page,
                        kind,
                    )
                else:
                    dropped[item.page] = (kind, reason)
                    logger.info(
                        "extract_filing layer doc=%s page %d %s layer dropped: %s",
                        doc.source.doc_id,
                        item.page,
                        kind,
                        reason,
                    )
        image_pages, cut = _select_images(table_pages, dropped)
        for page in cut:
            kind, _reason = dropped.get(page, ("table", ""))
            logger.info(
                "extract_filing layer doc=%s page %d %s image cut: over %d-image cap",
                doc.source.doc_id,
                page,
                kind,
                MAX_TEXT_IMAGES,
            )
        parts = []
        for item in doc.pages[:MAX_TEXT_PAGES]:
            if item.page in image_pages:
                parts.append(f"[[PAGE {item.page}]]\n{IMAGE_STUB}")
            else:
                parts.append(f"[[PAGE {item.page}]]\n{item.text}")
        pages_text = "\n".join(parts)
        text = f"Document id: {doc.source.doc_id}\n" + prompt.fill(pages=pages_text)
        images: list[ImagePart] = []
        if pdf_path is not None and image_pages:
            renders = await asyncio.gather(
                *(
                    asyncio.to_thread(render_page, pdf_path, n, DROPPED_LAYER_DPI if n in dropped else 110)
                    for n in sorted(image_pages)
                )
            )
            images = [ImagePart(data=raw, mime="image/png") for raw in renders]
        if images:
            text += (
                f"\nPages {sorted(image_pages)} are attached as images instead of text; "
                "read every value on those pages from the image with the same page number. "
                "When the text and the page image disagree about which row "
                "a value belongs to, trust the image."
            )
        try:
            import io as _io_mod

            from PIL import Image as _PilImage

            _dims = []
            for _img in images:
                with _PilImage.open(_io_mod.BytesIO(_img.data)) as _im:
                    _dims.append(f"{_im.width}x{_im.height}")
        except Exception:
            _dims = ["unknown"]
        logger.info(
            "extract_filing text doc=%s image_pages=%s text_pages=%s markers=%s text_len=%d image_dims=%s",
            doc.source.doc_id,
            sorted(image_pages),
            [item.page for item in doc.pages[:MAX_TEXT_PAGES] if item.page not in image_pages],
            [int(n) for n in re.findall(r"\[\[PAGE (\d+)\]\]", text)],
            len(text),
            _dims,
        )
        filing, _ = await ask_json(
            reader,
            prompt_name="filing_v5",
            system=prompt.system,
            text=text,
            schema=QuarterFiling,
            images=images,
            purpose="extract_filing",
            check_id=check_id,
        )
        return _post_process(filing, doc, image_only_pages=image_pages)
    if pdf_path is None:
        raise IngestError("scanned PDF needs pdf_path for page renders")
    filings: list[QuarterFiling] = []
    numbers = [item.page for item in doc.pages]
    for start in range(0, len(numbers), SCAN_BATCH_PAGES):
        batch = numbers[start : start + SCAN_BATCH_PAGES]
        renders = await asyncio.gather(*(asyncio.to_thread(render_page, pdf_path, n) for n in batch))
        images = [ImagePart(data=raw, mime="image/png") for raw in renders]
        stubs = "\n".join(f"[[PAGE {n}]]\n{IMAGE_STUB}" for n in batch)
        text = f"Document id: {doc.source.doc_id}\n" + prompt.fill(pages=stubs)
        filing, _ = await ask_json(
            reader,
            prompt_name="filing_v5",
            system=prompt.system,
            text=text,
            schema=QuarterFiling,
            images=images,
            purpose="extract_filing",
            check_id=check_id,
        )
        filings.append(filing)
    return _post_process(_merge(filings), doc)
