"""Quarterly report web pages (Bihar RERA QPR-Show.aspx). Deterministic, no model calls.

From the Apr-Jun 2025 quarter on, Bihar publishes quarterly reports as web pages
instead of PDFs. A page carries the same facts a PDF filing would: the quarter,
work items with percent complete, financials, booked inventory and site photos
served as JPEG files under /QPRimages/. The uploader supplies each page URL;
nothing here lists or crawls the portal.
"""

import hashlib
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urljoin, urlsplit

from PIL import Image
from pydantic import BaseModel, Field

from stackcheck.errors import IngestError
from stackcheck.models import (
    ActivityProgress,
    BlockProgress,
    ExtractedImage,
    Financials,
    IngestedDoc,
    PageRef,
    PageText,
    PhotoFacts,
    PhotoKind,
    QuarterFiling,
    SourceDoc,
)

logger = logging.getLogger(__name__)

QPR_HOST = "rera.bihar.gov.in"
_QPR_PATH = "/QPR/QPR-Show.aspx"
_QPR_PARAMS = ("pr", "ld", "pj", "f", "q")
# Quarter number in the f=YYYY-YYYY fiscal year to (end month, end day, start month, start day, year offset).
_QUARTERS = {"1": (6, 30, 4, 1, 0), "2": (9, 30, 7, 1, 0), "3": (12, 31, 10, 1, 0), "4": (3, 31, 1, 1, 1)}
_FY_RE = re.compile(r"^(\d{4})-(\d{4})$")
_G_FLOORS_RE = re.compile(r"G\s*\+\s*(\d+)", re.IGNORECASE)
_PHOTO_RE = re.compile(
    r"""((?:https://rera\.bihar\.gov\.in)?/QPRimages/([A-Za-z0-9]+)_(\d{12})\.(?:jpe?g|png))""",
    re.IGNORECASE,
)
_STAMP_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$")
_SLOT_IMAGE_RE = re.compile(r".+-qpr\d+-([A-Za-z0-9]+)$")
_SERIAL_RE = re.compile(r"^\d+[.\)]?$")
_DATE_FORMATS = ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")
MIN_SIDE_PX = 200


class QprPhoto(BaseModel):
    slot: str
    url: str
    uploaded_on: date | None = None


class QprActivity(BaseModel):
    name: str
    percent: float | None = None


class QprPage(BaseModel):
    project_reg_no: str | None = None
    project_name: str | None = None
    promoter_name: str | None = None
    quarter_end: date | None = None
    quarter_start: date | None = None
    filed_on: date | None = None
    sanctioned_floors: int | None = None
    slabs_percent: float | None = None
    activities: list[QprActivity] = Field(default_factory=list)
    money: dict[str, Decimal] = Field(default_factory=dict)
    money_units: dict[str, str] = Field(default_factory=dict)
    booked_units: int | None = None
    photos: list[QprPhoto] = Field(default_factory=list)


