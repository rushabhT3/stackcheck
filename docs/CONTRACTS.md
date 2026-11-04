# CONTRACTS: exact shapes, prompts, formulas and endpoints

Everything here is binding. Names, fields and types must match exactly. Section numbers are referenced from task cards.

---

## C1. Configuration (`src/stackcheck/config.py`)

```python
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STACKCHECK_",
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,  # lets tests pass meta_api_key="..." by field name
    )

    # Meta Model API
    meta_api_key: str = Field(default="", validation_alias="MODEL_API_KEY")
    meta_base_url: str = Field(default="https://api.meta.ai/v1", validation_alias="META_BASE_URL")
    reader_model: str = "muse-spark-1.3-contributor"  # public filings (D07)
    private_model: str = "muse-spark-1.3"  # anything a user uploads (D07)
    sam_model: str = "sam-3.1"
    sam_image_mode: Literal["data_url", "file_id"] = "data_url"  # set by T03 probe
    sam_prompts: str = "balcony,window"  # set by T03 spike, comma separated
    reasoning_effort: str = "low"  # "" disables the parameter (UNVERIFIED shape)

    # Reader provider switch (D06)
    extract_provider: Literal["meta", "baseten", "fireworks"] = "meta"
    baseten_api_key: str = Field(default="", validation_alias="BASETEN_API_KEY")
    baseten_base_url: str = Field(
        default="https://inference.baseten.co/v1", validation_alias="BASETEN_BASE_URL"
    )
    baseten_model: str = Field(default="meta-models/muse-spark-open-1026", validation_alias="BASETEN_MODEL")
    fireworks_api_key: str = Field(default="", validation_alias="FIREWORKS_API_KEY")
    fireworks_base_url: str = Field(
        default="https://api.fireworks.ai/inference/v1", validation_alias="FIREWORKS_BASE_URL"
    )
    fireworks_model: str = Field(
        default="accounts/fireworks/models/muse-spark-open", validation_alias="FIREWORKS_MODEL"
    )

    # App
    data_dir: Path = Path("data")
    samples_file: Path = Path("data/samples.json")  # list of {"check_id", "project_label", "headline"}
    web_dist_dir: Path = Path("web/dist")
    redact_names: bool = False  # True on the public deploy: hides project, promoter, reg no
    budget_usd: Decimal = Decimal("150")
    alert_usd: Decimal = Decimal("100")
    live: bool = False
    max_concurrent_checks: int = 2
    max_queue: int = 10
    allowed_hosts: str = (
        "rera.bihar.gov.in,up-rera.in,www.up-rera.in,rerait.telangana.gov.in,"
        "maharera.maharashtra.gov.in,rera.karnataka.gov.in,gujrera.gujarat.gov.in,haryanarera.gov.in"
    )
    stamp_required_states: str = "Bihar"
    fetch_min_interval_s: float = 5.0
    fetch_timeout_s: float = 60.0
    fetch_max_bytes: int = 30_000_000


def get_settings() -> Settings:
    return Settings()
```

`.env.example` lists every variable above with an empty value or its default. Env names: `MODEL_API_KEY`, `META_BASE_URL`, `BASETEN_API_KEY`, `BASETEN_BASE_URL`, `BASETEN_MODEL`, `FIREWORKS_API_KEY`, `FIREWORKS_BASE_URL`, `FIREWORKS_MODEL`, and `STACKCHECK_<FIELD>` for the rest.

Privacy rule (D07), in `config.py`:

```python
def model_for(origin: Literal["url", "upload"], settings: Settings) -> str:
    return settings.reader_model if origin == "url" else settings.private_model
```

When `extract_provider` is `baseten` or `fireworks`, uploads still go to Meta's `private_model`. Open-weights providers read public filings only.

---

## C2. Errors (`src/stackcheck/errors.py`)

```python
class StackCheckError(Exception): ...


class ConfigError(StackCheckError): ...


class FetchError(StackCheckError): ...


class IngestError(StackCheckError): ...


class ModelError(StackCheckError): ...  # provider failure after SDK retries


class SchemaError(StackCheckError): ...  # model output failed validation after repair


class SamParseError(StackCheckError): ...


class BudgetExceeded(StackCheckError): ...
```

---

## C3. Data models (`src/stackcheck/models.py`)

```python
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["OK", "FLAG", "CANT_VERIFY"]
Severity = Literal["info", "low", "medium", "high"]
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


class Financials(BaseModel):
    received_this_quarter: Decimal | None = None
    spent_this_quarter: Decimal | None = None
    cumulative_received: Decimal | None = None
    cumulative_spent: Decimal | None = None
    estimated_total_cost: Decimal | None = None
    currency_unit: Literal["INR", "INR_LAKH", "INR_CRORE"] | None = None
    unit_overrides: dict[str, Literal["INR", "INR_LAKH", "INR_CRORE"]] = Field(default_factory=dict)
    ref: PageRef | None = None


class BlockProgress(BaseModel):
    block_label: str  # "MAIN" when the filing has one building
    floors_above_ground_sanctioned: int | None = None  # "G+4" -> 4
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
    stamp_date_resolved: date | None = None  # D24
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
    tier: Literal["CONFIDENT", "ESTIMATE"] | None = None  # F1: every OK count carries a tier
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
    created_at: datetime
    updated_at: datetime


class Verdict(BaseModel):
    headline: str  # D22: "Gaps found" | "No gaps found in what we could check" | "Not enough data to check" | "No major gaps. N minor note(s)."
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
```

