# ARCHITECTURE

## Shape of the system

```
                 ┌──────────────────────────── web/ (Svelte 5, Vite) ───────────────────────────┐
                 │  Start page ──► Progress page (polls status) ──► Report page (findings, photos) │
                 └───────────────────────────────┬─────────────────────────────────────────────────┘
                                                 │ JSON over HTTP
┌────────────────────────────────── stackcheck/api (FastAPI) ─────────────────────────────────────────┐
│ POST /api/checks   GET /api/checks/{id}   GET /api/checks/{id}/report   /api/uploads   /media   /    │
└───────────────────────────────┬──────────────────────────────────────────────────────────────────────┘
                                │ starts job
┌───────────────────────── stackcheck/pipeline (orchestrator + jobs) ──────────────────────────────────┐
│ for each source PDF:  ingest ─► extract facts ─► analyse photos ─► count floors                       │
│ then:                 rules (all quarters) ─► investigate (bounded agent, only on CANT_VERIFY R1)     │
│ then:                 build report ─► write data/work/<check_id>/report.json                          │
└──────┬───────────────┬──────────────────┬─────────────────────┬───────────────────┬──────────────────┘
       │               │                  │                     │                   │
  ingest/          extract/            vision/               rules/              report/
  pdf.py           filing.py           sam.py (I/O)          checks.py (pure)    build.py
  sources.py       photos.py           sam_parse.py (pure)   engine.py (pure)
                   prompts/*.md        floors.py (pure)
                                       phash.py (pure)
                                       overlay.py
       │               │                  │
       └───────────────┴─────► llm/ (ModelClient: meta | baseten | fireworks) ──► cache.py + costs.py
                                         vision/sam.py ──────────────────────────► cache.py + costs.py
```

Everything that costs money goes through `cache.py` (so reruns are free) and `costs.py` (so every cent is logged).

## Module responsibilities

| Module | Job | Must not |
|---|---|---|
| `config.py` | Load settings from env vars with pydantic-settings | Hold logic |
| `errors.py` | `StackCheckError` and subclasses | |
| `models.py` | Every Pydantic contract from CONTRACTS | Do I/O |
| `cache.py` | Disk cache: `get_or_compute(key_parts, fn)` keyed by SHA-256 of canonical JSON | Know about providers |
| `costs.py` | Price table, `record(...)` to `data/ledger.jsonl`, `total_usd()` | Call APIs |
| `llm/` | One interface (`ModelClient`) over Meta Responses API and OpenAI-compatible Chat APIs; `ask_json` for schema-validated answers | Contain prompts |
| `ingest/pdf.py` | Text per page, embedded images with page and position, page renders | Call models |
| `ingest/sources.py` | Polite single-URL PDF fetch with allow-list; accept uploads; content hashing | Crawl |
| `extract/filing.py` | QPR text → `QuarterFiling` via `ask_json` | Decide verdicts |
| `extract/photos.py` | Photo → `PhotoFacts` (kind, stamp date, location text, vision floor count) | Count with SAM |
| `vision/sam.py` | Build SAM 3.1 requests, stream, return raw `output_text` | Parse |
| `vision/sam_parse.py` | Grammar → `Detection` list | Do I/O |
| `vision/floors.py` | Detections + vision count → `FloorCount` (rows, pitch, agreement, abstain) | Do I/O |
| `vision/phash.py` | 64-bit dHash, Hamming distance | Do I/O |
| `vision/overlay.py` | Draw boxes and row lines on a photo, save PNG | Decide anything |
| `rules/checks.py` | R1 to R8, one pure function each | Do I/O |
| `rules/engine.py` | Run all rules over a `ProjectEvidence`, return `Finding` list | Do I/O |
| `pipeline/run_check.py` | Orchestrate one check, update step status | Contain rule logic |
| `pipeline/investigate.py` | Bounded agent: choose up to 3 extra actions for an undecided R1 | Override a rule |
| `pipeline/jobs.py` | In-process job runner, status file per check | |
| `report/build.py` | Assemble `Report`, verdict headline, optional summary | Change findings |
| `api/app.py` | FastAPI app factory, routes, static files | Contain pipeline logic |
| `web/` | UI | Compute findings |

