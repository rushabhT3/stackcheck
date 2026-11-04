# KPIS: how to tell, every day, whether StackCheck is on track

`scripts/kpi_report.py` (T14) prints all of these from the test results, the eval files and `data/ledger.jsonl`, and writes `docs/kpi_snapshot.md`. Run it at the end of each day, before the gate check in PLAN.

## A. Build health (is the project going right?)

| KPI | Target | Red line | How it's measured | When it's off |
|---|---|---|---|---|
| Daily gate passed | Yes | Two gates missed in a row | PLAN gate list | Apply that gate's fallback the same day; don't carry the miss forward |
| Unit tests passing | 100% | Any failure at end of day | `uv run pytest -q` | Fix before starting the next card |
| Branch coverage of `rules/`, `vision/floors.py`, `vision/sam_parse.py`, `vision/phash.py` | 100% | < 95% | `uv run pytest --cov=stackcheck.rules --cov=stackcheck.vision --cov-branch --cov-report=term-missing` | Add the missing case tests |
| End-to-end smoke passes | Every day from D2 | Fails at end of day | `tests/e2e/test_pipeline_smoke.py` (synthetic PDF, fake clients) | Nothing else ships until it passes |
| Credit spent (all Meta usage) | ≤ $10 per day average | > $40 by end of D4, or > $100 total | `ledger.jsonl` for the app, plus the Meta Billing page for Muse Code | Check cache hit rate; shorten Muse Code sessions; move reruns to cached data |
| Cache hit rate on a rerun of the same check | ≥ 95% | < 80% | ledger `cache_hit` share for the second run | A cache key includes something unstable (a timestamp, a temp path): fix it |
| Time for one quarter's check (cold) | ≤ 90 s | > 180 s | `status.json` step times | Limit photos per quarter to the 8 largest elevations; run SAM prompts concurrently (max 4) |
| Open STUCK items | 0 at end of day | Any item older than 1 day | `STUCK.md` | Bring it to Opus or the Discord office hours |

## B. Product quality (is StackCheck right?)

| KPI | Target | Red line | How it's measured | When it's off |
|---|---|---|---|---|
| Floor count within ±1 (non-abstained) | ≥ 80% | < 60% | `scripts/bench_floors.py` on `data/bench/labels.csv` (30+ Commons photos) | Try the next SAM prompt pair from T03's results; tighten AGREE_TOLERANCE |
| Wrong-and-confident rate (status OK but off by ≥ 2) | ≤ 5% | > 10% | same script | This is the most important number. Raise MIN_ROWS, set AGREE_TOLERANCE to 0. More abstains are fine |
| Abstain rate on the benchmark | ≤ 35% | > 50% | same script | Investigate step (T09) should recover some; check if building boxes are missed |
| Filing field accuracy | ≥ 95% | < 85% | `scripts/eval_extract.py` vs `data/truth/*.json` (5 filings you labelled by hand) | Fix the prompt rule for the failing field; bump prompt version |
| Photo kind accuracy (elevation vs other) | ≥ 90% | < 80% | eval on 40 QPR photos you labelled | Tighten the kind definitions in `photo_v1` |
| Stamp date read correctly | ≥ 90% of stamped photos | < 75% | same 40 photos | Day-first rule; return null when unsure |
| Hand-verified share of FLAGs shown publicly | 100% | anything less | `data/samples/verified.csv` | Don't show unverified FLAGs |
| Real projects checked | ≥ 5 | < 3 | `data/samples/` | Use UP RERA uploads or Telangana uploads |
| Real FLAGs (any rule, hand-verified) | ≥ 1 | 0 by D6 | same | Demo leads with the honest "no gaps found" plus what it caught in the constructed example, labelled as such |
| Cost per project check (8 quarters, cold) | ≤ $0.50 | > $2.00 | ledger sum per `check_id` | Fewer photos per quarter; contributor tier for public filings |
| Provider comparison done | Meta vs Baseten vs Fireworks on the same 5 filings | Missing on D6 | `scripts/compare_providers.py` | Run it with whichever two providers work |

## C. Red flags that mean "change course now"

- End of D1 with no SAM boxes on any photo in any mode.
- End of D2 with no real filing extracted.
- End of D3 with wrong-and-confident above 15%.
- More than $60 spent by end of D3.
- Muse Code repeatedly changing contracts or adding dependencies: restart the session with a stricter first message quoting AGENTS.md section 4.

## D. Credit plan (Meta $150)

| Bucket | Budget | Notes |
|---|---|---|
| Muse Code sessions (contributor) | $50 | About 15 to 20 sessions; cached input costs $0.002 per 1M, so long sessions are cheap if context is reused |
| App development runs | $25 | Cache makes reruns free |
| Benchmarks and evals | $20 | 30 photos × 3 SAM prompts ≈ $0.23 per run |
| Real project checks | $15 | 5 to 10 projects at under $0.50 each |
| Buffer | $40 | Don't touch before D6 |

Partner credits: Baseten ($100) and Fireworks pay for T12 and for the reader step if an open model becomes the default. Modal ($100 plus $30/month free) pays for hosting: one warm small CPU container during judging week.

Spend notifications on the Meta Billing page: $50, $100, $140. The app also stops itself at `STACKCHECK_BUDGET_USD` and warns at `STACKCHECK_ALERT_USD`.
