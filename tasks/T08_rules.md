# T08: The rules engine (R1 to R8)

**Owner:** Muse Code. **Day:** D4. **Read:** AGENTS.md; CONTRACTS C3 (`ProjectEvidence` and below, `Finding`, `Evidence`), C14 in full; SPEC "The checks"; AGENTS.md section 4 wording rules.

## Goal

Eight pure functions that turn a project's evidence into findings, each with status, severity, plain-words title and explanation, and evidence that points to a page, a photo or a computation. 100% branch coverage.

## Files

Create: `src/stackcheck/rules/checks.py`, `rules/engine.py`, `tests/unit/test_rules_helpers.py`, `tests/unit/test_rules_r1.py` ... `test_rules_r8.py`, `tests/unit/test_engine.py`. Extend `tests/factories.py` with `make_quarter(...)` and `make_project(...)` builders.

## Steps

1. Factories: `make_quarter(quarter_end, slabs=None, percent=None, floors=None, activities: dict[str, float] | None = None, fin: dict | None = None, photos: list[PhotoRecord] = (), sha="...", state="Bihar", block="MAIN")` and `make_photo(image_id, kind="elevation", lower=None, upper=None, status="OK", stamp=None, dhash="0"*16, block_guess=None, top_obscured=False)`.
2. Helpers in `checks.py` per C14: `quarter_start`, `to_inr`, `claimed_slabs`, `photo_in_window`, `RuleSettings`.
3. One function per rule, named `r1_photo_vs_claim`, `r2_reused_photo`, `r3_photo_date`, `r4_claim_regression`, `r5_copy_paste_quarter`, `r6_money_vs_progress`, `r7_arithmetic`, `r8_missing_quarter`. Each follows C14 exactly and builds evidence:
   - quotes: the filing's field value with its `PageRef` from `filing.refs` or the block/activity/financials `ref`;
   - photos: `image_id` and `overlay_path` when present;
   - computations: a `value` string showing the arithmetic, e.g. `"(7 - 1) - 3 = 3 floors short"`;
   - `inputs`: every number used, as strings.
4. Titles and explanations: plain words, factual, under the length limits, never the forbidden words. Money formatted Indian style (`₹1,01,05,126`), dates as `31 Mar 2025`. Write two small formatting helpers and test them.
5. `engine.run_rules(project, rule_settings)` per C14: run all eight, assign `finding_id`s (`f"{rule}:{quarter_end or 'all'}:{block or 'all'}:{n}"`, `n` counting within that prefix from 1), sort.

## Tests first

`test_rules_helpers.py`:
- `quarter_start`: 2025-03-31 → 2025-01-01; 2025-06-30 → 2025-04-01; 2025-12-31 → 2025-10-01;
- `to_inr`: 2.5 lakh → 250000; 1.2 crore → 12000000; INR passes through; None stays None;
- `claimed_slabs`: count 3 → 3; percent 40 with floors 4 → round(0.4 × 5) = 2; percent without floors → None; count wins over percent;
- `photo_in_window`: None → None; quarter_start − 45 days → True; one day earlier → False; quarter_end + 15 → True; + 16 → False;
- Indian money format: 10105126 → `₹1,01,05,126`; 999 → `₹999`.

One file per rule. Each must cover every branch in C14. Minimum cases:
- **R1:** no claim → CANT_VERIFY; no usable photo → CANT_VERIFY; claim 3, photo 3–3 → OK; claim 7, photo 3–3 → FLAG high (gap 3); claim 6, photo 3–4 → FLAG medium (gap 1); claim 7, photo lower 4 upper None (obscured) → CANT_VERIFY (overlap); photo with stamp outside window excluded → CANT_VERIFY; photo with block guess "Block B" not used for block "MAIN"; photo with `floors.status == "CANT_VERIFY"` not used.
- **R2:** one quarter only → CANT_VERIFY; two quarters, distinct hashes → OK; distance 6 → FLAG; distance 7 → no flag; both elevations with different claims → high; one floor_level photo → medium; `document_or_logo` ignored; same photo twice within one quarter → no FLAG.
- **R3:** no photos → CANT_VERIFY; stamp outside window → FLAG medium; Bihar with 3 of 4 photos unstamped → FLAG low; non-Bihar state with unstamped photos → OK.
- **R4:** single quarter → CANT_VERIFY; slabs 5 → 4 → FLAG high; activity 60 → 57 → FLAG medium; 60 → 59 → OK (within 2 points); different blocks not compared.
- **R5:** identical sha256 → FLAG high; 6 shared activities all equal + same cumulatives → FLAG medium; all equal + spent this quarter 5 lakh → FLAG low; 4 shared activities → not enough → CANT_VERIFY; changes → OK.
- **R6:** missing cost → CANT_VERIFY; spent 60% vs progress 30% → FLAG low and explanation contains "Land and approval costs"; spent 40% vs progress 30% → OK; progress from slabs when < 3 activities.
- **R7:** within-filing cumulative < quarter → FLAG low; across quarters mismatch of ₹50,000 on ₹1 crore (0.5%, below 1%) → OK; mismatch of ₹5 lakh on ₹1 crore → FLAG medium; units mixed (lakh in A, INR in B) converted correctly; nothing → CANT_VERIFY.
- **R8:** quarters Mar, Jun, Dec → FLAG medium naming Sep; consecutive → OK; one filing → CANT_VERIFY; explanation ends with "the portal may hold more."

`test_engine.py`: ids unique and stable across two runs; order is severity then rule; a project with nothing gives only CANT_VERIFY findings; no finding text contains "fraud", "scam", "lying" or "cheat" (scan all titles and explanations from all tests' projects).

## Done when

All tests green and `uv run pytest --cov=stackcheck.rules --cov-branch --cov-report=term-missing` shows 100% for `checks.py` and `engine.py`.

## How an expert thinks about it

This is the part judges and buyers must be able to trust, so it's the part with no AI in it. Each rule is a sentence a lawyer could read, written as code a test can pin down. The tolerances (S − 1, 2 points, 1%, 45 days) all lean toward not flagging: a missed gap costs little, a false gap costs a lot. When in doubt, CANT_VERIFY, and say exactly what was missing.

## Traps

- Always convert money with `to_inr` before comparing; units can differ between quarters.
- Sort quarters by `quarter_end`; skip filings with `quarter_end` None in pairwise rules.
- Don't let R2 compare a photo with itself.
- Keep explanations factual: "The report claims 7 slabs (page 4). The elevation photo filed with it shows 3 floors." Not "The builder overstated progress."