## Decision log

Each decision is final for the hackathon unless a task card says otherwise. If the agent believes one is wrong, it writes `STUCK.md`; it doesn't change it.

**D01. Evidence source: QPR photos, not filmed video.** The builder's own dated photos sit in the same public document as the builder's own claims, so the check is "the filing against itself". This removes filming, removes YouTube, and makes every finding traceable to one public PDF. Video stays as stretch S1 for user uploads.

**D02. Two independent floor counts, agree or abstain.** SAM 3.1 has no "count floors" mode, and in an earlier test the prompt "floor slab" returned nothing while "balcony" rows counted 19 floors correctly. So: SAM finds repeated elements (balconies, window openings), code groups them into rows, and Muse Spark separately counts storeys by looking. Accept only when they agree within 1. A wrong confident count would create a false accusation, so abstaining is cheaper than being wrong.

**D03. Rules are deterministic code; the model never decides a verdict.** Models extract facts and propose actions. Pure functions decide OK, FLAG or CANT_VERIFY. This is what makes the agent verifiable (the theme of the Agents 102 session) and lets the rules have 100% branch tests.

**D04. Bounded agent only where it adds value.** The only open-ended step is "R1 couldn't decide": the agent may choose up to 3 actions from a fixed menu (another SAM prompt, another photo, a crop) and every action is logged. Everything else is a fixed pipeline, which is easier for the coding agent to build correctly and for judges to trust.

**D05. Send extracted text plus selected page images, not raw PDFs, to models.** Meta's API accepts PDFs, but Baseten and Fireworks don't document PDF input. Extracting with PyMuPDF keeps all three providers interchangeable and gives exact page numbers for evidence.

**D06. Provider switch for the reading step.** `STACKCHECK_EXTRACT_PROVIDER = meta | baseten | fireworks`. Task T12 measures accuracy, cost and speed for each on the same 5 filings. If Muse Spark Open (Baseten or Fireworks) is within 2 points of Muse Spark 1.3 on field accuracy, it becomes the default reader, which qualifies for the open-weights bonus prize. SAM 3.1 stays on Meta's API (only place it runs as a service).

**D07. Contributor tier for public data, standard tier for uploads.** `muse-spark-1.3-contributor` costs $0.10 / $0.20 per 1M tokens against $1.25 / $4.25, and public filings carry no private data. Anything a user uploads goes only to `muse-spark-1.3`, which doesn't train on prompts. This is a privacy rule in code, not a setting.

**D08. File-based storage, no database.** The original plan said SQLite. Dropped: every record is a document written once and read whole (a filing's facts, a check's report). A folder per check (`data/work/<check_id>/`) is simpler to inspect, diff, cache, mount on Modal and show to judges. Revisit only if there are more than 1,000 checks.

**D09. In-process background jobs.** `asyncio` tasks with a semaphore (max 2 checks at once) and a status JSON file. No Celery, no Redis: one container, a few concurrent users during judging.

**D10. Our own dHash instead of the `imagehash` package.** 20 lines with Pillow and numpy, no SciPy dependency, and dHash handles the case we care about (the same photo re-saved, resized or recompressed).

**D11. Parse SAM boxes in Python; skip mask decoding.** Meta's official parser is TypeScript only (`@meta-sam/parser` 0.1.0), and its mask codec is a context-modelled range coder that's risky to port. Boxes are plain text in the output (`<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|>`) and are all that row counting needs. Overlays draw boxes. Mask overlays are stretch S3, via a small Node script that uses the official parser.

**D12. Plain Vite + Svelte 5, not SvelteKit.** Three views, no server rendering, served as static files by FastAPI. Fewer moving parts for the coding agent. TypeScript pinned to 6.0.3 because svelte-check 4.7.7 declares `typescript ^5 || ^6` and TS 7 is a new compiler.