def is_qpr_url(url: str) -> bool:
    """True for a user-supplied QPR page URL on the Bihar portal only."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or (parts.hostname or "").lower() != QPR_HOST:
        return False
    if parts.path != _QPR_PATH:
        return False
    params = parse_qs(parts.query)
    return all(params.get(name) for name in _QPR_PARAMS)


def qpr_quarter(fiscal: str, quarter: str) -> tuple[date, date] | None:
    """(quarter_end, quarter_start) for f=YYYY-YYYY and q=1..4, else None."""
    match = _FY_RE.match(fiscal.strip())
    spec = _QUARTERS.get(quarter.strip())
    if match is None or spec is None:
        return None
    year = int(match.group(1)) + spec[4]
    end_month, end_day, start_month, start_day, _ = spec
    try:
        return date(year, end_month, end_day), date(year, start_month, start_day)
    except ValueError:
        return None


def parse_upload_time(filename: str) -> date | None:
    """Upload date from a QPR image name stamp (ddmmyyHHMMSS, day first)."""
    stem = Path(filename).stem
    match = _STAMP_RE.search(stem.split("_")[-1])
    if match is None:
        return None
    day, month, year, _, _, _ = (int(part) for part in match.groups())
    try:
        return date(2000 + year, month, day)
    except ValueError:
        return None


def slot_kind(slot: str) -> PhotoKind | None:
    """Photo kind from a QPR image slot name, else None to keep the model kind."""
    if slot == "FrontElevation":
        return "elevation"
    if slot in ("fuInteriorWorks", "fuCommonAreas", "fuDisplayBoard"):
        return "site_other"
    return None


def parse_qpr_slot(image_id: str) -> str | None:
    """Slot name encoded in a QPR image id, else None for PDF images."""
    match = _SLOT_IMAGE_RE.match(image_id)
    return match.group(1) if match else None


def parse_money(text: str) -> Decimal | None:
    """Rupees from a QPR money cell. Indian grouping, =00 and /- tolerated."""
    low = text.strip().lower()
    if low in ("", "-", "n/a", "na"):
        return None
    if low in ("no collection", "nil", "00=00", "00"):
        return Decimal(0)
    core = re.sub(r"(rs\.?|inr|₹)", "", low)
    core = core.replace(",", "").replace(" ", "")
    while core.endswith("/-") or core.endswith("=00"):
        core = core[: -len("/-")] if core.endswith("/-") else core[: -len("=00")]
    if not core:
        return None
    try:
        return Decimal(core)
    except InvalidOperation:
        return None


def _parse_day(text: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


class _QprTableParser(HTMLParser):
    """Tables as row lists, visible text in order, image sources in order."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._rows: list[list[str]] | None = None
        self._cells: list[str] | None = None
        self._cell_text: list[str] | None = None
        self.text_parts: list[str] = []
        self.img_srcs: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip = True
        elif tag == "table":
            self._rows = []
            self.tables.append(self._rows)
        elif tag == "tr" and self._rows is not None:
            self._cells = []
            self._rows.append(self._cells)
        elif tag in ("td", "th") and self._cells is not None:
            self._cell_text = []
        elif tag == "img":
            for name, value in attrs:
                if name == "src" and value:
                    self.img_srcs.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("td", "th") and self._cells is not None and self._cell_text is not None:
            self._cells.append(" ".join("".join(self._cell_text).split()))
            self._cell_text = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        cleaned = " ".join(data.split())
        if self._cell_text is not None:
            self._cell_text.append(data)
        if cleaned:
            self.text_parts.append(cleaned)


def _label_value(rows: list[list[str]], *labels: str) -> str | None:
    for row in rows:
        for index, cell in enumerate(row):
            if cell.strip().lower() in labels and index + 1 < len(row) and row[index + 1].strip():
                return row[index + 1].strip()
    return None


def _looks_like_reg_no(value: str) -> bool:
    return re.fullmatch(r"[A-Z0-9/\-]{6,}", value.strip().upper()) is not None


_MONEY_FIELD_ORDER = (
    "cumulative_received",
    "cumulative_spent",
    "received_this_quarter",
    "spent_this_quarter",
    "estimated_total_cost",
)
_MONEY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cumulative_received": ("receiv", "collect"),
    "cumulative_spent": ("spent", "expenditure", "utili"),
    "received_this_quarter": ("receiv", "collect"),
    "spent_this_quarter": ("spent", "expenditure", "utili"),
    "estimated_total_cost": ("cost",),
}


def _row_money(row: list[str]) -> tuple[str, Decimal | None] | None:
    text = " ".join(row).lower()
    if not any(char.isdigit() for char in text):
        return None
    money_cell = next((cell for cell in reversed(row) if any(char.isdigit() for char in cell)), "")
    for field in _MONEY_FIELD_ORDER:
        keywords = _MONEY_KEYWORDS[field]
        if field.startswith("cumulative"):
            if "cumulative" not in text or not any(k in text for k in keywords):
                continue
        elif field == "estimated_total_cost":
            if "estimat" not in text or "cost" not in text:
                continue
        else:
            if "cumulative" in text or "estimat" in text:
                continue
            if not any(k in text for k in keywords):
                continue
        value = parse_money(money_cell)
        if value is None and any(char.isdigit() for char in money_cell):
            continue
        return field, value
    return None


def _row_unit(row: list[str]) -> str | None:
    text = " ".join(row).lower()
    if "crore" in text:
        return "INR_CRORE"
    if "lakh" in text:
        return "INR_LAKH"
    return None


def _work_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    for table in tables:
        for row in table:
            lowered = [cell.lower() for cell in row]
            if any("percent" in cell for cell in lowered) and any(
                "expect" in cell or "date" in cell or "completion" in cell for cell in lowered
            ):
                return table
    return None