`DISCLAIMER = "StackCheck compares public RERA filings with each other. A gap is a question to ask the builder or RERA, not proof of wrongdoing."`

---

## C4. Model client layer (`src/stackcheck/llm/`)

```python
# base.py
class ImagePart(BaseModel):
    data: bytes
    mime: Literal["image/png", "image/jpeg"]


class ModelReply(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cache_hit: bool


class ModelClient(Protocol):
    provider: str
    model: str

    async def complete(
        self, *, system: str, text: str, images: Sequence[ImagePart] = (), purpose: str, check_id: str | None
    ) -> ModelReply: ...
```

Constructors take an optional SDK object so tests can pass a fake with the same method names (no HTTP mocking needed):

```python
class MetaClient:  # meta.py
    def __init__(self, settings: Settings, model: str, *, data_dir: Path, sdk: Any | None = None) -> None: ...


class OpenAICompatClient:  # openai_compat.py
    def __init__(
        self,
        provider: Literal["baseten", "fireworks"],
        base_url: str,
        api_key: str,
        model: str,
        *,
        data_dir: Path,
        sdk: Any | None = None,
    ) -> None: ...


class SamClient:  # vision/sam.py
    def __init__(self, settings: Settings, *, data_dir: Path, sdk: Any | None = None) -> None: ...
```

When `sdk` is None, each builds `openai.AsyncOpenAI(base_url=..., api_key=..., max_retries=3, timeout=120)`. `complete` and `segment_image` go through the cache (C6) and the ledger (C7) themselves.

`meta.py` (Responses API):

```python
resp = await client.responses.create(
    model=self.model,
    instructions=system,
    input=[
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": text},
                *[{"type": "input_image", "image_url": to_data_url(img)} for img in images],
            ],
        }
    ],
    **({"reasoning": {"effort": settings.reasoning_effort}} if settings.reasoning_effort else {}),
)
# text: resp.output_text ; usage: resp.usage.input_tokens, resp.usage.output_tokens,
# resp.usage.input_tokens_details.cached_tokens (treat missing as 0)
```

`openai_compat.py` (Baseten, Fireworks: Chat Completions):

```python
resp = await client.chat.completions.create(
    model=self.model,
    messages=[
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                *[{"type": "image_url", "image_url": {"url": to_data_url(img)}} for img in images],
            ],
        },
    ],
)
# text: resp.choices[0].message.content ; usage: resp.usage.prompt_tokens, resp.usage.completion_tokens
```

`structured.py`:

```python
async def ask_json(client: ModelClient, *, prompt_name: str, system: str, text: str,
                   schema: type[T], images: Sequence[ImagePart] = (), purpose: str,
                   check_id: str | None) -> tuple[T, list[ModelReply]]:
```

Behaviour:
1. Append to `text`: `"\n\nReturn only one JSON object that matches this JSON Schema:\n" + json.dumps(schema.model_json_schema())`.
2. Call `client.complete(...)`, which already caches (C6) and records cost (C7). `prompt_name` is used only in error messages and logs.
3. Parse: strip a leading/trailing code fence if present, take the substring from the first `{` to the last `}`, `json.loads`, then `schema.model_validate`.
4. On failure, one repair call: same system, text = original text + `"\n\nYour previous answer was invalid: " + error[:1500] + "\nReturn only the corrected JSON object."`. Repair replies are cached under their own key.
5. Second failure raises `SchemaError` with the prompt name and the first 300 characters of the raw answer.

`registry.py`: `def reader_client(settings, origin) -> ModelClient` returns Meta with `model_for(origin)` when origin is "upload" or provider is "meta"; otherwise the Baseten or Fireworks client. `def vision_client(settings, origin) -> ModelClient` always returns Meta with `model_for(origin)`.

Missing API key for the chosen provider raises `ConfigError` at client creation, naming the env variable.

---

## C5. Prompts (`src/stackcheck/extract/prompts/`)

Each prompt file starts with a first line `version: <name>_v<N>`, then a line `SYSTEM:`, the system text, a line `USER:`, and the user template. Change the text, bump the version.

Filling templates: `Prompt.fill(**values)` replaces each `{key}` for the keys given, using plain string replacement. Never use `str.format`: the templates contain literal JSON braces (`{"action": ...}`) that `str.format` would break on. `fill` raises `ValueError` if a given key has no placeholder, or if any of the placeholders listed for that prompt below is left unfilled.

