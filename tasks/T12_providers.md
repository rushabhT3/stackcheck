# T12: Compare readers: Muse Spark 1.3 vs Muse Spark Open on Baseten and Fireworks

**Owner:** Muse Code builds the script; the human runs it. **Day:** D6. **Read:** AGENTS.md; ARCHITECTURE D06; CONTRACTS C4, C7; FACTS "Partners".

## Goal

Measure field accuracy, cost and speed of the filing reader on the same truth filings for three providers, pick the default by a fixed rule, and get real numbers for the README and the video.

## Files

Create: `scripts/compare_providers.py`, `tests/unit/test_compare_rule.py`.

## Steps

1. Human first: find the Baseten price for `meta-models/muse-spark-open-1026` (Baseten pricing page or the dashboard's usage view). Update the Baseten row in the `costs.py` price table and mark it verified in FACTS. If not found, keep the estimate and say so in the README.
2. Script: for each provider in `meta`, `baseten`, `fireworks` (skip any without a key, print SKIPPED):
   - run `extract_filing` on every truth filing with that provider (cache keys differ by provider, so each provider is really called once);
   - score with `eval_extract.score`;
   - collect cost per filing from the ledger and wall time per filing (cold, first run only);
   - write `data/eval/providers.json` and print a markdown table: provider, model, accuracy, cost per filing, p50 seconds.
3. Choice rule, as a pure function `choose_default(results) -> str` in the script:
   - candidates = open providers (`baseten`, `fireworks`) whose accuracy ≥ meta accuracy − 0.02;
   - if any, pick the cheapest per filing (ties: faster); else `meta`.
   Print the chosen value and the exact line to put in `.env` and the Modal secret: `STACKCHECK_EXTRACT_PROVIDER=<value>`.
4. Fireworks base URL: if the first call fails with a 404 or connection error, print the error and stop; the human checks the Fireworks quickstart and sets `FIREWORKS_BASE_URL`.

## Tests first

`test_compare_rule.py`: meta 0.96 / baseten 0.95 / fireworks 0.93 → baseten; meta 0.96 / baseten 0.90 / fireworks 0.90 → meta; both open within 2 points, fireworks cheaper → fireworks; equal cost, baseten faster → baseten; providers missing → handled.

## Done when

Table recorded in PROGRESS.md and `docs/kpi_snapshot.md`; default provider set. If an open model is the default, note in the README that the reading step runs on Muse Spark Open (this is what the "Best Build on Meta Open Weight Models" bonus looks for).

## How an expert thinks about it

The bonus prize is a reason to try open weights, not a reason to ship a worse reader. A 2-point tolerance is a stated, testable trade-off, decided before seeing the numbers, so nobody can say the rule was bent to win a prize.

## Traps

- Uploads always stay on Meta's standard tier whatever the default is (D07). Don't touch `registry.py`'s origin rule.
- Don't compare on filings used to tune the prompt only; if you changed `filing_v1` while looking at a filing, say so in the table notes.
