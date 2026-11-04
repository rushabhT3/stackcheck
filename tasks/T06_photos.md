# T06: Photo facts and image hashing

**Owner:** Muse Code, with 40 hand labels from the human. **Day:** D3. **Read:** AGENTS.md; CONTRACTS C3 (`PhotoFacts`, `PhotoRecord`), C5 (`photo_v1`), C12.

## Goal

For each photo in a filing: what kind it is, what its stamp says (date, place), and how many storeys Muse Spark sees, plus a 64-bit dHash for spotting reused photos.

## Files

Create: `src/stackcheck/extract/photos.py`, `src/stackcheck/vision/phash.py`, `scripts/eval_photos.py`, `tests/unit/test_phash.py`, `tests/unit/test_extract_photos.py`, `data/truth/photos_README.md`.

## Steps

1. `phash.py` per C12. Pure functions.
2. `photos.py`:
   ```python
   def select_photos(doc: IngestedDoc, limit: int = 12) -> list[ExtractedImage]
   async def describe_photo(image: ExtractedImage, image_path: Path, filing: QuarterFiling,
                            client: ModelClient, *, check_id: str | None) -> PhotoFacts
   ```
   - `select_photos`: largest first by width × height, at most `limit`; page renders only when no extracted images exist.
   - `describe_photo`: one image, prompt `photo_v1` filled with image id, quarter end (or "unknown") and block labels (or "MAIN"); `ask_json` with `schema=PhotoFacts`, purpose `"describe_photo"`.
   - Post-process in code: set `image_id`; `stamp_date` outside 2015-01-01 to today + 1 day becomes `None`; latitude outside −90..90 or longitude outside −180..180 becomes `None`; `vision_storeys_visible` forced to `None` unless kind is `elevation`; negative or > 120 storeys becomes `None`.
3. `data/truth/photos_README.md` and `data/truth/photos.csv` header: `sha256,kind,stamp_date,storeys_visible,top_obscured`. The human labels 40 photos from real filings by their SHA-256 (no image bytes or names in the repo).
4. `scripts/eval_photos.py` (live): for every labelled SHA found in `data/work/**/images/`, run `describe_photo`, report kind accuracy, stamp-date accuracy (on photos whose truth has a date), storeys within ±1 (elevations), and write `data/eval/photos.json`.

## Tests first

`test_phash.py`:
- identical bytes give distance 0 and a 16-character lowercase hex string;
- the same synthetic "building" image saved as JPEG quality 60 and resized to 70% gives distance ≤ 6;
- "stripes" vs "checker" gives distance ≥ 20;
- `hamming("0" * 16, "f" * 16) == 64`.

`test_extract_photos.py` (fake client):
- `select_photos` returns at most 12, largest first, and uses page renders only when there are no extracted images;
- the prompt text includes the image id, the quarter end and the block labels;
- `image_id` is overwritten with the real one;
- a stamp date in 2031 becomes `None`; 2013 becomes `None`;
- `vision_storeys_visible` from a `floor_level` answer becomes `None`;
- latitude 123 becomes `None`.

## Done when

Tests green. Human labels 40 photos and runs `eval_photos.py` live; results go in PROGRESS.md. KPI targets: kind ≥ 90%, stamp date ≥ 90%.

## How an expert thinks about it

The photo stamp is the cheapest strong evidence in the whole product: when a phone app prints "12/05/2023" on a photo filed for March 2025, that is checkable by anyone, no AI needed to believe it. So reading the stamp exactly, and refusing to guess an ambiguous date, matters more than anything clever. dHash is enough for reuse because a reused photo is usually the same file re-saved, not a new picture.

## Traps

- Day-first dates. "05/03/2025" is 5 March 2025 in India. The prompt says so; the post-processing must not "fix" it.
- Don't send all photos in one request: one photo per call keeps answers attached to the right image and cacheable.
- Don't hash page renders that are mostly text; `kind == "document_or_logo"` photos are excluded later by R2, but keep their hash anyway (cheap, useful for debugging).