Placeholders: `filing_v1`: `{pages}`. `photo_v1`: `{image_id}`, `{quarter_end}`, `{block_labels}`. `investigate_v1`: `{finding_title}`, `{reason}`, `{image_id}`, `{quarter_end}`, `{sam_rows}`, `{vision_estimate}`, `{top_obscured}`, `{other_photos}`, `{tried_prompts}`, `{allowed_prompts}`, `{history}`. `summary_v1`: `{language}`, `{headline}`, `{findings}`.

### filing_v1.md

```text
version: filing_v1
SYSTEM:
You copy facts from Indian RERA quarterly progress reports (QPRs) into JSON. You never guess. When a value is missing, blank, "NA", "-" or unreadable, you return null. Accuracy matters more than completeness.

USER:
Below is the text of one QPR. Each page starts with a line [[PAGE n]].

Rules:
1. Copy values as printed. Convert number words only when the digits are also given, for example "Three (3)" becomes 3.
2. Put a value in slabs_claimed_percent only when the document shows a % sign or the column heading says percentage. A plain number of slabs goes in slabs_claimed_count.
3. floors_above_ground_sanctioned comes from text like "G+4", "B+G+12", "S+10" or "Stilt+10": take the number after the plus sign. Ignore basements.
4. If the report covers more than one building, add one entry per building to blocks, using the label printed in the report. If it covers one building, use block_label "MAIN".
5. Money: copy the number and set currency_unit from the column heading ("Rs. in Lakh" gives INR_LAKH, "Crore" gives INR_CRORE). If no unit is stated, use INR.
6. quarter_end is the last day of the quarter the report covers. "Quarter ending March 2025" gives 2025-03-31.
7. For every non-null top-level field, add the page number where you read it to refs, keyed by the field name. Give each block, activity and the financials a ref too.
8. photo_section_present is true if the report has a section for site photographs, even if it looks empty.
9. notes: up to 5 short factual observations about inconsistencies you can see in the numbers. No opinions.

Report text:
{pages}
```

`{pages}` is built as `"\n".join(f"[[PAGE {p.page}]]\n{p.text}" for p in doc.pages)`, truncated to the first 60 pages. For scanned PDFs (`is_scanned`), send page renders as images in batches of at most 10 pages, with text "Pages {a} to {b} of the QPR are attached as images." and the same rules.

### photo_v1.md

```text
version: photo_v1
SYSTEM:
You look at one photograph taken from an Indian real-estate progress report and describe only what is visible. You never guess.

USER:
Image id: {image_id}. Filing quarter ends {quarter_end}. Blocks named in the filing: {block_labels}.

1. kind: "elevation" if it shows the outside of a building from the side or front with several storeys visible; "floor_level" if it shows one floor or the inside of a structure; "site_other" for site views without a building elevation; "document_or_logo" for scanned text, signatures, stamps, logos or maps.
2. stamp_text: copy exactly any date, time, address or coordinates printed on the photo (often in a corner). null if none.
3. stamp_date: the date from stamp_text as YYYY-MM-DD. Indian stamps are usually day first (DD/MM/YYYY). If the order is ambiguous, null.
4. stamp_lat and stamp_lon: decimal degrees from stamp_text, else null.
5. For elevations only, vision_storeys_visible: count storeys you can see, including the ground floor. A storey counts when you can see its floor level or its row of openings. Do not count a level with only columns or rods and no slab above it. For other kinds, null.
6. top_obscured: true if nets, scaffolding, sheets, trees or the photo edge hide the top of the building.
7. block_label_guess: one of the block labels above if the photo shows a label or the filing places it under that block; else null.
8. confidence: "high" only if the storeys are clearly separated and fully visible.
9. reason: one sentence, at most 200 characters, on what you counted.
```

### investigate_v1.md

```text
version: investigate_v1
SYSTEM:
You help a verification program decide what to try next. The program, not you, decides the result. Pick one action from the allowed list. Never repeat an action that is already in the history.

USER:
Undecided check: {finding_title}
Why it is undecided: {reason}
Current photo: {image_id} (quarter ending {quarter_end}), SAM rows: {sam_rows}, visual count: {vision_estimate}, top obscured: {top_obscured}
Other elevation photos available for the same quarter and block: {other_photos}
SAM prompts already tried: {tried_prompts}
Allowed SAM prompts: {allowed_prompts}
History: {history}

Allowed actions:
- retry_sam_prompt (argument: one prompt from the allowed list not yet tried)
- use_other_photo (argument: one image id from the list above)
- crop_to_building (argument: the current image id; use when the building is small in the frame)
- give_up (argument: null; use when nothing new can help)

Return {"action": ..., "argument": ..., "rationale": "<at most 200 characters>"}.
```

Response schema for investigate: `class AgentChoice(BaseModel): action: Literal[...]; argument: str | None; rationale: str = Field(max_length=240)`. Defined in `pipeline/investigate.py`, not in `models.py`.

### summary_v1.md