def _classify_work_row(row: list[str]) -> QprActivity | None:
    cells = [cell for cell in row if cell.strip()]
    if len(cells) < 2:
        return None
    rest = cells
    serial = ""
    if _SERIAL_RE.match(cells[0]) and len(cells) >= 3:
        serial = cells[0].rstrip(".)")
        rest = cells[1:]
    percent: float | None = None
    item = ""
    for cell in rest:
        plain = cell.strip()
        if _parse_day(plain) is not None:
            continue
        number = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%?", plain)
        if number is not None and percent is None:
            percent = float(number.group(1))
            continue
        if len(plain) > len(item):
            item = plain
    if not item:
        return None
    name = " ".join(f"{serial} {item}".split()) if serial else item
    return QprActivity(name=name, percent=percent)


def parse_qpr_html(html: str, url: str) -> QprPage:
    """Parse a saved QPR page. No network, no model calls."""
    params = parse_qs(urlsplit(url).query)

    def first(name: str) -> str:
        return (params.get(name) or [""])[0]

    quarter = qpr_quarter(first("f"), first("q"))
    if quarter is None:
        raise IngestError("That quarterly report link doesn't name a valid quarter.")
    quarter_end, quarter_start = quarter
    parser = _QprTableParser()
    parser.feed(html)
    rows = [row for table in parser.tables for row in table]
    text = "\n".join(parser.text_parts)
    project_name = _label_value(rows, "project name", "name of project", "projectname")
    if project_name is None:
        title = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        project_name = " ".join(title.group(1).split()) if title else None
    reg_text = _label_value(rows, "rera registration no", "registration no", "reg. no", "registration number")
    project_reg_no = reg_text.strip() if reg_text and _looks_like_reg_no(reg_text) else (first("pr") or None)
    promoter_name = _label_value(rows, "promoter name", "name of promoter", "promoter")
    sanctioned: int | None = None
    match = _G_FLOORS_RE.search(text)
    if match is not None:
        sanctioned = int(match.group(1))
    activities: list[QprActivity] = []
    slabs_percent: float | None = None
    work = _work_table(parser.tables)
    if work is not None:
        for row in work:
            if any("percent" in cell.lower() for cell in row):
                continue
            activity = _classify_work_row(row)
            if activity is None:
                continue
            activities.append(activity)
            if "slab" in activity.name.lower() and slabs_percent is None:
                slabs_percent = activity.percent
    money: dict[str, Decimal] = {}
    money_units: dict[str, str] = {}
    for row in rows:
        found = _row_money(row)
        if found is None:
            continue
        field, value = found
        if field in money or value is None:
            continue
        money[field] = value
        unit = _row_unit(row)
        if unit is not None:
            money_units[field] = unit
    booked: int | None = None
    for row in rows:
        joined = " ".join(row).lower()
        if "book" in joined and ("invent" in joined or "unit" in joined or "flat" in joined):
            digits = re.search(r"(\d+)", " ".join(row))
            if digits is not None:
                booked = int(digits.group(1))
                break
    filed_on: date | None = None
    for row in rows:
        joined = " ".join(row).lower()
        if "submi" in joined or "filing date" in joined:
            for cell in row:
                filed_on = _parse_day(cell)
                if filed_on is not None:
                    break
        if filed_on is not None:
            break
    photos: list[QprPhoto] = []
    seen: set[str] = set()
    for src_match in _PHOTO_RE.finditer(html):
        src, slot, _stamp = src_match.groups()
        if src in seen:
            continue
        seen.add(src)
        photos.append(
            QprPhoto(
                slot=slot,
                url=urljoin(url, src),
                uploaded_on=parse_upload_time(src.rsplit("/", 1)[-1]),
            )
        )
    return QprPage(
        project_reg_no=project_reg_no,
        project_name=project_name,
        promoter_name=promoter_name,
        quarter_end=quarter_end,
        quarter_start=quarter_start,
        filed_on=filed_on,
        sanctioned_floors=sanctioned,
        slabs_percent=slabs_percent,
        activities=activities,
        money=money,
        money_units=money_units,
        booked_units=booked,
        photos=photos,
    )


def _format_day(value: date) -> str:
    return value.strftime("%d-%b-%Y")


