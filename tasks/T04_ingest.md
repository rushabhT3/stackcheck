# T04: PDF ingest and polite fetching

**Owner:** Muse Code. **Day:** D2. **Read:** AGENTS.md; CONTRACTS C1, C3 (`SourceDoc`, `PageText`, `ExtractedImage`, `IngestedDoc`), C8, C9; DATA_AND_LEGAL "Fetch etiquette".

## Goal

Turn a QPR PDF (from an allowed URL or an upload) into page texts and extracted photos with exact page numbers, without ever behaving like a crawler.

## Files

Create: `src/stackcheck/ingest/pdf.py`, `ingest/sources.py`, `tests/unit/test_ingest_pdf.py`, `tests/unit/test_sources.py`. Extend `tests/conftest.py` with factories.

## Steps

1. Test factories in `conftest.py` (no real filings, ever):
   - `make_png(width, height, pattern) -> bytes` with patterns `"stripes"`, `"checker"`, `"gradient"`, `"building"` (a grey rectangle with a grid of darker small rectangles: 4 columns × N rows, to imitate a facade);
   - `make_pdf(path, pages: list[dict]) -> Path` using `pymupdf`: each dict may have `"text"` (inserted with `page.insert_text`) and `"images"` (list of PNG bytes placed with `page.insert_image`);
   - `make_encrypted_pdf(path)`.
2. `pdf.py` per C8. `image_id = f"{doc_id}-p{page}-i{index}"`. Save images as PNG under `out_dir/images/`. Return `IngestedDoc`.
3. `sources.py` per C9:
   - `doc_id = sha256[:16]`;
   - host check, https check, manual same-host redirects (max 3), size cap while streaming, `%PDF` magic check;
   - per-host spacing with a module-level dict of `asyncio.Lock` and last request time; take `sleep` and `monotonic` as injectable parameters (defaults `asyncio.sleep`, `time.monotonic`) so tests never really wait;
   - `register_upload` validates `%PDF` and size, saves to `dest_dir/sources/{doc_id}.pdf`, origin `"upload"`.

## Tests first

`test_ingest_pdf.py`:
- 3-page PDF with text on each page: 3 `PageText` with pages 1, 2, 3 and the text present;
- one 400×300 image on page 2: one `ExtractedImage` with `page == 2`, `index == 0`, file exists, width and height right;
- a 100×100 image is skipped;
- two images on one page get indexes 0 and 1;
- PDF with no text and one 800×1000 image: `is_scanned` is True and images come from extraction (not render);
- PDF with no text and no images: `is_scanned` is True and every page is rendered with `from_page_render=True`;
- encrypted PDF raises `IngestError`.

`test_sources.py` (httpx mocked with `respx`, fake sleep and clock):
- `http://` URL raises `FetchError`;
- host not in allow-list raises `FetchError` and makes no request;
- 200 with `%PDF` body saves the file, `doc_id` is the first 16 hex chars of its SHA-256, `origin == "url"`;
- body not starting with `%PDF` raises;
- body larger than `fetch_max_bytes` raises (set the cap to 1,000 in the test);
- redirect to the same host is followed; redirect to another host raises;
- two fetches to one host: the fake sleep is called with about `fetch_min_interval_s` before the second;
- User-Agent header is `StackCheck/0.1 (hackathon research; contact via GitHub)`;
- `register_upload` rejects non-PDF bytes.

## Done when

Tests green, ruff clean. Human runs `uv run python -c "..."` (the agent prints the one-liner) to ingest one real QPR from `data/sources.csv` and reports page count, image count and `is_scanned`. Record in PROGRESS.md.

## How an expert thinks about it

Evidence is only as good as its address. Every later finding points to "page 7 of doc 3f9a...", so page numbers and content hashes have to be right here. The fetch rules are written as code, not intentions, because "be polite" isn't testable but "wait 5 seconds and refuse other hosts" is.

## Traps

- `import pymupdf`, not `import fitz`.
- PyMuPDF page numbers are 0-based; contracts are 1-based.
- Some PDF images are CMYK or have alpha masks: convert with Pillow to RGB before saving PNG.
- Don't follow redirects automatically (`follow_redirects=False`), or the host check is bypassed.