**D13. Deploy on Modal.** FastAPI runs as `@modal.asgi_app()` with a Modal Volume for `data/`. Uses the Modal credits for what Modal is for, and keeps one warm container during judging so the live demo answers instantly.

**D14. Python 3.15 locally, code compatible with 3.13+.** 3.15.2 is the latest stable (Oct 25, 2026) and every dependency has wheels for it (cp315 or abi3). The Modal image may only offer an older Python, so no 3.14+ only syntax.

**D15. Cache key = exact request.** Key parts: provider, model, hashes of the system text, user text and images, and parameters. Editing a prompt changes the text hashes, so only the affected entries are recomputed. The version line in each prompt file is for people and the report's `versions` field: bump it whenever the text changes.

**D16. SAM prompts and image mode from the T03 spike.** `probe_sam_input.py` confirmed base64 `data_url` input works (7 boxes on the first bench image), so `sam_image_mode` stays `"data_url"`. `spike_sam.py` on 10 bench images ranked prompts by images with 6+ detections: `window` (7/10, median 18) beat `balcony` (2/10), with `floor slab` finding nothing, so `sam_prompts` was `"window,balcony"`. Numbers are in `PROGRESS.md` (T03) and `data/eval/spike_sam.json`. T07 update: `bench_floors.py --compare` on 25 labelled photos picked `"window,window opening"` (within1 1.0, wrong_confident 0.0, abstain 0.52) over `"window,balcony"` (1.0, 0.0, 0.76) and `"balcony,concrete column"` (0.875, 0.04, 0.68), so the default is now `"window,window opening"`. No constant was tuned: floors.py is unchanged. Full table in `PROGRESS.md` (T07) and `data/eval/bench_floors.json`.

**D17. extract_filing takes an optional pdf_path.** Scanned filings need page renders, and IngestedDoc doesn't carry the file path. Keyword-only; required when doc.is_scanned (raises IngestError otherwise); ignored in text mode. Approved by Rushabh, Oct 28.

**D19. Value-on-page check in extract_filing.** Every numeric value (money, percent, slab count, floors) must appear in the text of its cited page after removing commas and spaces (the value is also stripped of trailing "/-" and zero decimals, so 1,81,50,755 matches 18150755 and 40.0 matches 40%). Otherwise the field is nulled with the note "value not found on cited page"; a value with no PageRef is nulled too. A deterministic guard against invented numbers: a null becomes "can't verify" downstream, while a wrong number would create a false flag. Added Oct 28 (T05b). D18 stays reserved for S2.

**D20. Financials.unit_overrides.** When a single money row states its own unit (for example "(In Lakh)") while the table heading states another, `currency_unit` keeps the heading unit and `unit_overrides` maps that money field name to its row unit, for example `{"estimated_total_cost": "INR_LAKH"}`. In C14, money comparisons use the override for that field and `currency_unit` otherwise. Approved by Rushabh, Oct 28 (T05c).

**D21. Activities are keyed by row number.** Every activity name starts with its S. No. from the table, a space, then the task text as printed, for example "9.2 Lifts Wells and Lobbies". When one numbered row shows several percentages, it becomes one activity per percentage, keyed `<row>.<1,2,3...>` from top to bottom. Text printed inside a numbered row's cell (like "Any other activities") belongs to that row and is never its own activity. Eval and C14 (R4, R5) match activities by this leading key, falling back to the normalised name. Approved by Rushabh, Oct 28 (T05c).

**D22. Verdict headline is a free string.** Low-only FLAGs must not read "No gaps found", so the headline adds a fourth form, "No major gaps. N minor note(s)." A closed Literal cannot hold the count, so `Verdict.headline` is `str`. Ordered by the Oct 30 win brief (FIX 5); needs Rushabh's explicit confirmation like D20/D21.

**D23. R2 only compares usable site photos.** Blank scans share a hash, so R2 skips kinds outside {elevation, floor_level, site_other}, confidence below medium, dhashes with fewer than 12 or more than 52 of 64 bits set, and photos smaller than 64 px on a side. One finding per newer photo lists every older photo it matches. Ordered by the Oct 30 retest (FIX 8); needs Rushabh's explicit confirmation.