def build_filing(page: QprPage, doc_id: str, photos_skipped: int = 0) -> QuarterFiling:
    """QPR page facts into the shared filing schema. Deterministic, no checks skipped silently."""
    ref = PageRef(doc_id=doc_id, page=1)
    notes = ["Figures read from the project's quarterly report web page, not from a PDF filing."]
    dated = sorted({photo.uploaded_on for photo in page.photos if photo.uploaded_on is not None})
    if dated:
        when = ", ".join(_format_day(day) for day in dated)
        notes.append(f"Site photos uploaded on {when} (upload time, not camera date).")
    if page.booked_units is not None:
        notes.append(f"Booked inventory: {page.booked_units} units.")
    if photos_skipped:
        notes.append(f"{photos_skipped} site photo(s) could not be downloaded.")
    units: dict[str, Literal["INR", "INR_LAKH", "INR_CRORE"]] = {}
    for field, unit in page.money_units.items():
        if field in page.money and unit in ("INR", "INR_LAKH", "INR_CRORE"):
            units[field] = unit  # type: ignore[literal-required]
    financials = Financials(
        received_this_quarter=page.money.get("received_this_quarter"),
        spent_this_quarter=page.money.get("spent_this_quarter"),
        cumulative_received=page.money.get("cumulative_received"),
        cumulative_spent=page.money.get("cumulative_spent"),
        estimated_total_cost=page.money.get("estimated_total_cost"),
        currency_unit="INR",
        unit_overrides=units,
        ref=ref,
    )
    return QuarterFiling(
        doc_id=doc_id,
        project_reg_no=page.project_reg_no,
        project_name=page.project_name,
        promoter_name=page.promoter_name,
        state="Bihar",
        quarter_end=page.quarter_end,
        filed_on=page.filed_on,
        blocks=[
            BlockProgress(
                block_label="MAIN",
                floors_above_ground_sanctioned=page.sanctioned_floors,
                slabs_claimed_percent=page.slabs_percent,
                ref=ref,
            )
        ],
        activities=[
            ActivityProgress(name=" ".join(a.name.split()), percent_done=a.percent, ref=ref)
            for a in page.activities
        ],
        financials=financials,
        notes=notes[:5],
    )


def apply_qpr_slot(facts: PhotoFacts, slot: str) -> PhotoFacts:
    """Trust the QPR image slot for the kind; QPR images carry no camera stamps.

    The upload time lives in the filing notes, never in the stamp fields, so no
    rule can mistake it for a camera date.
    """
    update: dict[str, object] = {
        "stamp_text": None,
        "stamp_date": None,
        "stamp_date_resolved": None,
        "stamp_date_status": "unreadable",
    }
    kind = slot_kind(slot)
    if kind is not None:
        update["kind"] = kind
    return facts.model_copy(update=update)  # type: ignore[arg-type]


def ingest_qpr(html_path: Path, source: SourceDoc, out_dir: Path) -> tuple[IngestedDoc, QuarterFiling]:
    """Read a fetched QPR page plus its downloaded photos into pipeline shapes."""
    try:
        html = html_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(f"Cannot open {html_path}: {exc}") from exc
    page = parse_qpr_html(html, source.url or "")
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    images: list[ExtractedImage] = []
    skipped = 0
    for index, photo in enumerate(page.photos):
        raw_path = html_path.parent / f"{source.doc_id}-qpr{index}.jpg"
        try:
            raw = raw_path.read_bytes()
        except OSError as exc:
            logger.error("QPR photo missing: %s: %s", raw_path.name, exc)
            skipped += 1
            continue
        try:
            with Image.open(BytesIO(raw)) as img:
                rgb = img.convert("RGB")
                width, height = rgb.size
                buf = BytesIO()
                rgb.save(buf, format="PNG")
                png = buf.getvalue()
        except Exception as exc:
            raise IngestError(f"QPR photo is not a readable image: {raw_path.name}") from exc
        if min(width, height) < MIN_SIDE_PX:
            logger.info("QPR photo %s too small (%dx%d), skipped", raw_path.name, width, height)
            continue
        image_id = f"{source.doc_id}-qpr{index}-{photo.slot}"
        (images_dir / f"{image_id}.png").write_bytes(png)
        images.append(
            ExtractedImage(
                image_id=image_id,
                doc_id=source.doc_id,
                page=1,
                index=index,
                path=f"images/{image_id}.png",
                width=width,
                height=height,
                sha256=hashlib.sha256(png).hexdigest(),
            )
        )
    filing = build_filing(page, source.doc_id, photos_skipped=skipped)
    full_text = "\n".join(
        [
            page.project_name or "",
            page.project_reg_no or "",
            page.promoter_name or "",
            *(
                f"{activity.name} {activity.percent if activity.percent is not None else ''}"
                for activity in page.activities
            ),
            *(f"{field} {value}" for field, value in page.money.items()),
        ]
    )
    doc = IngestedDoc(
        source=source,
        pages=[PageText(doc_id=source.doc_id, page=1, text=full_text)],
        images=images,
        is_scanned=False,
    )
    return doc, filing