```text
version: summary_v1
SYSTEM:
You write short, factual summaries for homebuyers. You only use facts given to you. You never accuse anyone.

USER:
Language: {language}   (en = English, hi = simple Hindi in Devanagari)
Verdict: {headline}
Findings (status, title, explanation):
{findings}

Write at most 120 words in plain words. Say what was checked, what did not match, and what could not be checked. End with this sentence, translated if needed: "A gap is a question to ask the builder or RERA, not proof of wrongdoing."
Return {"summary": "..."}.
```

---

## C6. Cache (`src/stackcheck/cache.py`)

```python
def cache_key(parts: dict[str, object]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()

async def get_or_compute(parts: dict[str, object], compute: Callable[[], Awaitable[dict]], *, data_dir: Path) -> tuple[dict, bool]:
    # returns (value, cache_hit). File: data_dir/"cache"/key[:2]/f"{key}.json"
    # stored JSON: {"key_parts": parts, "created_at": iso, "value": value}
    # write atomically: write to f"{path}.tmp" then os.replace
```

Key parts for a model call: `{"provider", "model", "system_sha", "text_sha", "image_shas": [...], "params": {...}}`. The prompt version needs no separate key part: the filled prompt text includes the template, so any template change changes `system_sha` or `text_sha`. For SAM: `{"provider": "meta", "model": "sam-3.1", "prompt": "<noun phrase>", "image_sha": ..., "mode": settings.sam_image_mode}`.

---

## C7. Cost ledger (`src/stackcheck/costs.py`)

Prices in USD per 1M tokens, checked Oct 28, 2026 (Baseten row Oct 29, 2026):

| provider/model | input | cached input | output | status |
|---|---|---|---|---|
| meta/muse-spark-1.3 | 1.25 | 0.15 | 4.25 | verified |
| meta/muse-spark-1.3-contributor | 0.10 | 0.002 | 0.20 | verified |
| fireworks/accounts/fireworks/models/muse-spark-open | 1.25 | 0.15 | 4.25 | verified |
| baseten/meta-models/muse-spark-open-1026 | 1.25 | 0.15 | 4.25 | verified Oct 29: listed for `muse-spark-open`, -1026 billing to confirm in usage view |

SAM 3.1: `0.0025` USD per image request; video `0.0002` USD per frame with a 50-frame minimum per request.

```python
def record(*, provider: str, model: str, purpose: str, check_id: str | None,
           input_tokens: int = 0, cached_input_tokens: int = 0, output_tokens: int = 0,
           units: int = 0, cache_hit: bool = False, data_dir: Path) -> Decimal
```
Appends one JSON line to `data/ledger.jsonl`: `{"ts", "provider", "model", "purpose", "check_id", "input_tokens", "cached_input_tokens", "output_tokens", "units", "usd", "cache_hit", "estimated_price"}`. Cache hits record `usd: 0`. Uncached input = `input_tokens - cached_input_tokens`.

`total_usd(data_dir) -> Decimal`. Before every paid call, if `total_usd >= settings.budget_usd`, raise `BudgetExceeded`. When it crosses `alert_usd`, log a warning once per process.

---

## C8. PDF ingest (`src/stackcheck/ingest/pdf.py`)

```python
def ingest_pdf(pdf_path: Path, source: SourceDoc, out_dir: Path) -> IngestedDoc
def render_page(pdf_path: Path, page: int, dpi: int = 110) -> bytes   # PNG bytes
```

- `import pymupdf` (not `fitz`).
- Text: `page.get_text("text")` per page.
- Images: `page.get_images(full=True)`, extract each with `doc.extract_image(xref)`, convert to PNG with Pillow, skip images smaller than 200 px on either side, save to `out_dir/images/{image_id}.png`.
- Same image bytes appearing on several pages are kept once per page (R2 needs to see repeats across filings, not within).
- `is_scanned = total_text_chars < 200`. If scanned and no images were extracted, render every page at 110 dpi as an `ExtractedImage` with `from_page_render=True`.
- Raise `IngestError` for encrypted or unreadable PDFs.

## C9. Fetching sources (`src/stackcheck/ingest/sources.py`)

```python
async def fetch_pdf(url: str, settings: Settings, dest_dir: Path) -> SourceDoc
def register_upload(data: bytes, filename: str, dest_dir: Path) -> SourceDoc
```

`fetch_pdf` rules: `https` only; host (lowercased, no port) must be in `allowed_hosts`; `follow_redirects=False`, and a redirect is followed manually only if it stays on the same host (max 3); wait so that requests to one host are at least `fetch_min_interval_s` apart (module-level `asyncio.Lock` and last-request time per host); stream the body and stop with `FetchError` past `fetch_max_bytes`; the first bytes must be `%PDF`; User-Agent `StackCheck/0.1 (hackathon research; contact via GitHub)`. The file is saved as `dest_dir/sources/{doc_id}.pdf`.

## C10. SAM 3.1 (`src/stackcheck/vision/sam.py`, `sam_parse.py`)

Request (Meta Responses API, streaming):

