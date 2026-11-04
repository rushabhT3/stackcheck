# FACTS: what was verified, where, and what is still open

Checked on Oct 28, 2026. When the agent confirms an UNVERIFIED item with a probe, it moves the line to "Verified" with the probe's date and output summary.

## Verified

### Meta Model API (dev.meta.ai/docs)
- Base URL `https://api.meta.ai/v1`. Bearer auth. OpenAI SDK works; pass the key explicitly (env var name used in docs: `MODEL_API_KEY`). Source: Quickstart.
- Responses API (`client.responses.create`) is the main interface; Chat Completions also documented. Source: Quickstart.
- Model ids: `muse-spark-1.3`, `muse-spark-1.3-contributor`, `sam-3.1`. Source: Quickstart, Pricing and rate limits, SAM overview.
- Prices per 1M tokens: `muse-spark-1.3` $1.25 in, $0.15 cached, $4.25 out. `muse-spark-1.3-contributor` $0.10 in, $0.002 cached, $0.20 out. Contributor mode is chosen by model id. Source: Pricing and rate limits.
- Rate limits per team, per model: `muse-spark-1.3` 3,000 RPM and 4M TPM; `muse-spark-1.3-contributor` 150 RPM and 3M TPM. Source: Pricing and rate limits.
- SAM 3.1: $2.50 per 1,000 images; video $0.20 per 1,000 frames, minimum 50 frames per video request. Source: Pricing and rate limits.
- SAM 3.1 uses `POST /v1/responses`, model `sam-3.1`, one user message with an `input_text` noun phrase and one `input_image` (or `input_video`, MP4/MOV with H.264, H.265 or MPEG-4). One concept per request. `stream: true` recommended. Output arrives as `response.output_text.delta` text in the grammar in CONTRACTS C10. Empty output means no match. Source: SAM overview.
- SAM output grammar and box fields (`x1, y1, x2, y2, w, h`, optional `c`), one line per frame, stable object ids across frames, masks base85 in a custom codec. Source: `@meta-sam/parser` 0.1.0 README (npm, published by Meta, updated Oct 6, 2026).
- Muse Spark image input: `input_image` with `image_url` as an http(s) URL or a base64 `data:` URL; or `file_id` from the Files API. Max 50 images per request, 50 MB inline per image. Source: Image understanding.
- PDF input: `input_file` with `file_data` (base64 data URL), `file_url` or `file_id`. Text read from the first 100 pages, images from the first 50. Files API: `POST /v1/files` with `purpose=user_data`. Source: File handling.
- `max` reasoning effort is not available on `muse-spark-1.3-contributor`. Source: Use Model API with coding agents.
- `reasoning={"effort": "low"}` is accepted by the Responses API for `muse-spark-1.3-contributor`. Source: scripts/smoke_meta.py live probe, Oct 28, 2026.
- The Responses API accepts the `instructions` parameter for the system text. Source: scripts/smoke_meta.py live probe, Oct 28, 2026.
- `usage` carries `input_tokens`, `input_tokens_details.cached_tokens` (0 on a first call), `output_tokens`, `output_tokens_details`, `total_tokens`. Source: scripts/smoke_meta.py live probe, Oct 28, 2026.
- Image input as a base64 `data:` URL works: the model answered "right" for a half-black half-white test image. Source: scripts/smoke_meta.py live probe, Oct 28, 2026.
- SAM 3.1 accepts a base64 `data:` URL in `input_image`: 7 boxes on bench image b01 with prompt "building". Source: scripts/probe_sam_input.py live probe, Oct 28, 2026.

### Muse Code
- Terminal coding agent for Muse Spark. Install: `curl -fsSL https://dev.meta.ai/install.sh | sh` (macOS/Linux) or `irm https://dev.meta.ai/install.ps1 | iex` (Windows). Start with `muse`; one-shot with `muse exec "<prompt>"`. Source: dev.meta.ai/docs/muse-code.
- Reads `AGENTS.md` (seeded by `muse init`), falls back to `CLAUDE.md`. Approval modes via `--approval-mode on-request | untrusted | never`. `--yolo` disables approvals and the sandbox. The sandbox makes `.git`, `.muse` and `.agents` read-only inside the workspace. Skills are read from `.codex/skills` and `.claude/skills`. Source: Codersera guide, Aug 13, 2026.
- Model chosen with `--model`, e.g. `muse --model muse-spark-1.3-contributor`. Contributor allows Meta to train on prompts and completions; don't send confidential material. Source: CodeAgentSwarm guide, Sep 24, 2026.
- Default model at launch was `muse-spark-1.2`. Source: dev.meta.ai/docs/muse-code.