**D24. PhotoFacts carries stamp resolution.** R3 stores the effective stamp date and its status (ok, swapped_low_confidence, impossible, unreadable) so D1/D2 cards can show what happened to each date. Ordered by the Oct 30 retest (SMALL); needs Rushabh's explicit confirmation.

**D25. F1 floor-count tiers (Nov 1-2 brief).** Every `OK` `FloorCount` carries `tier`: `CONFIDENT` when both methods agree within 1, `ESTIMATE` for vision-only counts and method gaps of exactly 2. An `ESTIMATE` never raises a flag on its own; R1 flags from one only at a claim gap of 3 or more (vision alone is wrong by at most 2 on the 25 bench photos). The accuracy page shows confident / estimate / can't tell as "N of 25". Needs Rushabh's explicit confirmation like D22-D24.

**D26. V1 site-video check (Nov 2 brief).** One upload per check (mp4/mov/webm, 50 MB, 60 s; WhatsApp 40 s), filer-confirmed block label. 12 frames sampled with ffmpeg, sharpest 8 kept, SAM + vision count per frame (cached), consensus 4+ = `CONFIDENT`, 2-3 = `ESTIMATE`, else `CANT_VERIFY`. R9 is one-directional: `FLAG` only when the video top sits 2+ floors under the latest claim; above the claim is `OK`. Contact sheet evidence on the report and in WhatsApp replies; videos deleted after 7 days. Bench and constructed demo wait on `data/bench_video/videos.csv`. Needs Rushabh's explicit confirmation like D22-D24.

**D27. Video tier cap and R9 questions (Nov 2, bench vs public records).** Frame agreement is not accuracy: V07 bench said ESTIMATE 30 against Wikipedia's 25, V08 said CONFIDENT 7-8 against a public 9-10, and time-lapse V04 came out CONFIDENT. So video tiers cap at `ESTIMATE` (CONFIDENT returns only with person references for 5+ clips plus measured error), and R9 never flags: a 2+ floor shortfall is `CANT_VERIFY` with severity `question` and the sentence "Your video shows about N floors. The filing says M. ... A question to ask the builder or RERA, not proof of wrongdoing." The `OK` ("consistent with later progress") and overlap branches stay. Ordered by Claude's Nov 2 bench check via Rushabh; needs Rushabh's explicit confirmation, and CONTRACTS severity/video-tier amendments with it.

**D28. R4 matches on (block key, base name), not the raw name (Nov 2 status check, FIX 1).** Activity rows carry the block either in the name ("Block A - ...") or in the page (filing block refs map pages A p4, B-1 p5, B-2 p6, C p7, D p8, E p9). `_normalize_block_label` collapses "Block A", "BLOCK - A (ORION)", "BLOCK-A" to "A". Matching strips the S.NO and the block mention, so Block C staircases "14." (Sept 2024) and "13." (Dec 2024, Mar 2025) compare. Missing block key on either side is CANT_VERIFY with that cause, never "only one dated filing". Every FLAG carries both quarters' doc_id and page; missing refs are CANT_VERIFY. Ordered by Claude's Nov 2 retest via Rushabh; needs Rushabh's explicit confirmation.

**D29. Row-proximity second read and filing_v5 (Nov 2 status check, FIX 2).** The reader put Mar 2025 Block B-2 p6 item 9 at 70% (the value belongs to item 11). `_value_near_own_row` requires an activity percent to sit on its own S.NO row on the cited page (row line plus a continuation line); otherwise the value is nulled and the drop can never flag. filing_v5 adds the row rule ("the percentage belongs to the S. No. printed at the start of its own row") and the Section IX money mapping. Prompt versions bump v4 to v5 so the model cache cannot hide the fix. Re-read the Mar 2025 filing (doc 93aed199f53910be) as the only fresh read. Ordered by Claude's Nov 2 retest via Rushabh; needs Rushabh's explicit confirmation.

