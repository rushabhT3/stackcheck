# REVIEW FOR OPUS: the final check before submission

Run this on D7 (Nov 3) with Opus 5.5 in read-only mode on the repo. Paste the prompt below as the first message. Opus reports; Muse Code (or you) applies the fixes.

## Prompt

```text
You are reviewing StackCheck, a hackathon project, read-only. Don't edit files. Report findings.

Read in this order: docs/SPEC.md, docs/CONTRACTS.md, docs/KPIS.md, docs/DATA_AND_LEGAL.md, PROGRESS.md, docs/kpi_snapshot.md, then the code under src/stackcheck, tests/, web/src, deploy/.

Check, in this priority order:
1. Wrong results. Any path where StackCheck could show a FLAG that isn't supported by its evidence, or OK when inputs were missing. Read every rule in src/stackcheck/rules/checks.py against CONTRACTS C14 line by line, including units (lakh, crore), date windows, the S - 1 tolerance, and the None handling.
2. Abstain logic. vision/floors.py against C11. Any way a single method's count becomes OK.
3. The agent. pipeline/investigate.py against C15: can the model set a status, repeat an action, exceed 3 steps, or pass an argument not on the allowed list?
4. Legal hygiene. git ls-files: any PDF, filing photo, overlay of a filing photo, Pexels file, .env or ledger committed? Any user-facing text that accuses ("fraud", "scam", "lying")? Disclaimer present on the report page?
5. Privacy rule D07: can an upload ever reach a contributor-tier model or Baseten/Fireworks?
6. Security: path traversal in /media, host allow-list and redirect handling in ingest/sources.py, upload size limits, secrets in logs.
7. Cost and cache: every paid call goes through cache.get_or_compute and costs.record; cache keys stable across runs.
8. Claims vs reality: every number in README.md and the demo script matches docs/kpi_snapshot.md and the ledger.
9. "New work" proof: README section and PROGRESS.md dates cover every module.
10. Code quality: contract drift (names or types differing from CONTRACTS), dead code, functions doing two jobs, missing tests for rule branches.

Output a table: severity (blocker / should fix / nice to have), file:line, what's wrong, the concrete fix. Blockers first. Then a one-paragraph verdict: is it safe to submit?
```

## Known weak spots to look at first

- **Unit handling in money.** QPRs mix rupees, lakh and crore. `to_inr` must be applied before every comparison in R5, R6, R7.
- **Slab semantics.** "G+4" has 5 slabs above ground including the roof. R1 compares visible storeys with S - 1 on purpose. Check that `claimed_slabs` uses `floors + 1` only when converting a percentage.
- **Day-first dates on photo stamps.** A photo stamped 05/03/2025 is 5 March. Ambiguous stamps must give `stamp_date = None`, not a guess.
- **Multi-block projects.** Photos with no block guess are only used for a `MAIN` block. Check that a multi-block filing doesn't silently compare a photo of Block B with Block A's claim.
- **Scanned PDFs.** When `is_scanned`, page renders stand in for photos. Make sure page renders that are mostly text are classified `document_or_logo` and never counted.
- **R2 threshold.** Hamming ≤ 6 can match two different photos of a plain concrete frame from the same spot. Every R2 FLAG shown publicly must be checked by eye.
- **Job restart.** A Modal container restart marks running checks failed; the UI must show that clearly, not spin forever.
