# KPI snapshot

Snapshot: 2026-10-29 18:20 IST

| KPI | value | target | status |
|---|---|---|---|
| Unit tests passing | 100.0% | 100.0% | PASS |
| Branch coverage (rules+vision) | 92.0% | 100.0% | FAIL |
| End-to-end smoke passes | 100.0% | 100.0% | PASS |
| Credit max day (USD) | $1.15 | $10.00 | PASS |
| Cache hit share on rerun | NO DATA | 95.0% | NO DATA |
| Cold seconds per quarter | NO DATA | 90 s | NO DATA |
| Open STUCK items | 0 | 0 | PASS |
| Floor count within +-1 | 100.0% | 80.0% | PASS |
| Wrong-and-confident | 0.0% | 5.0% | PASS |
| Abstain rate | 52.0% | 35.0% | FAIL |
| Filing field accuracy | 99.1% | 95.0% | PASS |
| Photo kind accuracy | 96.7% | 90.0% | PASS |
| Stamp date correct | NO DATA | 90.0% | NO DATA |
| Hand-verified FLAG share | 0.0% | 100.0% | FAIL |
| Real projects checked | 1 | 5 | FAIL |
| Real FLAGs hand-verified | 0 | 1 | FAIL |
| Cost per project check (USD) | $0.2750 | $0.5000 | PASS |
| Provider comparison done | 100.0% | 100.0% | PASS |

## Provider comparison

| provider | model | accuracy | cost per filing | p50 seconds |
|---|---|---|---|---|
| meta | muse-spark-1.3-contributor | 98.2% | $0.0015 | 23 |
| baseten | meta-models/muse-spark-open-1026 | 99.1% | $0.0454 | 39 |
| fireworks | accounts/fireworks/models/muse-spark-open | 99.1% | $0.0547 | 108 |

Notes:
- filing_v4 was tuned on the 4 truth filings, so accuracy is optimistic.
- Meta scored 113/114 on the earlier run and 112/114 fresh: a one-field gap is run-to-run noise.
- Baseten costs about 30x Meta's contributor tier per filing ($0.0454 vs $0.0015).
- Fireworks timed out once on Oct 29 before succeeding.
- Baseten -1026 billing still to be confirmed in the usage view.