**D30. Financials.net_balance and the reconciliation check (Nov 2 status check, FIX 4).** `Financials` gains optional `net_balance` (closing/net amount; null when the filing does not state it). The v5 reader maps "Actual Cost Incurred during the quarter" to spent_this_quarter, "Cumulative Fund Collected" to cumulative_received, "Cumulative Expenditure" to cumulative_spent, and the net amount to net_balance. R7 flags low when net differs from collected minus spent by more than 2% of the estimated cost, with both page refs; missing refs are CANT_VERIFY. Ordered by Claude's Nov 2 retest via Rushabh; needs Rushabh's explicit confirmation, and a CONTRACTS financials amendment with it.

**D31. Row-check removal, v4 matching fallback (Nov 2 recheck, FIX A).** The D29 row-proximity nulling removed every percent on the scanned Bihar PDFs (31/34/38 values to 0) because OCR text lacks clean row structure. `_post_process` is back to value-on-page-only matching; the v5 prompt keeps the row instruction for fresh reads. `ActivityProgress` gains optional `block_label` (building the row belongs to; null when the table has no block sections); the v5 reader is instructed to set it (rule 13). Ordered by Claude's Nov 2 recheck via Rushabh; needs Rushabh's explicit confirmation, and a CONTRACTS activity amendment with it.

**D32. Stored normalized block keys (Nov 2 recheck, FIX B).** `_post_process` rewrites stored block labels to their normalized key ("BLOCK - A (ORION)" becomes "A"; "MAIN", "B-2" unchanged), dedupes blocks by key, and backfills null activity labels from the page-to-block map. R4 prefers the stored normalized `activity.block_label`, falling back to name derivation then the page map. Ordered by Claude's Nov 2 recheck via Rushabh; needs Rushabh's explicit confirmation.

**D33. Money guards and deterministic fallback (Nov 2 recheck, FIX C/D).** R7 treats two exactly-equal money figures as a copying error (CANT_VERIFY "needs a second read", excluded from reconciliation) and checks exact cross-quarter identities (cumulative(Q) equals cumulative(Q-1) plus this-quarter(Q); mismatch is CANT_VERIFY with the same guard). Guard-failed findings carry inputs guard=failed and are excluded from the model summary input. The net reconciliation emits ONE low FLAG per project covering all firing quarters (latest quarter_end; quote plus per-quarter computations). `_post_process` fills null money fields from Section-IX page labels as a deterministic fallback. Ordered by Claude's Nov 2 recheck via Rushabh; needs Rushabh's explicit confirmation.

**D36. Golden-test readiness (Nov 2 recheck 3).** Scanned filings keep values on page-text mismatch (one note per filing); the v5 money rule demands full grouped numbers. Model cache keys carry code_sha (sha256 of extract/filing.py, default None). A one-page row-by-row image re-read confirms R4 activity FLAGs (one call per page; agree keeps, disagree downgrades with guard=failed; errors keep the flag). Eval ingests every data/review_*.csv (default --review is data/). Ordered by the Nov 2 golden brief via Rushabh; needs Rushabh's explicit confirmation.

**D34. R4 read-fault guards and money plausibility (Nov 2 recheck 2, FIX 1/2/3).** Two or more activity drops sharing one newer page between the same filings is a read fault (CANT_VERIFY, guard=failed); a finished item (exactly 100) falling to 70 or below needs a second read (CANT_VERIFY, guard=failed). A crore-scale estimate (1 crore or more) with any present cumulative or net figure under Rs 1 lakh is a read fault (R6 and R7: one CANT_VERIFY, guard=failed). `_value_on_page` matches whole numbers only (digit boundaries on the stripped text), so fragments like 56195 inside 28,10,56,195 fail instead of passing. Ordered by Claude's Nov 2 recheck via Rushabh; needs Rushabh's explicit confirmation.

**D35. Multi-file review ingestion (Nov 2 recheck 2, FIX 4).** `eval_report` ingests every `data/review_*.csv` plus `data/review.csv` merged (flag precision per rule across files); `--review` defaults to the `data` directory and still accepts a single file. Ordered by Claude's Nov 2 recheck via Rushabh; needs no contract change.