```python
stream = await client.responses.create(
    model=settings.sam_model,
    input=[
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},  # one short noun phrase
                image_part,  # see below
            ],
        }
    ],
    stream=True,
    metadata={"mask_encoding": "one_bit"},
)
chunks = [event.delta async for event in stream if event.type == "response.output_text.delta"]
output_text = "".join(chunks)
```

`image_part` is `{"type": "input_image", "image_url": "data:image/png;base64,..."}` when `sam_image_mode == "data_url"`, or `{"type": "input_image", "file_id": "<id from client.files.create(file=..., purpose='user_data')>"}` when `"file_id"`. Which one works is decided by `scripts/probe_sam_input.py` in T03.

```python
class SamClient:
    async def segment_image(self, image_path: Path, prompt: str, *, check_id: str | None) -> str  # raw output_text, cached, ledgered
def parse_sam_output(text: str, prompt: str) -> list[Detection]                                  # pure, in sam_parse.py
```

Output grammar (from Meta's parser documentation):

```
<Nf>id<|box;x1=..;y1=..;x2=..;y2=..;w=<frameW>;h=<frameH>[;c=<confidence>]|><|mask;...|>,id<|box;...|><|mask;...|>
```

One line per frame (an image is frame 0). Empty output means nothing was found.

Parsing rules for `parse_sam_output`:
1. Empty or whitespace text returns `[]`.
2. Split on `"\n"`, skip blank lines.
3. A line must match `^<(\d+)f>(.*)$`. Otherwise raise `SamParseError` with the line number.
4. In the line body, find records with `re.finditer(r"(?:^|,)(\d+)<\|box;([^|]*)\|>", body)`. The mask token after each box is ignored. This is safe because mask payloads never contain `<` or `|`.
5. Box fields: split on `;`, then on the first `=`. Required: `x1 y1 x2 y2 w h` (parse as float, `w` and `h` as int). Optional: `c` (float). Unknown keys are ignored. A missing required key raises `SamParseError`.
6. Build `Detection(prompt=prompt, frame=N, object_id=id, box=Box(...), confidence=c, image_w=w, image_h=h)`.

## C11. Floor counting (`src/stackcheck/vision/floors.py`)

Constants:

```python
MIN_CONF = 0.35
BUILDING_MIN_CONF = 0.30
ROW_GAP_FACTOR = 0.6
MIN_ROW_MEMBERS = 2
MIN_ROWS = 3
AGREE_TOLERANCE = 1
ESTIMATE_TOLERANCE = 2  # F1: methods differing by exactly 2 give an ESTIMATE
ESTIMATE_FLAG_MARGIN = 3  # F1: R1 flags from an ESTIMATE only at a gap of 3 or more
```

```python
def pick_building_box(building_dets: list[Detection], element_dets: list[Detection]) -> tuple[Box | None, bool]
def group_rows(element_dets: list[Detection], building: Box) -> list[list[Detection]]
def count_floors(image_id: str, building_dets: list[Detection], element_dets: list[Detection],
                 facts: PhotoFacts | None, prompts_used: list[str]) -> FloorCount
```

Algorithm:
1. Building box: the largest-area detection from the `"building"` prompt with confidence ≥ `BUILDING_MIN_CONF` (missing confidence counts as passing). If none, the bounding box of all element detections, padded by 2% of image size on each side, with `building_box_inferred=True`. If there are no detections at all, return `CANT_VERIFY` with reason "no building found".
2. Element detections: keep those with confidence ≥ `MIN_CONF` (or no confidence), centre inside the building box (edges included), height between 0.5% and 25% of the building box height (0.5% keeps windows on 50-plus-storey towers).
3. Rows: sort by `cy`. Let `h_med` be the median kept height. Walk the list: start a new row when `cy - mean_cy(current row) > ROW_GAP_FACTOR * h_med`. Drop rows with fewer than `MIN_ROW_MEMBERS` members, unless the building is narrow (`building.width < 3 * median kept width`), in which case rows of 1 are kept.
4. `sam_rows = len(rows)`. `rows_y` = row mean `cy`, sorted.
5. `sam_pitch_estimate = round(building.height / median(diff(rows_y)))` when there are at least 2 rows and the box was not inferred; else `None`. It's shown, not used for the decision.
6. `vision = facts.vision_storeys_visible if facts and facts.kind == "elevation" else None`.
7. Decision (F1 tiers: `tier` is set on every `OK` count):
   - `sam_rows < MIN_ROWS` and `vision is None`: `CANT_VERIFY`, reason "not enough evidence in this photo".
   - Vision has a count but `sam_rows < MIN_ROWS`: `OK` + `ESTIMATE`, `lower = upper = vision`, reason "about N floors (estimate); SAM found no rows".
   - SAM alone (vision `None`): `CANT_VERIFY`, reason "only one method produced a count (sam = n)".
   - `abs(sam_rows - vision) <= AGREE_TOLERANCE`: `OK` + `CONFIDENT`, `lower = min`, `upper = max`; if `facts.top_obscured` then `upper = None`.
   - `abs(sam_rows - vision) == ESTIMATE_TOLERANCE`: `OK` + `ESTIMATE`, `lower = min`, `upper = max`, reason "methods differ by 2 ... (estimate)".
   - Otherwise `CANT_VERIFY`, reason f"methods disagree: SAM rows {sam_rows}, visual count {vision}".

## C12. Image hash (`src/stackcheck/vision/phash.py`)

```python
def dhash_hex(image_bytes: bytes) -> str        # 64-bit difference hash, 16 lowercase hex chars
def hamming(a: str, b: str) -> int
```
dHash: convert to grayscale, resize to 9×8 with `Image.Resampling.LANCZOS`, bit = left pixel > right pixel for each of the 8 rows × 8 columns, row-major, most significant bit first. Hamming = `(int(a, 16) ^ int(b, 16)).bit_count()`.

## C13. Overlay (`src/stackcheck/vision/overlay.py`)

```python
def draw_floor_overlay(image_path: Path, count: FloorCount, detections: list[Detection], out_path: Path) -> Path
```
Building box blue `#1D4ED8` 3 px. Element boxes amber `#F59E0B` 1 px. One red `#DC2626` 2 px line per row across the building box, labelled 1, 2, 3 ... from the bottom row up, in white text on a red tag. A top bar (height 28 px, background `#101828`, text `#FFFFFF`): `"StackCheck  rows {sam_rows}  visual {vision_estimate}  {status}"`. Save as PNG.

## C14. Rules (`src/stackcheck/rules/checks.py`, `engine.py`)

Helpers (pure, in `checks.py`):

```python
def quarter_start(quarter_end: date) -> date              # first day of the month two months before quarter_end's month
def to_inr(value: Decimal | None, unit: str | None) -> Decimal | None   # LAKH x 100_000, CRORE x 10_000_000
def claimed_slabs(block: BlockProgress) -> int | None
    # count if present; else round(percent / 100 * (floors_above_ground_sanctioned + 1)) when both present; else None
def photo_in_window(stamp: date | None, quarter_end: date) -> bool | None
    # None if stamp is None; True if quarter_start(qe) - 45 days <= stamp <= qe + 15 days
```

Each rule is `def rN_...(project: ProjectEvidence, settings_view: RuleSettings) -> list[Finding]`. `RuleSettings` is a small Pydantic model in `checks.py` with `stamp_required_states: set[str]`. Severity for OK and CANT_VERIFY is always `"info"`.

**R1_PHOTO_VS_CLAIM** (per quarter, per block)
- Claimed `S = claimed_slabs(block)`. Photos: kind `elevation`, `floors.status == "OK"`, block guess equal to the block label or (block is `MAIN` and guess is None), and `photo_in_window` not False.
- No S, or no usable photo: `CANT_VERIFY` (say which is missing).
- `CONFIDENT` photos (tier `CONFIDENT` or unset) decide when present. `ESTIMATE`-only photos never decide on their own: if `S - V_up >= ESTIMATE_FLAG_MARGIN` (vision alone is wrong by at most 2 on the 25 bench photos, so a gap of 3 never fired on a correct claim there): `FLAG` `high`, title f"Photo shows about {V_up} floors (estimate), report claims {S} slabs". Else `CANT_VERIFY` with "a question to ask the builder".
- `V_low = max(lower)`, `V_up = max(upper)` over usable photos; if any usable photo has `upper None`, `V_up = None`.
- `V_low >= S - 1`: `OK`.
- `V_up is not None and V_up < S - 1`: `FLAG`; gap = `(S - 1) - V_up`; severity `high` if gap ≥ 3 else `medium`. Title: f"Photo shows {V_up} floors, report claims {S} slabs". Evidence: the slab quote with page, the overlay photo, the computation.
- Otherwise `CANT_VERIFY`, reason "photo range overlaps the claim".

**R2_REUSED_PHOTO** (whole project)
- Photos with a `dhash` whose kind is not `document_or_logo`. For every pair from different quarters: `hamming <= 6` is a match.
- Each match: `FLAG`, severity `high` if both are `elevation` and the claimed slabs for that block differ between the two quarters, else `medium`. Title: "Same photo filed in {q1} and {q2}". Evidence: both photos.
- No pairs to compare (fewer than 2 quarters with photos): `CANT_VERIFY`. No matches: one `OK`.

**R3_PHOTO_DATE** (per quarter)
- Each photo with a `stamp_date` and `photo_in_window(...) is False`: `FLAG` `medium`, title f"Photo dated {stamp} filed for quarter ending {qe}".
- If `filing.state` is in `stamp_required_states` and more than half the non-document photos have no `stamp_date`: one `FLAG` `low`, title "Most photos have no readable date stamp".
- No photos at all: `CANT_VERIFY`. Otherwise, if nothing flagged: `OK`.

**R4_CLAIM_REGRESSION** (consecutive quarters with `quarter_end`, same block label)
- `claimed_slabs` decreased: `FLAG` `high`, title "Claimed slabs went down from {a} to {b}".
- Same activity name (lowercased, spaces collapsed) with `percent_done` down by more than 2 points: one `FLAG` `medium` per quarter pair, listing the activities.
- No comparable pair: `CANT_VERIFY`. Otherwise, if nothing flagged: `OK`.

**R5_COPY_PASTE_QUARTER** (consecutive quarters A then B)
- Same `source.sha256` for different quarters: `FLAG` `high`, "Identical report filed for two quarters".
- Shared activity names ≥ 5 and the share with identical `percent_done` ≥ 0.9:
  - and both `cumulative_received` and `cumulative_spent` identical and non-null: `FLAG` `medium`, "Quarter repeats the previous quarter's figures".
  - else if B's `spent_this_quarter` (in INR) > 0: `FLAG` `low`, "Spending reported with no change in progress".
- Nothing comparable: `CANT_VERIFY`. Otherwise `OK`.

**R6_MONEY_VS_PROGRESS** (latest quarter with the needed data)
- `spent_share = cumulative_spent / estimated_total_cost` (both in INR, cost > 0).
- `progress_share` = mean of `percent_done` / 100 when there are ≥ 3 activities with values, else `claimed_slabs / (floors + 1)` for the `MAIN` block, else missing.
- `spent_share - progress_share > 0.25`: `FLAG` `low`, title "Money spent runs ahead of work done". The explanation must add: "Land and approval costs can explain early spending."
- Missing inputs: `CANT_VERIFY`. Else `OK`.

**R7_ARITHMETIC**
- Within a filing (INR): `cumulative_spent < spent_this_quarter` or `cumulative_received < received_this_quarter`: `FLAG` `low`.
- Consecutive A, B: `abs(cum_spent_B - (cum_spent_A + spent_B)) > max(cum_spent_B * 0.01, 10_000)`: `FLAG` `medium`. Same for received.
- Nothing checkable: `CANT_VERIFY`. Else `OK`.

**R8_MISSING_QUARTER**
- Sorted distinct `quarter_end` values. Between the first and last, every quarter end (Mar 31, Jun 30, Sep 30, Dec 31) without a filing is missing. Any missing: `FLAG` `medium`, title "{n} quarter(s) missing between {first} and {last}", explanation ends with "Based on the filings provided; the portal may hold more."
- Fewer than 2 dated filings: `CANT_VERIFY`. Else `OK`.

**R9_VIDEO_VS_CLAIM** (whole project, only when `project.video` is set)
- `M` = the latest quarter's slab claim for `video.block_label` (count or percent, as in R1). `N = [video.lower, video.upper]`.
- No video, no usable range, or no claim for the block: `CANT_VERIFY` (say which is missing).
- `N.upper <= M - 2`: `FLAG` `high`. Floors only go up, so a video showing at least 2 fewer than claimed needs an answer.
- `N.lower > M`: `OK`, "consistent with later progress". One direction only: a video showing more than claimed is never a flag.
- Otherwise `CANT_VERIFY`, "range overlaps the claim". Evidence: the contact sheet (`media/<video_id>-sheet.png`).

`engine.run_rules(project, rule_settings) -> list[Finding]` runs R1 to R9 in order, numbers `finding_id`s, and returns them sorted by severity (high, medium, low, info), then rule code. Under an unconfirmed shared identity, `run_rules_capped` drops the video (single-quarter projects carry none), so R9 stays silent.

## C15. Bounded agent (`src/stackcheck/pipeline/investigate.py`)

For each R1 finding with status `CANT_VERIFY` whose reason is a disagreement or "only one method":
- Up to 3 steps. Each step: ask the model with `investigate_v1` (vision client, origin of the source), parse `AgentChoice`, validate (argument allowed, not repeated). An invalid choice ends the loop with a `give_up` step whose result is "invalid choice rejected by program".
- Execute: `retry_sam_prompt` runs SAM with that prompt and recounts; `use_other_photo` counts floors on that photo; `crop_to_building` crops to the building box plus 5% and reruns SAM and the vision count on the crop.
- After each step, rebuild the evidence and re-run R1 only. Stop when R1 is no longer `CANT_VERIFY`, or on `give_up`, or after 3 steps.
- Every step becomes an `AgentStep` in `trace` with the program's own `result` text.
- The agent can never set a finding's status. Only R1 does.

## C16. Pipeline and jobs

Steps in fixed order: `fetch`, `read_filings`, `photos`, `floors`, `rules`, `investigate`, `report`. Progress = finished steps / 7. Video frames are counted inside the `floors` step when the check carries a video.

```python
async def run_check(check_id: str, project_label: str, sources: list[SourceRequest], settings: Settings, *, clients=None, video: VideoUpload | None = None) -> Report
class SourceRequest(BaseModel):            # in pipeline/run_check.py
    url: str | None = None
    upload_id: str | None = None           # exactly one of the two
class VideoUpload(BaseModel):              # at most one video per check
    upload_id: str                         # doc_id of an mp4/mov/webm in sources/
    block_label: str = "MAIN"              # the block the video shows, filer-confirmed
```

Folder `data/work/<check_id>/`: `status.json`, `sources/<doc_id>.pdf`, `ingest/<doc_id>.json`, `images/<image_id>.png`, `facts/<doc_id>.filing.json`, `facts/<image_id>.photo.json`, `floors/<image_id>.json`, `media/<image_id>.overlay.png`, `evidence.json`, `findings.json`, `trace.json`, `report.json`.

`jobs.py`: `JobRunner` with an `asyncio.Semaphore(settings.max_concurrent_checks)`, a dict of running tasks, `submit(...) -> check_id` (raises `QueueFull` when queued + running > `max_queue`), and status writes (atomic) on every step change. On startup, any `status.json` left as `running` is marked `failed` with error "interrupted by restart".

Report building: verdict headline rule: any FLAG with severity high or medium gives "Gaps found"; else if only low FLAGs give "No major gaps. N minor note(s)."; else if `ok > 0` gives "No gaps found in what we could check"; else "Not enough data to check". Summaries are generated only if the step succeeds; a summary failure never fails the check.

Redaction: when `settings.redact_names` is True, the report builder sets `project_reg_no`, `project_name` and `promoter_name` to `"[redacted]"` in every `QuarterFiling` inside `Report.evidence`, and replaces any occurrence of those strings inside `Finding.title`, `Finding.explanation`, `Evidence.quote` and `Evidence.value` with `"[redacted]"`. Files on disk under `facts/` keep the real values.

`JobRunner(settings: Settings, *, run: Callable[..., Awaitable[Report]] = run_check, on_finished: Callable[[], None] | None = None)`. `on_finished` is called after every finished or failed check (Modal passes `volume.commit`).

## C17. HTTP API (`src/stackcheck/api/app.py`)

`create_app() -> FastAPI` (factory; reads settings once, creates one `JobRunner`).

| Method, path | Request | Response |
|---|---|---|
| `GET /api/health` | | `{"ok": true, "version": "<app version>"}` |
| `POST /api/uploads` | multipart `file` (PDF, ≤ 30 MB; or video mp4/mov/webm, ≤ 50 MB, ≤ 60 s) | `{"upload_id", "filename", "sha256"}` plus `"kind": "video"` for videos |
| `POST /api/checks` | `{"project_label": str 1..80, "sources": [SourceRequest] 1..24}` plus optional `video_upload_id`, `video_block_label` (default `MAIN`), `video_same_project` (must be `true` when a video is sent) | `202 {"check_id"}` |
| `GET /api/checks/{check_id}` | | `CheckStatus` |
| `GET /api/checks/{check_id}/report` | | `Report` (404 until done) |
| `GET /api/samples` | | contents of `settings.samples_file` (missing file gives `[]`) |
| `GET /media/{check_id}/{name}` | | PNG from `<data_dir>/work/<check_id>/media/`; `check_id` must match `^chk_[0-9a-f]{10}$` and `name` must match `^[A-Za-z0-9_.-]+\.png$` |
| `GET /` and other paths | | static files from `settings.web_dist_dir`, falling back to `index.html` |

Errors: `{"error": {"code": "<snake_case>", "message": "<plain sentence>"}}` with 400, 404, 413, 422, 429 or 500. Validation of URLs (C9 host allow-list) happens at `POST /api/checks`, before the job starts, and returns 400 `host_not_allowed`.

## C18. Frontend contract (`web/`)

- Views by URL hash: `#/` start, `#/check/<id>` progress, `#/report/<id>` report. No router library.
- `src/lib/api.ts`: typed `fetch` wrappers for every endpoint in C17. Types in `src/lib/types.ts` mirror C3 exactly (dates as ISO strings, Decimals as strings).
- Progress view polls `GET /api/checks/{id}` every 1.5 s and stops on `done` or `failed`.
- Report components: `VerdictBanner`, `FindingCard`, `PhotoEvidence` (overlay toggle), `QuarterTimeline` (SVG: claimed slabs vs visible floor range per quarter), `MoneyChart` (SVG: spent share vs progress share), `CantVerifyList`, `AgentTrace`, `CostBadge`, `Disclaimer`.
- Status is never shown by colour alone: each has an icon and a word.

Design tokens (CSS custom properties in `src/app.css`), light and dark:

```css
:root {
  --bg: #FFFFFF; --surface: #F9FAFB; --text: #101828; --muted: #475467; --border: #D0D5DD;
  --flag: #B42318; --flag-bg: #FEF3F2; --ok: #067647; --ok-bg: #ECFDF3;
  --unsure: #B54708; --unsure-bg: #FFFAEB; --accent: #1D4ED8;
  --radius: 10px; --space: 8px; --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0C111D; --surface: #161B26; --text: #F5F5F6; --muted: #94969C; --border: #333741;
    --flag: #F97066; --flag-bg: #55160C; --ok: #47CD89; --ok-bg: #053321;
    --unsure: #FDB022; --unsure-bg: #4E1D09; --accent: #84ADFF;
  }
}
```

Every text and background pair above passes WCAG AA (4.5:1) in its own theme.
