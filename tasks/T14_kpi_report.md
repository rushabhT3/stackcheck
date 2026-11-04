# T14: KPI report

**Owner:** Muse Code. **Day:** D6 (usable from D3 for the parts that exist). **Read:** AGENTS.md; `docs/KPIS.md` in full; CONTRACTS C7.

## Goal

One command prints every KPI from KPIS.md with its target and PASS / FAIL / NO DATA, and writes `docs/kpi_snapshot.md` (committed daily, so the history shows progress).

## Files

Create: `scripts/kpi_report.py`, `tests/unit/test_kpi_report.py`.

## Steps

1. Gather, each in its own small function that returns `None` when the source is missing:
   - tests: run `uv run pytest -q --junitxml=data/eval/junit.xml --cov=stackcheck.rules --cov=stackcheck.vision --cov-branch --cov-report=json:data/eval/coverage.json` as a subprocess; read pass/fail counts and branch coverage of the four files named in KPIS;
   - ledger: total USD, USD per day (by `ts` date in IST), cache-hit share for the most recent rerun of any `check_id` that ran twice, cost per `check_id`;
   - `data/eval/extract_*.json`, `photos.json`, `bench_floors.json`, `providers.json`;
   - `data/work/*/status.json`: median seconds per quarter (report step time / quarters);
   - `STUCK.md`: number of open items (`## ` headings without "RESOLVED");
   - `data/samples.json` and `data/samples/verified.csv`: projects checked, FLAGs shown, FLAGs hand-verified.
2. A table of KPI definitions in code mirroring KPIS.md: name, value function, target, comparison (`>=` or `<=`), red line. Status: PASS when the target is met, FAIL past the red line, WARN in between, NO DATA when the value is None.
3. Output: the table to stdout and `docs/kpi_snapshot.md` with a header line `Snapshot: <IST timestamp>`.
4. `--no-tests` flag skips the pytest run (fast mode).

## Tests first

`test_kpi_report.py`: status logic for `>=` and `<=` targets including the WARN band and NO DATA; ledger aggregation on a 5-line synthetic ledger (per-day sums in IST, cost per check); STUCK counting with one resolved and one open item.

## Done when

`uv run python scripts/kpi_report.py` prints the table and writes the snapshot; values match a manual check of the ledger total.

## How an expert thinks about it

KPIs nobody looks at are decoration. Making them one command, run at each day's gate, turns "I think it's going fine" into a table that says which number is red and which fallback to apply.

## Traps

- Day boundaries in IST, not UTC (the plan's days are IST).
- Don't fail the script when a source is missing; show NO DATA.
