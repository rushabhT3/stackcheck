from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["OK", "FLAG", "CANT_VERIFY"]
Severity = Literal["info", "low", "medium", "high", "question"]  # D27: R9 asks, never flags
RuleCode = Literal[
    "R1_PHOTO_VS_CLAIM",
    "R2_REUSED_PHOTO",
    "R3_PHOTO_DATE",
    "R4_CLAIM_REGRESSION",
    "R5_COPY_PASTE_QUARTER",
    "R6_MONEY_VS_PROGRESS",
    "R7_ARITHMETIC",
    "R8_MISSING_QUARTER",
    "R9_VIDEO_VS_CLAIM",
]
PhotoKind = Literal["elevation", "floor_level", "site_other", "document_or_logo"]


class PageRef(BaseModel):
    doc_id: str
    page: int = Field(ge=1)  # 1-based


class SourceDoc(BaseModel):
    doc_id: str  # first 16 hex chars of sha256
    origin: Literal["url", "upload"]
    url: str | None = None
    filename: str
    sha256: str
    retrieved_at: datetime
    page_count: int


class PageText(BaseModel):
    doc_id: str
    page: int
    text: str


class ExtractedImage(BaseModel):
    image_id: str  # f"{doc_id}-p{page}-i{index}"
    doc_id: str
    page: int
    index: int
    path: str  # relative to the check folder
    width: int
    height: int
    sha256: str
    from_page_render: bool = False  # True when the page itself is the image (scanned PDFs)


class IngestedDoc(BaseModel):
    source: SourceDoc
    pages: list[PageText]
    images: list[ExtractedImage]
    is_scanned: bool  # total extracted text < 200 characters


class ActivityProgress(BaseModel):
    name: str
    percent_done: float | None = Field(default=None, ge=0, le=100)
    ref: PageRef | None = None
    block_label: str | None = None  # building this row belongs to (FIX B)


class Financials(BaseModel):
    received_this_quarter: Decimal | None = None
    spent_this_quarter: Decimal | None = None
    cumulative_received: Decimal | None = None
    cumulative_spent: Decimal | None = None
    estimated_total_cost: Decimal | None = None
    net_balance: Decimal | None = None  # closing/net amount at quarter end (FIX4)
    currency_unit: Literal["INR", "INR_LAKH", "INR_CRORE"] | None = None
    unit_overrides: dict[str, Literal["INR", "INR_LAKH", "INR_CRORE"]] = Field(default_factory=dict)
    ref: PageRef | None = None


class BlockProgress(BaseModel):
    block_label: str  # "MAIN" when the filing has one building
    floors_above_ground_sanctioned: int | None = None  # "G+4" -> 4
    floors_entered_by_user: int | None = None  # check-form floors when the filing has none
    slabs_claimed_count: int | None = None
    slabs_claimed_percent: float | None = Field(default=None, ge=0, le=100)
    ref: PageRef | None = None


class QuarterFiling(BaseModel):
    doc_id: str
    project_reg_no: str | None = None
    project_name: str | None = None
    promoter_name: str | None = None
    state: str | None = None
    quarter_end: date | None = None
    filed_on: date | None = None
    blocks: list[BlockProgress] = []
    activities: list[ActivityProgress] = []
    financials: Financials = Field(default_factory=Financials)
    has_architect_certificate: bool | None = None
    has_engineer_certificate: bool | None = None
    has_ca_certificate: bool | None = None
    photo_section_present: bool | None = None
    notes: list[str] = []
    refs: dict[str, PageRef] = {}


class PhotoFacts(BaseModel):
    image_id: str
    kind: PhotoKind
    block_label_guess: str | None = None
    stamp_text: str | None = None
    stamp_date: date | None = None
    stamp_date_resolved: date | None = None  # D24: effective date after day/month resolution
    stamp_date_status: Literal["ok", "swapped_low_confidence", "impossible", "unreadable"] | None = None
    stamp_lat: float | None = None
    stamp_lon: float | None = None
    vision_storeys_visible: int | None = None  # elevations only, includes ground floor
    top_obscured: bool | None = None
    confidence: Literal["low", "medium", "high"]
    reason: str = Field(max_length=240)


