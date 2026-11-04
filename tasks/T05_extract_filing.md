# T05: Read a filing into structured facts, and measure accuracy

**Owner:** Muse Code, with hand-labelled truth from the human. **Day:** D2. **Read:** AGENTS.md; CONTRACTS C3 (`QuarterFiling` and parts), C4 (`ask_json`), C5 (`filing_v1`); `docs/KPIS.md` row "Filing field accuracy".

## Goal

`extract_filing` turns an `IngestedDoc` into a `QuarterFiling` with page references, and `eval_extract.py` measures field accuracy against hand-checked truth for 5 real filings.

## Files

Create: `src/stackcheck/extract/filing.py`, `scripts/eval_extract.py`, `tests/unit/test_extract_filing.py`, `data/truth/README.md`.

## Steps

1. `filing.py`:
   ```python
   async def extract_filing(doc: IngestedDoc, client: ModelClient, *, check_id: str | None) -> QuarterFiling
   ```
   - Text mode (`not doc.is_scanned`): build `{pages}` per C5, prepend `"Document id: {doc_id}\n"`, call `ask_json(..., prompt_name="filing_v1", schema=QuarterFiling, purpose="extract_filing")`.
   - Scanned mode: page renders from `render_page(...)` in batches of ≤ 10 pages, one `ask_json` per batch, then merge: for scalar fields the first non-null value in page order wins; `blocks`, `activities`, `notes` are concatenated with duplicates removed (blocks by label, activities by normalised name); `refs` merged, first wins.
   - Post-process in code (never trust the model for these): set `doc_id` to `doc.source.doc_id`; drop any `PageRef` whose page is outside `1..page_count`; trim `notes` to 5; normalise activity names (strip, collapse spaces).
2. `data/truth/README.md`: how to label. One JSON file per filing, `data/truth/<doc_id>.json`, same shape as `QuarterFiling` but only these fields are scored: `project_reg_no`, `quarter_end`, each block's `floors_above_ground_sanctioned`, `slabs_claimed_count`, `slabs_claimed_percent`, every `financials` value and `currency_unit`, the three certificate flags, `photo_section_present`, and each activity's `percent_done` matched by normalised name. Truth `null` matched by extracted `null` counts as correct.
3. `scripts/eval_extract.py`:
   - `--make-template <check_id or pdf path>`: runs extraction and writes `data/truth/<doc_id>.template.json` for the human to correct and rename;
   - default: for every `data/truth/*.json` (not templates), find the PDF in `data/work/**/sources/<doc_id>.pdf` or `data/uploads/`, extract with the provider in `--provider` (default from settings), score per field, print a table (field, correct/total) and the overall accuracy, write `data/eval/extract_<provider>.json` with per-field results, cost from the ledger and wall time.

## Tests first

`test_extract_filing.py` (fake `ModelClient` returning prepared JSON strings):
- valid JSON gives a `QuarterFiling` whose `doc_id` is the source's even if the model returned another;
- a `PageRef` with page 99 on a 3-page doc is dropped;
- scanned 23-page doc makes 3 calls with ≤ 10 images each;
- merge: first non-null scalar wins; two batches both listing activity "Slabs of Super Structure" give one activity;
- the request text contains `[[PAGE 1]]` and the document id;
- scoring function (put it in `eval_extract.py` as `score(truth, extracted) -> dict`; import it in the test): null vs null correct, 3 vs 3 correct, 3 vs 4 wrong, activity matched by normalised name, missing activity counted wrong.

## Done when

Tests green. Human labels at least 3 filings (target 5) using `--make-template`, runs `eval_extract.py` live, and the agent records the accuracy table in PROGRESS.md. Gate G2 needs ≥ 90% on the real filing used for the skeleton run.

## How an expert thinks about it

The model is a fast clerk, not a judge. Its job is copying numbers with their page, and the prompt's rules exist to stop the three classic clerk errors: guessing missing values, confusing a count with a percentage, and mixing units. Measure on real filings early, because QPR layouts vary by state and promoter, and only real ones show where the clerk slips. When a field fails, fix the one rule that covers it, bump the prompt version, re-measure.

## Traps

- Don't let the model compute anything (no totals, no conversions). Code converts units later (`to_inr`).
- Don't send more than 60 pages in text mode.
- A truth file must never contain names or registration numbers in the public repo: the README tells the human to set `project_name`, `promoter_name` to null in truth files and keep `project_reg_no` only in a private copy if needed. Score `project_reg_no` only when present in truth.
