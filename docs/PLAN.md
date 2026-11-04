# PLAN: Oct 28 to Nov 4, with a gate every day

Deadline: **Wed Nov 4, 9:00 PM ET = Thu Nov 5, 7:30 AM IST.** Target: submit by **Wed Nov 4, 10:00 PM IST**, nine and a half hours early.

Hours are AI-assisted estimates for Rushabh's own time (reviewing, running, testing, committing), not the agent's. Total about 50 hours.

| Day (IST) | Cards | Rushabh's hours | Gate at end of day (all must be true) | If the gate fails |
|---|---|---|---|---|
| **D1 Wed Oct 28** | T00 (human), T01, T02, T03 | 7 | G1: Gate 0 answered in PROGRESS.md. `uv run pytest` green. `smoke_meta.py` returns text and token usage. SAM returns at least one box on 2 of 3 benchmark photos. Billing shows usage. | No SAM boxes: try `file_id` mode, then other prompts from T03's list. Still nothing by noon D2: floor counting becomes Commons-benchmark only, demo goes document-first. |
| **D2 Thu Oct 29** | T04, T05, start T09 skeleton | 7 | G2: one real QPR goes PDF to `QuarterFiling` JSON with at least 90% of fields correct against your hand check. A crude end-to-end run (ingest, extract, empty rules, report.json) works on that file. | Extraction below 80%: fix prompt rules for the failing fields (bump to `filing_v2`), re-measure. Scanned PDFs: switch that file to page-image mode. |
| **D3 Fri Oct 30** | T06, T07 | 7 | G3: on the 30-photo Commons benchmark, floor count within ±1 on at least 70% of non-abstained photos, wrong-and-confident rate at most 10%. Overlays look right on 5 real QPR elevations. | Below 60% or wrong-and-confident above 15%: tighten (MIN_ROWS 4, AGREE_TOLERANCE 0), accept more abstains. R1 stays in, but the demo leads with R2, R3, R4, R5, R7. |
| **D4 Sat Oct 31** | T08, T09 complete, T10 | 7 | G4: a full project (≥ 4 quarters) runs end to end from the API: findings with evidence, trace, costs. Rules have 100% branch coverage. | Cut R6 and R8 to CANT_VERIFY stubs; keep R1 to R5 and R7. |
| **D5 Sun Nov 1** | T11 | 6 | G5: the report page shows real findings with overlay photos, timeline and money chart, in light and dark mode, at phone width. | Ship VerdictBanner, FindingCard, PhotoEvidence only; charts move to D7 buffer. |
| **D6 Mon Nov 2** | T12, T13, T14 | 7 | G6: live Modal URL works from your phone. 5 projects checked and saved as samples. Provider comparison table done. KPI report generated. | Deploy fails: record the demo locally and keep fixing on D8; the video is what judges mostly see. |
| **D7 Tue Nov 3** | T15, Opus review | 6 | G7: README with measured numbers, 3-minute video under 3:00, X post drafted, submission form drafted (not submitted). Opus review fixes applied. | Cut stretch items; re-record only if a claim in the video is wrong. |
| **D8 Wed Nov 4** | Buffer, S1 to S3 only if all gates passed | 3+ | G8: submitted by 10:00 PM IST. X post live. Repo public and clean. | Submit what works. A working core beats a broken extra. |

## What never moves

1. The end-to-end path exists from D2 and stays runnable every day after.
2. No stretch work (S1 to S3) before G6 passes.
3. Every FLAG shown publicly is checked by hand first.
4. Submit early. Late submissions aren't considered.

## Live sessions worth attending (optional)

- Wed Oct 28, 9:30 PM IST: Agents 102, "Best Practices for Verifiable Agents". Directly relevant to D03/D04.
- Fri Oct 30, 9:30 PM IST: Fireworks, "Building Specialized Intelligence on Fireworks". Relevant to T12.
- Daily office hours on Discord, 10 to 11 AM ET (7:30 to 8:30 PM IST until Oct 31, 8:30 to 9:30 PM IST from Nov 2), weekdays only. Ask SAM or Model API questions there if a probe fails.