class Box(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


class Detection(BaseModel):
    prompt: str
    frame: int
    object_id: int
    box: Box
    confidence: float | None = None
    image_w: int
    image_h: int


class FloorCount(BaseModel):
    image_id: str
    status: Literal["OK", "CANT_VERIFY"]
    tier: Literal["CONFIDENT", "ESTIMATE"] | None = None  # F1: set on every OK count
    lower: int | None = None
    upper: int | None = None  # None with status OK means "at least lower" (top obscured)
    sam_rows: int | None = None
    sam_pitch_estimate: int | None = None
    vision_estimate: int | None = None
    prompts_used: list[str] = []
    rows_y: list[float] = []
    building_box: Box | None = None
    building_box_inferred: bool = False
    reason: str


class PhotoRecord(BaseModel):
    image: ExtractedImage
    facts: PhotoFacts | None = None
    dhash: str | None = None  # 16 lowercase hex chars
    floors: FloorCount | None = None
    overlay_path: str | None = None


class QuarterEvidence(BaseModel):
    source: SourceDoc
    filing: QuarterFiling
    photos: list[PhotoRecord] = []


class VideoEvidence(BaseModel):
    """One site video for the whole check. Floors only go up, so the video is
    compared against the latest claim for its block (R9)."""

    video_id: str
    block_label: str = "MAIN"
    tier: Literal["CONFIDENT", "ESTIMATE"] | None = None
    lower: int | None = None
    upper: int | None = None
    frames_used: int = 0
    frame_counts: list[int] = []
    contact_sheet: str | None = None  # "media/<video_id>-sheet.png"
    uploaded_on: date | None = None  # upload date; never a camera date
    credit: str | None = None  # bench/demo videos: "author, licence, link"


class ProjectEvidence(BaseModel):
    project_label: str
    quarters: list[QuarterEvidence]  # sorted by filing.quarter_end, None last
    video: VideoEvidence | None = None
    photos: list[PhotoRecord] = Field(default_factory=list)  # user site photos, upload order
    floors_above_ground: int | None = None  # form-entered floors, echoed for no-filing reports


class Evidence(BaseModel):
    kind: Literal["quote", "photo", "number", "computation"]
    ref: PageRef | None = None
    quote: str | None = Field(default=None, max_length=240)
    image_id: str | None = None
    overlay_path: str | None = None
    value: str | None = None
    note: str


class Finding(BaseModel):
    finding_id: str  # f"{rule}:{quarter_end or 'all'}:{block or 'all'}:{n}"
    rule: RuleCode
    status: Status
    severity: Severity  # "info" for OK and CANT_VERIFY
    quarter_end: date | None = None
    block_label: str | None = None
    title: str = Field(max_length=90)
    explanation: str = Field(max_length=400)
    evidence: list[Evidence] = []
    inputs: dict[str, str] = {}


class AgentStep(BaseModel):
    step: int
    finding_id: str
    action: Literal["retry_sam_prompt", "use_other_photo", "crop_to_building", "give_up"]
    argument: str | None = None
    rationale: str = Field(max_length=240)
    result: str


class StepStatus(BaseModel):
    name: Literal["fetch", "read_filings", "photos", "floors", "rules", "investigate", "report"]
    state: Literal["pending", "running", "done", "failed", "skipped"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    note: str | None = None


class CheckStatus(BaseModel):
    check_id: str  # "chk_" + secrets.token_hex(5)
    project_label: str
    state: Literal["queued", "running", "done", "failed"]
    steps: list[StepStatus]
    progress: float = Field(ge=0, le=1)
    error: str | None = None
    queue_position: int | None = None  # 1-based place in line while queued; user-ordered B-open-2
    created_at: datetime
    updated_at: datetime


class Verdict(BaseModel):
    headline: str  # D22: low-only flags read "No major gaps. N minor note(s)."
    flags_high: int
    flags_medium: int
    flags_low: int
    cant_verify: int
    ok: int


class CostSummary(BaseModel):
    usd_total: Decimal
    by_model: dict[str, Decimal]
    calls: int
    cache_hits: int


class Report(BaseModel):
    check_id: str
    project_label: str
    created_at: datetime
    verdict: Verdict
    findings: list[Finding]
    evidence: ProjectEvidence
    trace: list[AgentStep]
    summary_en: str | None = None
    summary_hi: str | None = None
    costs: CostSummary
    versions: dict[str, str]
    disclaimer: str
    placeholder: bool = False


DISCLAIMER = (
    "StackCheck compares public RERA filings with each other. "
    "A gap is a question to ask the builder or RERA, not proof of wrongdoing."
)