## Approved dependencies

Python runtime (versions are the latest stable on Oct 28, 2026):

| Package | Version floor | Why |
|---|---|---|
| fastapi | 0.162.0 | API |
| uvicorn | 0.54.0 | ASGI server (no `[standard]` extras: avoids compiled wheels) |
| python-multipart | 0.0.32 | PDF uploads |
| pydantic | 2.14.1 | Contracts |
| pydantic-settings | 2.16.2 | Config from env |
| openai | 3.40.0 | Client for Meta Responses API and OpenAI-compatible Chat APIs |
| httpx | 0.28.1 | Fetching public PDFs |
| pymupdf | 2.0 | PDF text, images, page renders (AGPL-3.0: fine for a public open-source repo; say so in README) |
| pillow | 13.0.0 | Images, overlays |
| numpy | 2.5.4 | dHash, row maths |

Python dev: `pytest 9.2.0`, `pytest-asyncio 1.4.1`, `pytest-cov 7.1.0`, `respx 0.23.1`, `ruff 0.17.3`.

Tooling: `uv 0.13.6`, Python `3.15.2`, Modal CLI `modal 1.6.3` (installed as a uv tool, not a project dependency).

Frontend: `svelte 5.57.3`, `vite 8.3.5`, `@sveltejs/vite-plugin-svelte 7.3.1`, `typescript 6.0.3` (pinned), `svelte-check 4.7.7`. Node 22.12+ (Vite 8 needs `^20.19.0 || >=22.12.0`).

Nothing else. No chart library (charts are small hand-written SVG), no CSS framework, no state library.

## Repo layout

```
stackcheck/
├── AGENTS.md                  # coding agent rules (this kit)
├── README.md                  # public project README (written in T15)
├── PROGRESS.md                # task log, updated after every card
├── STUCK.md                   # created only when blocked
├── pyproject.toml             # deps, ruff, pytest config
├── uv.lock
├── .python-version            # 3.15
├── .env.example               # every env var, no values
├── .gitignore
├── docs/                      # SPEC, ARCHITECTURE, CONTRACTS, DATA_AND_LEGAL, FACTS, PLAN, KPIS, DEMO, SETUP, REVIEW_FOR_OPUS, KIT_GUIDE
├── tasks/                     # task cards T00..T15, S1..S3
├── src/stackcheck/
│   ├── __init__.py
│   ├── config.py
│   ├── errors.py
│   ├── models.py
│   ├── cache.py
│   ├── costs.py
│   ├── llm/       base.py  meta.py  openai_compat.py  structured.py  registry.py
│   ├── ingest/    pdf.py  sources.py
│   ├── extract/   filing.py  photos.py  prompts/filing_v1.md  prompts/photo_v1.md  prompts/investigate_v1.md  prompts/summary_v1.md
│   ├── vision/    sam.py  sam_parse.py  floors.py  phash.py  overlay.py
│   ├── rules/     checks.py  engine.py
│   ├── pipeline/  run_check.py  investigate.py  jobs.py
│   ├── report/    build.py
│   └── api/       app.py
├── scripts/                   # smoke_meta.py, probe_*.py, spike_sam.py, bench_floors.py, eval_extract.py, compare_providers.py, run_project.py, kpi_report.py
├── tests/
│   ├── conftest.py            # synthetic PDF and image factories, fake clients
│   ├── unit/                  # one file per module
│   └── e2e/test_pipeline_smoke.py
├── data/
│   ├── sources.csv            # committed
│   ├── truth/                 # committed: hand-labelled facts for eval (numbers only)
│   ├── bench/                 # committed: Commons images, labels.csv, ATTRIBUTION.md
│   ├── samples/               # committed: sample reports with photo bytes stripped
│   ├── ledger.jsonl           # git-ignored
│   ├── cache/                 # git-ignored
│   └── work/                  # git-ignored
├── web/                       # Vite + Svelte 5 app, builds to web/dist
└── deploy/modal_app.py
```