### Partners
- Baseten: OpenAI-compatible base `https://inference.baseten.co/v1`, Chat Completions; Muse Spark Open accepts text, image, audio and video. Hackathon model id `meta-models/muse-spark-open-1026` (the docs' `meta-models/muse-spark-open` must not be used for the hackathon). Price $1.25 input / $0.15 cached input / $4.25 output per 1M tokens, listed for `meta-models/muse-spark-open`; -1026 billing to confirm in the usage view after the T12 run. Sources: Baseten changelog; hackathon participant guide; baseten.co/pricing and baseten.co/library/muse-spark-open, checked Oct 29, 2026.
- Fireworks: model path `accounts/fireworks/models/muse-spark-open`, $1.25 in, $0.15 cached, $4.25 out per 1M, 1,040k context, image input, function calling. Source: fireworks.ai model page.
- Live text calls work on both open providers: Baseten `meta-models/muse-spark-open-1026` and Fireworks `accounts/fireworks/models/muse-spark-open` each replied "OK". Source: scripts/smoke_open.py live probe, Oct 28, 2026.
- Modal: `modal.App`, `modal.Image.debian_slim(python_version=...)`, `.uv_sync()`, `.uv_pip_install()`, `.add_local_python_source()`, `.add_local_dir(local_path, remote_path)`; `@app.function(...)` then `@modal.concurrent(max_inputs=...)` then `@modal.asgi_app()`; `min_containers` and `scaledown_window` replace `keep_warm` and `container_idle_timeout`; `modal deploy`, `modal serve`. Sources: modal.com docs (Image reference, Web functions, 1.0 migration guide).

### Versions (PyPI and npm, Oct 28, 2026)
Python 3.15.2. uv 0.13.6. fastapi 0.162.0, uvicorn 0.54.0, python-multipart 0.0.32, pydantic 2.14.1, pydantic-settings 2.16.2, openai 3.40.0, httpx 0.28.1, pymupdf 2.0 (AGPL-3.0), pillow 13.0.0, numpy 2.5.4, pytest 9.2.0, pytest-asyncio 1.4.1, pytest-cov 7.1.0, respx 0.23.1, ruff 0.17.3, modal 1.6.3. All of numpy, pillow, pydantic-core ship cp315 wheels; pymupdf and opencv ship abi3 wheels. svelte 5.57.3, vite 8.3.5 (Node `^20.19.0 || >=22.12.0`), @sveltejs/vite-plugin-svelte 7.3.1, typescript 6.0.3 (7.0.2 exists, but svelte-check 4.7.7 declares `^5 || ^6`), svelte-check 4.7.7.

### Data and legal
- YouTube Terms of Service, effective Jan 9, 2026: no accessing, reproducing or downloading content except as authorised, no automated access.
- Pexels terms: free use and modification, no attribution needed; forbids standalone redistribution, building a similar service, and scraping or automated collection including for machine learning.
- Bihar RERA QPR Form 7 requires "geo tagged and date photograph" of each block (front, rear, side elevations) and of each floor, slab progress, and money received and spent per quarter and cumulative. Filings are public at `rera.bihar.gov.in/Progress_Doc/...pdf`. One filing (APOGEE TOWER, quarter ending March 2025) states "Slabs of Super Structure Three (3)", plan "G+4", and includes architect (Form 1) and engineer (Form 2) certificates.
- Bihar RERA copyright policy: reproduction after asking by email, accurately, with credit; third-party material excluded.
- Telangana QPRs must include site photographs and are due within 15 days of each quarter; an estimated 30 to 40% of builders don't file them (Deccan Chronicle, July 13, 2025).
- UP RERA project pages show photos and videos under "View Physical and Financial Report", then "Physical Target" (CommercialNoida guide, June 22, 2025).
- Earlier SAM 3 test (Hugging Face demo, open weights, not the 3.1 API): "balcony" rows counted all 19 visible floors of a Bangalore tower; "floor slab" and "safety net" found nothing.

## UNVERIFIED (probe before relying on it)

| Item | Probe | Fallback |
|---|---|---|
| SAM `input_video` field name for the media URL or file | S1 probe | Skip video (stretch only) |
| JSON-schema structured output on Meta, Baseten, Fireworks | not needed: `ask_json` validates and repairs | |
| Baseten price for `muse-spark-open-1026` (-1026 id billing) | T12 run: confirm in the Baseten usage view | Ledger uses the listed `muse-spark-open` price, now marked verified || Hackathon $150 credit covers `muse-spark-1.3-contributor` and Muse Code pay-as-you-go | Human: run one session, check Billing next day | Switch Muse Code to a $5 Everyday subscription or standard model |
| Bihar QPR PDFs embed the photos (not just headings) | Gate 0 (T00, human) | UP RERA photos by hand, then document-first demo |
| Modal image `python_version="3.13"` | T13: image built Oct 29 on 3.13 (3.15 never tried); `add_local_python_source` skips non-.py files by default (ignore=NON_PYTHON_FILES), so the prompt .md files and the .pem need `ignore=["**/__pycache__", "**/*.pyc"]` |
| Fireworks base URL `https://api.fireworks.ai/inference/v1` | T12 smoke | Read Fireworks quickstart |
