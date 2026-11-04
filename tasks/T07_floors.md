# T07: Floor counting, overlays, benchmark

**Owner:** Muse Code, with a live benchmark run by the human. **Day:** D3. **Read:** AGENTS.md; CONTRACTS C3 (`Detection`, `FloorCount`), C10, C11, C13; KPIS section B rows on floor counting.

## Goal

Turn SAM detections plus Muse Spark's own count into a `FloorCount` that is either a trustworthy range or an honest `CANT_VERIFY`, draw it on the photo, and measure it on the Commons benchmark.

## Files

Create: `src/stackcheck/vision/floors.py`, `vision/overlay.py`, `scripts/bench_floors.py`, `tests/unit/test_floors.py`, `tests/unit/test_overlay.py`, `tests/factories.py` (detection builders).

## Steps

1. `tests/factories.py`: `grid_detections(rows, cols, building=Box(...), h=20, w=30, conf=0.9, prompt="window")` that places boxes in a regular grid inside the building; `building_detection(box, conf)`.
2. `floors.py` per C11, exactly: constants, `pick_building_box`, `group_rows`, `count_floors`. Pure.
3. `overlay.py` per C13. The output image is the original with a 28 px bar added above it (new height = h + 28).
4. `scripts/bench_floors.py` (live):
   - arguments: `--prompts` (default from settings), `--labels data/bench/labels.csv`;
   - for each labelled image: SAM `"building"` plus each element prompt (all cached), parse, `describe_photo` with a dummy filing (quarter "unknown", blocks `["MAIN"]`), `count_floors`;
   - metrics on all images: `within1` = share of OK results whose range contains a value within 1 of the label; `wrong_confident` = share of all images that are OK but with `min(|lower - label|, |upper - label|) >= 2` (use `lower` when `upper` is None); `abstain` = share CANT_VERIFY;
   - `--compare "balcony,window;window,window opening;balcony,concrete column"` runs several prompt sets and picks the best by: highest `within1` among sets with `wrong_confident <= 0.05`, ties broken by lower `abstain`. If no set meets 0.05, pick the lowest `wrong_confident`;
   - write `data/eval/bench_floors.json`, print the table, draw overlays for the 6 worst errors into `data/eval/bench_overlays/` for a human look.

## Tests first

`test_floors.py` (table-driven, all pure):
- 5 rows × 4 cols, vision 5: OK, lower 5, upper 5, `sam_rows` 5;
- 5 rows × 4 cols, vision 6: OK, lower 5, upper 6;
- 5 rows, vision 7: CANT_VERIFY, reason mentions "disagree";
- 5 rows, vision None: CANT_VERIFY, "only one method";
- 2 rows, vision 5: CANT_VERIFY, "only one method";
- 2 rows, vision None: CANT_VERIFY, "not enough evidence";
- no detections at all: CANT_VERIFY, "no building found";
- 5 rows, vision 5, `top_obscured` True: OK, lower 5, upper None;
- detections below `MIN_CONF` are ignored (5 good rows plus 3 rows at conf 0.2 still gives 5);
- detections outside the building box are ignored;
- narrow building (1 column) keeps rows of 1;
- no "building" detection: box inferred, `building_box_inferred` True, `sam_pitch_estimate` None;
- with a SAM building box and 5 evenly spaced rows: `sam_pitch_estimate` is computed;
- rows spaced 3 detection-heights apart, with each box's y jittered by up to ±20% of the detection height, still group into the right number of rows.

`test_overlay.py`: output exists, is PNG, size is (w, h + 28); drawing on a 1-row result doesn't crash.

## Done when

Tests green, 100% branch coverage on `floors.py`. Human runs `bench_floors.py --compare ...` live; the agent records the table, the chosen prompt set (update `sam_prompts` default and decision log D16 if it changes) and the three KPIs in PROGRESS.md. Gate G3: `within1 ≥ 0.70` and `wrong_confident ≤ 0.10`.

## How an expert thinks about it

Two weak counters that fail differently make one strong counter, if you only accept their agreement. SAM misses rows (bad angle, shadows) and Muse Spark misjudges tall buildings; they rarely fail the same way at the same time. Abstaining on disagreement costs coverage, which the bounded agent (T09) wins some of back. Wrong-and-confident is the number to protect, because in this product a confident wrong count becomes an accusation.

## Traps

- Image coordinates grow downward: the bottom row has the largest `cy`. Label rows from the bottom in the overlay.
- Use the median, not the mean, for typical box size; one huge false box shouldn't move the row gap.
- Don't tune constants on the same images you report: if you change a constant after seeing results, say so in PROGRESS.md and keep the original numbers too.
